"""A GitHub repo's latest release as a client pack (`github_release`, T853).

No network: `_Site` is the fake opener, holding the release JSON per URL and
the asset bytes, and recording every request so a test can say what was NOT
asked. The cache is pointed at `tmp_path` by moving `platform.config_dir()`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import client_packs, platform
from yulon.catalog.catalog import ClientPack, load_catalog
from yulon.client_packs import (
    GITHUB_RELEASE_MAX_BYTES,
    PackError,
    PackUnavailable,
    fetch_github_release,
)

REPO = "SuttonX/ConsolePortLK-Enhanced"
ASSET = "ConsolePortLK-Enhanced.zip"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD = f"https://github.com/{REPO}/releases/download/v1.0.6/{ASSET}"
HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    monkeypatch.setattr(platform, "config_dir", lambda: config)
    return config / "client-packs"


def _zip(members: Mapping[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in (
            members or {"ConsolePort/ConsolePort.toc": b"## Interface: 30300"}
        ).items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


DATA = _zip()
DIGEST = hashlib.sha256(DATA).hexdigest()


def _release(
    tag: str = "v1.0.6",
    *,
    asset: str = ASSET,
    download: str = DOWNLOAD,
    digest: str | None = f"sha256:{DIGEST}",
    size: int | None = None,
    data_len: int | None = None,
    assets: Any = "default",
) -> bytes:
    entry: dict[str, Any] = {
        "name": asset,
        "browser_download_url": download,
        "size": len(DATA) if data_len is None else data_len,
    }
    if digest is not None:
        entry["digest"] = digest
    if size is not None:
        entry["size"] = size
    body: dict[str, Any] = {"tag_name": tag, "assets": [entry] if assets == "default" else assets}
    return json.dumps(body).encode()


def _pack(**extra: Any) -> ClientPack:
    return ClientPack.model_validate(
        {
            "id": "consoleport",
            "label": "Controller support (ConsolePort)",
            "source": {"kind": "github_release", "repo": REPO, "asset": ASSET},
            "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
            "optional": True,
            **extra,
        }
    )


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes
    chunk: int = 4096
    _pos: int = 0

    def read1(self, amount: int, /) -> bytes:
        end = min(len(self.body), self._pos + min(amount, self.chunk))
        data = self.body[self._pos : end]
        self._pos = end
        return data

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return self.headers.get(name, default)

    def close(self) -> None:
        pass


@dataclass
class _Site:
    """The release answer and the asset bytes, per URL; records every request."""

    files: dict[str, bytes]
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

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
        assert HOSTS <= hosts, hosts
        if url not in self.files:
            return _Response(404, {}, b"")
        data = self.files[url]
        if method == "HEAD":
            return _Response(200, {"Content-Length": str(len(data))}, b"")
        start = 0
        status = 200
        reply: dict[str, str] = {}
        wanted = headers.get("Range")
        if wanted:
            start = int(wanted.removeprefix("bytes=").removesuffix("-"))
            status = 206
            reply["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
        body = data[start:]
        reply["Content-Length"] = str(len(body))
        return _Response(status, reply, body)

    def gets(self, url: str = DOWNLOAD) -> list[dict[str, str]]:
        return [h for (m, u, h) in self.requests if m == "GET" and u == url]


def _site(release: bytes = _release(), data: bytes = DATA) -> _Site:
    return _Site({API: release, DOWNLOAD: data})


# -- the model ------------------------------------------------------------------


def test_github_release_needs_a_repo_and_an_asset() -> None:
    with pytest.raises(Exception, match="repo and an asset"):
        _pack(source={"kind": "github_release", "repo": REPO})  # type: ignore[typeddict-unknown-key]


def test_github_release_refuses_a_url_or_version_url() -> None:
    with pytest.raises(Exception, match="no path, url or version_url"):
        ClientPack.model_validate(
            {
                "id": "consoleport",
                "label": "Controller support (ConsolePort)",
                "source": {
                    "kind": "github_release",
                    "repo": REPO,
                    "asset": ASSET,
                    "url": "https://example.org/x.zip",
                },
                "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
                "optional": True,
            }
        )


@pytest.mark.parametrize("repo", ["SuttonX", "a/b/c", "", "a b/c", "a//b"])
def test_a_bad_repo_is_refused(repo: str) -> None:
    with pytest.raises(Exception, match="owner and a name"):
        ClientPack.model_validate(
            {
                "id": "consoleport",
                "label": "Controller support (ConsolePort)",
                "source": {"kind": "github_release", "repo": repo, "asset": ASSET},
                "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
                "optional": True,
            }
        )


def test_other_kinds_refuse_a_repo_and_no_checksum_is_needed() -> None:
    with pytest.raises(Exception, match="no repo or asset"):
        ClientPack.model_validate(
            {
                "id": "x",
                "label": "X",
                "source": {"kind": "url", "url": "https://example.org/x.zip", "repo": REPO},
                "install": [{"member": "a", "to": "a"}],
            }
        )
    pack = _pack()
    assert pack.sha256 is None and pack.md5 is None
    with pytest.raises(Exception, match="takes no pinned checksum"):
        _pack(sha256="0" * 64)


# -- the fetch ------------------------------------------------------------------


def test_the_release_asset_downloads_and_proves_against_its_digest() -> None:
    site = _site()

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got.version == "v1.0.6"
    assert got.path == client_packs.cache_dir() / "wotlk" / "consoleport" / "v1.0.6" / ASSET
    assert got.path.read_bytes() == DATA
    assert got.sha256 == DIGEST
    assert [u for (_, u, _) in site.requests] == [API, DOWNLOAD]


def test_the_second_call_with_the_same_tag_downloads_nothing() -> None:
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    site.requests.clear()

    second = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert second == first
    assert [u for (_, u, _) in site.requests] == [API]  # the tag, nothing else
    assert site.gets() == []


def test_a_new_tag_downloads_again_into_its_own_folder() -> None:
    site = _site()
    before = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    new_data = _zip({"ConsolePort/ConsolePort.toc": b"## Interface: 30300\n## Version: 2"})
    new_digest = hashlib.sha256(new_data).hexdigest()
    site.files[API] = _release(
        tag="v1.0.7",
        digest=f"sha256:{new_digest}",
        download="https://github.com/x/y/v1.0.7.zip",
        data_len=len(new_data),
    )
    site.files["https://github.com/x/y/v1.0.7.zip"] = new_data
    site.requests.clear()

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got.version == "v1.0.7"
    assert got.sha256 == new_digest != before.sha256
    assert got.path.read_bytes() == new_data
    assert site.gets("https://github.com/x/y/v1.0.7.zip") != []


def test_a_digest_mismatch_refuses_and_keeps_no_file() -> None:
    site = _site(release=_release(digest="sha256:" + "0" * 64))
    folder = client_packs.cache_dir() / "wotlk" / "consoleport" / "v1.0.6"

    with pytest.raises(PackError, match="does not match its published checksum"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert list(folder.iterdir()) == []


def test_a_cached_zip_that_no_longer_proves_is_downloaded_again() -> None:
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    first.path.write_bytes(_zip({"other.txt": b"changed"}))
    site.requests.clear()

    second = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert second == first
    assert site.gets() != []


def test_a_release_without_the_asset_is_refused_naming_it() -> None:
    site = _site(release=_release(assets=[{"name": "other.zip"}]))

    with pytest.raises(PackError, match=ASSET):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert not client_packs.cache_dir().exists()


def test_a_release_without_a_digest_is_refused() -> None:
    site = _site(release=_release(digest=None))

    with pytest.raises(PackError, match="published no checksum for"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert not client_packs.cache_dir().exists()


@pytest.mark.parametrize("digest", ["sha256:xyz", "md5:" + "0" * 32, "sha256:" + "0" * 63])
def test_a_malformed_digest_is_refused(digest: str) -> None:
    site = _site(release=_release(digest=digest))

    with pytest.raises(PackError, match="published no checksum"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)


def test_an_unreachable_release_keeps_the_cached_version_with_the_sentence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The outage keeps the newest VERIFIED cache; the sentence names the outage (T853 G1)."""
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    class _Broken:
        def __init__(self, site: _Site) -> None:
            self.site = site

        def __call__(self, url: str, watcher: _Deadline, **kw: Any) -> _Response:
            if url == API:
                raise ConnectionError("down")
            return self.site(url, watcher, **kw)

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=_Broken(site))

    assert got == first
    assert (
        "Could not use Controller support (ConsolePort)'s newest release "
        "(Controller support (ConsolePort): the server's site could not be reached (down). "
        "Check the connection and try again.); kept v1.0.6." in caplog.text
    )


