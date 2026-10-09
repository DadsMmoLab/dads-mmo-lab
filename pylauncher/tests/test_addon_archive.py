"""Staging a client add-on from a zip, a zip link or a folder, safely (T613 PR-1).

Everything is checked before anything is written, and a refusal leaves nothing
behind in staging. Each negative fixture trips exactly one rule, and the test
pins the wording only that rule produces.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import threading
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from yulon import addon_archive, client_packs, platform
from yulon.addon_archive import AddonRefusal, check_folder, stage_link, stage_zip
from yulon.addon_layout import Found, find_addons
from yulon.selfupdate import fetch
from yulon.update import _Deadline

NOTHING = " Nothing was changed."


@pytest.fixture(autouse=True)
def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setattr(client_packs, "cache_dir", lambda: cache)
    return cache


def _left(cache: Path) -> list[str]:
    """Whatever is under the add-on staging and download folders, the folders themselves aside."""
    base = cache / "addons"
    if not base.exists():
        return []
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.relative_to(base).as_posix() not in ("staging", "downloads")
    )


def _zip(path: Path, members: Mapping[str, bytes | str], *, deflate: bool = True) -> Path:
    method = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", method) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = method
            archive.writestr(info, data)
    return path


def _refusal(call: Callable[[], object]) -> str:
    with pytest.raises(AddonRefusal) as caught:
        call()
    said = str(caught.value)
    assert said.endswith(NOTHING), said
    return said


GOOD = {"pfUI-master/pfUI.toc": "## Interface: 11200\n", "pfUI-master/pfUI.lua": "-- x\n"}


# --- a local zip --------------------------------------------------------------------------


def test_a_local_zip_unpacks_into_a_new_folder_under_the_cache(
    tmp_path: Path, _cache: Path
) -> None:
    staged = stage_zip(_zip(tmp_path / "pfUI-master.zip", GOOD))

    assert staged.root.parent == _cache / "addons" / "staging"
    assert (staged.root / "pfUI-master" / "pfUI.toc").read_text() == "## Interface: 11200\n"
    found = find_addons(staged.root, interface=11200, shipped={})
    assert isinstance(found, Found) and [a.name for a in found.addons] == ["pfUI"]
    staged.discard()
    assert _left(_cache) == []


def test_two_zips_never_share_a_staging_folder(tmp_path: Path) -> None:
    one = stage_zip(_zip(tmp_path / "a.zip", GOOD))
    two = stage_zip(_zip(tmp_path / "b.zip", GOOD))

    assert one.root != two.root


def test_the_zips_own_sha256_is_kept(tmp_path: Path) -> None:
    path = _zip(tmp_path / "a.zip", GOOD)

    assert stage_zip(path).sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path, _cache: Path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(b"<html>not a zip</html>")

    said = _refusal(lambda: stage_zip(path))

    assert "is not a zip file Yu'lon can read" in said
    assert _left(_cache) == []


# --- 7. member names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["../x.lua", "/abs.lua", "C:x.lua", "a/../../b.lua", "a/b.lua.", "a /c.lua"],
)
def test_a_member_that_would_land_outside_is_refused_and_nothing_is_left(
    tmp_path: Path, _cache: Path, bad: str
) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, bad: "x"})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"{bad!r} in the zip would land outside the add-on's folder.")
    assert _left(_cache) == []


def test_backslashes_are_the_separator_in_a_zip_with_no_slash(tmp_path: Path) -> None:
    """An old Windows tool writes `pfUI\\pfUI.toc`; with no `/` anywhere, `\\` is the separator."""
    path = _zip(tmp_path / "a.zip", {"pfUI\\pfUI.toc": "## Interface: 11200\n"})

    staged = stage_zip(path)

    assert (staged.root / "pfUI" / "pfUI.toc").is_file()


def test_a_dot_dot_spelled_with_backslashes_is_refused(tmp_path: Path, _cache: Path) -> None:
    path = _zip(tmp_path / "a.zip", {"pfUI\\pfUI.toc": "x", "a\\..\\..\\b.lua": "x"})

    said = _refusal(lambda: stage_zip(path))

    assert "would land outside the add-on's folder" in said
    assert _left(_cache) == []


def test_a_zip_that_mixes_slashes_and_backslashes_is_refused(tmp_path: Path, _cache: Path) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master\\extra.lua": "x"})

    said = _refusal(lambda: stage_zip(path))

    assert "mixes / and \\ in its names" in said
    assert _left(_cache) == []


def test_a_symlink_member_is_refused(tmp_path: Path, _cache: Path) -> None:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in GOOD.items():
            archive.writestr(name, data)
        link = zipfile.ZipInfo("pfUI-master/evil.lua")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/home/someone/.ssh/id_rsa")

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith("'pfUI-master/evil.lua' in the zip is a link, not a file")
    assert _left(_cache) == []


def test_two_names_that_fold_to_one_are_refused(tmp_path: Path, _cache: Path) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/PFUI.lua": "x"})

    said = _refusal(lambda: stage_zip(path))

    assert "differ only in case" in said
    assert _left(_cache) == []


def test_a_file_where_another_member_needs_a_folder_is_refused(
    tmp_path: Path, _cache: Path
) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/libs": "x", "pfUI-master/Libs/a": "x"})

    said = _refusal(lambda: stage_zip(path))

    assert (
        "'pfUI-master/libs' in the zip is a file where 'pfUI-master/Libs/a' needs a folder" in said
    )
    assert _left(_cache) == []


def test_a_member_deeper_than_the_cap_is_refused(tmp_path: Path, _cache: Path) -> None:
    deep = "/".join(["d"] * (addon_archive.MAX_DEPTH + 1)) + "/x.lua"
    path = _zip(tmp_path / "a.zip", {**GOOD, deep: "x"})

    said = _refusal(lambda: stage_zip(path))

    assert (
        f"is {addon_archive.MAX_DEPTH + 1} folders deep; Yu'lon takes add-ons up to "
        f"{addon_archive.MAX_DEPTH} folders deep." in said
    )
    assert _left(_cache) == []


def test_a_member_exactly_at_the_depth_cap_is_taken(tmp_path: Path) -> None:
    deep = "/".join(["d"] * addon_archive.MAX_DEPTH) + "/x.lua"

    staged = stage_zip(_zip(tmp_path / "a.zip", {**GOOD, deep: "x"}))

    assert (staged.root / deep).is_file()


# --- 7. sizes, counts and bombs -------------------------------------------------------------


def test_more_files_than_the_cap_are_refused_before_anything_is_written(
    tmp_path: Path, _cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addon_archive, "MAX_FILES", 3)
    written: list[str] = []
    monkeypatch.setattr(addon_archive, "_write_member", lambda *a, **k: written.append("x"))
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/a.lua": "x", "pfUI-master/b.lua": "x"})

    said = _refusal(lambda: stage_zip(path))

    assert "(4 files); Yu'lon takes add-ons up to" in said
    assert written == []
    assert _left(_cache) == []


def test_a_declared_total_over_the_cap_is_refused_before_anything_is_written(
    tmp_path: Path, _cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addon_archive, "MAX_UNPACKED_BYTES", 1000)
    written: list[str] = []
    monkeypatch.setattr(addon_archive, "_write_member", lambda *a, **k: written.append("x"))
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/big.lua": "-" * 1001})

    said = _refusal(lambda: stage_zip(path))

    assert "would unpack to" in said and "Yu'lon takes add-ons up to" in said
    assert written == []
    assert _left(_cache) == []


def test_bytes_are_counted_again_while_writing(
    tmp_path: Path, _cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The belt: a total the declared sizes understated still stops at the cap."""
    monkeypatch.setattr(addon_archive, "MAX_UNPACKED_BYTES", 1000)
    monkeypatch.setattr(addon_archive, "_declared_total", lambda _items: 0)
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/big.lua": "-" * 1001})

    said = _refusal(lambda: stage_zip(path))

    assert "would unpack to more than" in said
    assert _left(_cache) == []


