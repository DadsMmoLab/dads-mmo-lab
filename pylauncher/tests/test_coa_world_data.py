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

import pytest

from tests import support_rendered
from yulon.catalog import composegen


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
