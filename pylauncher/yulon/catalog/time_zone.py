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
  package list (48 and 82 packages) pulls none in. There a bare name is
  silently UTC: measured in all three built images on a Linux test box, `TZ=Europe/Oslo`
  printed `Europe +0000`. So Yu'lon brings the zone's own file: it copies it
  from the app's `tzdata` into the server folder (`zoneinfo/Europe/Oslo`, by
  `place()`) and binds that folder read-only where glibc looks
  (`- ./zoneinfo:/usr/share/zoneinfo:ro`). The `TZ` line is then the same name
  WotLK's is. Measured the same way: CEST +0200 for Oslo, AEDT/AEST for
  Sydney, in each image, with no rebuild. The folder bind rather than one file
  under `TZ=":/path"`, because the line then names the zone -- what a player
  reads and writes by hand, and what the tab reads back -- and a hand-written
  name that the folder holds works too. Every writer that places the line
  copies the file again from the app's current `tzdata`, so a rule change a
  Yu'lon update brings reaches the server at the next Repair, Update to latest
  or Apply. (Round 1 wrote the file's POSIX footer instead; the footer only
  governs after a zone's last listed change, so a zone with changes scheduled
  ahead could not be written that way.)

**The names come from the `tzdata` package the app ships**, not from the
computer: Windows has no zone database for Python to read, and one list on
every host is one list to test. "This computer's zone" is Qt's answer
(`QTimeZone.systemTimeZoneId()`: the IANA id on Linux, macOS and Windows, which
Qt maps from the Windows zone). A server inside a WSL distro is run from
Windows, so its "this computer" is Windows' zone, which is also the zone the
distro's own clock follows.

Nothing here imports Qt at module level. Only `place()` writes, and only the
zone files under the server's `zoneinfo/`; `composegen` imports this, so this
imports nothing that imports `composegen`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path

from yulon.catalog import compose_env
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

UTC = "UTC"
"""What a server runs on when its override names no zone: both images' own default."""

ENV = "TZ"

FOLDER = "zoneinfo"
"""The server folder's copy of the zone files a CMaNGOS server reads, relative to it."""

MOUNT = "/usr/share/zoneinfo"
"""Where glibc looks a zone name up; the CMaNGOS images have nothing there of their own."""

LIKE_UTC = frozenset(
    {
        "UTC",
        "Etc/UTC",
        "UCT",
        "Etc/UCT",
        "Zulu",
        "Etc/Zulu",
        "Universal",
        "Etc/Universal",
        "GMT",
        "Etc/GMT",
        "GMT0",
        "Etc/GMT0",
        "GMT+0",
        "Etc/GMT+0",
        "GMT-0",
        "Etc/GMT-0",
        "Greenwich",
        "Etc/Greenwich",
    }
)
"""Names for a clock that is always UTC+0 with no change ever: this computer's answer as UTC.

A computer set to `Etc/UTC` is a computer in UTC, and a new server there gets
no line at all, as on any UTC computer (cold review NIT).
"""

UNSAFE = frozenset("$\"\\;#{}\r\n\t'")
"""What a carried value may not hold: `composegen._UNSAFE_SCALAR_CHARS`, the set it refuses.

A copy, because `composegen` imports this module; `test_time_zone.py` holds the two equal.
"""

MISSING_DATA = (
    "the time zone list is not in this copy of Yu'lon (its `tzdata` package could not be read), "
    "so no zone can be set here; the server keeps the zone its files name"
)


class TimeZoneError(RuntimeError):
    """The override's shape is one this module does not rewrite; nothing was changed."""


@dataclass(frozen=True)
class Line:
    """What one container's `TZ` says, and the note written after it."""

    value: str
    comment: str | None = None
    """The text after `#` on a carried line, kept as it is; `None` on a line the tab writes."""


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


