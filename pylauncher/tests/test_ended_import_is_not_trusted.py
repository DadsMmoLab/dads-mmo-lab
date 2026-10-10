"""T658: databases an ENDED import left are cleared and imported again, whatever they read as.

Measured on m910q (2026-10-10), the Steam Deck player's path: an import started by hand
(`docker compose up -d <auth> <world>`, given up on after 163 s) was still running when
Install was pressed with a working Compose. The press ended it (T539) and then probed:
AzerothCore's importer creates every schema's `updates` tables before it applies the
updates, so the killed import read `imported`, the stage left it alone, and the server
came up with an empty realm list. `end_one_shot()` now records every kill in the folder,
and the import stage never trusts such schemas.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.support_native import ABSENT, IMPORTED, PARTIAL, Recorder, engine
from yulon import docker
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller_wow_wotlk import repair
from yulon.manifest import Db

IMPORTER = "ac-db-import"
POPULATED_DONE = docker.ImportState("populated", "651 accounts", complete=True)


def _press(rec: Recorder, server: Path) -> list[str]:
    return list(engine(rec).run(InstallOptions(server_dir=server)))


def _an_install_whose_import_was_ended(rec: Recorder, server: Path) -> None:
    """A first press that cloned and built but whose import did not finish, then the record.

    The record only ever sits in a folder an install already filled, so it is written
    after a real first press rather than into an empty folder.
    """
    rec.verify_error = docker.DockerCommandError("the import exited 137")
    with pytest.raises(InstallerError):
        _press(rec, server)
    rec.verify_error = None
    rec.calls.clear()
    docker.one_shot_ended_marker(server, IMPORTER).write_text("ended\n")


def test_an_importer_this_press_has_to_end_is_never_read_as_finished(tmp_path: Path) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[IMPORTED, IMPORTED])
    rec.one_shots_running = [IMPORTER]
    lines = _press(rec, server)
    assert "reset-everything" in rec.calls, rec.calls
    assert rec.calls.index("reset-everything") < rec.calls.index(f"one-shot:{IMPORTER}")
    # A world the player started by hand is stopped before anything is dropped.
    assert rec.calls.index("stop_servers") < rec.calls.index("reset-everything"), rec.calls
    assert any("ended before it finished" in line for line in lines), lines
    assert not any("leaving them alone" in line for line in lines), lines
    # The import that followed finished and was verified: the record goes.
    assert not docker.one_shot_ended_marker(server, IMPORTER).exists()


@pytest.mark.parametrize("before", [IMPORTED, PARTIAL], ids=["imported", "partial"])
def test_an_import_ended_by_an_earlier_press_is_cleared_whole(
    tmp_path: Path, before: docker.ImportState
) -> None:
    """A Stop, or a Yu'lon closed mid-import, left the record; this press reads it."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT])
    _an_install_whose_import_was_ended(rec, server)
    rec.probe_answers = [before, IMPORTED]
    _press(rec, server)
    assert "reset-everything" in rec.calls, rec.calls
    assert "reset" not in rec.calls, rec.calls
    assert f"one-shot:{IMPORTER}" in rec.calls
    assert not docker.one_shot_ended_marker(server, IMPORTER).exists()


def test_without_the_record_a_finished_import_is_left_alone(tmp_path: Path) -> None:
    rec = Recorder(probe_answers=[IMPORTED])
    lines = _press(rec, tmp_path / "s")
    assert not any(call.startswith("reset") for call in rec.calls), rec.calls
    assert "stop_servers" not in rec.calls, rec.calls
    assert f"one-shot:{IMPORTER}" not in rec.calls
    assert any("leaving them alone" in line for line in lines), lines


def test_player_data_is_never_cleared_even_after_an_ended_import(tmp_path: Path) -> None:
    """Someone played on it: the record does not make their characters a reset."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT])
    _an_install_whose_import_was_ended(rec, server)
    rec.probe_answers = [POPULATED_DONE]
    _press(rec, server)
    assert not any(call.startswith("reset") for call in rec.calls), rec.calls
    assert f"one-shot:{IMPORTER}" not in rec.calls


def test_a_failed_import_keeps_the_record(tmp_path: Path) -> None:
    """Only a verified import removes it: a failure leaves the next press as wary."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT])
    _an_install_whose_import_was_ended(rec, server)
    rec.probe_answers = [IMPORTED, IMPORTED]
    rec.verify_error = docker.DockerCommandError("the import exited 1")
    with pytest.raises(InstallerError, match="exited 1"):
        _press(rec, server)
    assert "reset-everything" in rec.calls, rec.calls
    assert docker.one_shot_ended_marker(server, IMPORTER).exists()


