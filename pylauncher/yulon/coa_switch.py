"""Realm-type switches for Conquest of Azeroth, under the per-server hold (T812 C3b).

No Qt. The Server tab builds one `RealmSwitch` for an entry with
`realm_types`; every other entry gets None, so nothing about those tabs
changes. The press runs on the worker thread.

The race this closes: `coa_realm.switch()`, the installer import stage,
`_mark_realm_type` and the Server tab's Repair import were guarded only by a
file check (the crash marker), which races. The Server tab press that calls
`switch_to()` takes the existing per-server hold (`docker.server_hold`, the
T607 seam every other mutating press of the tab uses) around the engine's own
checks, so a switch, an Install/Repair import and a second switch can never
run at once; the engine's file-plus-database guards are re-checked under it.

The same hold guards the installer import stage and `Controller.repair_import`
(which the Repair press drives): a press of either while a switch holds is
refused with the holder's sentence, before anything is imported.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from yulon import coa_realm, docker
from yulon.catalog.catalog import CatalogEntry

SWITCH_PRESS = "Switch realm type"
"""The press name the switch holds the server under, and the hold's sentence."""

REPAIR_PRESS = "Repair the database"
"""The press name the Server tab's Repair import holds the server under."""

INSTALL_PRESS = "Install"
"""The press name the installer import stage holds the server under (native.INSTALL_PRESS)."""


class RealmSwitch:
    """One entry's Server-tab switch, held under its per-server hold (T812 C3b)."""

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        hold_server: Callable[[str], AbstractContextManager[None]],
        sql: coa_realm.SqlRunner,
        servers_running: Callable[[], bool],
        run_import: Callable[[coa_realm.SwitchImport], Iterator[str]],
        check_import: Callable[[], None],
        set_realm_row: Callable[[str, str], None],
        write_override: Callable[[Mapping[str, str]], None],
        stop: Callable[[], bool],
        start_db: Callable[[], object],
        start: Callable[[], None],
        bot_prefix: str,
    ) -> None:
        self._entry = entry
        self._server_dir = server_dir
        self._hold_server = hold_server
        self._sql = sql
        self._servers_running = servers_running
        self._run_import = run_import
        self._check_import = check_import
        self._set_realm_row = set_realm_row
        self._write_override = write_override
        self._stop = stop
        self._start_db = start_db
        self._start = start
        self._bot_prefix = bot_prefix

    def current_id(self) -> str:
        """The file's active type id (the default when it is missing, C3a)."""
        return coa_realm.active(self._entry, self._server_dir).id

    def switch_to(self, target_id: str) -> Iterator[str]:
        """Stop, move every table set to `target_id`, point the folder at it, start.

        The hold wraps the whole sequence, and the engine's own pre-checks run inside
        it BEFORE the Stop: a refusal there stops nothing, and a second switch while
        one holds is refused with the hold's sentence before the engine is asked
        anything. After the Stop the press says what the failure left behind: a
        failure BEFORE the engine's first database write starts the ORIGINAL type
        again and says so in one plain sentence; a failure after the first write
        (the crash marker now stands) leaves the server stopped, and the next press
        of Switch finishes or undoes it. The engine still refuses on a running
        server (metadata locks), so the Stop is what lets it through; the restart
        after a pre-write failure runs only when the engine says starting is safe.

        The Stop is the tab's own (it saves every character) and stops the
        database too, which every read of the switch needs (T812 C8 D5, the live
        run: "the acore_characters schema could not be listed (container ... is
        not running)"). So the database alone is started again right after it
        (`start_db`, the start-db seam the install and the start gate use),
        before the engine reads anything; the world and auth stay down.
        """
        with self._hold_server(SWITCH_PRESS):
            original = coa_realm.pre_check(
                self._entry,
                self._server_dir,
                target_id,
                sql=self._sql,
                bot_prefix=self._bot_prefix,
                check_import=self._check_import,
            )
            self._stop()
            before = len(_writes_of(self._sql))
            try:
                self._start_db()
                yield from coa_realm.switch(
                    self._entry,
                    self._server_dir,
                    target_id,
                    sql=self._sql,
                    servers_running=self._servers_running,
                    run_import=self._run_import,
                    set_realm_row=self._set_realm_row,
                    write_override=self._write_override,
                    bot_prefix=self._bot_prefix,
                    check_import=self._check_import,
                )
            except Exception as exc:
                if len(_writes_of(self._sql)) == before and _safe_to_start_again(
                    self._entry, self._server_dir, self._sql
                ):
                    try:
                        self._start()
                    except Exception as start_exc:
                        raise _stopped_after_failed_switch(
                            exc, f"starting it again failed ({start_exc})"
                        ) from exc
                    raise _restarted_after_failed_switch(exc, original) from exc
                raise _stopped_after_failed_switch(exc) from exc
            self._start()


