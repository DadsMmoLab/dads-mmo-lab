"""Application self-update check (README §10, roadmap 5.4): check + notify only.

Asks the GitHub Releases API for the latest release tag, compares it with the
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

logger = get_logger(__name__)

RELEASES_API = "https://api.github.com/repos/DadsMmoLab/dads-mmo-lab/releases/latest"
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
    request = urllib.request.Request(
        url, headers={"User-Agent": f"yulon/{__version__}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


def check_for_update(
    current: str = __version__,
    *,
    http_get: HttpGetText = _urllib_get_text,
    api_url: str = RELEASES_API,
) -> UpdateCheck:
    """Compare the running version with the latest GitHub release. Never raises."""
    try:
        payload = json.loads(http_get(api_url))
        tag = str(payload.get("tag_name") or "")
        url = str(payload.get("html_url") or RELEASES_PAGE)
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as exc:
        logger.info(f"update check skipped: {exc}")
        return UpdateCheck(current, None, False, RELEASES_PAGE, error=str(exc))
    if parse_version(tag) is None:
        logger.info(f"update check: unrecognized tag {tag!r}")
        return UpdateCheck(current, tag or None, False, url, error=f"unrecognized tag {tag!r}")
    newer = is_newer(tag, current)
    logger.info(f"update check: current={current} latest={tag} newer={newer}")
    return UpdateCheck(current, tag, newer, url)
