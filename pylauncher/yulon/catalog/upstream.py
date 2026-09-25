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
_SHA = re.compile(r"^[0-9a-f]{40}$")


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
class Release:
    """A published release: its tag, and the commit that tag names (T126)."""

    tag: str
    sha: str


def newest_release(slug: str, *, get: HttpGet) -> Release | None:
    """The newest published release of `slug` and its commit, or None if GitHub would not say.

    `releases/latest` is GitHub's own answer to "newest": the most recent release
    that is neither a draft nor a pre-release. It never returns a bare tag with
    no release behind it, which is what keeps a rolling tag out -- and a source
    only asks this at all if its catalog entry says `follow: releases`, so
    cmangos' `latest` (a release, but a rolling one) is never asked about.

    The tag is then resolved to its commit through `commits/{tag}` in the
    `vnd.github.sha` form, which peels an annotated tag and answers forty hex
    characters and nothing else. Resolved at the moment of asking, not cached
    by name: a dated tag here has been moved during its own day (the
    2026-09-25 release is titled "builds v1-v31"), and what is built must be
    the commit the tag names NOW.
    """
    base = f"https://api.github.com/repos/{slug}"
    try:
        payload = json.loads(get(f"{base}/releases/latest", "application/vnd.github+json"))
    except (OSError, ValueError) as exc:
        logger.debug(f"could not ask GitHub for the newest release of {slug}: {exc}")
        return None
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag or payload.get("draft") or payload.get("prerelease"):
        logger.debug(
            f"GitHub's newest release of {slug} is not one to follow: {str(payload)[:200]}"
        )
        return None
    try:
        sha = get(f"{base}/commits/{quote(tag, safe='')}", "application/vnd.github.sha")
    except OSError as exc:
        logger.debug(f"could not ask GitHub which commit {slug} {tag} is: {exc}")
        return None
    said = sha.decode("utf-8", errors="replace").strip()
    if not _SHA.match(said):
        logger.debug(f"GitHub did not answer {slug} {tag} with a commit: {said[:80]!r}")
        return None
    return Release(tag=tag, sha=said)


@dataclass(frozen=True)
class SourceNews:
    """One moving source's reading: how far upstream is past what was built."""

    repo: str
    label: str
    """What the line calls it: "server" for the core, the repository's name otherwise."""
    behind: int | None
    """Commits upstream has past this checkout's HEAD. `None` = could not ask.

    For a source that follows releases (T126) the "upstream" is the newest
    release's commit, and any positive count is reported as that release.
    """
    checked_unix: int = 0
    """When THIS source was asked. Per source, so one that failed (a 403, a timeout)
    is asked again after `RETRY_SECONDS` while the ones that answered keep their day."""
    follow: str = "branch"
    """The catalog's `Source.follow`, kept so a cache about the other mode is not served."""
    release: str = ""
    """The newest release's tag, for a source that follows releases and reached one."""
    installed: str = ""
    """The release tag the install record says this source was moved to, or `""`.

    Read so that a tag moved within its own day -- v2026-09-25 was re-published
    as "builds v1-v31" -- is said as an UPDATED release rather than as being in
    step: `behind` compares commits, and a positive count against a release of
    the same name is a newer build of it.
    """


@dataclass(frozen=True)
class UpstreamNews:
    """Every moving source's reading, and when it was taken."""

    checked_unix: int
    sources: tuple[SourceNews, ...]

    def answered(self) -> bool:
        """Did any source answer at all? False is "no network", which says nothing."""
        return any(source.behind is not None for source in self.sources)


def release_word(newest: str, installed: str) -> str:
    """ "new release" or "updated release": the second when the NAME is the one installed.

    Only ever asked about a release whose commit is ahead of what is installed,
    so a matching name means the tag was moved to newer commits since.
    """
    return "updated release" if installed and installed == newest else "new release"


def line(news: UpstreamNews | None) -> str:
    """The Server tab's sentence, or `""` when there is nothing new or nothing known.

    Only sources with something new are named. A source that could not be
    asked is left out rather than reported as zero or as a failure: the line is
    news, and "could not ask" is not news a player can act on.
    """
    if news is None:
        return ""
    said = [
        (
            f"{source.label} {release_word(source.release, source.installed)} {source.release}"
            if source.release
            else f"{source.label} {source.behind} {'commit' if source.behind == 1 else 'commits'}"
        )
        for source in news.sources
        if source.behind
    ]
    if not said:
        return ""
    return (
        f"Upstream has new code since this server was built: {', '.join(said)}. "
        "“Update the server to latest…” on the Modules tab brings it in."
    )


def read_cached(
    server_dir: Path, sources: Sequence[tuple[str, str]], now: float
) -> dict[str, SourceNews]:
    """The cached rows that are still fresh, by repository. Empty when there are none.

    Freshness is PER SOURCE: a row that answered is kept `MAX_AGE_SECONDS`, a
    row that could not be asked `RETRY_SECONDS`, each from its own
    `checked_unix`. So a 403 on one source is asked about again within the
    hour without re-asking the sources that answered.

    Nothing is served from a file that is damaged, from the future (a clock set
    back), or about a different set of sources -- a newer app that moves a
    different source, or follows one differently (`(repo, follow)` pairs), must
    not be told a count about the old one.
    """
    path = server_dir / UPSTREAM_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        if payload["version"] != CACHE_VERSION:
            return {}
        checked = int(payload["checked_unix"])
        rows = [
            SourceNews(
                repo=str(row["repo"]),
                label=str(row["label"]),
                behind=None if row["behind"] is None else int(row["behind"]),
                checked_unix=int(row.get("checked_unix", checked)),
                follow=str(row.get("follow", "branch")),
                release=str(row.get("release", "")),
                installed=str(row.get("installed", "")),
            )
            for row in payload["sources"]
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.debug(f"{path} is not a reading this build can use: {exc}")
        return {}
    if [(row.repo, row.follow) for row in rows] != list(sources):
        return {}
    fresh: dict[str, SourceNews] = {}
    for row in rows:
        age = now - row.checked_unix
        limit = MAX_AGE_SECONDS if row.behind is not None else RETRY_SECONDS
        if 0 <= age < limit:
            fresh[row.repo] = row
    return fresh


def cached_row(server_dir: Path, repo: str) -> SourceNews | None:
    """The last cached reading of one source, however old, or None (T126).

    What the Tortoise addon's note reads to learn whether the server's bot
    module is behind a newer build of the release it is named after.
    """
    try:
        payload = json.loads((server_dir / UPSTREAM_FILE).read_text(encoding="utf-8"))
        row = next(r for r in payload["sources"] if r["repo"] == repo)
        return SourceNews(
            repo=repo,
            label=str(row["label"]),
            behind=None if row["behind"] is None else int(row["behind"]),
            release=str(row.get("release", "")),
            installed=str(row.get("installed", "")),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
        return None


def write_cached(server_dir: Path, news: UpstreamNews) -> None:
    """Write the reading atomically. Best-effort: a failure costs one more ask, logged."""
    path = server_dir / UPSTREAM_FILE
    payload = {
        "version": CACHE_VERSION,
        "checked_unix": news.checked_unix,
        "sources": [
            {
                "repo": source.repo,
                "label": source.label,
                "behind": source.behind,
                "checked_unix": source.checked_unix or news.checked_unix,
                "follow": source.follow,
                "release": source.release,
                "installed": source.installed,
            }
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
