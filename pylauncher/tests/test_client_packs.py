"""Getting and proving a server's client packs (`yulon.client_packs`, T181 b).

No network: a URL pack is served by `_Site`, a fake opener holding bytes per URL
that answers HEAD, GET and `Range` the way a static file server does, and records
every request so a test can say what was NOT asked. The cache is pointed at
`tmp_path` by moving `platform.config_dir()`, never the real one.

Each refusal is matched on a fragment of its own message, so a fixture that
trips a different rule than the one it is about fails rather than passes; and
each positive base (`_url_pack`, `_zip`) is shown to work on its own first.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import shutil
import time
import types
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import client_config, client_packs, platform, play_client, server_build_presses
from yulon.catalog.catalog import ClientPack
from yulon.client_packs import (
    Cancelled,
    PackError,
    PackUnavailable,
    fetch_checkout,
    fetch_url,
)
from yulon.selfupdate import fetch
from yulon.update import _Deadline

HOST = "packs.example.org"
ZIP_URL = f"https://{HOST}/downloads/hd-creatures.zip"
VERSION_URL = f"https://{HOST}/downloads/hd-creatures.version"
HOSTS = frozenset({HOST})
LATEST = server_build_presses.UPDATE_TO_LATEST


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    monkeypatch.setattr(platform, "config_dir", lambda: config)
    return config / "client-packs"


def _zip(members: Mapping[str, bytes] | None = None) -> bytes:
    """A real zip, stored (not deflated) so a test can flip a byte of a member's data."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in (members or {"patch-F.MPQ": b"MPQ\x1a" + bytes(range(256)) * 40}).items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


def _checkout_pack(path: str, **checksum: str) -> ClientPack:
    return ClientPack.model_validate(
        {
            "id": "patch-y",
            "label": "World patch",
            "source": {"kind": "checkout", "path": path},
            **checksum,
            "install": [{"member": "patch-Y.MPQ", "to": "Data/patch-X.MPQ"}],
        }
    )


def _url_pack(*, version: bool = True, **extra: Any) -> ClientPack:
    source: dict[str, str] = {"kind": "url", "url": ZIP_URL}
    if version:
        source["version_url"] = VERSION_URL
    return ClientPack.model_validate(
        {
            "id": "hd-creatures",
            "label": "HD creatures",
            "source": source,
            "install": [{"member": "patch-F.MPQ", "to": "Data/patch-F.MPQ"}],
            "optional": True,
            **extra,
        }
    )


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes
    cut_after: int | None = None
    chunk: int = 4096
    _pos: int = 0
    closed: bool = False

    def read1(self, amount: int, /) -> bytes:
        if self.cut_after is not None and self._pos >= self.cut_after:
            raise ConnectionResetError("connection reset by peer")
        end = min(len(self.body), self._pos + min(amount, self.chunk))
        if self.cut_after is not None:
            end = min(end, self.cut_after)
        data = self.body[self._pos : end]
        self._pos = end
        return data

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return self.headers.get(name, default)

    def close(self) -> None:
        self.closed = True


