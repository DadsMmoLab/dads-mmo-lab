"""T126: "latest" follows versioned releases where the catalog says a source publishes them.

Most server repositories publish no releases, so the branch tip stays right for
them. `Sagiroth/TortoiseBots` and its client addon publish dated releases
(v2026-09-25), and for those "latest" is the newest release's commit. The choice
is declared per source (`follow: releases`) and never guessed at run time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder, install
from tests.test_update_to_latest import OLD, _ready
from yulon import apply as apply_module
from yulon import resources
from yulon.apply import Applier, ApplyError
from yulon.catalog import native, upstream
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.git import CloneSpec
from yulon.manifest import Source, parse_manifest
from yulon.resources import manifests_dir
from yulon.ui.widgets import modules_panel

BOTS = "mod-playerbots/mod-playerbots"
CORE = "mod-playerbots/azerothcore-wotlk"
TAG = "v2026-09-25"
REL = "f858f9c97758d73469423162d0692cd72fe82687"
T0 = 1_790_000_000


def _releasing() -> CatalogEntry:
    """WotLK with its bots module marked as following releases: the shape, on a test engine."""
    sources = list(ENTRY.emulator.sources)
    sources[1] = sources[1].model_copy(update={"follow": "releases"})
    emulator = ENTRY.emulator.model_copy(update={"sources": tuple(sources)})
    return ENTRY.model_copy(update={"emulator": emulator})


def _engine(rec: Recorder, entry: CatalogEntry | None = None) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry or _releasing(),
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def _press(rec: Recorder, server_dir: Path, **kwargs: Any) -> list[str]:
    return list(_engine(rec).update_to_latest(InstallOptions(server_dir=server_dir), **kwargs))


def _bots_dest(server_dir: Path) -> Path:
    return server_dir / ENTRY.emulator.sources[1].dest


# -- the catalog and the manifests say it, and only where it is true ----------


def test_only_tortoisebots_follows_releases_in_the_catalog() -> None:
    """Every other source keeps its branch; cmangos' rolling `latest` tag is not a version."""
    follows = {
        source.repo: source.follow
        for entry in load_catalog().games
        for source in entry.emulator.sources
    }
    assert {repo for repo, how in follows.items() if how == "releases"} == {"Sagiroth/TortoiseBots"}
    assert follows["cmangos/mangos-tbc"] == "branch"


