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
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from yulon import __version__
from yulon.log import get_logger
from yulon.platform import verify_context
from yulon.update_state import UpdateState, load_update_state, remember

logger = get_logger(__name__)

RELEASES_REPO = "DadsMmoLab/dads-mmo-lab"
"""The repository the app asks about, written once.

Both URLs below and the rule in `safe_release_url()` are derived from it, so a
gate that points the check at a fork cannot end up trusting one repo and
opening another.
"""

RELEASES_API = f"https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=100"
"""100, the API's maximum, and not the 20 this asked for until the cold review.

The feed is ordered by creation date, so every test tag cut since the last
`-Public` release sits in front of it: twenty `-fixtest` builds — a week of
gate work — would push the newest public release off the end of the page, and
the check would then answer "no public release" and go on answering it from
the cached feed for a day at a time. A hundred is one request either way.
"""

RELEASES_PAGE = f"https://github.com/{RELEASES_REPO}/releases"
"""Where a user is sent when the check has no better link.

`/releases/latest`, which this was until T90, means "latest NON-prerelease" —
and every release this project has ever cut is flagged `Pre-release`, so that
page answered 404 for every user the fallback ever ran for.
"""

_VERSION = re.compile(r"^[vV]?(\d{1,9})\.(\d{1,9})\.(\d{1,9})(?![\d.])")
r"""Three numbers, and nothing that would make them mean something else.

Every part of this was a defect before the review of 2026-09-21, and the same
pattern goes into plan 1's `build/release_notes.py` verbatim — two copies of
one rule, pinned against each other by
`test_the_build_script_orders_versions_exactly_as_the_app_does`.

* `(?![\d.])` — without it there was no end anchor, so `v0.8.7.1` parsed
  SILENTLY as `.7` and `v1.2.3.4-Public` as `.3`. A fourth number is not this
  scheme, and reading one as though it were is how a release gets offered under
  the wrong number.
* `[vV]` — `PUBLIC_TAG` is `re.IGNORECASE`, so a `V0.8.7-Public` tag passes the
  public-tag filter and then keyed to None, and `_public_releases` ANDs the
  two: the release was dropped without a word.
* `{1,9}` — a tag is not a promise to be sane. CPython refuses `int()` on more
  than 4300 digits, so `v0.8.` + 5000 nines raised `ValueError` straight out of
  `evaluate_feed`, whose contract is "ValueError on non-JSON only". Nine digits
  is far past any real version, and the tenth digit is REFUSED rather than
  trimmed, because the lookahead sees it.
"""
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