def test_a_large_member_that_compresses_too_well_is_refused(tmp_path: Path, _cache: Path) -> None:
    size = addon_archive.BOMB_MIN_BYTES + 1
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/zeros.blp": b"\0" * size})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith("'pfUI-master/zeros.blp' in the zip unpacks to ")
    assert "which no add-on does" in said
    assert _left(_cache) == []


def test_a_small_member_that_compresses_well_is_taken(tmp_path: Path) -> None:
    """The ratio is asked only of members over the size floor: a 1 MB blank texture is normal."""
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/blank.blp": b"\0" * 1024 * 1024})

    staged = stage_zip(path)

    assert (staged.root / "pfUI-master" / "blank.blp").stat().st_size == 1024 * 1024


def test_a_large_member_that_does_not_compress_much_is_taken(tmp_path: Path) -> None:
    size = addon_archive.BOMB_MIN_BYTES + 1
    data = os.urandom(size)
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/art.blp": data})

    staged = stage_zip(path)

    assert (staged.root / "pfUI-master" / "art.blp").stat().st_size == size


def test_a_nested_zip_is_never_opened(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("Evil/Evil.toc", "## Interface: 11200\n")
    path = _zip(tmp_path / "a.zip", {"Pack/inner.zip": inner.getvalue()})

    staged = stage_zip(path)

    assert (staged.root / "Pack" / "inner.zip").read_bytes() == inner.getvalue()
    assert not any(p.name == "Evil.toc" for p in staged.root.rglob("*"))
    assert not isinstance(find_addons(staged.root, interface=11200, shipped={}), Found)


def test_there_must_be_room_for_the_unpacked_size(
    tmp_path: Path, _cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_packs, "_free_bytes", lambda _folder: 10)
    path = _zip(tmp_path / "a.zip", GOOD)

    said = _refusal(lambda: stage_zip(path))

    assert "of free space in" in said
    assert _left(_cache) == []


# --- 8. program files ---------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".dll", ".EXE", ".ps1", ".so", ".dylib", ".bat"])
def test_a_program_file_by_its_suffix_is_refused(tmp_path: Path, _cache: Path, suffix: str) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, f"pfUI-master/dinput8{suffix}": "plain text"})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"pfUI-master/dinput8{suffix} is a program file")
    assert _left(_cache) == []


