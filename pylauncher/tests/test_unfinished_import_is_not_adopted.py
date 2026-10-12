"""T755: a CMaNGOS or Centurion install that did not finish installing is not started half-made.

These games have no import service, so T658/T678's import record and Start's probe
never reached them, and their login tables read `present` once the auth part of the
SQL plan ran, long before the world content. The Stop popup said "Use existing…,
nothing is lost"; "Use existing…" took the folder and Start ran mangosd on a
half-imported world.

The install record's `completed` list is the first half of the evidence (`import`
goes into it only once the plan verified). Round 2 (cold review): the record alone
must not refuse, because a player who followed the old popup and has played since
holds a record without `import` over databases with characters in them, and the way
out (Install again) refuses those as "already hold data". So Start also asks the
family's own import gate (`sqlplan.MarkerGate`) and refuses only on `absent` or
`partial`, the two states the Install route clears and redoes. `imported`,
`populated` and `unreadable` start as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests import test_families_cmangos as cm
from tests.support_native import IMPORTED
from yulon import docker
from yulon.catalog import composegen, installer, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families import family_for, sqlplan
from yulon.catalog.installer import cancelled_install_message, unfinished_import
from yulon.controller import Controller, StartRefused

CATALOG = load_catalog()
ABSENT = docker.ImportState("absent", "none of the plan's schemas exist")
PARTIAL = docker.ImportState("partial", "schemas exist, no marker, no player rows")
POPULATED = docker.ImportState("populated", "characters holds rows beyond the seeds")
MARKED = docker.ImportState("imported", "a marker row exists", complete=True)
UNREADABLE = docker.ImportState("unreadable", "the database container is not running")


def _entry(game: str) -> CatalogEntry:
    return CATALOG.get(game)


def _record(folder: Path, entry: CatalogEntry, *, through: str, game: str = "") -> None:
    """The record an install of `entry` writes, its `completed` list ending at `through`."""
    stages = family_for(entry).STAGE_NAMES
    done = stages[: stages.index(through) + 1]
    native.write_state(
        folder,
        native.InstallState(
            game_id=game or entry.id,
            install_id="id",
            family=entry.install.native.family if entry.install.native else "",
            completed=done,
        ),
    )


def _databases_read(
    monkeypatch: pytest.MonkeyPatch, state: docker.ImportState, distros: list[Any] | None = None
) -> list[str]:
    """`installer.install_import_state()` answering `state`; the list records each ask."""
    asked: list[str] = []

    def probe(entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None = None) -> Any:
        asked.append(entry.id)
        if distros is not None:
            distros.append(wsl_distro)
        return state

    monkeypatch.setattr(installer, "install_import_state", probe, raising=False)
    return asked


def _controller(entry: CatalogEntry, folder: Path, monkeypatch: pytest.MonkeyPatch) -> Controller:
    """The real refusal chain; only the steps that would need Docker are answered here."""
    controller = Controller(entry.container_spec(), folder)
    controller.entry = entry
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    monkeypatch.setattr(controller, "refuse_a_taken_port", lambda: None)
    monkeypatch.setattr(controller, "refuse_a_missing_image", lambda: None)
    monkeypatch.setattr(controller, "refuse_a_missing_database", lambda: None)
    monkeypatch.setattr(controller, "_refresh_world_data", lambda: None)
    return controller


class _Started(Exception):
    """Raised where the servers' writes would begin: the chain let the Start through."""


