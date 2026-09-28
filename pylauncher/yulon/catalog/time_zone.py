"""The server's time zone: one `TZ` line per container in the compose override (T171).

Asked for on #yulon many times: "since the number of bots can be edited in
Yu'lon now, how about the server time zone?" Until now the only way was a hand
edit of `docker-compose.override.yml` -- the edit that once broke a player's
server so it let nobody log in (`reset_defaults`, owner decision 4). The zone
decides the in-game clock, the daily-quest reset hour, the calendar events and
the log timestamps. The realm list's `timezone` column is a realm category,
not a clock, and nothing here touches it.

**Where it lives.** A `TZ` line in the environment of the world and the login
server, in the override -- the settings surface of the three compose files,
auto-loaded, and read when a container is CREATED, so a change owes a recreate
(`reset_defaults.apply_rule`). The file on disk is the only source: nothing is
probed and nothing is remembered elsewhere (T117's rule for the bot count), so
what a rewrite keeps is what the containers were going to read.

**What the line says depends on the image, measured 2026-09-28.**

* WotLK (AzerothCore): the image's skeleton stage installs `tzdata` and links
  `/etc/localtime` to `Etc/UTC` (`apps/docker/Dockerfile:34-37` at the pinned
  revision; `TZ` there is a build ARG, not an ENV). glibc reads `TZ` before
  `/etc/localtime`, so the IANA name itself works: `TZ: "Europe/Oslo"`, the
  line a player would write by hand.
* TBC, Vanilla, Tortoise (CMaNGOS, images Yu'lon builds): the runtime stage is
  `ubuntu:22.04` / `ubuntu:24.04` with `ENV TZ=UTC` and no `tzdata` -- the
  base rootfs has no `/usr/share/zoneinfo`, and the apt closure of the runtime
  package list (48 and 82 packages) pulls none in. There an IANA name is
  silently UTC: glibc finds no file and cannot parse the name as a rule. So the
  line carries the POSIX rule glibc reads WITHOUT any zone file -- the footer
  of the zone's own TZif file, `CET-1CEST,M3.5.0,M10.5.0/3` for Oslo -- with
  the name beside it as a comment, so the file still says which zone it is:
  `TZ: "CET-1CEST,M3.5.0,M10.5.0/3"  # Europe/Oslo`. No rebuild. For all 598
  zones of tzdata 2026d, every three hours for five years from 2026-09-28, the
  rule gave the same local time and offset as the zone file under the images'
  own glibc (2.35, 2.39); `test_time_zone.py` keeps a year of that.
  The one thing a rule cannot hold is a zone's history, or a change the
  database schedules for a later date; neither mattered for any zone that day.

**The names come from the `tzdata` package the app ships**, not from the
computer: Windows has no zone database for Python to read, and one list on
every host is one list to test. "This computer's zone" is Qt's answer
(`QTimeZone.systemTimeZoneId()`: the IANA id on Linux, macOS and Windows, which
Qt maps from the Windows zone). A server inside a WSL distro is run from
Windows, so its "this computer" is Windows' zone, which is also the zone the
distro's own clock follows.

Nothing here imports Qt at module level or reads a server's files -- only the
app's own `tzdata`; `composegen` imports this, so this imports nothing that
imports `composegen`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources

from yulon.catalog import compose_env
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

UTC = "UTC"
"""What a server runs on when its override names no zone: both images' own default."""

ENV = "TZ"

UNSAFE = frozenset("$\"\\;#{}\r\n\t'")
"""What a carried value may not hold: `composegen._UNSAFE_SCALAR_CHARS`, the set it refuses.

A copy, because `composegen` imports this module; `test_time_zone.py` holds the two equal.
"""


class TimeZoneError(RuntimeError):
    """The override's shape is one this module does not rewrite; nothing was changed."""


@dataclass(frozen=True)
class Line:
    """What one container's `TZ` says: the value, and the zone it was made from."""

    value: str
    zone: str | None = None
    """The IANA name written beside a CMaNGOS rule, when it names a zone; `None` otherwise."""


@cache
def zones() -> frozenset[str]:
    """Every zone name the shipped database has, links included. Empty when it cannot be read."""
    try:
        text = (resources.files("tzdata") / "zones").read_text(encoding="utf-8")
    except (ModuleNotFoundError, OSError, UnicodeDecodeError) as exc:
        logger.warning(f"the time zone database could not be read: {exc}")
        return frozenset()
    return frozenset(line.strip() for line in text.splitlines() if line.strip())


@cache
def picker_zones() -> tuple[str, ...]:
    """The zones a player picks from: `zone.tab`, one place per country and region, sorted.

    Not every name: the database also carries old spellings (`US/Eastern`) and
    the `Etc/GMT+5` offsets, whose sign is the reverse of what they look like.
    """
    try:
        text = (resources.files("tzdata") / "zoneinfo" / "zone.tab").read_text(encoding="utf-8")
    except (ModuleNotFoundError, OSError, UnicodeDecodeError) as exc:
        logger.warning(f"the time zone list could not be read: {exc}")
        return ()
    names = {
        fields[2]
        for line in text.splitlines()
        if not line.startswith("#") and len(fields := line.split("\t")) >= 3
    }
    return tuple(sorted(names & zones()))


