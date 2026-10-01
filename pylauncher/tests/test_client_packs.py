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

import hashlib
import io
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import client_packs, platform
from yulon.catalog.catalog import ClientPack
from yulon.client_packs import Cancelled, PackError, fetch_checkout, fetch_url
from yulon.selfupdate import fetch
from yulon.update import _Deadline

HOST = "packs.example.org"
ZIP_URL = f"https://{HOST}/downloads/hd-creatures.zip"
VERSION_URL = f"https://{HOST}/downloads/hd-creatures.version"
HOSTS = frozenset({HOST})


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
            archive.writestr(name, data)
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


# --- the real opener's redirect rule -------------------------------------------------------


def _redirect_handler(hosts: frozenset[str] | None) -> urllib.request.HTTPRedirectHandler:
    opener = fetch._https_only_opener(_Deadline(1.0), platform.verify_context(), hosts=hosts)
    return next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))


def test_a_redirect_to_a_host_the_entry_does_not_name_is_not_followed() -> None:
    handler = _redirect_handler(HOSTS)
    request = urllib.request.Request(ZIP_URL, method="GET")

    with pytest.raises(fetch.UpdateError, match="does not name"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/x.zip")


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
