"""Tests for the process install engine (`yulon.catalog.process_install`).

Every external effect is a `ProcessSeams` fake: the "downloads" copy zips built here,
and `go build` is a stream that writes the program it was asked for. What these prove
is the engine's own control flow: the stage order, the checks on every archive, the
resume, the patches, and the refusals.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import threading
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import runner
from yulon.catalog import native
from yulon.catalog.catalog import CATALOG_FILE, SingleEntry, load_catalog
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    InstallStopped,
    UnsupportedPlatformError,
)
from yulon.catalog.process_install import (
    DOWNLOADS,
    STAGE_NAMES,
    UNPACKED,
    ProcessInstaller,
    ProcessSeams,
    existing_install,
    go_env,
)
from yulon.ui import lines

REV = "db707d59e303b49426c62d9e35d14181e7823631"
ROOT = f"115-{REV}"
TARGET = "server/work/dfo-lan/internal/catalog/odyssey.go"
SOURCE_FILES = {
    "server/work/dfo-lan/go.mod": "module dfolan\n",
    TARGET: 'package catalog\n\nconst sha = "old"\n',
    "launcher.settings.json": "{}\n",
}
PATCH = (
    f"--- a/{TARGET}\n+++ b/{TARGET}\n@@ -1,3 +1,3 @@\n package catalog\n \n"
    '-const sha = "old"\n+const sha = "new"\n'
)
NEW_FILE_PATCH = (
    "--- /dev/null\n+++ b/server/work/dfo-lan/internal/catalog/added.go\n"
    "@@ -0,0 +1 @@\n+package catalog\n"
)


def _zip(root: str, files: Mapping[str, str], *, stray: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr(f"{root}/", "")
        for name, text in files.items():
            zipped.writestr(f"{root}/{name}", text)
        if stray is not None:
            zipped.writestr(stray, "x")
    return buffer.getvalue()


def _tgz(members: list[tuple[str, bytes | None, int]], *, link: str | None = None) -> bytes:
    """A .tar.gz of (name, data or None for a folder, mode); `link` adds a symlink member."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tarred:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if data is None:
                info.type = tarfile.DIRTYPE
                tarred.addfile(info)
            else:
                info.size = len(data)
                tarred.addfile(info, io.BytesIO(data))
        if link is not None:
            info = tarfile.TarInfo(link)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tarred.addfile(info)
    return buffer.getvalue()


