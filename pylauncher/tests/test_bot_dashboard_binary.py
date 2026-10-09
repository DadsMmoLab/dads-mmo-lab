"""T542: the bot dashboard runs from TortoiseBots' prebuilt binary when one matches the module.

The switch (`controller_wow_tortoise.botdash`) used to always build the daemon
from the module checkout's `tools/observability` (a Go build). Now it first
looks for the newest TortoiseBots release, at or before the module's own
commit, that carries `tortoise-observability-linux-amd64`; proves the download
against the release's sha256 file; and builds a tiny image around it. It fails
closed: a binary that cannot be proved is never put in an image, and the Go
build is the way out, said in one line.

GitHub is replaced at the two seams the Dashboard takes (`http_get` for the API,
`open_url` for the downloads), Docker at `yulon.docker`'s functions.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.test_bot_dashboard import (
    TORTOISE,
    _base,
    _Docker,
    _install,
    _Lifecycle,
)
from yulon import docker
from yulon.catalog import bot_dashboard as files
from yulon.catalog.catalog import CatalogEntry
from yulon.controller_wow_tortoise import botdash, botpool
from yulon.controller_wow_tortoise import botdash_binary as binary

BINARY = b"\x7fELF pretend this is the daemon"
SUMS_NAME = "tortoise-observability-sha256.txt"
LINUX = "tortoise-observability-linux-amd64"
REV = "a" * 40


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sums(data: bytes = BINARY) -> bytes:
    other = f"{_digest(b'windows')}  tortoise-observability-windows-amd64.exe"
    return f"{_digest(data)}  {LINUX}\n{other}\n".encode()


def _tag_date(tag: str) -> str:
    """When the tag's commit was made: the changelog job's commit, early in the day."""
    return f"{tag[1:]}T08:44:08Z"


def _release(
    tag: str,
    *,
    binaries: bool = True,
    size: int | None = None,
    updated_at: str | None = None,
    state: str = "uploaded",
    draft: bool = False,
    prerelease: bool = False,
) -> dict[str, object]:
    assets: list[dict[str, object]] = []
    stamp = updated_at or f"{tag[1:]}T08:44:47Z"
    if binaries:
        assets = [
            {
                "name": LINUX,
                "size": len(BINARY) if size is None else size,
                "state": state,
                "updated_at": stamp,
            },
            {"name": SUMS_NAME, "size": 208, "state": "uploaded", "updated_at": stamp},
            {
                "name": "tortoise-observability-windows-amd64.exe",
                "size": 9,
                "state": "uploaded",
                "updated_at": stamp,
            },
        ]
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "created_at": f"{tag[1:]}T08:00:00Z",
        "assets": assets,
    }


class _GitHub:
    """The API and the release downloads, as two seams. Records every address asked."""

    def __init__(self, repo: str = "Sagiroth/TortoiseBots") -> None:
        self.repo = repo
        self.releases: list[dict[str, object]] = [_release("v2026-10-09")]
        self.behind: dict[str, int] = {}
        """`behind_by` of the compare `tag...rev`: 0 means the tag is at or before the rev."""
        self.api_error: OSError | None = None
        self.commit_dates: dict[str, str] = {}
        """The date of the commit a tag names; `_tag_date()` when a tag is not here."""
        self.files: dict[str, bytes] = {}
        self.urls: list[str] = []
        self.api: list[str] = []
        self.serve("v2026-10-09")

    def serve(self, tag: str, *, binary_bytes: bytes = BINARY, sums: bytes | None = None) -> None:
        base = f"https://github.com/{self.repo}/releases/download/{tag}/"
        self.files[base + LINUX] = binary_bytes
        self.files[base + SUMS_NAME] = _sums() if sums is None else sums

    def drop(self, tag: str, name: str) -> None:
        del self.files[f"https://github.com/{self.repo}/releases/download/{tag}/{name}"]

    def get(self, url: str, accept: str) -> bytes:
        self.api.append(url)
        assert f"/repos/{self.repo}/" in url, f"asked about another repository: {url}"
        if self.api_error is not None:
            raise self.api_error
        if "/releases?" in url:
            return json.dumps(self.releases).encode()
        if "/commits/" in url:
            tag = url.split("/commits/")[1]
            date = self.commit_dates.get(tag, _tag_date(tag))
            return json.dumps({"commit": {"committer": {"date": date}}}).encode()
        if "/compare/" in url:
            tag = url.split("/compare/")[1].split("...")[0]
            behind = self.behind.get(tag, 0)
            body = {"ahead_by": 5, "behind_by": behind, "status": "diverged" if behind else "ahead"}
            return json.dumps(body).encode()
        raise AssertionError(f"unexpected API address {url}")

    def open_url(self, url: str, _watcher: object) -> object:
        self.urls.append(url)
        if url not in self.files:
            raise OSError(f"HTTP Error 404: Not Found ({url})")
        return _Body(self.files[url])


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read1(self, amount: int, /) -> bytes:
        chunk, self._data = self._data[:amount], self._data[amount:]
        return chunk

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return default

    def close(self) -> None:
        return None


class _RecordingDocker(_Docker):
    """Remembers what each build's context folder held at the moment of the build."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(monkeypatch)
        self.contexts: list[dict[str, bytes]] = []

    def build_image(
        self, context: Path, tag: str, *, sink: Callable[[str], None] | None = None, **kw: object
    ) -> docker.AttachedRun:
        self.contexts.append({p.name: p.read_bytes() for p in context.iterdir() if p.is_file()})
        return super().build_image(context, tag, sink=sink, **kw)


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rev: str | None = REV,
    entry: CatalogEntry = TORTOISE,
    repo: str = "Sagiroth/TortoiseBots",
) -> tuple[Path, _RecordingDocker, _GitHub, botdash.Dashboard]:
    server_dir = _install(tmp_path)
    fake = _RecordingDocker(monkeypatch)
    github = _GitHub(repo)
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: rev)
    switch = botdash.Dashboard(
        entry,
        server_dir,
        _Lifecycle(),  # type: ignore[arg-type]
        http_get=github.get,
        open_url=github.open_url,  # type: ignore[arg-type]
    )
    return server_dir, fake, github, switch


