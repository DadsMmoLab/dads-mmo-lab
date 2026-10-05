"""Patching a ready-to-play Wow.exe from stock bytes (`yulon.client_exe`, T181 c).

Blizzard's Wow.exe is never in this repo. `_stock()` builds a SYNTHETIC
7,704,216-byte stand-in: random filler (so a stray write anywhere shows up in a
diff), the stock bytes the spec lists at every patched offset, and a minimal PE
header, and the patch under test names that buffer's own SHA-256. One opt-in
test checks a real exe when `YULON_REAL_WOW_335A_EXE` points at one.

No network: `_Archive` serves a zip by Range, either a small real one or a
virtual ZIP64 archive past 4 GiB whose central directory is where a real one
would be, built from a few hand-written records and zeros nobody asks for.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import struct
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import client_exe, platform
from yulon.catalog.catalog import ExePatch
from yulon.client_exe import ExeError
from yulon.update import _Deadline

SIZE = 7_704_216
HOST = "clean.example.org"
URL = f"https://{HOST}/WoW-Client-3.3.5a.zip"
MEMBER = "WoW-3.3.5a/Wow.exe"
PE = 0x80
JUMPS = (0x1F41BF, 0x415A25, 0x415A3F, 0x415A95, 0x415B46, 0x415B5F, 0x33D7C9, 0x0355BF)
JUMPS += (0x1DDC5D, 0x10CA41, 0x469A2C, 0x528AA2, 0x4691B1, 0x469183, 0x16D899, 0x2DB241)
JUMPS += (0x5CFBC0,)
BORDERLESS = 0x0E94

# (offset, stock bytes) the synthetic exe holds; the patch below rewrites each.
STOCK_BYTES: list[tuple[int, bytes]] = [
    (0x5F3A00, b"12340\x00"),
    (0x4C99F0, struct.pack("<H", 12340)),
    (0x6404F, b"\x0a"),
    (BORDERLESS, b"\x74"),
    (0x2E1C67, b"\x55" * 11),
    (0x33E0D6, b"\x55" * 22),
    *[(at, b"\x74") for at in JUMPS],
]


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    monkeypatch.setattr(platform, "config_dir", lambda: config)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return config / "client-exe"


@pytest.fixture(scope="module")
def stock() -> bytes:
    buffer = bytearray(random.Random(335).randbytes(SIZE))
    for offset, data in STOCK_BYTES:
        buffer[offset : offset + len(data)] = data
    struct.pack_into("<I", buffer, 0x3C, PE)
    buffer[PE : PE + 4] = b"PE\0\0"
    struct.pack_into("<H", buffer, PE + 0x16, 0x0103)
    return bytes(buffer)


def _patch_dict(stock: bytes, **extra: Any) -> dict[str, Any]:
    return {
        "expect_sha256": hashlib.sha256(stock).hexdigest(),
        "expect_size": len(stock),
        "clean_sources": [{"url": URL, "member": MEMBER}],
        "fallback_page": "https://fallback.example.org/clients/",
        "writes": [
            {"offset": 0x5F3A00, "bytes": "313233343200"},
            {"offset": 0x4C99F0, "bytes": "3630"},
            {"offset": 0x6404F, "bytes": "14"},
            {"offset": 0x2E1C67, "fill": 0x90, "count": 11},
            {"offset": 0x33E0D6, "fill": 0x90, "count": 22},
            *[{"offset": at, "bytes": "eb"} for at in JUMPS],
        ],
        "options": {
            "borderless": {
                "label": "Borderless window",
                "default": True,
                "on": [{"offset": BORDERLESS, "bytes": "eb"}],
                "off": [{"offset": BORDERLESS, "bytes": "74"}],
            }
        },
        "pe_large_address_aware": True,
        "build": 12342,
        **extra,
    }


@pytest.fixture(scope="module")
def patch(stock: bytes) -> ExePatch:
    return ExePatch.model_validate(_patch_dict(stock))


def _diff(a: bytes, b: bytes) -> list[int]:
    return [i for i in range(len(a)) if a[i] != b[i]] if a != b else []


# --- patched() -------------------------------------------------------------------------------


def test_patched_puts_exactly_the_tables_bytes_and_nothing_else_changes(
    stock: bytes, patch: ExePatch
) -> None:
    out = client_exe.patched(stock, patch, {"borderless": True})
    expected = bytearray(stock)
    for write in patch.writes:
        expected[write.offset : write.offset + write.length] = write.payload()
    expected[BORDERLESS] = 0xEB
    struct.pack_into("<H", expected, PE + 0x16, 0x0123)
    assert out == bytes(expected)
    listed = {i for w in patch.writes for i in range(w.offset, w.offset + w.length)}
    listed |= {BORDERLESS, PE + 0x16}
    assert set(_diff(stock, out)) <= listed
    assert out[0x5F3A00:0x5F3A06] == b"12342\x00"
    assert out[0x2E1C67 : 0x2E1C67 + 11] == b"\x90" * 11  # a fill
    assert len(out) == SIZE
    assert stock[BORDERLESS] == 0x74  # the input is not changed


def test_an_option_off_writes_its_off_bytes_and_a_missing_one_takes_its_default(
    stock: bytes, patch: ExePatch
) -> None:
    off = client_exe.patched(stock, patch, {"borderless": False})
    assert off[BORDERLESS] == 0x74
    assert client_exe.patched(stock, patch, {})[BORDERLESS] == 0xEB  # default is on
    assert off != client_exe.patched(stock, patch, {"borderless": True})


def test_large_address_aware_ors_its_flag_and_keeps_the_others(
    stock: bytes, patch: ExePatch
) -> None:
    out = client_exe.patched(stock, patch, {})
    assert struct.unpack_from("<H", out, PE + 0x16)[0] == 0x0103 | 0x0020
    plain = ExePatch.model_validate(_patch_dict(stock, pe_large_address_aware=False))
    assert struct.unpack_from("<H", client_exe.patched(stock, plain, {}), PE + 0x16)[0] == 0x0103


def test_large_address_aware_refuses_an_exe_with_no_pe_signature(
    stock: bytes, patch: ExePatch
) -> None:
    broken = bytearray(stock)
    broken[PE : PE + 4] = b"PX\0\0"
    with pytest.raises(ExeError, match="PE header"):
        client_exe.patched(bytes(broken), patch, {})


# --- A zip served by Range -------------------------------------------------------------------


class _Backing:
    size: int

    def read(self, start: int, end: int) -> bytes:  # inclusive end
        raise NotImplementedError


@dataclass
class _Bytes(_Backing):
    data: bytes

    @property
    def size(self) -> int:  # type: ignore[override]
        return len(self.data)

    def read(self, start: int, end: int) -> bytes:
        return self.data[start : end + 1]


@dataclass
class _Virtual(_Backing):
    """An archive of `size` bytes where only the listed segments are not zero."""

    size: int  # type: ignore[misc]
    segments: dict[int, bytes]

    def read(self, start: int, end: int) -> bytes:
        out = bytearray(end - start + 1)
        for at, data in self.segments.items():
            lo, hi = max(at, start), min(at + len(data) - 1, end)
            if lo <= hi:
                out[lo - start : hi - start + 1] = data[lo - at : hi - at + 1]
        return bytes(out)


@dataclass
class _Reply:
    status: int
    headers: dict[str, str]
    body: bytes
    _pos: int = 0

    def read1(self, amount: int, /) -> bytes:
        data = self.body[self._pos : self._pos + amount]
        self._pos += len(data)
        return data

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return self.headers.get(name, default)

    def close(self) -> None:
        pass


@dataclass
class _Archive:
    """A fake opener: Range requests answered 206 from a backing, every request recorded."""

    backing: _Backing
    ignore_range: bool = False
    requests: list[tuple[str, dict[str, str], frozenset[str]]] = field(default_factory=list)

    def __call__(
        self,
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Reply:
        self.requests.append((url, dict(headers), hosts))
        spec = headers.get("Range", "")
        size = self.backing.size
        if self.ignore_range or not spec.startswith("bytes="):
            return _Reply(200, {}, self.backing.read(0, min(size, 1 << 20) - 1))
        first, _, last = spec[len("bytes=") :].partition("-")
        if first == "":
            start, end = max(0, size - int(last)), size - 1
        else:
            start, end = int(first), min(int(last), size - 1)
        body = self.backing.read(start, end)
        return _Reply(206, {"Content-Range": f"bytes {start}-{end}/{size}"}, body)

    @property
    def bytes_asked(self) -> int:
        total = 0
        for _, headers, _ in self.requests:
            first, _, last = headers["Range"][len("bytes=") :].partition("-")
            total += int(last) if first == "" else int(last) - int(first) + 1
        return total


def _small_zip(members: Mapping[str, bytes], *, stored: bool = False) -> bytes:
    buffer = io.BytesIO()
    method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(buffer, "w", compression=method) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _zip64_archive(member: str, data: bytes, *, deflate: bool, crc: int | None = None) -> _Virtual:
    """A ZIP64 archive whose member sits past 4 GiB and whose directory sits past 5 GiB."""
    local_at = 0x1_2000_0000
    raw = zlib.compress(data, 6)[2:-4] if deflate else data
    name = member.encode()
    crc32 = zlib.crc32(data) if crc is None else crc
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        45,
        0,
        8 if deflate else 0,
        0,
        0,
        crc32,
        len(raw),
        len(data),
        len(name),
        0,
    )
    local += name
    decoy = b"WoW-3.3.5a/Wow.exe.bak"
    extra = struct.pack("<HHQ", 0x0001, 8, local_at)  # only the offset overflowed
    entry = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 45, 45, 0, 8 if deflate else 0, 0, 0, crc32, len(raw), len(data),
        len(name), len(extra), 0, 0, 0, 0, 0xFFFFFFFF,
    )  # fmt: skip
    entry += name + extra
    other = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 45, 45, 0, 0, 0, 0, 0, 0, 0, len(decoy), 0, 0, 0, 0, 0, 0,
    )  # fmt: skip
    other += decoy
    directory = other + entry
    cd_at = 0x1_5000_0000
    z64_at = cd_at + len(directory)
    z64 = struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, 2, 2, len(directory), cd_at)
    locator = struct.pack("<IIQI", 0x07064B50, 0, z64_at, 1)
    eocd = struct.pack(
        "<IHHHHIIH", 0x06054B50, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0
    )
    tail = z64 + locator + eocd
    return _Virtual(
        z64_at + len(tail),
        {local_at: local + raw, cd_at: directory, z64_at: tail},
    )


# --- fetch_zip_member ------------------------------------------------------------------------

HOSTS = frozenset({HOST})


@pytest.mark.parametrize("deflate", [True, False])
def test_a_member_past_4_gib_is_read_through_zip64_by_range_requests_only(deflate: bool) -> None:
    data = random.Random(1).randbytes(300_000)
    site = _Archive(_zip64_archive(MEMBER, data, deflate=deflate))
    assert site.backing.size > 5 * 2**30  # the directory really is past 4 GiB
    got = client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)
    assert got == data
    assert all(h.get("Range", "").startswith("bytes=") for _, h, _ in site.requests)
    assert site.bytes_asked < 1_000_000 + 200_000  # a tail, a header, the data: never the archive
    assert {url for url, _, _ in site.requests} == {URL}
    assert all(hosts == HOSTS for _, _, hosts in site.requests)


def test_a_small_ordinary_zip_works_too_and_the_right_member_is_picked() -> None:
    zip_bytes = _small_zip({"other.txt": b"no", MEMBER: b"MZ" + bytes(5000)})
    site = _Archive(_Bytes(zip_bytes))
    got = client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)
    assert got == b"MZ" + bytes(5000)


def test_a_member_whose_crc_does_not_match_is_refused() -> None:
    data = random.Random(2).randbytes(10_000)
    site = _Archive(_zip64_archive(MEMBER, data, deflate=False, crc=0xDEADBEEF))
    with pytest.raises(ExeError, match="checksum"):
        client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)


def test_a_stored_member_with_a_flipped_byte_is_refused() -> None:
    zip_bytes = bytearray(_small_zip({MEMBER: b"A" * 3000}, stored=True))
    zip_bytes[100] ^= 0xFF  # inside the stored data: the header is 30 + name bytes
    site = _Archive(_Bytes(bytes(zip_bytes)))
    with pytest.raises(ExeError, match="checksum"):
        client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)


def test_a_member_that_is_not_in_the_zip_is_refused_naming_it() -> None:
    site = _Archive(_Bytes(_small_zip({"a.txt": b"x"})))
    with pytest.raises(ExeError, match="no member"):
        client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)


def test_a_server_that_ignores_range_is_refused_not_read_whole() -> None:
    site = _Archive(_Bytes(_small_zip({MEMBER: b"x" * 100})), ignore_range=True)
    with pytest.raises(ExeError, match="byte-range"):
        client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)


def test_a_host_the_entry_does_not_name_is_never_asked() -> None:
    site = _Archive(_Bytes(_small_zip({MEMBER: b"x"})))
    with pytest.raises(ExeError, match="not a plain https"):
        client_exe.fetch_zip_member(
            URL, MEMBER, opener=site, allowed_hosts=frozenset({"elsewhere.example.org"})
        )
    assert site.requests == []


def test_a_member_far_larger_than_an_exe_is_not_fetched() -> None:
    data = b"\0" * (client_exe.MAX_MEMBER_BYTES + 1)
    site = _Archive(_Bytes(_small_zip({MEMBER: data})))
    with pytest.raises(ExeError, match="far larger"):
        client_exe.fetch_zip_member(URL, MEMBER, opener=site, allowed_hosts=HOSTS)
    assert site.bytes_asked < 100_000


def test_a_dropped_connection_is_an_exe_error() -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionResetError("reset")

    with pytest.raises(ExeError, match="could not be reached"):
        client_exe.fetch_zip_member(URL, MEMBER, opener=broken, allowed_hosts=HOSTS)


# --- apply / stock_bytes ---------------------------------------------------------------------


def _clients(tmp_path: Path, play_exe: bytes | None, orig_exe: bytes | None) -> tuple[Path, Path]:
    play, orig = tmp_path / "play", tmp_path / "orig"
    play.mkdir()
    orig.mkdir()
    if play_exe is not None:
        (play / "Wow.exe").write_bytes(play_exe)
    if orig_exe is not None:
        (orig / "Wow.exe").write_bytes(orig_exe)
    return play, orig


def _off_by_one(data: bytes) -> bytes:
    broken = bytearray(data)
    broken[0x5F3A00] ^= 1  # the build string's first digit
    return bytes(broken)


def test_apply_patches_a_stock_copy_and_records_what_it_made(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, stock, stock)
    done = client_exe.apply(play, orig, patch, {"borderless": False})
    want = client_exe.patched(stock, patch, {"borderless": False})
    assert (play / "Wow.exe").read_bytes() == want
    assert done == {
        "stock_sha256": patch.expect_sha256,
        "patched_sha256": hashlib.sha256(want).hexdigest(),
        "options": {"borderless": False},
    }
    assert (orig / "Wow.exe").read_bytes() == stock


def test_a_second_apply_writes_nothing(tmp_path: Path, stock: bytes, patch: ExePatch) -> None:
    play, orig = _clients(tmp_path, stock, stock)
    first = client_exe.apply(play, orig, patch, {})
    os.utime(play / "Wow.exe", ns=(10**9, 10**9))
    before = (play / "Wow.exe").stat()
    again = client_exe.apply(play, orig, patch, {})
    after = (play / "Wow.exe").stat()
    assert again == first
    assert (after.st_mtime_ns, after.st_ino) == (before.st_mtime_ns, before.st_ino)
    assert not list(play.glob("*.yulon-exe-tmp"))


def test_changing_an_option_rewrites_it_from_stock_bytes(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, stock, stock)
    client_exe.apply(play, orig, patch, {"borderless": True})
    client_exe.apply(play, orig, patch, {"borderless": False})
    assert (play / "Wow.exe").read_bytes() == client_exe.patched(
        stock, patch, {"borderless": False}
    )


def test_the_ready_to_play_exe_is_a_real_copy_never_a_write_through_a_link(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, None, stock)
    os.link(orig / "Wow.exe", play / "Wow.exe")
    client_exe.apply(play, orig, patch, {})
    assert (orig / "Wow.exe").read_bytes() == stock  # the player's own exe is untouched
    assert not os.path.samestat((orig / "Wow.exe").stat(), (play / "Wow.exe").stat())
    assert (play / "Wow.exe").read_bytes() == client_exe.patched(stock, patch, {})
    assert (orig / "Wow.exe").stat().st_nlink == 1


def test_a_leftover_temp_that_is_a_link_to_the_players_exe_is_removed_not_written_through(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, stock, stock)
    os.link(orig / "Wow.exe", play / ("Wow.exe" + client_exe.TEMP_SUFFIX))
    client_exe.apply(play, orig, patch, {})
    assert (orig / "Wow.exe").read_bytes() == stock
    assert (play / "Wow.exe").read_bytes() == client_exe.patched(stock, patch, {})
    assert not list(play.glob("*.yulon-exe-tmp"))


def test_a_failed_write_leaves_the_old_exe_whole_and_no_temp_behind(
    tmp_path: Path, stock: bytes, patch: ExePatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    play, orig = _clients(tmp_path, stock, stock)

    def refuse(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(client_exe.os, "replace", refuse)
    with pytest.raises(ExeError, match="No space left"):
        client_exe.apply(play, orig, patch, {})
    assert (play / "Wow.exe").read_bytes() == stock
    assert not list(play.glob("*.yulon-exe-tmp"))


def test_a_non_stock_exe_is_never_patched_and_the_clean_one_is_fetched_then_cached(
    tmp_path: Path, stock: bytes, patch: ExePatch, _cache_in_tmp: Path
) -> None:
    bad = _off_by_one(stock)
    play, orig = _clients(tmp_path, bad, bad)
    site = _Archive(_Bytes(_small_zip({MEMBER: stock})))
    done = client_exe.apply(play, orig, patch, {}, opener=site)
    want = client_exe.patched(stock, patch, {})  # from the CLEAN bytes
    assert (play / "Wow.exe").read_bytes() == want
    assert done["stock_sha256"] == patch.expect_sha256
    assert (_cache_in_tmp / f"{patch.expect_sha256}.exe").read_bytes() == stock
    assert (orig / "Wow.exe").read_bytes() == bad
    asked = len(site.requests)
    assert asked > 0
    # The play exe is now patched (not stock) and the original still is not: the cache serves.
    client_exe.apply(play, orig, patch, {"borderless": False}, opener=site)
    assert len(site.requests) == asked
    assert (play / "Wow.exe").read_bytes() == client_exe.patched(
        stock, patch, {"borderless": False}
    )


def test_fetching_the_clean_exe_is_a_line_in_the_log(
    tmp_path: Path,
    stock: bytes,
    patch: ExePatch,
    _cache_in_tmp: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T211 2: the T179 live check found no line in yulon.log for the clean-exe fetch."""
    import logging

    bad = _off_by_one(stock)
    play, orig = _clients(tmp_path, bad, bad)
    site = _Archive(_Bytes(_small_zip({MEMBER: stock})))
    with caplog.at_level(logging.INFO, logger="yulon"):
        client_exe.stock_bytes(play, orig, patch, opener=site)
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    url = patch.clean_sources[0].url
    assert any("fetched" in line and url in line for line in said), said