def test_only_the_bots_manager_addon_follows_releases_among_the_manifests() -> None:
    """TortoiseGMManager publishes no releases and no tags (read 2026-09-25).

    So it keeps following its branch.
    """
    releasing = []
    for path in sorted(manifests_dir().glob("*/*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        source = data.get("source") if isinstance(data, dict) else None
        if isinstance(source, dict) and source.get("follow") == "releases":
            releasing.append((path.parent.parent.name, data["id"]))
    assert releasing == [("wow-tortoise", "tortoise-bots-manager")]
    # And it parses: the field is the shared `Source` model's, not a private key.
    assert _addon().source.follow == "releases"


def test_follow_defaults_to_the_branch_and_releases_needs_github() -> None:
    assert Source(repo="a/b").follow == "branch"
    assert Source(repo="https://github.com/a/b.git", follow="releases").follow == "releases"
    with pytest.raises(ValueError, match="GitHub"):
        Source(repo="https://gitlab.com/a/b.git", follow="releases")
    with pytest.raises(ValueError):
        Source(repo="a/b", follow="tags")  # type: ignore[arg-type]


# -- the resolver --------------------------------------------------------------


def _get(answers: dict[str, bytes]) -> upstream.HttpGet:
    def get(url: str, accept: str) -> bytes:
        if url not in answers:
            raise OSError(f"no answer for {url}")
        return answers[url]

    return get


API = "https://api.github.com/repos/Sagiroth/TortoiseBots"


def test_the_newest_release_is_resolved_to_the_commit_its_tag_names_now() -> None:
    got = upstream.newest_release(
        "Sagiroth/TortoiseBots",
        get=_get(
            {
                f"{API}/releases/latest": json.dumps({"tag_name": TAG}).encode(),
                f"{API}/commits/{TAG}": (REL + "\n").encode(),
            }
        ),
    )
    assert got == upstream.Release(tag=TAG, sha=REL)


@pytest.mark.parametrize(
    "latest",
    [
        {"tag_name": TAG, "draft": True},
        {"tag_name": TAG, "prerelease": True},
        {"tag_name": ""},
        {"message": "Not Found"},
        [],
    ],
)
def test_a_draft_a_prerelease_or_no_release_is_nothing_to_follow(latest: object) -> None:
    got = upstream.newest_release(
        "Sagiroth/TortoiseBots",
        get=_get(
            {
                f"{API}/releases/latest": json.dumps(latest).encode(),
                f"{API}/commits/{TAG}": REL.encode(),
            }
        ),
    )
    assert got is None


def test_a_tag_that_does_not_resolve_to_a_commit_is_nothing_to_follow() -> None:
    answers = {f"{API}/releases/latest": json.dumps({"tag_name": TAG}).encode()}
    assert upstream.newest_release("Sagiroth/TortoiseBots", get=_get(answers)) is None
    answers[f"{API}/commits/{TAG}"] = b"<html>"
    assert upstream.newest_release("Sagiroth/TortoiseBots", get=_get(answers)) is None


# -- the update route ----------------------------------------------------------


def _releasing_ready(tmp_path: Path) -> tuple[Recorder, Path]:
    rec, server_dir = _ready(tmp_path)
    rec.releases = {BOTS: (TAG, REL)}
    rec.github = {BOTS: 5, CORE: 3}
    return rec, server_dir


def test_an_update_checks_out_the_newest_release_and_the_core_its_branch_tip(
    tmp_path: Path,
) -> None:
    rec, server_dir = _releasing_ready(tmp_path)
    lines = _press(rec, server_dir)
    revs = {spec.url: spec.rev for spec in rec.clones}
    assert revs[ENTRY.emulator.sources[1].url] == REL
    assert revs[ENTRY.emulator.sources[0].url] is None, "the core still follows its branch"
    assert rec.heads[_bots_dest(server_dir)] == REL
    assert f"{BOTS} follows its releases; the newest is {TAG} ({REL[:7]})." in lines
    state = native.read_state(server_dir, valid=())
    assert state is not None
    bots = state.rev_for(BOTS)
    assert bots is not None and bots.release == TAG
    core = state.rev_for(CORE)
    assert core is not None and core.release == ""


def test_the_version_line_names_the_release(tmp_path: Path) -> None:
    rec, server_dir = _releasing_ready(tmp_path)
    _press(rec, server_dir)
    line = native.source_version(native.read_state(server_dir, valid=())).line
    rows = line.splitlines()
    assert any(row.startswith(f"mod-playerbots {TAG}, built from {REL[:7]}") for row in rows), rows
    assert any(row.startswith(f"{CORE}: built from") for row in rows), rows


def test_the_release_survives_the_install_record_round_trip(tmp_path: Path) -> None:
    state = native.InstallState(
        game_id="wow-tortoise",
        install_id="x",
        source_revs=(
            native.SourceRev("Sagiroth/TortoiseBots", "f858f9c · 2026-09-25", release=TAG),
        ),
    )
    native.write_state(tmp_path, state)
    raw = json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))
    assert raw["source_revs"]["Sagiroth/TortoiseBots"]["release"] == TAG
    again = native.read_state(tmp_path, valid=())
    assert again is not None and again.source_revs[0].release == TAG
    assert native.commits_past_pin(again.source_revs[0]) == (
        f"TortoiseBots {TAG}, built from f858f9c (2026-09-25)"
    )


