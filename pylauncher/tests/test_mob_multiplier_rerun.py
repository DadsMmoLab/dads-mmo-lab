"""T115: running a mob-multiplier mod again lands on the new multiplier, never on both.

The four mob mods (Baby, Nerf, Buff and Extreme Buff Mobs) install with a
RELATIVE statement, `HealthModifier=HealthModifier*{hp}`, and remove with its
inverse. Measured on the T104 head before this fix, through the real `Applier`:
Install at x2 then Install again at x3 left every creature at x6, and Remove
then put it back to x2, not to stock. A sourceless mod leaves no folder behind,
so the Modules row keeps offering Install and nothing warned.

These tests drive the REAL `Applier` over a temporary server folder. The SQL
runner is sqlite: each text the applier sends is executed against one
`creature_template` row, so the numbers below are what the manifest's own
statements do, not what a test thinks they do.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import apply as apply_module
from yulon import module_answers
from yulon.apply import Applier, ApplyError
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest

BASE = (1.0, 1.0, 1.0, 2000.0, 2000.0)
MOB_MODS = ("baby-mobs", "buff-mobs", "nerf-mobs", "xbuff-mobs")


class _Db:
    """A `SqlRunner` that runs every text against a one-row sqlite `creature_template`.

    `START TRANSACTION` is MySQL's spelling; sqlite's is `BEGIN`. A text that
    fails part-way is rolled back, as `mysql` ending its session does (T100).

    `BaseAttackTime` and `RangeAttackTime` are INTEGER columns on AzerothCore, and
    MySQL rounds a fractional result into them on every UPDATE (cold review, T115
    fix wave). sqlite would keep `666.5` in an INTEGER column, so a trigger does
    MySQL's rounding (half away from zero, as for the DECIMAL literal `0.5`).

    `on_statement` is called with the text before anything runs: what the disk
    says at that moment is what a kill right then would leave behind.
    """

    def __init__(
        self,
        fail: bool = False,
        base: tuple[float, ...] = BASE,
        on_statement: Callable[[str], None] | None = None,
    ) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE creature_template (entry INT, HealthModifier REAL, DamageModifier REAL, "
            "ArmorModifier REAL, BaseAttackTime INTEGER, RangeAttackTime INTEGER)"
        )
        self.db.execute(
            "CREATE TRIGGER mysql_int_rounding AFTER UPDATE ON creature_template BEGIN "
            "UPDATE creature_template SET BaseAttackTime = CAST(ROUND(NEW.BaseAttackTime) AS "
            "INTEGER), RangeAttackTime = CAST(ROUND(NEW.RangeAttackTime) AS INTEGER) "
            "WHERE rowid = NEW.rowid; END"
        )
        self.db.execute("INSERT INTO creature_template VALUES (1, ?, ?, ?, ?, ?)", base)
        self.db.commit()
        self.texts: list[str] = []
        self.fail = fail
        self.on_statement = on_statement

    def run_file(self, db: str, path: Path) -> None:
        raise AssertionError(f"a mob mod runs no file, got {path}")

    def run_statement(self, db: str, statement: str) -> None:
        if self.on_statement is not None:
            self.on_statement(statement)
        if self.fail:
            raise ApplyError("mysql exited 1: ERROR 2013 (HY000): Lost connection")
        self.texts.append(statement)
        try:
            self.db.executescript(statement.replace("START TRANSACTION;", "BEGIN;"))
        except sqlite3.Error as exc:
            self.db.rollback()
            raise ApplyError(f"mysql exited 1: {exc}") from exc

    def row(self) -> tuple[float, ...]:
        return tuple(
            round(v, 6)
            for v in self.db.execute(
                "SELECT HealthModifier, DamageModifier, ArmorModifier, BaseAttackTime, "
                "RangeAttackTime FROM creature_template"
            ).fetchone()
        )


def _mob(item_id: str = "baby-mobs") -> Manifest:
    return wotlk_modules.store().load("mod", item_id)


def _all(value: str) -> dict[str, str]:
    return {"hp": value, "dmg": value, "arm": value, "spd": value}


def _times(factor: float) -> tuple[float, ...]:
    return tuple(round(v * factor, 6) for v in BASE)


def _record(server_dir: Path) -> dict[str, object]:
    return json.loads((server_dir / module_answers.ANSWERS_FILE).read_text(encoding="utf-8"))


# ------------------------------------------------------------ the repro


def test_installing_again_at_a_new_multiplier_lands_on_exactly_the_new_one(tmp_path: Path) -> None:
    """The ticket's repro: x2, then x3, is x3 -- not x6.

    Mutation: run the plain install SQL on a re-run (drop the undo) and this
    reads 6.0.
    """
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("2"))
    assert db.row() == _times(2)

    applier.install(_mob(), _all("3"))
    assert db.row() == _times(3)


def test_the_undo_and_the_new_multiplier_are_one_transaction(tmp_path: Path) -> None:
    """One text, the divide first and the multiply second, inside START/COMMIT.

    Sent as one `run_statement()` so `mysql` stopping at an error rolls back the
    divide with it (T100's route): a half-run re-install would leave creatures
    at stock with the mod still recorded as applied.
    """
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("2"))
    applier.install(_mob(), _all("3"))

    text = db.texts[-1]
    assert text.startswith("START TRANSACTION;\n") and text.rstrip().endswith("COMMIT;")
    divide = text.index("HealthModifier=HealthModifier/2")
    multiply = text.index("HealthModifier=HealthModifier*3")
    assert divide < multiply
    assert len(db.texts) == 2, "one text per press"


@pytest.mark.parametrize("item_id", MOB_MODS)
def test_every_mob_mod_goes_install_reinstall_remove_back_to_stock(
    tmp_path: Path, item_id: str
) -> None:
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(item_id), _all("2"))
    applier.install(_mob(item_id), _all("0.5"))
    assert db.row() == _times(0.5)
    applier.remove(_mob(item_id), None)
    assert db.row() == BASE


def test_install_after_a_remove_does_not_divide_by_the_removed_values(tmp_path: Path) -> None:
    """T104 keeps the ANSWERS after a Remove; they are not a record that it is applied.

    Mutation: treat the remembered answers as the applied record and this
    reads x1.5 (divided by 2 that is no longer there, times 3).
    """
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("2"))
    applier.remove(_mob(), None)
    assert db.row() == BASE
    assert applier.remembered_answers(_mob())["hp"] == "2", "T104's pre-fill is kept"
    assert applier.applied_record(_mob()) == (None, "")

    applier.install(_mob(), _all("3"))
    assert db.row() == _times(3)


def test_the_record_says_what_the_database_holds_after_each_press(tmp_path: Path) -> None:
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    assert applier.applied_record(_mob()) == (None, "")
    applier.install(_mob(), {"hp": "2", "dmg": "0.5", "arm": "1", "spd": "1.5"})
    assert applier.applied_record(_mob()) == (
        {"hp": "2", "dmg": "0.5", "arm": "1", "spd": "1.5"},
        "",
    )
    applier.remove(_mob(), None)
    assert applier.applied_record(_mob()) == (None, "")
    assert "applied" in _record(tmp_path), "the map is kept, only the entry goes"


def test_a_silent_install_records_the_values_it_actually_rendered(tmp_path: Path) -> None:
    """`values=None` renders the defaults; the record must say so or the next run compounds."""
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), None)
    assert applier.applied_record(_mob())[0] == {
        "hp": "0.25",
        "dmg": "0.25",
        "arm": "0.25",
        "spd": "1.5",
    }
    applier.install(_mob(), _all("2"))
    assert db.row() == _times(2)


def test_remove_divides_by_the_applied_record_not_by_the_last_answers(tmp_path: Path) -> None:
    """The applied record is what the database holds; the answers are only a pre-fill."""
    db = _Db()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("2"))
    # Another press saved different answers without applying them (a configure
    # of some future step, a hand edit): Remove must still divide by 2.
    assert module_answers.record_answers(tmp_path, _mob(), _all("4")) == ""
    applier.remove(_mob(), None)
    assert db.row() == BASE


# ------------------------------------------------ never a guess, never half


@pytest.mark.parametrize(
    "entry",
    [
        {"hp": "lots", "dmg": "2", "arm": "2", "spd": "2"},
        {"hp": "0", "dmg": "2", "arm": "2", "spd": "2"},
        {"hp": "2", "dmg": "2", "arm": "2"},
        "2",
    ],
    ids=["not-a-number", "zero", "missing-key", "not-an-object"],
)
def test_a_rerun_over_an_unusable_record_is_refused_and_changes_nothing(
    tmp_path: Path, entry: object
) -> None:
    """Ruling: a known re-run with no usable record is refused, never guessed.

    Zero is unusable because nothing divides by it: an install at x0 cannot be
    undone by arithmetic, and MySQL would write NULL.
    """
    payload = {"applied": {"mod/baby-mobs": entry}, "modules": {}, "schema_version": 1}
    path = tmp_path / module_answers.ANSWERS_FILE
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    db = _Db()
    applier = Applier(tmp_path, sql=db)

    refusal = applier.reapply_refusal(_mob())
    assert refusal is not None and "Remove it first" in refusal
    with pytest.raises(ApplyError, match="Remove it first") as caught:
        applier.install(_mob(), _all("3"))
    assert "Nothing was changed" in str(caught.value)
    assert db.texts == [] and db.row() == BASE
    assert path.read_bytes() == before


def test_a_first_install_has_nothing_to_refuse(tmp_path: Path) -> None:
    assert Applier(tmp_path, sql=_Db()).reapply_refusal(_mob()) is None


def test_a_failed_reinstall_leaves_the_applied_record_as_it_was(tmp_path: Path) -> None:
    Applier(tmp_path, sql=_Db()).install(_mob(), _all("2"))
    with pytest.raises(ApplyError):
        Applier(tmp_path, sql=_Db(fail=True)).install(_mob(), _all("3"))
    assert Applier(tmp_path).applied_record(_mob())[0] == _all("2")


def test_a_failed_first_install_leaves_no_applied_record(tmp_path: Path) -> None:
    with pytest.raises(ApplyError):
        Applier(tmp_path, sql=_Db(fail=True)).install(_mob(), _all("3"))
    assert Applier(tmp_path).applied_record(_mob()) == (None, "")


def test_a_failed_remove_keeps_the_applied_record(tmp_path: Path) -> None:
    Applier(tmp_path, sql=_Db()).install(_mob(), _all("2"))
    with pytest.raises(ApplyError):
        Applier(tmp_path, sql=_Db(fail=True)).remove(_mob(), None)
    assert Applier(tmp_path).applied_record(_mob())[0] == _all("2")


def test_an_unwritable_record_refuses_the_install_before_any_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: SQL applied with no record of it is the defect this ticket is about."""
    monkeypatch.setattr(module_answers, "record_pending", lambda *_a, **_k: "disk full")
    db = _Db()
    with pytest.raises(ApplyError, match="disk full"):
        Applier(tmp_path, sql=db).install(_mob(), _all("2"))
    assert db.texts == []


def test_an_unclearable_record_refuses_the_remove_before_any_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _Db()
    Applier(tmp_path, sql=db).install(_mob(), _all("2"))
    monkeypatch.setattr(module_answers, "record_pending", lambda *_a, **_k: "read-only")
    with pytest.raises(ApplyError, match="read-only"):
        Applier(tmp_path, sql=db).remove(_mob(), None)
    assert db.row() == _times(2) and len(db.texts) == 1


def test_a_record_written_by_t104_alone_is_kept_intact(tmp_path: Path) -> None:
    """The applied map sits beside T104's `modules`; neither write loses the other."""
    applier = Applier(tmp_path, sql=_Db())
    applier.install(_mob(), _all("2"))
    record = _record(tmp_path)
    assert record["modules"] == {"mod/baby-mobs": _all("2")}  # type: ignore[comparison-overlap]
    assert record["applied"] == {"mod/baby-mobs": _all("2")}  # type: ignore[comparison-overlap]


# ------------------------------------------------ the generic rule


def test_every_relative_manifest_can_be_undone_in_one_transaction() -> None:
    """The combined text is built from inline direct statements on one database only.

    A file, a `db-import` step, a precondition or a verify would each change
    what "one text" means, so the re-run refuses them; none of the four has one.
    """
    root = wotlk_modules.BUNDLED_MANIFESTS_DIR / "wow-wotlk" / "mods"
    shipped = [wotlk_modules.store().load("mod", path.stem) for path in root.glob("*.json")]
    assert len(shipped) > len(MOB_MODS), root
    relative = [m for m in shipped if apply_module.reapplies_on_top(m)]
    assert sorted(m.id for m in relative) == list(MOB_MODS)
    for manifest in relative:
        assert apply_module.reapply_steps_problem(manifest) == "", manifest.id


# ------------------------------------------------ whole-number columns


def test_whole_number_columns_can_drift_by_one_and_that_is_pinned(tmp_path: Path) -> None:
    """Honest about the integer columns (cold review, T115 fix wave): no rounding logic.

    Attack times are INTEGER on AzerothCore. At x0.5 an odd 1333 ms becomes
    666.5, stored as 667; dividing back gives 1334. So a round trip through a
    fractional multiplier can leave an attack time 1 ms off, and a re-install
    undoes through the same rounding. The float modifiers are unaffected.
    """
    base = (1.0, 1.0, 1.0, 1333, 1333)
    db = _Db(base=base)
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("0.5"))
    assert db.row()[3:] == (667, 667)
    applier.install(_mob(), _all("1"))
    assert db.row() == (1.0, 1.0, 1.0, 1334, 1334), "base x new, the attack times 1 ms off"
    applier.remove(_mob(), None)
    assert db.row() == (1.0, 1.0, 1.0, 1334, 1334)