def _press(controller: Controller, press: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def started(*_a: object, **_k: object) -> None:
        raise _Started

    # The first step after every refusal, on each press (the realm-port UPDATE).
    monkeypatch.setattr(controller, "_ask_before_the_servers", started)
    getattr(controller, press)()


PRESSES = ["start", "refuse_before_a_stop", "stop_conflicting_and_start"]


@pytest.mark.parametrize("press", PRESSES)
@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"])
@pytest.mark.parametrize("state", [PARTIAL, ABSENT], ids=["partial", "absent"])
def test_start_refuses_an_unfinished_install_whose_import_never_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game: str, press: str, state: Any
) -> None:
    """Stopped during `import` (or before it), the databases holding no finished import.

    Mutations this catches: dropping the refusal from any of the three presses.
    """
    entry = _entry(game)
    _record(tmp_path, entry, through="conf")
    asked = _databases_read(monkeypatch, state)
    with pytest.raises(StartRefused) as refused:
        _press(_controller(entry, tmp_path, monkeypatch), press, monkeypatch)
    said = str(refused.value)
    assert "did not finish installing" in said and str(tmp_path) in said, said
    assert "Remove from Yu'lon…" in said and "press Install" in said, said
    assert asked == [game], "the databases were not asked"


@pytest.mark.parametrize("press", PRESSES)
@pytest.mark.parametrize(
    "state", [POPULATED, MARKED, UNREADABLE], ids=["populated", "marker", "unreadable"]
)
def test_start_goes_on_when_the_databases_do_not_read_unfinished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, press: str, state: Any
) -> None:
    """The record lacks `import`, but the databases are a played world, a finished import,
    or could not be asked: never a lock-out on the record alone.

    `populated` is the cold review's case: the old popup sent the player to "Use
    existing…", they have played since, and Install again would refuse their data.

    Mutations this catches: refusing on the record alone, on `populated`, on
    `imported`, on `unreadable`.
    """
    entry = _entry("wow-tbc")
    _record(tmp_path, entry, through="conf")
    _databases_read(monkeypatch, state)
    with pytest.raises(_Started):
        _press(_controller(entry, tmp_path, monkeypatch), press, monkeypatch)


def test_a_server_inside_wsl_has_its_databases_asked_through_its_distro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked of this host's Docker, a WSL server's database answers `unreadable` (or worse).

    Mutation this catches: the Controller not handing its distro to the probe.
    """
    entry = _entry("wow-tbc")
    _record(tmp_path, entry, through="conf")
    distros: list[Any] = []
    _databases_read(monkeypatch, PARTIAL, distros)
    controller = _controller(entry, tmp_path, monkeypatch)
    controller.wsl_distro = "Ubuntu"
    with pytest.raises(StartRefused):
        controller.refuse_an_unfinished_install()
    assert distros == ["Ubuntu"]


@pytest.mark.parametrize("game", ["wow-tbc", "wow-centurion"])
def test_a_finished_import_is_not_asked_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """Stopped in `up` or `ready` (unrecorded): `import` is in the record; nothing is asked.

    Mutation this catches: asking the databases on the record's presence alone.
    """
    entry = _entry(game)
    _record(tmp_path, entry, through="import")
    asked = _databases_read(monkeypatch, PARTIAL)
    assert unfinished_import(entry, tmp_path) is None
    assert installer.unfinished_install_refusal(entry, tmp_path) is None
    assert asked == []


def test_a_folder_without_a_record_is_not_refused(tmp_path: Path) -> None:
    """A bash-era or hand-made server has no record: nothing Yu'lon wrote says it is unfinished."""
    assert unfinished_import(_entry("wow-tbc"), tmp_path) is None


def test_another_games_record_is_not_this_refusal(tmp_path: Path) -> None:
    """A Vanilla record in a folder opened as TBC is the guard's and the family check's to say."""
    _record(tmp_path, _entry("wow-tbc"), through="conf", game="wow-vanilla")
    assert unfinished_import(_entry("wow-tbc"), tmp_path) is None