def test_a_branch_source_writes_no_release_key(tmp_path: Path) -> None:
    """The record of a source that follows its branch is byte for byte what T64 wrote."""
    rec, server_dir = _ready(tmp_path)
    list(_engine(rec, ENTRY).update_to_latest(InstallOptions(server_dir=server_dir)))
    raw = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert all("release" not in row for row in raw["source_revs"].values())
    assert not any("/releases/" in url for url in rec.gets)


def test_github_silent_about_the_release_refuses_before_anything_is_fetched(
    tmp_path: Path,
) -> None:
    """The branch tip is the in-between commit this source is marked to avoid: no fallback."""
    rec, server_dir = _releasing_ready(tmp_path)
    rec.releases = {}
    before = dict(rec.heads)
    with pytest.raises(InstallerError, match="could not ask GitHub which release"):
        _press(rec, server_dir)
    assert rec.clones == []
    assert rec.heads == before


def test_a_return_to_the_pin_ignores_releases(tmp_path: Path) -> None:
    rec, server_dir = _releasing_ready(tmp_path)
    _press(rec, server_dir)
    rec.gets.clear()
    rec.clones.clear()
    _press(rec, server_dir, to_pin=True)
    assert {spec.rev for spec in rec.clones} == {s.rev for s in ENTRY.emulator.sources}
    assert not any("/releases/" in url for url in rec.gets)
    state = native.read_state(server_dir, valid=())
    assert state is not None
    assert all(rev.release == "" for rev in state.source_revs)


def test_a_checkout_already_past_the_newest_release_stays_where_it_is(tmp_path: Path) -> None:
    """A pin newer than the newest release: an "update" onto the release would go backwards."""
    rec, server_dir = _releasing_ready(tmp_path)
    rec.github = {BOTS: 0, CORE: 3}
    lines = _press(rec, server_dir)
    assert rec.heads[_bots_dest(server_dir)] == OLD
    assert any("is already in" in line and "stays there" in line for line in lines), lines
    state = native.read_state(server_dir, valid=())
    assert state is not None
    bots = state.rev_for(BOTS)
    assert bots is not None and bots.release == "", "it is not ON the release, so none is named"


# -- T124's count, for a releases source ---------------------------------------


def _counted(tmp_path: Path) -> tuple[Recorder, Path]:
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
    rec.gets.clear()
    rec.releases = {BOTS: (TAG, REL)}
    rec.github = {CORE: 300, BOTS: 4}
    return rec, server_dir


def test_the_count_says_new_release_for_a_source_that_follows_releases(tmp_path: Path) -> None:
    rec, server_dir = _counted(tmp_path)
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert upstream.line(news).startswith(
        "Upstream has new code since this server was built: server 300 commits, "
        f"mod-playerbots new release {TAG}."
    )
    # Compared against the RELEASE's commit, not the branch.
    assert f"https://api.github.com/repos/{BOTS}/compare/{OLD}...{REL}?per_page=1&page=2" in (
        rec.gets
    )


def test_a_checkout_on_the_newest_release_has_no_news(tmp_path: Path) -> None:
    rec, server_dir = _counted(tmp_path)
    rec.github = {CORE: 0, BOTS: 0}
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert upstream.line(news) == ""


def test_a_count_cached_for_the_branch_is_not_served_to_a_source_now_following_releases(
    tmp_path: Path,
) -> None:
    rec, server_dir = _counted(tmp_path)
    _engine(rec, ENTRY).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    asked = len(rec.gets)
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0 + 60)
    assert len(rec.gets) > asked
    assert any(s.release == TAG for s in news.sources)


# -- module manifests ----------------------------------------------------------


class _Git:
    def __init__(self) -> None:
        self.calls: list[CloneSpec] = []

    def clone(self, spec: CloneSpec) -> None:
        self.calls.append(spec)
        spec.dest.mkdir(parents=True, exist_ok=True)
        (spec.dest / ".git").mkdir(exist_ok=True)
        (spec.dest / "TortoiseBotsManager.toc").write_text("## Title: x\n", encoding="utf-8")

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        return None

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return None