@dataclass
class _Site:
    """A static file server: bytes per URL, HEAD, GET and single `bytes=N-` ranges."""

    files: dict[str, bytes]
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    cut_after: int | None = None
    """Cut the zip's NEXT GET after this many body bytes, then serve normally."""
    ignore_range: bool = False
    get_length: int | None = None
    """A GET's Content-Length that disagrees with the file, where set."""

    def __call__(
        self,
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Response:
        self.requests.append((method, url, dict(headers)))
        assert hosts == HOSTS
        if url not in self.files:
            return _Response(404, {}, b"")
        data = self.files[url]
        if method == "HEAD":
            return _Response(200, {"Content-Length": str(len(data))}, b"")
        start = 0
        status = 200
        reply: dict[str, str] = {}
        wanted = headers.get("Range")
        if wanted and not self.ignore_range:
            start = int(wanted.removeprefix("bytes=").removesuffix("-"))
            status = 206
            reply["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
        body = data[start:]
        reply["Content-Length"] = str(self.get_length if self.get_length is not None else len(body))
        cut = None
        if url == ZIP_URL:
            cut, self.cut_after = self.cut_after, None
        return _Response(status, reply, body, cut_after=cut)

    def gets(self, url: str = ZIP_URL) -> list[dict[str, str]]:
        return [h for (m, u, h) in self.requests if m == "GET" and u == url]


def _site(zip_bytes: bytes, version: bytes = b"1.00155") -> _Site:
    return _Site({ZIP_URL: zip_bytes, VERSION_URL: version})


# --- cache location ------------------------------------------------------------------------


def test_the_cache_lives_in_the_config_dir(_cache_in_tmp: Path) -> None:
    assert client_packs.cache_dir() == _cache_in_tmp
    assert client_packs.cache_dir() == platform.config_dir() / "client-packs"


def test_on_windows_the_cache_is_in_local_appdata_not_the_roaming_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    local = r"C:\Users\test\AppData\Local"
    monkeypatch.setenv("LOCALAPPDATA", local)

    assert client_packs.cache_dir() == Path(local) / "yulon" / "client-packs"


def test_on_windows_without_local_appdata_the_cache_falls_back_to_the_config_dir(
    monkeypatch: pytest.MonkeyPatch, _cache_in_tmp: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert client_packs.cache_dir() == _cache_in_tmp


def test_off_windows_local_appdata_is_ignored(
    monkeypatch: pytest.MonkeyPatch, _cache_in_tmp: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("LOCALAPPDATA", "/somewhere/else")

    assert client_packs.cache_dir() == _cache_in_tmp


# --- from the server's checkout ------------------------------------------------------------


def test_a_plain_checkout_file_is_proved_where_it_is_and_reports_its_version(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    patches = server / "centurion" / "patches"
    patches.mkdir(parents=True)
    data = _zip()
    (patches / "patch-Y.zip").write_bytes(data)
    (patches / "patch-Y.version").write_bytes(b"1.00148")
    pack = _checkout_pack("centurion/patches/patch-Y.zip", sha256=hashlib.sha256(data).hexdigest())

    got = fetch_checkout(pack, server)

    assert got.path == patches / "patch-Y.zip"
    assert got.sha256 == hashlib.sha256(data).hexdigest()
    assert got.version == "1.00148"
    assert not client_packs.cache_dir().exists()  # nothing copied: the file is used in place


def test_a_plain_checkout_file_with_the_wrong_checksum_is_refused_and_left_alone(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    (server / "p").mkdir(parents=True)
    (server / "p" / "patch-Y.zip").write_bytes(_zip())
    pack = _checkout_pack("p/patch-Y.zip", sha256="0" * 64)

    with pytest.raises(PackError, match="does not match its published checksum"):
        fetch_checkout(pack, server)
    assert (server / "p" / "patch-Y.zip").is_file()  # the server's own file is never deleted


def _split(data: bytes, folder: Path, name: str, count: int) -> None:
    size = -(-len(data) // count)
    for index in range(count):
        (folder / f"{name}.part{index:02d}").write_bytes(data[index * size : (index + 1) * size])


def test_parts_are_joined_in_name_order_part10_after_part09_and_md5_is_checked(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    folder = server / "centurion" / "patches"
    folder.mkdir(parents=True)
    data = _zip({"patch-Y.MPQ": bytes(range(256)) * 300})
    _split(data, folder, "patch-Y.zip", 12)  # part00..part11: part10 and part11 sort after 09
    (folder / "patch-Y.zip.partial-notes").write_bytes(b"not a part")  # not `.partNN`
    md5 = hashlib.md5(data).hexdigest()
    pack = _checkout_pack("centurion/patches/patch-Y.zip", md5=md5)

    got = fetch_checkout(pack, server)

    assert got.path == client_packs.cache_dir() / "checkout" / md5 / "patch-Y.zip"
    assert got.path.read_bytes() == data
    assert got.sha256 == hashlib.sha256(data).hexdigest()
    assert got.version is None
    assert list(got.path.parent.iterdir()) == [got.path]  # no `.joining` left behind


def test_parts_joined_in_text_order_would_not_match_so_the_order_is_proved(
    tmp_path: Path,
) -> None:
    """Guards the test above: with 12 parts, `part1x` vs `part0x` ordering matters to the md5."""
    folder = tmp_path / "f"
    folder.mkdir()
    data = _zip({"patch-Y.MPQ": bytes(range(256)) * 300})
    _split(data, folder, "patch-Y.zip", 12)
    parts = client_packs._parts_of(folder / "patch-Y.zip")
    assert [p.name[-2:] for p in parts] == [f"{i:02d}" for i in range(12)]
    shuffled = parts[:9] + [parts[10], parts[9]] + parts[11:]
    assert b"".join(p.read_bytes() for p in shuffled) != data


def test_joined_parts_with_the_wrong_md5_are_refused_and_nothing_is_kept(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    folder = server / "p"
    folder.mkdir(parents=True)
    _split(_zip(), folder, "patch-Y.zip", 3)
    pack = _checkout_pack("p/patch-Y.zip", md5="0" * 32)

    with pytest.raises(PackError, match="does not match its published checksum"):
        fetch_checkout(pack, server)
    cached = client_packs.cache_dir() / "checkout" / ("0" * 32)
    assert list(cached.iterdir()) == []


def test_a_gap_in_the_part_numbers_is_refused_before_joining_and_names_the_missing_piece(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    folder = server / "p"
    folder.mkdir(parents=True)
    for number in (1, 2, 4):
        (folder / f"patch-Y.zip.part{number:02d}").write_bytes(b"x")
    pack = _checkout_pack("p/patch-Y.zip", md5="0" * 32)

    with pytest.raises(PackError) as caught:
        fetch_checkout(pack, server)
    assert "patch-Y.zip.part03" in str(caught.value)
    assert LATEST in str(caught.value)
    assert not client_packs.cache_dir().exists()  # refused before anything was written


def test_a_duplicate_part_number_is_refused_before_joining(tmp_path: Path) -> None:
    server = tmp_path / "server"
    folder = server / "p"
    folder.mkdir(parents=True)
    (folder / "patch-Y.zip.part1").write_bytes(b"x")
    (folder / "patch-Y.zip.part01").write_bytes(b"x")
    (folder / "patch-Y.zip.part2").write_bytes(b"x")
    pack = _checkout_pack("p/patch-Y.zip", md5="0" * 32)

    with pytest.raises(PackError, match="more than one piece numbered 1") as caught:
        fetch_checkout(pack, server)
    assert LATEST in str(caught.value)
    assert not client_packs.cache_dir().exists()


# --- the checksum read from the checkout (`md5_file`, T179 Task 7) ---------------------------


MD5_FILE = "centurion/patches/patches.md5"


def _md5_pack(path: str = "centurion/patches/patch-Y.zip") -> ClientPack:
    return _checkout_pack(path, md5_file=MD5_FILE)


def _md5_lines(**zips: bytes) -> bytes:
    """`md5sum` output for `name=bytes` (`patch_Y_zip` spells `patch-Y.zip`), Centurion's shape."""
    return b"".join(
        f"{hashlib.md5(data).hexdigest()}  {_zip_name(name)}\n".encode()
        for name, data in zips.items()
    )


def _zip_name(keyword: str) -> str:
    return keyword.replace("_zip", ".zip").replace("_", "-")


def _lay_split(server: Path, data: bytes, md5s: bytes) -> Path:
    folder = server / "centurion" / "patches"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("patch-Y.zip.part*"):
        old.unlink()
    _split(data, folder, "patch-Y.zip", 3)
    (folder / "patches.md5").write_bytes(md5s)
    return folder


def test_a_checkout_pack_takes_its_md5_from_the_md5_file_at_the_checked_out_commit(
    tmp_path: Path,
) -> None:
    """Centurion's own `patches.md5` (md5sum lines, the joined zips by name) proves the join."""
    server = tmp_path / "server"
    data = _zip({"patch-Y.MPQ": bytes(range(256)) * 300})
    other = _zip({"addons/x.toc": b"## Title: x"})
    _lay_split(server, data, _md5_lines(addons_zip=other, patch_Y_zip=data))

    got = fetch_checkout(_md5_pack(), server)

    md5 = hashlib.md5(data).hexdigest()
    assert got.path == client_packs.cache_dir() / "checkout" / md5 / "patch-Y.zip"
    assert got.path.read_bytes() == data
    assert got.sha256 == hashlib.sha256(data).hexdigest()
    assert client_packs.checkout_checksum(_md5_pack(), server) == md5


def test_the_md5_file_may_mark_binary_mode_and_end_its_lines_in_crlf(tmp_path: Path) -> None:
    """`md5sum -b` writes `<md5> *<name>`; a file saved on Windows ends `\\r\\n`."""
    server = tmp_path / "server"
    data = _zip()
    md5 = hashlib.md5(data).hexdigest()
    _lay_split(server, data, f"{'0' * 32}  addons.zip\r\n{md5} *patch-Y.zip\r\n".encode())

    assert fetch_checkout(_md5_pack(), server).sha256 == hashlib.sha256(data).hexdigest()


def test_a_pack_changed_in_the_checkout_with_a_matching_md5_file_is_used_at_the_next_play(
    tmp_path: Path,
) -> None:
    """The server's makers update a pack and its line in `patches.md5` in one commit.

    Nothing in the catalog changes, and the next fetch -- Play's, or the map data's
    re-extraction -- gets the new zip under a new checksum, so the ready-to-play
    client's record (by sha256) no longer matches and the pack is installed again.
    """
    server = tmp_path / "server"
    first = _zip({"patch-Y.MPQ": b"MPQ\x1a the art base, 1.00024"})
    _lay_split(server, first, _md5_lines(patch_Y_zip=first))
    before = fetch_checkout(_md5_pack(), server)

    second = _zip({"patch-Y.MPQ": b"MPQ\x1a the art base, 1.00025, a little larger"})
    _lay_split(server, second, _md5_lines(patch_Y_zip=second))
    after = fetch_checkout(_md5_pack(), server)

    assert after.sha256 == hashlib.sha256(second).hexdigest() != before.sha256
    assert after.path.read_bytes() == second
    assert after.path.parent.name == hashlib.md5(second).hexdigest()


def test_a_zip_that_does_not_match_its_line_in_the_md5_file_is_refused(tmp_path: Path) -> None:
    """A pack changed WITHOUT its line (or a piece lost) is refused, and nothing is kept."""
    server = tmp_path / "server"
    old = _zip({"patch-Y.MPQ": b"MPQ\x1a old"})
    _lay_split(server, _zip({"patch-Y.MPQ": b"MPQ\x1a new"}), _md5_lines(patch_Y_zip=old))

    with pytest.raises(PackError, match="does not match its published checksum") as caught:
        fetch_checkout(_md5_pack(), server)
    assert LATEST in str(caught.value)
    cached = client_packs.cache_dir() / "checkout" / hashlib.md5(old).hexdigest()
    assert list(cached.iterdir()) == []


def test_a_plain_checkout_file_is_proved_against_the_md5_file_too(tmp_path: Path) -> None:
    server = tmp_path / "server"
    folder = server / "centurion" / "patches"
    folder.mkdir(parents=True)
    data = _zip({"dinput8.dll": b"MZ"})
    (folder / "client-tweaks.zip").write_bytes(data)
    (folder / "patches.md5").write_bytes(_md5_lines(client_tweaks_zip=data))
    pack = _md5_pack("centurion/patches/client-tweaks.zip")

    assert fetch_checkout(pack, server).path == folder / "client-tweaks.zip"

    (folder / "patches.md5").write_bytes(_md5_lines(client_tweaks_zip=b"another"))
    with pytest.raises(PackError, match="does not match its published checksum"):
        fetch_checkout(pack, server)
    assert (folder / "client-tweaks.zip").read_bytes() == data  # never deleted


def test_a_missing_md5_file_is_refused_naming_it_before_anything_is_joined(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    folder = _lay_split(server, _zip(), b"")
    (folder / "patches.md5").unlink()

    with pytest.raises(PackError) as caught:
        fetch_checkout(_md5_pack(), server)
    assert MD5_FILE in str(caught.value)
    assert LATEST in str(caught.value)
    assert not client_packs.cache_dir().exists()


def test_an_md5_file_without_the_packs_line_is_refused_naming_both(tmp_path: Path) -> None:
    server = tmp_path / "server"
    _lay_split(server, _zip(), _md5_lines(addons_zip=b"a", patch_Z_zip=b"z"))

    with pytest.raises(PackError) as caught:
        fetch_checkout(_md5_pack(), server)
    assert "patch-Y.zip" in str(caught.value)
    assert MD5_FILE in str(caught.value)
    assert not client_packs.cache_dir().exists()


def test_a_missing_zip_is_named_as_missing_before_its_md5_line_is_looked_for(
    tmp_path: Path,
) -> None:
    """T179 Task 7 review: a checkout that lost the zip AND its line names the zip."""
    server = tmp_path / "server"
    folder = server / "centurion" / "patches"
    folder.mkdir(parents=True)
    (folder / "patches.md5").write_bytes(_md5_lines(addons_zip=b"a"))

    with pytest.raises(PackError) as caught:
        fetch_checkout(_md5_pack(), server)
    assert "checkout has no centurion/patches/patch-Y.zip (nor its .partNN pieces)" in str(
        caught.value
    )
    assert "has no line for" not in str(caught.value)


def test_an_md5_file_naming_the_pack_twice_with_two_answers_is_refused(tmp_path: Path) -> None:
    server = tmp_path / "server"
    data = _zip()
    md5s = _md5_lines(patch_Y_zip=data) + f"{'1' * 32}  patch-Y.zip\n".encode()
    _lay_split(server, data, md5s)

    with pytest.raises(PackError, match="more than once"):
        fetch_checkout(_md5_pack(), server)


def test_a_line_for_a_file_of_a_similar_name_is_not_the_packs(tmp_path: Path) -> None:
    """`xpatch-Y.zip` and `patch-Y.zip.old` are other files: only the exact name answers."""
    server = tmp_path / "server"
    data = _zip()
    md5 = hashlib.md5(data).hexdigest()
    _lay_split(server, data, f"{md5}  xpatch-Y.zip\n{md5}  patch-Y.zip.old\n".encode())

    with pytest.raises(PackError, match="has no line for patch-Y.zip"):
        fetch_checkout(_md5_pack(), server)


def test_the_pinned_checksum_is_what_checkout_checksum_answers_without_an_md5_file(
    tmp_path: Path,
) -> None:
    assert client_packs.checkout_checksum(_checkout_pack("p/x.zip", md5="a" * 32), tmp_path) == (
        "a" * 32
    )
    assert client_packs.checkout_checksum(_checkout_pack("p/x.zip", sha256="b" * 64), tmp_path) == (
        "b" * 64
    )


def test_unpadded_part_numbers_are_joined_numerically_part2_before_part10(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    folder = server / "p"
    folder.mkdir(parents=True)
    data = _zip({"patch-Y.MPQ": bytes(range(256)) * 300})
    size = -(-len(data) // 10)
    for number in range(1, 11):
        piece = data[(number - 1) * size : number * size]
        (folder / f"patch-Y.zip.part{number}").write_bytes(piece)
    # By NAME `.part10` sorts before `.part2`; only a numeric order joins these into `data`.
    assert sorted(p.name for p in folder.iterdir())[1] == "patch-Y.zip.part10"
    pack = _checkout_pack("p/patch-Y.zip", md5=hashlib.md5(data).hexdigest())

    assert fetch_checkout(pack, server).path.read_bytes() == data


def test_a_join_mismatch_points_to_the_servers_sources_not_to_trying_again(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    folder = server / "p"
    folder.mkdir(parents=True)
    _split(_zip(), folder, "patch-Y.zip", 3)
    pack = _checkout_pack("p/patch-Y.zip", md5="0" * 32)

    with pytest.raises(PackError) as caught:
        fetch_checkout(pack, server)
    assert LATEST in str(caught.value)
    assert "Try again" not in str(caught.value)


def test_a_checkout_without_the_file_or_its_parts_names_the_path_and_the_commit(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    (server / "centurion" / "patches").mkdir(parents=True)
    pack = _checkout_pack("centurion/patches/patch-Y.zip", md5="0" * 32)

    with pytest.raises(PackError) as caught:
        fetch_checkout(pack, server)
    assert "centurion/patches/patch-Y.zip" in str(caught.value)
    assert "at the commit it is on" in str(caught.value)


# --- from the server's site ----------------------------------------------------------------


def test_a_url_pack_is_downloaded_into_entry_pack_version_and_crc_checked() -> None:
    data = _zip()
    site = _site(data, version=b"\xef\xbb\xbf 1.00155\r\n")
    seen: list[tuple[int, int]] = []

    got = fetch_url(
        _url_pack(),
        entry_id="centurion",
        allowed_hosts=HOSTS,
        opener=site,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert got.version == "1.00155"
    assert got.path == (
        client_packs.cache_dir() / "centurion" / "hd-creatures" / "1.00155" / "hd-creatures.zip"
    )
    assert got.path.read_bytes() == data
    assert got.sha256 == hashlib.sha256(data).hexdigest()
    assert seen[-1] == (len(data), len(data))
    assert list(got.path.parent.iterdir()) == [got.path]


def test_a_cached_url_pack_of_the_same_version_is_not_downloaded_again() -> None:
    site = _site(_zip())
    first = fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    site.requests.clear()

    second = fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)

    assert second == first
    assert [m for (m, u, _) in site.requests] == ["GET"]  # the version, nothing else
    assert site.gets() == []


@pytest.mark.parametrize(
    "junk",
    [
        b"../1.0",
        b"1.0/2",
        b"1.0\\2",
        b"1<2",
        b"1>2",
        b"1:2",
        b'1"2',
        b"1|2",
        b"1?2",
        b"1*2",
        b"",
        b"  \n",
        b"..",
        b"1.0.",
        b"1\x002",
        b"1" * 65,
        b"\xff\xfe",
    ],
)
def test_junk_version_text_is_refused_before_the_zip_is_asked_for(junk: bytes) -> None:
    site = _site(_zip(), version=junk)

    with pytest.raises(PackError, match="is not a version Yu'lon can use"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert [u for (_, u, _) in site.requests] == [VERSION_URL]


def test_a_host_the_entry_does_not_name_is_refused_before_any_request() -> None:
    site = _site(_zip())

    with pytest.raises(PackError, match="not a plain https address on a host"):
        fetch_url(
            _url_pack(),
            entry_id="centurion",
            allowed_hosts=frozenset({"other.example.org"}),
            opener=site,
        )
    assert site.requests == []


def test_a_cut_connection_keeps_the_part_and_the_next_try_resumes_from_its_size() -> None:
    data = _zip()
    site = _site(data)
    site.cut_after = 5000
    part = (
        client_packs.cache_dir()
        / "centurion"
        / "hd-creatures"
        / "1.00155"
        / "hd-creatures.zip.part"
    )

    with pytest.raises(PackError, match="continues from there"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert part.stat().st_size == 5000

    got = fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)

    assert site.gets()[-1] == {"Range": "bytes=5000-"}
    assert got.path.read_bytes() == data
    assert not part.exists()


def test_a_server_that_ignores_range_is_downloaded_from_the_start() -> None:
    data = _zip()
    site = _site(data)
    site.cut_after = 5000
    with pytest.raises(PackError, match="continues from there"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    site.ignore_range = True

    got = fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)

    assert got.path.read_bytes() == data  # not the first 5000 bytes twice


def test_a_get_whose_length_disagrees_with_head_is_refused_and_the_part_dropped() -> None:
    data = _zip()
    site = _site(data)
    site.get_length = len(data) + 1
    folder = client_packs.cache_dir() / "centurion" / "hd-creatures" / "1.00155"

    with pytest.raises(PackError, match="had said"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert list(folder.iterdir()) == []


def test_a_body_longer_than_head_said_is_refused_and_the_part_dropped() -> None:
    data = _zip()
    site = _site(data)
    real_head = site.__call__

    def short_head(url: str, watcher: _Deadline, **kw: Any) -> _Response:
        answer = real_head(url, watcher, **kw)
        if kw["method"] == "HEAD":
            answer.headers["Content-Length"] = str(len(data) - 10)
        else:
            answer.headers.pop("Content-Length", None)
        return answer

    folder = client_packs.cache_dir() / "centurion" / "hd-creatures" / "1.00155"
    with pytest.raises(PackError, match="is longer than the server's site said"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=short_head)
    assert list(folder.iterdir()) == []


def test_a_checksum_mismatch_keeps_no_file() -> None:
    site = _site(_zip())
    folder = client_packs.cache_dir() / "centurion" / "hd-creatures" / "1.00155"

    with pytest.raises(PackError, match="does not match its published checksum"):
        fetch_url(
            _url_pack(sha256="0" * 64), entry_id="centurion", allowed_hosts=HOSTS, opener=site
        )
    assert list(folder.iterdir()) == []


def test_a_matching_checksum_is_accepted_without_a_version_url() -> None:
    data = _zip()
    sha = hashlib.sha256(data).hexdigest()
    site = _Site({ZIP_URL: data})

    got = fetch_url(
        _url_pack(version=False, sha256=sha), entry_id="centurion", allowed_hosts=HOSTS, opener=site
    )

    assert got.version is None
    assert got.path.parent.name == sha


def test_a_zip_failing_its_crc_is_refused_when_there_is_no_checksum() -> None:
    data = bytearray(_zip({"patch-F.MPQ": b"A" * 4000}))
    data[data.index(b"A" * 100) + 50] = ord("B")  # inside the stored member's data
    site = _site(bytes(data))
    folder = client_packs.cache_dir() / "centurion" / "hd-creatures" / "1.00155"

    with pytest.raises(PackError, match="patch-F.MPQ fails its check"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert list(folder.iterdir()) == []


def test_bytes_that_are_not_a_zip_are_refused_when_there_is_no_checksum() -> None:
    site = _site(b"<html>not found</html>" * 10)

    with pytest.raises(PackError, match="is not a readable zip"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)


def test_cancel_raises_cancelled_and_keeps_the_part_for_the_next_try() -> None:
    data = _zip()
    site = _site(data)
    seen: list[int] = []
    part = (
        client_packs.cache_dir()
        / "centurion"
        / "hd-creatures"
        / "1.00155"
        / "hd-creatures.zip.part"
    )

    with pytest.raises(Cancelled, match="cancelled"):
        fetch_url(
            _url_pack(),
            entry_id="centurion",
            allowed_hosts=HOSTS,
            opener=site,
            progress=lambda done, _total: seen.append(done),
            cancelled=lambda: bool(seen),
        )
    assert isinstance(Cancelled("x"), PackError)
    assert part.stat().st_size == seen[-1] == 4096

    fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert site.gets()[-1] == {"Range": "bytes=4096-"}


def test_too_little_free_space_is_refused_before_downloading_naming_the_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _zip()
    site = _site(data)
    monkeypatch.setattr(client_packs, "_free_bytes", lambda _folder: len(data) - 1)

    with pytest.raises(PackError) as caught:
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert f"needs {len(data)} bytes of free space" in str(caught.value)
    assert site.gets() == []


def test_free_space_counts_only_what_a_resume_still_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _zip()
    site = _site(data)
    site.cut_after = 5000
    with pytest.raises(PackError, match="continues from there"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    monkeypatch.setattr(client_packs, "_free_bytes", lambda _folder: len(data) - 5000)

    got = fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)

    assert got.path.read_bytes() == data


def test_a_404_says_the_pack_is_not_available() -> None:
    site = _Site({VERSION_URL: b"1.0"})

    with pytest.raises(
        PackError, match=r"not available from the server's site right now \(HTTP 404"
    ):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)


# --- OS errors become PackError -----------------------------------------------------------


def test_a_full_disk_mid_download_is_a_pack_error_and_keeps_the_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _zip()
    real_open = Path.open

    class _Full:
        def __init__(self, handle: Any) -> None:
            self.handle = handle
            self.written = 0

        def __enter__(self) -> _Full:
            return self

        def __exit__(self, *exc: object) -> None:
            self.handle.close()

        def write(self, chunk: bytes) -> int:
            if self.written:
                raise OSError(errno.ENOSPC, "No space left on device")
            self.written += self.handle.write(chunk[:100])
            return len(chunk)

    def opening(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, mode, *args, **kwargs)
        return _Full(handle) if self.name.endswith(".part") else handle

    monkeypatch.setattr(Path, "open", opening)
    site = _site(data)
    pack = _url_pack()

    with pytest.raises(PackError, match="No space left") as caught:
        fetch_url(pack, entry_id="centurion", allowed_hosts=HOSTS, opener=site)
    assert "press Play again" in str(caught.value)
    parts = list(client_packs.cache_dir().rglob("*.part"))
    assert len(parts) == 1 and parts[0].stat().st_size == 100


def test_a_cache_folder_that_cannot_be_made_is_a_pack_error_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(self: Path, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(Path, "mkdir", refuse)

    with pytest.raises(PackError, match="Permission denied") as caught:
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=_site(_zip()))
    assert "1.00155" in str(caught.value)  # the folder it could not make


def test_a_checkout_cache_folder_that_cannot_be_made_is_a_pack_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    (server / "p").mkdir(parents=True)
    _split(_zip(), server / "p", "patch-Y.zip", 3)

    def refuse(self: Path, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(PackError, match="Permission denied"):
        fetch_checkout(_checkout_pack("p/patch-Y.zip", md5="0" * 32), server)


@pytest.mark.parametrize("name", ["NUL", "con", "COM1", "lpt9", "AUX.txt", "prn.1"])
def test_a_windows_device_name_is_not_a_version(name: str) -> None:
    with pytest.raises(PackError, match="not a version Yu'lon can use"):
        fetch_url(
            _url_pack(),
            entry_id="centurion",
            allowed_hosts=HOSTS,
            opener=_site(_zip(), version=name.encode()),
        )


def test_a_name_that_only_starts_like_a_device_is_a_version() -> None:
    got = fetch_url(
        _url_pack(),
        entry_id="centurion",
        allowed_hosts=HOSTS,
        opener=_site(_zip(), version=b"COM10"),
    )
    assert got.version == "COM10"


# --- 404 is "unavailable" ------------------------------------------------------------------


class _Gone:
    """An opener answering one URL with an HTTPError and everything else from a site."""

    def __init__(self, site: _Site, url: str, code: int) -> None:
        self.site, self.url, self.code = site, url, code

    def __call__(self, url: str, watcher: _Deadline, **kwargs: Any) -> Any:
        if url == self.url:
            raise urllib.error.HTTPError(url, self.code, "gone", {}, None)  # type: ignore[arg-type]
        return self.site(url, watcher, **kwargs)


@pytest.mark.parametrize("code", [404, 410])
@pytest.mark.parametrize("gone", [ZIP_URL, VERSION_URL])
def test_a_pack_whose_url_is_gone_is_unavailable(code: int, gone: str) -> None:
    opener = _Gone(_site(_zip()), gone, code)

    with pytest.raises(PackUnavailable, match=f"HTTP {code}"):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=opener)


def test_a_404_status_answer_is_unavailable_too() -> None:
    site = _Site({VERSION_URL: b"1.0"})  # the zip is absent: the fake answers a 404 status

    with pytest.raises(PackUnavailable):
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=site)


def test_a_server_error_is_not_unavailable() -> None:
    opener = _Gone(_site(_zip()), VERSION_URL, 503)

    with pytest.raises(PackError) as caught:
        fetch_url(_url_pack(), entry_id="centurion", allowed_hosts=HOSTS, opener=opener)
    assert not isinstance(caught.value, PackUnavailable)


# --- the real opener's redirect rule -------------------------------------------------------


def _redirect_handler(hosts: frozenset[str] | None) -> urllib.request.HTTPRedirectHandler:
    opener = fetch._https_only_opener(_Deadline(1.0), platform.verify_context(), hosts=hosts)
    return next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))


def test_a_redirect_to_a_host_the_entry_does_not_name_is_not_followed() -> None:
    handler = _redirect_handler(HOSTS)
    request = urllib.request.Request(ZIP_URL, method="GET")

    with pytest.raises(fetch.UpdateError, match="does not name"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/x.zip")


@pytest.mark.parametrize("target", [f"https://{HOST}:8443/x.zip", f"https://user:pw@{HOST}/x.zip"])
def test_a_redirect_to_a_listed_host_with_a_port_or_userinfo_is_not_followed(target: str) -> None:
    handler = _redirect_handler(HOSTS)
    request = urllib.request.Request(ZIP_URL, method="GET")

    with pytest.raises(fetch.UpdateError, match="does not name"):
        handler.redirect_request(request, None, 302, "Found", {}, target)


def test_a_redirected_head_stays_a_head_and_a_range_survives() -> None:
    handler = _redirect_handler(HOSTS)
    request = urllib.request.Request(ZIP_URL, method="HEAD", headers={"Range": "bytes=10-"})

    followed = handler.redirect_request(
        request, None, 302, "Found", {}, f"https://{HOST}/mirror/hd-creatures.zip"
    )

    assert followed is not None
    assert followed.get_method() == "HEAD"
    assert followed.get_header("Range") == "bytes=10-"


def test_the_self_updater_still_follows_a_redirect_to_any_https_host() -> None:
    handler = _redirect_handler(None)
    request = urllib.request.Request(ZIP_URL, method="GET")

    followed = handler.redirect_request(
        request, None, 302, "Found", {}, "https://objects.example/x"
    )

    assert followed is not None and followed.get_method() == "GET"


# -- Installing, recording and removing packs in a ready-to-play client (T181 b/c, Task 3) ------

_REAL_CHMOD = os.chmod
GAME = "wow-wotlk"
STOCK_X = b"stock patch-X from the player's own client"
NEW_Y = b"MPQ\x1a" + b"centurion patch-Y " * 50


def _original(root: Path) -> Path:
    c = root / "WoW"
    (c / "Data" / "enUS").mkdir(parents=True)
    (c / "Data" / "common.MPQ").write_bytes(b"mpq" * 100)
    (c / "Data" / "patch-X.MPQ").write_bytes(STOCK_X)
    (c / "Data" / "enUS" / "realmlist.wtf").write_text("set realmlist logon.example\n")
    (c / "Wow.exe").write_bytes(b"MZexe")
    (c / "WTF").mkdir()
    (c / "WTF" / "Config.wtf").write_bytes(b'SET locale "enUS"\n')
    (c / "Interface" / "AddOns" / "Blizzard_Own").mkdir(parents=True)
    (c / "Interface" / "AddOns" / "Blizzard_Own" / "x.toc").write_text("## own\n")
    return c


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


class _Rig:
    """A real ready-to-play client made by step (a) from a fake original, plus its watcher."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.original = _original(root)
        self.server = root / "srv"
        self.play = root / "WoW (Yu'lon)"
        play_client.create(
            self.original,
            self.play,
            game=GAME,
            server_dir=self.server,
            allow_full_copy=False,
            reflink=lambda src, dst: False,
        )
        self.before = _snapshot(self.original)

    def untouched(self) -> None:
        """The player's own client holds exactly the bytes it held when the rig was made."""
        assert _snapshot(self.original) == self.before, "the original client was changed"

    def fetched(self, members: Mapping[str, bytes], name: str = "pack.zip") -> client_packs.Fetched:
        path = self.root / "zips" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(_zip(members))
        return client_packs.Fetched(path, "1.00155", hashlib.sha256(path.read_bytes()).hexdigest())

    def install(self, pack: ClientPack, fetched: client_packs.Fetched, **kw: Any) -> dict[str, Any]:
        return client_packs.install(
            self.play, pack, fetched, game=GAME, server_dir=self.server, **kw
        )

    def remove(self, entry: dict[str, Any], pack: ClientPack | None) -> tuple[str, ...]:
        return client_packs.remove(self.play, entry, pack, game=GAME, server_dir=self.server)


@pytest.fixture
def rig(tmp_path: Path) -> _Rig:
    return _Rig(tmp_path)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pack_of(install: list[dict[str, str]], **extra: Any) -> ClientPack:
    return ClientPack.model_validate(
        {
            "id": "p",
            "label": "Patch pack",
            "source": {"kind": "checkout", "path": "centurion/patches/p.zip"},
            "md5": "0" * 32,
            "install": install,
            **extra,
        }
    )


WORLD = _pack_of([{"member": "patch-Y.MPQ", "to": "Data/patch-X.MPQ"}])
ADDONS = _pack_of([{"member": "*", "to_dir": "Interface/AddOns"}], id="addons")
ADDON_FILES = {"Nova/Nova.toc": b"## Title: Nova\n", "Nova/Nova.lua": b"print('hi')\n"}


def test_a_named_member_is_installed_under_its_new_name_and_recorded_with_its_hash(
    rig: _Rig,
) -> None:
    entry = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))

    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == NEW_Y
    assert not (rig.play / "Data" / "patch-Y.MPQ").exists()
    assert entry == {
        "version": "1.00155",
        "sha256": rig.fetched({"patch-Y.MPQ": NEW_Y}).sha256,
        "files": {"Data/patch-X.MPQ": _sha(NEW_Y)},
    }
    rig.untouched()


def test_the_fake_zip_is_the_same_bytes_whenever_it_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T648: zipfile stamps entries with the clock (2 s steps), so the same members made across a
    tick hashed differently and the test above compared two different zips."""
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    first = _zip({"a": b"1"})
    monkeypatch.setattr(time, "time", lambda: 1_700_000_010.0)

    assert _zip({"a": b"1"}) == first


def test_a_star_member_unpacks_the_whole_zip_under_its_folder(rig: _Rig) -> None:
    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))

    addons = rig.play / "Interface" / "AddOns"
    assert (addons / "Nova" / "Nova.toc").read_bytes() == ADDON_FILES["Nova/Nova.toc"]
    assert (addons / "Nova" / "Nova.lua").read_bytes() == ADDON_FILES["Nova/Nova.lua"]
    assert entry["files"] == {
        "Interface/AddOns/Nova/Nova.toc": _sha(ADDON_FILES["Nova/Nova.toc"]),
        "Interface/AddOns/Nova/Nova.lua": _sha(ADDON_FILES["Nova/Nova.lua"]),
    }
    assert (addons / "Blizzard_Own" / "x.toc").exists(), "the client's own addons stay"
    rig.untouched()


def test_an_mpq_is_hard_linked_from_an_extracted_copy_in_the_cache(rig: _Rig) -> None:
    rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))

    target = rig.play / "Data" / "patch-X.MPQ"
    assert target.stat().st_nlink == 2, "linked with the cache's extracted copy"
    rig.untouched()


def test_an_mpq_is_copied_when_the_cache_is_on_another_volume(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cross_device(src: object, dst: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", cross_device)

    rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))

    target = rig.play / "Data" / "patch-X.MPQ"
    assert target.read_bytes() == NEW_Y
    assert target.stat().st_nlink == 1, "a copy, not a link"
    rig.untouched()


def test_an_install_over_a_file_hard_linked_to_the_original_never_writes_through_it(
    rig: _Rig,
) -> None:
    """The ready-to-play client's patch-X.MPQ IS the player's own file (one inode)."""
    target = rig.play / "Data" / "patch-X.MPQ"
    assert target.stat().st_nlink > 1, "the fixture must start as a shared file"

    rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))

    assert target.read_bytes() == NEW_Y
    assert (rig.original / "Data" / "patch-X.MPQ").read_bytes() == STOCK_X
    rig.untouched()


def test_a_non_mpq_install_over_a_shared_file_never_writes_through_it(rig: _Rig) -> None:
    pack = _pack_of([{"member": "tweaks.dll", "to": "Wow.exe"}], id="dll")
    # Wow.exe is a copy in step (a); make it a hard link to prove the rule on its own.
    (rig.play / "Wow.exe").unlink()
    os.link(rig.original / "Wow.exe", rig.play / "Wow.exe")

    rig.install(pack, rig.fetched({"tweaks.dll": b"MZ new"}))

    assert (rig.play / "Wow.exe").read_bytes() == b"MZ new"
    rig.untouched()


def test_a_changed_pack_replaces_its_files_and_the_record_follows(rig: _Rig) -> None:
    first = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    newer = NEW_Y + b" version 2"

    second = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": newer}, name="v2.zip"))

    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == newer
    assert second["files"] == {"Data/patch-X.MPQ": _sha(newer)} != first["files"]
    assert not list((rig.play / "Data").glob("*.yulon-pack-tmp")), "no temporary file left"
    rig.untouched()


@pytest.mark.parametrize(
    "name",
    ["../../evil.dll", "../evil.dll", "/abs/evil.dll", "C:evil.dll", "sub\\..\\evil.dll"],
)
def test_a_member_that_would_leave_the_client_is_refused_and_nothing_is_written(
    rig: _Rig, name: str
) -> None:
    fetched = rig.fetched({"Nova/Nova.toc": b"ok", name: b"MZ evil"})
    files_before = _snapshot(rig.play)

    with pytest.raises(PackError, match="would leave the client"):
        rig.install(ADDONS, fetched)

    assert _snapshot(rig.play) == files_before, "not even the harmless first member"
    assert not (rig.root / "evil.dll").exists()
    assert not (rig.root.parent / "evil.dll").exists()
    rig.untouched()


def test_a_zip_cannot_forge_the_marker_or_the_record(rig: _Rig) -> None:
    pack = _pack_of([{"member": "*", "to_dir": "."}], id="root")
    marker = (rig.play / play_client.MARKER).read_bytes()

    with pytest.raises(PackError, match="Yu'lon's own"):
        rig.install(pack, rig.fetched({play_client.MARKER: b"{}"}))

    assert (rig.play / play_client.MARKER).read_bytes() == marker


def test_a_symlink_member_is_refused(rig: _Rig) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("Nova/evil")
        info.create_system = 3
        info.external_attr = (0o120777) << 16
        archive.writestr(info, "/etc/passwd")
    path = rig.root / "link.zip"
    path.write_bytes(buffer.getvalue())

    with pytest.raises(PackError, match="link"):
        rig.install(ADDONS, client_packs.Fetched(path, None, _sha(path.read_bytes())))

    assert not (rig.play / "Interface" / "AddOns" / "Nova").exists()
    rig.untouched()


def test_a_missing_named_member_is_refused_naming_it(rig: _Rig) -> None:
    with pytest.raises(PackError, match=r"patch-Y\.MPQ"):
        rig.install(WORLD, rig.fetched({"other.MPQ": b"x"}))

    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == STOCK_X
    rig.untouched()


def test_a_target_folder_that_is_a_symlink_is_refused_and_the_other_folder_untouched(
    rig: _Rig,
) -> None:
    """`Interface/AddOns` linked to the original's: writing there would be writing the original."""
    addons = rig.play / "Interface" / "AddOns"
    shutil.rmtree(addons)
    addons.symlink_to(rig.original / "Interface" / "AddOns", target_is_directory=True)

    with pytest.raises(PackError, match="link"):
        rig.install(ADDONS, rig.fetched(ADDON_FILES))

    rig.untouched()


def test_a_target_folder_that_looks_like_a_junction_is_refused(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = play_client._is_link
    junction = rig.play / "Interface"
    (junction / "AddOns").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(play_client, "_is_link", lambda p: Path(p) == junction or real(p))

    with pytest.raises(PackError, match="link"):
        rig.install(ADDONS, rig.fetched(ADDON_FILES))

    assert not (junction / "AddOns" / "Nova").exists()


def test_a_damaged_member_leaves_even_the_members_before_it_uninstalled(rig: _Rig) -> None:
    fetched = rig.fetched({"A/first.txt": b"first file", "B/second.txt": b"second file" * 20})
    raw = bytearray(fetched.path.read_bytes())
    at = bytes(raw).index(b"second file")
    raw[at] ^= 0xFF  # a stored member: its CRC no longer matches
    fetched.path.write_bytes(bytes(raw))
    files_before = _snapshot(rig.play)

    with pytest.raises(PackError, match="damaged"):
        rig.install(ADDONS, fetched)

    assert _snapshot(rig.play) == files_before
    rig.untouched()


def test_a_failed_write_is_a_pack_error_and_leaves_no_temporary_file(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device", str(rig.play))

    monkeypatch.setattr(client_packs, "_copy", full)  # every byte copy of a pack (T303)

    with pytest.raises(PackError, match="No space left"):
        rig.install(ADDONS, rig.fetched(ADDON_FILES))

    assert not list(rig.play.rglob("*.yulon-pack-tmp"))
    rig.untouched()


def test_an_install_refuses_a_folder_without_the_marker(rig: _Rig) -> None:
    (rig.play / play_client.MARKER).unlink()

    with pytest.raises(PackError, match="not a ready-to-play client"):
        rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))

    rig.untouched()


def test_an_install_refuses_another_servers_client(rig: _Rig) -> None:
    with pytest.raises(PackError, match="another server"):
        client_packs.install(
            rig.play,
            WORLD,
            rig.fetched({"patch-Y.MPQ": NEW_Y}),
            game=GAME,
            server_dir=rig.root / "other",
        )

    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == STOCK_X
    rig.untouched()


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
def test_read_only_targets_are_replaced_without_clearing_any_flag(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses to replace a read-only file; the swap renames it aside instead."""
    real = os.replace
    refused: list[str] = []

    def replace(src: Any, dst: Any) -> None:
        if os.path.isfile(dst) and not os.lstat(dst).st_mode & 0o200:
            refused.append(os.fspath(dst))
            raise PermissionError(errno.EACCES, "Access is denied", os.fspath(dst))
        real(src, dst)

    monkeypatch.setattr(client_packs.os, "replace", replace)
    pack = _pack_of([{"member": "t.dll", "to": "Own.dll"}], id="own")
    own = rig.play / "Own.dll"
    own.write_bytes(b"old")
    os.chmod(own, 0o444)
    rig.install(pack, rig.fetched({"t.dll": b"new"}))
    assert own.read_bytes() == b"new"

    shared_original = rig.original / "Data" / "patch-X.MPQ"
    os.chmod(shared_original, 0o444)  # one inode: the player's file too
    rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == NEW_Y
    assert shared_original.stat().st_mode & 0o777 == 0o444, "the player's flag was not cleared"
    assert refused == [], "no replace was ever aimed at a read-only file"
    os.chmod(shared_original, 0o644)
    rig.untouched()


# -- the record --------------------------------------------------------------------------------


def _record(**packs: Any) -> Any:
    return client_packs.PackRecord(
        packs=dict(packs), exe=None, choices={"packs": {}, "exe_options": {}}
    )


def _entry(**files: bytes) -> dict[str, Any]:
    return {"version": "1", "sha256": "0" * 64, "files": {k: _sha(v) for k, v in files.items()}}


def test_a_record_round_trips_and_a_missing_one_reads_as_empty(rig: _Rig) -> None:
    empty = client_packs.read_record(rig.play)
    assert (empty.packs, empty.exe, empty.choices) == (
        {},
        None,
        {"packs": {}, "exe_options": {}},
    )
    record = client_packs.PackRecord(
        packs={"p": _entry(**{"Data/patch-X.MPQ": NEW_Y})},
        exe={"stock_sha256": "a" * 64},
        choices={"packs": {"hd": True}, "exe_options": {"borderless": False}},
    )

    client_packs.write_record(rig.play, record, game=GAME, server_dir=rig.server)

    assert client_packs.read_record(rig.play) == record
    assert not list(rig.play.glob("*.tmp")) and not list(rig.play.glob("*.yulon-tmp"))
    rig.untouched()


def test_config_seeded_round_trips_and_an_old_record_reads_false(rig: _Rig) -> None:
    assert client_packs.read_record(rig.play).config_seeded is False
    record = client_packs.PackRecord({}, None, {"packs": {}, "exe_options": {}}, config_seeded=True)

    client_packs.write_record(rig.play, record, game=GAME, server_dir=rig.server)

    assert client_packs.read_record(rig.play).config_seeded is True
    # A record written before the field existed (and one where it is not a bool).
    for raw in (b'{"version": 1, "packs": {}}', b'{"packs": {}, "config_seeded": "yes"}'):
        (rig.play / client_packs.RECORD).write_bytes(raw)
        assert client_packs.read_record(rig.play).config_seeded is False


@pytest.mark.parametrize(
    "junk", [b"", b"\xff\xfe", b"[1, 2]", b'{"packs": 5}', b'{"packs": {"p": 3}}']
)
def test_a_record_that_cannot_be_read_reads_as_empty(rig: _Rig, junk: bytes) -> None:
    (rig.play / client_packs.RECORD).write_bytes(junk)

    assert client_packs.read_record(rig.play).packs == {}
    assert client_packs.pack_files(rig.play) == frozenset()


def test_writing_a_record_refuses_a_folder_without_the_marker(rig: _Rig) -> None:
    (rig.play / play_client.MARKER).unlink()

    with pytest.raises(PackError, match="not a ready-to-play client"):
        client_packs.write_record(rig.play, _record(), game=GAME, server_dir=rig.server)

    assert not (rig.play / client_packs.RECORD).exists()


@pytest.mark.parametrize(
    ("game", "server", "fragment"),
    [(GAME, "other", "another server"), ("wow-tbc", "srv", "another game")],
)
def test_writing_a_record_refuses_another_servers_or_games_client(
    rig: _Rig, game: str, server: str, fragment: str
) -> None:
    with pytest.raises(PackError, match=fragment):
        client_packs.write_record(rig.play, _record(), game=game, server_dir=rig.root / server)

    assert not (rig.play / client_packs.RECORD).exists()


def test_writing_a_record_refuses_a_folder_that_is_a_link(rig: _Rig) -> None:
    link = rig.root / "link"
    link.symlink_to(rig.play, target_is_directory=True)

    with pytest.raises(PackError, match="link"):
        client_packs.write_record(link, _record(), game=GAME, server_dir=rig.server)

    assert not (rig.play / client_packs.RECORD).exists()


def test_a_record_leftover_that_is_a_link_is_never_written_through(rig: _Rig) -> None:
    tmp = rig.play / f"{client_packs.RECORD}.{os.getpid()}.yulon-tmp"
    tmp.symlink_to(rig.original / "Wow.exe")

    client_packs.write_record(rig.play, _record(), game=GAME, server_dir=rig.server)

    rig.untouched()
    assert client_packs.read_record(rig.play).packs == {}


def test_a_failed_record_write_is_a_pack_error_and_keeps_the_old_record(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_packs.write_record(rig.play, _record(p=_entry()), game=GAME, server_dir=rig.server)

    def refuse(src: object, dst: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(client_packs.os, "replace", refuse)
    with pytest.raises(PackError, match="No space left"):
        client_packs.write_record(rig.play, _record(), game=GAME, server_dir=rig.server)
    monkeypatch.undo()

    assert set(client_packs.read_record(rig.play).packs) == {"p"}
    assert not list(rig.play.glob("*.yulon-tmp"))


def test_pack_files_lists_every_recorded_file_and_ignores_unsafe_names(rig: _Rig) -> None:
    record = _record(
        p=_entry(**{"Data/patch-X.MPQ": b"a", "Interface/AddOns/Nova/Nova.toc": b"b"}),
        q={"version": "1", "sha256": "0" * 64, "files": {"../escape.dll": "0" * 64}},
    )
    client_packs.write_record(rig.play, record, game=GAME, server_dir=rig.server)

    assert client_packs.pack_files(rig.play) == frozenset(
        {Path("Data/patch-X.MPQ"), Path("Interface/AddOns/Nova/Nova.toc")}
    )


def _client_with(*packs: ClientPack) -> Any:
    return types.SimpleNamespace(packs=packs)


def test_wanted_is_every_required_pack_and_the_optional_ones_switched_on(rig: _Rig) -> None:
    hd_on = _pack_of(
        [{"member": "a.MPQ", "to": "Data/patch-A.MPQ"}], id="hd-on", optional=True, default=True
    )
    hd_off = _pack_of([{"member": "b.MPQ", "to": "Data/patch-B.MPQ"}], id="hd-off", optional=True)
    client = _client_with(WORLD, hd_on, hd_off)

    assert [p.id for p in client_packs.wanted(client, {})] == ["p", "hd-on"]
    chosen = {"packs": {"hd-on": False, "hd-off": True}}
    assert [p.id for p in client_packs.wanted(client, chosen)] == ["p", "hd-off"]
    assert [p.id for p in client_packs.wanted(client, {"packs": {"p": False}})] == ["p", "hd-on"]


# -- removing ----------------------------------------------------------------------------------


def test_remove_deletes_only_the_files_that_still_match_and_reports_a_hand_edited_one(
    rig: _Rig,
) -> None:
    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))
    toc = rig.play / "Interface" / "AddOns" / "Nova" / "Nova.toc"
    lua = rig.play / "Interface" / "AddOns" / "Nova" / "Nova.lua"
    lua.write_bytes(b"print('the player edited this')\n")

    left = rig.remove(entry, ADDONS)

    assert not toc.exists()
    assert lua.read_bytes() == b"print('the player edited this')\n"
    assert left == ("Interface/AddOns/Nova/Nova.lua",)
    assert (rig.play / "Interface" / "AddOns" / "Blizzard_Own").parent.is_dir()
    rig.untouched()


def test_remove_takes_away_the_folders_the_pack_made_but_never_a_toplevel_one(rig: _Rig) -> None:
    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))

    left = rig.remove(entry, ADDONS)

    assert left == ()
    assert not (rig.play / "Interface" / "AddOns" / "Nova").exists()
    assert (rig.play / "Interface" / "AddOns").is_dir() and (rig.play / "Data").is_dir()
    rig.untouched()


def test_removing_a_pack_is_a_line_in_the_log(rig: _Rig, caplog: pytest.LogCaptureFixture) -> None:
    """T211 2: switching an HD pack off left no line in yulon.log."""
    import logging

    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))

    with caplog.at_level(logging.INFO, logger="yulon"):
        rig.remove(entry, ADDONS)

    said = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("removed" in line and ADDONS.id in line for line in said), said


def test_remove_also_deletes_the_remove_when_off_files_whoever_put_them_there(rig: _Rig) -> None:
    pack = _pack_of(
        [{"member": "t.MPQ", "to": "Data/patch-T.MPQ"}], remove_when_off=["Data/patch-Y.MPQ"]
    )
    entry = rig.install(pack, rig.fetched({"t.MPQ": b"t"}))
    other = rig.play / "Data" / "patch-Y.MPQ"
    other.write_bytes(b"put there by somebody else")

    rig.remove(entry, pack)

    assert not other.exists() and not (rig.play / "Data" / "patch-T.MPQ").exists()
    rig.untouched()


def test_remove_for_a_pack_gone_from_the_catalog_removes_what_it_recorded(rig: _Rig) -> None:
    entry = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))

    rig.remove(entry, None)

    assert not (rig.play / "Data" / "patch-X.MPQ").exists()
    rig.untouched()


def test_remove_of_a_recorded_name_that_is_a_hard_link_to_the_original_leaves_the_original(
    rig: _Rig,
) -> None:
    """A tampered record naming a shared file with the original's own hash: the NAME goes."""
    entry = {"version": "1", "sha256": "0" * 64, "files": {"Data/common.MPQ": _sha(b"mpq" * 100)}}

    rig.remove(entry, None)

    rig.untouched()


@pytest.mark.parametrize("rel", ["../outside.txt", "/abs.txt", "C:evil.txt", "a\\b.txt"])
def test_remove_refuses_a_record_that_names_a_path_outside_the_client(rig: _Rig, rel: str) -> None:
    outside = rig.root / "outside.txt"
    outside.write_bytes(b"precious")
    entry = {"version": "1", "sha256": "0" * 64, "files": {rel: _sha(b"precious")}}

    with pytest.raises(PackError, match="outside"):
        rig.remove(entry, None)

    assert outside.read_bytes() == b"precious"


def test_remove_does_not_go_through_a_linked_folder(rig: _Rig) -> None:
    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))
    addons = rig.play / "Interface" / "AddOns"
    shutil.rmtree(addons)
    addons.symlink_to(rig.original / "Interface" / "AddOns", target_is_directory=True)
    (rig.original / "Interface" / "AddOns" / "Nova").mkdir()
    (rig.original / "Interface" / "AddOns" / "Nova" / "Nova.toc").write_bytes(
        ADDON_FILES["Nova/Nova.toc"]
    )
    before = _snapshot(rig.original)

    with pytest.raises(PackError, match="link"):
        rig.remove(entry, ADDONS)

    assert _snapshot(rig.original) == before


def test_remove_refuses_a_folder_without_the_marker(rig: _Rig) -> None:
    entry = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    (rig.play / play_client.MARKER).unlink()

    with pytest.raises(PackError, match="not a ready-to-play client"):
        rig.remove(entry, WORLD)

    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == NEW_Y


# -- step (a) leaves pack files alone ----------------------------------------------------------


def _record_installed(rig: _Rig, pack: ClientPack, entry: dict[str, Any]) -> None:
    client_packs.write_record(
        rig.play, _record(**{pack.id: entry}), game=GAME, server_dir=rig.server
    )


def test_stale_and_refresh_leave_a_recorded_pack_file_alone(rig: _Rig) -> None:
    """The pack's patch-X.MPQ differs from the original's: without the record it is stale."""
    entry = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y + b"-longer"}))
    rel = Path("Data/patch-X.MPQ")
    assert play_client.stale(rig.play, rig.original) == (rel,), "the fixture must read as stale"
    assert play_client.refresh(
        rig.play,
        rig.original,
        game=GAME,
        server_dir=rig.server,
        exe_patch=None,
        catalog_always={},
    ) == (rel,), "and be refreshed"
    rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y + b"-longer"}, name="again.zip"))
    _record_installed(rig, WORLD, entry)
    # the player's patcher rewrote the original's archive: now the original differs
    (rig.original / "Data" / "patch-X.MPQ").write_bytes(b"the original grew a new patch-X")
    before = _snapshot(rig.original)

    assert play_client.stale(rig.play, rig.original) == ()
    assert (
        play_client.refresh(
            rig.play,
            rig.original,
            game=GAME,
            server_dir=rig.server,
            exe_patch=None,
            catalog_always={},
        )
        == ()
    )
    assert (rig.play / rel).read_bytes() == NEW_Y + b"-longer"
    assert _snapshot(rig.original) == before


def test_left_out_archives_does_not_name_an_original_archive_a_pack_installed_by_that_name(
    rig: _Rig,
) -> None:
    entry = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    (rig.play / "Data" / "patch-X.MPQ").unlink()  # say it is momentarily absent
    assert play_client.left_out_archives(rig.play, rig.original) == (
        Path("Data/patch-X.MPQ"),
    ), "the fixture must name it without a record"
    _record_installed(rig, WORLD, entry)

    assert play_client.left_out_archives(rig.play, rig.original) == ()


# -- the cache of old versions -----------------------------------------------------------------


def _version_folder(entry: str, pack: str, version: str) -> Path:
    folder = client_packs.cache_dir() / entry / pack / version
    folder.mkdir(parents=True)
    (folder / "pack.zip").write_bytes(b"zip " + version.encode())
    (folder / "pack.zip.part").write_bytes(b"half")
    return folder


def test_prune_cache_removes_older_versions_and_keeps_the_current_one() -> None:
    old = _version_folder("centurion", "hd", "1.00100")
    older = _version_folder("centurion", "hd", "1.00090")
    current = _version_folder("centurion", "hd", "1.00155")
    sibling = _version_folder("centurion", "hd-misc", "1.00100")
    other_server = _version_folder("other", "hd", "1.00100")

    client_packs.prune_cache("centurion", "hd", "1.00155")

    assert not old.exists() and not older.exists()
    assert (current / "pack.zip").read_bytes() == b"zip 1.00155"
    assert (current / "pack.zip.part").exists(), "the current version's own .part is its resume"
    assert sibling.exists() and other_server.exists()


def test_prune_cache_never_follows_a_link_out_of_the_cache(tmp_path: Path) -> None:
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_bytes(b"mine")
    folder = client_packs.cache_dir() / "centurion" / "hd"
    folder.mkdir(parents=True)
    (folder / "1.0").symlink_to(precious, target_is_directory=True)

    client_packs.prune_cache("centurion", "hd", "1.1")

    assert (precious / "keep.txt").read_bytes() == b"mine"
    assert not (folder / "1.0").exists() and not (folder / "1.0").is_symlink()


@pytest.mark.parametrize("entry_id,pack_id", [("..", "hd"), ("centurion", "../x"), ("a/b", "hd")])
def test_prune_cache_refuses_a_name_that_is_not_one_folder(
    tmp_path: Path, entry_id: str, pack_id: str
) -> None:
    victim = client_packs.cache_dir().parent / "x"
    victim.mkdir(parents=True)

    client_packs.prune_cache(entry_id, pack_id, "1")

    assert victim.is_dir()


def test_an_install_from_a_new_url_version_prunes_the_old_versions_of_that_pack(
    rig: _Rig,
) -> None:
    site_old = _version_folder("centurion", "p", "1.00100")
    folder = _version_folder("centurion", "p", "1.00155")
    zip_path = folder / "pack.zip"
    zip_path.write_bytes(_zip({"patch-Y.MPQ": NEW_Y}))
    fetched = client_packs.Fetched(zip_path, "1.00155", _sha(zip_path.read_bytes()))

    rig.install(WORLD, fetched)

    assert not site_old.exists()
    assert zip_path.is_file()


def test_an_install_does_not_prune_what_a_failed_install_may_still_need(rig: _Rig) -> None:
    old = _version_folder("centurion", "p", "1.00100")
    folder = _version_folder("centurion", "p", "1.00155")
    zip_path = folder / "pack.zip"
    zip_path.write_bytes(_zip({"wrong-name.MPQ": b"x"}))
    fetched = client_packs.Fetched(zip_path, "1.00155", _sha(zip_path.read_bytes()))

    with pytest.raises(PackError):
        rig.install(WORLD, fetched)

    assert old.exists(), "the older version is the only one that installs, until the new one does"


# -- fix round 1 (review of Task 3) ----------------------------------------------------------

V1 = {"Nova/Nova.toc": b"## v1\n", "Nova/Nova.lua": b"v1 lua\n", "Old/Old.toc": b"## old\n"}
ADDONS_DIR = Path("Interface") / "AddOns"


def _addon(rig: _Rig, rel: str) -> Path:
    return rig.play / ADDONS_DIR / rel


def test_a_reinstall_removes_the_files_the_new_version_no_longer_has(rig: _Rig) -> None:
    first = rig.install(ADDONS, rig.fetched(V1))
    v2 = {"Nova/Nova.toc": b"## v2\n", "Old/Old.toc": b"## old\n"}

    second = rig.install(ADDONS, rig.fetched(v2, name="v2.zip"), previous=first)

    assert not _addon(rig, "Nova/Nova.lua").exists(), "never old and new mixed"
    assert _addon(rig, "Nova/Nova.toc").read_bytes() == b"## v2\n"
    assert set(second["files"]) == {
        "Interface/AddOns/Nova/Nova.toc",
        "Interface/AddOns/Old/Old.toc",
    }
    assert "left_behind" not in second
    rig.untouched()


def test_a_reinstall_that_drops_a_whole_addon_removes_its_folder_but_not_addons(
    rig: _Rig,
) -> None:
    first = rig.install(ADDONS, rig.fetched(V1))

    rig.install(ADDONS, rig.fetched({"Nova/Nova.toc": b"## v2\n"}, name="v2.zip"), previous=first)

    assert not _addon(rig, "Old").exists()
    assert (rig.play / ADDONS_DIR).is_dir() and _addon(rig, "Nova/Nova.toc").exists()
    rig.untouched()


def test_a_reinstall_under_a_new_target_removes_the_old_target(rig: _Rig) -> None:
    first = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    moved = _pack_of([{"member": "patch-Y.MPQ", "to": "Data/patch-Q.MPQ"}])

    second = rig.install(moved, rig.fetched({"patch-Y.MPQ": NEW_Y}, name="q.zip"), previous=first)

    assert not (rig.play / "Data" / "patch-X.MPQ").exists()
    assert (rig.play / "Data" / "patch-Q.MPQ").read_bytes() == NEW_Y
    assert set(second["files"]) == {"Data/patch-Q.MPQ"}
    rig.untouched()


def test_a_case_only_rename_never_deletes_the_file_it_just_installed(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows `nova.lua` and `Nova.lua` are one file: dropping the old name drops the new.

    Since T227 the new version lands on the name already in the folder, whatever
    its case, so on a disk that tells cases apart there is one file too, not an
    old `nova.lua` left beside the new `Nova.lua` for the game to pick from. The
    only file removed is the swap's own aside of the old bytes.
    """
    first = rig.install(ADDONS, rig.fetched({"Nova/nova.lua": b"v1\n"}))
    removed: list[Path] = []
    real = client_config._remove_own
    monkeypatch.setattr(client_config, "_remove_own", lambda p: (removed.append(p), real(p)))

    second = rig.install(
        ADDONS, rig.fetched({"Nova/Nova.lua": b"v2\n"}, name="v2.zip"), previous=first
    )

    dropped = [path for path in removed if not path.name.endswith(".yulon-pack-old")]
    assert dropped == [], "nothing was dropped: the old name folds onto a new one"
    nova = rig.play / "Interface" / "AddOns" / "Nova"
    assert [p.name for p in nova.iterdir() if p.name.casefold() == "nova.lua"] == ["nova.lua"]
    assert (nova / "nova.lua").read_bytes() == b"v2\n"
    assert set(second["files"]) == {"Interface/AddOns/Nova/nova.lua"}


def test_a_dropped_file_the_player_edited_is_left_and_named_in_the_entry(rig: _Rig) -> None:
    first = rig.install(ADDONS, rig.fetched(V1))
    _addon(rig, "Nova/Nova.lua").write_bytes(b"the player's own edit\n")

    second = rig.install(
        ADDONS, rig.fetched({"Nova/Nova.toc": b"## v2\n"}, name="v2.zip"), previous=first
    )

    assert _addon(rig, "Nova/Nova.lua").read_bytes() == b"the player's own edit\n"
    assert second["left_behind"] == ["Interface/AddOns/Nova/Nova.lua"]
    assert "Interface/AddOns/Nova/Nova.lua" not in second["files"]


def test_a_reinstall_does_not_apply_remove_when_off(rig: _Rig) -> None:
    pack = _pack_of(
        [{"member": "t.MPQ", "to": "Data/patch-T.MPQ"}], remove_when_off=["Data/patch-Y.MPQ"]
    )
    first = rig.install(pack, rig.fetched({"t.MPQ": b"t1"}))
    other = rig.play / "Data" / "patch-Y.MPQ"
    other.write_bytes(b"somebody else's")

    rig.install(pack, rig.fetched({"t.MPQ": b"t2"}, name="t2.zip"), previous=first)

    assert other.read_bytes() == b"somebody else's"


def test_without_previous_nothing_old_is_removed(rig: _Rig) -> None:
    rig.install(ADDONS, rig.fetched(V1))

    rig.install(ADDONS, rig.fetched({"Nova/Nova.toc": b"## v2\n"}, name="v2.zip"))

    assert _addon(rig, "Nova/Nova.lua").exists()


def test_a_previous_naming_a_path_outside_the_client_refuses_before_writing_anything(
    rig: _Rig,
) -> None:
    bad = {"version": "1", "sha256": "0" * 64, "files": {"../outside.dll": "0" * 64}}
    before = _snapshot(rig.play)

    with pytest.raises(PackError, match="outside"):
        rig.install(ADDONS, rig.fetched(V1), previous=bad)

    assert _snapshot(rig.play) == before
    rig.untouched()


# -- a swap that is all or nothing ----------------------------------------------------------


V2 = {"Nova/Nova.toc": b"## v2\n", "Nova/Nova.lua": b"v2 lua\n", "Old/Old.toc": b"## v2 old\n"}
V1_FILES = {
    "Interface/AddOns/Nova/Nova.toc": b"## v1\n",
    "Interface/AddOns/Nova/Nova.lua": b"v1 lua\n",
    "Interface/AddOns/Old/Old.toc": b"## old\n",
}


def _failing_replace(
    monkeypatch: pytest.MonkeyPatch, fails: Any, exc: OSError
) -> list[tuple[str, str]]:
    """`os.replace` that raises `exc` for the calls `fails(src_name, dst_name)` picks."""
    real = os.replace
    calls: list[tuple[str, str]] = []

    def replace(src: Any, dst: Any) -> None:
        calls.append((Path(src).name, Path(dst).name))
        if fails(Path(src), Path(dst)):
            raise exc
        real(src, dst)

    monkeypatch.setattr(client_packs.os, "replace", replace)
    return calls


def _bytes_of_play(rig: _Rig, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        assert (rig.play / rel).read_bytes() == data, rel
    assert not list(rig.play.rglob("*.yulon-pack-tmp")), "a temporary file was left"
    assert not list(rig.play.rglob("*.yulon-pack-old")), "an aside file was left"


def test_a_failure_on_the_third_rename_puts_every_target_back(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.install(ADDONS, rig.fetched(V1))
    _failing_replace(
        monkeypatch,
        lambda src, dst: src.name.endswith(".yulon-pack-tmp") and dst.name == "Old.toc",
        OSError(errno.EIO, "Input/output error", "Old.toc"),
    )

    with pytest.raises(PackError, match="Input/output error"):
        rig.install(ADDONS, rig.fetched(V2, name="v2.zip"), sleep=lambda _s: None)
    monkeypatch.undo()

    _bytes_of_play(rig, V1_FILES)
    rig.untouched()


def test_a_refused_rename_says_to_close_wow_not_to_free_space(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.install(ADDONS, rig.fetched(V1))
    _failing_replace(
        monkeypatch,
        lambda src, dst: src.name.endswith(".yulon-pack-tmp") and dst.name == "Old.toc",
        PermissionError(errno.EACCES, "Access is denied", "Old.toc"),
    )

    with pytest.raises(PackError) as info:
        rig.install(ADDONS, rig.fetched(V2, name="v2.zip"), sleep=lambda _s: None)
    monkeypatch.undo()

    text = str(info.value)
    assert "Close World of Warcraft (and any program using the client's files)" in text
    assert "free some space" not in text.casefold()
    _bytes_of_play(rig, V1_FILES)


def test_a_failed_swap_of_a_new_file_removes_what_the_earlier_renames_installed(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing was there before: the rollback deletes the files already renamed in."""
    _failing_replace(
        monkeypatch,
        lambda src, dst: src.name.endswith(".yulon-pack-tmp") and dst.name == "Old.toc",
        OSError(errno.EIO, "Input/output error", "Old.toc"),
    )

    with pytest.raises(PackError):
        rig.install(ADDONS, rig.fetched(V1), sleep=lambda _s: None)
    monkeypatch.undo()

    assert not _addon(rig, "Nova/Nova.toc").exists()
    assert not list(rig.play.rglob("*.yulon-pack-*"))
    rig.untouched()


def test_a_rollback_that_fails_too_raises_partial_install_with_what_is_there_now(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.install(ADDONS, rig.fetched(V1))
    new_lua = V2["Nova/Nova.lua"]
    _failing_replace(
        monkeypatch,
        lambda src, dst: (src.name.endswith(".yulon-pack-tmp") and dst.name == "Old.toc")
        or (src.name.endswith(".yulon-pack-old") and dst.name == "Nova.lua"),
        OSError(errno.EIO, "Input/output error", "x"),
    )

    with pytest.raises(client_packs.PartialInstall) as info:
        rig.install(ADDONS, rig.fetched(V2, name="v2.zip"), sleep=lambda _s: None)
    monkeypatch.undo()

    entry = info.value.entry
    assert isinstance(info.value, PackError)
    assert entry["version"] is None
    assert entry["files"] == {"Interface/AddOns/Nova/Nova.lua": _sha(new_lua)}
    assert _addon(rig, "Nova/Nova.lua").read_bytes() == new_lua
    assert _addon(rig, "Nova/Nova.toc").read_bytes() == b"## v1\n", "rolled back"
    assert _addon(rig, "Old/Old.toc").read_bytes() == b"## old\n", "rolled back"
    rig.untouched()


def test_the_aside_files_are_deleted_only_after_every_rename_succeeded(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.install(ADDONS, rig.fetched(V1))
    seen: list[bool] = []
    real = os.replace

    def replace(src: Any, dst: Any) -> None:
        if Path(dst).name == "Old.toc" and Path(src).name.endswith(".yulon-pack-tmp"):
            seen.append(bool(list(rig.play.rglob("*.yulon-pack-old"))))
        real(src, dst)

    monkeypatch.setattr(client_packs.os, "replace", replace)
    rig.install(ADDONS, rig.fetched(V2, name="v2.zip"))

    assert seen == [True], "the earlier targets' old files were still aside at the last rename"
    _bytes_of_play(rig, {k: V2[k.removeprefix("Interface/AddOns/")] for k in V1_FILES})


# -- smaller fixes ---------------------------------------------------------------------------


def test_a_zip_with_a_name_that_is_not_utf8_is_refused_as_damaged(rig: _Rig) -> None:
    fetched = rig.fetched({"Nova/xÿ.toc": b"x"})
    raw = fetched.path.read_bytes()
    assert raw.count(b"\xc3\xbf") == 2, "the fixture must carry the name in both headers"
    fetched.path.write_bytes(raw.replace(b"\xc3\xbf", b"\xff\xff"))

    with pytest.raises(PackError, match="damaged"):
        rig.install(ADDONS, fetched)

    rig.untouched()


@pytest.mark.parametrize("suffix", [".yulon-pack-tmp", ".yulon-pack-old", ".YULON-PACK-TMP"])
def test_a_member_named_like_one_of_the_swap_files_is_refused(rig: _Rig, suffix: str) -> None:
    files = {"Nova/Nova.lua": b"real", f"Nova/Nova.lua{suffix}": b"hostile"}

    with pytest.raises(PackError, match="temporary file"):
        rig.install(ADDONS, rig.fetched(files))

    assert not _addon(rig, "Nova").exists()
    rig.untouched()


@pytest.mark.parametrize("name", ["Nova/Nova.toc.", "Nova/Nova.toc ", "Nova./x.toc", "Nova /x.toc"])
def test_a_name_ending_in_a_dot_or_a_space_is_refused(rig: _Rig, name: str) -> None:
    """Windows reads `Wow.exe.` as `Wow.exe`."""
    with pytest.raises(PackError, match="would leave the client"):
        rig.install(ADDONS, rig.fetched({name: b"x"}))

    rig.untouched()


def test_remove_refuses_a_recorded_name_with_a_trailing_dot(rig: _Rig) -> None:
    entry = {"version": "1", "sha256": "0" * 64, "files": {"Wow.exe.": _sha(b"MZexe")}}

    with pytest.raises(PackError, match="outside"):
        rig.remove(entry, None)

    assert (rig.play / "Wow.exe").read_bytes() == b"MZexe"


def test_two_servers_with_the_same_pack_id_each_keep_their_extracted_copy(
    tmp_path: Path,
) -> None:
    one, two = _Rig(tmp_path / "one"), _Rig(tmp_path / "two")
    first = one.fetched({"patch-Y.MPQ": NEW_Y})
    second = two.fetched({"patch-Y.MPQ": NEW_Y + b" a different build"})
    one.install(WORLD, first)
    two.install(WORLD, second)
    extracted = client_packs.cache_dir() / "extracted"
    assert len(list(extracted.iterdir())) == 2, "one folder per server"

    one.install(WORLD, one.fetched({"patch-Y.MPQ": NEW_Y + b" newer"}, name="n.zip"))

    folders = sorted(p.name for p in extracted.iterdir())
    assert len(folders) == 2, "server one replaced its own copy only"
    assert any(
        (p / f).read_bytes() == NEW_Y + b" a different build"
        for p in extracted.iterdir()
        for f in os.listdir(p)
    ), "server two's copy survived"
    one.untouched()
    two.untouched()


# -- fix round 2 (re-review of 2bc604aa) ----------------------------------------------------


@pytest.fixture
def windows_read_only(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Linux cannot say WinError 5: refuse to delete or replace a read-only file, as Windows does.

    Returns the `os.chmod` calls as `(path, link count at the time)`.
    """
    import pathlib

    real_unlink, real_replace, real_chmod = pathlib.Path.unlink, os.replace, _REAL_CHMOD
    chmods: list[tuple[str, int]] = []

    def read_only(path: Any) -> bool:
        try:
            return os.path.isfile(path) and not os.lstat(path).st_mode & 0o200
        except OSError:
            return False

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if read_only(self):
            raise PermissionError(errno.EACCES, "Access is denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    def replace(src: Any, dst: Any) -> None:
        if read_only(dst):
            raise PermissionError(errno.EACCES, "Access is denied", os.fspath(dst))
        real_replace(src, dst)

    def chmod(path: Any, mode: int, **kw: Any) -> None:
        chmods.append((os.fspath(path), os.lstat(path).st_nlink))
        real_chmod(path, mode, **kw)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    monkeypatch.setattr(client_packs.os, "replace", replace)
    monkeypatch.setattr(client_packs.os, "chmod", chmod)
    monkeypatch.setattr(client_config.os, "chmod", chmod)
    return chmods


def test_a_read_only_target_shared_with_the_original_never_wedges_the_next_install(
    rig: _Rig, windows_read_only: list[tuple[str, int]]
) -> None:
    original = rig.original / "Data" / "patch-X.MPQ"
    _REAL_CHMOD(original, 0o444)  # the player's own file, hard-linked into the client
    first = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    stuck = rig.play / "Data" / ("patch-X.MPQ" + client_packs._ASIDE)
    assert stuck.exists(), "the fixture must leave the read-only aside that cannot be deleted"
    newer = NEW_Y + b" version 2"

    second = rig.install(WORLD, rig.fetched({"patch-Y.MPQ": newer}, name="v2.zip"), previous=first)

    assert (rig.play / "Data" / "patch-X.MPQ").read_bytes() == newer
    assert second["files"] == {"Data/patch-X.MPQ": _sha(newer)}
    assert original.stat().st_mode & 0o777 == 0o444 and original.read_bytes() == STOCK_X
    assert [c for c in windows_read_only if c[1] > 1] == [], "a shared name was chmodded"
    _REAL_CHMOD(original, 0o644)
    rig.untouched()


def test_a_read_only_aside_with_one_name_is_made_writable_and_deleted(
    rig: _Rig, windows_read_only: list[tuple[str, int]]
) -> None:
    pack = _pack_of([{"member": "t.dll", "to": "Own.dll"}], id="own")
    own = rig.play / "Own.dll"
    own.write_bytes(b"old")
    _REAL_CHMOD(own, 0o444)

    rig.install(pack, rig.fetched({"t.dll": b"new"}))

    assert own.read_bytes() == b"new"
    assert not (rig.play / ("Own.dll" + client_packs._ASIDE)).exists()
    assert [Path(p).name for p, _ in windows_read_only] == ["Own.dll" + client_packs._ASIDE]
    assert [n for _, n in windows_read_only] == [1]


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
def test_deleting_the_client_after_a_pack_over_a_shared_read_only_archive_keeps_its_flag(
    rig: _Rig, windows_read_only: list[tuple[str, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T196: the install could not delete the aside of the player's read-only archive.

    Delete has to clear the flag on that inode to remove it (Windows), and puts it
    back on the player's own `Data/patch-X.MPQ`, the name the aside was moved from.
    """
    original = rig.original / "Data" / "patch-X.MPQ"
    _REAL_CHMOD(original, 0o444)  # the player's own file, hard-linked into the client
    rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}))
    aside = rig.play / "Data" / ("patch-X.MPQ" + client_packs._ASIDE)
    assert os.path.samefile(aside, original), "the fixture must leave the player's file aside"
    refused: list[str] = []

    def windows_unlink(path: Any) -> None:
        if not os.lstat(path).st_mode & 0o200:
            refused.append(os.fspath(path))
            raise PermissionError(errno.EACCES, "Access is denied", os.fspath(path))
        os.unlink(path)

    real_remove = play_client.remove_folder
    monkeypatch.setattr(
        play_client,
        "remove_folder",
        lambda folder, **kw: real_remove(folder, unlink=windows_unlink, **kw),
    )

    play_client.delete(rig.play, game=GAME, server_dir=rig.server)

    assert not rig.play.exists()
    assert set(refused) == {os.fspath(aside)}, "the read-only rule was not what the delete met"
    assert original.stat().st_mode & 0o777 == 0o444, "the player's own file left writable"
    _REAL_CHMOD(original, 0o644)
    rig.untouched()


def _fail_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(client_packs, "_stage", refuse)


def test_a_stray_aside_of_a_missing_target_is_put_back_before_anything_is_staged(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after the aside rename and before the new file went in left the name empty."""
    target = _addon(rig, "Nova/Nova.lua")
    target.parent.mkdir(parents=True)
    stray = target.with_name(target.name + client_packs._ASIDE)
    stray.write_bytes(b"the file before the crash")
    _fail_staging(monkeypatch)

    with pytest.raises(PackError):
        rig.install(ADDONS, rig.fetched({"Nova/Nova.lua": b"new"}))

    assert target.read_bytes() == b"the file before the crash"
    assert not stray.exists()
    rig.untouched()


def test_a_stray_aside_beside_an_existing_target_is_deleted_before_anything_is_staged(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _addon(rig, "Nova/Nova.lua")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"current")
    stray = target.with_name(target.name + client_packs._ASIDE + ".3")
    stray.write_bytes(b"stale")
    _fail_staging(monkeypatch)

    with pytest.raises(PackError):
        rig.install(ADDONS, rig.fetched({"Nova/Nova.lua": b"new"}))

    assert target.read_bytes() == b"current" and not stray.exists()
    rig.untouched()


@pytest.mark.parametrize("suffix", [".yulon-pack-old.1", ".yulon-pack-tmp.2"])
def test_a_member_named_like_a_numbered_swap_file_is_refused(rig: _Rig, suffix: str) -> None:
    with pytest.raises(PackError, match="temporary file"):
        rig.install(ADDONS, rig.fetched({f"Nova/Nova.lua{suffix}": b"x"}))

    rig.untouched()


def test_removing_a_read_only_file_shared_with_the_players_client_names_it_and_leaves_it(
    rig: _Rig, windows_read_only: list[tuple[str, int]]
) -> None:
    common = rig.original / "Data" / "common.MPQ"
    _REAL_CHMOD(common, 0o444)
    entry = {
        "version": "1",
        "sha256": "0" * 64,
        "files": {"Data/common.MPQ": _sha(common.read_bytes())},
    }

    with pytest.raises(PackError) as info:
        rig.remove(entry, None)

    text = str(info.value)
    assert "Data/common.MPQ" in text and "shared with your own" in text and "left" in text
    assert "Close World of Warcraft" not in text
    assert (rig.play / "Data" / "common.MPQ").exists()
    _REAL_CHMOD(common, 0o644)
    rig.untouched()


def test_the_record_never_keeps_left_behind(rig: _Rig) -> None:
    entry = _entry(**{"Data/patch-X.MPQ": NEW_Y}) | {"left_behind": ["Interface/AddOns/x.lua"]}

    client_packs.write_record(rig.play, _record(p=entry), game=GAME, server_dir=rig.server)

    assert "left_behind" not in (rig.play / client_packs.RECORD).read_text()
    assert "left_behind" not in client_packs.read_record(rig.play).packs["p"]
    assert entry["left_behind"] == ["Interface/AddOns/x.lua"], "the caller's entry is not changed"


def test_a_missing_checkout_pack_refusal_ends_by_naming_the_press_to_repeat(
    tmp_path: Path,
) -> None:
    """Play built nothing yet either way, but the press to repeat differs: Play or Make…."""
    from yulon.ui import controller_view as cv

    pack = _checkout_pack("centurion/patches/patch-Y.zip", sha256="0" * 64)
    play_text = cv._checkout_refusal(pack, tmp_path)
    make_text = cv._checkout_refusal(pack, tmp_path, again=f"“{cv.MAKE_PLAY_CLIENT_LABEL}”")
    assert play_text is not None and make_text is not None
    assert play_text.endswith("then press Play again.")
    assert make_text.endswith(f"then press “{cv.MAKE_PLAY_CLIENT_LABEL}” again.")
    assert "press Play" not in make_text


# -- the launcher's picks (T187) ----------------------------------------------------------------

_NO_CHOICES: dict[str, Any] = {"packs": {}, "exe_options": {}}


def test_launcher_picks_round_trip_and_an_old_record_reads_empty(rig: _Rig) -> None:
    assert client_packs.read_record(rig.play).launcher == {}
    picks = {
        "display": {"window": "windowed", "resolution": "1920x1080"},
        "account": "PLAYER_1",
        "realm_address": "192.168.1.5",
    }
    record = client_packs.PackRecord({}, None, _NO_CHOICES, launcher=picks)

    client_packs.write_record(rig.play, record, game=GAME, server_dir=rig.server)

    assert client_packs.read_record(rig.play).launcher == picks
    (rig.play / client_packs.RECORD).write_bytes(b'{"version": 1, "packs": {}}')
    assert client_packs.read_record(rig.play).launcher == {}


@pytest.mark.parametrize(
    "junk",
    [
        {"display": {"window": "floating"}},
        {"display": {"resolution": "big"}},
        {"display": "windowed"},
        {"account": 'A"B'},
        {"account": "A\nSET realmList x"},
        {"account": "X" * 33},
        {"account": 5},
        {"realm_address": "host name"},
        {"realm_address": 'a"b'},
        {"realm_address": ""},
        {"unknown": "x"},
    ],
)
def test_junk_launcher_values_are_dropped_on_read(rig: _Rig, junk: dict[str, Any]) -> None:
    (rig.play / client_packs.RECORD).write_text(
        json.dumps({"packs": {}, "launcher": junk}), encoding="utf-8"
    )
    assert client_packs.read_record(rig.play).launcher == {}


def test_a_launcher_that_is_not_a_mapping_reads_empty(rig: _Rig) -> None:
    (rig.play / client_packs.RECORD).write_text(
        json.dumps({"packs": {}, "launcher": ["x"]}), encoding="utf-8"
    )
    assert client_packs.read_record(rig.play).launcher == {}


@pytest.mark.parametrize(
    ("window", "keys"),
    [
        ("fullscreen", {"gxWindow": "0"}),
        ("windowed", {"gxWindow": "1", "gxMaximize": "0"}),
        ("maximized", {"gxWindow": "1", "gxMaximize": "1"}),
        ("borderless", {"gxWindow": "1", "gxMaximize": "1"}),
    ],
)
def test_each_window_mode_maps_to_its_config_keys(window: str, keys: dict[str, str]) -> None:
    got = client_packs.launcher_config_keys({"display": {"window": window}}, catalog_always={})
    assert got == keys


def test_resolution_and_account_map_to_their_keys() -> None:
    got = client_packs.launcher_config_keys(
        {"display": {"resolution": "1280x720"}, "account": "bob"}, catalog_always={}
    )
    assert got == {"gxResolution": "1280x720", "accountName": "BOB"}
    assert client_packs.launcher_config_keys({}, catalog_always={}) == {}


def test_a_key_the_catalog_always_sets_is_dropped_whatever_its_spelling() -> None:
    picks = {"display": {"window": "windowed", "resolution": "1280x720"}, "account": "BOB"}
    got = client_packs.launcher_config_keys(picks, catalog_always={"GXWINDOW": "0"})
    assert got == {"gxMaximize": "0", "gxResolution": "1280x720", "accountName": "BOB"}


def test_a_password_handed_to_the_launcher_keys_never_becomes_a_config_line() -> None:
    """Whatever reaches these functions raw, no password comes out of them (T187).

    `read_record` already drops a `password` key; this holds the functions that
    turn picks into Config.wtf lines to the same rule on their own.
    """
    raw = {"account": "BOB", "password": "s3cret", "accountPassword": "s3cret"}
    keys = client_packs.launcher_config_keys(raw, catalog_always={})
    assert keys == {"accountName": "BOB"}
    assert "password" not in client_packs.clean_launcher(raw)
    removals = client_packs.launcher_config_removals(raw, catalog_always={})
    assert not [key for key in removals if "password" in key.lower()]


def test_borderless_follows_the_window_pick_only_where_the_patch_has_it() -> None:
    def options(launcher: dict[str, Any], chosen: dict[str, bool], patch: list[str]) -> Any:
        return client_packs.launcher_exe_options(launcher, chosen, patch, catalog_always={})

    mode = {"display": {"window": "borderless"}}
    assert options(mode, {}, ["borderless"]) == {"borderless": True}
    other = {"display": {"window": "windowed"}}
    assert options(other, {"borderless": True}, ["borderless"]) == {"borderless": False}
    assert options(mode, {}, []) == {}
    assert options({}, {"borderless": True}, ["borderless"]) == {"borderless": True}


@pytest.mark.parametrize("key", ["gxWindow", "GXMAXIMIZE"])
def test_a_catalog_that_fixes_the_window_keys_leaves_the_borderless_option_alone(key: str) -> None:
    """The server sets the window itself: a launcher window pick says nothing about borderless."""
    picks = {"display": {"window": "windowed"}}
    chosen = {"borderless": True}
    got = client_packs.launcher_exe_options(
        picks, chosen, ["borderless"], catalog_always={key: "1"}
    )
    assert got == {"borderless": True}
    free = client_packs.launcher_exe_options(picks, chosen, ["borderless"], catalog_always={})
    assert free == {"borderless": False}


@pytest.mark.parametrize(
    "typed", ["10.0.", ".example.com", "example.com.", ".", "-", ":", "..", "-.:", "-:"]
)
def test_a_realm_address_needs_a_letter_or_digit_and_no_dot_at_either_end(typed: str) -> None:
    assert "realm_address" not in client_packs.clean_launcher({"realm_address": typed})


@pytest.mark.parametrize("typed", ["10.0.0.7", "logon.example.com", "host:3724", "my-realm", "a"])
def test_a_whole_realm_address_is_kept(typed: str) -> None:
    assert client_packs.clean_launcher({"realm_address": typed}) == {"realm_address": typed}


def test_this_computer_said_writes_the_default_address_where_config_wtf_is_the_only_channel() -> (
    None
):
    """Codex: no realmlist.wtf and no catalog realmList; removing the lines left the typed one."""
    said = {"realm_address": None}
    keys = client_packs.launcher_config_keys(said, catalog_always={}, default_address="127.0.0.1")
    assert keys == {"realmList": "127.0.0.1", "patchList": "127.0.0.1"}
    assert client_packs.launcher_config_keys(said, catalog_always={}, default_address=None) == {}
    assert (
        client_packs.launcher_config_keys({}, catalog_always={}, default_address="127.0.0.1") == {}
    )


def test_a_typed_realm_address_sets_realmlist_and_patchlist_over_the_catalogs() -> None:
    """Lead ruling: the typed address wins over the catalog's for these two keys only."""
    picks = {"realm_address": "10.0.0.7", "display": {"window": "windowed"}}
    always = {"REALMLIST": "127.0.0.1", "patchlist": "127.0.0.1", "gxWindow": "0"}
    got = client_packs.launcher_config_keys(picks, catalog_always=always)
    assert got == {"realmList": "10.0.0.7", "patchList": "10.0.0.7", "gxMaximize": "0"}
    assert "realmList" not in client_packs.launcher_config_keys({}, catalog_always={})


@pytest.mark.parametrize(
    ("typed", "auth_port", "written"),
    [
        ("10.0.0.7", 3724, "10.0.0.7"),
        ("10.0.0.7", 3725, "10.0.0.7:3725"),
        ("logon.example.com:3725", 3725, "logon.example.com:3725"),
    ],
)
def test_a_typed_address_carries_the_servers_auth_port_into_config_wtf(
    typed: str, auth_port: int, written: str
) -> None:
    """T565: realmlist.wtf said `host:3725` for a second server, Config.wtf said `host`."""
    got = client_packs.launcher_config_keys(
        {"realm_address": typed}, catalog_always={}, auth_port=auth_port
    )
    assert got == {"realmList": written, "patchList": written}


def test_this_computer_said_carries_the_auth_port_into_config_wtf_too() -> None:
    got = client_packs.launcher_config_keys(
        {"realm_address": None}, catalog_always={}, default_address="127.0.0.1", auth_port=3725
    )
    assert got == {"realmList": "127.0.0.1:3725", "patchList": "127.0.0.1:3725"}


def test_ask_in_the_game_removes_accountname_and_only_when_said_so() -> None:
    assert client_packs.launcher_config_removals({"account": None}, catalog_always={}) == (
        "accountName",
    )
    assert client_packs.launcher_config_removals({}, catalog_always={}) == ()
    assert client_packs.launcher_config_removals({"account": "BOB"}, catalog_always={}) == ()
    fixed = {"ACCOUNTNAME": "SERVER"}
    assert client_packs.launcher_config_removals({"account": None}, catalog_always=fixed) == ()


def test_this_computer_said_takes_the_realm_keys_out_only_where_realmlist_wtf_carries_it() -> None:
    """Lead ruling (T187 fix 1): "Use this computer" is saved as `realm_address: None`.

    Where this computer's address reaches the game another way (realmlist.wtf on an
    entry without `config_wtf`, or the catalog's own `realmList`), the
    `realmList`/`patchList` a typed address put into Config.wtf earlier come out;
    where nothing else carries it (flag off) nothing is taken out, and a key the
    catalog's `always` sets is never taken out either way.
    """
    said = client_packs.clean_launcher({"realm_address": None})
    assert said == {"realm_address": None}
    assert client_packs.launcher_config_keys(said, catalog_always={}) == {}
    removals = client_packs.launcher_config_removals
    assert removals(said, catalog_always={}, default_address_written=True) == (
        "realmList",
        "patchList",
    )
    assert removals(said, catalog_always={}, default_address_written=False) == ()
    assert removals({}, catalog_always={}, default_address_written=True) == ()
    both = {"realm_address": None, "account": None}
    assert removals(both, catalog_always={"realmlist": "x"}, default_address_written=True) == (
        "accountName",
        "patchList",
    )


# -- T227: a client whose files are named in lower case ----------------------


@pytest.fixture
def lower_rig(tmp_path: Path) -> _Rig:
    """`_Rig`, made from a player's client named in lower case: `patch-x.mpq`, `enus/`."""
    rig = _Rig(tmp_path)
    play_client.remove_folder(rig.play, original=rig.original)
    os.rename(rig.original / "Data" / "patch-X.MPQ", rig.original / "Data" / "patch-x.mpq")
    os.rename(rig.original / "Data" / "enUS", rig.original / "Data" / "enus")
    play_client.create(
        rig.original,
        rig.play,
        game=GAME,
        server_dir=rig.server,
        allow_full_copy=False,
        reflink=lambda src, dst: False,
    )
    rig.before = _snapshot(rig.original)
    return rig


def _named_alike(folder: Path, name: str) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.casefold() == name.casefold())


def test_a_pack_lands_on_the_lowercase_name_already_there_not_beside_it(lower_rig: _Rig) -> None:
    """T227: two archives whose names differ only in case are one archive to the game.

    Written beside it, `patch-X.MPQ` would leave the player's stock `patch-x.mpq`
    in the folder as well, and which one WoW loads would be anybody's guess.
    """
    entry = lower_rig.install(WORLD, lower_rig.fetched({"patch-Y.MPQ": NEW_Y}))

    assert _named_alike(lower_rig.play / "Data", "patch-X.MPQ") == ["patch-x.mpq"]
    assert (lower_rig.play / "Data" / "patch-x.mpq").read_bytes() == NEW_Y
    assert entry["files"] == {"Data/patch-x.mpq": _sha(NEW_Y)}
    lower_rig.untouched()


def test_a_pack_for_a_locale_folder_goes_into_the_one_already_there(lower_rig: _Rig) -> None:
    pack = _pack_of([{"member": "a.MPQ", "to": "Data/enUS/patch-enUS-A.MPQ"}])

    entry = lower_rig.install(pack, lower_rig.fetched({"a.MPQ": b"MPQ locale"}))

    assert _named_alike(lower_rig.play / "Data", "enUS") == ["enus"]
    assert (lower_rig.play / "Data" / "enus" / "patch-enUS-A.MPQ").read_bytes() == b"MPQ locale"
    assert entry["files"] == {"Data/enus/patch-enUS-A.MPQ": _sha(b"MPQ locale")}
    lower_rig.untouched()


def test_switching_a_pack_off_removes_its_file_in_whatever_case(lower_rig: _Rig) -> None:
    """`remove_when_off` names `Data/patch-Y.MPQ`; on this client it is `Data/patch-y.mpq`."""
    pack = _pack_of(
        [{"member": "t.MPQ", "to": "Data/patch-T.MPQ"}], remove_when_off=["Data/patch-Y.MPQ"]
    )
    entry = lower_rig.install(pack, lower_rig.fetched({"t.MPQ": b"t"}))
    other = lower_rig.play / "Data" / "patch-y.mpq"
    other.write_bytes(b"the stock alternative terrain, in lower case")

    lower_rig.remove(entry, pack)

    assert not other.exists()
    assert not (lower_rig.play / "Data" / "patch-T.MPQ").exists()
    lower_rig.untouched()


# -- T303: a Stop while a pack is proved or laid ----------------------------------------------


class _StopAt:
    """A Stop pressed at the `at`-th time the pack code asks, and set from then on."""

    def __init__(self, at: int) -> None:
        self.at = at
        self.asked = 0

    def __call__(self) -> bool:
        self.asked += 1
        return self.asked >= self.at


def test_a_stop_while_the_parts_are_joined_stops_the_join_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    """At the second piece. Each piece is one chunk, and so is the joined zip's proof, so a
    join that did not ask would reach the proof having asked once, and finish."""
    server = tmp_path / "server"
    folder = server / "p"
    folder.mkdir(parents=True)
    data = _zip()
    assert len(data) < client_packs.CHUNK_BYTES, "the ground: one chunk per piece"
    _split(data, folder, "patch-Y.zip", 3)
    md5 = hashlib.md5(data).hexdigest()
    stop = _StopAt(2)

    with pytest.raises(client_packs.Cancelled):
        fetch_checkout(_checkout_pack("p/patch-Y.zip", md5=md5), server, cancelled=stop)

    assert stop.asked == 2
    assert list((client_packs.cache_dir() / "checkout" / md5).iterdir()) == []


def test_a_stop_while_a_plain_checkout_file_is_proved_is_a_stop_not_a_refusal(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server"
    (server / "p").mkdir(parents=True)
    data = _zip()
    (server / "p" / "patch-Y.zip").write_bytes(data)
    pack = _checkout_pack("p/patch-Y.zip", sha256=hashlib.sha256(data).hexdigest())

    with pytest.raises(PackError) as stopped:
        fetch_checkout(pack, server, cancelled=lambda: True)

    assert type(stopped.value) is client_packs.Cancelled, stopped.value
    assert str(stopped.value) == (
        "Laying World patch was stopped part-way; nothing of it was installed."
    )
    assert (server / "p" / "patch-Y.zip").read_bytes() == data, "the server's own file stays"


def test_a_stop_while_a_pack_is_laid_leaves_the_client_as_it_was(rig: _Rig) -> None:
    before = _snapshot(rig.play)

    with pytest.raises(client_packs.Cancelled):
        rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}), cancelled=lambda: True)

    assert _snapshot(rig.play) == before
    rig.untouched()


def test_a_stop_while_a_cached_archive_is_checked_stops_before_it_is_linked_in(
    rig: _Rig,
) -> None:
    """A second laying finds the archive in the cache and reads it to check it: that read
    asks too, or a Stop waits for a whole cached archive to be read."""
    fetched = rig.fetched({"patch-Y.MPQ": NEW_Y})
    rig.install(WORLD, fetched)
    laid = _snapshot(rig.play)
    laid_in = rig.play / "Data" / "patch-X.MPQ"
    laid_in.unlink()  # a hard link to the cached archive: never written through
    laid_in.write_bytes(STOCK_X)  # as a fresh copy of the player's client holds it
    before = _snapshot(rig.play)
    assert before != laid

    with pytest.raises(client_packs.Cancelled):
        rig.install(WORLD, fetched, cancelled=lambda: True)

    assert _snapshot(rig.play) == before


def test_a_stop_after_the_last_file_is_staged_still_installs_nothing(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review: asked once more between staging and the renames, so a Stop that lands
    after the last chunk was read is not answered by a pack laid in anyway."""
    staged: list[bool] = []
    real_stage = client_packs._stage

    def stage(*args: Any) -> Any:
        made = real_stage(*args)
        staged.append(True)  # the Stop lands now: every chunk of this file was read
        return made

    monkeypatch.setattr(client_packs, "_stage", stage)
    before = _snapshot(rig.play)

    with pytest.raises(client_packs.Cancelled):
        rig.install(WORLD, rig.fetched({"patch-Y.MPQ": NEW_Y}), cancelled=lambda: bool(staged))

    assert staged == [True], "the ground: the one file was staged before the Stop"
    assert _snapshot(rig.play) == before


# --- A pack that is a folder of the checkout (T555 T1) -------------------------------------------
#
# mod-unbound ships its three addons as 43 loose files under `client/Interface/AddOns`, with a
# `sha256sum` list (`MANIFEST.sha256`) at the module's root. Each file is proved against its line
# and packed, in the same pass, into a zip in the cache; from there the pack is installed the way
# every zip pack is.

MODULE = "modules/mod-unbound"
FOLDER = f"{MODULE}/client/Interface/AddOns"
LIST = f"{MODULE}/MANIFEST.sha256"
BLP = b"BLP2" + bytes(range(256)) * 4
FOLDER_FILES: dict[str, bytes] = {
    "multiclass-talents-ui/multiclass-talents-ui.toc": b"## Interface: 30300\r\n## Title: MC\r\n",
    "multiclass-talents-ui/Art/!UI-Frame.blp": BLP,
    "multiclass-talents-ui/Core.lua": b"local x = 1\r\nprint(x)\r\n",
    "multiclass-resources/multiclass-resources.toc": b"## Interface: 30300\n",
    "UnboundSpellbook/UnboundSpellbook.toc": b"## Interface: 30300\n## Title: Book\n",
    "UnboundSpellbook/UI.lua": b"-- lf only\nreturn {}\n",
}
OUTSIDE_FILES: dict[str, bytes] = {
    "README.md": b"# mod-unbound\n",
    "client/README-PLAYERS.txt": b"Install the addons.\n",
    "data/sql/db-world/00_npc_setup.sql": b"SELECT 1;\n",
}


def _folder_pack(**extra: Any) -> ClientPack:
    return ClientPack.model_validate(
        {
            "id": "unbound-addons",
            "label": "Unbound addons",
            "source": {"kind": "checkout_folder", "path": FOLDER},
            "sha256_file": LIST,
            "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
            **extra,
        }
    )


def _list_lines(files: Mapping[str, bytes], prefix: str = "client/Interface/AddOns/") -> bytes:
    """`sha256sum` output for `files`, named the way the module's list names them."""
    return b"".join(
        f"{_sha(data)}  {prefix}{name}\n".encode() for name, data in sorted(files.items())
    )


def _lay_module(
    server: Path,
    files: Mapping[str, bytes] | None = None,
    *,
    listed: Mapping[str, bytes] | None = None,
) -> Path:
    """A checkout of the module: the addon files, a few others, and the sha256sum list."""
    files = FOLDER_FILES if files is None else files
    listed = files if listed is None else listed
    root = server / MODULE
    for name, data in files.items():
        path = server / FOLDER / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name, data in OUTSIDE_FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "MANIFEST.sha256").write_bytes(
        _list_lines(OUTSIDE_FILES, prefix="") + _list_lines(listed)
    )
    return server / FOLDER


def _fetch_folder(server: Path, **kw: Any) -> client_packs.Fetched:
    return client_packs.fetch_checkout_folder(_folder_pack(), server, entry_id="wow-unbound", **kw)


def _cache_files(cache: Path) -> list[str]:
    if not cache.exists():
        return []
    return sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file())


def test_a_folder_pack_is_proved_and_packed_into_the_same_zip_every_time(
    tmp_path: Path, _cache_in_tmp: Path
) -> None:
    server = tmp_path / "srv"
    folder = _lay_module(server)

    first = _fetch_folder(server)
    # A checkout that was touched (a fresh clone, a git checkout) has new times, same bytes.
    for path in folder.rglob("*"):
        os.utime(path, (1_900_000_000, 1_900_000_000))
    second = _fetch_folder(server)

    assert first == second, "the same folder must make the same zip, whatever its file times"
    assert first.version is None
    assert first.sha256 == _sha(first.path.read_bytes())
    digest = first.path.parent.name
    assert first.path.parent.parent == _cache_in_tmp / first.path.parts[-4] / "unbound-addons"
    assert first.path.parts[-4].startswith("wow-unbound."), "one cache folder per server"
    assert first.path.name == "unbound-addons.zip"
    with zipfile.ZipFile(first.path) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == sorted(FOLDER_FILES)
        assert {name: archive.read(name) for name in archive.namelist()} == FOLDER_FILES
        assert {info.compress_type for info in infos} == {zipfile.ZIP_STORED}
        assert {info.date_time for info in infos} == {(1980, 1, 1, 0, 0, 0)}
    assert _cache_files(_cache_in_tmp) == [
        f"{first.path.parts[-4]}/unbound-addons/{digest}/unbound-addons.zip"
    ], "no .part left, and nothing else"


def test_a_folder_pack_installs_byte_for_byte_and_keeps_the_players_own_addons(
    rig: _Rig, _cache_in_tmp: Path
) -> None:
    _lay_module(rig.server)
    own = rig.play / "Interface" / "AddOns" / "Questie" / "Questie.toc"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"## Title: Questie\n")

    fetched = _fetch_folder(rig.server)
    entry = rig.install(_folder_pack(), fetched)

    addons = rig.play / "Interface" / "AddOns"
    for name, data in FOLDER_FILES.items():
        assert (addons / name).read_bytes() == data, name
    assert (addons / "multiclass-talents-ui" / "Art" / "!UI-Frame.blp").read_bytes()[:4] == b"BLP2"
    assert entry == {
        "version": None,
        "sha256": fetched.sha256,
        "files": {f"Interface/AddOns/{name}": _sha(data) for name, data in FOLDER_FILES.items()},
    }
    assert own.read_bytes() == b"## Title: Questie\n", "the player's own addon was touched"
    assert (addons / "Blizzard_Own" / "x.toc").exists()
    rig.untouched()


FOLDER_REFUSALS: dict[str, tuple[Callable[[Path], None], str]] = {}


def _refusal(
    name: str, fragment: str
) -> Callable[[Callable[[Path], None]], Callable[[Path], None]]:
    def register(spoil: Callable[[Path], None]) -> Callable[[Path], None]:
        FOLDER_REFUSALS[name] = (spoil, fragment)
        return spoil

    return register


@_refusal("a file the list does not name", "UnboundSpellbook/Extra.lua")
def _extra_file(server: Path) -> None:
    (server / FOLDER / "UnboundSpellbook" / "Extra.lua").write_bytes(b"-- not listed\n")


@_refusal("a listed file that is not there", "multiclass-resources/multiclass-resources.toc")
def _missing_file(server: Path) -> None:
    (server / FOLDER / "multiclass-resources" / "multiclass-resources.toc").unlink()


@_refusal("a file whose bytes changed", "multiclass-talents-ui/Core.lua")
def _changed_file(server: Path) -> None:
    path = server / FOLDER / "multiclass-talents-ui" / "Core.lua"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))  # an EOL "fix" is a change


@_refusal("two lines for one file that disagree", "UnboundSpellbook/UI.lua")
def _two_lines(server: Path) -> None:
    listing = server / LIST
    other = f"{'0' * 64}  client/Interface/AddOns/UnboundSpellbook/UI.lua\n".encode()
    listing.write_bytes(listing.read_bytes() + other)


@_refusal("a file that is a link", "UnboundSpellbook/Link.lua")
def _linked_file(server: Path) -> None:
    target = server / FOLDER / "UnboundSpellbook" / "UI.lua"
    (server / FOLDER / "UnboundSpellbook" / "Link.lua").symlink_to(target)


@_refusal("a folder that is a link", "Linked")
def _linked_folder(server: Path) -> None:
    (server / FOLDER / "Linked").symlink_to(server / FOLDER / "UnboundSpellbook")


@_refusal("a folder of the path that is a link", "modules/mod-unbound/client")
def _linked_ancestor(server: Path) -> None:
    """Codex adversarial (T555 T1): a link on the way, not only at the end, leaves the checkout."""
    real = server.parent / "elsewhere"
    shutil.move(server / MODULE / "client", real)
    (server / MODULE / "client").symlink_to(real)


@_refusal("a list reached through a link", "modules/mod-unbound")
def _linked_module(server: Path) -> None:
    real = server.parent / "another-module"
    shutil.move(server / MODULE, real)
    (server / MODULE).symlink_to(real)


@_refusal("a folder with nothing in it and no lines", "has no files in")
def _empty(server: Path) -> None:
    """Codex adversarial round 2: an empty pack would install as done and never come back."""
    shutil.rmtree(server / FOLDER)
    (server / FOLDER).mkdir()
    (server / LIST).write_bytes(_list_lines(OUTSIDE_FILES, prefix=""))


@_refusal("no sha256sum list", "MANIFEST.sha256")
def _no_list(server: Path) -> None:
    (server / LIST).unlink()


@_refusal("no folder", FOLDER)
def _no_folder(server: Path) -> None:
    shutil.rmtree(server / FOLDER)


@pytest.mark.parametrize("name", sorted(FOLDER_REFUSALS))
def test_a_folder_that_does_not_match_its_list_is_refused_naming_the_file_and_nothing_is_kept(
    tmp_path: Path, _cache_in_tmp: Path, name: str
) -> None:
    spoil, fragment = FOLDER_REFUSALS[name]
    server = tmp_path / "srv"
    _lay_module(server)
    _fetch_folder(server)  # the ground: the unspoilt folder is accepted
    shutil.rmtree(_cache_in_tmp)
    spoil(server)

    with pytest.raises(PackError) as caught:
        _fetch_folder(server)

    message = str(caught.value)
    assert fragment in message
    assert message.startswith("Unbound addons")
    assert LATEST in message, "points at the Server build menu"
    assert _cache_files(_cache_in_tmp) == [], "a refused folder left a zip or a part behind"


def test_a_folder_pack_refusal_names_every_odd_file_up_to_a_few(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    _lay_module(server)
    for n in range(7):
        (server / FOLDER / "UnboundSpellbook" / f"New{n}.lua").write_bytes(b"x")

    with pytest.raises(PackError, match=r"UnboundSpellbook/New0\.lua.*and 2 more") as caught:
        _fetch_folder(server)

    assert "New5.lua" not in str(caught.value)


def test_a_server_folder_reached_through_a_link_is_still_the_servers(tmp_path: Path) -> None:
    """Only the path below the server folder is held to "no link": the player's own choice
    of where the server folder lives (a link to another drive) is theirs."""
    real = tmp_path / "real-srv"
    _lay_module(real)
    (tmp_path / "srv").symlink_to(real)

    fetched = _fetch_folder(tmp_path / "srv")

    with zipfile.ZipFile(fetched.path) as archive:
        assert sorted(archive.namelist()) == sorted(FOLDER_FILES)


def test_two_agreeing_lines_for_one_file_are_one_line(tmp_path: Path) -> None:
    """`sha256sum -c` checks such a file twice and passes; so does this (the md5_file rule)."""
    server = tmp_path / "srv"
    _lay_module(server)
    listing = server / LIST
    listing.write_bytes(
        listing.read_bytes()
        + _list_lines({"UnboundSpellbook/UI.lua": FOLDER_FILES["UnboundSpellbook/UI.lua"]})
    )

    fetched = _fetch_folder(server)

    with zipfile.ZipFile(fetched.path) as archive:
        assert sorted(archive.namelist()) == sorted(FOLDER_FILES)


def test_two_servers_of_one_game_never_share_a_folder_packs_zip(
    tmp_path: Path, _cache_in_tmp: Path
) -> None:
    """Cold review note 3: two servers playing at once must not rename over the zip the other
    is installing from (Windows refuses that), nor prune it from under the other."""
    one, two = tmp_path / "srv-one", tmp_path / "srv-two"
    _lay_module(one)
    _lay_module(two)

    first, second = _fetch_folder(one), _fetch_folder(two)

    assert first.sha256 == second.sha256, "the same folder, the same zip"
    assert first.path != second.path
    assert first.path.parents[2] != second.path.parents[2]


def test_a_zip_already_there_with_the_same_bytes_is_not_renamed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zip an earlier Play made may be open (an install reading it); the same bytes are
    kept as they are rather than renamed over, which Windows refuses for an open file."""
    server = tmp_path / "srv"
    _lay_module(server)
    first = _fetch_folder(server)
    real = os.replace

    def refuse_onto_the_zip(src: Any, dst: Any) -> None:
        if Path(dst) == first.path:
            raise PermissionError(13, "The process cannot access the file", str(dst))
        real(src, dst)

    monkeypatch.setattr(client_packs.os, "replace", refuse_onto_the_zip)

    again = _fetch_folder(server)

    assert again == first
    assert sorted(p.name for p in first.path.parent.iterdir()) == ["unbound-addons.zip"]


def test_a_list_line_with_a_star_and_windows_slashes_names_the_same_file(tmp_path: Path) -> None:
    """`sha256sum -b` marks binary mode with `*`; a list made on Windows may use backslashes."""
    server = tmp_path / "srv"
    _lay_module(server)
    lines = [
        f"{_sha(data)} *client\\Interface\\AddOns\\{name.replace('/', chr(92))}\r\n"
        for name, data in sorted(FOLDER_FILES.items())
    ]
    (server / LIST).write_bytes("".join(lines).encode())

    fetched = _fetch_folder(server)

    with zipfile.ZipFile(fetched.path) as archive:
        assert sorted(archive.namelist()) == sorted(FOLDER_FILES)


def test_a_stop_while_a_folder_is_proved_keeps_nothing(tmp_path: Path, _cache_in_tmp: Path) -> None:
    server = tmp_path / "srv"
    _lay_module(server)
    asked: list[bool] = []

    def cancelled() -> bool:
        asked.append(True)
        return len(asked) > 2  # part-way through the files

    with pytest.raises(Cancelled):
        _fetch_folder(server, cancelled=cancelled)

    assert _cache_files(_cache_in_tmp) == []


def test_a_folder_packs_checksum_follows_its_list_without_reading_the_files(tmp_path: Path) -> None:
    """For the map data's salt (`trinitycore._packs_salt`): a changed list is a changed pack."""
    server = tmp_path / "srv"
    _lay_module(server)
    before = client_packs.checkout_checksum(_folder_pack(), server)
    (server / FOLDER / "UnboundSpellbook" / "UI.lua").write_bytes(b"changed, list not")
    assert client_packs.checkout_checksum(_folder_pack(), server) == before
    changed = {**FOLDER_FILES, "UnboundSpellbook/UI.lua": b"changed, list too"}
    _lay_module(server, changed)

    after = client_packs.checkout_checksum(_folder_pack(), server)

    assert after != before and len(after) == 64


def test_a_zip_fetcher_refuses_a_folder_pack_and_the_folder_fetcher_a_zip_pack(
    tmp_path: Path,
) -> None:
    """Neither fetcher guesses at the other's kind: the caller must say which it means."""
    with pytest.raises(PackError, match="is a folder"):
        fetch_checkout(_folder_pack(), tmp_path)
    with pytest.raises(PackError, match="not a folder"):
        client_packs.fetch_checkout_folder(
            _checkout_pack("p.zip", md5="0" * 32), tmp_path, entry_id="wow-unbound"
        )


def test_putting_back_missing_files_writes_only_those_and_keeps_an_edited_sibling(
    rig: _Rig,
) -> None:
    """Codex adversarial (T555 T1): a repair is not an update. The pack is the same version,
    so only the files gone from the client are written; one the player edited stays, as it
    does on every Play the pack is not changed."""
    fetched = rig.fetched(ADDON_FILES)
    entry = rig.install(ADDONS, fetched)
    nova = rig.play / "Interface" / "AddOns" / "Nova"
    (nova / "Nova.lua").unlink()
    (nova / "Nova.toc").write_bytes(b"## Title: Nova, as I like it\n")

    again = rig.install(ADDONS, fetched, previous=entry, only_missing=True)

    assert (nova / "Nova.lua").read_bytes() == ADDON_FILES["Nova/Nova.lua"]
    assert (nova / "Nova.toc").read_bytes() == b"## Title: Nova, as I like it\n"
    assert again == entry, "the record still names every file with the hash Yu'lon wrote"
    rig.untouched()


def test_putting_back_a_file_whose_old_backup_was_left_writes_the_packs_bytes(
    rig: _Rig,
) -> None:
    """Cold review note 2: a `.yulon-pack-old` a crashed swap left beside a missing file must
    not come back in its place while the record says the pack's hash."""
    fetched = rig.fetched(ADDON_FILES)
    entry = rig.install(ADDONS, fetched)
    nova = rig.play / "Interface" / "AddOns" / "Nova"
    (nova / "Nova.lua").unlink()
    (nova / "Nova.lua.yulon-pack-old").write_bytes(b"print('an older Nova')\n")

    again = rig.install(ADDONS, fetched, previous=entry, only_missing=True)

    assert (nova / "Nova.lua").read_bytes() == ADDON_FILES["Nova/Nova.lua"]
    assert again["files"]["Interface/AddOns/Nova/Nova.lua"] == _sha(ADDON_FILES["Nova/Nova.lua"])
    assert sorted(p.name for p in nova.iterdir()) == ["Nova.lua", "Nova.toc"], "an aside was left"


def test_a_linked_addons_folder_is_not_a_missing_pack(rig: _Rig) -> None:
    """Cold review note 1: the player made `Interface/AddOns` a link after the pack went in.
    What is behind the link is theirs; Play neither repairs through it nor refuses for it."""
    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))
    addons = rig.play / "Interface" / "AddOns"
    elsewhere = rig.root / "my-addons"
    shutil.move(addons, elsewhere)
    shutil.rmtree(elsewhere / "Nova")
    addons.symlink_to(elsewhere, target_is_directory=True)

    assert client_packs.recorded_files_missing(rig.play, entry) == ()