def test_a_source_that_serves_a_non_stock_exe_is_refused_and_nothing_is_cached(
    tmp_path: Path, stock: bytes, patch: ExePatch, _cache_in_tmp: Path
) -> None:
    bad = _off_by_one(stock)
    play, orig = _clients(tmp_path, bad, bad)
    site = _Archive(_Bytes(_small_zip({MEMBER: bad})))
    with pytest.raises(ExeError, match="not the stock one"):
        client_exe.apply(play, orig, patch, {}, opener=site)
    assert (play / "Wow.exe").read_bytes() == bad
    assert not _cache_in_tmp.exists() or not list(_cache_in_tmp.iterdir())


def test_a_corrupt_cache_entry_is_not_trusted(
    tmp_path: Path, stock: bytes, patch: ExePatch, _cache_in_tmp: Path
) -> None:
    bad = _off_by_one(stock)
    play, orig = _clients(tmp_path, bad, bad)
    _cache_in_tmp.mkdir(parents=True)
    (_cache_in_tmp / f"{patch.expect_sha256}.exe").write_bytes(bad)
    site = _Archive(_Bytes(_small_zip({MEMBER: stock})))
    assert client_exe.stock_bytes(play, orig, patch, opener=site) == stock
    assert site.requests  # it went to the source
    assert (_cache_in_tmp / f"{patch.expect_sha256}.exe").read_bytes() == stock