def confirm_text(target_label: str, target_realm_name: str) -> str:
    """The switch confirm dialog's question for the target type (T812 C3b fix round 1)."""
    return (
        "Switching stops the server and keeps each type's characters apart. "
        "If the switch fails before anything is written, the server starts again on "
        "its current type; if it fails after that, it stays stopped, and pressing "
        "Switch realm type again finishes it or puts it back. "
        f"The first switch to {target_label} sets up its databases (a few minutes).\n\n"
        f"Switch to {target_label} ({target_realm_name})?"
    )


def _writes_of(sql: coa_realm.SqlRunner) -> list[str]:
    """The database writes the switch has made so far, as the seam records them."""
    recorded = getattr(sql, "executed", None)
    if isinstance(recorded, list):
        return recorded
    return []


def _safe_to_start_again(entry: CatalogEntry, server_dir: Path, sql: coa_realm.SqlRunner) -> bool:
    """Whether starting the original type again is safe after a pre-write failure."""
    if not callable(getattr(sql, "query", None)):
        return True
    try:
        return coa_realm.refusal(entry, server_dir, sql) is None
    except Exception:
        return False


def _restarted_after_failed_switch(exc: BaseException, original: str) -> BaseException:
    """`exc`, plus the one plain sentence saying the original type started again."""
    message = (
        f"{exc} The switch did not go through, and the server started again "
        f"on the {original} realm type."
    )
    if isinstance(exc, coa_realm.RealmSwitchError):
        return coa_realm.RealmSwitchError(message)
    return RuntimeError(message)


def _stopped_after_failed_switch(exc: BaseException, extra: str = "") -> BaseException:
    """`exc`, plus the stopped sentence naming the next Switch press as the way out."""
    tail = f" {extra}." if extra else ""
    message = (
        f"{exc} The server is stopped. Switch realm type again to finish the switch "
        f"or put it back.{tail}"
    )
    if isinstance(exc, coa_realm.RealmSwitchError):
        return coa_realm.RealmSwitchError(message)
    return RuntimeError(message)


def held_import(
    hold_server: Callable[[str], AbstractContextManager[object]],
    press: str,
    call: Callable[[], Iterator[str]],
) -> Iterator[str]:
    """Run an install/import stage under the per-server hold (T812 C3b).

    The installer import stage and `_mark_realm_type` both reach the plain
    schemas, which a switch moves at the same time: while a switch holds, this
    is refused with its sentence before anything is imported; a second press
    of the same stage holds in turn like every other held press.
    """
    with hold_server(press):
        yield from call()


def held_repair(
    hold_server: Callable[[str], AbstractContextManager[None]],
    press: str,
    call: Callable[[], bool],
) -> bool:
    """`held_import()` for the Repair call, which answers a bool, not lines."""
    with hold_server(press):
        return call()


def needs_hold(entry: CatalogEntry) -> bool:
    """Whether this entry's import stages run under the hold: realm-types ones (T812 C3b)."""
    return coa_realm.has_realm_types(entry)


def install_hold(
    reserved: Callable[[str], AbstractContextManager[object]], press: str
) -> Callable[[str], AbstractContextManager[object]]:
    """The installer's hold as the tab's hold seam (T812 C3b).

    The installer caller passes its own `reserved()` bound to the server dir
    and the press it already holds (`native.INSTALL_PRESS` from `start-db`);
    the import stage re-takes it so a Server tab switch (which holds the same
    reservation from outside) is refused with the holder's sentence instead of
    racing. A tiny adapter so the engine stays importable without Qt.
    """

    def hold(_press: str) -> AbstractContextManager[object]:
        return reserved(press)

    return hold


def switch_for(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    hold_server: Callable[[str], AbstractContextManager[None]] | None,
    sql: coa_realm.SqlRunner,
    servers_running: Callable[[], bool],
    run_import: Callable[[coa_realm.SwitchImport], Iterator[str]],
    check_import: Callable[[], None],
    set_realm_row: Callable[[str, str], None],
    write_override: Callable[[Mapping[str, str]], None],
    stop: Callable[[], bool],
    start_db: Callable[[], object],
    start: Callable[[], None],
    bot_prefix: str,
) -> RealmSwitch | None:
    """The tab's switch for an entry with `realm_types`, else None (T812 C3b)."""
    if not coa_realm.has_realm_types(entry):
        return None
    if hold_server is None:
        raise ValueError(f"{entry.id}: a realm-types entry needs the server hold")
    return RealmSwitch(
        entry,
        server_dir,
        hold_server=hold_server,
        sql=sql,
        servers_running=servers_running,
        run_import=run_import,
        check_import=check_import,
        set_realm_row=set_realm_row,
        write_override=write_override,
        stop=stop,
        start_db=start_db,
        start=start,
        bot_prefix=bot_prefix,
    )


