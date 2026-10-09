"""The bot dashboard from TortoiseBots' prebuilt binary instead of a Go build (T542).

TortoiseBots attaches `tortoise-observability-linux-amd64` and a
`tortoise-observability-sha256.txt` to its daily release (v2026-10-09 was the
first). `stage()` finds the right one for the bots module a server has, checks
the download against that release's checksum, and lays out a tiny Docker build
context around it; the Dashboard builds that instead of the module's
`tools/observability` Go source, which takes a few minutes and downloads a Go
toolchain.

**What the checksum proves, and what it does not.** The sha256 file is published
by the same release as the binary, so a match proves an intact download of what
that release holds -- not that the binary is a pinned, reproducible build of the
module's source. Using it is a decision to trust the releases of the repository
the catalog clones the module from (Sagiroth/TortoiseBots; owner, 2026-10-09).
The player's text says "checked against the checksum its release published" and
claims nothing more.

**Which release.** The newest release that carries the binary, whose tag is at
or before the module's own commit (GitHub's compare of `tag...commit` says the
module is not behind it), and whose binary was not re-uploaded long after the tag
was made. A daemon newer than the module may speak a datagram protocol the module
does not (T162). Upstream's workflow re-uploads the day's assets on every push to
main (`--clobber`) while the tag moves only when the changelog job succeeds, so a
binary uploaded more than `SKEW` after its tag's commit may be from a later build
than the tag: that release is skipped. A module older than the first release with
a binary has none, and builds from source -- for the installs that sit on an older
TortoiseBots (T597) that is the normal path.

**Fail closed.** The binary is downloaded only after the release's sha256 file
has been read and names it; the download is held to the size the release
declares; and the finished file is hashed against that digest. Any miss -- no
sha file, no line for the binary, a different hash, a download that stops, a disk
that refuses the staging folder -- deletes what was fetched and raises
`Unavailable`, whose text is the one clause the player is told before the Go
build runs. A binary whose checksum did not match is never copied into an image.

**Where from.** Addresses are built here from the repository slug and the tag,
judged with the self-updater's own rule (https, exactly github.com, no `..`),
and fetched through `selfupdate.fetch`'s hardened reader: a stall watchdog, a
byte bound, redirects followed only over https.

**The image** is `alpine` plus `ca-certificates` and `tzdata` with the binary at
`/app/tortoise-observability` -- the same runtime stage as upstream's own
Dockerfile, minus the Go builder. The compose service around it is unchanged.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from yulon.catalog import upstream
from yulon.log import get_logger
from yulon.selfupdate import fetch
from yulon.update import _can_only_go_where_it_says

logger = get_logger(__name__)

BINARY_NAME = "tortoise-observability-linux-amd64"
SUMS_NAME = "tortoise-observability-sha256.txt"
INSTALLED_NAME = "tortoise-observability"
"""The file's name inside the image, upstream's own Dockerfile's."""

STAGING_DIR = ".yulon-dashboard-binary"
"""Under the server folder: the build context while it exists. Removed after every build."""

RELEASES_PER_PAGE = 10
"""The newest releases asked for. A binary is on a few of the daily releases at most."""

MAX_BINARY_BYTES = 200 * 1024 * 1024
"""The daemon is ~29 MB. A release declaring more than this is not asked for."""

MAX_COMPARES = 5
"""How many releases are compared with the module's commit before giving up (one request each)."""

SKEW = timedelta(hours=2)
"""How long after its tag's commit a release's binary may have been uploaded."""

_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

DOCKERFILE = f"""\
# Written by Yu'lon (T542): TortoiseBots' prebuilt dashboard, checked against the
# checksum its release published before it got here. Same runtime as upstream's image.
FROM alpine:3.20
RUN apk --no-cache add ca-certificates tzdata
WORKDIR /app
COPY {BINARY_NAME} /app/{INSTALLED_NAME}
RUN chmod 0755 /app/{INSTALLED_NAME}
EXPOSE 8095
EXPOSE 9195/udp
ENTRYPOINT ["/app/{INSTALLED_NAME}"]
"""


class Unavailable(Exception):
    """No usable prebuilt binary; the text is one clause for the player. Nothing is left behind."""


class Stopped(Unavailable):
    """The player's Stop ended the download."""


@dataclass(frozen=True)
class Pick:
    tag: str
    size: int


@dataclass(frozen=True)
class Staged:
    """A build context holding the checked binary and the Dockerfile that wraps it."""

    context: Path
    tag: str


@dataclass(frozen=True)
class _Candidate:
    created: str
    tag: str
    size: int
    uploaded: str
    """The binary asset's `updated_at`."""


def _oneline(text: object) -> str:
    return " ".join(str(text).split())


def _moment(text: object) -> datetime | None:
    """An ISO-8601 time with a zone, or None. A time without a zone is not trusted."""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _candidates(releases: object) -> list[_Candidate]:
    """Each published release that carries both files, newest first."""
    found: list[_Candidate] = []
    if not isinstance(releases, list):
        return found
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not _TAG.match(tag) or tag.strip(".") == "":
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        by_name = {
            a.get("name"): a for a in assets if isinstance(a, dict) and a.get("state") == "uploaded"
        }
        binary = by_name.get(BINARY_NAME)
        if SUMS_NAME not in by_name or binary is None:
            continue
        size = binary.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_BINARY_BYTES:
            continue
        created, uploaded = release.get("created_at"), binary.get("updated_at")
        found.append(
            _Candidate(
                created if isinstance(created, str) else "",
                tag,
                size,
                uploaded if isinstance(uploaded, str) else "",
            )
        )
    found.sort(key=lambda c: c.created, reverse=True)
    return found