# ------------------------------------------------------------- end_one_shot


class _Daemon:
    def __init__(self, running: list[str], folder: Path) -> None:
        self.running = list(running)
        self.folder = folder

    def __call__(
        self, argv: list[str], *a: object, **k: object
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] == "ps":
            out = "\n".join(f"{name}\t{self.folder}" for name in self.running)
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[0] == "kill":
            self.running = [name for name in self.running if name not in argv[1:]]
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


@pytest.fixture
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "pinned_project_name", lambda server_dir: "wow-server")
    monkeypatch.setattr(docker.time, "sleep", lambda seconds: None)


@pytest.mark.usefixtures("_quiet")
def test_ending_a_running_importer_records_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(docker, "_docker", _Daemon([IMPORTER], tmp_path))
    assert docker.end_one_shot(IMPORTER, tmp_path, record_ended=True) is None
    assert docker.one_shot_ended_marker(tmp_path, IMPORTER).is_file()


@pytest.mark.usefixtures("_quiet")
def test_ending_a_download_leaves_no_record_nothing_would_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The server-data download re-checks its own volume; a record of it would only pile up."""
    monkeypatch.setattr(docker, "_docker", _Daemon(["ac-client-data-init"], tmp_path))
    assert docker.end_one_shot("ac-client-data-init", tmp_path) is None
    assert not list(tmp_path.glob(".yulon-*-ended"))


def test_the_install_ends_a_leftover_download_without_a_record(tmp_path: Path) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    rec.one_shots_running = ["ac-client-data-init"]
    _press(rec, server)
    assert not list(server.glob(".yulon-*-ended"))


@pytest.mark.usefixtures("_quiet")
def test_nothing_to_end_records_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(docker, "_docker", _Daemon([], tmp_path))
    assert docker.end_one_shot(IMPORTER, tmp_path) is None
    assert not docker.one_shot_ended_marker(tmp_path, IMPORTER).exists()


# ---------------------------------------------------------- reset_unfinished


AUTH, CHARACTERS, WORLD = "acore_auth", "acore_characters", "acore_world"


class _Server:
    def __init__(self, tables: dict[str, list[str]], counts: dict[str, int] | None = None) -> None:
        self.tables = tables
        self.counts = counts or {}
        self.dropped: list[str] = []

    def databases(self) -> tuple[str, ...]:
        return ("information_schema", "mysql", *self.tables)

    def run_statement(self, db: Db, statement: str) -> None:
        if statement.startswith("DROP DATABASE"):
            name = statement.split("`")[1]
            self.tables.pop(name, None)
            self.dropped.append(name)

    def query(self, db: Db, statement: str) -> str:
        if "information_schema" in statement:
            return "".join(f"{s}\t{t}\n" for s, names in self.tables.items() for t in names)
        wanted = [
            self.counts.get(f"{schema}.{table}", 0)
            for schema, table in repair.KEY_TABLES.items()
            if f"`{schema}`.`{table}`" in statement
        ]
        return "\t".join(str(n) for n in wanted) + "\n"


def _done(*tables: str) -> list[str]:
    return [*tables, *repair.IMPORT_MARKERS]


def test_everything_drops_schemas_that_only_look_finished() -> None:
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    assert repair.reset_unfinished(server, server) == ()  # type: ignore[arg-type]
    dropped = repair.reset_unfinished(server, server, everything=True)  # type: ignore[arg-type]
    assert dropped == (AUTH, CHARACTERS, WORLD)


def test_everything_still_refuses_player_data() -> None:
    server = _Server(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")},
        counts={f"{AUTH}.account": 3},
    )
    with pytest.raises(RuntimeError):
        repair.reset_unfinished(server, server, everything=True)  # type: ignore[arg-type]
    assert server.dropped == []


def test_the_apps_reset_seam_hands_everything_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam the real Install press builds, not a test double, carries the flag."""
    from yulon import install_wiring
    from yulon.catalog.catalog import load_catalog

    asked: list[bool] = []

    def reset_unfinished(
        sql: object, mysql: object, *, everything: bool = False
    ) -> tuple[str, ...]:
        asked.append(everything)
        return ()

    monkeypatch.setattr(repair, "reset_unfinished", reset_unfinished)
    _probe, reset = install_wiring.import_gate_for(load_catalog().get("wow-wotlk"))
    assert reset is not None
    reset()
    reset(everything=True)
    assert asked == [False, True]


