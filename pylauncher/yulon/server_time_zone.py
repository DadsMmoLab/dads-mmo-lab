"""The Tuning tab's "Server time zone": read and set where the install keeps it (T171).

The zone is the `TZ` line of the world's and the login server's environment in
`docker-compose.override.yml` -- what it says, and why it differs between the
AzerothCore and the CMaNGOS images, is `catalog/time_zone.py`. This module is
the tab's two presses over that file, in `bot_population`'s shape (T99): read
what the file says, and set one zone with a backup first, changing only the
`TZ` lines -- and on a CMaNGOS game the bind of the server's own zone files,
whose copy it makes first (`time_zone.place`) -- every other byte kept.

Chosen from a list, never typed (owner decision 2026-09-28): a hand-typed zone
is how a player once broke the file. A value the list does not hold -- one a
player wrote by hand -- is read and shown as it is, and kept by every rewrite
until the player picks a zone here.

Nothing here imports Qt, and every function but `question` touches the disk:
the view runs them on its job runner.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from yulon import reset_defaults, tuning
from yulon.catalog import compose_env, composegen, time_zone
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

FILE = composegen.OVERRIDE_FILE
UTC = time_zone.UTC

MISSING = (
    "{file} is not on disk, so there is no time zone to read or change. Repair the server to "
    "write it again."
)
FOREIGN = (
    "this server's compose files were not made by Yu'lon, so Yu'lon does not rewrite its "
    "{file}. Set TZ in that file yourself."
)
UNREADABLE = "{file} could not be read ({exc})"
NOT_UTF8 = "{file} is not UTF-8 text, so Yu'lon will not read or rewrite it ({exc})"
TWICE = (
    "TZ is in the {service} environment of {file} more than once, so Yu'lon does not know which "
    "line the server reads. Remove the extra line and press Reload."
)
NOT_A_ZONE = "{zone!r} is not a time zone Yu'lon knows"
WRITE_FAILED = "{file} could not be written ({exc}); it was left as it was"
NO_ZONE_FILE = (
    "this server's image has no time zone files, and its folder has no {folder}/{value} bound "
    "for it, so the server reads {value} as UTC. Apply {value} here, or Repair, to copy it in."
)


class TimeZoneSettingError(RuntimeError):
    """A refusal a player reads, raised before anything is written."""


@dataclass(frozen=True)
class Reading:
    """What one install's time zone is now, as its override says."""

    file: str
    value: str | None = None
    """The world's `TZ` value; `None` when it has no line, which is UTC."""
    zone: str | None = UTC
    """The zone that value IS, when Yu'lon can name it; `None` for a hand-written one it cannot."""
    login: str | None = None
    """The login server's `TZ` value, when it has one."""
    problem: str | None = None
    """Why the zone cannot be read or changed here; `None` when it can."""
    note: str | None = None
    """What the player should know about the value, when there is something."""

    @property
    def shown(self) -> str:
        """The zone in the words the tab uses: its name, or the value as it is written."""
        if self.zone is not None:
            return self.zone
        return self.value or UTC


@dataclass(frozen=True)
class Written:
    """What one press did: the file, its backup, and the job that makes it count."""

    file: str
    rule: tuning.ApplyRule
    backup: Path | None
    """`None` when the file already said this zone and nothing was written."""
    before: str
    after: str

    @property
    def changed(self) -> bool:
        return self.backup is not None


def _read_text(path: Path) -> str:
    """Exact text (`newline=""`), strictly decoded: a byte it cannot read it must not write."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _foreign(server_dir: Path) -> bool:
    base = server_dir / composegen.BASE_FILE
    return not (base.is_file() and composegen.is_ours(base))


def _raw(text: str, entry: CatalogEntry, service: str) -> list[str]:
    """Each `TZ` value of `service` as the file spells it, quotes off: what the tab shows."""
    lines = text.split("\n")
    at = compose_env.env_lines(lines, service).get(time_zone.ENV, [])
    return [compose_env.env_value(lines[index]) for index in at]


def _zone_of(line: time_zone.Line | None) -> str | None:
    """The zone a line names, when it names one the database lists."""
    if line is None:
        return None
    if line.value in time_zone.LIKE_UTC:
        return UTC
    return line.value if line.value in time_zone.zones() else None


def _label(server_dir: Path, text: str) -> str:
    """The install's own SELinux answer, read off its files: the override's binds, else the base's.

    The install's decision read back, never the host asked again (T102, T106):
    a host briefly permissive would strip the `:z` a zone bind needs.
    """
    for candidate in (text, _text_or_none(server_dir / composegen.BASE_FILE)):
        if candidate is None:
            continue
        try:
            label = composegen.bind_label_of(candidate)
        except composegen.MixedBindLabels:
            continue
        if label is not None:
            return label
    return ""


def _text_or_none(path: Path) -> str | None:
    try:
        return _read_text(path)
    except (OSError, UnicodeDecodeError):
        return None


def read(entry: CatalogEntry, server_dir: Path) -> Reading:
    """This install's time zone as its override says it now. Never raises for a file."""
    path = server_dir / FILE
    if _foreign(server_dir):
        return Reading(FILE, problem=FOREIGN.format(file=path.name))
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return Reading(FILE, problem=MISSING.format(file=path.name))
    except UnicodeDecodeError as exc:
        return Reading(FILE, problem=NOT_UTF8.format(file=path.name, exc=exc))
    except OSError as exc:
        return Reading(FILE, problem=UNREADABLE.format(file=path.name, exc=exc))
    auth, world = time_zone.services(entry)
    found = time_zone.found(text, entry)
    for service in (world, auth):
        if len(found[service]) > 1:
            return Reading(FILE, problem=TWICE.format(service=service, file=path.name))
    login = next(iter(_raw(text, entry, auth)), None)
    problem: str | None = None
    try:
        time_zone.lay_over(text, entry, dict.fromkeys((auth, world), time_zone.Line(UTC)))
    except time_zone.TimeZoneError as exc:
        problem = f"{exc}; set TZ in {path.name} yourself"
    if not found[world]:
        return Reading(FILE, None, UTC, login, problem)
    value = _raw(text, entry, world)[0]
    zone = _zone_of(found[world][0])
    note: str | None = None
    if zone is not None and zone != UTC and time_zone.needs_files(entry):
        # A CMaNGOS image has no zone files of its own (`time_zone`): without
        # the copy in the server folder, or the bind that shows it, the name
        # is UTC there -- not what the player who set it meant.
        bound = time_zone.bind_line("") in text or time_zone.bind_line(":z") in text
        if not (bound and time_zone.ready(entry, server_dir, zone)):
            note = NO_ZONE_FILE.format(value=value, folder=time_zone.FOLDER)
    return Reading(FILE, value, zone, login, problem, note)


