"""T813: a TortoiseBots migration with new bytes runs again instead of refusing.

Shipped TortoiseBots migrations are idempotent by project rule, so a changed
SHA-1 in that module never blocks an update: the world re-runs the file and
its own guards make it a no-op where it already applied. The catalog carries
the module (`CmangosData.rerunnable_modules`); a changed file whose owner
module is listed goes on with any new bytes, and anything else -- a core file,
a file of an unlisted module -- refuses exactly as before (T770).

Every test drives the real `update_to_latest()` on an installed Tortoise
folder, with git's trees at the `tree_bytes` seam and the `migrations` ledger
at the `sql_query` seam, as `tests/test_changed_migrations.py` does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support_native import FakeSnapshot, Recorder
from tests.test_changed_migrations import (
    BOTS_NAME,
    EDITED,
    OLDER,
    OLDER_NAME,
    RAN,
    _core_changed,
    _forward,
    _ready,
)
from tests.test_families_cmangos import engine_for
from tests.test_return_newer_migrations import (
    BOTS,
    BOTS_CHAR,
    BOTS_PIN,
    BOTS_WORLD,
    TORTOISE,
)
from tests.test_update_to_latest import (
    NEW,
    OLD,
    _gated,  # noqa: F401 - autouse fixture the engine build needs
)
from yulon.catalog import native
from yulon.catalog.catalog import CATALOG_FILE, CmangosData, RerunnableModule, parse_catalog
from yulon.catalog.installer import InstallOptions

REASON = (
    "Its author guarantees every migration is idempotent; checked on the three rewritten files."
)

PRESSES = pytest.mark.parametrize("to_pin", [False, True], ids=["update-to-latest", "return"])


def _hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest().upper()  # noqa: S324


def _target(to_pin: bool) -> str:
    return BOTS_PIN if to_pin else NEW


def _bots_changed(rec: Recorder, server_dir: Path, to_pin: bool, *, held: bool = True) -> None:
    """The running commit ships `BOTS_NAME` as `RAN`; the target ships it as `EDITED`."""
    from tests.test_return_newer_migrations import BOTS_CMAKE, CMAKE

    bots = server_dir / BOTS.dest
    rec.byte_trees[(bots, OLD, BOTS_WORLD)] = {BOTS_NAME: RAN}
    rec.byte_trees[(bots, _target(to_pin), BOTS_WORLD)] = {BOTS_NAME: EDITED}
    rec.blobs[(bots, OLD, BOTS_CMAKE)] = CMAKE
    rec.blobs[(bots, _target(to_pin), BOTS_CMAKE)] = CMAKE
    if held:
        rec.migrations["tw_world"] = f"TortoiseBots:{_hash(RAN)}\n"
    _forward(rec, server_dir, to_pin)


def _made_with(rec: Recorder, entry: object) -> native.StagedInstaller:
    made = engine_for(entry, rec)  # type: ignore[arg-type]
    made._test_gate = native.CallableGate(rec.probe, rec.reset)  # type: ignore[attr-defined]
    made._snapshot = FakeSnapshot(rec)  # type: ignore[attr-defined]
    return made


def _listed(entry: object, *rows: RerunnableModule) -> object:
    assert entry.install.native is not None and entry.install.native.cmangos is not None  # type: ignore[union-attr]
    data = entry.install.native.cmangos.model_copy(  # type: ignore[union-attr]
        update={"rerunnable_modules": rows}
    )
    native_block = entry.install.native.model_copy(update={"cmangos": data})  # type: ignore[union-attr]
    install = entry.install.model_copy(update={"native": native_block})  # type: ignore[union-attr]
    return entry.model_copy(update={"install": install})


def _return(
    made: native.StagedInstaller, server_dir: Path, *, to_pin: bool
) -> tuple[list[str], Exception | None]:
    said: list[str] = []
    try:
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=to_pin):
            said.append(line)
    except Exception as exc:  # noqa: BLE001 - the tests assert on either refusal
        return said, exc
    return said, None


def _module() -> RerunnableModule:
    return RerunnableModule(module="TortoiseBots", reason=REASON)


# -- the module rule -----------------------------------------------------------------


@PRESSES
def test_a_rewritten_module_file_with_any_new_bytes_goes_on(tmp_path: Path, to_pin: bool) -> None:
    """The rule is per module, not per bytes: any new bytes in TortoiseBots go on."""
    rec, server_dir, _made = _ready(tmp_path)
    other = EDITED + b"-- upstream edited it again\n"
    bots = server_dir / BOTS.dest
    from tests.test_return_newer_migrations import BOTS_CMAKE, CMAKE

    rec.byte_trees[(bots, OLD, BOTS_WORLD)] = {BOTS_NAME: RAN}
    rec.byte_trees[(bots, _target(to_pin), BOTS_WORLD)] = {BOTS_NAME: other}
    rec.blobs[(bots, OLD, BOTS_CMAKE)] = CMAKE
    rec.blobs[(bots, _target(to_pin), BOTS_CMAKE)] = CMAKE
    rec.migrations["tw_world"] = f"TortoiseBots:{_hash(RAN)}\n"
    _forward(rec, server_dir, to_pin)
    made = _made_with(rec, _listed(TORTOISE, _module()))

    said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is None, raised
    assert any("so no migration runs a second time" in line for line in said), said


@PRESSES
def test_a_changed_core_file_still_refuses(tmp_path: Path, to_pin: bool) -> None:
    """The core ("") is never listed: its files refuse exactly as before."""
    rec, server_dir, _made = _ready(tmp_path)
    _core_changed(rec, server_dir, to_pin)
    made = _made_with(rec, _listed(TORTOISE, _module()))

    _said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is not None, "a changed core file went on"
    assert f"tw_world: {OLDER_NAME}" in str(raised), raised


@PRESSES
def test_a_listed_module_file_plus_a_core_file_names_only_the_core_one(
    tmp_path: Path, to_pin: bool
) -> None:
    from tests.test_return_newer_migrations import CORE, CORE_PIN, CORE_WORLD

    rec, server_dir, _made = _ready(tmp_path)
    _bots_changed(rec, server_dir, to_pin)
    dest = server_dir / CORE.dest
    target = CORE_PIN if to_pin else NEW
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: OLDER}
    rec.byte_trees[(dest, target, CORE_WORLD)] = {
        OLDER_NAME: OLDER + b"-- edited\n",
    }
    rec.migrations["tw_world"] += f":{_hash(OLDER)}\n"
    made = _made_with(rec, _listed(TORTOISE, _module()))

    _said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is not None
    message = str(raised)
    assert OLDER_NAME in message, message
    assert BOTS_NAME not in message, message


# -- the model ---------------------------------------------------------------------


def test_an_empty_module_is_refused() -> None:
    with pytest.raises(ValidationError, match="module"):
        RerunnableModule(module="", reason=REASON)


def test_a_repeat_is_refused() -> None:
    with pytest.raises(ValidationError, match="repeat"):
        CmangosData.model_validate(
            {
                **TORTOISE.install.native.cmangos.model_dump(),  # type: ignore[union-attr]
                "rerunnable_modules": [_module().model_dump(), _module().model_dump()],
            }
        )


def test_a_reason_naming_a_command_is_refused() -> None:
    with pytest.raises(ValidationError, match="command"):
        RerunnableModule.model_validate(
            {"module": "TortoiseBots", "reason": "Run docker compose logs to check it."}
        )


def test_a_reason_naming_a_ticket_is_refused() -> None:
    with pytest.raises(ValidationError, match="ticket"):
        RerunnableModule.model_validate(
            {"module": "TortoiseBots", "reason": "Checked in T813 and safe."}
        )


def test_a_module_no_source_clones_is_refused() -> None:
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    game = next(game for game in data["games"] if game["id"] == "wow-tortoise")
    game["install"]["native"]["cmangos"]["rerunnable_modules"] = [
        {"module": "SomewhereElse", "reason": REASON}
    ]
    with pytest.raises(ValidationError, match="SomewhereElse"):
        parse_catalog(data)


def test_the_core_folder_name_is_refused() -> None:
    """`tortoise-wow` is the core's folder; the ledger keys its files with an empty module."""
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    game = next(game for game in data["games"] if game["id"] == "wow-tortoise")
    game["install"]["native"]["cmangos"]["rerunnable_modules"] = [
        {"module": "tortoise-wow", "reason": REASON}
    ]
    with pytest.raises(ValidationError, match="tortoise-wow"):
        parse_catalog(data)