@dataclass(frozen=True)
class ImportSeams:
    """What a first switch's import stage runs through (T812 C8 D6/D8); docker by default.

    `service` is the entry's db-import one-shot (`ContainerSpec.import_service`),
    `probe` the install's import probe (`install_wiring.import_gate_for`), and
    the three callables are `docker.end_one_shot`, `docker.run_one_shot` and
    `docker.verify_import` bound to this install's daemon.
    """

    service: str | None
    probe: docker.ImportProbe | None
    end_one_shot: Callable[[str, Path], docker.OneShotLeft | None]
    one_shot: Callable[[str, Path, docker.OutputSink], docker.AttachedRun]
    verify: Callable[[docker.ImportProbe, str, Path, docker.AttachedRun], docker.ImportState]


def check_switch_import(server_dir: Path, seams: ImportSeams) -> None:
    """Every refusal of a first switch's import that is knowable before the rename (D6).

    The live run's switch renamed 259 tables and only then heard the installer
    refuse for a client folder it was never given. The switch's import is now
    the db-import one-shot alone (`switch_import()`), which needs no client
    folder; what it does need is asked here, before the Stop and again just
    before the crash note: the entry names its import service, the import can
    be verified afterwards (a probe), and no earlier import of this server is
    standing unfinished (its record would make the new set's verdict a guess).
    """
    if not seams.service:
        raise coa_realm.RealmSwitchError(
            "This server names no database import service, so a new realm type's databases "
            "cannot be set up. Nothing was switched."
        )
    if seams.probe is None:
        raise coa_realm.RealmSwitchError(
            "This server's databases cannot be asked what state they are in, so an import for "
            "a new realm type could not be checked afterwards. Nothing was switched."
        )
    if docker.one_shot_ended_marker(server_dir, seams.service).is_file():
        raise coa_realm.RealmSwitchError(
            "An earlier database import of this server did not finish. Press Repair the "
            "database on the Server tab first, then switch realm type. Nothing was switched."
        )


def switch_import(
    entry: CatalogEntry,
    server_dir: Path,
    context: coa_realm.SwitchImport,
    seams: ImportSeams,
) -> Iterator[str]:
    """A first switch's import: the db-import one-shot alone into the fresh plain schemas (D8).

    Not the installer: the live run's `engine.run()` preflighted a client folder
    (D6), refused on the switch's own crash note (D7), refused again at `up`,
    and its marking stage labelled the fresh set with the default type, after
    which the switch could not be undone (D8). This runs only what the switch
    needs: the crash-note check under the switch's own trusted context (an
    Install or Repair keeps refusing), the pre-checks again, any leftover
    importer ended, the one-shot, and the probe's verdict. It writes no realm
    type marker and starts no server: the switch marks the set last, after the
    accounts are copied, and the tab's Start runs after the switch.
    """
    from yulon.catalog import native

    refused = coa_realm.import_refusal(entry, server_dir, switch=context)
    if refused is not None:
        raise coa_realm.RealmSwitchError(refused)
    check_switch_import(server_dir, seams)
    service, probe = seams.service, seams.probe
    assert service and probe is not None  # check_switch_import refused otherwise
    left = seams.end_one_shot(service, server_dir)
    if left is not None:
        raise coa_realm.RealmSwitchError(native._one_shot_left_sentence(left, earlier=True))
    check_switch_import(server_dir, seams)
    yield f"Importing the {context.target} databases ({service}). This takes several minutes."
    run = yield from native.pump_lines(
        lambda sink: seams.one_shot(service, server_dir, sink), cancel=None, stage="import"
    )
    if run.returncode != 0:
        failed = docker.import_failed(service, server_dir, run)
        raise coa_realm.RealmSwitchError(str(failed)) from failed
    try:
        after = seams.verify(probe, service, server_dir, run)
    except docker.DockerCommandError as exc:
        raise coa_realm.RealmSwitchError(str(exc)) from exc
    yield f"The {context.target} databases now read as {after.state}."
