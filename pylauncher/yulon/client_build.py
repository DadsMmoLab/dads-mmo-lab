"""Which build a client's `Wow.exe` is, read from the exe itself (T576).

A 3.3.5a realm's authserver accepts a login from every build its `build_info`
lists (11723, 12340, 13930, ...), so an older WotLK client gets past the password
and is then dropped by the world server, whose packet layout it does not speak.
The player sees "password accepted, then disconnected" and nothing says why.
This reads the build out of the exe's PE version resource (`VS_FIXEDFILEINFO`,
the "File version" of the exe's Properties > Details) so Yu'lon can say it first.

Pure Python and no dependency: a PE file is a few tables, and only the headers
and the one version resource are read, never the whole 6 MB exe.

**Never block the unknown.** An exe with no readable version resource, or one that
is not a PE file at all (a repack's launcher, a stub), has no version here and is
accepted by `refusal()`; it logs one line saying so. Only a build that is read and
is a different number is refused.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from yulon.log import get_logger

logger = get_logger(__name__)

_RT_VERSION = 16
_FIXED_SIGNATURE = 0xFEEF04BD
_MAX_SECTIONS = 96
_MAX_RESOURCE_BYTES = 1 << 20
"""A version resource is a few hundred bytes; one claiming a megabyte is not read."""
_MAX_DIRECTORY_ENTRIES = 4096


@dataclass(frozen=True)
class ExeVersion:
    """The four parts of an exe's file version: `3.3.5.12340` is 3, 3, 5 and build 12340."""

    major: int
    minor: int
    patch: int
    build: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch} ({self.build})"


class _Unreadable(Exception):
    """The file is not a PE file this module can walk (cut short, or the tables lie)."""


