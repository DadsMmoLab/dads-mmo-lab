"""Tests for the throttled update check (`check_with_cache`, T90).

One request a day, revalidated by ETag, and a cached feed that is re-judged
against whatever version is running now. GitHub allows 60 unauthenticated
requests an hour per IP, and the check before this one asked on every launch.

No test here touches the network: `fetch` is the seam, and `Server` below is
the whole of GitHub as far as these tests are concerned.
"""

from __future__ import annotations

import dataclasses
import email.message
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.support_update import FEED
from yulon import update
from yulon.update import (
    CHECK_INTERVAL_SECONDS,
    HttpAnswer,
    UpdateCheck,
    check_with_cache,
    evaluate_feed,
    should_announce,
    skip_version,
)
from yulon.update_state import UpdateState, load_update_state, save_update_state


class Clock:
    """The injected wall clock. Never `time.time()`: a day is a distance, not a wait."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Server:
    """Counts requests; answers 304 to a matching ETag, the way GitHub does."""

    def __init__(self, feed: str, etag: str = 'W/"one"') -> None:
        self.feed = feed
        self.etag = etag
        self.calls = 0
        self.sent: list[str | None] = []

    def __call__(self, url: str, if_none_match: str | None) -> HttpAnswer:
        self.calls += 1
        self.sent.append(if_none_match)
        if if_none_match == self.etag:
            return HttpAnswer(304, "", self.etag)
        return HttpAnswer(200, self.feed, self.etag)


def _check(server: object, clock: Clock, path: Path, **kw: object) -> UpdateCheck:
    return check_with_cache(
        current="0.8.66-Public",
        fetch=server,  # type: ignore[arg-type]
        state_path=path,
        now=clock,
        **kw,  # type: ignore[arg-type]
    )


def test_the_first_check_asks_and_remembers(tmp_path: Path) -> None:
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"

    assert _check(server, clock, path).latest == "v0.8.70-Public"

    state = load_update_state(path)
    assert (server.calls, state.etag, state.last_checked, state.feed) == (
        1,
        'W/"one"',
        1000.0,
        FEED,
    )


def test_within_a_day_nothing_is_asked_and_the_answer_is_the_same(tmp_path: Path) -> None:
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"

    first = _check(server, clock, path)
    clock.t += CHECK_INTERVAL_SECONDS - 1

    assert _check(server, clock, path) == first
    assert server.calls == 1


def test_after_a_day_it_revalidates_with_the_etag_and_a_304_keeps_the_feed(tmp_path: Path) -> None:
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)
    clock.t += CHECK_INTERVAL_SECONDS

    assert _check(server, clock, path).available

    assert server.sent == [None, 'W/"one"']
    # A 304 is an answer: the day starts again from it, or every launch asks.
    assert load_update_state(path).last_checked == clock.t


def test_force_asks_inside_the_day_and_still_sends_the_etag(tmp_path: Path) -> None:
    """The manual button ignores the 24 h rule; a 304 costs nothing against the limit."""
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)

    _check(server, clock, path, force=True)

    assert server.sent == [None, 'W/"one"']


def test_the_cached_feed_is_judged_against_the_version_running_now(tmp_path: Path) -> None:
    """Someone who updated by hand must not be told about the version they are running."""
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)

    after = check_with_cache(current="0.8.70-Public", fetch=server, state_path=path, now=clock)

    assert not after.available
    assert server.calls == 1


def test_offline_with_a_cache_still_knows_the_update_and_says_why(tmp_path: Path) -> None:
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)

    def down(url: str, etag: str | None) -> HttpAnswer:
        raise urllib.error.URLError("no route")

    result = check_with_cache(
        current="0.8.66-Public", fetch=down, state_path=path, now=clock, force=True
    )

    assert result.available
    assert "no route" in (result.error or "")


def test_offline_with_no_cache_is_no_update_and_an_error(tmp_path: Path) -> None:
    def down(url: str, etag: str | None) -> HttpAnswer:
        raise OSError("dns")

    result = check_with_cache(
        current="0.8.66-Public", fetch=down, state_path=tmp_path / "u.json", now=Clock(1.0)
    )

    assert not result.available
    assert result.error == "dns"


def test_a_rate_limit_answer_is_not_cached_over_a_good_feed(tmp_path: Path) -> None:
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)
    server.feed = json.dumps({"message": "API rate limit exceeded"})
    server.etag = 'W/"two"'
    clock.t += CHECK_INTERVAL_SECONDS

    assert _check(server, clock, path).available, "the good cached feed still answers"
    assert load_update_state(path).feed == FEED


def test_a_failed_check_does_not_start_the_day_again(tmp_path: Path) -> None:
    """Somebody who opened this on a train wants the next launch to try, not tomorrow."""
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)
    clock.t += CHECK_INTERVAL_SECONDS

    def down(url: str, etag: str | None) -> HttpAnswer:
        raise OSError("dns")

    check_with_cache(current="0.8.66-Public", fetch=down, state_path=path, now=clock)

    assert load_update_state(path).last_checked == 1000.0
    _check(server, clock, path)
    assert server.calls == 2, "the next launch asks again rather than waiting another day"


def test_a_304_with_no_cached_feed_asks_again_without_the_etag(tmp_path: Path) -> None:
    """An ETag without the body it describes is worse than no ETag at all."""
    path = tmp_path / "update.json"
    save_update_state(UpdateState(etag='W/"one"', feed=None), path)
    server = Server(FEED)

    assert _check(server, Clock(5.0), path).available
    assert server.sent == [None]


def test_a_clock_that_went_backwards_is_stale_not_fresh_forever(tmp_path: Path) -> None:
    """A machine whose clock was wrong, then corrected, must not stop checking for good."""
    server, path = Server(FEED), tmp_path / "update.json"
    _check(server, Clock(10_000_000.0), path)

    _check(server, Clock(5.0), path)

    assert server.calls == 2


def test_a_corrupt_cached_feed_is_refetched(tmp_path: Path) -> None:
    path = tmp_path / "update.json"
    save_update_state(UpdateState(last_checked=1000.0, feed="{broken"), path)
    server = Server(FEED)

    assert _check(server, Clock(1001.0), path).available
    assert server.calls == 1


def test_skip_hides_this_version_and_only_this_version(tmp_path: Path) -> None:
    path = tmp_path / "update.json"
    result = _check(Server(FEED), Clock(1.0), path)

    assert should_announce(result, path)
    assert skip_version("v0.8.70-Public", path) is True
    assert not should_announce(result, path)

    newer = dataclasses.replace(result, latest="v0.8.71-Public")
    assert should_announce(newer, path)
    assert load_update_state(path).feed == FEED, "skipping must not throw the cache away"


def test_nothing_available_is_never_announced(tmp_path: Path) -> None:
    assert not should_announce(evaluate_feed(FEED, "0.8.70-Public"), tmp_path / "u.json")


def test_urllib_turns_a_304_into_an_answer_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """urllib raises on 304. Here that is the cheapest possible answer, not a failure."""

    def raise_304(request: urllib.request.Request, timeout: float, context: object) -> object:
        assert request.get_header("If-none-match") == 'W/"one"'
        headers = email.message.Message()
        headers["ETag"] = 'W/"one"'
        raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", headers, None)

    monkeypatch.setattr(urllib.request, "urlopen", raise_304)

    assert update._urllib_fetch("https://example.invalid", 'W/"one"') == HttpAnswer(
        304, "", 'W/"one"'
    )


def test_any_other_http_error_is_still_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 (rate limited) and 500 are failures; only 304 is an answer."""

    def raise_403(request: urllib.request.Request, timeout: float, context: object) -> object:
        raise urllib.error.HTTPError(
            request.full_url, 403, "rate limited", email.message.Message(), None
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_403)

    with pytest.raises(urllib.error.HTTPError):
        update._urllib_fetch("https://example.invalid", None)