def zone_file(zone: str) -> bytes | None:
    """The app's own TZif file for `zone`, or `None` for a name the database does not list.

    Only a listed name is opened, so no text from a file becomes a path.
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
    return data if data[:4] == b"TZif" else None


def host_zone() -> str:
    """This computer's zone as an IANA name the database has; `UTC` when Qt cannot name one."""
    try:
        from PySide6.QtCore import QTimeZone

        name = bytes(QTimeZone.systemTimeZoneId().data()).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - any failure to ask is "cannot name one"
        logger.info(f"this computer's time zone could not be read: {exc}")
        return UTC
    if name in LIKE_UTC:
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
    if zone not in zones() or not services(entry):
        return None
    return Line(zone)


def needs_files(entry: CatalogEntry) -> bool:
    """Whether this game's image has no zone files, so the server folder must bring them."""
    return _family(entry) == "cmangos"


def bind_line(label: str) -> str:
    """The volume entry that shows the server's zone files to glibc, read-only.

    `label` is the install's own SELinux answer (`":z"` or `""`), as on every
    other bind Yu'lon writes; with `ro` the two options are one list, `ro,z`.
    """
    return f"./{FOLDER}:{MOUNT}:ro" + (",z" if label == ":z" else "")


def _parse(raw: str) -> Line | None:
    """One `TZ: value  # note` line as something a rewrite could write again, or `None`."""
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
    comment = tail[1:].strip() if tail else ""
    return Line(value, comment or None)


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


def _comment(line: Line) -> str:
    return f"  # {line.comment}" if line.comment else ""


def _render_line(indent: str, line: Line, ending: str) -> str:
    return f'{indent}{ENV}: "{line.value}"{_comment(line)}{ending}'


def _set_value(raw: str, line: Line, ending: str) -> str:
    """The same line with the new value: its indent and its quote kept, its old note not."""
    match = compose_env.ENV_LINE.match(raw.rstrip("\r"))
    if match is None:  # `env_lines` only hands over lines that match
        raise TimeZoneError(f"not a `KEY: value` line: {raw!r}")
    old = match.group("value")
    quote = old[0] if old[:1] in ('"', "'") else '"'
    return f"{match.group('head')}{quote}{line.value}{quote}{_comment(line)}{ending}"


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


def _services_at(lines: list[str], ending: str) -> int:
    """The top-level `services:` line; `services: {}` opened into a mapping first."""
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
    return top


def _service_at(lines: list[str], service: str, ending: str) -> tuple[int, int, int]:
    """`(line, depth, child depth)` of `service`, a block made for it at the end if it has none.

    New lines take the file's own indent step (the first child's depth), so a
    four-space file gets four-space blocks: YAML refuses a mapping whose keys
    do not line up.
    """
    top = _services_at(lines, ending)
    step = _child_indent(lines, top, 0) or 2
    at = _child_at(lines, top, 0, service)
    if at is None:
        end = _last_inside(lines, top, 0)
        lines[end + 1 : end + 1] = [f"{' ' * step}{service}:{ending}"]
        at = end + 1
    depth = compose_env.indent(lines[at])
    return at, depth, _child_indent(lines, at, depth) or depth + step


def _set_env(lines: list[str], service: str, line: Line, ending: str) -> None:
    """Give `service` exactly this `TZ` line, in place; raise before changing a shape it cannot."""
    at, depth, inner = _service_at(lines, service, ending)
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


def _target(item: str) -> str | None:
    """The container path of one `- host:container[:options]` volume entry."""
    bare = item.strip()[1:].strip().strip("\"'")
    parts = bare.split(":")
    return parts[1] if len(parts) >= 2 else None