def _staging(server_dir: Path) -> Path:
    return server_dir / binary.STAGING_DIR


def _on(switch: botdash.Dashboard) -> list[str]:
    return list(switch.switch_on(lan=False))


# -- the newest release at or before the module's commit ------------------------


def test_the_binary_is_proved_then_put_in_a_small_image_and_the_staging_is_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)

    said = _on(switch)

    image = files.image_ref(TORTOISE, server_dir)
    assert fake.calls == [f"build {binary.STAGING_DIR} {image}", "up tortoise-observability"]
    (context,) = fake.contexts
    assert context[LINUX] == BINARY
    dockerfile = context["Dockerfile"].decode()
    assert "COPY tortoise-observability-linux-amd64 /app/tortoise-observability" in dockerfile
    assert 'ENTRYPOINT ["/app/tortoise-observability"]' in dockerfile
    assert "EXPOSE 8095" in dockerfile and "EXPOSE 9195/udp" in dockerfile
    assert "golang" not in dockerfile, "no Go toolchain in the image"
    assert not _staging(server_dir).exists(), "the staged binary is not left in the server folder"
    assert any("prebuilt" in line and "v2026-10-09" in line for line in said), said
    assert not any("Go" in line for line in said), "no Go download is announced"
    # Only these three kinds of address were asked for, all over https.
    assert github.urls == [
        "https://github.com/Sagiroth/TortoiseBots/releases/download/v2026-10-09/" + SUMS_NAME,
        "https://github.com/Sagiroth/TortoiseBots/releases/download/v2026-10-09/" + LINUX,
    ]
    assert all(
        u.startswith("https://api.github.com/repos/Sagiroth/TortoiseBots/") for u in github.api
    )


def test_the_newest_release_at_or_before_the_modules_commit_is_the_one_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [
        _release("v2026-10-12"),
        _release("v2026-10-11", binaries=False),
        _release("v2026-10-10"),
        _release("v2026-10-09"),
    ]
    github.behind = {"v2026-10-12": 4}  # this release is NEWER than the module's commit
    for tag in ("v2026-10-12", "v2026-10-10", "v2026-10-09"):
        github.serve(tag)

    said = _on(switch)

    assert github.urls[-1].endswith("/v2026-10-10/" + LINUX), github.urls
    assert any("v2026-10-10" in line for line in said)
    assert not any("/v2026-10-12/" in u for u in github.urls), "a newer release is never fetched"