# ------------------------------------------------ a kill part-way (fix wave)


def _snapshot(server_dir: Path, into: list[Path]) -> Callable[..., None]:
    """Copy the record as it is on disk now into a fresh server folder: a kill right here."""

    def take(*_args: object) -> None:
        folder = server_dir.parent / f"killed-{len(into)}"
        folder.mkdir()
        record = server_dir / module_answers.ANSWERS_FILE
        if record.is_file():
            (folder / module_answers.ANSWERS_FILE).write_bytes(record.read_bytes())
        into.append(folder)

    return take


def _no_database_start(snap: Callable[..., None]) -> Callable[..., bool]:
    def start(self: Applier, manifest: Manifest, when: str, log: object) -> bool:
        snap()
        return False

    return start


def test_a_kill_while_the_database_starts_leaves_the_old_record_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review, Important: the record was written before a wait of up to 180 s.

    `_start_the_database_for_direct_sql()` waits for the database to be healthy.
    Killed there, nothing has been sent, so the record must still say x2.

    Fails on cd72db01: the record already said x3 while the database held x2.
    """
    server = tmp_path / "srv"
    server.mkdir()
    Applier(server, sql=_Db()).install(_mob(), _all("2"))
    kills: list[Path] = []
    monkeypatch.setattr(
        Applier, "_start_the_database_for_direct_sql", _no_database_start(_snapshot(server, kills))
    )
    Applier(server, sql=_Db()).install(_mob(), _all("3"))
    assert Applier(kills[0]).applied_record(_mob()) == (_all("2"), "")


def test_a_kill_while_the_database_starts_for_a_remove_keeps_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails on cd72db01: Remove cleared the record before the wait."""
    server = tmp_path / "srv"
    server.mkdir()
    Applier(server, sql=_Db()).install(_mob(), _all("2"))
    kills: list[Path] = []
    monkeypatch.setattr(
        Applier, "_start_the_database_for_direct_sql", _no_database_start(_snapshot(server, kills))
    )
    Applier(server, sql=_Db()).remove(_mob(), None)
    assert Applier(kills[0]).applied_record(_mob()) == (_all("2"), "")


