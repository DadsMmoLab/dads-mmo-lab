"""What upstream has that this server was not built from (T124).

The Modules tab has said "N commits behind" per module since 8.7a; a server's
own sources said nothing, and a player learned there was newer server code only
by pressing "Update the server to latest…". This module is the reading behind
the Server tab's line that says so, and the small file that keeps it to one
reading a day.

**Why the GitHub compare API and not the Modules tab's `git fetch`.** The count
is the same figure -- commits upstream's branch carries that this checkout's
HEAD does not, `None` for "could not ask" and never a zero for it -- but the
Modules count runs behind a button the user pressed, and `git.commits_behind()`
says in its own docstring that a fetch "belongs behind a control the user
pressed and not on a timer". This one is asked when a tab opens. A fetch there
would write objects into the install's `.git` every day, through a WRITER
container that on an enforcing SELinux box relabels the checkout (for
AzerothCore, the whole server directory), to draw one line of text. The compare
API writes nothing into the install and costs one small HTTPS GET per source;
the checkout is only READ, for its HEAD, through the read-only container the
update route already uses.

`?per_page=1&page=2` is not decoration. GitHub's documented pagination rule for
this endpoint is that the changed-file list comes only on the first page, and
the first page of the WotLK core's comparison was 1.2 MB of diffs (measured
2026-09-25) for the one number this reads; page two is ~15 kB and carries the
same `ahead_by`.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from yulon import __version__, platform
from yulon.log import get_logger

logger = get_logger(__name__)

UPSTREAM_FILE = ".yulon-upstream.json"
"""The cached reading, beside `.yulon-install.json` in the server directory."""

MAX_AGE_SECONDS = 24 * 60 * 60
"""An answer is asked for at most once a day per install -- the ticket's rule."""

RETRY_SECONDS = 60 * 60
"""How long a reading in which NO source answered is kept before asking again.

Shorter than a day because "no network" is usually a laptop that was offline
when the tab opened, and a whole day of silence after it reconnects would hide
the line for no reason. Still kept at all, because a Refresh pressed over and
over on a machine GitHub is refusing (a rate limit is a 403) must not become a
request per press.
"""

CACHE_VERSION = 1

HttpGet = Callable[[str, str], bytes]
"""`(url, accept)` in, the response body out. Raises `OSError` on any failure."""

_TIMEOUT_SECONDS = 10.0
_MAX_BYTES = 1024 * 1024
"""A compare page two is ~15 kB; anything past a megabyte is not an answer."""

_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def https_get(url: str, accept: str) -> bytes:
    """One GET over `platform.verify_context()`'s root set, capped in time and size.

    Verified for the reason every GET in this package is: an unverified answer
    would decide what a user is told about their server's code.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": f"yulon/{__version__}", "Accept": accept}
    )
    with urllib.request.urlopen(
        request, timeout=_TIMEOUT_SECONDS, context=platform.verify_context()
    ) as resp:
        body = resp.read(_MAX_BYTES + 1)
    if len(body) > _MAX_BYTES:
        raise OSError(f"{url} answered with more than {_MAX_BYTES} bytes")
    return bytes(body)


def github_slug(repo: str) -> str | None:
    """`owner/name` for a GitHub-hosted `Source.repo`, or None for any other host.

    `Source.repo` is a slug (GitHub) or an https URL on an allowed host. Only
    GitHub has the compare API this module asks, so a GitLab or Codeberg source
    answers None here and its count is "could not ask" -- never zero.
    """
    if "://" not in repo:
        return repo if _SLUG.match(repo) else None
    parts = urlsplit(repo)
    if (parts.hostname or "").lower() != "github.com":
        return None
    path = parts.path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path if _SLUG.match(path) else None


def commits_ahead(slug: str, base: str, ref: str, *, get: HttpGet) -> int | None:
    """How many commits `ref` on GitHub carries that `base` does not. None = could not ask.

    `ahead_by` of `compare/{base}...{ref}`, which is `git rev-list --count
    base..ref` -- the Modules tab's `HEAD..FETCH_HEAD`, asked of GitHub instead
    of a fetched clone. A diverged pair (a pin that upstream rewrote away)
    still answers `ahead_by`: those are commits upstream has and the checkout
    does not, which is what the line reports.
    """
    url = (
        f"https://api.github.com/repos/{slug}/compare/"
        f"{quote(base, safe='')}...{quote(ref, safe='')}?per_page=1&page=2"
    )
    try:
        payload = json.loads(get(url, "application/vnd.github+json").decode("utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug(f"could not ask GitHub how far {slug} {ref} is past {base}: {exc}")
        return None
    ahead = payload.get("ahead_by") if isinstance(payload, dict) else None
    if isinstance(ahead, bool) or not isinstance(ahead, int) or ahead < 0:
        logger.debug(f"GitHub's compare of {slug} did not carry a count: {str(payload)[:200]}")
        return None
    return ahead


@dataclass(frozen=True)
class SourceNews:
    """One moving source's reading: how far upstream is past what was built."""

    repo: str
    label: str
    """What the line calls it: "server" for the core, the repository's name otherwise."""
    behind: int | None
    """Commits upstream has past this checkout's HEAD. `None` = could not ask."""