def posix(zone: str) -> str | None:
    """The POSIX rule glibc reads for `zone` without its file: the TZif footer. `None` if none.

    RFC 8536 section 3.3: a version 2+ file ends with the rule for every time
    after its last transition, between two newlines. Only a name the database
    lists is opened, so no text from a file becomes a path.
    """
    if zone not in zones():
        return None
    node = resources.files("tzdata") / "zoneinfo"
    for part in zone.split("/"):
        node = node / part
    try:
        data = node.read_bytes()
    except OSError:
        return None
    if data[:4] != b"TZif" or data[4:5] not in (b"2", b"3", b"4") or not data.endswith(b"\n"):
        return None
    start = data.rfind(b"\n", 0, len(data) - 1)
    try:
        rule = data[start + 1 : -1].decode("ascii")
    except UnicodeDecodeError:
        return None
    return rule if rule and not set(rule) & UNSAFE else None


def host_zone() -> str:
    """This computer's zone as an IANA name the database has; `UTC` when Qt cannot name one."""
    try:
        from PySide6.QtCore import QTimeZone

        name = bytes(QTimeZone.systemTimeZoneId().data()).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - any failure to ask is "cannot name one"
        logger.info(f"this computer's time zone could not be read: {exc}")
        return UTC
    return name if name in zones() else UTC


def _family(entry: CatalogEntry) -> str | None:
    native = entry.install.native
    return None if native is None else native.family


def services(entry: CatalogEntry) -> tuple[str, ...]:
    """The login server's and the world's services, named as their containers."""
    if _family(entry) not in ("azerothcore", "cmangos"):
        return ()
    spec = entry.container_spec()
    return (spec.auth, spec.world)


def line_for(entry: CatalogEntry, zone: str) -> Line | None:
    """What this game's containers get for `zone`, or `None` for a name that is not a zone."""
    if zone not in zones():
        return None
    family = _family(entry)
    if family == "azerothcore":
        return Line(zone)
    if family == "cmangos":
        rule = posix(zone)
        return None if rule is None else Line(rule, zone)
    return None


def _parse(raw: str) -> Line | None:
    """One `TZ: value  # zone` line as something a rewrite could write again, or `None`."""
    match = compose_env.ENV_LINE.match(raw.rstrip("\r"))
    if match is None:
        return None
    value, tail = match.group("value"), match.group("tail").strip()
    if value[:1] in ('"', "'"):
        # A closed quote, or the bare-value arm of `ENV_LINE` took an unclosed one.
        if len(value) < 2 or value[-1] != value[0]:
            return None
        value = value[1:-1]
    if not value or set(value) & UNSAFE:
        return None
    if tail and not tail.startswith("#"):
        return None
    named = tail[1:].strip() if tail else ""
    return Line(value, named if named in zones() and named != value else None)


def found(text: str, entry: CatalogEntry) -> dict[str, list[Line | None]]:
    """Each service's `TZ` lines in its `environment:` mapping, parsed; `None` for a bad one."""
    lines = text.split("\n")
    return {
        service: [
            _parse(lines[index]) for index in compose_env.env_lines(lines, service).get(ENV, [])
        ]
        for service in services(entry)
    }


def carried(text: str, entry: CatalogEntry) -> dict[str, Line]:
    """The line each service keeps through a rewrite: its one usable `TZ` line, if it has one."""
    return {
        service: each[0]
        for service, each in found(text, entry).items()
        if len(each) == 1 and each[0] is not None
    }


def _render_line(indent: str, line: Line, ending: str) -> str:
    comment = f"  # {line.zone}" if line.zone is not None and line.zone != line.value else ""
    return f'{indent}{ENV}: "{line.value}"{comment}{ending}'


def _set_value(raw: str, line: Line, ending: str) -> str:
    """The same line with the new value: its indent and its quote kept, its old comment not."""
    match = compose_env.ENV_LINE.match(raw.rstrip("\r"))
    if match is None:  # `env_lines` only hands over lines that match
        raise TimeZoneError(f"not a `KEY: value` line: {raw!r}")
    old = match.group("value")
    quote = old[0] if old[:1] in ('"', "'") else '"'
    comment = f"  # {line.zone}" if line.zone is not None and line.zone != line.value else ""
    return f"{match.group('head')}{quote}{line.value}{quote}{comment}{ending}"


def _last_inside(lines: list[str], start: int, depth: int) -> int:
    """The index of the last line that says something and sits deeper than `depth`, from `start`."""
    last = start
    for index in range(start + 1, len(lines)):
        line = lines[index].rstrip("\r")
        if compose_env.says_nothing(line):
            continue
        if compose_env.indent(line) <= depth:
            break
        last = index
    return last


