"""Download the artifact, and prove it against the release's `SHA256SUMS`.

**Not `platform.download_verified()`, and the difference is the point.** That
function is for a manifest or a client archive: it has no progress and no
cancel, it prefers the operating system's `curl` where one exists, and it
reuses an existing `dest` without looking inside it. Every one of those is
wrong for a file that is about to be UNPACKED OVER THE RUNNING APPLICATION and
then executed — a half-finished download from a previous session must not be
adopted, a 70 MB transfer must be cancellable, and the bytes must come through
a reader this app can bound.

**Not a second, weaker downloader either.** The bound is plan 2's, reused:
`_Deadline` shuts the socket down from another thread when the clock runs out,
`read1()` answers with whatever has arrived so the clock is consulted often,
and the per-socket `timeout=` covers a server that goes completely silent. The
one difference is that the clock here is per STALL and is restarted on every
chunk (`_Deadline.restart`), because a large artifact over a slow line is
legitimate and a connection that sends nothing for half a minute is not.

**The address is computed, never read.** `artifact_url()` builds
`https://github.com/<repo>/releases/download/<tag>/<name>` from the repository
constant and then judges the result with `update.safe_release_url`'s rule, so
a tag or a name carrying `..`, a `%2e`, a backslash or whitespace cannot
re-point it. Redirects are followed only to `https` (GitHub redirects release
downloads to its object store).

Nothing here is switchable at run time. There is deliberately no environment
variable and no setting that says where an update comes from: a self-updater
with a run-time switch for its own source is a way to install anything.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from yulon import __version__
from yulon.log import get_logger
from yulon.platform import verify_context
from yulon.update import RELEASES_REPO, _can_only_go_where_it_says, _Deadline

logger = get_logger(__name__)


class UpdateError(Exception):
    """A refusal, worded for the player rather than for a log.

    Everything in this package raises this and nothing else reaches the UI, so
    the dialog can show `str(exc)` and never a traceback.
    """


class Cancelled(UpdateError):
    """The player pressed Cancel. Not a failure, and shown as neither."""


Progress = Callable[[int, int], None]
"""`(bytes so far, total)`. The total is the size the RELEASE declares, not a header."""

IsCancelled = Callable[[], bool]
"""Asked between chunks. True means stop now and leave nothing behind."""


class Response(Protocol):
    """The little of an HTTP response this module uses, so a test can be one."""

    def read1(self, amount: int, /) -> bytes: ...

    def getheader(self, name: str, default: str | None = None, /) -> str | None: ...

    def close(self) -> None: ...


Opener = Callable[[str, _Deadline], Response]
"""`(url, watchdog) -> Response`. The watchdog is handed each connection as it is made.

A test's opener ignores the second argument; the real one gives it to the
handler that builds the connection, which is the earliest moment anything
exists to shut down — `urlopen` returns nothing until the headers have arrived,
and the header phase is one of the two places a trickling server can hold this
app (plan 2 measured both).
"""

CHUNK_BYTES = 256 * 1024
"""How much is asked for at a time. `read1` answers with less if less has arrived."""

STALL_SECONDS = 30.0
"""How long a connection may send NOTHING before it is shut down.

Not a bound on the download: a 70 MB AppImage on a slow line is a legitimate
several minutes, and a timer over the whole transfer would refuse exactly the
users who need the update most. Thirty seconds of complete silence is not a
slow line, it is a connection that has stopped.
"""

_SOCKET_TIMEOUT = 30.0
"""The per-operation timeout, which covers a socket that never answers at all."""

MAX_TEXT_BYTES = 1_000_000
"""How much of a `SHA256SUMS` is read before the answer is refused as not-a-checksum-file.

Four artifacts and a name each is under 400 bytes. A megabyte is three orders
of magnitude past that and still nothing to hold in memory.
"""

_CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+\*?([^\s/\\]+)[ \t]*$")
r"""`<hex>  <name>` or `<hex> *<name>`, which is what `sha256sum` writes.

`[^\s/\\]+` for the name: a `SHA256SUMS` is remote text and the names in it are
keys this app looks up. Nothing here joins one onto a path — but a line with a
separator in it is not a line `sha256sum` wrote over a flat directory, so it is
dropped rather than read.
"""


class DuplicateName(UpdateError):
    """`SHA256SUMS` lists one name twice, with two digests. Refused, never last-wins."""


def parse_checksums(text: str) -> dict[str, str]:
    """`SHA256SUMS` as `{name: lowercase hex}`. A line that is not one is ignored.

    Ignored rather than refused: the file is produced by `sha256sum` in a
    workflow this project owns, and a future line this parser has not seen must
    not cost a user their update when the line they need is right there.

    **A name listed TWICE is refused** (cold review 1). A dict silently keeps
    the last line, so a file with `Yulon-x.tar.gz` twice would have this app
    check the artifact against whichever digest came second — a choice made by
    whoever wrote the file rather than by this app. There is no reading of two
    digests for one name that is safe, so neither is taken.

    A leading byte-order mark is stripped by the caller (`fetch_text` decodes
    `utf-8-sig`), so a `SHA256SUMS` that has been through a Windows editor
    still parses. Before that it failed closed on line 1 — safe, but it refused
    a file that was perfectly good.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _CHECKSUM_LINE.match(line.strip("\r"))
        if not match:
            continue
        name, digest = match.group(2), match.group(1).lower()
        if name in found and found[name] != digest:
            raise DuplicateName(
                f"This release's SHA256SUMS lists {name} twice with different checksums, "
                "so Yu'lon cannot tell which is right. Use the release page instead."
            )
        found[name] = digest
    return found


