"""T835: the Conquest of Azeroth world server's map data, in the `world-data` volume on Windows.

Centurion (TrinityCore) has read its map data from a named volume on Windows since T219:
`composegen.world_data_dirs()` names the folders, the `WORLD_DATA_*` tokens mount the volume
and run `world-data-sync.sh` as the world's entrypoint, and `world_data.refresh()` writes the
fingerprint every start copies by. This module holds the same machinery to the AzerothCore
family, whose CoA worldserver opens `dbc maps vmaps mmaps Cameras` under `AC_DATA_DIR`.

The Linux, WSL-distro and macOS files must not move: `tests/data/coa-rendered/` was written
before the tokens went into the template, and these tests compare every render against it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests import support_rendered
from yulon import resources
from yulon.catalog import composegen, native, world_data
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import extract

COA = load_catalog().get("wow-coa")
"""The SHIPPED CoA entry: its `world_data_dirs` are the catalog's."""
DIRS = ("dbc", "maps", "vmaps", "mmaps", "Cameras")
CORE = composegen.AC_DIST_DIR
FILE = world_data.FINGERPRINT_FILE


# -- T835: the CoA files off Windows do not move -------------------------------------------


@pytest.mark.parametrize("folder", sorted(support_rendered.COA_LABELS))
def test_the_coa_linux_render_reproduces_its_committed_snapshot_byte_for_byte(folder: str) -> None:
    """T835 gives a Windows CoA a named volume for its map data; Linux keeps the bind.

    The snapshots were written before that change (`support_rendered`), with no label and
    with SELinux's `:z`. A WSL-distro install renders as `linux` and is covered here too.
    """
    texts = support_rendered.coa_rendered(support_rendered.COA_LABELS[folder])
    root = support_rendered.COA_SNAPSHOT_ROOT / folder
    assert set(texts) == {path.name for path in root.iterdir() if path.is_file()}
    for name, text in texts.items():
        assert text == (root / name).read_text(encoding="utf-8"), f"{folder}: {name}"
    assert "./data:/azerothcore/env/dist/data:ro" in texts[composegen.BASE_FILE]


@pytest.mark.parametrize("folder", sorted(support_rendered.COA_LABELS))
def test_the_coa_macos_render_is_the_linux_snapshot(folder: str) -> None:
    """macOS keeps the bind until it is measured (T219 Decision 2): nothing in these files
    differs from Linux on a Mac, so its render is the Linux snapshot byte for byte."""
    label = support_rendered.COA_LABELS[folder]
    texts = support_rendered.coa_rendered(label, platform_id="macos")
    root = support_rendered.COA_SNAPSHOT_ROOT / folder
    for name, text in texts.items():
        assert text == (root / name).read_text(encoding="utf-8"), f"macos {folder}: {name}"


# -- T835: on Windows the CoA world reads its map data from a volume ------------------------


def render_coa_on(platform_id: str, bind_label: str = "") -> composegen.ComposePlan:
    """The SHIPPED CoA entry on `platform_id`, for `/home/user/wow-coa-server`."""
    return composegen.render(
        COA,
        support_rendered.linux_server_dir(COA.id),
        templates_root=resources.installers_dir(),
        db_password=support_rendered.PASSWORD,
        bind_label=bind_label,
        platform_id=lambda: platform_id,
    )


def test_the_windows_coa_world_server_mounts_the_world_data_volume() -> None:
    """The volume at `AC_DATA_DIR`, the server folder's `data` beside it read-only at `data-src`,
    `env/dist/etc` and `logs` as before; `world-data` declared next to `db-data`."""
    base = yaml.safe_load(render_coa_on("windows").base)
    world = base["services"]["coa-worldserver"]
    assert world["volumes"] == [
        f"./env/dist/etc:{CORE}/etc",
        f"./env/dist/logs:{CORE}/logs",
        f"world-data:{CORE}/data",
        f"./data:{CORE}/data-src:ro",
    ]
    assert set(base["volumes"]) == {"db-data", "world-data"}


def test_the_world_data_volume_never_takes_the_selinux_label_on_coa() -> None:
    """`:z` relabels a HOST folder; a named volume is Docker's own. The read-only source takes
    it as a second option beside `ro`, the one spelling compose reads."""
    base = yaml.safe_load(render_coa_on("windows", bind_label=":z").base)
    assert base["services"]["coa-worldserver"]["volumes"] == [
        f"./env/dist/etc:{CORE}/etc:z",
        f"./env/dist/logs:{CORE}/logs:z",
        f"world-data:{CORE}/data",
        f"./data:{CORE}/data-src:ro,z",
    ]