def test_a_garbled_release_keeps_the_cached_version() -> None:
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    site.files[API] = b"<html>not json</html>"
    site.requests.clear()

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got == first
    assert [u for (_, u, _) in site.requests] == [API]


def test_an_unreachable_release_without_a_cache_is_a_plain_sentence() -> None:
    class _Broken:
        def __call__(self, url: str, watcher: _Deadline, **kw: Any) -> _Response:
            raise ConnectionError("down")

    with pytest.raises(PackError, match="could not be reached"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=_Broken())


def test_a_garbled_release_without_a_cache_is_a_plain_sentence() -> None:
    site = _Site({API: b"<html>not json</html>"})

    with pytest.raises(PackError, match="is not JSON"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)


def test_an_asset_host_outside_the_fence_is_refused_before_any_download() -> None:
    site = _site(
        release=_release(download="https://evil.example.org/ConsolePortLK-Enhanced.zip"),
    )

    with pytest.raises(PackError, match="not a plain https address on a host"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert site.gets("https://evil.example.org/ConsolePortLK-Enhanced.zip") == []


def test_a_non_github_download_host_is_refused_even_when_listed() -> None:
    site = _site(
        release=_release(download="https://evil.example.org/ConsolePortLK-Enhanced.zip"),
    )
    evil_hosts = HOSTS | {"evil.example.org"}

    with pytest.raises(PackError, match="not a plain https address on a host"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=evil_hosts, opener=site)
    assert site.gets("https://evil.example.org/ConsolePortLK-Enhanced.zip") == []


# -- the catalog -----------------------------------------------------------------


def test_the_three_entries_carry_the_pack_and_coa_does_not() -> None:
    catalog = load_catalog().games
    by_id = {game.id: game for game in catalog}
    for game_id in ("wow-wotlk", "wow-unbound", "wow-centurion"):
        packs = {pack.id: pack for pack in by_id[game_id].client.packs}
        pack = packs["consoleport"]
        assert pack.label == "Controller support (ConsolePort)"
        assert pack.optional is True and pack.default is False
        assert pack.platforms == ()
        assert (pack.source.kind, pack.source.repo, pack.source.asset) == (
            "github_release",
            REPO,
            ASSET,
        )
        assert pack.size_hint == 13646207
        assert [(rule.member, rule.to_dir) for rule in pack.install] == [("*", "Interface/AddOns")]
    assert by_id["wow-coa"].client.packs == ()


def test_the_fence_names_the_release_and_asset_hosts() -> None:
    for game in load_catalog().games:
        if game.id in ("wow-wotlk", "wow-unbound", "wow-centurion"):
            pack = next(pack for pack in game.client.packs if pack.id == "consoleport")
            assert HOSTS <= game.client.pack_hosts(pack), game.id
            assert game.client.hosts() != game.client.pack_hosts(pack), game.id
        if game.id == "wow-coa":
            assert "api.github.com" not in game.client.hosts()


# -- the review round 1 fallback (G1-G3) ---------------------------------------------------
def _note(folder, *, tag, hours_old=0.0):
    """A fetch note as `_note_fetch` would have left it, `hours_old` hours back."""
    import datetime

    at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours_old)
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath(".yulon-github-release.json").write_text(
        json.dumps(
            {
                "tag": tag,
                "asset": ASSET,
                "size": len(DATA),
                "sha256": hashlib.sha256(folder.joinpath(ASSET).read_bytes()).hexdigest(),
                "verified_at": at.isoformat(timespec="seconds"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _lay_verified(tag, data=DATA):
    """A cache folder as a successful fetch would have left it: the zip and its note."""
    folder = client_packs.cache_dir() / "wotlk" / "consoleport" / tag
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath(ASSET).write_bytes(data)
    _note(folder, tag=tag)
    return folder


def test_a_tampered_but_valid_zip_in_cache_is_refused():
    """G1: the note's digest proves; the archive's own CRCs alone do not stand in."""
    folder = _lay_verified("v1.0.6")
    folder.joinpath(ASSET).write_bytes(_zip({"other.txt": b"changed"}))

    class _Down:
        def __call__(self, url, watcher, **kw):
            raise ConnectionError("down")

    with pytest.raises(PackUnavailable, match="could not be reached"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=_Down())


def test_the_newer_verified_at_wins_whatever_the_name():
    """G1: the fallback is newest by note, not by folder name."""
    _lay_verified("v1.10")
    old_data = _zip({"ConsolePort/ConsolePort.toc": b"## v1.9\n"})
    old_folder = _lay_verified("v1.9", old_data)
    _note(old_folder, tag="v1.9", hours_old=3)

    class _Down:
        def __call__(self, url, watcher, **kw):
            raise ConnectionError("down")

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=_Down())

    assert got.version == "v1.10"
    assert got.path.read_bytes() == DATA


def test_a_folder_without_a_note_is_not_a_cached_version():
    """G1: a note-less folder is ignored, even when its zip would prove."""
    folder = client_packs.cache_dir() / "wotlk" / "consoleport" / "v1.0.6"
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath(ASSET).write_bytes(DATA)

    class _Down:
        def __call__(self, url, watcher, **kw):
            raise ConnectionError("down")

    with pytest.raises(PackUnavailable, match="could not be reached"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=_Down())


def test_a_missing_asset_keeps_the_verified_cache_with_the_sentence(caplog):
    """G2: no asset of that name is not a reason to stop Play for everyone."""
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    site.files[API] = _release(assets=[{"name": "other.zip"}])
    site.requests.clear()

    with caplog.at_level("INFO", logger="yulon.client_packs"):
        got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got == first
    assert (
        "Could not use Controller support (ConsolePort)'s newest release "
        f"(Controller support (ConsolePort): its latest release has no {ASSET} asset. "
        "Try again later.); kept v1.0.6." in caplog.text
    )


def test_a_missing_asset_without_a_cache_is_unavailable_like_a_404():
    """G2: without a verified cache, the missing asset refuses as a url pack's 404 does."""
    site = _site(release=_release(assets=[{"name": "other.zip"}]))

    with pytest.raises(PackUnavailable, match="has no ConsolePortLK-Enhanced.zip asset"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert not (client_packs.cache_dir() / "wotlk").exists()


def test_a_release_without_a_digest_keeps_the_verified_cache():
    """G2: a checksum-less release keeps the verified cache, too."""
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    site.files[API] = _release(digest=None)
    site.requests.clear()

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got == first
    assert [u for (_, u, _) in site.requests] == [API]


def test_a_release_without_a_digest_and_without_a_cache_is_unavailable():
    """G2: without a verified cache, no digest refuses as a url pack's 404 does."""
    site = _site(release=_release(digest=None))

    with pytest.raises(PackUnavailable, match="published no checksum"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert not client_packs.cache_dir().exists()


def test_an_asset_over_64_mib_is_unavailable_before_any_download():
    """G3: the release's own size bounds the asset; a verified cache still stands by."""
    site = _site(release=_release(size=GITHUB_RELEASE_MAX_BYTES + 1))

    with pytest.raises(PackUnavailable, match="over the 64 MB Yu'lon downloads"):
        fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    assert site.gets() == []
    assert not client_packs.cache_dir().exists()


def test_an_oversized_release_keeps_the_verified_cache():
    """G3: an over-size release keeps the verified cache with the kept sentence."""
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    site.files[API] = _release(tag="v1.0.7", size=GITHUB_RELEASE_MAX_BYTES + 1)
    site.requests.clear()

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got == first
    assert site.gets("https://github.com/x/y/v1.0.7.zip") == []


def test_a_failing_note_write_keeps_the_old_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: a failure after the note temp is opened leaves the old note intact."""
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    folder = first.path.parent
    before = (folder / ".yulon-github-release.json").read_bytes()
    real_replace = os.replace

    def _refusing_replace(src: object, dst: object, *a: object, **k: object) -> None:
        if str(dst).endswith(".yulon-github-release.json"):
            raise OSError(28, "No space left on device")
        real_replace(src, dst, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", _refusing_replace)
    site.files[API] = _release(tag="v1.0.7", digest=f"sha256:{DIGEST}")
    site.files[f"https://github.com/{REPO}/releases/download/v1.0.7/{ASSET}"] = DATA

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got.version == "v1.0.7"
    assert (folder / ".yulon-github-release.json").read_bytes() == before
    assert not list(folder.glob("*.yulon-tmp"))


def test_a_reverify_with_a_failing_note_write_keeps_the_old_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1: re-verify of a cached archive with a failing note write keeps the old note."""
    site = _site()
    first = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)
    folder = first.path.parent
    before = (folder / ".yulon-github-release.json").read_bytes()
    real_replace = os.replace

    def _refusing_replace(src: object, dst: object, *a: object, **k: object) -> None:
        if str(dst).endswith(".yulon-github-release.json"):
            raise OSError(28, "No space left on device")
        real_replace(src, dst, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", _refusing_replace)

    got = fetch_github_release(_pack(), entry_id="wotlk", allowed_hosts=HOSTS, opener=site)

    assert got == first
    assert (folder / ".yulon-github-release.json").read_bytes() == before
    assert not list(folder.glob("*.yulon-tmp"))