def test_a_release_without_the_binaries_is_never_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("v2026-10-11", binaries=False), _release("v2026-10-09")]

    _on(switch)

    assert all("/v2026-10-11/" not in u for u in github.urls)
    assert any("/v2026-10-09/" in u for u in github.urls)


def test_a_module_older_than_every_prebuilt_release_builds_from_source_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.behind = {"v2026-10-09": 7}

    said = _on(switch)

    image = files.image_ref(TORTOISE, server_dir)
    assert fake.calls == [f"build observability {image}", "up tortoise-observability"]
    assert github.urls == [], "nothing is downloaded when no release is at or before the commit"
    why = [line for line in said if "prebuilt" in line]
    assert len(why) == 1 and "Go" in why[0], said
    assert "from the bots module's own source" in why[0]
    assert files.state(server_dir).on


def test_a_module_commit_nobody_could_read_builds_from_source_without_asking_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch, rev=None)

    said = _on(switch)

    assert github.api == [] and github.urls == []
    assert fake.calls[0].startswith("build observability ")
    assert any("prebuilt" in line for line in said)


def test_github_not_answering_builds_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.api_error = OSError("network is unreachable")

    said = _on(switch)

    assert fake.calls[0].startswith("build observability ")
    assert any("prebuilt" in line and "GitHub" in line for line in said), said
    assert files.state(server_dir).on


# -- fail closed ----------------------------------------------------------------


def test_a_binary_that_does_not_match_its_published_checksum_is_never_built_into_an_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.serve("v2026-10-09", binary_bytes=BINARY + b"tampered")
    github.releases = [_release("v2026-10-09", size=len(BINARY) + 8)]

    said = _on(switch)

    assert [c for c in fake.contexts if LINUX in c] == [], "the unproved binary was never built in"
    assert fake.calls[0].startswith("build observability "), "the Go build is the way out"
    assert not _staging(server_dir).exists(), "the unproved file is deleted, not left"
    assert any("checksum" in line and "prebuilt" in line for line in said), said


def test_a_release_whose_checksum_file_is_missing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.drop("v2026-10-09", SUMS_NAME)

    said = _on(switch)

    assert [c for c in fake.contexts if LINUX in c] == []
    assert github.urls == [
        "https://github.com/Sagiroth/TortoiseBots/releases/download/v2026-10-09/" + SUMS_NAME
    ], "the binary is not even downloaded without a checksum to hold it to"
    assert fake.calls[0].startswith("build observability ")
    assert any("prebuilt" in line and "checksum" in line for line in said), said


def test_a_checksum_file_that_does_not_list_the_binary_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.serve("v2026-10-09", sums=f"{_digest(BINARY)}  something-else\n".encode())

    _on(switch)

    assert [c for c in fake.contexts if LINUX in c] == []
    assert fake.calls[0].startswith("build observability ")


def test_a_download_that_fails_midway_builds_from_source_and_leaves_no_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.drop("v2026-10-09", LINUX)

    said = _on(switch)

    assert fake.calls[0].startswith("build observability ")
    assert not _staging(server_dir).exists()
    assert any("prebuilt" in line for line in said)


def test_an_asset_size_that_is_absurd_is_refused_before_anything_is_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("v2026-10-09", size=10**12)]

    _on(switch)

    assert not any(u.endswith(LINUX) for u in github.urls)
    assert fake.calls[0].startswith("build observability ")


def test_a_tag_that_could_re_point_the_address_is_never_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("../../evil/repo/releases/download/v1")]

    _on(switch)

    assert github.urls == []
    assert fake.calls[0].startswith("build observability ")


def test_stopping_during_the_download_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    cancel = threading.Event()
    cancel.set()
    before = _base(server_dir)

    with pytest.raises(botdash.SwitchStopped):
        list(switch.switch_on(lan=False, cancel=cancel))

    assert fake.calls == []
    assert _base(server_dir) == before
    assert not _staging(server_dir).exists()
    assert not files.state(server_dir).on


# -- the wiring does not move ---------------------------------------------------


def test_the_service_block_is_the_same_whichever_way_the_image_was_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path / "bin", monkeypatch)
    _on(switch)
    from_binary = files.block_in(_base(server_dir))

    other_dir, fake2, github2, switch2 = _setup(tmp_path / "src", monkeypatch, rev=None)
    _on(switch2)
    from_source = files.block_in(_base(other_dir))

    assert from_binary is not None and from_source is not None
    # The two installs sit in different folders; only the image tag carries it.

    def norm(text: str, folder: Path) -> str:
        return text.replace(files.image_ref(TORTOISE, folder), "IMAGE")

    assert norm(from_binary, server_dir) == norm(from_source, other_dir)
    assert "tortoise-observability:" in from_binary
    assert '"127.0.0.1:8095:8095"' in from_binary
    assert "DB_PASSWORD: ${DB_ROOT_PASSWORD:?" in from_binary
    assert "SESSION_SECRET:" in from_binary