def expected_digest(checksums_text: str, name: str) -> str:
    """What `SHA256SUMS` says `name` must hash to. Refuses if it says nothing.

    **This is the refusal that matters most in the package.** An artifact the
    release does not list is never installed — not downloaded-and-hoped, not
    installed with a warning. There is no path past this one.
    """
    digest = parse_checksums(checksums_text).get(name)
    if digest is None:
        raise UpdateError(
            f"This release does not list {name} in its SHA256SUMS, so Yu'lon cannot "
            "prove the download. Use the release page instead."
        )
    return digest


def _https_only_opener(
    watcher: _Deadline, context: ssl.SSLContext
) -> urllib.request.OpenerDirector:
    """An opener that watches its connections and follows redirects only over https.

    The watching half is `update._watching_opener`'s, for its reasons. The
    redirect half is this module's own: a release download is answered by
    GitHub with a 302 to its object store, so redirects have to be followed —
    and a redirect is a URL this app did not choose, which is exactly the thing
    `artifact_url()` refuses to let the feed do.
    """

    def capture(factory: Any) -> Any:
        def build(*args: Any, **kwargs: Any) -> Any:
            connection = factory(*args, **kwargs)
            watcher.watch(connection)
            return connection

        return build

    # `Any` at these points for `update._watching_opener`'s reason: `do_open` is
    # typed against a keyword-only protocol a wrapper cannot restate without
    # copying CPython's parameter list.
    class _Http(urllib.request.HTTPHandler):
        def http_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(capture(http.client.HTTPConnection), req)

    class _Https(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(capture(http.client.HTTPSConnection), req, context=context)

    class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: urllib.request.Request,
            fp: Any,
            code: int,
            msg: str,
            headers: Any,
            newurl: str,
        ) -> urllib.request.Request | None:
            # urllib's own handler allows http, https and ftp. An update that
            # is fetched over plain http is an update anyone on the path can
            # replace — the checksum would still catch it, and being caught is
            # not the same as not being offered.
            if urllib.parse.urlsplit(newurl).scheme != "https":
                raise UpdateError(f"The download was redirected off https, to {newurl[:200]!r}.")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_Http(), _Https(context=context), _HttpsOnlyRedirect())


def _open(url: str, watcher: _Deadline) -> Response:
    """The real opener: verified TLS, this app's User-Agent, the watchdog attached."""
    request = urllib.request.Request(url, headers={"User-Agent": f"yulon/{__version__}"})
    opener = _https_only_opener(watcher, verify_context())
    opened = opener.open(request, timeout=_SOCKET_TIMEOUT)
    return opened  # type: ignore[no-any-return]


_DOWNLOAD_PREFIX = f"https://github.com/{RELEASES_REPO}/releases/download"


def artifact_url(tag: str, name: str) -> str:
    """Where this app asks for `name` of release `tag`. **Built here, never read off a feed.**

    The feed's `browser_download_url` is remote text; this is not. The result
    is still judged before it is returned, with `update.safe_release_url`'s own
    rule — https, exactly `github.com`, no userinfo, no port, no `.` or `..`
    segment, no `%2e` in any case, no backslash, no whitespace or control
    character — because `tag` and `name` are interpolated into it and a rule
    that is only applied to strings from outside is a rule with a hole in it.
    """
    url = f"{_DOWNLOAD_PREFIX}/{tag}/{name}"
    host = urllib.parse.urlsplit(_DOWNLOAD_PREFIX).hostname
    if not _can_only_go_where_it_says(url, host) or not url.startswith(_DOWNLOAD_PREFIX + "/"):
        logger.info(f"self-update: refusing to build a download address from {tag!r}/{name!r}")
        raise UpdateError("Yu'lon could not build a safe download address for this release.")
    return url