@dataclass(frozen=True)
class UpstreamNews:
    """Every moving source's reading, and when it was taken."""

    checked_unix: int
    sources: tuple[SourceNews, ...]

    def answered(self) -> bool:
        """Did any source answer at all? False is "no network", which says nothing."""
        return any(source.behind is not None for source in self.sources)


def line(news: UpstreamNews | None) -> str:
    """The Server tab's sentence, or `""` when there is nothing new or nothing known.

    Only sources with something new are named. A source that could not be
    asked is left out rather than reported as zero or as a failure: the line is
    news, and "could not ask" is not news a player can act on.
    """
    if news is None:
        return ""
    said = [
        f"{source.label} {source.behind} {'commit' if source.behind == 1 else 'commits'}"
        for source in news.sources
        if source.behind
    ]
    if not said:
        return ""
    return (
        f"Upstream has new code since this server was built: {', '.join(said)}. "
        "“Update the server to latest…” on the Modules tab brings it in."
    )


def read_cached(server_dir: Path, repos: Sequence[str], now: float) -> UpstreamNews | None:
    """The cached reading if it is still fresh for these sources, else None.

    Stale when older than `MAX_AGE_SECONDS` (or `RETRY_SECONDS` if nothing
    answered), when it is from the future (a clock set back), or when it is
    about a different set of sources -- a newer app that moves a different
    source must not be told a count about the old one.
    """
    path = server_dir / UPSTREAM_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        if payload["version"] != CACHE_VERSION:
            return None
        checked = int(payload["checked_unix"])
        rows = payload["sources"]
        news = UpstreamNews(
            checked_unix=checked,
            sources=tuple(
                SourceNews(
                    repo=str(row["repo"]),
                    label=str(row["label"]),
                    behind=None if row["behind"] is None else int(row["behind"]),
                )
                for row in rows
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug(f"{path} is not a reading this build can use: {exc}")
        return None
    if [source.repo for source in news.sources] != list(repos):
        return None
    age = now - checked
    limit = MAX_AGE_SECONDS if news.answered() else RETRY_SECONDS
    if age < 0 or age >= limit:
        return None
    return news


def write_cached(server_dir: Path, news: UpstreamNews) -> None:
    """Write the reading atomically. Best-effort: a failure costs one more ask, logged."""
    path = server_dir / UPSTREAM_FILE
    payload = {
        "version": CACHE_VERSION,
        "checked_unix": news.checked_unix,
        "sources": [
            {"repo": source.repo, "label": source.label, "behind": source.behind}
            for source in news.sources
        ],
    }
    tmp = path.with_name(path.name + ".new")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not keep the upstream reading in {path}: {exc}")


def forget(server_dir: Path) -> None:
    """Drop the cached reading. Called when a press moves this install's sources.

    The Modules tab's `_behind.pop()` after a module update, for the server: a
    count taken before an update is a count against a HEAD that is gone, and
    serving it for the rest of the day would tell a player who just updated
    that there is new code.
    """
    try:
        (server_dir / UPSTREAM_FILE).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not drop the upstream reading in {server_dir}: {exc}")


def now_unix() -> int:
    """The wall clock, as the cache records it."""
    return int(time.time())