def test_a_failed_save_does_not_fail_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unwritable config dir costs one extra request tomorrow, not the answer today."""
    monkeypatch.setattr(update, "remember", lambda fields, path=None: False)
    server = Server(FEED)

    assert _check(server, Clock(1.0), tmp_path / "update.json").available


def test_a_skip_during_a_check_survives_the_checks_own_save(tmp_path: Path) -> None:
    """The lost update this file exists to stop, driven deterministically.

    The launch check loads the state, spends up to five seconds on the network,
    and saves. The player can press "Skip this version" in that window — the
    bar is up from an earlier check — and a check that saved the copy it loaded
    would write the skip straight back out of existence. The `fetch` here IS
    that window: it skips a version while the check is mid-flight.
    """
    path = tmp_path / "update.json"
    server = Server(FEED)

    def fetch_and_skip(url: str, if_none_match: str | None) -> HttpAnswer:
        answer = server(url, if_none_match)
        skip_version("v0.8.70-Public", path)
        return answer

    result = check_with_cache(
        current="0.8.66-Public", fetch=fetch_and_skip, state_path=path, now=Clock(1000.0)
    )

    state = load_update_state(path)
    assert state.skipped_version == "v0.8.70-Public", "the player's skip was overwritten"
    assert state.feed == FEED, "the freshly fetched feed was not kept"
    assert state.last_checked == 1000.0
    assert result.available, "the check still answers with what it fetched"
    assert not should_announce(result, path), "and the bar stays down, because it was skipped"


def test_a_hostile_body_that_raises_something_unexpected_still_answers(tmp_path: Path) -> None:
    """ "Never raises" is the contract `_UpdateWorker` depends on for its `done` signal.

    `RecursionError` is neither an `OSError` nor a `ValueError`, and a deeply
    nested JSON body produces one from `json.loads`. Injected rather than built:
    the subject is the boundary, not CPython's recursion limit.
    """

    def hostile(url: str, if_none_match: str | None) -> HttpAnswer:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")

    result = check_with_cache(
        current="0.8.66-Public", fetch=hostile, state_path=tmp_path / "u.json", now=Clock(1.0)
    )

    assert not result.available
    assert "recursion" in (result.error or "")


def test_a_hostile_body_with_a_cache_keeps_the_cached_answer(tmp_path: Path) -> None:
    server, clock, path = Server(FEED), Clock(1000.0), tmp_path / "update.json"
    _check(server, clock, path)

    def hostile(url: str, if_none_match: str | None) -> HttpAnswer:
        raise RecursionError("too deep")

    result = check_with_cache(
        current="0.8.66-Public", fetch=hostile, state_path=path, now=clock, force=True
    )

    assert result.available and "too deep" in (result.error or "")
