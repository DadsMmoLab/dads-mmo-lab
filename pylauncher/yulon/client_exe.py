"""Byte patches to a server's ready-to-play `Wow.exe` (T181 c).

A server's catalog entry may describe patches to the game's executable
(Centurion: a build number, a borderless window, no signature checks). They
are applied to the ready-to-play client's copy only, and only ever to
**stock bytes**: the same offset in another build of the exe is another
instruction, so an exe whose SHA-256 is not the one the offsets were measured
on is never patched, however close it looks. Where the stock exe comes from,
in order: the ready-to-play copy itself, the player's original, Yu'lon's cache
of it, and last a clean zip on the server's site, of which only the one
member is fetched (HTTP Range) and then proved by size, CRC and SHA-256.

**Never write through a link.** The ready-to-play `Wow.exe` may be a hard link
to the player's own. Every write here goes to a new temporary file that is
renamed into place, so the player's exe keeps its bytes whatever happens;
nothing is chmodded while another name shares the file.

Only `ExeError` leaves this module, worded for the player.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import struct
import time
import urllib.error
import urllib.parse
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from yulon import play_client
from yulon.catalog.catalog import CleanSource, ExePatch
from yulon.client_packs import STALL_SECONDS, Opener, _open, cache_dir
from yulon.log import get_logger
from yulon.steam import client_executable
from yulon.update import _can_only_go_where_it_says, _Deadline

logger = get_logger(__name__)

TEMP_SUFFIX = ".yulon-exe-tmp"
MAX_MEMBER_BYTES = 64 * 1024 * 1024
"""The most a clean-source member may be: a Wow.exe is under 10 MB, and what is
read is held in memory, so a source naming a gigabyte is refused, not fetched."""
MAX_DIRECTORY_BYTES = 256 * 1024 * 1024
_TAIL_BYTES = 65_557 + 22 + 20 + 56
"""Where the end-of-central-directory record can be: its fixed 22 bytes, a comment
of up to 65535, and the ZIP64 locator and record that sit before it."""
_LAA = 0x0020
_PE_POINTER = 0x3C


class ExeError(RuntimeError):
    """A refusal worded for the player, naming what to do next. Nothing else leaves this module."""


# --- A zip member by range requests --------------------------------------------------------


def _tell(exc: BaseException) -> str:
    return exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)


def _range(
    opener: Opener,
    url: str,
    allowed_hosts: frozenset[str],
    spec: str,
    *,
    expect: int | None,
) -> tuple[bytes, int | None]:
    """The bytes of one Range request (`spec` after `bytes=`) and the total the server declared.

    Only a 206 answer is accepted: a 200 would be the whole archive (17 GB for
    the WotLK client), which this never reads. `expect` is the length the
    request must come back with; more or fewer is refused.
    """
    try:
        with _Deadline(STALL_SECONDS) as watcher:
            response = opener(
                url, watcher, method="GET", headers={"Range": f"bytes={spec}"}, hosts=allowed_hosts
            )
            try:
                if response.status != 206:
                    raise ExeError(
                        f"{url} did not answer a byte-range request (HTTP {response.status}), "
                        "so Yu'lon cannot take just the exe out of that zip."
                    )
                header = response.getheader("Content-Range") or ""
                total: int | None = None
                if "/" in header:
                    tail = header.rsplit("/", 1)[1].strip()
                    total = int(tail) if tail.isdigit() else None
                limit = MAX_DIRECTORY_BYTES if expect is None else expect
                data = bytearray()
                while len(data) <= limit:
                    chunk = response.read1(1 << 20)
                    if not chunk:
                        break
                    data += chunk
                if watcher.fired:
                    raise ExeError(f"{url} stopped answering. Try again later.")
            finally:
                with contextlib.suppress(Exception):
                    response.close()
    except ExeError:
        raise
    except urllib.error.HTTPError as exc:
        raise ExeError(
            f"{url} is not available right now (HTTP {exc.code}). Try again later."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - every failure is the same refusal to the player
        raise ExeError(f"{url} could not be reached ({_tell(exc)}). Try again later.") from exc
    if expect is not None and len(data) != expect:
        raise ExeError(
            f"{url} answered {len(data)} bytes where {expect} were asked for, so Yu'lon "
            "does not trust it."
        )
    return bytes(data), total


def _refuse_host(url: str, allowed_hosts: frozenset[str]) -> None:
    host = urllib.parse.urlsplit(url).hostname
    if host is None or host not in allowed_hosts or not _can_only_go_where_it_says(url, host):
        raise ExeError(
            f"A clean Wow.exe would be fetched from {host or url!r}, which is not a plain https "
            "address on a host this server's catalog entry names, so Yu'lon did not ask it."
        )


def _u(fmt: str, data: bytes, at: int = 0) -> tuple[int, ...]:
    return struct.unpack_from(fmt, data, at)


def _zip64_fields(extra: bytes, want: list[str], values: dict[str, int]) -> None:
    """Replace the 0xFFFFFFFF placeholders in `values` from a ZIP64 extra field, in spec order."""
    at = 0
    while at + 4 <= len(extra):
        tag, size = _u("<HH", extra, at)
        body = extra[at + 4 : at + 4 + size]
        if tag == 0x0001:
            slot = 0
            for name in want:
                if values[name] == 0xFFFFFFFF:
                    if slot + 8 > len(body):
                        raise ValueError("ZIP64 extra field too short")
                    values[name] = _u("<Q", body, slot)[0]
                    slot += 8
            return
        at += 4 + size
    if any(values[name] == 0xFFFFFFFF for name in want):
        raise ValueError("ZIP64 values missing")


def fetch_zip_member(
    url: str,
    member: str,
    *,
    opener: Opener = _open,
    allowed_hosts: frozenset[str],
) -> bytes:
    """One member of a zip at `url`, read with HTTP Range requests only, and proved.

    End-of-central-directory (and its ZIP64 record, for an archive past 4 GiB)
    -> the central directory -> the member's local header -> its data, stored
    or deflated, whose size and CRC-32 are checked against the directory.
    Nothing but those few ranges is ever requested.
    """
    _refuse_host(url, allowed_hosts)
    try:
        return _fetch_member(url, member, opener, allowed_hosts)
    except ExeError:
        raise
    except (struct.error, ValueError, zlib.error, IndexError) as exc:
        raise ExeError(
            f"{url} is not a zip Yu'lon can read the exe out of ({exc}). Try again later."
        ) from exc


def _fetch_member(url: str, member: str, opener: Opener, hosts: frozenset[str]) -> bytes:
    tail, total = _range(opener, url, hosts, f"-{_TAIL_BYTES}", expect=None)
    if total is None:
        raise ValueError("the server did not say how large the archive is")
    base = total - len(tail)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("no end-of-central-directory record")
    entries_total = _u("<H", tail, eocd + 10)[0]
    cd_size, cd_offset = _u("<II", tail, eocd + 12)
    locator = eocd - 20
    if locator >= 0 and tail[locator : locator + 4] == b"PK\x06\x07":
        z64_at = _u("<Q", tail, locator + 8)[0]
        if z64_at >= base and z64_at - base + 56 <= len(tail):
            record = tail[z64_at - base : z64_at - base + 56]
        else:
            record, _ = _range(opener, url, hosts, f"{z64_at}-{z64_at + 55}", expect=56)
        if record[:4] != b"PK\x06\x06":
            raise ValueError("bad ZIP64 end record")
        entries_total, cd_size, cd_offset = _u("<QQQ", record, 32)
    if cd_size > MAX_DIRECTORY_BYTES or cd_offset + cd_size > total:
        raise ValueError("the central directory is not where the archive says")
    if cd_offset >= base and cd_offset + cd_size - base <= len(tail):
        directory = tail[cd_offset - base : cd_offset - base + cd_size]
    else:
        directory, _ = _range(
            opener, url, hosts, f"{cd_offset}-{cd_offset + cd_size - 1}", expect=cd_size
        )
    wanted = member.encode("utf-8")
    at = 0
    for _ in range(entries_total):
        if directory[at : at + 4] != b"PK\x01\x02":
            raise ValueError("bad central directory entry")
        flags, method = _u("<HH", directory, at + 8)
        crc, csize, usize = _u("<III", directory, at + 16)
        name_len, extra_len, comment_len = _u("<HHH", directory, at + 28)
        offset = _u("<I", directory, at + 42)[0]
        name = directory[at + 46 : at + 46 + name_len]
        extra = directory[at + 46 + name_len : at + 46 + name_len + extra_len]
        at += 46 + name_len + extra_len + comment_len
        if name != wanted:
            continue
        values = {"usize": usize, "csize": csize, "offset": offset}
        _zip64_fields(extra, ["usize", "csize", "offset"], values)
        return _read_member(url, opener, hosts, member, flags, method, crc, values)
    raise ExeError(f"{url} has no member {member!r}, so the exe cannot be taken from it.")


def _read_member(
    url: str,
    opener: Opener,
    hosts: frozenset[str],
    member: str,
    flags: int,
    method: int,
    crc: int,
    values: dict[str, int],
) -> bytes:
    usize, csize, offset = values["usize"], values["csize"], values["offset"]
    if flags & 1 or method not in (0, 8):
        raise ExeError(f"{member} in {url} is encrypted or packed in a way Yu'lon does not read.")
    if usize > MAX_MEMBER_BYTES or csize > MAX_MEMBER_BYTES:
        raise ExeError(f"{member} in {url} is far larger than a Wow.exe, so it was not fetched.")
    header, _ = _range(opener, url, hosts, f"{offset}-{offset + 29}", expect=30)
    if header[:4] != b"PK\x03\x04":
        raise ValueError("bad local header")
    name_len, extra_len = _u("<HH", header, 26)
    start = offset + 30 + name_len + extra_len
    packed = b""
    if csize:
        packed, _ = _range(opener, url, hosts, f"{start}-{start + csize - 1}", expect=csize)
    if method == 0:
        data = packed
    else:
        inflater = zlib.decompressobj(-15)
        data = inflater.decompress(packed, usize + 1)
        if not inflater.eof:
            raise ValueError("the compressed data is longer than the directory says")
    if len(data) != usize or zlib.crc32(data) != crc:
        raise ExeError(
            f"{member} from {url} failed its size and checksum test, so it was thrown away. "
            "Try again later."
        )
    return data


# --- Stock bytes -----------------------------------------------------------------------------


def exe_cache_dir() -> Path:
    """Where proved clean exes are kept: `client-exe/` beside the packs' `client-packs/`."""
    return cache_dir().parent / "client-exe"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stock_in(path: Path, patch: ExePatch) -> bytes | None:
    """The bytes of `path` if it is the stock exe, else None. Never raises for a bad file."""
    try:
        if not path.is_file() or path.stat().st_size != patch.expect_size:
            return None
        data = path.read_bytes()
    except OSError:
        logger.warning("client exe: could not read %s", path, exc_info=True)
        return None
    return data if _sha256(data) == patch.expect_sha256 else None