def _child_at(lines: list[str], start: int, depth: int, key: str) -> int | None:
    """The line of `key:` among the direct children of the block at `lines[start]`."""
    child = _child_indent(lines, start, depth)
    for index in range(start + 1, len(lines)):
        line = lines[index].rstrip("\r")
        if compose_env.says_nothing(line):
            continue
        here = compose_env.indent(line)
        if here <= depth:
            return None
        if here == child and line.strip().split(":", 1)[0] == key:
            return index
    return None


def _child_indent(lines: list[str], start: int, depth: int) -> int | None:
    """How deep the first child of the block at `lines[start]` sits, or `None` without one."""
    for index in range(start + 1, len(lines)):
        line = lines[index].rstrip("\r")
        if compose_env.says_nothing(line):
            continue
        here = compose_env.indent(line)
        return here if here > depth else None
    return None


def _head(raw: str) -> str:
    """A line without its comment and its CR, stripped: what the key and value say."""
    return raw.rstrip("\r").split("#", 1)[0].strip()


def _set_one(lines: list[str], service: str, line: Line, ending: str) -> None:
    """Give `service` exactly this `TZ` line, in place; raise before changing a shape it cannot.

    New lines take the file's own indent step (the first child's depth), so a
    four-space file gets four-space blocks: YAML refuses a mapping whose keys
    do not line up.
    """
    top = next(
        (
            index
            for index, raw in enumerate(lines)
            if compose_env.indent(raw) == 0 and _head(raw) in ("services:", "services: {}")
        ),
        None,
    )
    if top is None:
        raise TimeZoneError("the file has no `services:` mapping to give a time zone to")
    if _head(lines[top]) == "services: {}":
        lines[top] = f"services:{ending}"
    step = _child_indent(lines, top, 0) or 2
    at = _child_at(lines, top, 0, service)
    if at is None:
        end = _last_inside(lines, top, 0)
        lines[end + 1 : end + 1] = [
            f"{' ' * step}{service}:{ending}",
            f"{' ' * step * 2}environment:{ending}",
            _render_line(" " * step * 3, line, ending),
        ]
        return
    depth = compose_env.indent(lines[at])
    inner = _child_indent(lines, at, depth) or depth + step
    env = _child_at(lines, at, depth, "environment")
    if env is None:
        end = _last_inside(lines, at, depth)
        lines[end + 1 : end + 1] = [
            f"{' ' * inner}environment:{ending}",
            _render_line(" " * (inner + inner - depth), line, ending),
        ]
        return
    if _head(lines[env]) != "environment:":
        raise TimeZoneError(
            f"{service}'s environment is written on one line, which Yu'lon does not rewrite"
        )
    env_depth = compose_env.indent(lines[env])
    end = _last_inside(lines, env, env_depth)
    entries = [
        raw for raw in lines[env + 1 : end + 1] if not compose_env.says_nothing(raw.rstrip("\r"))
    ]
    if any(raw.strip().startswith("-") for raw in entries):
        raise TimeZoneError(
            f"{service}'s environment is a `- KEY=value` list, which Yu'lon does not rewrite"
        )
    spots = compose_env.env_lines(lines, service).get(ENV, [])
    if len(spots) > 1:
        raise TimeZoneError(f"{ENV} is in {service}'s environment more than once")
    if spots:
        lines[spots[0]] = _set_value(lines[spots[0]], line, ending)
        return
    indent = compose_env.indent(entries[0]) if entries else env_depth + inner - depth
    lines[end + 1 : end + 1] = [_render_line(" " * indent, line, ending)]


def lay_over(text: str, entry: CatalogEntry, lines: Mapping[str, Line]) -> str:
    """`text` with each named service's `TZ` set, every other byte kept.

    A service without the line gets it after its environment's last key; one
    without an environment gets one after its last line; one the file does not
    have gets a block at the end of `services:`. The login server first, so a
    block made for it sits before the world's, as in the base file. The same
    function lays a render's zone and the Tuning tab's, so a file the tab
    wrote is the file the next render makes of it.

    Raises:
        TimeZoneError: a service's environment is a list, is written on one
            line, or has `TZ` twice -- a shape a scanner cannot set one value in.
    """
    if not lines:
        return text
    ending = "\r" if text.split("\n", 1)[0].endswith("\r") else ""
    out = text.split("\n")
    for service in services(entry):
        if service in lines:
            _set_one(out, service, lines[service], ending)
    return "\n".join(out)


def for_render(
    entry: CatalogEntry, override: str | None, *, installed: bool, new_zone: str | None
) -> dict[str, Line]:
    """The lines a render lays over the override it makes.

    The ones the file it replaces holds, first and whatever else is asked: a
    Repair, Update to latest, the channel's presses and Reset to default all
    keep the zone (owner, 2026-09-28: the reset does not reset it). Only a NEW
    install -- no Yu'lon base file in the folder yet -- is given `new_zone`,
    the computer's own (owner: the default for a new server); an installed
    server with no line keeps running on UTC until the player picks a zone.
    UTC is both images' own clock, so it is written as no line at all.
    """
    kept = carried(override, entry) if override is not None else {}
    if kept or installed or new_zone is None or new_zone == UTC:
        return kept
    line = line_for(entry, new_zone)
    return {} if line is None else dict.fromkeys(services(entry), line)