def _addon() -> Any:
    return tortoise_modules.store().load("mod", "tortoise-bots-manager")


def test_an_addon_that_follows_releases_installs_the_newest_releases_commit(
    tmp_path: Path,
) -> None:
    git = _Git()
    asked: list[str] = []

    def newest(slug: str) -> upstream.Release | None:
        asked.append(slug)
        return upstream.Release(TAG, REL)

    report = Applier(tmp_path / "server", git=git, newest_release=newest).install(_addon())
    assert asked == ["Sagiroth/TortoiseBotsManager"]
    assert [spec.rev for spec in git.calls] == [REL]
    assert any(f"at release {TAG}" in line for line in report.done), report.done
    clone = tmp_path / "server" / apply_module.CLONE_DIRS["mod"] / "tortoise-bots-manager"
    assert apply_module.clone_release(clone, item_id="tortoise-bots-manager") == TAG


def test_an_addon_install_github_cannot_answer_for_is_refused_before_the_clone(
    tmp_path: Path,
) -> None:
    git = _Git()
    with pytest.raises(ApplyError, match="could not ask GitHub which release"):
        Applier(tmp_path / "server", git=git, newest_release=lambda slug: None).install(_addon())
    assert git.calls == []
    assert not (tmp_path / "server" / "sql_scripts").exists()


def test_a_branch_module_never_asks_for_a_release(tmp_path: Path) -> None:
    manifest = tortoise_modules.store().load("mod", "tortoise-gm-manager")

    def newest(slug: str) -> upstream.Release | None:
        raise AssertionError("a branch-following module asked for a release")

    git = _Git()
    Applier(tmp_path / "server", git=git, newest_release=newest).install(manifest)
    assert [spec.rev for spec in git.calls] == [None]
    clone = tmp_path / "server" / apply_module.CLONE_DIRS["mod"] / "tortoise-gm-manager"
    assert apply_module.clone_release(clone, item_id="tortoise-gm-manager") == ""
    claim = json.loads((clone / apply_module.CLAIM_FILE).read_text(encoding="utf-8"))
    assert "release" not in claim


class _Behind:
    def __init__(self, answer: int | None) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str | None]] = []

    def commits_behind(self, dest: Path, branch: str | None) -> int | None:
        self.asked.append((dest.name, branch))
        return self.answer


def test_the_module_count_for_a_releases_module_is_against_the_release(tmp_path: Path) -> None:
    (tmp_path / "modules" / "mod-x" / ".git").mkdir(parents=True)
    git = _Behind(7)
    rows = apply_module.module_updates(
        tmp_path,
        git=git,
        releases={"mod-x": "o/mod-x"},
        newest_release=lambda slug: upstream.Release(TAG, REL),
    )
    assert git.asked == [("mod-x", REL)]
    assert rows[0].line == f"mod-x: new release {TAG}"
    git.answer = 0
    rows = apply_module.module_updates(
        tmp_path,
        git=git,
        releases={"mod-x": "o/mod-x"},
        newest_release=lambda slug: upstream.Release(TAG, REL),
    )
    assert rows[0].line == f"mod-x: on the newest release, {TAG}"
    rows = apply_module.module_updates(
        tmp_path, git=git, releases={"mod-x": "o/mod-x"}, newest_release=lambda slug: None
    )
    assert rows[0].behind is None


def test_the_update_chip_offers_the_release_not_a_commit_count() -> None:
    assert modules_panel.chip_update_label(7, TAG) == f"Update available — new release {TAG}"
    assert modules_panel.chip_update_label(7) == "Update available — 7 commits behind"


# -- the addon's release against the server's ----------------------------------


