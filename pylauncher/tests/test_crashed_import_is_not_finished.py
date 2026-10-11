"""T678: an AzerothCore database import whose importer dies is never recorded as finished.

Two facts made a crashed first import read as a finished one. AzerothCore's importer
creates every schema's `updates` tables before it applies the updates, so one that
dies part-way leaves schemas the probe reads as `imported` (T658, m910q 2026-10-10).
And the install's import and Repair never looked at the importer's exit: they ran it
as attached `compose up --no-deps <importer>`, which exits 0 whatever the one-shot
exits (measured on yulon-ubuntu, Compose 2.40.3, 2026-10-11: a one-shot that printed
`ERROR 1146` and exited 1 left `compose up` at 0, and `--exit-code-from` at 1).

Now the one-shot's own status comes back, a non-zero one is a failure on both routes
whatever the probe reads, and the folder records an import as unfinished from before
it starts until it is verified, so a run cut off with nothing of Yu'lon left to record
it (power lost, Docker or WSL gone) is not trusted either.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support_native import ABSENT, IMPORTED, Recorder, engine, install
from yulon import docker
from yulon.catalog.installer import InstallerError, InstallOptions, InstallStopped
from yulon.controller import ENDED_IMPORT_DETAIL, Controller

IMPORTER = "ac-db-import"
POPULATED_DONE = docker.ImportState("populated", "400 accounts", complete=True)

# The importer's own last lines, from the T706 live run on yulon-ubuntu (2026-10-11).
BROKEN_FILE = "/azerothcore/modules/mod-city-bots/data/sql/db-world/updates/zz_t706_broken.sql"
DIED = (
    ">> Applying update \"zz_t706_broken.sql\" 'AD3D0CA'...",
    "ERROR 1146 (42S02) at line 3: Table 'acore_world.t706_no_such_table' doesn't exist",
    f"Applying of file '{BROKEN_FILE}' to database 'acore_world' failed! If you are a user, "
    "please pull the latest revision from the repository.",
    "Could not update the World database, see log for details.",
)


def _marker(server: Path) -> Path:
    return docker.one_shot_ended_marker(server, IMPORTER)


def _press(
    rec: Recorder, server: Path, importer: docker.AttachedRun | None = None, **overrides: object
) -> list[str]:
    """One Install press. `importer`: what the import one-shot alone comes back with.

    The real post-check, not the Recorder's always-imported double: what it reads is
    what the probe answers, here `imported`, as a dead importer leaves it (T658).
    """
    if importer is not None:

        def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
            rec.calls.append(f"one-shot:{service}")
            return importer if service == IMPORTER else docker.AttachedRun(0, ("ran",))

        overrides["one_shot"] = one_shot
    return install(rec, server, verify_import=docker.verify_import, **overrides)


# ------------------------------------------------------------------ the install


def test_an_install_whose_importer_exits_non_zero_is_not_finished(tmp_path: Path) -> None:
    """THE bug: exit 1, probe `imported` afterwards, and the install went on to start."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError) as raised:
        _press(rec, server, docker.AttachedRun(1, DIED))
    said = str(raised.value)
    assert "zz_t706_broken.sql" in said and "mod-city-bots" in said, said
    assert "acore_world" in said, said
    assert "ERROR 1146" not in said, "the importer's own words are under Details (T214)"
    assert "unfinished" in said and "nothing was started" in said, said
    assert "Details" in said and "ERROR 1146" in raised.value.detail
    assert "start" not in rec.calls, f"the server was started on it: {rec.calls}"
    assert _marker(server).is_file(), "nothing records the import as unfinished"


def test_an_importer_that_exits_with_no_file_named_still_fails(tmp_path: Path) -> None:
    """A database that restarted, a full disk: no update named, its last words instead."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError) as raised:
        _press(rec, server, docker.AttachedRun(1, ("Lost connection to MySQL server",)))
    assert "exit 1" in str(raised.value) and "Lost connection" in raised.value.detail
    assert _marker(server).is_file()


def test_the_next_press_clears_what_the_crashed_import_left_and_imports_again(
    tmp_path: Path,
) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError):
        _press(rec, server, docker.AttachedRun(1, DIED))
    rec.calls.clear()
    rec.probe_answers = [IMPORTED, IMPORTED]
    lines = _press(rec, server, docker.AttachedRun(0, ("World database is up-to-date!",)))
    assert "reset-everything" in rec.calls, rec.calls
    assert rec.calls.index("reset-everything") < rec.calls.index(f"one-shot:{IMPORTER}")
    assert not any("leaving them alone" in line for line in lines), lines
    assert not _marker(server).exists(), "a verified import keeps the record"


def test_the_record_stands_while_the_importer_runs(tmp_path: Path) -> None:
    """Power lost or Yu'lon killed mid-import: nothing of ours runs after, so write it first."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    seen: list[bool] = []

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        if service == IMPORTER:
            seen.append(_marker(server_dir).is_file())
        return docker.AttachedRun(0, ("ran",))

    _press(rec, server, one_shot=one_shot)
    assert seen == [True], seen
    assert not _marker(server).exists()


def test_a_crashed_import_that_left_rows_is_refused_not_called_finished(tmp_path: Path) -> None:
    """A module's own updates seed accounts (City Bots: 400) before the world schema is done.

    Those rows read as player data, which is never cleared; the press says so and stops
    rather than reading the databases as imported and starting the server on them.
    """
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError):
        _press(rec, server, docker.AttachedRun(1, DIED))
    rec.calls.clear()
    rec.probe_answers = [POPULATED_DONE]
    with pytest.raises(InstallerError) as raised:
        _press(rec, server)
    said = str(raised.value)
    assert "did not finish" in said and "400 accounts" in said, said
    assert not any(call.startswith("reset") for call in rec.calls), rec.calls
    assert f"one-shot:{IMPORTER}" not in rec.calls
    assert "start" not in rec.calls, rec.calls