def test_the_windows_coa_world_starts_through_the_sync_and_then_the_images_own_entrypoint() -> None:
    """The sync is the container's own entrypoint (`start_staged()` runs `compose up --no-deps`,
    which never starts a one-shot service it depends on); the image's ENTRYPOINT and CMD follow
    as `command:`, because a compose `entrypoint:` drops the image's CMD."""
    world = yaml.safe_load(render_coa_on("windows").base)["services"]["coa-worldserver"]
    entrypoint = world["entrypoint"]
    assert entrypoint[:2] == ["sh", "-c"]
    assert len(entrypoint) == 4, "the script, then $0 for it"
    assert entrypoint[3] == "yulon-world"
    assert 'exec "$$@"' in entrypoint[2]
    assert f"src={CORE}/data-src" in entrypoint[2]
    assert f"vol={CORE}/data" in entrypoint[2]
    assert 'dirs="dbc maps vmaps mmaps Cameras"' in entrypoint[2]
    assert world["command"] == list(composegen.AC_WORLD_COMMAND)


def test_the_windows_coa_world_has_one_user_key_and_it_is_root() -> None:
    """`container_user()` renders `user: "0:0"` on Windows, so the sync writes a fresh
    root-owned volume; a second `user:` from the token would be a duplicate key yaml hides."""
    plan = render_coa_on("windows")
    world = yaml.safe_load(plan.base)["services"]["coa-worldserver"]
    assert world["user"] == "0:0"
    body = plan.base[
        plan.base.index("\n  coa-worldserver:\n") : plan.base.index("\n  coa-tools:\n")
    ]
    assert body.count("\n    user:") == 1


def test_the_world_command_is_the_pins_entrypoint_and_cmd() -> None:
    """`AC_WORLD_COMMAND` is read off a copy of the pin's Dockerfile, not guessed.

    `apps/docker/Dockerfile:148` is the runtime stage's ENTRYPOINT and `:192` the
    worldserver stage's CMD.
    """
    dockerfile = Path(__file__).resolve().parent / "data" / "coa-pin" / "Dockerfile-98570f24"
    lines = dockerfile.read_text(encoding="utf-8").splitlines()

    def declared(stage: str, keyword: str) -> list[str]:
        start = lines.index(f"FROM {stage}")
        for line in lines[start + 1 :]:
            if line.startswith("FROM "):
                break
            if line.startswith(f"{keyword} ["):
                return json.loads(line[len(keyword) + 1 :])
        raise AssertionError(f"no {keyword} line in the pin's {stage} stage")

    entrypoint = declared("skeleton AS runtime", "ENTRYPOINT")
    cmd = declared("runtime AS worldserver", "CMD")
    assert composegen.AC_WORLD_COMMAND == (*entrypoint, *cmd)
    rev = COA.emulator.sources[0].rev
    assert rev.startswith("98570f24"), (
        "the CoA pin moved: copy apps/docker/Dockerfile at the new pin into tests/data/coa-pin/ "
        "and check AC_WORLD_COMMAND"
    )


def test_the_linux_coa_render_has_no_volume_and_no_entrypoint() -> None:
    base = render_coa_on("linux").base
    assert "world-data" not in base
    assert "data-src" not in base
    assert "entrypoint" not in yaml.safe_load(base)["services"]["coa-worldserver"]


# -- T835: the rendered sync script, on CoA's two paths ------------------------------------

needs_a_linux_sh = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("sh") is None,
    reason="the script runs in a Linux container with GNU coreutils",
)


def pointed_at(src: Path, vol: Path) -> str:
    """The Windows entrypoint's script with its two folders moved to `src` and `vol`."""
    world = yaml.safe_load(render_coa_on("windows").base)["services"]["coa-worldserver"]
    script = world["entrypoint"][2].replace("$$", "$")
    for name, path in (("src", f"{CORE}/data-src"), ("vol", f"{CORE}/data")):
        line = f"{name}={path}\n"
        assert script.count(line) == 1, f"the script must set {name} exactly once: {line!r}"
        script = script.replace(line, f"{name}={src if name == 'src' else vol}\n")
    return script


@needs_a_linux_sh
def test_the_coa_sync_copies_the_five_world_folders_and_then_runs_the_images_entrypoint(
    tmp_path: Path,
) -> None:
    """`sql/`, `coa-world/` and `Buildings/` stay behind: the first two are the bootstrap's and
    the third is the extractor's scratch, and none is a path `World.cpp` opens."""
    src = tmp_path / "data"
    vol = tmp_path / "volume"
    files = {
        "dbc/Spell.dbc": b"spell",
        "maps/0000000.map": b"map zero",
        "vmaps/000.vmtree": b"tree",
        "mmaps/000.mmap": b"navmesh",
        "Cameras/FlyBy.m2": b"camera",
        "sql/x.sql": b"insert",
        "coa-world/b.json": b"{}",
        "Buildings/dir_bin": b"an extractor intermediate",
    }
    for name, body in files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    (src / FILE).write_text("".join(f"{name} {name}-v1\n" for name in DIRS), encoding="utf-8")
    vol.mkdir()

    done = subprocess.run(
        [
            "sh",
            "-c",
            pointed_at(src, vol),
            "yulon-world",
            "echo",
            "started",
            *composegen.AC_WORLD_COMMAND,
        ],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=60,
    )

    assert done.returncode == 0, done.stderr
    assert "started /usr/bin/env bash /azerothcore/entrypoint.sh worldserver" in done.stdout
    for name in ("dbc/Spell.dbc", "maps/0000000.map", "vmaps/000.vmtree", "mmaps/000.mmap"):
        assert (vol / name).read_bytes() == (src / name).read_bytes(), name
    assert (vol / "Cameras/FlyBy.m2").read_bytes() == b"camera"
    assert sorted(path.name for path in vol.iterdir()) == sorted([*DIRS, FILE])
    assert (vol / FILE).read_text(encoding="utf-8") == (src / FILE).read_text(encoding="utf-8")