def version_key(text: str) -> tuple[int, int, Fraction] | None:
    """What orders two Yu'lon versions. `v1.2.3` / `1.2.3` / `1.2.3-beta`; else None.

    **Major and minor are whole numbers; the LAST number is a decimal fraction
    of its digits** (owner, 2026-09-21). `65` is 65/100 and `7` is 7/10, so
    `.6 < .65 < .66 < .69 < .7`, and `.7 == .70` — equal, therefore not newer.

    This is not a house style, it is what this project's tags MEAN, and the
    measurement is the tag dates: `v0.8.7-Public` was cut on **2026-09-19**,
    after `v0.8.65-Public` on **2026-09-13**. The whole history reads the same
    way —

        0.6.5 → 0.6.51 → 0.6.52 → 0.6.53 → 0.6.55 → 0.6.57 → 0.6.58 →
        0.6.59 → 0.8.0 → 0.8.4 → 0.8.5 → 0.8.6 → 0.8.65 → 0.8.7

    — and the fork's `-fixtest` tags .66 .67 .68 .69 sit between .65 and .7.
    Compared as integers 65 > 7, so nobody running 0.8.65 or 0.8.66 would ever
    be offered 0.8.7; and picking the newest BY VERSION made that worse than
    the first-in-feed rule it replaced, because it actively named v0.8.65 the
    newest release in a feed that had v0.8.7 in it.

    The bump that proves the rule is upstream's own: commit `3534587b` sets
    `__version__ = "0.8.70-Public"` and its tag is **`v0.8.7-Public`**. To the
    person cutting the release those are one release, which is exactly what
    `.7 == .70` says — and it is why a build calling itself `0.8.70-Public` is
    correctly offered nothing (measured: `current=0.8.70-Public
    latest=v0.8.7-Public newer=False`).

    `Fraction`, never a float: `0.65` and `0.7` are both inexact in binary, and
    an ordering that decides releases may not be decided by a rounding.

    **A leading zero is meant literally:** `.05` is five hundredths, so
    `0.8.0 < 0.8.05 < 0.8.1`. The digits ARE the number — that is the whole
    rule — and a zero is a digit like any other.

    **The known cost, stated rather than worked around:** `0.8.10` orders
    BEFORE `0.8.9`, because `.10` is a tenth and `.9` is nine tenths. This
    scheme has never produced a tag like that, and every rule that would
    special-case it also changes the meaning of the tags that do exist.

    What `_VERSION` refuses — a fourth number, a tenth digit — is refused
    rather than read as something smaller; its docstring has each case.

    There is deliberately no second ordering in this module. The triple-valued
    `parse_version()` this replaces is gone rather than kept beside it: two
    orderings that disagree about `0.8.7` is the defect, not the fix.
    """
    match = _VERSION.match(text.strip())
    if not match:
        return None
    last = match.group(3)
    return int(match.group(1)), int(match.group(2)), Fraction(int(last), 10 ** len(last))


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` parses and is strictly greater than `current`. See `version_key`."""
    a, b = version_key(latest), version_key(current)
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


TOTAL_FETCH_SECONDS = 15.0
"""A wall-clock bound on the WHOLE fetch, which `timeout=` is not.

`urlopen(timeout=…)` is per socket operation: a server that sends a byte every
four seconds never trips it, and the launch check's thread lives for as long as
it keeps sending. While that thread is alive the header button answers "Yu'lon
is already checking for updates" — for ever (second cold review, 2026-09-21).
Fifteen seconds is three times the per-read bound and far past any real answer.
"""

MAX_FEED_BYTES = 4 * 1024 * 1024
"""How much body is read before the answer is refused as not-a-feed.

A hundred releases with their notes is a few hundred kB. Four megabytes is an
order of magnitude past that and still small enough to hold twice over while
`json.loads` runs.
"""

_READ_CHUNK = 64 * 1024


def _urllib_fetch(
    url: str,
    if_none_match: str | None,
    *,
    now: Callable[[], float] = time.monotonic,
    deadline: float = TOTAL_FETCH_SECONDS,
) -> HttpAnswer:
    """The same GET with `If-None-Match`, over the same verified context.

    urllib RAISES on a 304, and here that is an answer rather than a failure: a
    conditional request answered 304 does not count against GitHub's 60-an-hour
    unauthenticated limit, so it is the cheapest thing this app can ask for.
    Every other status keeps raising, so a 403 (rate limited) or a 500 still
    reaches the caller's `except` and is reported as the failure it is.

    The body is read in chunks against `TOTAL_FETCH_SECONDS` and
    `MAX_FEED_BYTES` rather than in one `read()`, because neither a trickle nor
    a flood is a thing `timeout=` can see. `now` is injected so a test can say
    what a slow server costs without waiting for it.
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
                _read_bounded(resp, now, now() + deadline),
                resp.headers.get("ETag"),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return HttpAnswer(304, "", exc.headers.get("ETag") if exc.headers else if_none_match)
        raise


def _read_bounded(resp: object, now: Callable[[], float], stop_at: float) -> str:
    """Read a response body under a wall clock and a size cap. See `_urllib_fetch`."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = resp.read(_READ_CHUNK)  # type: ignore[attr-defined]
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_FEED_BYTES:
            raise ValueError(f"the releases feed is too large: over {MAX_FEED_BYTES} bytes")
        if now() >= stop_at:
            raise TimeoutError(f"the releases feed was still arriving after {TOTAL_FETCH_SECONDS}s")
    return b"".join(chunks).decode("utf-8", errors="replace")


def is_public_tag(tag: str) -> bool:
    """True for `v1.2.3-Public` only.

    Test tags (`-fixtest`, `-DeckTest`) share this repo's release feed and used
    to be offered to players, because only the numeric triple was compared
    (T90). `v0.6.59Public`, with no dash, is older than every build that can
    carry this code, so it is left out rather than special-cased in.
    """
    return PUBLIC_TAG.match(tag.strip()) is not None


VersionKey = tuple[int, int, Fraction]
"""What `version_key()` answers, and the only thing this module sorts releases by."""


def _public_releases(feed: object) -> list[tuple[VersionKey, dict[str, object]]]:
    """Published `-Public` releases, highest version first.

    `/releases` and not `/releases/latest`: that endpoint means "latest
    non-prerelease", and every release this project has ever cut is flagged
    `Pre-release`, so it answered 404 for every user the check ever ran for.
    `/releases` lists them all, and a prerelease here is a normal download —
    only a draft is invisible, so a draft is all that is skipped.

    Sorted by version rather than taken in feed order. `/releases` is
    newest-first *by creation date*, which is not the same thing: a re-cut of an
    old tag arrives at the top, and so does every test build. Sorted by
    `version_key`, so `v0.8.7` outranks `v0.8.65` — which is what their dates
    say and what an integer comparison got backwards.
    """
    if not isinstance(feed, list):
        return []
    found: list[tuple[VersionKey, dict[str, object]]] = []
    for entry in feed:
        if not isinstance(entry, dict) or entry.get("draft"):
            continue
        tag = str(entry.get("tag_name") or "")
        version = version_key(tag)
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
        return UpdateCheck(current, tag, False, url)
    mine = version_key(current)
    sections = [
        f"## {entry.get('tag_name')}\n\n{str(entry.get('body') or '').strip()}\n"
        for version, entry in releases
        if mine is not None and version > mine
    ]
    assets = _assets(newest)
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
        return _log_check(evaluate_feed(http_get(api_url), current), ASKED_GITHUB)
    except Exception as exc:  # boundary: "never raises" is the whole contract
        logger.info(f"update check skipped: {type(exc).__name__}: {exc}")
        logger.debug("the update check's exception", exc_info=exc)
        return UpdateCheck(current, None, False, RELEASES_PAGE, error=str(exc))


def safe_release_url(url: str, *, page: str = RELEASES_PAGE) -> str:
    """`url` if it is a page of this app's own repository, else the releases page.

    `UpdateCheck.url` is `html_url` **as the feed gave it** — a string this app
    did not choose — and the dialog's action hands it to `QDesktopServices`,
    which will start whatever the scheme says: a `file:` path, or on Windows a
    `\\\\host\\share` that is an SMB connection before it is anything else. The
    feed arrives over a verified TLS connection from an API this app named, so
    this is not the first line of defence; it is the one that means a bad
    answer from that API cannot reach the desktop.

    The allowed prefix is DERIVED from `page` rather than written again, so
    that a gate build which points `api_url` at the fork points this at the
    fork too. Exact match, or the prefix followed by `/` — otherwise
    `…/dads-mmo-lab-evil/x` would pass on a plain `startswith`.

    **A prefix is not a destination**, which is the second cold review's point:
    `…/dads-mmo-lab/../../other` starts with the right string and a browser
    normalises it to `github.com/other` before it asks for anything. So the URL
    is parsed and judged, not just matched — https, host exactly the one the
    page names, no userinfo, no port — and any of `/../`, `/./`, a `%2e` in any
    case, a backslash or a whitespace/control character is refused outright
    rather than normalised by this app, because normalising is how the two
    readings of a URL come apart in the first place.
    """
    repo = page[: -len("/releases")] if page.endswith("/releases") else page
    if _can_only_go_where_it_says(url, urllib.parse.urlsplit(repo).hostname) and (
        url == repo or url.startswith(repo + "/")
    ):
        return url
    logger.info(f"update: refusing to open {url!r}, which is not under {repo!r}")
    return page


_SUSPICIOUS = ("/../", "/./", "%2e", "%2E", "\\")
"""Spellings that make a URL mean one thing here and another to a browser."""


def _can_only_go_where_it_says(url: str, host: str | None) -> bool:
    """Is `url` a plain https URL on exactly `host`, with nothing that re-points it?"""
    if not url or host is None:
        return False
    if any(mark in url for mark in _SUSPICIOUS):
        return False
    if any(character.isspace() or ord(character) < 0x20 for character in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    try:
        if parts.port is not None:
            return False
    except ValueError:  # a port that is not a number at all
        return False
    return parts.hostname == host


ASKED_GITHUB = "asked GitHub"
NOT_MODIFIED = "not modified (304)"
FROM_TODAYS_CACHE = "from today's cache"
UNREACHABLE = "from the cache, GitHub could not be reached"
NOTHING_ON_OFFER = "from the cache, GitHub offered nothing"


def _log_check(result: UpdateCheck, source: str) -> UpdateCheck:
    """One line per check, saying where the answer came from. Returns `result`.

    The source is on it because the line was otherwise IDENTICAL whether the
    feed had just been fetched or a day-old cache had answered — measured on
    all four live gates of 2026-09-21, where the only way to tell was to read
    `last_checked` out of `update.json` before and after. A gate that cannot
    see which of the two happened cannot prove the once-a-day rule at all.

    Logged HERE and not in `evaluate_feed`, which is pure and is called twice
    in one check (once for the cache, once for the fresh feed) — so that line
    appeared twice per check, with nothing to tell the two apart.
    """
    logger.info(
        f"update check: current={result.current} latest={result.latest} "
        f"newer={result.available} source={source}"
    )
    return result


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

    **A failure does not start the day again.** `last_checked` is written only
    when GitHub answered — 200 or 304 — so a launch with no network retries at
    the NEXT launch rather than tomorrow, which is what somebody who opened
    this on a train and then got home wants. The cost is stated rather than
    hidden: a run of launches while GitHub is rate-limiting asks once each.
    Both are bounded by how often the app is started, not by a timer, and the
    alternative loses a day of update news to one bad minute.
    """
    state = load_update_state(state_path)
    cached = _from_cache(state, current)
    moment = now()
    if (
        not force
        and cached is not None
        and 0 <= moment - state.last_checked < CHECK_INTERVAL_SECONDS
    ):
        return _log_check(cached, FROM_TODAYS_CACHE)
    try:
        # The ETag goes only with the body it describes. Sending one whose feed
        # was lost or unreadable buys a 304 with nothing to read it against.
        # OUTSIDE the lock: this blocks for up to `_TIMEOUT_SECONDS`, and a
        # lock held across a network read is a frozen Skip button.
        answer = fetch(api_url, state.etag if cached is not None else None)
        if answer.status == 304 and cached is not None:
            remember({"last_checked": moment}, state_path)
            return _log_check(cached, NOT_MODIFIED)
        fresh = evaluate_feed(answer.text, current)
    except Exception as exc:
        # Everything, not the four types this used to name. `HTTPError` IS a
        # `URLError` so a 403 or a 500 was always covered — but a hostile body
        # can make `json.loads` raise `RecursionError`, which is neither an
        # `OSError` nor a `ValueError`, and it would have come out of a worker
        # thread whose `done` signal then never fires: the app would sit on
        # "Checking for updates…" for the rest of the session. "Never raises"
        # is this function's whole contract with `_UpdateWorker`.
        logger.info(f"update check failed: {type(exc).__name__}: {exc}")
        logger.debug("the update check's exception", exc_info=exc)
        if cached is not None:
            # Offline with a cache still knows about the update; the error says
            # why the answer might be old, and a manual check shows it.
            return _log_check(dataclasses.replace(cached, error=str(exc)), UNREACHABLE)
        return _log_check(
            UpdateCheck(current, None, False, RELEASES_PAGE, error=str(exc)), UNREACHABLE
        )
    if fresh.error:
        # A rate-limit body parses as JSON and holds no release. Caching it over
        # a good feed would throw away the only answer this app has — but the
        # ATTEMPT still happened, and not recording it left the check asking on
        # every launch for as long as the feed stayed empty. Only the stamp
        # moves; the good feed and its ETag stay where they are.
        remember({"last_checked": moment}, state_path)
        if cached is not None:
            return _log_check(cached, NOTHING_ON_OFFER)
        return _log_check(fresh, ASKED_GITHUB)
    remember(
        {"last_checked": moment, "etag": answer.etag, "feed": answer.text},
        state_path,
    )
    return _log_check(fresh, ASKED_GITHUB)


def skip_version(tag: str, state_path: Path | None = None) -> bool:
    """Remember that the player does not want this version. False if it was not written.

    Only its own field is written, onto whatever is on disk at that moment, so
    the cached feed and ETag survive: a skip must not cost the next launch a
    request.
    """
    return remember({"skipped_version": tag}, state_path)


def should_announce(result: UpdateCheck, state_path: Path | None = None) -> bool:
    """Whether an unprompted banner may say this. A skipped version says nothing.

    A NEWER release than the skipped one un-hides it: "Skip this version" is
    about one version, not about updates.
    """
    return result.available and result.latest != load_update_state(state_path).skipped_version
