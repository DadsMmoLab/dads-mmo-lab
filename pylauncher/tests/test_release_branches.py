"""T862: Update to latest follows the newest CoA release branch and its paired bots tag.

The core follows the highest `coa-bots-<version>` branch of the pjerra mirror
(listed over HTTPS, no API quota); the bots follow the tag `v<version>` beside
it. A release without its bots tag offers nothing; a move that changes
`data/coa-world/baseline.json` is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder, advert_body, install
from yulon import resources
from yulon.catalog import native, upstream
from yulon.catalog.catalog import CatalogEntry, EmulatorSource, load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.manifest import Source

CORE = "mod-playerbots/azerothcore-wotlk"
BOTS = "mod-playerbots/mod-playerbots"
OLD_A = "a" * 40
NEW_B = "b" * 40
TAG_C = "c" * 40
T0 = 1_790_000_000
BASELINE = "data/coa-world/baseline.json"

# Real shas from the mirror: v2.9.1 is annotated (peel `^{}`), v2.8.1 is lightweight.
TAG_V291 = "79c5437a84bb0b9593011c261b1bd9afa558427e"
TAG_V281 = "8203e0a3d85948d40e063e7c277ff25597e8d81c"


def _branching() -> CatalogEntry:
    """WotLK shaped like CoA: core follows release branches, bots the paired tag."""
    sources = list(ENTRY.emulator.sources)
    sources[0] = sources[0].model_copy(
        update={
            "follow": "release_branches",
            "release_prefix": "coa-bots-",
            "branch": "coa-bots-2.9.1",
        }
    )
    sources[1] = sources[1].model_copy(update={"follow": "release_tag", "release_prefix": "v"})
    emulator = ENTRY.emulator.model_copy(update={"sources": tuple(sources)})
    entry = ENTRY.model_copy(update={"emulator": emulator})
    block = entry.install.native
    assert block is not None and block.azerothcore is not None
    azerothcore = block.azerothcore.model_copy(
        update={
            "world_bootstrap_service": "coa-world-bootstrap",
            "world_baseline": BASELINE,
        }
    )
    native_block = block.model_copy(update={"azerothcore": azerothcore})
    install_block = entry.install.model_copy(update={"native": native_block})
    return CatalogEntry.model_validate(
        entry.model_copy(update={"install": install_block}).model_dump()
    )


def _engine(rec: Recorder, entry: CatalogEntry | None = None) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry or _branching(),
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def _ready(tmp_path: Path) -> tuple[Recorder, Path]:
    """An installed folder on OLD_A with a 2.10 release (core B, bots C) to move to."""
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    entry = _branching()
    for source in entry.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD_A
        rec.upstream[server_dir / source.dest] = NEW_B
    rec.clones.clear()
    rec.calls.clear()
    rec.gets.clear()
    core, bots = entry.emulator.sources[0], entry.emulator.sources[1]
    rec.adverts = {
        core.url: {
            "refs/heads/coa-bots-2.9.1": OLD_A,
            "refs/heads/coa-bots-2.10": NEW_B,
            "refs/heads/main": "d" * 40,
        },
        bots.url: {
            "refs/tags/v2.10^{}": TAG_C,
            "refs/tags/v2.10": TAG_C,
        },
    }
    rec.github = {CORE: 5, BOTS: 5}
    return rec, server_dir


def _press(rec: Recorder, server_dir: Path, **kwargs: Any) -> list[str]:
    return list(_engine(rec).update_to_latest(InstallOptions(server_dir=server_dir), **kwargs))


# -- 1. versions ---------------------------------------------------------------


def test_release_versions_order_as_numbers() -> None:
    assert (
        upstream.release_version("coa-bots-2.8", "coa-bots-")
        < upstream.release_version("coa-bots-2.8.4", "coa-bots-")
        < upstream.release_version("coa-bots-2.9", "coa-bots-")
        < upstream.release_version("coa-bots-2.9.1", "coa-bots-")
        < upstream.release_version("coa-bots-2.10", "coa-bots-")
    )
    for bad in ("coa-bots-2.9-rc1", "coa-bots-", "coa-bots-x", "coa-bots-2..9", "main"):
        assert upstream.release_version(bad, "coa-bots-") is None


# -- 2. the wire answer --------------------------------------------------------


def test_advertised_refs_reads_the_wire_answer() -> None:
    refs = {"refs/heads/coa-bots-2.10": NEW_B, "refs/heads/main": OLD_A}
    got = upstream.advertised_refs("https://example.com/x.git", get=lambda u, a: advert_body(refs))
    assert got == refs
    assert upstream.advertised_refs("https://example.com/x.git", get=lambda u, a: b"<html>") is None
    assert upstream.advertised_refs("https://example.com/x.git", get=lambda u, a: b"0012") is None

    def failing(url: str, accept: str) -> bytes:
        raise OSError("down")

    assert upstream.advertised_refs("https://example.com/x.git", get=failing) is None


# -- 3. heads only -------------------------------------------------------------


def test_newest_release_branch_reads_heads_only() -> None:
    got = upstream.newest_release_branch(
        {
            "refs/heads/main": "0" * 40,
            "refs/heads/coa-bots-2.9.1": OLD_A,
            "refs/heads/coa-bots-2.10": NEW_B,
            "refs/tags/coa-bots-9.9": "f" * 40,
        },
        "coa-bots-",
    )
    assert got is not None and (got.name, got.sha) == ("coa-bots-2.10", NEW_B)
    assert upstream.newest_release_branch({"refs/heads/main": "0" * 40}, "coa-bots-") is None


# -- 4. tags ------------------------------------------------------------------


def test_tag_commit_peels_annotated_tags_and_reads_lightweight_ones() -> None:
    assert (
        upstream.tag_commit(
            {"refs/tags/v2.9.1^{}": TAG_V291, "refs/tags/v2.9.1": "0" * 40}, "v2.9.1"
        )
        == TAG_V291
    )
    assert upstream.tag_commit({"refs/tags/v2.8.1": TAG_V281}, "v2.8.1") == TAG_V281
    assert upstream.tag_commit({}, "v9.9") is None


# -- 5. validators -------------------------------------------------------------


def test_release_follow_needs_a_prefix_a_pair_and_github() -> None:
    with pytest.raises(ValueError, match="needs release_prefix"):
        EmulatorSource(repo="a/b", branch="coa-bots-2.9.1", dest=".", follow="release_branches")
    with pytest.raises(ValueError, match="only read with follow"):
        EmulatorSource(repo="a/b", branch="main", dest=".", release_prefix="coa-bots-")
    with pytest.raises(ValueError, match="needs a GitHub repository"):
        EmulatorSource(
            repo="https://gitlab.com/a/b.git",
            branch="coa-bots-2.9.1",
            dest=".",
            follow="release_branches",
            release_prefix="coa-bots-",
        )
    with pytest.raises(ValueError, match="is not coa-bots-<version>"):
        EmulatorSource(
            repo="a/b",
            branch="main",
            dest=".",
            follow="release_branches",
            release_prefix="coa-bots-",
        )
    with pytest.raises(ValueError, match="at most one source"):
        from yulon.catalog.catalog import Emulator

        Emulator(
            name="x",
            sources=(
                EmulatorSource(
                    repo="a/b",
                    branch="coa-bots-2.9.1",
                    dest=".",
                    follow="release_branches",
                    release_prefix="coa-bots-",
                ),
                EmulatorSource(
                    repo="c/d",
                    branch="coa-bots-2.9.1",
                    dest="m",
                    follow="release_branches",
                    release_prefix="coa-bots-",
                ),
            ),
        )
    with pytest.raises(ValueError, match="no source of this emulator follows"):
        from yulon.catalog.catalog import Emulator

        Emulator(
            name="x",
            sources=(
                EmulatorSource(
                    repo="c/d",
                    branch="coa",
                    dest="m",
                    follow="release_tag",
                    release_prefix="v",
                ),
            ),
        )
    with pytest.raises(ValueError):
        Source(repo="a/b", follow="release_branches")  # type: ignore[arg-type]


# -- 6. the update -------------------------------------------------------------


def test_update_moves_the_core_onto_the_newest_release_branch_and_the_bots_onto_its_tag(
    tmp_path: Path,
) -> None:
    rec, server_dir = _ready(tmp_path)
    lines = _press(rec, server_dir)
    by_url = {spec.url: spec for spec in rec.clones}
    entry = _branching()
    core, bots = entry.emulator.sources[0], entry.emulator.sources[1]
    assert by_url[core.url].branch == "coa-bots-2.10"
    assert by_url[core.url].rev == NEW_B
    assert by_url[bots.url].branch == bots.branch
    assert by_url[bots.url].rev == TAG_C
    state = native.read_state(server_dir, valid=())
    assert state is not None
    assert state.rev_for(CORE) is not None and state.rev_for(CORE).release == "coa-bots-2.10"
    assert state.rev_for(BOTS) is not None and state.rev_for(BOTS).release == "v2.10"
    assert any("the newest is coa-bots-2.10" in line for line in lines), lines


# -- 7. missing bots tag -------------------------------------------------------


def test_a_release_without_its_bots_tag_is_refused_before_anything_moves(
    tmp_path: Path,
) -> None:
    rec, server_dir = _ready(tmp_path)
    entry = _branching()
    bots = entry.emulator.sources[1]
    rec.adverts[bots.url] = {}
    before = dict(rec.heads)
    with pytest.raises(InstallerError) as exc:
        _press(rec, server_dir)
    assert "coa-bots-2.10 is out upstream, but its bots are not yet" in str(exc.value)
    assert "has no v2.10" in str(exc.value)
    assert "never builds a release with another release's bots" in str(exc.value)
    assert rec.clones == []
    assert [c for c in rec.calls if c.startswith("clone:")] == []
    assert rec.heads == before


# -- 8. unlistable -------------------------------------------------------------


def test_release_branches_that_cannot_be_listed_change_nothing(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    entry = _branching()
    core = entry.emulator.sources[0]
    rec.adverts.pop(core.url)
    rec.advert_errors.add(core.url)
    before = dict(rec.heads)
    with pytest.raises(InstallerError) as exc:
        _press(rec, server_dir)
    assert "could not list them" in str(exc.value)
    assert rec.clones == []
    assert rec.heads == before


def test_release_branches_with_no_matching_branch_say_so_not_unreachable(
    tmp_path: Path,
) -> None:
    rec, server_dir = _ready(tmp_path)
    entry = _branching()
    core = entry.emulator.sources[0]
    rec.adverts[core.url] = {}
    before = dict(rec.heads)
    with pytest.raises(InstallerError) as exc:
        _press(rec, server_dir)
    assert "lists none named coa-bots-<version>" in str(exc.value)
    assert "could not list them" not in str(exc.value)
    assert rec.clones == []
    assert rec.heads == before


def test_news_with_no_matching_branch_says_nothing(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    entry = _branching()
    rec.adverts[entry.emulator.sources[0].url] = {}
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert upstream.line(news) == ""
    assert all(row.behind is None and row.release == "" for row in news.sources)


# -- 9. news ------------------------------------------------------------------


def test_news_counts_against_the_newest_release_branch(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert upstream.line(news).startswith(
        "Upstream has new code since this server was built: server new release coa-bots-2.10"
    )
    assert (
        f"https://api.github.com/repos/{CORE}/compare/{OLD_A}...{NEW_B}?per_page=1&page=2"
        in rec.gets
    )
    row = next(s for s in news.sources if s.repo == CORE)
    assert row.release == "coa-bots-2.10"


def test_a_stale_bots_row_beside_a_fresh_core_row_is_still_counted(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    import json as _json

    cached = _json.loads((server_dir / upstream.UPSTREAM_FILE).read_text(encoding="utf-8"))
    stale = T0 - upstream.MAX_AGE_SECONDS - 1
    for row in cached["sources"]:
        if row["repo"] == BOTS:
            row["checked_unix"] = stale
    (server_dir / upstream.UPSTREAM_FILE).write_text(_json.dumps(cached), encoding="utf-8")
    rec.gets.clear()
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0 + 10)
    bots_row = next(s for s in news.sources if s.repo == BOTS)
    assert bots_row.release == "v2.10"
    assert any(f"/repos/{BOTS}/compare/" in url for url in rec.gets)
    assert not any(f"/repos/{CORE}/compare/" in url for url in rec.gets)
    core_row = next(s for s in news.sources if s.repo == CORE)
    assert core_row.release == "coa-bots-2.10"


# -- 10. held -----------------------------------------------------------------


def test_news_holds_a_release_whose_bots_are_not_out(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    entry = _branching()
    bots = entry.emulator.sources[1]
    rec.adverts[bots.url] = {}
    rec.github = {CORE: 0, BOTS: 0}
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    core_row = next(s for s in news.sources if s.repo == CORE)
    assert core_row.behind == 0
    assert core_row.held == upstream.bots_not_out_line("coa-bots-2.10", BOTS, "v2.10")
    assert upstream.line(news) == core_row.held
    adopted = upstream.UpstreamNews(
        checked_unix=news.checked_unix, sources=news.sources, adopted=True
    )
    assert upstream.line(adopted) == ""


# -- 11. refused ---------------------------------------------------------------


def test_a_refused_release_is_not_offered_again(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    state = native.read_state(server_dir, valid=())
    assert state is not None
    from dataclasses import replace

    native.write_state(server_dir, replace(state, refused_updates=((CORE, NEW_B),)))
    rec.gets.clear()
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0 + 100000)
    core_row = next(s for s in news.sources if s.repo == CORE)
    assert core_row.behind == 0
    assert not any(f"/repos/{CORE}/compare/" in url for url in rec.gets)


def test_a_baseline_refusal_holds_the_paired_release_in_the_news(tmp_path: Path) -> None:
    rec, server_dir, entry = _baseline_ready(tmp_path)
    dest = server_dir / entry.emulator.sources[0].dest
    rec.blob_ids[(dest, OLD_A, BASELINE)] = "f8f4001a" * 5
    rec.blob_ids[(dest, NEW_B, BASELINE)] = "1" * 40
    with pytest.raises(InstallerError):
        _press(rec, server_dir)
    news = _engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert upstream.line(news) == ""
    for row in news.sources:
        assert row.behind == 0
        assert row.release == ""


def test_pressing_again_after_a_baseline_refusal_refuses_without_cloning(
    tmp_path: Path,
) -> None:
    rec, server_dir, entry = _baseline_ready(tmp_path)
    dest = server_dir / entry.emulator.sources[0].dest
    rec.blob_ids[(dest, OLD_A, BASELINE)] = "f8f4001a" * 5
    rec.blob_ids[(dest, NEW_B, BASELINE)] = "1" * 40
    with pytest.raises(InstallerError):
        _press(rec, server_dir)
    rec.clones.clear()
    before = dict(rec.heads)
    with pytest.raises(InstallerError) as refused:
        _press(rec, server_dir)
    assert "was refused before: it brings a new world baseline" in str(refused.value)
    assert "wait for a Yu'lon release" in str(refused.value)
    assert rec.clones == []
    assert rec.heads == before


# -- 12. the guard -------------------------------------------------------------


def test_the_local_commit_guard_asks_the_branch_the_install_moved_to(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    rec.local_commit_branches.clear()
    rec.upstream[server_dir / _branching().emulator.sources[0].dest] = "e" * 40
    rec.github = {CORE: 3, BOTS: 0}
    _press(rec, server_dir)
    asked = dict(rec.local_commit_branches)
    entry = _branching()
    assert "coa-bots-2.10" in set(asked.values())
    rec2, server_dir2 = _ready(tmp_path / "second")
    list(_engine(rec2).update_to_latest(InstallOptions(server_dir=server_dir2)))
    asked2 = dict(rec2.local_commit_branches)
    assert entry.emulator.sources[0].branch == "coa-bots-2.9.1"
    bots_dest = entry.emulator.sources[1].dest.rsplit("/", 1)[-1]
    assert asked.get(bots_dest) == entry.emulator.sources[1].branch
    assert asked2.get(bots_dest) == entry.emulator.sources[1].branch


# -- 13. return ----------------------------------------------------------------


def test_return_to_the_pin_fetches_the_pinned_branch_and_lists_nothing(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    rec.gets.clear()
    rec.clones.clear()
    list(_engine(rec).update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    entry = _branching()
    core, bots = entry.emulator.sources[0], entry.emulator.sources[1]
    by_url = {spec.url: spec for spec in rec.clones}
    assert by_url[core.url].branch == "coa-bots-2.9.1"
    assert by_url[core.url].rev == core.rev
    assert by_url[bots.url].branch == bots.branch
    assert not any("info/refs" in url for url in rec.gets)


# -- 14/15. baseline -----------------------------------------------------------


def _baseline_ready(tmp_path: Path, name: str = "server") -> tuple[Recorder, Path, Any]:
    rec = Recorder()
    server_dir = tmp_path / name
    install(rec, server_dir)
    entry = _branching()
    dest = server_dir / entry.emulator.sources[0].dest
    rec.heads[dest] = OLD_A
    rec.upstream[dest] = NEW_B
    bots_dest = server_dir / entry.emulator.sources[1].dest
    rec.heads[bots_dest] = OLD_A
    rec.upstream[bots_dest] = TAG_C
    rec.clones.clear()
    rec.calls.clear()
    rec.gets.clear()
    core, bots = entry.emulator.sources[0], entry.emulator.sources[1]
    rec.adverts = {
        core.url: {
            "refs/heads/coa-bots-2.9.1": OLD_A,
            "refs/heads/coa-bots-2.10": NEW_B,
        },
        bots.url: {"refs/tags/v2.10^{}": TAG_C},
    }
    rec.github = {CORE: 5, BOTS: 5}
    return rec, server_dir, entry


def test_a_new_world_baseline_is_refused_and_remembered(tmp_path: Path) -> None:
    rec, server_dir, entry = _baseline_ready(tmp_path)
    dest = server_dir / entry.emulator.sources[0].dest
    rec.blob_ids[(dest, OLD_A, BASELINE)] = "f8f4001a" * 5
    rec.blob_ids[(dest, NEW_B, BASELINE)] = "1" * 40
    with pytest.raises(InstallerError) as exc:
        _press(rec, server_dir)
    assert "brings a new world baseline" in str(exc.value)
    assert BASELINE in str(exc.value)
    assert "Nothing was built" in str(exc.value)
    assert native.SOURCES_PUT_BACK_NOTE in str(exc.value)
    assert rec.heads[dest] == OLD_A
    state = native.read_state(server_dir, valid=())
    assert state is not None
    assert (CORE, NEW_B) in state.refused_updates
    assert not any(c.startswith("build") for c in rec.calls)


def test_an_unchanged_baseline_updates(tmp_path: Path) -> None:
    rec, server_dir, entry = _baseline_ready(tmp_path)
    lines = _press(rec, server_dir)
    assert any("is running on" in line for line in lines), lines


def test_an_unreadable_baseline_refuses_without_remembering(tmp_path: Path) -> None:
    rec, server_dir, entry = _baseline_ready(tmp_path)
    dest = server_dir / entry.emulator.sources[0].dest
    rec.blob_ids[(dest, OLD_A, BASELINE)] = None
    with pytest.raises(InstallerError) as exc:
        _press(rec, server_dir)
    assert "could not read whether the newest code" in str(exc.value)
    assert "world baseline" in str(exc.value)
    state = native.read_state(server_dir, valid=())
    assert state is not None
    assert state.refused_updates == ()


# -- 16. cache -----------------------------------------------------------------


def test_held_survives_the_cache(tmp_path: Path) -> None:
    news = upstream.UpstreamNews(
        checked_unix=T0,
        sources=(
            upstream.SourceNews(
                repo=CORE,
                label="server",
                behind=0,
                checked_unix=T0,
                follow="release_branches",
                held=upstream.bots_not_out_line("coa-bots-2.10", BOTS, "v2.10"),
            ),
        ),
    )
    upstream.write_cached(tmp_path, news)
    back = upstream.read_cached(tmp_path, [(CORE, "release_branches")], T0 + 10)
    assert back[CORE].held == news.sources[0].held


# -- 17. git -------------------------------------------------------------------


def test_blob_id_reads_the_commit_not_the_disk(tmp_path: Path) -> None:
    import subprocess

    from yulon import git as gitmod

    if not gitmod.git_available():
        pytest.skip("no host git")
    repo = tmp_path / "repo"
    folder = repo / "data" / "coa-world"
    folder.mkdir(parents=True)
    (folder / "baseline.json").write_text('{"v": 1}\n', encoding="utf-8")

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    run("init", "-q")
    run("add", ".")
    run("commit", "-q", "-m", "one")
    head = run("rev-parse", "HEAD")
    (folder / "baseline.json").write_text('{"v": 2}\n', encoding="utf-8")
    assert gitmod.RunnerGit().blob_id(repo, head, BASELINE) == run(
        "rev-parse", f"{head}:{BASELINE}"
    )
    assert gitmod.RunnerGit().blob_id(repo, head, "data/nope.json") == ""
    assert gitmod.blob_id_args(head, BASELINE) == ["ls-tree", head, "--", BASELINE]


# -- 18. databases -------------------------------------------------------------


def test_coa_update_copies_its_four_databases() -> None:
    entry = load_catalog().get("wow-coa")
    installer = AzerothCoreInstaller(entry, installers_root=resources.installers_dir())
    assert installer.snapshot_databases() == (
        "acore_auth",
        "acore_characters",
        "acore_world",
        "acore_playerbots",
    )