SOURCE_ZIP = _zip(ROOT, SOURCE_FILES)
GO_ZIP = _zip("go", {"bin/go.exe": "MZ", "VERSION": "go1.26.5\n"})
GO_BIN = "go.exe" if os.name == "nt" else "go"
"""The engine names Go by the OS it runs on (`toolchain_paths()`), the test host's here."""
GO_TGZ_MEMBERS: list[tuple[str, bytes | None, int]] = [
    ("go", None, 0o755),
    ("go/bin", None, 0o755),
    (f"go/bin/{GO_BIN}", b"\x7fELF", 0o755),
    ("go/VERSION", b"go1.26.5\n", 0o644),
]
GO_TGZ = _tgz(GO_TGZ_MEMBERS)
GO_TGZ_URL = "https://go.example/go.linux-amd64.tar.gz"
LINUX_PATCH = (
    "--- /dev/null\n+++ b/server/work/dfo-lan/internal/catalog/linux.go\n"
    "@@ -0,0 +1 @@\n+package catalog\n"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Fakes:
    """The network and the compiler: records what the engine asked, answers as told."""

    blobs: dict[str, bytes]
    downloads: list[str] = field(default_factory=list)
    builds: list[tuple[list[str], Path, dict[str, str]]] = field(default_factory=list)
    build_exit: int = 0
    build_stopped: bool = False
    corrupt_first: int = 0

    def download(self, url: str, dest: Path) -> Path:
        self.downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            return dest  # `download_verified()` reuses a completed file as it finds it
        data = self.blobs[url]
        if self.corrupt_first:
            self.corrupt_first -= 1
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        dest.write_bytes(data)
        return dest

    def stream(
        self,
        argv: list[str],
        cwd: Path,
        *,
        merge_stderr: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> Iterator[str]:
        self.builds.append((argv, cwd, dict(env or {})))
        yield f"# {argv[-1]}"
        if self.build_stopped:
            raise runner.StreamEnded(-1, argv)
        if self.build_exit:
            yield "undefined: hostProcess"
            raise subprocess.CalledProcessError(self.build_exit, argv)
        output = cwd / argv[argv.index("-o") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"MZ")

    def seams(self, **overrides: Any) -> ProcessSeams:
        values: dict[str, Any] = {
            "platform_id": lambda: "windows",
            "dir_problem": lambda _p: None,
            "download": self.download,
            "stream": self.stream,
        }
        values.update(overrides)
        return ProcessSeams(**values)


def _entry(
    *, patches: tuple[str, ...] = ("fix.patch",), linux_patches: tuple[str, ...] = ()
) -> SingleEntry:
    """The shipped DFO entry with its archive, toolchains and patches pointed at the fakes."""
    raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    entry = copy.deepcopy(next(e for e in raw["single"] if e["id"] == "dfo"))
    process = entry["install"]["process"]
    process["archive"].update(
        url=f"https://forge.example/x/-/archive/{REV}/x.zip",
        sha256=_sha(SOURCE_ZIP),
        size=len(SOURCE_ZIP),
        root=ROOT,
    )
    process["toolchain"].update(
        url="https://go.example/go.zip", sha256=_sha(GO_ZIP), size=len(GO_ZIP), root="go"
    )
    process["toolchain_by_platform"]["linux"].update(
        url=GO_TGZ_URL, sha256=_sha(GO_TGZ), size=len(GO_TGZ), root="go"
    )
    process["patches"] = [
        {"file": f"dfo/process/patches/{name}", "source": "src", "reason": f"applies {name}"}
        for name in patches
    ] + [
        {
            "file": f"dfo/process/patches/{name}",
            "source": "src",
            "reason": f"applies {name}",
            "platforms": ["linux"],
        }
        for name in linux_patches
    ]
    return SingleEntry.model_validate(entry)


@pytest.fixture
def installers(tmp_path: Path) -> Path:
    root = tmp_path / "installers"
    patches = root / "dfo" / "process" / "patches"
    patches.mkdir(parents=True)
    (patches / "fix.patch").write_text(PATCH, encoding="utf-8")
    (patches / "add.patch").write_text(NEW_FILE_PATCH, encoding="utf-8")
    (patches / "linux.patch").write_text(LINUX_PATCH, encoding="utf-8")
    return root


@pytest.fixture
def fakes() -> Fakes:
    return Fakes(
        blobs={
            f"https://forge.example/x/-/archive/{REV}/x.zip": SOURCE_ZIP,
            "https://go.example/go.zip": GO_ZIP,
            GO_TGZ_URL: GO_TGZ,
        }
    )


def _engine(entry: SingleEntry, installers: Path, fakes: Fakes, **seams: Any) -> ProcessInstaller:
    return ProcessInstaller(entry, installers_root=installers, seams=fakes.seams(**seams))


def _run(engine: ProcessInstaller, server: Path, **kwargs: Any) -> list[str]:
    return [
        lines.parse(line).text for line in engine.run(InstallOptions(server_dir=server), **kwargs)
    ]


def test_the_shipped_dfo_entry_is_built_by_this_engine() -> None:
    dfo = load_catalog().get_single("dfo")
    engine = ProcessInstaller(dfo)
    assert engine.process.family == "go"
    with pytest.raises(ValueError, match="host programs"):
        ProcessInstaller(load_catalog().get_single("ddon"))


def test_a_fresh_install_runs_every_stage_in_order_and_records_them(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "yulon-dfo"
    said = _run(_engine(_entry(), installers, fakes), server)

    assert [line[4:] for line in said if line.startswith("--- ")] == list(STAGE_NAMES)
    state = native.read_state(server, valid=STAGE_NAMES)
    assert state is not None and state.completed == STAGE_NAMES and state.family == "go"
    checkout = server / "src"
    assert (checkout / TARGET).read_text() == 'package catalog\n\nconst sha = "new"\n'
    assert json.loads((checkout / UNPACKED).read_text())["sha256"] == _sha(SOURCE_ZIP)
    assert (server / "toolchain" / "go" / "bin" / "go.exe").read_bytes() == b"MZ"
    assert not (server / DOWNLOADS).exists(), "each zip is deleted once unpacked"
    assert "Applied fix.patch: applies fix.patch" in said
    assert said[-1].startswith("DFO is built in")
    assert "choose your DFO client folder" in said[-1]


def test_the_build_uses_the_installs_own_go_and_nothing_of_the_players(
    tmp_path: Path, installers: Path, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOROOT", "C:/Program Files/Go")
    server = tmp_path / "yulon-dfo"
    _run(_engine(_entry(), installers, fakes), server)

    module = server / "src" / "server" / "work" / "dfo-lan"
    packages = [(argv[0], argv[1:], cwd) for argv, cwd, _env in fakes.builds]
    go = str(server / "toolchain" / "go" / "bin" / GO_BIN)
    assert packages == [
        (go, ["build", "-trimpath", "-o", "bin/wireprobe-pvf.exe", "./cmd/wireprobe"], module),
        (go, ["build", "-trimpath", "-o", "bin/dfolauncher.exe", "./cmd/dfolauncher"], module),
    ]
    env = fakes.builds[0][2]
    assert env["GOTOOLCHAIN"] == "local" and env["GOENV"] == "off" and env["CGO_ENABLED"] == "0"
    assert env["GOPATH"] == str(server / "toolchain" / "gopath")
    assert env["GOCACHE"] == str(server / "toolchain" / "gocache")
    assert "GOROOT" not in {key.upper() for key in env}


def test_a_second_press_downloads_nothing_and_builds_nothing(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "yulon-dfo"
    _run(_engine(_entry(), installers, fakes), server)
    fakes.downloads.clear()
    fakes.builds.clear()

    said = _run(_engine(_entry(), installers, fakes), server)

    assert fakes.downloads == [] and fakes.builds == []
    assert "fix.patch is already in place." in said
    assert "The server is already built; skipping the build." in said
    assert "src is already unpacked; leaving it exactly as it is." in said


def test_a_patch_a_newer_version_adds_is_applied_and_the_server_rebuilt(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "yulon-dfo"
    _run(_engine(_entry(), installers, fakes), server)
    fakes.builds.clear()

    said = _run(_engine(_entry(patches=("fix.patch", "add.patch")), installers, fakes), server)

    assert "Applied add.patch: applies add.patch" in said
    assert (server / "src" / "server/work/dfo-lan/internal/catalog/added.go").is_file()
    assert len(fakes.builds) == 2, "a patch applied this press makes the build run again"


def test_a_download_that_does_not_match_is_fetched_once_more(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    fakes.corrupt_first = 1
    said = _run(_engine(_entry(), installers, fakes), tmp_path / "s")
    assert "The download did not match its pinned sha256; fetching it again." in said
    assert (tmp_path / "s" / "src" / TARGET).is_file()


def test_a_download_that_never_matches_is_deleted_and_nothing_is_unpacked(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    fakes.corrupt_first = 2
    server = tmp_path / "s"
    with pytest.raises(InstallerError, match="did not match the size and sha256"):
        _run(_engine(_entry(), installers, fakes), server)
    assert not (server / "src").exists()
    assert not any((server / DOWNLOADS).glob("*.zip"))
    state = native.read_state(server, valid=STAGE_NAMES)
    assert state is not None and "did not match" in state.last_error


def test_an_archive_with_files_outside_its_root_is_not_unpacked(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    bad = _zip(ROOT, SOURCE_FILES, stray="../evil.txt")
    url = f"https://forge.example/x/-/archive/{REV}/x.zip"
    fakes.blobs[url] = bad
    raw_entry = _entry().model_dump(mode="json")
    raw_entry["install"]["process"]["archive"].update(sha256=_sha(bad), size=len(bad))
    entry = SingleEntry.model_validate(raw_entry)
    with pytest.raises(InstallerError, match="outside its"):
        _run(_engine(entry, installers, fakes), tmp_path / "s")
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "s" / "src").exists()


def test_a_checkout_folder_somebody_else_filled_is_left_alone(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "s"
    engine = _engine(_entry(), installers, fakes)
    server.mkdir()
    native.write_state(
        server,
        native.InstallState(game_id="dfo", install_id=engine.install_id(server), family="go"),
    )
    (server / "src").mkdir()
    (server / "src" / "mine.txt").write_text("player's")
    with pytest.raises(InstallerError, match="did not unpack"):
        _run(engine, server)
    assert (server / "src" / "mine.txt").read_text() == "player's"


def test_a_folder_with_files_and_no_record_is_refused_before_anything_is_written(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "s"
    server.mkdir()
    (server / "notes.txt").write_text("x")
    with pytest.raises(InstallerError, match="already has files"):
        _run(_engine(_entry(), installers, fakes), server)
    assert fakes.downloads == []
    assert not (server / native.STATE_FILE).exists()


def test_on_a_platform_it_does_not_list_it_refuses_with_the_platform_sentence(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    engine = _engine(_entry(), installers, fakes, platform_id=lambda: "macos")
    with pytest.raises(UnsupportedPlatformError):
        _run(engine, tmp_path / "s")
    assert fakes.downloads == []


def test_a_failed_build_quotes_its_last_lines_and_is_recorded(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    fakes.build_exit = 1
    server = tmp_path / "s"
    with pytest.raises(InstallerError, match="did not build") as caught:
        _run(_engine(_entry(), installers, fakes), server)
    assert "undefined: hostProcess" in str(caught.value)
    state = native.read_state(server, valid=STAGE_NAMES)
    assert state is not None and "build" not in state.completed
    assert state.has("fetch-toolchain")


def test_a_build_the_panel_stopped_is_a_stop_not_a_failure(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    fakes.build_stopped = True
    with pytest.raises(InstallStopped, match="was stopped"):
        _run(_engine(_entry(), installers, fakes), tmp_path / "s")


def test_a_stop_before_the_first_stage_writes_nothing(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InstallStopped):
        _run(_engine(_entry(), installers, fakes), tmp_path / "s", cancel=cancel)
    assert fakes.downloads == []


def test_use_existing_takes_a_finished_install_and_refuses_an_unfinished_one(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "s"
    entry = _entry()
    with pytest.raises(InstallerError, match="holds no DFO server"):
        existing_install(entry, server, platform_id=lambda: "windows")
    fakes.build_exit = 1
    with pytest.raises(InstallerError):
        _run(_engine(entry, installers, fakes), server)
    with pytest.raises(InstallerError, match="did not finish"):
        existing_install(entry, server, platform_id=lambda: "windows")
    fakes.build_exit = 0
    _run(_engine(entry, installers, fakes), server)
    state = existing_install(entry, server, platform_id=lambda: "windows")
    assert state.completed == STAGE_NAMES


def test_go_env_points_every_cache_inside_the_server_folder(tmp_path: Path) -> None:
    process = load_catalog().get_single("dfo").install.process
    assert process is not None
    env = go_env(tmp_path, process)
    for key in ("GOPATH", "GOMODCACHE", "GOCACHE"):
        assert Path(env[key]).is_relative_to(tmp_path), key
    assert env["GOFLAGS"] == "-modcacherw"


# -- Linux (a Steam Deck) ------------------------------------------------------------------


def test_on_linux_go_is_linuxs_own_tarball_and_keeps_its_execute_bits(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    server = tmp_path / "yulon-dfo"
    said = _run(_engine(_entry(), installers, fakes, platform_id=lambda: "linux"), server)

    assert GO_TGZ_URL in fakes.downloads and "https://go.example/go.zip" not in fakes.downloads
    go = server / "toolchain" / "go" / "bin" / GO_BIN
    assert go.read_bytes() == b"\x7fELF"
    assert (server / "toolchain" / "go" / "VERSION").read_text() == "go1.26.5\n"
    if os.name == "posix":
        assert go.stat().st_mode & 0o777 == 0o755
        assert (server / "toolchain" / "go" / "VERSION").stat().st_mode & 0o777 == 0o644
    marker = json.loads((server / "toolchain" / "go" / UNPACKED).read_text())
    assert marker["sha256"] == _sha(GO_TGZ)
    assert said[-1].startswith("DFO is built in")


def test_a_patch_for_linux_only_is_applied_on_linux_and_skipped_on_windows(
    tmp_path: Path, installers: Path, fakes: Fakes
) -> None:
    added = Path("src/server/work/dfo-lan/internal/catalog/linux.go")
    entry = _entry(linux_patches=("linux.patch",))
    _run(_engine(entry, installers, fakes, platform_id=lambda: "linux"), tmp_path / "deck")
    assert (tmp_path / "deck" / added).read_text() == "package catalog\n"
    _run(_engine(entry, installers, fakes), tmp_path / "pc")
    assert not (tmp_path / "pc" / added).exists()


@pytest.mark.parametrize(
    ("members", "link", "refusal"),
    [
        (GO_TGZ_MEMBERS, "go/bin/escape", "not a file or a folder"),
        ([*GO_TGZ_MEMBERS, ("elsewhere/x", b"x", 0o644)], None, "outside its go folder"),
        ([*GO_TGZ_MEMBERS, ("go/../../x", b"x", 0o644)], None, "not a path inside"),
    ],
)
def test_a_tarball_with_a_link_or_a_path_out_of_its_folder_is_not_unpacked(
    tmp_path: Path,
    installers: Path,
    fakes: Fakes,
    members: list[tuple[str, bytes | None, int]],
    link: str | None,
    refusal: str,
) -> None:
    bad = _tgz(members, link=link)
    fakes.blobs[GO_TGZ_URL] = bad
    entry = _entry()
    raw = entry.model_dump(mode="json", by_alias=True)
    raw["install"]["process"]["toolchain_by_platform"]["linux"].update(
        sha256=_sha(bad), size=len(bad)
    )
    server = tmp_path / "s"
    engine = _engine(
        SingleEntry.model_validate(raw), installers, fakes, platform_id=lambda: "linux"
    )
    with pytest.raises(InstallerError, match=refusal):
        _run(engine, server)
    assert not (server / "toolchain" / "go").exists()
    assert not (tmp_path / "x").exists() and not (tmp_path / "etc").exists()


def test_go_env_hands_go_the_cleaned_environment_a_frozen_build_gives_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AppImage's own LD_LIBRARY_PATH must not reach Go (it breaks SteamOS's /bin/sh)."""
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIxyz")
    monkeypatch.setattr(
        runner,
        "child_env",
        lambda env=None: {k: v for k, v in (env or {}).items() if k != "LD_LIBRARY_PATH"},
    )
    process = load_catalog().get_single("dfo").install.process
    assert process is not None
    assert "LD_LIBRARY_PATH" not in go_env(tmp_path, process)
