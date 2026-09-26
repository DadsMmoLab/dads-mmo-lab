"""T121: the four mob-multiplier mods read Installed, and their exclusive rule fires.

Baby, Nerf, Buff and Extreme Buff Mobs are SQL-only mods with no `source`: an
install runs one inline statement and leaves no folder under
`sql_scripts/clones/`. Everything that said "installed" asked that folder
(`apply.installed_clones()`), so their rows read Not installed for ever and
`conflicts_with` -- the four name each other -- never saw one of them: Baby Mobs
at x0.25 and then Buff Mobs at x2 stacked.

Since T115 the install writes an `applied` record (and a `pending` mark while
its statement is in flight) into `.yulon-module-answers.json`. That record is
the installed-state source for these mods now: applied reads Installed, a mark
left behind reads as an honest "State unknown", and Remove clearing the record
reads Not installed again.

The applier here is the REAL `Applier` over a temporary server folder, with a
SQL runner that records the texts it is handed, so the record on disk is the
one the real install wrote.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from yulon import apply as apply_module
from yulon import module_answers
from yulon.apply import Applier, ApplyError
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest
from yulon.ui.widgets.modules_panel import (
    BADGE_INSTALLED,
    BADGE_NOT_INSTALLED,
    BADGE_STATE_UNKNOWN,
    ModuleRow,
    SessionState,
    build_module_rows,
)

MOB_MODS = ("baby-mobs", "buff-mobs", "nerf-mobs", "xbuff-mobs")


class _Recorder:
    """A `SqlRunner` that records every text and runs nothing."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def run_file(self, db: str, path: Path) -> None:
        raise AssertionError(f"a mob mod runs no file, got {path}")

    def run_statement(self, db: str, statement: str) -> None:
        self.texts.append(statement)


def _mob(item_id: str = "baby-mobs") -> Manifest:
    return wotlk_modules.store().load("mod", item_id)


def _catalog() -> list[Manifest]:
    return list(wotlk_modules.store().load_all("mod"))


def _all(value: str) -> dict[str, str]:
    return {"hp": value, "dmg": value, "arm": value, "spd": value}


def _relative() -> frozenset[str]:
    return apply_module.relative_keys(_catalog())


def _rows(server_dir: Path) -> dict[str, ModuleRow]:
    rows = build_module_rows(
        _catalog(),
        apply_module.installed_modules(server_dir, _relative()),
        SessionState(),
        None,
        unknown=apply_module.unknown_modules(server_dir, _relative()),
    )
    return {row.id: row for row in rows}


def _mark_pending(server_dir: Path, item_id: str = "baby-mobs") -> None:
    """What a press killed between marking its statement and recording it leaves."""
    assert module_answers.record_pending(server_dir, _mob(item_id), _all("2")) == ""


# ------------------------------------------------------------ the reading


def test_an_applied_record_reads_as_installed(tmp_path: Path) -> None:
    Applier(tmp_path, sql=_Recorder()).install(_mob(), _all("2"))

    assert "baby-mobs" in apply_module.installed_modules(tmp_path)["mod"]
    assert (
        "baby-mobs" not in apply_module.installed_clones(tmp_path)["mod"]
    ), "the folder listing still means the folder"
    row = _rows(tmp_path)["baby-mobs"]
    assert row.installed and row.badge == BADGE_INSTALLED


def test_no_record_reads_not_installed(tmp_path: Path) -> None:
    rows = _rows(tmp_path)
    for item_id in MOB_MODS:
        assert not rows[item_id].installed and rows[item_id].badge == BADGE_NOT_INSTALLED


def test_a_pending_mark_reads_as_an_honest_unknown(tmp_path: Path) -> None:
    """Not Installed and not Not installed: the statement may or may not have committed.

    The row offers Remove, which asks the values (T115's remedy), and not a
    second Install, which T115 refuses over a mark.
    """
    _mark_pending(tmp_path)

    assert "baby-mobs" in apply_module.unknown_modules(tmp_path)["mod"]
    row = _rows(tmp_path)["baby-mobs"]
    assert row.badge == BADGE_STATE_UNKNOWN
    assert row.installed, "Remove is the press on offer"
    assert set(apply_module.unknown_modules(tmp_path).get("mod", {})) == {"baby-mobs"}


def test_an_applied_record_with_a_pending_mark_still_reads_unknown(tmp_path: Path) -> None:
    """A re-run killed mid-statement: the old record is there, and it is not the truth."""
    applier = Applier(tmp_path, sql=_Recorder())
    applier.install(_mob(), _all("2"))
    _mark_pending(tmp_path)

    assert _rows(tmp_path)["baby-mobs"].badge == BADGE_STATE_UNKNOWN


def test_the_four_relative_mods_are_the_ones_whose_state_comes_from_the_record() -> None:
    assert _relative() == {f"mod/{item_id}" for item_id in MOB_MODS}


def _corrupt(server_dir: Path, how: str) -> None:
    """The two ways the answers file cannot be read: bad JSON, or no permission."""
    path = server_dir / module_answers.ANSWERS_FILE
    if how == "malformed":
        path.write_text("{ not json", encoding="utf-8")
        return
    path.write_text(json.dumps({"applied": {"mod/baby-mobs": _all("2")}}), encoding="utf-8")
    path.chmod(0)


