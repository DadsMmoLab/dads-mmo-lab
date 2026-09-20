"""Application self-update check (README §10, roadmap 5.4): check, notify, and ask.

Asks the GitHub Releases API which `-Public` release is newest, compares it with
the running `yulon.__version__`, and returns a typed `UpdateCheck` the UI turns
into a quiet banner and a what's-new dialog. Nothing is downloaded or replaced
here — "Update now" opens the download page (T90 plan 2); the swap is plan 3.
The HTTP call is a seam, so the check is unit-testable offline, and every
failure (offline, rate-limited, odd tag) degrades to "no update known", never to
a crash at launch.

Only a tag spelled `v1.2.3-Public` counts. This repository's release feed also
carries the test builds (`-fixtest`, `-DeckTest`) that the gates are cut from,
and until T90 the check compared the numeric triple alone — so whatever anyone
had tagged most recently was offered to every player as their next version.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import __version__
from yulon.log import get_logger
from yulon.platform import verify_context
from yulon.update_state import UpdateState, load_update_state, save_update_state

logger = get_logger(__name__)

RELEASES_API = "https://api.github.com/repos/DadsMmoLab/dads-mmo-lab/releases?per_page=20"
RELEASES_PAGE = "https://github.com/DadsMmoLab/dads-mmo-lab/releases"
"""Where a user is sent when the check has no better link.

`/releases/latest`, which this was until T90, means "latest NON-prerelease" —
and every release this project has ever cut is flagged `Pre-release`, so that
page answered 404 for every user the fallback ever ran for.
"""

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
PUBLIC_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-public$", re.IGNORECASE)
CHECKSUMS_NAME = "SHA256SUMS"
_TIMEOUT_SECONDS = 5.0

CHECK_INTERVAL_SECONDS = 24 * 60 * 60
"""How long an automatic answer is good for.

GitHub allows 60 unauthenticated requests an hour per IP, shared by everyone
behind it, and the check before T90 asked on every launch. A day is the
granularity a release is cut at; the manual button ignores this.
"""

HttpGetText = Callable[[str], str]


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable file of a release, as the API lists it."""

    name: str
    url: str
    size: int


@dataclass(frozen=True)
class UpdateCheck:
    """Outcome of one check. `available` is True only when a newer release exists."""

    current: str
    latest: str | None
    available: bool
    url: str
    error: str | None = None
    notes_markdown: str = ""
    """Every public release between the running version and the offered one, newest first.

    Markdown, because that is what the GitHub release body already is and what
    the dialog renders. Empty when there is nothing to offer.
    """
    assets: tuple[ReleaseAsset, ...] = ()
    has_checksums: bool = False


@dataclass(frozen=True)
class HttpAnswer:
    """One conditional GET's outcome: the status, the body, and the ETag to send next time."""

    status: int
    text: str
    etag: str | None


HttpFetch = Callable[[str, str | None], HttpAnswer]
"""`(url, if_none_match) -> HttpAnswer`. The seam `check_with_cache` is tested through."""