def test_a_program_file_wearing_a_lua_suffix_is_refused(tmp_path: Path, _cache: Path) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/core.lua": _pe_stub()})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith("pfUI-master/core.lua is a program file")
    assert _left(_cache) == []


def test_a_lua_file_that_merely_mentions_mz_is_taken(tmp_path: Path) -> None:
    staged = stage_zip(_zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/a.lua": "-- MZ\n"}))

    assert (staged.root / "pfUI-master" / "a.lua").is_file()


# --- a folder the player chose --------------------------------------------------------------


def _folder(root: Path) -> Path:
    (root / "pfUI").mkdir(parents=True)
    (root / "pfUI" / "pfUI.toc").write_text("## Interface: 11200\n")
    (root / "pfUI" / "pfUI.lua").write_text("--\n")
    return root


def test_a_clean_folder_is_counted(tmp_path: Path) -> None:
    tree = check_folder(_folder(tmp_path / "src"))

    assert (tree.files, tree.bytes) == (2, len("## Interface: 11200\n") + len("--\n"))


def test_a_folder_with_a_program_file_is_refused(tmp_path: Path) -> None:
    root = _folder(tmp_path / "src")
    (root / "pfUI" / "x.lua").write_bytes(_pe_stub())

    said = _refusal(lambda: check_folder(root))

    assert said.startswith("pfUI/x.lua is a program file")


def test_a_folder_with_a_link_is_refused(tmp_path: Path) -> None:
    root = _folder(tmp_path / "src")
    (tmp_path / "secret").write_text("x")
    (root / "pfUI" / "x.lua").symlink_to(tmp_path / "secret")

    said = _refusal(lambda: check_folder(root))

    assert "pfUI/x.lua in the folder is a link to" in said


def test_a_folder_over_the_file_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addon_archive, "MAX_FILES", 1)

    said = _refusal(lambda: check_folder(_folder(tmp_path / "src")))

    assert "(2 files); Yu'lon takes add-ons up to" in said