def test_a_stop_whose_importer_comes_back_killed_is_still_the_stop(tmp_path: Path) -> None:
    """With the one-shot's own status back, a Stop's kill can surface as 137: that is the Stop."""
    rec = Recorder()
    cancel = threading.Event()

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        if service != IMPORTER:
            return docker.AttachedRun(0, ("ran",))
        cancel.set()
        return docker.AttachedRun(137, ("importing acore_world",))

    with pytest.raises(InstallStopped):
        list(
            engine(rec, one_shot=one_shot, verify_import=docker.verify_import).run(
                InstallOptions(server_dir=tmp_path / "s"), cancel=cancel
            )
        )


# ---------------------------------------------------------------------- Repair


def _repair_with_exit(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], exit_code: int
) -> None:
    from tests.test_docker import SPEC, _repair_doubles

    _repair_doubles(
        monkeypatch, calls, running={SPEC.db}, import_exit=exit_code, import_output=lambda: DIED
    )


def test_a_repair_whose_importer_exits_non_zero_fails_and_stays_on_offer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, UNIMPORTED, _probe

    calls: list[list[str]] = []
    _repair_with_exit(monkeypatch, calls, 1)
    with pytest.raises(docker.DockerCommandError) as raised:
        docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED))
    said = str(raised.value)
    assert "zz_t706_broken.sql" in said and "mod-city-bots" in said, said
    assert "ERROR 1146" in raised.value.detail, raised.value.detail
    assert _marker(tmp_path).is_file()
    # The Server tab reads it as unfinished, so Repair is offered again and Start refused.
    ctl = Controller(SPEC, tmp_path, import_probe=_probe(IMPORTED))
    state = ctl.import_state()
    assert state.repairable and state.detail == ENDED_IMPORT_DETAIL, state


def test_a_repair_after_a_crashed_one_clears_and_imports_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, UNIMPORTED, _probe

    calls: list[list[str]] = []
    _repair_with_exit(monkeypatch, calls, 1)
    with pytest.raises(docker.DockerCommandError):
        docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED))
    _repair_with_exit(monkeypatch, calls, 0)
    resets: list[bool] = []

    def reset(*, everything: bool = False) -> tuple[str, ...]:
        resets.append(everything)
        return ("acore_auth", "acore_characters", "acore_world")

    assert docker.repair_import(SPEC, tmp_path, _probe(IMPORTED, IMPORTED), reset=reset) is True
    assert resets == [True]
    assert not _marker(tmp_path).exists()


def test_a_repair_records_the_import_before_it_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, UNIMPORTED, _probe, _repair_doubles

    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    real_stream = docker.runner.stream
    seen: list[bool] = []

    def stream(argv: list[str], **kw: object) -> Iterator[str]:
        if "--exit-code-from" in argv or argv[-1] == SPEC.import_service:
            seen.append(_marker(tmp_path).is_file())
        return real_stream(argv, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(docker.runner, "stream", stream)
    assert docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED)) is True
    assert seen == [True], seen
    assert not _marker(tmp_path).exists()


# ------------------------------------------------------------- the one-shot's status


def test_the_import_one_shot_asks_compose_for_its_own_exit_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attached `compose up` exits 0 whatever the one-shot does; `--exit-code-from` does not."""
    seen: list[list[str]] = []

    def stream(argv: list[str], **_kw: object) -> Iterator[str]:
        seen.append(argv)
        yield "applying"
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(docker.runner, "stream", stream)
    run = docker.run_one_shot(IMPORTER, tmp_path)
    assert run.returncode == 1
    assert seen[0][-5:] == ["up", "--no-deps", "--exit-code-from", IMPORTER, IMPORTER], seen


# The end of a real `compose up --exit-code-from ac-db-import` run with a failing update
# (yulon-ubuntu, 2026-10-11): every importer line carries compose's prefix, and compose's
# own closing lines come after them.
COMPOSE_UP_SAID = (
    "Attaching to ac-db-import",
    "ac-db-import  | >> Applying update \"zz_t678_broken.sql\" '5B1F6C2'...",
    "ac-db-import  | ERROR 1146 (42S02) at line 1: Table 'acore_world.t678_no_such_table' "
    "doesn't exist",
    f"ac-db-import  | Applying of file '{BROKEN_FILE}' to database 'acore_world' failed!",
    "ac-db-import  | Could not update the World database, see log for details.",
    "ac-db-import  |",
    "Aborting on container exit...",
    "Container ac-db-import  Stopping",
    "Container ac-db-import  Stopped",
)


def test_the_importers_words_are_read_through_composes_prefix_and_closing_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Details and the sentence show mysql's error, not "Container ac-db-import Stopped"."""

    def stream(argv: list[str], **_kw: object) -> Iterator[str]:
        yield from COMPOSE_UP_SAID
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(docker.runner, "stream", stream)
    run = docker.run_one_shot(IMPORTER, tmp_path)
    assert run.returncode == 1
    assert "Stopped" not in run.tail[-1] and "Could not update" in run.tail[-2], run.tail
    words = docker.importer_last_words(run.tail)
    assert any(line.startswith("ERROR 1146") for line in words), words
    assert "" not in words, "a bare `ac-db-import  |` line read as a line of its own"
    rebuild = docker.importer_failure(run, "Applying the database updates")
    assert "(ERROR 1146 (42S02)" in rebuild, rebuild
    assert "zz_t678_broken.sql" in docker.import_failed(IMPORTER, tmp_path, run).detail