def test_off_removes_the_image_and_the_staging_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    _on(switch)

    list(switch.switch_off())

    assert not _staging(server_dir).exists()
    assert not files.state(server_dir).on
    assert fake.calls[-2:] == [
        "rm tortoise-observability",
        f"rmi {files.image_ref(TORTOISE, server_dir)}",
    ]


# -- an update of the module re-picks -------------------------------------------


def test_the_update_re_picks_the_binary_for_the_module_it_moved_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    # Switched on while the module sat BEFORE the first prebuilt release...
    github.behind = {"v2026-10-09": 3}
    _on(switch)
    assert fake.calls[0].startswith("build observability ")
    fake.calls.clear()
    fake.contexts.clear()
    # ...and the update moves it to a commit at or after v2026-10-09.
    github.behind = {}
    heads = {"module": "b" * 40}
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: heads["module"])

    def update(_cancel: threading.Event | None) -> Iterator[str]:
        heads["module"] = "c" * 40
        yield "engine: updated"

    said = list(botdash.after_update(update, None, dashboard=switch))

    assert any("/v2026-10-09/" + LINUX in u for u in github.urls)
    (context,) = fake.contexts
    assert context[LINUX] == BINARY
    assert any("prebuilt" in line and "v2026-10-09" in line for line in said)
    assert not _staging(server_dir).exists()
    assert any(u.endswith("..." + "c" * 40 + "?per_page=1&page=2") for u in github.api), github.api


def test_a_rebuild_whose_binary_is_refused_falls_back_to_the_go_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    _on(switch)
    fake.calls.clear()
    fake.contexts.clear()
    github.serve("v2026-10-09", binary_bytes=b"not what the checksum says")
    github.releases = [_release("v2026-10-09", size=len(b"not what the checksum says"))]

    said = list(switch.rebuild())

    assert fake.calls[0].startswith("build observability ")
    assert [c for c in fake.contexts if LINUX in c] == []
    assert any("prebuilt" in line and "checksum" in line for line in said)
    assert not _staging(server_dir).exists()


# -- upstream re-uploads the day's assets on every push (the owner's guard) ------


def test_a_binary_uploaded_long_after_its_tag_was_made_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tag moves only when the changelog job succeeds; the binary may be a later push's."""
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("v2026-10-09", updated_at="2026-10-09T10:44:09Z")]  # +2 h 1 s

    said = _on(switch)

    assert github.urls == [], "nothing is downloaded from a release whose binary outran its tag"
    assert fake.calls[0].startswith("build observability ")
    why = [line for line in said if "prebuilt" in line]
    assert len(why) == 1 and "after" in why[0] and "v2026-10-09" in why[0], said


def test_a_binary_uploaded_within_two_hours_of_its_tag_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("v2026-10-09", updated_at="2026-10-09T10:44:08Z")]  # +2 h exactly

    _on(switch)

    assert any(u.endswith(LINUX) for u in github.urls)


def test_a_release_that_outran_its_tag_is_skipped_for_an_older_one_that_did_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [
        _release("v2026-10-10", updated_at="2026-10-10T23:00:00Z"),
        _release("v2026-10-09"),
    ]
    github.serve("v2026-10-10")

    _on(switch)

    assert github.urls[-1].endswith("/v2026-10-09/" + LINUX), github.urls
    assert not any("/v2026-10-10/" in u for u in github.urls)


def test_a_tag_whose_commit_date_cannot_be_read_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.commit_dates["v2026-10-09"] = "not a date"

    _on(switch)

    assert github.urls == []
    assert fake.calls[0].startswith("build observability ")


# -- the filters on what a release says about itself ------------------------------


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_a_draft_or_pre_release_is_never_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("v2026-10-10", **{flag: True}), _release("v2026-10-09")]
    github.serve("v2026-10-10")

    _on(switch)

    assert not any("/v2026-10-10/" in u for u in github.urls)
    assert any("/v2026-10-09/" in u for u in github.urls)