def parse_version(text: str) -> tuple[int, int, int] | None:
    """`v1.2.3` / `1.2.3` / `1.2.3-beta` → (1, 2, 3); anything else → None."""
    match = _VERSION.match(text.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` parses and is strictly greater than `current`."""
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def _urllib_get_text(url: str) -> str:
    """GET the releases API over `platform.verify_context()`'s root set.

    The check degrades to "no update known" on any failure, so an unverified
    connection would not crash anything — it would quietly decide, from an
    unauthenticated answer, which version the user is told to install. The
    verified context costs nothing here and removes that.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": f"yulon/{__version__}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(
        request, timeout=_TIMEOUT_SECONDS, context=verify_context()
    ) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


def _urllib_fetch(url: str, if_none_match: str | None) -> HttpAnswer:
    """The same GET with `If-None-Match`, over the same verified context.

    urllib RAISES on a 304, and here that is an answer rather than a failure: a
    conditional request answered 304 does not count against GitHub's 60-an-hour
    unauthenticated limit, so it is the cheapest thing this app can ask for.
    Every other status keeps raising, so a 403 (rate limited) or a 500 still
    reaches the caller's `except` and is reported as the failure it is.
    """
    headers = {"User-Agent": f"yulon/{__version__}", "Accept": "application/vnd.github+json"}
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(
            request, timeout=_TIMEOUT_SECONDS, context=verify_context()
        ) as resp:
            return HttpAnswer(
                int(resp.status),
                str(resp.read().decode("utf-8", errors="replace")),
                resp.headers.get("ETag"),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return HttpAnswer(304, "", exc.headers.get("ETag") if exc.headers else if_none_match)
        raise


def is_public_tag(tag: str) -> bool:
    """True for `v1.2.3-Public` only.

    Test tags (`-fixtest`, `-DeckTest`) share this repo's release feed and used
    to be offered to players, because only the numeric triple was compared
    (T90). `v0.6.59Public`, with no dash, is older than every build that can
    carry this code, so it is left out rather than special-cased in.
    """
    return PUBLIC_TAG.match(tag.strip()) is not None


def _public_releases(feed: object) -> list[tuple[tuple[int, int, int], dict[str, object]]]:
    """Published `-Public` releases, highest version first.

    `/releases` and not `/releases/latest`: that endpoint means "latest
    non-prerelease", and every release this project has ever cut is flagged
    `Pre-release`, so it answered 404 for every user the check ever ran for.
    `/releases` lists them all, and a prerelease here is a normal download —
    only a draft is invisible, so a draft is all that is skipped.

    Sorted by version rather than taken in feed order. `/releases` is
    newest-first *by creation date*, which is not the same thing: a re-cut of an
    old tag arrives at the top, and so does every test build.
    """
    if not isinstance(feed, list):
        return []
    found: list[tuple[tuple[int, int, int], dict[str, object]]] = []
    for entry in feed:
        if not isinstance(entry, dict) or entry.get("draft"):
            continue
        tag = str(entry.get("tag_name") or "")
        version = parse_version(tag)
        if is_public_tag(tag) and version is not None:
            found.append((version, entry))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return found


def _assets(release: dict[str, object]) -> tuple[ReleaseAsset, ...]:
    """The release's downloadable files, skipping anything the API did not shape right.

    A malformed entry is dropped rather than raising: the asset list decides
    what plan 3 can install, and an odd one of them must not cost the user the
    banner telling them an update exists at all.
    """
    raw = release.get("assets")
    if not isinstance(raw, list):
        return ()
    out: list[ReleaseAsset] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name, url, size = item.get("name"), item.get("browser_download_url"), item.get("size")
        # `not isinstance(size, bool)`: `True` is an `int` in Python, and a
        # one-byte download is not what a JSON `true` meant.
        if (
            isinstance(name, str)
            and isinstance(url, str)
            and isinstance(size, int)
            and not isinstance(size, bool)
        ):
            out.append(ReleaseAsset(name, url, size))
    return tuple(out)


def evaluate_feed(feed_text: str, current: str) -> UpdateCheck:
    """Decide, from a releases feed, what to offer someone running `current`.

    Pure and offline: the one place that knows what a feed MEANS, so the cached
    copy in `update.json` can be re-judged against whatever version is running
    now without asking GitHub again. Raises `ValueError` on a body that is not
    JSON at all, and nothing else — every caller here catches it.
    """
    releases = _public_releases(json.loads(feed_text))
    if not releases:
        # An empty repo, a feed of nothing but test tags, or an answer that is
        # not a feed at all — a rate-limit body is valid JSON too.
        logger.info("update check: no public release in the feed")
        return UpdateCheck(current, None, False, RELEASES_PAGE, error="no public release")
    _, newest = releases[0]
    tag = str(newest.get("tag_name"))
    url = str(newest.get("html_url") or RELEASES_PAGE)
    if not is_newer(tag, current):
        logger.info(f"update check: current={current} latest={tag} newer=False")
        return UpdateCheck(current, tag, False, url)
    mine = parse_version(current)
    sections = [
        f"## {entry.get('tag_name')}\n\n{str(entry.get('body') or '').strip()}\n"
        for version, entry in releases
        if mine is not None and version > mine
    ]
    assets = _assets(newest)
    logger.info(f"update check: current={current} latest={tag} newer=True")
    return UpdateCheck(
        current,
        tag,
        True,
        url,
        notes_markdown="\n".join(sections),
        assets=assets,
        has_checksums=any(a.name == CHECKSUMS_NAME for a in assets),
    )


def check_for_update(
    current: str = __version__,
    *,
    http_get: HttpGetText = _urllib_get_text,
    api_url: str = RELEASES_API,
) -> UpdateCheck:
    """One unconditional request, judged. Never raises.

    `check_with_cache()` is what the app runs; this is the seam underneath it
    that takes a plain text-getter, and what a caller with its own HTTP wants.
    """
    try:
        return evaluate_feed(http_get(api_url), current)
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
        logger.info(f"update check skipped: {exc}")
        return UpdateCheck(current, None, False, RELEASES_PAGE, error=str(exc))


def _from_cache(state: UpdateState, current: str) -> UpdateCheck | None:
    """What the remembered feed says about `current`, or None if it says nothing usable.

    The FEED is cached, not the verdict, so a user who updated by hand between
    two launches is judged against the version they are running now rather than
    told again about the one they just installed.
    """
    if not state.feed:
        return None
    try:
        result = evaluate_feed(state.feed, current)
    except ValueError:
        return None
    return None if result.error else result


def check_with_cache(
    *,
    force: bool = False,
    current: str = __version__,
    fetch: HttpFetch = _urllib_fetch,
    api_url: str = RELEASES_API,
    state_path: Path | None = None,
    now: Callable[[], float] = time.time,
) -> UpdateCheck:
    """The check the app runs: at most one request a day, revalidated by ETag. Never raises.

    `now` is injected and never `time.time()` inside a test: a day is a distance
    between two numbers, not something to wait for.

    `0 <= moment - state.last_checked` and not just `<`: a clock that was wrong
    and has been corrected leaves a stamp in the future, and a check whose
    freshness window opens backwards would never ask again on that machine.
    """
    state = load_update_state(state_path)
    cached = _from_cache(state, current)
    moment = now()
    if (
        not force
        and cached is not None
        and 0 <= moment - state.last_checked < CHECK_INTERVAL_SECONDS
    ):
        return cached
    try:
        # The ETag goes only with the body it describes. Sending one whose feed
        # was lost or unreadable buys a 304 with nothing to read it against.
        answer = fetch(api_url, state.etag if cached is not None else None)
        if answer.status == 304 and cached is not None:
            save_update_state(state.model_copy(update={"last_checked": moment}), state_path)
            return cached
        fresh = evaluate_feed(answer.text, current)
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
        # `HTTPError` IS a `URLError`, so a 403 or a 500 lands here too.
        logger.info(f"update check failed: {exc}")
        if cached is not None:
            # Offline with a cache still knows about the update; the error says
            # why the answer might be old, and a manual check shows it.
            return dataclasses.replace(cached, error=str(exc))
        return UpdateCheck(current, None, False, RELEASES_PAGE, error=str(exc))
    if fresh.error:
        # A rate-limit body parses as JSON and holds no release. Caching it over
        # a good feed would throw away the only answer this app has.
        return cached if cached is not None else fresh
    save_update_state(
        state.model_copy(update={"last_checked": moment, "etag": answer.etag, "feed": answer.text}),
        state_path,
    )
    return fresh


def skip_version(tag: str, state_path: Path | None = None) -> bool:
    """Remember that the player does not want this version. False if it was not written.

    A `model_copy` of what is on disk, so the cached feed and ETag survive: a
    skip must not cost the next launch a request.
    """
    state = load_update_state(state_path)
    return save_update_state(state.model_copy(update={"skipped_version": tag}), state_path)


def should_announce(result: UpdateCheck, state_path: Path | None = None) -> bool:
    """Whether an unprompted banner may say this. A skipped version says nothing.

    A NEWER release than the skipped one un-hides it: "Skip this version" is
    about one version, not about updates.
    """
    return result.available and result.latest != load_update_state(state_path).skipped_version