def _addon_installed(server_dir: Path, release: str) -> None:
    clone = server_dir / apply_module.CLONE_DIRS["mod"] / tortoise_modules.ADDON_ID
    clone.mkdir(parents=True)
    apply_module.write_clone_claim(
        clone, item_id=tortoise_modules.ADDON_ID, url="u", completed=True, release=release
    )


def _server_on(server_dir: Path, release: str) -> None:
    native.write_state(
        server_dir,
        native.InstallState(
            game_id="wow-tortoise",
            install_id="x",
            source_revs=(
                native.SourceRev(
                    tortoise_modules.BOTS_REPO, "abc1234 · 2026-09-25", release=release
                ),
            ),
        ),
    )


SKEW = (
    "The addon (v2026-09-24) and the server's bot module (v2026-09-25) are from different "
    "releases. They work together, but updating both keeps them in step."
)


def test_the_addon_row_names_both_releases_and_says_when_they_differ(tmp_path: Path) -> None:
    _addon_installed(tmp_path, "v2026-09-24")
    _server_on(tmp_path, TAG)
    note = tortoise_modules.release_notes(tmp_path)[("mod", "tortoise-bots-manager")]
    assert note == (
        "Installed: release v2026-09-24. The server's bot module is on v2026-09-25.\n" + SKEW
    )


def test_the_addon_row_says_nothing_about_skew_when_they_match(tmp_path: Path) -> None:
    _addon_installed(tmp_path, TAG)
    _server_on(tmp_path, TAG)
    note = tortoise_modules.release_notes(tmp_path)[("mod", "tortoise-bots-manager")]
    assert note == f"Installed: release {TAG}. The server's bot module is on {TAG}."


def test_a_server_on_its_pin_names_only_the_addons_release(tmp_path: Path) -> None:
    _addon_installed(tmp_path, TAG)
    native.write_state(tmp_path, native.InstallState(game_id="wow-tortoise", install_id="x"))
    assert tortoise_modules.release_notes(tmp_path) == {
        ("mod", "tortoise-bots-manager"): f"Installed: release {TAG}."
    }


def test_no_addon_release_means_no_note(tmp_path: Path) -> None:
    _server_on(tmp_path, TAG)
    assert tortoise_modules.release_notes(tmp_path) == {}


def test_the_note_is_drawn_on_its_row_in_full(qapp: object) -> None:
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "id": "tortoise-bots-manager",
            "name": "TortoiseBots Manager (client addon)",
            "type": "mod",
            "game": "wow-tortoise",
            "description": "d",
            "source": {"repo": "Sagiroth/TortoiseBotsManager", "follow": "releases"},
            "build": {"rebuild": False},
            "client": [{"src": ".", "dest": "addons", "name": "TortoiseBotsManager"}],
        }
    )
    rows = modules_panel.build_module_rows(
        [manifest],
        {},
        modules_panel.SessionState(),
        None,
        notes={("mod", "tortoise-bots-manager"): "Installed: release v1.\n" + SKEW},
    )
    assert rows[0].note == "Installed: release v1.\n" + SKEW
    widget = modules_panel.RowWidget(rows[0])
    assert widget.note_label is not None
    assert widget.note_label.text().endswith("keeps them in step.")


# -- review fixes: a compare GitHub will not answer refuses (Codex high) --------


def test_a_release_whose_comparison_github_will_not_answer_refuses_before_anything_moves(
    tmp_path: Path,
) -> None:
    """The release resolves, the compare does not (a 403, a timeout): no source moves.

    Without the comparison nobody knows whether the release is ahead of the
    checkout or behind it, and the old code moved onto it anyway.
    """
    rec, server_dir = _releasing_ready(tmp_path)
    rec.github = {CORE: 3}  # the bots' compare has no answer
    before = dict(rec.heads)
    with pytest.raises(InstallerError, match="GitHub did not answer"):
        _press(rec, server_dir)
    assert rec.clones == []
    assert rec.heads == before


# -- review fixes: a tag moved within its own day -------------------------------


