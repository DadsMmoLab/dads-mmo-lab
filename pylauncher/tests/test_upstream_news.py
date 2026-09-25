"""T124: the Server tab says when upstream has code this server was not built from.

The engine half: what is counted, against what, how often GitHub is asked, and
when the answer is thrown away. The label and the job runner are in
`test_controller_view.py`, beside the version line they sit under.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import UNGUARDED_HTTPS_GET
from tests.support_native import ENTRY, Recorder, engine, install
from tests.test_update_to_latest import OLD, _press, _ready
from yulon.catalog import native, upstream
from yulon.catalog.installer import InstallerError, InstallOptions

DAY = 24 * 60 * 60
T0 = 1_790_000_000
CORE = "mod-playerbots/azerothcore-wotlk"
BOTS = "mod-playerbots/mod-playerbots"


def _news(rec: Recorder, server_dir: Path, now: int = T0) -> upstream.UpstreamNews:
    return engine(rec).upstream_news(InstallOptions(server_dir=server_dir), now=now)


def _installed(tmp_path: Path) -> tuple[Recorder, Path]:
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
    rec.github = {CORE: 300, BOTS: 50}
    rec.gets.clear()
    return rec, server_dir


def test_the_count_is_asked_against_each_head_and_said_per_source(tmp_path: Path) -> None:
    """The WotLK shape measured on 2026-09-25: the core 300 behind, the bots 50.

    Asked of GitHub against the HEAD the checkout is ON, on the branch the
    catalog names, and on page two so the megabyte of diffs on page one is not
    downloaded to read one number.
    """
    rec, server_dir = _installed(tmp_path)
    news = _news(rec, server_dir)
    assert [(s.repo, s.label, s.behind) for s in news.sources] == [
        (CORE, "server", 300),
        (BOTS, "mod-playerbots", 50),
    ]
    assert rec.gets == [
        f"https://api.github.com/repos/{CORE}/compare/{OLD}...Playerbot?per_page=1&page=2",
        f"https://api.github.com/repos/{BOTS}/compare/{OLD}...master?per_page=1&page=2",
    ]
    assert upstream.line(news) == (
        "Upstream has new code since this server was built: server 300 commits, "
        "mod-playerbots 50 commits. “Update the server to latest…” on the "
        "Modules tab brings it in."
    )


def test_one_commit_is_singular_and_a_source_with_nothing_new_is_left_out(
    tmp_path: Path,
) -> None:
    rec, server_dir = _installed(tmp_path)
    rec.github = {CORE: 0, BOTS: 1}
    said = upstream.line(_news(rec, server_dir))
    assert said.startswith(
        "Upstream has new code since this server was built: mod-playerbots 1 commit. "
    )


def test_nothing_new_says_nothing(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    rec.github = {CORE: 0, BOTS: 0}
    news = _news(rec, server_dir)
    assert [s.behind for s in news.sources] == [0, 0]
    assert upstream.line(news) == ""


def test_no_network_says_nothing_and_raises_nothing(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    rec.github = {}
    news = _news(rec, server_dir)
    assert [s.behind for s in news.sources] == [None, None]
    assert not news.answered()
    assert upstream.line(news) == ""


def test_github_answering_something_that_is_not_a_count_is_could_not_ask(
    tmp_path: Path,
) -> None:
    """A rate-limit body, HTML, a negative -- none of them is zero, all of them are None."""
    rec, server_dir = _installed(tmp_path)
    bodies = [b'{"message": "API rate limit exceeded"}', b"<html>", b'{"ahead_by": -1}', b"[]"]
    for day, body in enumerate(bodies):
        news = engine(rec, upstream_get=lambda url, accept, body=body: body).upstream_news(
            InstallOptions(server_dir=server_dir), now=T0 + day * DAY
        )
        assert [s.behind for s in news.sources] == [None, None], body


def test_the_answer_is_kept_for_a_day_and_a_second_open_asks_nobody(tmp_path: Path) -> None:
    """At most one set of requests per install per day, whatever the tab does."""
    rec, server_dir = _installed(tmp_path)
    first = _news(rec, server_dir, T0)
    assert len(rec.gets) == 2
    assert (server_dir / upstream.UPSTREAM_FILE).is_file()
    assert (server_dir / native.STATE_FILE).is_file(), "beside the install record"
    rec.github = {CORE: 999, BOTS: 999}
    heads_read = sum(1 for c in rec.calls if c.startswith("head-sha:"))
    again = _news(rec, server_dir, T0 + DAY - 1)
    assert len(rec.gets) == 2, "a second open within the day went to the network"
    assert sum(1 for c in rec.calls if c.startswith("head-sha:")) == heads_read
    assert again == first
    later = _news(rec, server_dir, T0 + DAY)
    assert len(rec.gets) == 4
    assert [s.behind for s in later.sources] == [999, 999]


def test_no_network_is_asked_again_within_the_hour_but_not_on_every_press(
    tmp_path: Path,
) -> None:
    rec, server_dir = _installed(tmp_path)
    rec.github = {}
    _news(rec, server_dir, T0)
    _news(rec, server_dir, T0 + upstream.RETRY_SECONDS - 1)
    assert len(rec.gets) == 2, "a Refresh pressed on a machine with no network was a request"
    rec.github = {CORE: 3, BOTS: 0}
    news = _news(rec, server_dir, T0 + upstream.RETRY_SECONDS)
    assert len(rec.gets) == 4
    assert [s.behind for s in news.sources] == [3, 0]


def test_a_reading_from_the_future_or_about_other_sources_is_not_served(
    tmp_path: Path,
) -> None:
    rec, server_dir = _installed(tmp_path)
    _news(rec, server_dir, T0)
    _news(rec, server_dir, T0 - 60)
    assert len(rec.gets) == 4, "a clock set back served a reading from its future"
    path = server_dir / upstream.UPSTREAM_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sources"] = payload["sources"][:1]
    path.write_text(json.dumps(payload), encoding="utf-8")
    _news(rec, server_dir, T0)
    assert len(rec.gets) == 6, "a reading about another set of sources was served"


def test_a_damaged_cache_is_asked_again_rather_than_trusted(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    (server_dir / upstream.UPSTREAM_FILE).write_text("{", encoding="utf-8")
    assert [s.behind for s in _news(rec, server_dir).sources] == [300, 50]
    assert len(rec.gets) == 2


def test_a_folder_with_no_install_record_gets_no_file(tmp_path: Path) -> None:
    rec = Recorder()
    rec.github = {CORE: 1, BOTS: 1}
    folder = tmp_path / "not-an-install"
    folder.mkdir()
    _news(rec, folder)
    assert not (folder / upstream.UPSTREAM_FILE).exists()


def test_a_head_that_cannot_be_read_is_not_asked_about(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    rec.git_reads = False
    news = _news(rec, server_dir)
    assert [s.behind for s in news.sources] == [None, None]
    assert rec.gets == []


def test_the_database_repository_is_not_counted(tmp_path: Path) -> None:
    """TBC's `tbc-db` stays on its pin through the update, so its news is not the button's."""
    from tests.test_update_to_latest import _tbc

    rec, server_dir, made = _tbc(tmp_path)
    rec.github = {"cmangos/mangos-tbc": 18, "cmangos/playerbots": 49, "cmangos/tbc-db": 17}
    news = made.upstream_news(InstallOptions(server_dir=server_dir), now=T0)
    assert [(s.label, s.behind) for s in news.sources] == [("server", 18), ("playerbots", 49)]
    assert not any("tbc-db" in url for url in rec.gets)
    # CMaNGOS names no branch, so the comparison is against the default branch.
    assert all(url.endswith("...HEAD?per_page=1&page=2") for url in rec.gets)


@pytest.mark.parametrize(
    ("repo", "slug"),
    [
        ("cmangos/mangos-tbc", "cmangos/mangos-tbc"),
        ("https://github.com/Sagiroth/TortoiseBots.git", "Sagiroth/TortoiseBots"),
        ("https://github.com/Sagiroth/TortoiseBots", "Sagiroth/TortoiseBots"),
        ("https://gitlab.com/x/y.git", None),
        ("https://codeberg.org/x/y", None),
    ],
)
def test_only_github_is_asked(repo: str, slug: str | None) -> None:
    assert upstream.github_slug(repo) == slug


# -- wired into the route the tab holds -------------------------------------


class _CountingEngine:
    """Stands in for the engine behind `install_wiring`; answers a marked reading."""

    def __init__(self, asked: list[Path]) -> None:
        self.asked = asked

    def sources_that_move(self) -> tuple[object, ...]:
        return ENTRY.emulator.sources

    def upstream_news(
        self, options: InstallOptions | None = None, *, now: int | None = None
    ) -> upstream.UpstreamNews:
        assert options is not None and options.server_dir is not None
        self.asked.append(options.server_dir)
        return upstream.UpstreamNews(T0, (upstream.SourceNews("x/y", "server", 7),))


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-tortoise"])
def test_the_route_the_tab_holds_carries_the_count(
    game: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through `ControllerServices.for_entry()`: the wiring, and Tortoise's botpool wrap.

    The review replaced `upstream_news=` in `install_wiring` with `None` and
    133 tests still passed. This drives the real factory, so a route that
    loses the field -- at the wiring, or in `botpool.wrap_route()`'s
    `replace()` -- fails here.
    """
    from yulon import install_wiring
    from yulon.catalog.catalog import load_catalog
    from yulon.ui.controller_view import ControllerServices

    asked: list[Path] = []
    monkeypatch.setattr(install_wiring, "installer_for", lambda _entry: _CountingEngine(asked))
    entry = load_catalog().get(game)
    server_dir = tmp_path / "server"
    route = ControllerServices.for_entry(entry, server_dir).update_to_latest
    assert route is not None
    assert route.upstream_news is not None, f"{game}'s route lost its count"
    assert upstream.line(route.upstream_news()).startswith("Upstream has new code")
    assert asked == [server_dir]
    bare = install_wiring.update_to_latest_for_app(entry, server_dir)
    assert bare is not None and bare.upstream_news is not None