def write(entry: CatalogEntry, server_dir: Path, zone: str) -> Written:
    """Set both servers' `TZ` to `zone`, backing the file up first; nothing written if it says so.

    Read fresh here, not handed in: the file's shape is the disk's answer at the
    moment of writing. Only the `TZ` lines change (`time_zone.lay_over`), and
    on CMaNGOS the zone folder's bind, labelled as the install's other binds
    are; the zone's file is copied in before the override names it.

    Raises:
        TimeZoneSettingError: a refusal a player reads; nothing was written.
    """
    if not time_zone.zones():
        raise TimeZoneSettingError(time_zone.MISSING_DATA)
    line = time_zone.line_for(entry, zone)
    if line is None:
        raise TimeZoneSettingError(NOT_A_ZONE.format(zone=zone))
    reading = read(entry, server_dir)
    if reading.problem is not None:
        raise TimeZoneSettingError(reading.problem)
    path = server_dir / FILE
    rule = reset_defaults.apply_rule(FILE)
    try:
        text = _read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise TimeZoneSettingError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    services = time_zone.services(entry)
    unset = not any(time_zone.found(text, entry).values())
    if zone == UTC and unset:
        return Written(FILE, rule, None, reading.shown, zone)
    try:
        after = time_zone.lay_over(
            text, entry, dict.fromkeys(services, line), label=_label(server_dir, text)
        )
        # The zone's file first, so no bind ever names a folder without it; and
        # again when the file already says this zone, which refreshes the copy.
        time_zone.place(entry, server_dir, after)
    except (OSError, time_zone.TimeZoneError) as exc:
        raise TimeZoneSettingError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    if after == text:
        return Written(FILE, rule, None, reading.shown, zone)
    try:
        made = tuning.backup(path, root=server_dir)
    except (OSError, tuning.TuningError) as exc:
        raise TimeZoneSettingError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    try:
        conf.replace_file(path, after)
    except InstallerError as exc:
        # The file is as it was (`replace_file` is atomic), so its backup is a
        # copy of what is still there: removed, so it is not offered as a change.
        made.unlink(missing_ok=True)
        raise TimeZoneSettingError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    logger.info(f"set {entry.id}'s time zone to {zone} in {path}; backup {made.name}")
    return Written(FILE, rule, made, reading.shown, zone)


def question(entry: CatalogEntry, reading: Reading, zone: str) -> str:
    """The Yes/No the Tuning tab asks before `write()`, in the words a player reads."""
    name = Path(reading.file).name
    auth, world = time_zone.services(entry) or ("", "")
    parts = [f"Set this server's time zone to {zone}?"]
    said = (
        f"TZ for the world ({world}) and the login server ({auth}); only those lines change. A "
        "backup of it is made beside it first."
    )
    if time_zone.needs_files(entry):
        parts.append(
            f"{name} gets {said} This server's image has no time zone files, so Yu'lon copies "
            f"this zone's file into the server folder ({time_zone.FOLDER}/{zone}) and adds the "
            "line that shows that folder to both, read-only."
        )
    else:
        parts.append(f"{name} gets {said}")
    parts.append(
        "The server's clock sets when daily quests reset, when calendar events start and the "
        "times in its logs. Reset to default keeps the time zone."
    )
    parts.append(WHEN_IT_COUNTS)
    return "\n\n".join(parts)


WHEN_IT_COUNTS = (
    "The running server keeps its current time zone until its containers are RECREATED; Yu'lon "
    "offers the recreate when this is done."
)


@dataclass(frozen=True)
class TimeZoneRoute:
    """The Tuning tab's two presses, bound to one install. Both touch the disk: run off-thread."""

    entry: CatalogEntry
    server_dir: Path
    hold_server: Callable[[str], AbstractContextManager[object]] | None = None
    """The server's cross-process hold (T622): the write is made inside it, and another Yu'lon
    working on the server refuses it with `docker.ServerHeldError`, nothing written. The read
    takes none. `None`, as in a harness, holds nothing."""

    def read(self) -> Reading:
        return read(self.entry, self.server_dir)

    def write(self, zone: str) -> Written:
        if self.hold_server is None:
            return write(self.entry, self.server_dir, zone)
        with self.hold_server(HOLD_PRESS):
            return write(self.entry, self.server_dir, zone)


HOLD_PRESS = "Set the server's time zone"


def time_zone_route(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    hold_server: Callable[[str], AbstractContextManager[object]] | None = None,
) -> TimeZoneRoute | None:
    """This install's route, or `None` for a game whose compose files Yu'lon does not make."""
    if not time_zone.services(entry):
        return None
    return TimeZoneRoute(entry, server_dir, hold_server)
