"""Application self-update check (README §10, roadmap 5.4): check + notify only.

Asks the GitHub Releases API for the newest published release, compares it with the
running `yulon.__version__`, and returns a typed `UpdateCheck` the UI can turn
into a non-blocking banner with a download link. Nothing is downloaded or
replaced — v1 scope is check + notify. The HTTP call is a seam, so the check is
unit-testable offline, and every failure (offline, rate-limited, odd tag)
degrades to "no update known", never to a crash at launch.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from yulon import __version__
from yulon.log import get_logger
from yulon.platform import verify_context

logger = get_logger(__name__)

RELEASES_API = "https://api.github.com/repos/DadsMmoLab/dads-mmo-lab/releases?per_page=5"
RELEASES_PAGE = "https://github.com/DadsMmoLab/dads-mmo-lab/releases/latest"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_TIMEOUT_SECONDS = 5.0

HttpGetText = Callable[[str], str]


@dataclass(frozen=True)
class UpdateCheck:
    """Outcome of one check. `available` is True only when a newer release exists."""

    current: str
    latest: str | None
    available: bool
    url: str
    error: str | None = None


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


def _newest_published(feed: object) -> dict[str, object] | None:
    """The first release in the feed that a user could actually download, or None.

    Not `/releases/latest`: that endpoint means "latest non-prerelease", and every
    release this project has ever cut is flagged `Pre-release`, so it answered 404
    for every user the check ever ran for. `/releases` lists them all, and a
    prerelease here is a normal download — only a draft is invisible, so a draft is
    all we skip.

    What this relies on: `/releases` is ordered newest-first *by creation date*,
    which is not the same as by version, so a re-cut of an old tag can arrive at the
    top. That is why the caller still asks `is_newer` — the feed says what is newest,
    the version comparison decides whether it is an upgrade.
    """
    if not isinstance(feed, list):
        return None
    for entry in feed:
        if isinstance(entry, dict) and not entry.get("draft"):
            return entry
    return None


def check_for_update(
    current: str = __version__,
    *,
    http_get: HttpGetText = _urllib_get_text,
    api_url: str = RELEASES_API,
) -> UpdateCheck:
    """Compare the running version with the latest GitHub release. Never raises."""
    try:
        release = _newest_published(json.loads(http_get(api_url)))
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
        logger.info(f"update check skipped: {exc}")
        return UpdateCheck(current, None, False, RELEASES_PAGE, error=str(exc))
    if release is None:
        # An empty repo, or an answer that is not a feed at all — a rate-limit
        # body is valid JSON too.
        logger.info("update check: no published release in the feed")
        return UpdateCheck(current, None, False, RELEASES_PAGE, error="no published release")
    tag = str(release.get("tag_name") or "")
    url = str(release.get("html_url") or RELEASES_PAGE)
    if parse_version(tag) is None:
        logger.info(f"update check: unrecognized tag {tag!r}")
        return UpdateCheck(current, tag or None, False, url, error=f"unrecognized tag {tag!r}")
    newer = is_newer(tag, current)
    logger.info(f"update check: current={current} latest={tag} newer={newer}")
    return UpdateCheck(current, tag, newer, url)