# -- T835: the fingerprint a CoA start copies by -------------------------------------------


def lay_data(server_dir: Path) -> Path:
    """The server folder with the Windows base file and one file in each of the five folders."""
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / composegen.BASE_FILE).write_text(render_coa_on("windows").base, encoding="utf-8")
    data = server_dir / "data"
    for name in DIRS:
        folder = data / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.bin").write_bytes(name.encode())
    return data


def fingerprint_lines(server_dir: Path) -> list[str]:
    return (server_dir / "data" / FILE).read_text(encoding="utf-8").splitlines()


def vouch_for_mmaps(server_dir: Path, client: Path, argv_hash: str | None = None) -> None:
    """The extraction evidence a finished mmaps stage leaves, and its output folder."""
    block = COA.install.native.azerothcore  # type: ignore[union-attr]
    assert block is not None and block.client_extract is not None
    ce = block.client_extract
    client.mkdir(parents=True, exist_ok=True)
    required = client / str(ce.client.required_file)
    required.parent.mkdir(parents=True, exist_ok=True)
    required.write_bytes(b"MPQ")
    expected = extract.expected_evidence(ce.extract, client, ce.client.required_file)
    record = extract.ToolRecord(
        extract.MMAPS_TOOL, argv_hash or extract.argv_hash(ce.mmaps.argv), 1
    )
    data = server_dir / "data"
    extract.write_evidence(data, extract.with_record(expected, record))
    (data / extract.MMAPS_DIR).mkdir(parents=True, exist_ok=True)
    for number in range(500):
        (data / extract.MMAPS_DIR / f"{number:04d}.mmtile").write_bytes(b"tile")


def test_refresh_writes_mmaps_dash_for_coa_until_its_mmaps_stage_is_vouched_for(
    tmp_path: Path,
) -> None:
    """`mmaps` is an install stage on AzerothCore, so its folder is copied only once that
    stage's own evidence vouches for it -- the check `run_mmaps()` skips itself on."""
    server_dir = tmp_path / "server"
    lay_data(server_dir)
    assert world_data.refresh(COA, server_dir) is None
    assert fingerprint_lines(server_dir) == [
        f"dbc {world_data._folder_hash(server_dir / 'data/dbc')}",
        f"maps {world_data._folder_hash(server_dir / 'data/maps')}",
        f"vmaps {world_data._folder_hash(server_dir / 'data/vmaps')}",
        "mmaps -",
        f"Cameras {world_data._folder_hash(server_dir / 'data/Cameras')}",
    ]

    vouch_for_mmaps(server_dir, tmp_path / "client")
    assert world_data.refresh(COA, server_dir) is None
    assert fingerprint_lines(server_dir)[3] != "mmaps -"
    assert fingerprint_lines(server_dir)[3].startswith("mmaps ")


def test_a_coa_mmaps_record_for_another_argv_keeps_mmaps_dash(tmp_path: Path) -> None:
    """A folder full of output from a command the entry no longer runs vouches for nothing."""
    server_dir = tmp_path / "server"
    lay_data(server_dir)
    vouch_for_mmaps(server_dir, tmp_path / "client", argv_hash=extract.argv_hash(["other"]))
    assert world_data.refresh(COA, server_dir) is None
    assert "mmaps -" in fingerprint_lines(server_dir)


def test_refresh_writes_nothing_on_a_linux_coa_install(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    lay_data(server_dir)
    (server_dir / composegen.BASE_FILE).write_text(render_coa_on("linux").base, encoding="utf-8")
    assert world_data.refresh(COA, server_dir) is None
    assert not (server_dir / "data" / FILE).exists()


def test_refresh_writes_nothing_for_wotlk(tmp_path: Path) -> None:
    """WotLK's AzerothCore block names no `world_data_dirs`, so the Windows CoA file beside it
    is not its to copy by."""
    server_dir = tmp_path / "server"
    lay_data(server_dir)
    assert world_data.refresh(load_catalog().get("wow-wotlk"), server_dir) is None
    assert not (server_dir / "data" / FILE).exists()


def test_a_repair_that_adds_the_coa_volume_names_its_room() -> None:
    """The Repair confirmation says how much room the copy takes -- the block's own
    `world_data_gb`, whichever family it is on."""
    windows_base = render_coa_on("windows").base
    linux_base = render_coa_on("linux").base
    installer = SimpleNamespace(entry=COA)
    assert (
        native.StagedInstaller._world_data_added(  # type: ignore[arg-type]
            installer, linux_base, windows_base
        )
        == 6.5
    )
    assert (
        native.StagedInstaller._world_data_added(  # type: ignore[arg-type]
            installer, windows_base, windows_base
        )
        == 0.0
    )
