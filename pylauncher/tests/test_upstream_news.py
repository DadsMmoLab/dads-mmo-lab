"""T124: the Server tab says when upstream has code this server was not built from.

The engine half: what is counted, against what, how often GitHub is asked, and
when the answer is thrown away. The label and the job runner are in
`test_controller_view.py`, beside the version line they sit under.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

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
        upstream.https_get("https://api.github.com/x", "application/json")
    assert seen["context"] is upstream.platform.verify_context()
    assert isinstance(urllib.error.URLError("x"), OSError), "the failure the engine catches"