def test_the_original_is_a_stock_source_when_the_ready_to_play_exe_is_not(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, _off_by_one(stock), stock)
    site = _Archive(_Bytes(b""))
    assert client_exe.stock_bytes(play, orig, patch, opener=site) == stock
    assert site.requests == []


def test_no_stock_anywhere_names_every_source_and_the_fallback_page(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    bad = _off_by_one(stock)
    play, orig = _clients(tmp_path, bad, bad)

    def down(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionResetError("reset")

    with pytest.raises(ExeError) as caught:
        client_exe.apply(play, orig, patch, {}, opener=down)
    text = str(caught.value)
    assert URL in text and "https://fallback.example.org/clients/" in text
    assert (play / "Wow.exe").read_bytes() == bad


def test_each_source_is_tried_in_order_and_the_first_good_one_wins(
    tmp_path: Path, stock: bytes
) -> None:
    second = "https://backup.example.org/c.zip"
    two = ExePatch.model_validate(
        _patch_dict(
            stock,
            clean_sources=[
                {"url": URL, "member": MEMBER},
                {"url": second, "member": MEMBER},
            ],
        )
    )
    play, orig = _clients(tmp_path, None, _off_by_one(stock))
    good = _Archive(_Bytes(_small_zip({MEMBER: stock})))
    seen: list[str] = []

    def opener(url: str, watcher: _Deadline, **kw: Any) -> Any:
        seen.append(url)
        if url == URL:
            raise ConnectionResetError("first is down")
        return good(url, watcher, **kw)

    assert client_exe.stock_bytes(play, orig, two, opener=opener) == stock
    assert seen[0] == URL and seen[-1] == second
    # each source is allowed only its OWN host
    assert {h for _, _, h in good.requests} == {frozenset({"backup.example.org"})}


# --- exe_stale -------------------------------------------------------------------------------


def test_exe_stale_is_about_the_recorded_patched_checksum_only(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, stock, stock)
    record = client_exe.apply(play, orig, patch, {})
    assert client_exe.exe_stale(play, orig, record) is False
    assert client_exe.exe_stale(play, orig, None) is False
    (orig / "Wow.exe").write_bytes(b"MZ-the player's patched exe")  # the original changed
    assert client_exe.exe_stale(play, orig, record) is False
    (play / "Wow.exe").write_bytes(stock)  # a copy of the unpatched exe over it
    assert client_exe.exe_stale(play, orig, record) is True


def test_exe_stale_is_true_for_a_missing_exe_or_a_record_without_a_checksum(
    tmp_path: Path, stock: bytes, patch: ExePatch
) -> None:
    play, orig = _clients(tmp_path, stock, stock)
    record = client_exe.apply(play, orig, patch, {})
    assert client_exe.exe_stale(play, orig, {"options": {}}) is True
    (play / "Wow.exe").unlink()
    assert client_exe.exe_stale(play, orig, record) is True


# --- the real exe, opt in --------------------------------------------------------------------

REAL = os.environ.get("YULON_REAL_WOW_335A_EXE")


@pytest.mark.skipif(not REAL, reason="YULON_REAL_WOW_335A_EXE does not point at a 12340 Wow.exe")
def test_the_real_stock_exe_has_the_recorded_checksum_and_stock_bytes_at_every_offset(
    patch: ExePatch,
) -> None:
    assert REAL is not None
    data = Path(REAL).read_bytes()
    assert len(data) == 7_704_216
    assert (
        hashlib.sha256(data).hexdigest()
        == "aa63a5750d60ef16746c686b3d5e26876d98953eab08b1c026cd0faf78e88cb8"
    )
    # What the spec records as measured on the real exe: the build, the 20-character list byte and
    # the `74` jumps at the signature-check sites (the other offsets' bytes are not recorded).
    for offset, expected in STOCK_BYTES[:3] + [(at, b"\x74") for at in JUMPS[:6]]:
        assert data[offset : offset + len(expected)] == expected, hex(offset)
    # Run against the real exe, the patch table must be this repo's own SHA's patch.
    real_patch = ExePatch.model_validate(
        _patch_dict(data, expect_sha256=hashlib.sha256(data).hexdigest())
    )
    out = client_exe.patched(data, real_patch, {"borderless": True})
    assert out[0x5F3A00:0x5F3A06] == b"12342\x00"
    assert struct.unpack_from("<H", out, 0x4C99F0)[0] == 12342
    assert out[0x6404F] == 0x14