# ------------------------------------------------------------------- Repair


def test_repair_clears_what_an_ended_import_left_and_imports_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repair ends a leftover importer too (T539): it must not then refuse "nothing to repair"."""
    from tests.test_docker import SPEC, _probe, _repair_doubles

    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    docker.one_shot_ended_marker(tmp_path, SPEC.import_service).write_text("ended\n")
    resets: list[bool] = []

    def reset(*, everything: bool = False) -> tuple[str, ...]:
        resets.append(everything)
        return (AUTH, CHARACTERS, WORLD)

    assert docker.repair_import(SPEC, tmp_path, _probe(IMPORTED, IMPORTED), reset=reset) is True
    assert resets == [True]
    assert ["docker", "compose", "up", "--no-deps", SPEC.import_service] in calls
    assert not docker.one_shot_ended_marker(tmp_path, SPEC.import_service).exists()


def test_a_repair_with_no_ended_import_still_refuses_a_finished_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, _probe, _repair_doubles

    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    with pytest.raises(docker.DockerCommandError, match="nothing to repair"):
        docker.repair_import(SPEC, tmp_path, _probe(IMPORTED), reset=lambda **_k: ())


# ------------------------------------------------------- Start and the Server tab


def _controller(tmp_path: Path, state: docker.ImportState):  # type: ignore[no-untyped-def]
    from yulon.controller import Controller
    from yulon.docker import ContainerSpec

    asked: list[str] = []

    def probe() -> docker.ImportState:
        asked.append("probe")
        return state

    spec = ContainerSpec(
        db="t-db", auth="t-auth", world="t-world", ports=(1, 2), import_service=IMPORTER
    )
    return Controller(spec, tmp_path, import_probe=probe), asked


def test_a_cut_off_import_reads_as_unfinished_to_the_server_tab(tmp_path: Path) -> None:
    from yulon.controller import ENDED_IMPORT_DETAIL

    ctl, _ = _controller(tmp_path, IMPORTED)
    assert ctl.import_state() == IMPORTED
    docker.one_shot_ended_marker(tmp_path, IMPORTER).write_text("ended\n")
    state = ctl.import_state()
    assert state.state == "partial" and state.repairable
    assert state.detail == ENDED_IMPORT_DETAIL
    played, _ = _controller(tmp_path, POPULATED_DONE)
    assert played.import_state() == POPULATED_DONE, "player data is never offered a repair"


def test_start_refuses_a_cut_off_import_and_points_at_repair(tmp_path: Path) -> None:
    from yulon.controller import IMPORT_ENDED, ImportEnded

    ctl, asked = _controller(tmp_path, IMPORTED)
    ctl.refuse_an_ended_import()
    assert asked == [], "no record, no question"
    docker.one_shot_ended_marker(tmp_path, IMPORTER).write_text("ended\n")
    with pytest.raises(ImportEnded) as refused:
        ctl.refuse_an_ended_import()
    assert str(refused.value) == IMPORT_ENDED
    assert "Repair" in IMPORT_ENDED
    assert refused.value.state.repairable
    played, _ = _controller(tmp_path, POPULATED_DONE)
    played.refuse_an_ended_import()


def test_the_server_tab_offers_repair_beside_the_refusal(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_controller_view import WOTLK, _Ps, _services
    from yulon import runner
    from yulon.controller import ENDED_IMPORT_DETAIL, IMPORT_ENDED, ImportEnded
    from yulon.ui.controller_view import ControllerView
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(runner, "run", _Ps())
    view = ControllerView(
        WOTLK, _services(_Ps(), tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )
    assert not view.repair_button.isVisibleTo(view)

    def refused(*_a: object, **_k: object) -> None:
        raise ImportEnded(IMPORT_ENDED, state=docker.ImportState("partial", ENDED_IMPORT_DETAIL))

    monkeypatch.setattr(view.services.controller, "start", refused)
    view.start_server()
    assert view.problem_label.text().startswith(IMPORT_ENDED[:40])
    assert view.repair_button.isVisibleTo(view)
    assert ENDED_IMPORT_DETAIL in view.repair_label.text()