def _at(handle: BinaryIO, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0:
        raise _Unreadable("negative offset")
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise _Unreadable("the file ends inside a table")
    return data


def _parse(path: Path) -> ExeVersion | None:
    """The version in `path`'s resources, or None for no version resource or an unreadable file."""
    try:
        with open(path, "rb") as handle:
            return _walk(handle)
    except (OSError, _Unreadable, struct.error) as exc:
        logger.info("client build: %s has no readable PE version (%s)", path, exc)
        return None


def _walk(handle: BinaryIO) -> ExeVersion | None:
    if _at(handle, 0, 2) != b"MZ":
        raise _Unreadable("not a PE file")
    (pe_at,) = struct.unpack("<I", _at(handle, 0x3C, 4))
    if _at(handle, pe_at, 4) != b"PE\0\0":
        raise _Unreadable("no PE signature")
    sections, optional_size = struct.unpack("<H12xH", _at(handle, pe_at + 6, 16))
    optional = pe_at + 24
    magic = struct.unpack("<H", _at(handle, optional, 2))[0]
    if magic not in (0x10B, 0x20B):
        raise _Unreadable("unknown optional header")
    directories = optional + (112 if magic == 0x20B else 96)
    rsrc_rva, rsrc_size = struct.unpack("<II", _at(handle, directories + 16, 8))
    if rsrc_rva == 0 or rsrc_size == 0:
        return None
    if not 0 < sections <= _MAX_SECTIONS:
        raise _Unreadable("odd section count")
    table = _at(handle, optional + optional_size, 40 * sections)
    spans: list[tuple[int, int, int]] = []
    for index in range(sections):
        virtual_size, virtual, raw_size, raw = struct.unpack_from("<8xIIII", table, index * 40)
        spans.append((virtual, max(virtual_size, raw_size), raw))

    def file_offset(rva: int) -> int:
        for virtual, size, raw in spans:
            if virtual <= rva < virtual + size:
                return raw + (rva - virtual)
        raise _Unreadable("a resource points outside every section")

    root = file_offset(rsrc_rva)

    def inside(offset: int, size: int) -> int:
        """`root + offset`, when `size` bytes there lie within the declared resource directory."""
        if offset < 0 or offset + size > rsrc_size:
            raise _Unreadable("a resource offset points outside the resource directory")
        return root + offset

    def entries(directory: int) -> list[tuple[int, int]]:
        named, ids = struct.unpack("<HH", _at(handle, inside(directory, 16) + 12, 4))
        count = named + ids
        if count > _MAX_DIRECTORY_ENTRIES:
            raise _Unreadable("too many resource entries")
        raw = _at(handle, inside(directory, 16 + 8 * count) + 16, 8 * count)
        return [struct.unpack_from("<II", raw, 8 * i) for i in range(count)]

    def first_below(offset_field: int) -> int:
        """The data entry under a subdirectory, taking each level's first entry."""
        offset = offset_field
        for _ in range(3):
            if not offset & 0x80000000:
                return offset
            below = entries(offset & 0x7FFFFFFF)
            if not below:
                raise _Unreadable("an empty resource directory")
            offset = below[0][1]
        return offset

    for ident, offset in entries(0):
        if ident != _RT_VERSION or not offset & 0x80000000:
            continue
        data_entry = first_below(offset)
        data_rva, data_size = struct.unpack("<II", _at(handle, inside(data_entry, 16), 8))
        if not 0 < data_size <= _MAX_RESOURCE_BYTES:
            raise _Unreadable("odd version resource size")
        if data_rva < rsrc_rva or data_rva + data_size > rsrc_rva + rsrc_size:
            raise _Unreadable("the version resource lies outside the resource directory")
        return _fixed_version(_at(handle, file_offset(data_rva), data_size))
    return None


_KEY = "VS_VERSION_INFO\0".encode("utf-16-le")
_FIXED_AT = 40
"""Where VS_FIXEDFILEINFO starts: the 6-byte header, the 32-byte key, padded to 4."""


def _fixed_version(block: bytes) -> ExeVersion | None:
    """The file version of a VS_VERSIONINFO block, or None when it is not one.

    The block is checked as the structure it is: a header whose value length is
    VS_FIXEDFILEINFO's 52 bytes, the key `VS_VERSION_INFO`, and the fixed info's
    signature at the one place it sits. Bytes that merely contain the signature
    somewhere are not a version, so a stray or hostile resource never invents a build.
    """
    if len(block) < _FIXED_AT + 16 or block[6 : 6 + len(_KEY)] != _KEY:
        return None
    length, value_length = struct.unpack_from("<HH", block, 0)
    if value_length != 52 or length < _FIXED_AT + 52 or length > len(block) + 3:
        return None
    if struct.unpack_from("<I", block, _FIXED_AT)[0] != _FIXED_SIGNATURE:
        return None
    ms, ls = struct.unpack_from("<II", block, _FIXED_AT + 8)
    return ExeVersion(ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


_cache: dict[tuple[str, int, int], ExeVersion | None] = {}
"""What was read from an exe, by path, size and mtime: once per client, so Play stays fast."""


def forget_cached_builds() -> None:
    """Empty the cache (tests; a changed exe is read again on its own, by size and mtime)."""
    _cache.clear()


def read_version(exe: Path) -> ExeVersion | None:
    """`exe`'s file version, or None when it has none Yu'lon can read. Never raises."""
    try:
        info = exe.stat()
    except OSError:
        return None
    key = (str(exe), info.st_size, info.st_mtime_ns)
    if key not in _cache:
        _cache[key] = _parse(exe)
    return _cache[key]


def refusal(exe: Path, *, version: str, build: int | None) -> str | None:
    """The sentence refusing `exe`, or None when it may be used.

    `version` and `build` are what the server's catalog entry asks of the player's
    own client (`3.3.5a`, `12340`). No `build`, no exe to read, or an exe with no
    readable version resource is accepted: only a build that was read and is not
    `build` is refused, and the sentence names both.
    """
    if build is None or not exe.is_file():
        return None
    found = read_version(exe)
    if found is None:
        logger.info("client build: %s has no version resource; accepted as it is", exe)
        return None
    if found.build == build:
        return None
    return (
        f"That game client is {found}, not {version} ({build}). This server only lets "
        f"a {version} client, build {build}, stay connected: any other build is "
        "accepted at the password and then disconnected by the world server. "
        f"Use a stock {version} client folder."
    )
