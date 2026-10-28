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
from pathlib import Path

from yulon import coa_realm
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
        run_import: Callable[[], Iterator[str]],
        set_realm_row: Callable[[str, str], None],
        write_override: Callable[[Mapping[str, str]], None],
        stop: Callable[[], bool],
        start: Callable[[], None],
        bot_prefix: str,
    ) -> None:
        self._entry = entry
        self._server_dir = server_dir
        self._hold_server = hold_server
        self._sql = sql
        self._servers_running = servers_running
        self._run_import = run_import
        self._set_realm_row = set_realm_row
        self._write_override = write_override
        self._stop = stop
        self._start = start
        self._bot_prefix = bot_prefix

    def current_id(self) -> str:
        """The file's active type id (the default when it is missing, C3a)."""
        return coa_realm.active(self._entry, self._server_dir).id

    def switch_to(self, target_id: str) -> Iterator[str]:
        """Stop, move every table set to `target_id`, point the folder at it, start.

        The hold wraps the Stop too, and the engine's guards run inside it: a
        second switch while one holds is refused with the hold's sentence
        before the engine is asked anything, and the engine re-checks its
        file-plus-database guards under it. The engine still refuses on a
        running server (metadata locks), so the Stop is what lets it through.
        """
        with self._hold_server(SWITCH_PRESS):
            self._stop()
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
            )
            self._start()


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
    run_import: Callable[[], Iterator[str]],
    set_realm_row: Callable[[str, str], None],
    write_override: Callable[[Mapping[str, str]], None],
    stop: Callable[[], bool],
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
        set_realm_row=set_realm_row,
        write_override=write_override,
        stop=stop,
        start=start,
        bot_prefix=bot_prefix,
    )