def _tag_moment(repo: str, tag: str, get: upstream.HttpGet) -> datetime | None:
    """When the commit `tag` names was made, or None if GitHub would not say."""
    url = f"https://api.github.com/repos/{repo}/commits/{tag}"
    try:
        payload = json.loads(get(url, "application/vnd.github+json"))
        return _moment(payload["commit"]["committer"]["date"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pick(rev: str | None, repo: str, *, get: upstream.HttpGet = upstream.https_get) -> Pick:
    """The newest release with the binary at or before `rev`, or `Unavailable` saying why."""
    if rev is None or not re.fullmatch(r"[0-9a-f]{7,40}", rev):
        raise Unavailable("Yu'lon could not read which bots version this server has")
    url = f"https://api.github.com/repos/{repo}/releases?per_page={RELEASES_PER_PAGE}"
    try:
        releases = json.loads(get(url, "application/vnd.github+json"))
    except (OSError, ValueError) as exc:
        logger.info(f"could not list the {repo} releases: {exc}")
        raise Unavailable("GitHub did not answer, so Yu'lon could not look for one") from None
    candidates = _candidates(releases)
    if not candidates:
        raise Unavailable("no release of the bots module carries one yet")
    outran: str | None = None
    unknown: str | None = None
    for candidate in candidates[:MAX_COMPARES]:
        said = upstream.compare_or_refused(repo, candidate.tag, rev, get=get)
        if isinstance(said, upstream.Refused):
            raise Unavailable("GitHub is limiting requests from this PC just now")
        if said is None:
            raise Unavailable("GitHub did not answer, so Yu'lon could not look for one")
        if said.behind != 0:
            continue
        made, uploaded = _tag_moment(repo, candidate.tag, get), _moment(candidate.uploaded)
        if made is None or uploaded is None:
            unknown = unknown or (
                f"Yu'lon could not tell whether release {candidate.tag}'s dashboard is from "
                "the same build as its tag"
            )
            continue
        if uploaded > made + SKEW:
            outran = outran or (
                f"release {candidate.tag}'s dashboard was uploaded after that release's tag "
                "was made, so it may be from a later build than this server's bots module"
            )
            continue
        return Pick(candidate.tag, candidate.size)
    raise Unavailable(
        outran or unknown or "this server's bots module is older than the releases that carry one"
    )


def asset_url(repo: str, tag: str, name: str) -> str:
    """Where `name` of release `tag` is fetched. **Built here, never read off the API.**"""
    prefix = f"https://github.com/{repo}/releases/download"
    url = f"{prefix}/{tag}/{name}"
    if (
        not _TAG.match(tag)
        or not _can_only_go_where_it_says(url, "github.com")
        or not url.startswith(prefix + "/")
    ):
        raise Unavailable("Yu'lon could not build a safe download address for it")
    return url


def clear(server_dir: Path) -> None:
    shutil.rmtree(server_dir / STAGING_DIR, ignore_errors=True)


def stage(
    server_dir: Path,
    rev: str | None,
    repo: str | None,
    *,
    get: upstream.HttpGet = upstream.https_get,
    open_url: fetch.Opener = fetch._open,
    cancelled: Callable[[], bool] = lambda: False,
) -> Staged:
    """Pick, check and lay out the build context under `server_dir`. Raises `Unavailable`.

    `repo` is the GitHub slug the catalog clones the bots module from, or None
    when that is not GitHub. On any failure the staging folder is gone again:
    the caller only has to `clear()` after the build it did with a success.
    """
    if repo is None:
        raise Unavailable("this server's bots module does not come from GitHub")
    chosen = pick(rev, repo, get=get)
    context = server_dir / STAGING_DIR
    try:
        shutil.rmtree(context, ignore_errors=True)
        context.mkdir(parents=True)
        sums_url = asset_url(repo, chosen.tag, SUMS_NAME)
        binary_url = asset_url(repo, chosen.tag, BINARY_NAME)
        try:
            sums = fetch.parse_checksums(fetch.fetch_text(sums_url, open_url=open_url))
            digest = sums.get(BINARY_NAME)
            if digest is None:
                raise Unavailable(
                    f"the release's {SUMS_NAME} does not list the dashboard, so Yu'lon "
                    "could not check it against its checksum"
                )
            target = context / BINARY_NAME
            fetch.download(
                binary_url,
                target,
                expected_size=chosen.size,
                progress=lambda _done, _total: None,
                cancelled=cancelled,
                open_url=open_url,
            )
            try:
                fetch.verify(target, digest)
            except fetch.UpdateError:
                raise Unavailable(
                    "the download did not match the checksum its release published"
                ) from None
        except fetch.Cancelled as exc:
            raise Stopped(_oneline(exc)) from exc
        except fetch.UpdateError as exc:
            raise Unavailable(_oneline(exc).removesuffix(".")) from exc
        (context / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8", newline="\n")
    except OSError as exc:
        shutil.rmtree(context, ignore_errors=True)
        raise Unavailable(
            f"the dashboard could not be staged on this PC ({_oneline(exc)})"
        ) from exc
    except BaseException:
        shutil.rmtree(context, ignore_errors=True)
        raise
    return Staged(context, chosen.tag)