def _hosts_of(source: CleanSource) -> frozenset[str]:
    host = urllib.parse.urlsplit(source.url).hostname
    return frozenset({host.lower()}) if host else frozenset()


def _write_new(target: Path, data: bytes, *, mode: int) -> None:
    """`data` at `target` through a new temporary file: never into an existing name's file."""
    tmp = target.with_name(target.name + TEMP_SUFFIX)
    try:
        st = tmp.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(errno.EISDIR, "a folder is in the way", str(tmp))
        tmp.unlink()  # a leftover, or a link: removed as a name, never written through
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode | stat.S_IWRITE)
        _put(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _put(tmp: Path, target: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
    try:
        play_client._retrying(lambda: os.replace(tmp, target), sleep=sleep)
        return
    except PermissionError:
        st = target.lstat()
        if st.st_mode & stat.S_IWRITE:
            raise
        if st.st_nlink > 1:
            raise ExeError(
                f"{target} is read-only and another folder shares it, so it was not replaced "
                "without making that one writable too. Delete the other ready-to-play client "
                "made from the same client and try again."
            ) from None
    os.chmod(target, stat.S_IMODE(st.st_mode) | stat.S_IWRITE)
    os.replace(tmp, target)


def _cache(stock: bytes, sha256: str) -> None:
    folder = exe_cache_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _write_new(folder / f"{sha256}.exe", stock, mode=0o644)
    except OSError:
        logger.warning("client exe: could not cache the clean exe", exc_info=True)


def _no_stock(patch: ExePatch, problems: list[str]) -> ExeError:
    where = ", ".join(source.url for source in patch.clean_sources)
    page = f" Or get a clean client from {patch.fallback_page}." if patch.fallback_page else ""
    why = f" ({'; '.join(problems)})" if problems else ""
    return ExeError(
        "This server's Wow.exe patches need a stock Wow.exe, and Yu'lon found none: yours is "
        f"not the unmodified one, and it could not fetch a clean one from {where}{why}."
        f"{page} Nothing was started and your own client was left as it was."
    )


def stock_bytes(
    play_dir: Path, original: Path, patch: ExePatch, *, opener: Opener = _open
) -> bytes:
    """The stock Wow.exe, from the first place that has one matching `patch.expect_sha256`.

    The ready-to-play exe, the player's original, the cache, then each clean
    source in order (cached once proved). `ExeError` names the sources and the
    fallback page when none has it.
    """
    for path in (
        client_executable(play_dir),
        client_executable(original),
        exe_cache_dir() / f"{patch.expect_sha256}.exe",
    ):
        found = _stock_in(path, patch)
        if found is not None:
            return found
    problems: list[str] = []
    for source in patch.clean_sources:
        try:
            data = fetch_zip_member(
                source.url, source.member, opener=opener, allowed_hosts=_hosts_of(source)
            )
        except ExeError as exc:
            problems.append(str(exc))
            continue
        if len(data) != patch.expect_size or _sha256(data) != patch.expect_sha256:
            problems.append(f"{source.url} holds a Wow.exe that is not the stock one")
            continue
        _cache(data, patch.expect_sha256)
        return data
    raise _no_stock(patch, problems)


# --- Patching --------------------------------------------------------------------------------


def patched(stock: bytes, patch: ExePatch, options: dict[str, bool]) -> bytes:
    """`stock` with the fixed writes, each option's chosen writes, then the PE flag.

    An option missing from `options` takes its catalog default. The input is
    not changed.
    """
    out = bytearray(stock)
    chosen: list[Any] = list(patch.writes)
    for name, option in patch.options.items():
        chosen += option.on if options.get(name, option.default) else option.off
    for write in chosen:
        if write.offset + write.length > len(out):
            raise ExeError(f"a patch at {write.offset:#x} runs past the end of this Wow.exe.")
        out[write.offset : write.offset + write.length] = write.payload()
    if patch.pe_large_address_aware:
        if len(out) < _PE_POINTER + 4:
            raise ExeError("this Wow.exe has no PE header to set large-address-aware in.")
        pe = struct.unpack_from("<I", out, _PE_POINTER)[0]
        if pe + 0x18 > len(out) or bytes(out[pe : pe + 4]) != b"PE\0\0":
            raise ExeError("this Wow.exe has no PE header to set large-address-aware in.")
        flags = struct.unpack_from("<H", out, pe + 0x16)[0]
        struct.pack_into("<H", out, pe + 0x16, flags | _LAA)
    return bytes(out)


def apply(
    play_dir: Path,
    original: Path,
    patch: ExePatch,
    options: dict[str, bool],
    *,
    opener: Opener = _open,
) -> dict[str, Any]:
    """Patch the ready-to-play client's Wow.exe from stock bytes; return what to record.

    Writes only when the result differs from what is there (so a Play with
    nothing to do changes no time), and then always as a new real file renamed
    into place, never into a hard link to the player's exe.
    """
    try:
        stock = stock_bytes(play_dir, original, patch, opener=opener)
        result = patched(stock, patch, options)
        target = client_executable(play_dir)
        if not target.exists() and not os.path.lexists(target):
            target = play_dir / client_executable(original).name
        current: bytes | None = None
        mode = 0o755
        try:
            st = target.lstat()
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISREG(st.st_mode) and st.st_size == len(result):
                current = target.read_bytes()
        except FileNotFoundError:
            pass
        if current != result:
            _write_new(target, result, mode=mode)
            logger.info("ready-to-play client: patched %s (build %d)", target, patch.build)
    except OSError as exc:
        raise ExeError(
            f"Yu'lon could not write the patched Wow.exe into {play_dir} ({_tell(exc)}). Check "
            "that Yu'lon may write there and that the game is not running, then press Play again."
        ) from exc
    return {
        "stock_sha256": patch.expect_sha256,
        "patched_sha256": _sha256(result),
        "options": {name: options.get(name, opt.default) for name, opt in patch.options.items()},
    }


def options_for(patch: ExePatch, chosen: Mapping[str, bool]) -> dict[str, bool]:
    """Every option of `patch`: the player's pick where there is one, else the default."""
    return {name: chosen.get(name, option.default) for name, option in patch.options.items()}


def exe_stale(play_dir: Path, original: Path, rec_exe: dict[str, Any] | None) -> bool:
    """Whether the ready-to-play Wow.exe is no longer what the record says Yu'lon made.

    Stale when it is missing or its SHA-256 is not the recorded patched one
    (the player's own file was copied over it, or a patcher rewrote it). The
    ORIGINAL's exe is not compared: the patched exe is built from stock bytes,
    which no change to the original can alter, so copying that one over this
    would only undo the patch. No record is never stale.
    """
    del original
    if rec_exe is None:
        return False
    want = rec_exe.get("patched_sha256")
    if not isinstance(want, str):
        return True
    try:
        data = client_executable(play_dir).read_bytes()
    except OSError:
        return True
    return _sha256(data) != want