UNREADABLE = pytest.mark.parametrize(
    "how",
    [
        "malformed",
        pytest.param(
            "permission",
            marks=pytest.mark.skipif(
                sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                reason="chmod 0 does not stop this user reading",
            ),
        ),
    ],
)


@UNREADABLE
def test_an_unreadable_answers_file_puts_every_record_backed_mod_in_doubt(
    tmp_path: Path, how: str
) -> None:
    """Codex high, fix wave: `recorded_keys()` read an unreadable file as "nothing recorded".

    So the exclusive rule failed open: every mob row read Not installed and a
    second one was allowed to stack. Unreadable is its own answer now.
    """
    _corrupt(tmp_path, how)
    read = module_answers.recorded_keys(tmp_path)
    assert read.unreadable, "unreadable is not the same answer as no file"
    rows = _rows(tmp_path)
    for item_id in MOB_MODS:
        assert rows[item_id].badge == BADGE_STATE_UNKNOWN, item_id
        assert rows[item_id].installed, "offers Remove, not a second Install"
        assert module_answers.ANSWERS_FILE in (rows[item_id].state_detail or ""), item_id
    # The re-run check the tab asks before any dialog (T55's order) says it too.
    refusal = Applier(tmp_path, sql=_Recorder()).reapply_refusal(_mob("buff-mobs"))
    assert refusal is not None and module_answers.ANSWERS_FILE in refusal, refusal


def test_no_answers_file_is_nothing_recorded_and_not_a_doubt(tmp_path: Path) -> None:
    read = module_answers.recorded_keys(tmp_path)
    assert (read.applied, read.pending, read.unreadable) == (frozenset(), frozenset(), "")
    assert apply_module.unknown_modules(tmp_path, _relative()) == {}


@UNREADABLE
@pytest.mark.parametrize("item_id", MOB_MODS)
def test_an_unreadable_answers_file_refuses_a_mob_mod_before_any_sql(
    tmp_path: Path, how: str, item_id: str
) -> None:
    _corrupt(tmp_path, how)
    db = _Recorder()
    started: list[str] = []
    applier = Applier(
        tmp_path,
        sql=db,
        world_running=lambda: False,
        start_database=lambda: started.append("db") is None,
    )
    with pytest.raises(ApplyError) as refused:
        applier.install(_mob(item_id), _all("2"))
    message = str(refused.value)
    assert module_answers.ANSWERS_FILE in message and "Nothing was changed" in message, message
    assert "cannot tell whether this one or one of its alternatives" in message, message
    assert db.texts == [] and started == [], "refused before the database start and any SQL"


# ------------------------------------------------------------ the exclusive rule


def test_buff_mobs_after_baby_mobs_is_locked_on_the_tab_naming_baby_mobs(tmp_path: Path) -> None:
    Applier(tmp_path, sql=_Recorder()).install(_mob(), _all("2"))

    rows = _rows(tmp_path)
    for other in ("buff-mobs", "nerf-mobs", "xbuff-mobs"):
        assert not rows[other].installable, other
        assert "Baby Mobs is installed here" in (rows[other].install_reason or ""), other


def test_buff_mobs_after_baby_mobs_is_refused_by_the_applier_before_any_sql(
    tmp_path: Path,
) -> None:
    """For a caller with no tab in front: the applier's own refusal, nothing sent."""
    db = _Recorder()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("2"))
    sent = list(db.texts)

    with pytest.raises(ApplyError) as refused:
        applier.install(_mob("buff-mobs"), _all("2"))

    message = str(refused.value)
    assert "buff-mobs and baby-mobs cannot both be installed" in message, message
    assert "applied to this server's database" in message, message
    assert "Nothing was changed" in message, message
    assert db.texts == sent, "no SQL was sent for buff-mobs"
    assert module_answers.read_applied(tmp_path, _mob("buff-mobs")) is None
    assert not module_answers.is_pending(tmp_path, _mob("buff-mobs"))


def test_a_pending_baby_mobs_blocks_buff_mobs_too(tmp_path: Path) -> None:
    """It may be in the database: the sibling is refused until a Remove settles it."""
    _mark_pending(tmp_path)
    db = _Recorder()
    with pytest.raises(ApplyError, match="baby-mobs"):
        Applier(tmp_path, sql=db).install(_mob("buff-mobs"), _all("2"))
    assert db.texts == []
    buff = _rows(tmp_path)["buff-mobs"]
    assert not buff.installable
    # Fix wave (minor): the tab's lock says what the applier says, not "is installed here".
    reason = buff.install_reason or ""
    assert "Baby Mobs may be in this server's database" in reason, reason
    assert "a press on it stopped while its SQL was being sent" in reason, reason
    assert "is installed here" not in reason, reason


def test_after_remove_the_row_reads_not_installed_and_buff_mobs_installs(
    tmp_path: Path,
) -> None:
    db = _Recorder()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("2"))
    applier.remove(_mob(), None)

    rows = _rows(tmp_path)
    assert not rows["baby-mobs"].installed and rows["baby-mobs"].badge == BADGE_NOT_INSTALLED
    assert rows["buff-mobs"].installable

    applier.install(_mob("buff-mobs"), _all("2"))
    rows = _rows(tmp_path)
    assert rows["buff-mobs"].installed and rows["buff-mobs"].badge == BADGE_INSTALLED
    assert not rows["baby-mobs"].installable
    assert "Buff Mobs is installed here" in (rows["baby-mobs"].install_reason or "")