def download(
    url: str,
    dest: Path,
    *,
    expected_size: int,
    progress: Progress,
    cancelled: IsCancelled,
    open_url: Opener = _open,
) -> Path:
    """Fetch `url` to `dest`, reporting progress and stopping when asked.

    Written to `<dest>.part` and renamed onto `dest` only after the byte count
    agrees with what the release declared, so nothing that is not a complete
    download ever wears the real name — and a `.part` left by an earlier
    attempt is overwritten rather than resumed, because this file is about to
    be executed and half of it is not a head start.

    The size is a bound on the READ and not a check afterwards: a server that
    answers with an endless body would otherwise fill the disk before anything
    looked at the total. One byte past the declared size is enough to know it
    is too long and is the least that can prove it.

    Every failure removes the `.part` and raises `UpdateError`; a `cancelled()`
    that answers True raises `Cancelled`, which the UI shows as nothing at all.
    """
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    try:
        with _Deadline(STALL_SECONDS) as watcher:
            try:
                response = open_url(url, watcher)
            except UpdateError:
                raise
            except Exception as exc:
                raise UpdateError(f"The download could not be started: {exc}") from exc
            try:
                declared = response.getheader("Content-Length")
                if declared is not None and declared.strip().isdigit():
                    if int(declared) != expected_size:
                        # Logged and not refused: the byte count below is what
                        # cannot be fooled, and the digest after it is what
                        # actually decides. A refusal here would be this app
                        # arguing with a CDN about a header it has never
                        # measured against the real object store.
                        logger.info(
                            f"self-update: the server declared {declared} bytes where the "
                            f"release says {expected_size}"
                        )
                with part.open("wb") as handle:
                    while True:
                        if cancelled():
                            raise Cancelled("The update was cancelled.")
                        try:
                            # Never more than one byte past what the release
                            # declared: the bound has to be on what is ASKED
                            # for, or an endless body fills the disk a chunk at
                            # a time while the total is checked afterwards.
                            chunk = response.read1(min(CHUNK_BYTES, expected_size - done + 1))
                        except Exception as exc:
                            _refuse_if_stalled(watcher)
                            raise UpdateError(f"The download failed: {exc}") from exc
                        _refuse_if_stalled(watcher)
                        if not chunk:
                            break
                        watcher.restart(STALL_SECONDS)
                        handle.write(chunk)
                        done += len(chunk)
                        progress(done, expected_size)
                        if done > expected_size:
                            raise UpdateError(
                                f"The download is longer than this release says it is "
                                f"({expected_size} bytes); Yu'lon stopped it."
                            )
            finally:
                try:
                    response.close()
                except Exception as exc:  # noqa: BLE001 - closing is best effort
                    logger.debug(f"self-update: closing the download connection raised {exc}")
        if done != expected_size:
            raise UpdateError(
                f"The download stopped after {done} bytes; this release says it is "
                f"{expected_size} bytes."
            )
        os.replace(part, dest)
    except BaseException:
        # Every path, `Cancelled` and a KeyboardInterrupt included: a `.part`
        # left beside the install is rubbish nobody comes back for, and the
        # next attempt overwrites it anyway.
        part.unlink(missing_ok=True)
        raise
    return dest


def _refuse_if_stalled(watcher: _Deadline) -> None:
    """A shut-down socket reads as a clean end of stream. It is not one.

    Plan 2's lesson, and the reason the watchdog reports rather than just acts:
    without this, a connection cut off at the deadline hands back an empty,
    apparently complete body — and here that would be a short file the size
    check refuses with the wrong reason.
    """
    if watcher.fired:
        raise UpdateError(f"The download sent nothing for {STALL_SECONDS:.0f}s and was stopped.")


def sha256_of(path: Path) -> str:
    """The file's SHA-256, read in chunks so a 70 MB artifact is not held twice."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, digest: str) -> None:
    """Refuse and DELETE `path` unless it hashes to `digest`.

    Deleted rather than left: what is on disk at that moment is a file that
    claims to be a build of Yu'lon and is not, sitting beside the install under
    a name the next attempt would otherwise find.
    """
    actual = sha256_of(path)
    if actual != digest.strip().lower():
        path.unlink(missing_ok=True)
        raise UpdateError(
            f"The download's checksum does not match what the release published "
            f"({actual[:16]}… instead of {digest.strip().lower()[:16]}…). "
            "Nothing was installed."
        )


def fetch_text(
    url: str,
    *,
    open_url: Opener = _open,
    limit: int = MAX_TEXT_BYTES,
) -> str:
    """Read a small document (the `SHA256SUMS`) under the same bounds as a download."""
    chunks: list[bytes] = []
    size = 0
    with _Deadline(STALL_SECONDS) as watcher:
        try:
            response = open_url(url, watcher)
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(f"The checksums could not be downloaded: {exc}") from exc
        try:
            while True:
                try:
                    chunk = response.read1(CHUNK_BYTES)
                except Exception as exc:
                    _refuse_if_stalled(watcher)
                    raise UpdateError(f"The checksums could not be downloaded: {exc}") from exc
                _refuse_if_stalled(watcher)
                if not chunk:
                    break
                watcher.restart(STALL_SECONDS)
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise UpdateError(
                        f"This release's checksum file is too large ({limit} bytes is the most "
                        "Yu'lon will read)."
                    )
        finally:
            try:
                response.close()
            except Exception as exc:  # noqa: BLE001 - closing is best effort
                logger.debug(f"self-update: closing the checksums connection raised {exc}")
    # `utf-8-sig`: a byte-order mark is legal in a UTF-8 file and every Windows
    # editor writes one. Read as plain UTF-8 the BOM becomes U+FEFF at the
    # start of line 1, which no checksum line can match — so a hand-edited
    # `SHA256SUMS` refused the update on its first line (cold review 1). A file
    # without a BOM decodes identically.
    return b"".join(chunks).decode("utf-8-sig", errors="replace")