def test_a_folder_over_the_size_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addon_archive, "MAX_UNPACKED_BYTES", 5)

    said = _refusal(lambda: check_folder(_folder(tmp_path / "src")))

    assert "would unpack to" in said


def test_a_folders_git_history_is_not_counted(tmp_path: Path) -> None:
    root = _folder(tmp_path / "src")
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "objects" / "pack.exe").write_bytes(b"MZ")

    assert check_folder(root).files == 2


# --- 10. links: the hosts, and every redirect -----------------------------------------------


class _Response:
    def __init__(self, body: bytes, *, status: int = 200, length: int | None = -1) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self._length = len(body) if length == -1 else length
        self.reads = 0

    def read1(self, amount: int, /) -> bytes:
        self.reads += 1
        return self._body.read(amount)

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        if name.lower() == "content-length" and self._length is not None:
            return str(self._length)
        return default

    def close(self) -> None:
        pass


def _zip_bytes(members: Mapping[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


class _Opener:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, frozenset[str]]] = []

    def __call__(
        self,
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Response:
        self.calls.append((url, hosts))
        return self.response


ARCHIVE = "https://github.com/shagu/pfUI/archive/refs/heads/master.zip"


def test_the_hosts_are_the_three_forges_and_githubs_download_hosts() -> None:
    """Owner Q2, 2026-10-09: GitHub, GitLab and Codeberg only; no CurseForge CDN."""
    assert addon_archive.ALLOWED_HOSTS == frozenset(
        {
            "github.com",
            "gitlab.com",
            "codeberg.org",
            "codeload.github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
    )


def test_a_zip_link_downloads_unpacks_and_leaves_no_download_behind(_cache: Path) -> None:
    opener = _Opener(_Response(_zip_bytes(GOOD)))

    staged = stage_link(ARCHIVE, opener=opener)

    assert opener.calls == [(ARCHIVE, addon_archive.ALLOWED_HOSTS)]
    assert (staged.root / "pfUI-master" / "pfUI.toc").is_file()
    assert staged.root.parent == _cache / "addons" / "staging"
    assert not list((_cache / "addons" / "downloads").iterdir())


@pytest.mark.parametrize(
    "url",
    [
        "https://www.curseforge.com/api/v1/mods/1/files/2/download",
        "https://evil.example/pfUI.zip",
        "https://github.com.evil.example/pfUI.zip",
    ],
)
def test_a_link_off_the_host_list_is_refused_before_any_request(url: str, _cache: Path) -> None:
    opener = _Opener(_Response(_zip_bytes(GOOD)))

    said = _refusal(lambda: stage_link(url, opener=opener))

    assert said.startswith("Yu'lon downloads add-ons only from GitHub, GitLab and Codeberg")
    assert "Download it in your browser, then choose the zip." in said
    assert opener.calls == []
    assert _left(_cache) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/shagu/pfUI/archive/refs/heads/master.zip",
        "https://github.com:8443/shagu/pfUI/archive/master.zip",
        "https://user@github.com/shagu/pfUI/archive/master.zip",
        "https://github.com/shagu/pfUI/../../x.zip",
    ],
)
def test_a_link_that_is_not_plain_https_is_refused_before_any_request(
    url: str, _cache: Path
) -> None:
    opener = _Opener(_Response(_zip_bytes(GOOD)))

    said = _refusal(lambda: stage_link(url, opener=opener))

    assert "is not a plain https link" in said
    assert opener.calls == []