def _set_bind(lines: list[str], service: str, bind: str, ending: str) -> None:
    """Give `service` exactly one volume entry for `MOUNT`: this one, in place of any other."""
    at, depth, inner = _service_at(lines, service, ending)
    volumes = _child_at(lines, at, depth, "volumes")
    item = f"- {bind}"
    if volumes is None:
        end = _last_inside(lines, at, depth)
        lines[end + 1 : end + 1] = [
            f"{' ' * inner}volumes:{ending}",
            f"{' ' * (inner + inner - depth)}{item}{ending}",
        ]
        return
    if _head(lines[volumes]) != "volumes:":
        raise TimeZoneError(
            f"{service}'s volumes are written on one line, which Yu'lon does not rewrite"
        )
    vol_depth = compose_env.indent(lines[volumes])
    end = _last_inside(lines, volumes, vol_depth)
    spots = [
        index
        for index in range(volumes + 1, end + 1)
        if not compose_env.says_nothing(lines[index].rstrip("\r"))
        and lines[index].strip().startswith("-")
        and _target(lines[index]) == MOUNT
    ]
    if len(spots) > 1:
        raise TimeZoneError(f"{service} binds {MOUNT} more than once")
    if spots:
        lines[spots[0]] = f"{' ' * compose_env.indent(lines[spots[0]])}{item}{ending}"
        return
    first = next(
        (
            lines[index]
            for index in range(volumes + 1, end + 1)
            if not compose_env.says_nothing(lines[index].rstrip("\r"))
        ),
        None,
    )
    indent = compose_env.indent(first) if first is not None else vol_depth + inner - depth
    lines[end + 1 : end + 1] = [f"{' ' * indent}{item}{ending}"]


def lay_over(text: str, entry: CatalogEntry, lines: Mapping[str, Line], *, label: str = "") -> str:
    """`text` with each named service's `TZ` set, every other byte kept.

    A service without the line gets it after its environment's last key; one
    without an environment gets one after its last line; one the file does not
    have gets a block at the end of `services:`. The login server first, so a
    block made for it sits before the world's, as in the base file. On a
    CMaNGOS game a line that names a zone also gets the zone files' bind
    (`bind_line(label)`), in place of any bind of that folder already there. The
    same function lays a render's zone and the Tuning tab's, so a file the tab
    wrote is the file the next render makes of it.

    Raises:
        TimeZoneError: a service's environment or volumes are a shape a
            scanner cannot set one entry in (a list environment, a one-line
            mapping, `TZ` or the zone folder twice).
    """
    if not lines:
        return text
    ending = "\r" if text.split("\n", 1)[0].endswith("\r") else ""
    out = text.split("\n")
    for service in services(entry):
        if service not in lines:
            continue
        _set_env(out, service, lines[service], ending)
        if needs_files(entry) and lines[service].value in zones():
            _set_bind(out, service, bind_line(label), ending)
    return "\n".join(out)


def place(entry: CatalogEntry, server_dir: Path, override: str) -> tuple[Path, ...]:
    """Copy each zone the override names into the server's `zoneinfo/`, from the app's `tzdata`.

    CMaNGOS only (`needs_files`). Copied again whenever it differs, so a rule
    a Yu'lon update brings reaches the server at the next writer's press. A
    file already equal is left alone (its mtime does not move). Each file is
    written beside itself and renamed into place, so an interrupted copy
    leaves the old one whole. Returns the files it wrote.

    Raises:
        OSError: the folder or a file could not be written; nothing after it was.
        TimeZoneError: `zoneinfo` in the server folder is a link or a file,
            which is not the folder Yu'lon made; nothing is written through it.
    """
    if not needs_files(entry):
        return ()
    names = sorted(
        {line.value for line in carried(override, entry).values() if line.value in zones()}
    )
    if not names:
        return ()
    root = server_dir / FOLDER
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise TimeZoneError(
            f"{root} is not a folder Yu'lon made, so no zone file was written into it"
        )
    written: list[Path] = []
    for name in names:
        data = zone_file(name)
        if data is None:
            continue
        target = root.joinpath(*name.split("/"))
        try:
            if target.read_bytes() == data:
                continue
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.yulon-tmp")
        partial.write_bytes(data)
        os.replace(partial, target)
        written.append(target)
    return tuple(written)


def ready(entry: CatalogEntry, server_dir: Path, zone: str) -> bool:
    """Whether the server folder holds the app's own file for `zone` (always, off CMaNGOS)."""
    if not needs_files(entry):
        return True
    data = zone_file(zone)
    try:
        return (
            data is not None
            and (server_dir / FOLDER).joinpath(*zone.split("/")).read_bytes() == data
        )
    except OSError:
        return False


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
    if kept or installed or new_zone is None or new_zone in LIKE_UTC:
        return kept
    line = line_for(entry, new_zone)
    return {} if line is None else dict.fromkeys(services(entry), line)