def test_putting_back_with_nothing_missing_writes_nothing(rig: _Rig) -> None:
    fetched = rig.fetched(ADDON_FILES)
    entry = rig.install(ADDONS, fetched)
    before = _snapshot(rig.play)

    again = rig.install(ADDONS, fetched, previous=entry, only_missing=True)

    assert again == entry and _snapshot(rig.play) == before


def test_a_recorded_file_missing_from_the_client_is_reported(rig: _Rig) -> None:
    """The Play pipeline installs a pack again when one of its files was deleted (T555 T1)."""
    entry = rig.install(ADDONS, rig.fetched(ADDON_FILES))
    assert client_packs.recorded_files_missing(rig.play, entry) == ()

    (rig.play / "Interface" / "AddOns" / "Nova" / "Nova.lua").unlink()

    assert client_packs.recorded_files_missing(rig.play, entry) == (
        "Interface/AddOns/Nova/Nova.lua",
    )
    edited = rig.play / "Interface" / "AddOns" / "Nova" / "Nova.toc"
    edited.write_bytes(b"the player's edit")
    assert client_packs.recorded_files_missing(rig.play, entry) == (
        "Interface/AddOns/Nova/Nova.lua",
    ), "an edited file is there: only a missing one asks for a new install"
    # Codex review round 3: a file still there under another case is not missing, or a
    # case-sensitive disk would be "repaired" on every Play and nothing ever written.
    (rig.play / "Interface" / "AddOns" / "Nova").rename(rig.play / "Interface" / "AddOns" / "nova")
    (rig.play / "Interface" / "AddOns" / "nova" / "Nova.lua").write_bytes(
        b"back, lower-case folder"
    )
    assert client_packs.recorded_files_missing(rig.play, entry) == ()
    junk = {**entry, "files": {"../outside": "0" * 64}}
    assert client_packs.recorded_files_missing(rig.play, junk) == (), "a bad name is not a file"