def test_an_asset_that_is_not_uploaded_yet_is_not_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    github.releases = [_release("v2026-10-10", state="starter"), _release("v2026-10-09")]
    github.serve("v2026-10-10")

    _on(switch)

    assert not any("/v2026-10-10/" in u for u in github.urls)
    assert any("/v2026-10-09/" in u for u in github.urls)


def test_only_the_newest_ten_releases_are_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)

    _on(switch)

    assert "per_page=10" in github.api[0]


# -- a disk that refuses the staging folder -----------------------------------------


def test_a_dockerfile_that_cannot_be_written_falls_back_to_the_go_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    real = Path.write_text

    def refuse(self: Path, *args: object, **kwargs: object) -> int:
        if self.name == "Dockerfile" and self.parent.name == binary.STAGING_DIR:
            raise OSError(28, "No space left on device")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", refuse)

    said = _on(switch)

    assert fake.calls[0].startswith("build observability ")
    assert not _staging(server_dir).exists()
    assert any("prebuilt" in line and "No space" in line for line in said), said


def test_a_staging_folder_that_cannot_be_made_falls_back_to_the_go_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    _staging(server_dir).write_text("a file where the folder goes", encoding="utf-8")
    real = binary.shutil.rmtree
    monkeypatch.setattr(binary.shutil, "rmtree", lambda *_a, **_kw: None)  # cannot clear it

    said = _on(switch)

    monkeypatch.setattr(binary.shutil, "rmtree", real)
    assert fake.calls[0].startswith("build observability ")
    assert any("prebuilt" in line for line in said)


# -- whose releases ----------------------------------------------------------------


def _entry_with_bots_from(repo: str) -> CatalogEntry:
    sources = [
        s.model_copy(update={"repo": repo}) if s.dest.endswith("TortoiseBots") else s
        for s in TORTOISE.emulator.sources
    ]
    emulator = TORTOISE.emulator.model_copy(update={"sources": sources})
    return TORTOISE.model_copy(update={"emulator": emulator})


def test_the_releases_come_from_the_repository_the_catalog_clones_the_module_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry_with_bots_from("someone/TortoiseBots-fork")
    server_dir, fake, github, switch = _setup(
        tmp_path, monkeypatch, entry=entry, repo="someone/TortoiseBots-fork"
    )

    said = _on(switch)

    assert github.urls[-1] == (
        "https://github.com/someone/TortoiseBots-fork/releases/download/v2026-10-09/" + LINUX
    )
    assert any("v2026-10-09" in line for line in said)


def test_a_module_that_does_not_come_from_github_has_no_prebuilt_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry_with_bots_from("https://codeberg.org/someone/TortoiseBots")
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch, entry=entry)

    said = _on(switch)

    assert github.api == [] and github.urls == []
    assert fake.calls[0].startswith("build observability ")
    assert any("prebuilt" in line and "GitHub" in line for line in said), said


# -- a Stop in a rebuild is a stopped build, not a switch that never was on -----------


def test_a_stop_during_the_binary_download_of_a_rebuild_is_the_stopped_build_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    _on(switch)
    fake.calls.clear()
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(botdash.RebuildLeftOwed) as stopped:
        list(switch.rebuild(cancel))

    assert "the build was stopped" in str(stopped.value)
    assert "Stopped before anything was changed" not in str(stopped.value)
    assert "still off" not in str(stopped.value)
    assert not any(call.startswith("up ") for call in fake.calls)
    assert files.state(server_dir).on, "the switch was on and stays on"
    assert not _staging(server_dir).exists()


def test_a_stop_during_the_binary_download_of_an_update_rebuild_stops_the_old_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fake, github, switch = _setup(tmp_path, monkeypatch)
    _on(switch)
    fake.calls.clear()
    heads = {"module": "b" * 40}
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: heads["module"])
    cancel = threading.Event()

    def update(_cancel: threading.Event | None) -> Iterator[str]:
        heads["module"] = "c" * 40
        cancel.set()  # the player presses Stop as the update ends
        yield "engine: updated"

    said = list(botdash.after_update(update, cancel, dashboard=switch))

    text = "\n".join(said)
    assert "the build was stopped" in text
    assert "Stopped before anything was changed" not in text
    assert "rm tortoise-observability" in fake.calls, "the old dashboard is stopped (T162)"
    assert files.rebuild_owed(server_dir) is not None
