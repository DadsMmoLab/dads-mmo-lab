"""What the shipped CMaNGOS templates render to, as committed bytes (T179 Task 2).

The TrinityCore family (T179) widened `composegen`'s token and folder helpers from
one built-here family to two. WotLK's render was already pinned byte for byte
(`tests/data/wotlk-rendered/`, A16); the three CMaNGOS games' were not, so a change
to a shared helper could have moved their compose files or Dockerfiles with nothing
failing. `tests/data/cmangos-rendered/<game>/` holds what they rendered BEFORE that
change, written by `write_snapshots()` at `59fefbbc`, and `test_composegen.py`
compares every render against it.

Regenerate only when a CMaNGOS template change is the point of the commit:

    python -c "from tests.support_rendered import write_snapshots; write_snapshots()"
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from yulon import resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families import dockerfile

CMANGOS_GAMES = ("wow-tbc", "wow-vanilla", "wow-tortoise")
SNAPSHOT_ROOT = Path(__file__).resolve().parent / "data" / "cmangos-rendered"
PASSWORD = "snapshot-db-password"
"""A generated-mode password: it lands in the plan's `.env`, never in these files."""


def linux_server_dir(game: str) -> Path:
    return Path(f"/home/user/{game}-server")


def linux_install_id(game: str) -> str:
    """`install_id()` as Linux computes it; on Windows `abspath` grows a drive letter."""
    return hashlib.sha256(str(linux_server_dir(game)).encode()).hexdigest()[
        : composegen.INSTALL_ID_LENGTH
    ]


def rendered(entry: CatalogEntry) -> dict[str, str]:
    """The three compose files, the Dockerfile and the .dockerignore, as on Linux, no label."""
    server_dir = linux_server_dir(entry.id)
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password=PASSWORD,
        bind_label="",
        platform_id=lambda: "linux",
    )
    actual = composegen.install_id(server_dir, platform_id=lambda: "linux")
    wanted = linux_install_id(entry.id)
    block = entry.install.native
    assert block is not None and block.dockerfile_dir is not None
    docker, ignore = dockerfile.render(
        resources.installers_dir() / block.dockerfile_dir,
        composegen.entry_tokens(entry),
        secrets=native.Secrets(db_password=PASSWORD),
    )
    return {
        composegen.BASE_FILE: plan.base.replace(actual, wanted),
        composegen.OVERRIDE_FILE: plan.override.replace(actual, wanted),
        composegen.BUILD_FILE: plan.build.replace(actual, wanted),
        "Dockerfile": str(docker),
        ".dockerignore": str(ignore),
    }


def write_snapshots() -> None:
    catalog = load_catalog()
    for game in CMANGOS_GAMES:
        folder = SNAPSHOT_ROOT / game
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in rendered(catalog.get(game)).items():
            (folder / name).write_text(text, encoding="utf-8", newline="\n")


# -- T219: Centurion's compose files on Linux, before the Windows world-data volume ----------

CENTURION = "wow-centurion"
CENTURION_SNAPSHOT_ROOT = Path(__file__).resolve().parent / "data" / "centurion-rendered"
CENTURION_LABELS = {"linux": "", "linux-z": ":z"}
"""Snapshot folder -> the bind label it was rendered with: no SELinux, and SELinux enforcing.

Written by `write_centurion_snapshots()` at `dc685652`, BEFORE T219 taught the TrinityCore
template a Windows-only named volume for the world server's map data, so a Windows token
that leaks into a Linux render fails `test_composegen.py` by name. A WSL-distro install
renders as `linux` (`native.Seams.in_wsl`), so these are its files too. Regenerate only
when a TrinityCore template change for Linux is the point of the commit:

    python -c "from tests.support_rendered import write_centurion_snapshots as w; w()"
"""


def centurion_rendered(bind_label: str, platform_id: str = "linux") -> dict[str, str]:
    """Centurion's three compose files for `/home/user/wow-centurion-server` on `platform_id`."""
    entry = load_catalog().get(CENTURION)
    server_dir = linux_server_dir(CENTURION)
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password=PASSWORD,
        bind_label=bind_label,
        platform_id=lambda: platform_id,
    )
    actual = composegen.install_id(server_dir, platform_id=lambda: platform_id)
    wanted = linux_install_id(CENTURION)
    return {
        composegen.BASE_FILE: plan.base.replace(actual, wanted),
        composegen.OVERRIDE_FILE: plan.override.replace(actual, wanted),
        composegen.BUILD_FILE: plan.build.replace(actual, wanted),
    }


def write_centurion_snapshots() -> None:
    for folder_name, label in CENTURION_LABELS.items():
        folder = CENTURION_SNAPSHOT_ROOT / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in centurion_rendered(label).items():
            (folder / name).write_text(text, encoding="utf-8", newline="\n")