@pytest.mark.parametrize("press", ["install", "remove"])
def test_a_kill_at_the_statement_leaves_the_record_unusable_not_wrong(
    tmp_path: Path, press: str
) -> None:
    """Killed while the statement is in flight, Yu'lon cannot know if it committed.

    So the record is PENDING there, which reads as unusable: a re-run is refused
    and Remove asks -- the existing refusal and dialog, no new UI. After the
    commit the pending entry becomes the applied one.

    Fails on cd72db01: at the statement the record already said the new values
    (install) or nothing (remove), each a usable answer that may be wrong.
    """
    server = tmp_path / "srv"
    server.mkdir()
    Applier(server, sql=_Db()).install(_mob(), _all("2"))
    kills: list[Path] = []
    applier = Applier(server, sql=_Db(base=_times(2), on_statement=_snapshot(server, kills)))
    if press == "install":
        applier.install(_mob(), _all("3"))
    else:
        applier.remove(_mob(), None)

    killed = Applier(kills[0])
    applied, why = killed.applied_record(_mob())
    assert applied is None and why, why
    assert killed.reapply_refusal(_mob()) is not None
    after = _record(server)
    assert _mob().type + "/" + _mob().id not in after.get("pending", {})  # type: ignore[operator]
    expected = (_all("3"), "") if press == "install" else (None, "")
    assert Applier(server).applied_record(_mob()) == expected


