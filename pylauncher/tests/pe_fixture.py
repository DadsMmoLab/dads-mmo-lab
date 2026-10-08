"""Minimal PE files with a version resource, for the tests that read a client's build (T576)."""

from __future__ import annotations

import struct
from pathlib import Path

RSRC_RVA = 0x2000
RSRC_RAW = 0x400


def version_info(major: int, minor: int, patch: int, build: int) -> bytes:
    """A VS_VERSIONINFO block: the header, then VS_FIXEDFILEINFO with the file version."""
    fixed = struct.pack(
        "<13I",
        0xFEEF04BD,
        0x00010000,
        (major << 16) | minor,
        (patch << 16) | build,
        (major << 16) | minor,
        (patch << 16) | build,
        0x3F,
        0,
        0x4,
        0x1,
        0,
        0,
        0,
    )
    key = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0"
    head = struct.pack("<HHH", 40 + len(fixed), len(fixed), 0) + key
    head += b"\0" * (-len(head) % 4)
    return head + fixed


def resource_section(payload: bytes, *, rtype: int = 16) -> bytes:
    """A resource tree: type `rtype` -> name 1 -> language 0x409 -> `payload`."""
    root = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", rtype, 0x80000000 | 0x18)
    name = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1, 0x80000000 | 0x30)
    lang = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 0x409, 0x48)
    data_at = 0x48 + 16
    entry = struct.pack("<IIII", RSRC_RVA + data_at, len(payload), 0, 0)
    assert len(root) == 0x18 and len(name) == 0x18 and len(lang) == 0x18
    return root + name + lang + entry + payload


def pe(rsrc: bytes | None, *, pe32_plus: bool = False) -> bytes:
    """A PE file with one `.text` section and, when `rsrc` is given, a `.rsrc` one."""
    optional_size = 0xF0 if pe32_plus else 0xE0
    sections = 2 if rsrc is not None else 1
    dos = b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x80)
    dos += b"\0" * (0x80 - len(dos))
    coff = struct.pack("<HHIIIHH", 0x14C, sections, 0, 0, 0, optional_size, 0x102)
    optional = bytearray(optional_size)
    struct.pack_into("<H", optional, 0, 0x20B if pe32_plus else 0x10B)
    directories = 112 if pe32_plus else 96
    if rsrc is not None:
        struct.pack_into("<II", optional, directories + 16, RSRC_RVA, len(rsrc))
    table = struct.pack("<8sIIIIIIHHI", b".text", 0x100, 0x1000, 0x200, 0x200, 0, 0, 0, 0, 0x60)
    if rsrc is not None:
        table += struct.pack(
            "<8sIIIIIIHHI", b".rsrc", len(rsrc), RSRC_RVA, len(rsrc), RSRC_RAW, 0, 0, 0, 0, 0x40
        )
    headers = dos + b"PE\0\0" + coff + bytes(optional) + table
    image = headers + b"\0" * (0x200 - len(headers)) + b"\x90" * 0x200
    if rsrc is not None:
        image += rsrc
    return image


def exe(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def versioned(tmp_path: Path, *parts: int, pe32_plus: bool = False) -> Path:
    data = pe(resource_section(version_info(*parts)), pe32_plus=pe32_plus)
    return exe(tmp_path, "Wow.exe", data)
