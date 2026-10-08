"""Whether a module loaded, in one sentence for the Server tab (T555 T5, Unbound #71).

The dashboard asks this once per run, after the world says ready (`dashboard.Dashboard`).
Three things are read, and each is only ever what it was told:

* the tables and counts of the entry's `sql_checks`, through T3's `scriptdeploy.read_checks`:
  which tables are there first, then the counts of those. A table that is not there is said to
  be missing, by name; it is never counted as 0;
* this run's world log, for the lines the module prints when it loads (`ModuleHealth.log_markers`);
* the module's switches as the log says they are running, beside what the settings file says
  they will be at the next start.

A switch the log never mentioned is "not said", never "off": a log that did not say is not the
same as a switch that is off. A read that could not be made is the sentence "could not be
checked" with its reason, and no number.

Pure apart from the one `SqlReader` it is handed. Nothing here touches a widget, and nothing
goes to the world thread.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from yulon import dbreads, docker, server_build_presses
from yulon.catalog.catalog import ModuleHealth, SqlCheck
from yulon.catalog.families import scriptdeploy
from yulon.manifest import Db


@dataclass(frozen=True)
class Switch:
    """One of a module's on/off switches: how it is running, and how the file will start it."""

    label: str
    running: bool | None
    """What this run's log said it started as; `None` when the log never said."""
    conf: bool | None
    """What the settings file says now, for the next start; `None` if it cannot be read."""


@dataclass(frozen=True)
class HealthReading:
    """Everything the sentence is made of, before any word is chosen."""

    name: str
    unreadable: str = ""
    missing: tuple[str, ...] = ()
    short: tuple[tuple[str, int, int], ...] = ()
    """`(table, rows found, rows needed)` for each short table other than the counted one."""
    absent_marker: str = ""
    placed: tuple[str, int, int] | None = None
    """`(label, found, wanted)` for the counted table, if the block counts one."""
    switches: tuple[Switch, ...] = ()

    @property
    def good(self) -> bool:
        """Nothing is missing, short or unprinted: the reading says the module loaded."""
        placed_short = self.placed is not None and self.placed[1] < self.placed[2]
        return not (
            self.unreadable or self.missing or self.short or self.absent_marker or placed_short
        )


def _asker(sql: dbreads.SqlReader, schemas: dict[Db, str]) -> scriptdeploy.SqlAsk:
    """`read_checks`'s `(schema, statement)` question, over the dashboard's `(db role, ...)` one.

    Every failure of the seam is a `DockerCommandError`, the one type `read_checks` turns into
    a missing table (1146) or an unreadable answer, so it cannot leave as another exception.
    """
    role_of = {schema: role for role, schema in schemas.items()}

    def ask(schema: str, statement: str) -> str:
        role = role_of.get(schema)
        if role is None:
            raise docker.DockerCommandError(f"{schema} is not one of this server's databases")
        try:
            return sql.query(role, statement)
        except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
            raise docker.DockerCommandError(str(exc)) from exc

    return ask


def reading(
    health: ModuleHealth,
    checks: Sequence[SqlCheck],
    schemas: dict[Db, str],
    sql: dbreads.SqlReader,
    log_text: str,
    switches: tuple[Switch, ...],
) -> HealthReading:
    """Read the log, then ask the database. Never raises: a failed read is `unreadable`.

    The log first, because a world whose log cannot be read cannot be judged, and the database
    need not be asked for it again every tick while it stays unreadable.
    """
    if not log_text.strip():
        return HealthReading(health.name, unreadable="the world's log could not be read")
    try:
        got = scriptdeploy.read_checks(checks, schemas, _asker(sql, schemas))
    except scriptdeploy.ChecksUnreadable as exc:
        return HealthReading(health.name, unreadable=str(exc))
    placed: tuple[str, int, int] | None = None
    short: list[tuple[str, int, int]] = []
    for check, found in got.counts:
        if health.count_table and check.table == health.count_table:
            placed = (health.count_label, found, check.at_least)
        elif found < check.at_least:
            short.append((check.table, found, check.at_least))
    if got.missing or short:
        return HealthReading(health.name, missing=got.missing, short=tuple(short))
    absent = next((m for m in health.log_markers if m not in log_text), "")
    return HealthReading(health.name, absent_marker=absent, placed=placed, switches=switches)


def sentence(got: HealthReading) -> str:
    """The Server tab's words for one reading. The first of these that applies wins."""
    name = got.name
    if got.unreadable:
        return f"{name} could not be checked: {got.unreadable}"
    if got.missing:
        # Measured live (T555 H5): Rebuild does not recreate a module table, because the
        # importer skips a file its `updates` ledger already holds, and Repair refuses a
        # finished install. Yu'lon writes nothing to that ledger, so there is no press to name.
        return (
            f"{name} tables missing: {', '.join(got.missing)}. "
            f"{server_build_presses.REBUILD} does not bring them back, and nothing short of "
            "reinstalling the server, which loses your characters, does. "
            "Press “Save logs for support…” on the Logs tab and ask for help."
        )
    if got.short:
        parts = [
            f"{table} has {found} rows, at least {wanted} expected"
            for table, found, wanted in got.short
        ]
        return f"{name} data incomplete: " + "; ".join(parts)
    if got.absent_marker:
        return (
            f'{name} did not load: the world log has no "{got.absent_marker}" line this run. '
            "Open the console log"
        )
    if got.placed is not None and got.placed[1] < got.placed[2]:
        label, found, wanted = got.placed
        return f"{name} loaded, but the {label} stands in {found} of {wanted} places"
    details: list[str] = []
    if got.placed is not None:
        label, found, _wanted = got.placed
        details.append(f"{label} in {found} places")
    if got.switches:
        details.append(", ".join(_switch_text(s) for s in got.switches))
    return f"{name} loaded" + (": " + "; ".join(details) if details else "")


def _word(on: bool) -> str:
    return "on" if on else "off"


def _switch_text(switch: Switch) -> str:
    if switch.conf is None:
        file_side = " (the settings file could not be read)"
    elif switch.running is None:
        file_side = " (the settings file says on)" if switch.conf else ""
    elif switch.conf != switch.running:
        file_side = f" ({_word(switch.conf)} at the next start)"
    else:
        file_side = ""
    if switch.running is None:
        return f"{switch.label} not said{file_side}"
    return f"{switch.label} {_word(switch.running)}{file_side}"