def test_a_failed_statement_restores_the_previous_record(tmp_path: Path) -> None:
    """Pending while the statement runs; the old record back, and no pending, when it fails.

    Fails on cd72db01: during the statement the record read x3, usable.
    """
    server = tmp_path / "srv"
    server.mkdir()
    Applier(server, sql=_Db()).install(_mob(), _all("2"))
    kills: list[Path] = []
    with pytest.raises(ApplyError):
        Applier(server, sql=_Db(fail=True, on_statement=_snapshot(server, kills))).install(
            _mob(), _all("3")
        )
    assert Applier(kills[0]).applied_record(_mob())[0] is None
    assert Applier(server).applied_record(_mob()) == (_all("2"), "")
    assert "mod/baby-mobs" not in _record(server).get("pending", {})  # type: ignore[operator]


@pytest.mark.parametrize("press", ["install", "remove"])
def test_with_no_sql_runner_nothing_runs_and_the_record_is_left_alone(
    tmp_path: Path, press: str
) -> None:
    """Cold review, Minor 3: a press whose SQL is skipped must not change the record.

    Fails on cd72db01: Remove cleared it, and Install wrote the new values,
    with nothing sent to the database.
    """
    Applier(tmp_path, sql=_Db()).install(_mob(), _all("2"))
    skipped = Applier(tmp_path, sql=None)
    if press == "install":
        report = skipped.install(_mob(), _all("3"))
    else:
        report = skipped.remove(_mob(), None)
    assert any("no SQL runner" in line for line in report.skipped), report.skipped
    assert Applier(tmp_path).applied_record(_mob()) == (_all("2"), "")