def _redirecting(target: str, body: bytes) -> Callable[..., _Response]:
    """An opener whose server answers 302 to `target`, judged by the REAL redirect handler.

    The handler is built from `fetch._https_only_opener` with the hosts this module
    passed, exactly as `client_packs._open` builds it, so a change that stops passing
    the hosts lets the redirect through and this fixture then answers with `body`.
    """

    def opener(
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Response:
        built = fetch._https_only_opener(watcher, platform.verify_context(), hosts=hosts)
        handler = next(
            h for h in built.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
        )
        request = urllib.request.Request(url, method=method)
        followed = handler.redirect_request(request, None, 302, "Found", {}, target)
        assert followed is not None
        return _Response(body)

    return opener


def test_a_redirect_off_the_host_list_is_refused(_cache: Path) -> None:
    opener = _redirecting("https://evil.example/pfUI.zip", _zip_bytes(GOOD))

    said = _refusal(lambda: stage_link(ARCHIVE, opener=opener))

    assert "sent Yu'lon on to evil.example" in said
    assert said.count("Yu'lon downloads add-ons only from GitHub, GitLab and Codeberg") == 1
    assert _left(_cache) == []


def test_a_redirect_to_githubs_codeload_host_is_followed(_cache: Path) -> None:
    opener = _redirecting(
        "https://codeload.github.com/shagu/pfUI/zip/refs/heads/master", _zip_bytes(GOOD)
    )

    staged = stage_link(ARCHIVE, opener=opener)

    assert (staged.root / "pfUI-master" / "pfUI.toc").is_file()


def test_a_declared_size_over_the_cap_is_refused_before_the_body_is_read(_cache: Path) -> None:
    response = _Response(b"", length=addon_archive.MAX_DOWNLOAD_BYTES + 1)

    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(response)))

    assert "Yu'lon downloads add-ons up to 100 MB" in said
    assert response.reads == 0
    assert _left(_cache) == []


def test_an_undeclared_body_over_the_cap_is_stopped_at_the_cap(
    _cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addon_archive, "MAX_DOWNLOAD_BYTES", 100)
    response = _Response(b"x" * 1000, length=None)

    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(response)))

    assert "is larger than" in said
    assert response.reads < 1000
    assert _left(_cache) == []


def test_a_body_that_is_not_a_zip_is_refused(_cache: Path) -> None:
    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(_Response(b"<html></html>"))))

    assert "did not send a zip file" in said
    assert _left(_cache) == []


def test_an_http_error_status_is_refused(_cache: Path) -> None:
    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(_Response(b"gone", status=404))))

    assert "answered HTTP 404" in said
    assert _left(_cache) == []


def test_a_download_that_stalls_is_stopped(_cache: Path) -> None:
    class _Stalls(_Response):
        watcher: _Deadline | None = None

        def read1(self, amount: int, /) -> bytes:
            assert self.watcher is not None
            self.watcher.fired = True
            return b""

    response = _Stalls(b"", length=10)

    def opener(url: str, watcher: _Deadline, **_kw: object) -> _Response:
        response.watcher = watcher
        return response

    said = _refusal(lambda: stage_link(ARCHIVE, opener=opener))

    assert "sent nothing for" in said
    assert _left(_cache) == []


def test_a_short_body_is_refused(_cache: Path) -> None:
    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(_Response(b"abc", length=10))))

    assert "stopped at" in said
    assert _left(_cache) == []