def test_an_azerothcore_server_keeps_its_own_import_record(tmp_path: Path) -> None:
    """WotLK/Unbound are refused by T658/T678's record and probe, which offer Repair.

    Mutation this catches: dropping the import-service check, which would send a WotLK
    player to remove and reinstall where Repair the database… finishes the job.
    """
    entry = _entry("wow-wotlk")
    _record(tmp_path, entry, through="client-data")
    assert unfinished_import(entry, tmp_path) is None


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"])
def test_the_real_import_gate_is_asked_through_the_servers_own_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """`install_import_state()` builds the family's `MarkerGate` from the entry: this game's
    database container and its generated password. A database that does not answer reads
    `unreadable`, never `partial`.
    """
    entry = _entry(game)
    password_file = entry.install.password.file
    assert password_file, "these games generate their database password"
    (tmp_path / password_file).write_text("pw-123\n", encoding="utf-8")
    seen: list[tuple[str, str]] = []
    distros: list[object] = []

    def no_answer(container: str, client: str, password: str, *a: object, **k: object) -> str:
        seen.append((container, password))
        distros.append(k.get("wsl_distro"))
        raise docker.DockerCommandError("the database container is not running")

    monkeypatch.setattr(docker, "sql_query", no_answer)
    state = installer.install_import_state(entry, tmp_path, wsl_distro="Ubuntu")
    assert state.state == "unreadable", state
    assert seen and seen[0] == (entry.container_spec().db, "pw-123"), seen
    assert distros == ["Ubuntu"], "the database was asked of the wrong Docker"


def test_no_password_reads_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A folder whose password file is gone cannot be asked; Start goes on as before.

    Mutation this catches: asking the database with no password (Docker is never asked).
    """

    def asked(*_a: object, **_k: object) -> str:
        raise AssertionError("the database was asked with no password")

    monkeypatch.setattr(docker, "sql_query", asked)
    monkeypatch.setattr(docker, "exec_stdin", asked)
    assert installer.install_import_state(_entry("wow-tbc"), tmp_path).state == "unreadable"


def test_the_way_out_resumes_at_the_import_and_clears_the_half_written_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal's remedy, through the real engine: "Remove from Yu'lon…" keeps the folder
    and its record, and Install on that folder resumes from it. The record leaves `import`
    to run, and the import stage clears a `partial` database before it imports.
    """
    server = cm.server_with_sql(tmp_path)
    _record(server, cm.ENTRY, through="conf")
    state = native.read_state(server, valid=family_for(cm.ENTRY).STAGE_NAMES)
    assert state is not None and not state.has("import") and state.has("conf")
    rec = cm.ready_to_import(PARTIAL, IMPORTED)
    rec.reset_answer = (cm.ENTRY.databases.world,)
    # `engine()` attaches the Recorder's gate; `test_families_cmangos`' autouse fixture is
    # what hands it to `_gate()` there, so the same patch is made here.
    monkeypatch.setattr(
        cm.CmangosInstaller, "_gate", lambda self, ctx: self._test_gate, raising=True
    )
    said = list(cm.engine(rec)._import(cm.context(server)))
    assert rec.calls.index("reset") < rec.calls.index("sql")
    assert any(sqlplan.MARKER_TABLE in s for s in rec.sql_calls), "the import then ran"
    assert any(f"Cleared {cm.ENTRY.databases.world}" in line for line in said), said


def _ours(folder: Path) -> None:
    for name in composegen.COMPOSE_FILES:
        (folder / name).write_text(f"{composegen.GENERATED_MARKER}\nservices: {{}}\n", "utf-8")


def test_the_stop_popup_does_not_offer_use_existing_over_an_unfinished_install(
    tmp_path: Path,
) -> None:
    """installer.py's "Use existing… nothing is lost", said after a Stop in `import`.

    Mutation this catches: leaving `promised_nothing_lost` blind to the record.
    """
    entry = _entry("wow-tbc")
    _ours(tmp_path)
    _record(tmp_path, entry, through="conf")
    said = cancelled_install_message(entry, tmp_path)
    assert "nothing is lost" not in said, said
    assert "had not finished installing" in said
    assert f"Press Install again and choose {tmp_path}" in said


def test_the_stop_popup_still_offers_use_existing_once_the_import_finished(
    tmp_path: Path,
) -> None:
    """Stopped while the server came up: the import is done, and the adoption is right."""
    entry = _entry("wow-tbc")
    _ours(tmp_path)
    _record(tmp_path, entry, through="import")
    said = cancelled_install_message(entry, tmp_path)
    assert "Use existing" in said and "nothing is lost" in said