def _record_release(server_dir: Path, repo: str, tag: str) -> None:
    state = native.read_state(server_dir, valid=())
    assert state is not None
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=state.game_id,
            install_id=state.install_id,
            family=state.family,
            completed=state.completed,
            source_revs=(native.SourceRev(repo, f"{OLD[:7]} · 2026-09-25", release=tag),),
        ),
    )


def test_a_newer_build_of_the_release_installed_is_an_updated_release(tmp_path: Path) -> None:
    """Same tag name, commits ahead: "updated release", never "in step" or "new"."""
    rec, server_dir = _counted(tmp_path)
    _record_release(server_dir, BOTS, TAG)
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert f"mod-playerbots updated release {TAG}" in upstream.line(news)
    _record_release(server_dir, BOTS, "v2026-09-24")
    (server_dir / upstream.UPSTREAM_FILE).unlink()
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert f"mod-playerbots new release {TAG}" in upstream.line(news)


def test_a_module_row_on_an_older_build_of_its_release_says_updated() -> None:
    row = apply_module.ModuleUpdate(
        "tbm", Path("/x"), True, 3, family="mod", release=TAG, installed_release=TAG
    )
    assert row.line == f"tbm: updated release {TAG}"
    assert modules_panel.chip_update_label(3, TAG, updated=True) == (
        f"Update available — updated release {TAG}"
    )


def test_equal_release_names_are_not_in_step_when_one_side_is_an_older_build(
    tmp_path: Path,
) -> None:
    """The note compares what the counts found, not only the two names."""
    _addon_installed(tmp_path, TAG)
    _server_on(tmp_path, TAG)
    note = tortoise_modules.release_notes(tmp_path)[("mod", "tortoise-bots-manager")]
    assert "\n" not in note, "no count says either side moved: in step"
    # The addon's last "Check for updates" found a newer build of the same tag.
    clone = tmp_path / apply_module.CLONE_DIRS["mod"] / tortoise_modules.ADDON_ID
    (clone / ".git").mkdir()

    class _G:
        def commits_behind(self, dest: Path, branch: str | None) -> int | None:
            return 2

        def head_sha(self, dest: Path) -> str | None:
            return OLD

    tortoise_modules.module_updates(
        tmp_path, git=_G(), newest_release=lambda slug: upstream.Release(TAG, REL), now=T0
    )
    note = tortoise_modules.release_notes(tmp_path)[("mod", "tortoise-bots-manager")]
    assert note.endswith(
        f"Both are named {TAG}, but an updated release {TAG} has come out since the addon was "
        "installed. They work together, but updating both keeps them in step."
    ), note


# -- review fixes: Tortoise can see and take a new addon release -----------------


class _CountGit:
    def __init__(self, behind: dict[str, int | None], heads: dict[str, str]) -> None:
        self.behind = behind
        self.heads = heads
        self.asked: list[tuple[str, str | None]] = []

    def commits_behind(self, dest: Path, branch: str | None) -> int | None:
        self.asked.append((dest.name, branch))
        return self.behind.get(dest.name)

    def head_sha(self, dest: Path) -> str | None:
        return self.heads.get(dest.name)


def _clones(server_dir: Path, *names: str) -> None:
    for name in names:
        (server_dir / apply_module.CLONE_DIRS["mod"] / name / ".git").mkdir(parents=True)


def test_tortoise_counts_its_addons_each_against_what_it_follows(tmp_path: Path) -> None:
    _clones(tmp_path, "tortoise-bots-manager", "tortoise-gm-manager")
    git = _CountGit(
        {"tortoise-bots-manager": 4, "tortoise-gm-manager": 2},
        {"tortoise-bots-manager": OLD, "tortoise-gm-manager": OLD},
    )
    rows = tortoise_modules.module_updates(
        tmp_path, git=git, newest_release=lambda slug: upstream.Release(TAG, REL), now=T0
    )
    assert [(r.family, r.key, r.line) for r in rows] == [
        ("mod", "tortoise-bots-manager", f"tortoise-bots-manager: new release {TAG}"),
        ("mod", "tortoise-gm-manager", "tortoise-gm-manager: 2 commits behind"),
    ]
    assert git.asked == [("tortoise-bots-manager", REL), ("tortoise-gm-manager", None)]