# -- dropped when a press moves the sources ---------------------------------


def _cached(rec: Recorder, server_dir: Path) -> None:
    rec.github = {CORE: 300, BOTS: 50}
    _news(rec, server_dir)
    assert (server_dir / upstream.UPSTREAM_FILE).is_file()


def test_an_update_drops_the_count(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _cached(rec, server_dir)
    _press(rec, server_dir)
    assert not (server_dir / upstream.UPSTREAM_FILE).exists()


def test_a_return_to_the_pin_drops_the_count(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    _cached(rec, server_dir)
    _press(rec, server_dir, to_pin=True)
    assert not (server_dir / upstream.UPSTREAM_FILE).exists()


def test_a_press_that_fails_after_moving_drops_the_count_too(tmp_path: Path) -> None:
    """The sources went back, but a tab that counted mid-press cached the moved HEADs."""
    rec, server_dir = _ready(tmp_path)
    _cached(rec, server_dir)
    rec.build_result = native.docker.AttachedRun(2, ("boom",))

    def recount_mid_press(dest: Path) -> None:
        # A tab re-counting while the press runs, i.e. a cache written after
        # the first drop and before the press ended.
        _cached(rec, server_dir)

    rec.on_clone = recount_mid_press
    with pytest.raises(InstallerError):
        _press(rec, server_dir)
    assert not (server_dir / upstream.UPSTREAM_FILE).exists()


def test_a_count_asked_while_the_press_runs_is_not_served_the_pre_press_figure(
    tmp_path: Path,
) -> None:
    """The FIRST drop: before any source moves, the day's cache is already gone.

    Without it a tab that asked mid-press (a second window, a Refresh landing
    as the press starts) would be served "300 commits" about HEADs that are
    being moved under it.
    """
    rec, server_dir = _ready(tmp_path)
    _cached(rec, server_dir)
    repos = [s.repo for s in ENTRY.emulator.sources]
    seen: list[dict[str, upstream.SourceNews]] = []
    rec.on_clone = lambda dest: seen.append(upstream.read_cached(server_dir, repos, T0 + 1))
    _press(rec, server_dir)
    assert seen and all(found == {} for found in seen), seen


def _recounting(rec: Recorder, server_dir: Path) -> None:
    """A tab re-counting while the press runs: a cache written after the first drop."""
    rec.on_clone = lambda dest: _cached(rec, server_dir)


def test_a_count_cached_mid_press_is_dropped_when_the_update_succeeds(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _recounting(rec, server_dir)
    _press(rec, server_dir)
    assert not (server_dir / upstream.UPSTREAM_FILE).exists()


def test_a_count_cached_mid_press_is_dropped_when_a_step_before_the_build_fails(
    tmp_path: Path,
) -> None:
    rec, server_dir = _ready(tmp_path)
    _recounting(rec, server_dir)

    def refuse(*args: object, **kwargs: object) -> Iterator[str]:
        raise InstallerError("the carried patch no longer applies")

    made = engine(rec)
    made.check_carried_patches = refuse  # type: ignore[method-assign]
    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert not (server_dir / upstream.UPSTREAM_FILE).exists()


def test_a_count_cached_mid_press_is_dropped_when_the_press_is_abandoned(
    tmp_path: Path,
) -> None:
    """A generator closed half way raises GeneratorExit, which no handler catches."""
    rec, server_dir = _ready(tmp_path)
    _recounting(rec, server_dir)
    press = engine(rec).update_to_latest(InstallOptions(server_dir=server_dir))
    for _line in press:
        if (server_dir / upstream.UPSTREAM_FILE).exists():
            break
    assert (server_dir / upstream.UPSTREAM_FILE).exists(), "the fixture never recounted"
    press.close()
    assert not (server_dir / upstream.UPSTREAM_FILE).exists()


def test_one_source_that_failed_is_asked_again_within_the_hour_and_only_it(
    tmp_path: Path,
) -> None:
    """A 403 on one source must not hide it for a day, nor re-ask the one that answered."""
    rec, server_dir = _installed(tmp_path)
    rec.github = {CORE: 300}
    first = _news(rec, server_dir, T0)
    assert [s.behind for s in first.sources] == [300, None]
    assert len(rec.gets) == 2
    _news(rec, server_dir, T0 + upstream.RETRY_SECONDS - 1)
    assert len(rec.gets) == 2, "asked again before the hour was up"
    rec.github = {CORE: 999, BOTS: 50}
    later = _news(rec, server_dir, T0 + upstream.RETRY_SECONDS)
    assert rec.gets[2:] == [
        f"https://api.github.com/repos/{BOTS}/compare/{OLD}...master?per_page=1&page=2"
    ]
    assert [s.behind for s in later.sources] == [300, 50], "the answered source kept its day"
    _news(rec, server_dir, T0 + upstream.RETRY_SECONDS + 60)
    assert len(rec.gets) == 3
    after_a_day = _news(rec, server_dir, T0 + DAY)
    assert [s.behind for s in after_a_day.sources] == [999, 50]
    assert rec.gets[3:] == [
        f"https://api.github.com/repos/{CORE}/compare/{OLD}...Playerbot?per_page=1&page=2"
    ]


def test_the_default_github_seam_is_looked_up_when_called_so_the_suite_guard_sees_it() -> None:
    """`Seams().upstream_get` must reach the patched `upstream.https_get` (conftest's guard)."""
    with pytest.raises(AssertionError, match="asked GitHub"):
        native.Seams().upstream_get("https://api.github.com/repos/x/y/compare/a...b", "x")


def test_a_refused_press_keeps_the_count(tmp_path: Path) -> None:
    """Nothing moved, so the reading is still true and asking again would be a wasted request."""
    rec, server_dir = _ready(tmp_path)
    _cached(rec, server_dir)
    rec.edits[server_dir / ENTRY.emulator.sources[1].dest] = ("src/x.cpp",)
    with pytest.raises(InstallerError):
        _press(rec, server_dir)
    assert (server_dir / upstream.UPSTREAM_FILE).is_file()


def test_the_real_get_is_verified_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """`https_get` hands urlopen the verified context and refuses an unbounded body."""
    seen: dict[str, object] = {}

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, n: int) -> bytes:
            seen["n"] = n
            return b"x" * n

    def fake(request: object, timeout: float, context: object) -> _Resp:
        seen["context"] = context
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(upstream.urllib.request, "urlopen", fake)
    with pytest.raises(OSError):
        UNGUARDED_HTTPS_GET("https://api.github.com/x", "application/json")
    assert seen["context"] is upstream.platform.verify_context()
    assert isinstance(urllib.error.URLError("x"), OSError), "the failure the engine catches"
