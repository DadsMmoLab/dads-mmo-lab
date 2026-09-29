"""The line scanner that finds a compose service's `environment:` keys (T117, T171).

Moved out of `bot_count` for T171, unchanged: the time zone reads and writes
the same `KEY: "value"` lines, and `composegen.render()` lays the zone over
every override it makes, so the scanner has to be importable from below
`composegen` -- `bot_count` imports `composegen`, which would make the cycle.
`bot_count` still answers to the old names.

A line scanner and not a YAML round trip, on purpose: a YAML writer drops
comments and restyles what it emits (the override template's own header says
so), and the Bots box and the time zone must change their own values and
nothing else. Nothing here reads the disk or imports Qt.
"""

from __future__ import annotations

import re

ENV_LINE = re.compile(
    r"^(?P<head>[ \t]*(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s#]*)(?P<tail>.*)$"
)


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def says_nothing(line: str) -> bool:
    bare = line.strip()
    return bare == "" or bare.startswith("#")


def env_lines(lines: list[str], service: str) -> dict[str, list[int]]:
    """Where each key of `service`'s `environment:` mapping is, by line index. Comments skipped.

    The shape it reads is the one the install writes -- `  <service>:` then
    `    environment:` then `      KEY: "value"` lines; a block it cannot find
    answers `{}`.
    """
    found: dict[str, list[int]] = {}
    service_at: int | None = None
    env_at: int | None = None
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r")
        if says_nothing(line):
            continue
        depth = indent(line)
        if env_at is not None and depth <= env_at:
            env_at = None
        if service_at is not None and depth <= service_at:
            service_at = None
        if service_at is None:
            if line.strip() == f"{service}:":
                service_at = depth
            continue
        if env_at is None:
            if line.strip() == "environment:":
                env_at = depth
            continue
        match = ENV_LINE.match(line)
        if match is not None:
            found.setdefault(match.group("key"), []).append(index)
    return found


def env_value(line: str) -> str:
    """The value of one `KEY: value` line, quotes stripped."""
    match = ENV_LINE.match(line.rstrip("\r"))
    return "" if match is None else match.group("value").strip("\"'")