def test_a_download_needs_room_for_its_declared_size(
    _cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the download folder's drive is short, so the unpack's own check cannot answer."""
    downloads = _cache / "addons" / "downloads"
    monkeypatch.setattr(
        client_packs, "_free_bytes", lambda folder: 1 if downloads in folder.parents else 10**12
    )
    response = _Response(_zip_bytes(GOOD))

    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(response)))

    assert said.startswith("master.zip needs ") and f"of free space in {downloads}" in said
    assert response.reads == 0
    assert _left(_cache) == []


def test_a_cancelled_download_leaves_nothing(_cache: Path) -> None:
    response = _Response(_zip_bytes(GOOD))

    with pytest.raises(addon_archive.AddonCancelled) as caught:
        stage_link(ARCHIVE, opener=_Opener(response), cancelled=lambda: True)

    assert str(caught.value) == "The download of master.zip was cancelled." + NOTHING
    assert response.reads == 0
    assert _left(_cache) == []


def test_a_cancelled_unpack_leaves_nothing_and_is_the_players_stop(
    tmp_path: Path, _cache: Path
) -> None:
    from yulon.after_stop import StopTookEffect

    path = _zip(tmp_path / "a.zip", GOOD)

    with pytest.raises(addon_archive.AddonCancelled) as caught:
        stage_zip(path, cancelled=lambda: True)

    assert str(caught.value) == "Unpacking a.zip was cancelled." + NOTHING
    assert isinstance(caught.value, StopTookEffect)
    assert _left(_cache) == []


def test_a_zip_link_whose_zip_is_unsafe_leaves_nothing(_cache: Path) -> None:
    body = _zip_bytes({**GOOD, "../evil.lua": "x"})

    said = _refusal(lambda: stage_link(ARCHIVE, opener=_Opener(_Response(body))))

    assert "would land outside" in said
    assert _left(_cache) == []


def test_a_member_locked_with_a_password_is_refused(tmp_path: Path, _cache: Path) -> None:
    """`zipfile` cannot write an encrypted member, so the central flag is set by hand."""
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/locked.lua": "x"})
    data = bytearray(path.read_bytes())
    entry = data.rindex(b"PK\x01\x02")  # the last central entry is the last member written
    assert (
        data[entry + 46 : entry + 46 + len(b"pfUI-master/locked.lua")] == b"pfUI-master/locked.lua"
    )
    data[entry + 8] |= 0x1
    path.write_bytes(bytes(data))

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith("'pfUI-master/locked.lua' in the zip is locked with a password")
    assert _left(_cache) == []


@pytest.mark.parametrize("bad", ["pfUI-master/a?.lua", "pfUI-master/CON.lua", "pfUI-master/a|b"])
def test_a_member_name_windows_cannot_hold_is_refused(
    tmp_path: Path, _cache: Path, bad: str
) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, bad: "x"})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"{bad!r} in the zip has a name a game client's folder on Windows")
    assert _left(_cache) == []


# --- review round 1 (2026-10-09) -----------------------------------------------------------


def _pe_stub() -> bytes:
    """The smallest shape of a Windows program: `MZ`, e_lfanew at 0x3C pointing at `PE\\0\\0`."""
    header = bytearray(0x40)
    header[0:2] = b"MZ"
    header[0x3C:0x40] = (0x40).to_bytes(4, "little")
    return bytes(header) + b"PE\0\0" + b"\x4c\x01" + bytes(18)


def test_a_damaged_lzma_member_is_a_refusal_not_a_raw_error(tmp_path: Path, _cache: Path) -> None:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_LZMA) as archive:
        archive.writestr("pfUI/pfUI.toc", "## Interface: 11200\n" * 50)
    data = bytearray(path.read_bytes())
    data[30 + len("pfUI/pfUI.toc") + 20] ^= 0xFF  # inside the LZMA stream
    path.write_bytes(bytes(data))

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith("a.zip is damaged or uses a kind of zip Yu'lon cannot read")
    assert _left(_cache) == []


def test_a_lua_file_that_starts_with_mz_is_taken(tmp_path: Path) -> None:
    """`MZ = 'Mozambique'` is Lua, not a program: only a real PE header is refused."""
    lua = b"MZ = 'Mozambique'\n" + b"-- " + b"x" * 2000 + b"\n"
    staged = stage_zip(_zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/countries.lua": lua}))

    assert (staged.root / "pfUI-master" / "countries.lua").read_bytes() == lua


@pytest.mark.parametrize("name", ["pfUI-master/core.lua", "pfUI-master/hook.asi"])
def test_a_real_windows_program_under_any_suffix_is_refused(
    tmp_path: Path, _cache: Path, name: str
) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, name: _pe_stub()})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"{name} is a program file (a Windows program inside)")
    assert _left(_cache) == []


def test_mz_whose_header_points_past_the_end_is_not_a_program(tmp_path: Path) -> None:
    stub = bytearray(_pe_stub())
    stub[0x3C:0x40] = (0x10000).to_bytes(4, "little")
    staged = stage_zip(_zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/x.blp": bytes(stub)}))

    assert (staged.root / "pfUI-master" / "x.blp").is_file()


def test_mz_whose_header_points_at_something_else_is_not_a_program(tmp_path: Path) -> None:
    stub = bytearray(_pe_stub())
    stub[0x40:0x44] = b"NOPE"
    staged = stage_zip(_zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/x.blp": bytes(stub)}))

    assert (staged.root / "pfUI-master" / "x.blp").is_file()


def test_a_windows_program_whose_header_is_past_the_first_kilobyte_is_refused(
    tmp_path: Path, _cache: Path
) -> None:
    """Past the buffered head but inside the file: cautious, it is taken for a program."""
    body = bytearray(4096)
    body[0:2] = b"MZ"
    body[0x3C:0x40] = (3000).to_bytes(4, "little")
    body[3000:3004] = b"PE\0\0"
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/big.lua": bytes(body)})

    said = _refusal(lambda: stage_zip(path))

    assert "is a program file (a Windows program inside)" in said


@pytest.mark.parametrize(
    ("magic", "kind"),
    [
        (b"\x7fELF\x02\x01\x01", "a Linux program inside"),
        (b"\xfe\xed\xfa\xce", "a macOS program inside"),
        (b"\xce\xfa\xed\xfe", "a macOS program inside"),
        (b"\xfe\xed\xfa\xcf", "a macOS program inside"),
        (b"\xcf\xfa\xed\xfe", "a macOS program inside"),
        (b"\xca\xfe\xba\xbe", "a macOS program inside"),
        (b"\xbe\xba\xfe\xca", "a macOS program inside"),
    ],
)
def test_linux_and_macos_programs_under_any_suffix_are_refused(
    tmp_path: Path, _cache: Path, magic: bytes, kind: str
) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, "pfUI-master/x.lua": magic + bytes(60)})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"pfUI-master/x.lua is a program file ({kind})")
    assert _left(_cache) == []


@pytest.mark.parametrize("suffix", [".lnk", ".js", ".hta", ".reg", ".jar"])
def test_more_program_suffixes_are_refused(tmp_path: Path, _cache: Path, suffix: str) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, f"pfUI-master/x{suffix}": "text"})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"pfUI-master/x{suffix} is a program file (a {suffix} file)")


@pytest.mark.parametrize(
    "bad", ["pfUI-master/CON .lua", "pfUI-master/COM¹.lua", "pfUI-master/lpt³"]
)
def test_device_names_with_a_space_or_a_superscript_are_refused(
    tmp_path: Path, _cache: Path, bad: str
) -> None:
    path = _zip(tmp_path / "a.zip", {**GOOD, bad: "x"})

    said = _refusal(lambda: stage_zip(path))

    assert said.startswith(f"{bad!r} in the zip has a name a game client's folder on Windows")
    assert _left(_cache) == []


def test_a_folder_with_a_lua_starting_with_mz_is_taken(tmp_path: Path) -> None:
    root = _folder(tmp_path / "src")
    (root / "pfUI" / "countries.lua").write_bytes(b"MZ = 'Mozambique'\n")

    assert check_folder(root).files == 3


def test_a_folder_with_a_real_windows_program_is_refused(tmp_path: Path) -> None:
    root = _folder(tmp_path / "src")
    (root / "pfUI" / "hook.asi").write_bytes(_pe_stub())

    said = _refusal(lambda: check_folder(root))

    assert said.startswith("pfUI/hook.asi is a program file (a Windows program inside)")


def test_a_folder_with_a_linux_program_is_refused(tmp_path: Path) -> None:
    root = _folder(tmp_path / "src")
    (root / "pfUI" / "x.lua").write_bytes(b"\x7fELF" + bytes(60))

    said = _refusal(lambda: check_folder(root))

    assert said.startswith("pfUI/x.lua is a program file (a Linux program inside)")


def test_a_fifo_in_a_folder_is_passed_over_not_read(tmp_path: Path) -> None:
    """Opening a FIFO for reading blocks until a writer comes; the check must never do it.

    Run on a thread with a bound, and a regression is unblocked by opening the FIFO's
    other end, so a reverted fix fails here instead of hanging the suite.
    """
    root = _folder(tmp_path / "src")
    fifo = root / "pfUI" / "pipe"
    os.mkfifo(fifo)
    got: list[object] = []
    worker = threading.Thread(target=lambda: got.append(check_folder(root)), daemon=True)

    worker.start()
    worker.join(10)
    hung = worker.is_alive()
    if hung:
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(10)

    assert not hung, "check_folder opened the FIFO and blocked"
    assert [tree.files for tree in got] == [2]  # type: ignore[attr-defined]