def test_the_shipped_catalog_lists_tortoise_bots() -> None:
    assert TORTOISE.install.native is not None and TORTOISE.install.native.cmangos is not None
    assert TORTOISE.install.native.cmangos.rerunnable_modules == (
        RerunnableModule(
            module="TortoiseBots",
            reason=(
                "Its author guarantees every migration is idempotent; verified 2026-10-27 on "
                "the three rewritten files in commit 765ea993 on MariaDB 10.6 and MySQL 8.4, "
                "each run twice."
            ),
        ),
    )


def test_the_char_folder_uses_the_same_module_rule(tmp_path: Path) -> None:
    """The char-folder file is owned by TortoiseBots too, so it goes on as well."""
    rec, server_dir, _made = _ready(tmp_path)
    from tests.test_return_newer_migrations import BOTS_CMAKE, CMAKE

    name = "20260824090002_char.sql"
    bots = server_dir / BOTS.dest
    rec.byte_trees[(bots, OLD, BOTS_CHAR)] = {name: RAN}
    rec.byte_trees[(bots, NEW, BOTS_CHAR)] = {name: EDITED}
    rec.blobs[(bots, OLD, BOTS_CMAKE)] = CMAKE
    rec.blobs[(bots, NEW, BOTS_CMAKE)] = CMAKE
    rec.migrations["tw_char"] = f"TortoiseBots:{_hash(RAN)}\n"
    _forward(rec, server_dir, False)
    made = _made_with(rec, _listed(TORTOISE, _module()))

    said, raised = _return(made, server_dir, to_pin=False)

    assert raised is None, raised
    assert any("so no migration runs a second time" in line for line in said), said