def test_the_tortoise_count_is_kept_for_a_day_and_recounted_when_the_clone_moves(
    tmp_path: Path,
) -> None:
    """T124's rule for modules: a Check pressed again costs GitHub and git nothing."""
    _clones(tmp_path, "tortoise-bots-manager")
    git = _CountGit({"tortoise-bots-manager": 4}, {"tortoise-bots-manager": OLD})
    resolved: list[str] = []

    def newest(slug: str) -> upstream.Release:
        resolved.append(slug)
        return upstream.Release(TAG, REL)

    def count(now: int) -> tuple[apply_module.ModuleUpdate, ...]:
        return tortoise_modules.module_updates(tmp_path, git=git, newest_release=newest, now=now)

    count(T0)
    count(T0 + upstream.MAX_AGE_SECONDS - 1)
    assert len(resolved) == 1 and len(git.asked) == 1, "a second press within the day asked"
    git.heads["tortoise-bots-manager"] = REL  # an Update moved the clone
    git.behind["tortoise-bots-manager"] = 0
    rows = count(T0 + 60)
    assert len(resolved) == 2, "a row about a HEAD that is gone was served"
    assert rows[0].line == f"tortoise-bots-manager: on the newest release, {TAG}"
    count(T0 + upstream.MAX_AGE_SECONDS + 61)
    assert len(resolved) == 3


def test_a_tortoise_count_that_could_not_ask_is_retried_within_the_hour(tmp_path: Path) -> None:
    _clones(tmp_path, "tortoise-bots-manager")
    git = _CountGit({"tortoise-bots-manager": 4}, {"tortoise-bots-manager": OLD})
    answers: list[upstream.Release | None] = [None, upstream.Release(TAG, REL)]
    rows = tortoise_modules.module_updates(
        tmp_path, git=git, newest_release=lambda slug: answers.pop(0), now=T0
    )
    assert rows[0].behind is None
    rows = tortoise_modules.module_updates(
        tmp_path,
        git=git,
        newest_release=lambda slug: answers.pop(0),
        now=T0 + upstream.RETRY_SECONDS,
    )
    assert rows[0].behind == 4


def test_the_tortoise_tab_is_given_the_module_folder_seams(tmp_path: Path) -> None:
    """What makes the addon rows installed, countable and updatable on Tortoise."""
    from yulon.ui.controller_view import ControllerServices

    services = ControllerServices.for_entry(load_catalog().get("wow-tortoise"), tmp_path)
    assert services.module_updates is not None
    assert services.installed_modules is not None
    assert services.unfinished_modules is not None
    assert services.module_version is not None
    _clones(tmp_path, "tortoise-bots-manager")
    assert services.installed_modules()["mod"] == frozenset({"tortoise-bots-manager"})


# -- Codex's final pass: a release on history upstream rewrote -------------------


def _diverged(tmp_path: Path) -> tuple[Recorder, Path]:
    """The release is ahead of the checkout AND behind it: upstream rewrote its history."""
    rec, server_dir = _releasing_ready(tmp_path)
    rec.github = {BOTS: 126, CORE: 3}
    rec.github_behind = {BOTS: 65}
    return rec, server_dir


REWRITTEN = (
    f"{BOTS}: Upstream rewrote its history: the release {TAG} does not contain 65 commits "
    "this server was built from. Updating moves the server onto the release as upstream "
    "publishes it."
)
"""The ruling's sentence, spelled here rather than read from the code under test."""


def test_the_rewritten_line_is_the_rulings_sentence() -> None:
    assert native.rewritten_line(BOTS, TAG, 65) == REWRITTEN


def test_a_diverged_release_the_question_did_not_describe_moves_nothing(tmp_path: Path) -> None:
    """Silently resetting onto a rewritten history is what 140f86a1 did; now it stops first."""
    rec, server_dir = _diverged(tmp_path)
    before = dict(rec.heads)
    with pytest.raises(InstallerError, match="Upstream rewrote its history") as refused:
        _press(rec, server_dir)
    assert getattr(refused.value, "repo", None) == BOTS
    assert getattr(refused.value, "line", None) == REWRITTEN
    assert rec.clones == []
    assert rec.heads == before


def test_a_diverged_release_the_question_described_moves_and_says_so_in_the_log(
    tmp_path: Path,
) -> None:
    """Warn, don't refuse: the install built on the old history is not locked out."""
    rec, server_dir = _diverged(tmp_path)
    lines = _press(rec, server_dir, rewritten_ok=frozenset({BOTS}))
    assert rec.heads[_bots_dest(server_dir)] == REL, "HEAD moves, so T123's adoption runs"
    assert REWRITTEN in "\n".join(lines)


def test_a_checkout_only_ahead_of_the_release_stays_and_says_by_how_much(tmp_path: Path) -> None:
    rec, server_dir = _releasing_ready(tmp_path)
    rec.github = {BOTS: 0, CORE: 3}
    rec.github_behind = {BOTS: 8}
    lines = _press(rec, server_dir)
    assert rec.heads[_bots_dest(server_dir)] == OLD
    assert any("which is 8 commits past it, so it stays there" in line for line in lines), lines


def test_the_notice_says_a_diverged_source_was_rewritten(tmp_path: Path) -> None:
    rec, server_dir = _counted(tmp_path)
    rec.github_behind = {BOTS: 65}
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    said = upstream.line(news)
    assert f"mod-playerbots upstream rewrote its history (new release {TAG})" in said, said
    assert "server 300 commits" in said


def test_a_diverged_branch_source_is_said_as_rewritten_too(tmp_path: Path) -> None:
    rec, server_dir = _counted(tmp_path)
    rec.github_behind = {CORE: 4}
    news = _engine(rec, ENTRY).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert "server upstream rewrote its history (300 new commits)" in upstream.line(news)


def _route(rec: Recorder, server_dir: Path, monkeypatch: pytest.MonkeyPatch) -> native.LatestRoute:
    from yulon import install_wiring

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: _engine(rec))
    route = install_wiring.update_to_latest_for_app(_releasing(), server_dir)
    assert route is not None
    return route


def test_the_question_carries_the_line_from_the_days_count_and_yes_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir = _diverged(tmp_path)
    _engine(rec).upstream_news(InstallOptions(server_dir=server_dir))
    route = _route(rec, server_dir, monkeypatch)
    asked = route.confirmation()
    assert asked.endswith("\n\n" + REWRITTEN), asked
    list(route.press(None))
    assert rec.heads[_bots_dest(server_dir)] == REL


def test_a_divergence_the_question_missed_is_asked_about_on_the_next_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No count cached (a stale day, a new window): the press stops.

    And the next question says it.
    """
    rec, server_dir = _diverged(tmp_path)
    route = _route(rec, server_dir, monkeypatch)
    assert REWRITTEN not in route.confirmation()
    with pytest.raises(InstallerError, match="Upstream rewrote its history"):
        list(route.press(None))
    assert rec.clones == []
    assert REWRITTEN in route.confirmation()
    list(route.press(None))
    assert rec.heads[_bots_dest(server_dir)] == REL


def test_an_ordinary_release_asks_the_ordinary_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir = _releasing_ready(tmp_path)
    _engine(rec).upstream_news(InstallOptions(server_dir=server_dir))
    route = _route(rec, server_dir, monkeypatch)
    assert "rewrote" not in route.confirmation()
    list(route.press(None))
    assert rec.heads[_bots_dest(server_dir)] == REL
