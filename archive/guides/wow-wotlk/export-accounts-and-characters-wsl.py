#!/usr/bin/env python3
"""Pack a WotLK server's accounts and characters for a move to Yu'lon, from inside WSL.

Run this inside the WSL distro where the server lives, with no Yu'lon installed there. It reads
the server's database through the distro's Docker (the server's own database container), or
through the MySQL client programs (`mysql`, `mysqldump`) when Docker is not there, and writes one
file: `yulon-move-wow-wotlk-<stamp>-keep-private.zip`. On the other computer, a fresh WotLK
server in Yu'lon takes it through Maintenance > "Bring in accounts and characters…".

What the file holds: the login accounts (names, password verifiers, GM levels), the characters,
and the bot and Lua-engine databases where the server has them. Not the world database, not the
config files, not the modules. The database password is never written into the file or printed.

**Secrets.** The file holds every account's login verifier and salt. Keep it private, and delete
it once the move is done. It is created readable by you only.

Standard library only, so any distro with Python 3.10 or newer runs it. Exit status: 0 packed,
2 refused (the reason is printed in plain words and nothing was packed).
"""

from __future__ import annotations

import argparse
import errno
import getpass
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FORMAT = 1
MANIFEST_NAME = "yulon-move.json"
KEEP_PRIVATE = "keep-private"
SCRIPT_TAG = "wsl-export"
"""What `made_by.yulon` says: this file came from this script, not from a Yu'lon build."""

GAME_ID = "wow-wotlk"
GAME_NAME = "WoW WotLK"
SCHEMA = {
    "auth": "acore_auth",
    "characters": "acore_characters",
    "playerbots": "acore_playerbots",
    "ale": "acore_ale",
}
CONTAINERS = {"db": "ac-database", "auth": "ac-authserver", "world": "ac-worldserver"}
REALMLIST_TABLE = "realmlist"
REALM_ID = 1
CHARACTERS_TABLE = "characters"
CHARACTERS_ACCOUNT = "account"
DEFAULT_BOT_PREFIX = "rndbot"
BOT_PREFIX_KEYS = (
    "AiPlayerbot.RandomBotAccountPrefix",
    "Playerbots.RandomBotAccountPrefix",
)
BOT_CONF = Path("env/dist/etc/modules/playerbots.conf")
ACCOUNT_PREFIX = "YULON_"
"""Yu'lon's own command-channel account starts with this; it is not a player."""

SYSTEM_SCHEMAS = frozenset({"information_schema", "performance_schema", "mysql", "sys"})
ROLES = ("auth", "characters", "playerbots", "ale")
EVIDENCE_ROLES = ("auth", "characters")
OPTIONAL_EVIDENCE_ROLES = ("playerbots", "ale")
LEDGER_NAMES = ("updates", "migrations", "character_db_version", "realmd_db_version")
GAME_RECORD_TEMPLATE = "-- yulon-backup: game={game}"
SECRETS = (
    "This file holds every account's login verifier and salt, so anyone who has it can try to "
    "work out the passwords. Keep it private and delete it once the move is done. The database "
    "password and the command-channel credential are not in it."
)
PASSWORD_VARIABLES = ("MYSQL_ROOT_PASSWORD", "DOCKER_DB_ROOT_PASSWORD")
"""The names the server's own files use for the root password, the one compose reads first."""
PASSWORD_FILE = ".db_password"
STOPPED_CONTAINERS = ("auth", "world")
DUMP_FLAGS = ("--single-transaction", "--routines", "--events", "--triggers")
CHARSET_FLAG = "--default-character-set=utf8mb4"
"""Host-mode clients only: a MariaDB client defaults to utf8mb3, which turns 4-byte text into
question marks."""
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3306
OTHER_GAMES = (
    # (game id, name, database container): what the script names when the server is not WotLK.
    ("wow-tbc", "WoW TBC", "tbc-db"),
    ("wow-vanilla", "WoW Vanilla", "vanilla-db"),
    ("wow-tortoise", "WoW Tortoise", "tortoise-db"),
    ("wow-centurion", "Centurion", "centurion-db"),
    ("wow-unbound", "WoW Unbound", "ub-database"),
)
CHUNK = 1 << 20
MIN_PYTHON = (3, 10)
WORK_PREFIX = ".yulon-move-work-"
"""A run's dump folder is `<prefix><pid>-<random>`, so a later run can tell a dead run's from a
live one's."""
PARTIAL_SUFFIX = ".zip.partial"
STALE_PARTIAL_SECONDS = 600
"""A `.partial` this old, from before the current run began, is an earlier run's leftover."""

_BOT_PREFIX = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_DUMP_HEADER = re.compile(rb"(?:\A|\n)--\s+(MySQL|MariaDB)\s+dump\s")
_DUMP_TRAILER = b"-- Dump completed"
_USE_LINE = re.compile(rb"(?:\A|\n)USE\s+`([^`\n]+)`")
_CREATE_DB_LINE = re.compile(rb"(?:\A|\n)CREATE DATABASE[^\n`]*`([^`\n]+)`")
_USE_AT_START = re.compile(rb"USE\s+`([^`\n]+)`")
_TABLE_LINE = re.compile(
    rb"(?:/\*!\d+\s+)?(?:CREATE\s+TABLE|CREATE\s+VIEW|VIEW)\s+`((?:[^`\n]|``)+)`"
)
_EDGE_BYTES = 8192


class Refusal(Exception):
    """A reason the script stops, in a sentence a player can act on. Nothing was packed."""


@dataclass
class Access:
    """How this distro reaches the server's database: its container, or the MySQL client."""

    mode: str  # "docker" or "host"
    container: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    env: dict[str, str] = field(default_factory=dict)

    def argv(self, tool: str, extra: list[str], *, stdin: bool = False) -> list[str]:
        """The command for one client run. MYSQL_PWD is passed by name, never by value."""
        if self.mode == "docker":
            flags = ["-i"] if stdin else []
            return [
                "docker",
                "exec",
                *flags,
                "-e",
                "MYSQL_PWD",
                str(self.container),
                tool,
                *extra,
            ]
        return [tool, "-h", self.host, "-P", str(self.port), CHARSET_FLAG, *extra]


class AccessDenied(Refusal):
    """The database refused the password the script used."""


DISK_FULL = (
    "There is not enough free space where the package goes (the disk is full). Free some space "
    "or pick another folder with --out, then run this again. Nothing was packed."
)
DOCKER_SILENT_HINT = (
    " Docker is installed here but is not answering: start it (sudo service docker start), "
    "or add this user to the docker group, then run this again."
)
NO_PASSWORD = (
    "Yu'lon's script could not find the database password: it is not in the server folder's .env "
    "or compose files, and there is no terminal to ask for it. Run it from a terminal, or put "
    "MYSQL_ROOT_PASSWORD in the server folder's .env, then run it again. Nothing was packed."
)
REFUSED_PASSWORD = (
    "The database refused the password, so nothing was packed. Check this server's database "
    "root password (its .env or compose file) and run this again."
)
NO_TYPED_PASSWORD = "No password was typed, so nothing was packed."
NUL_PASSWORD = (
    "The database root password in the server folder holds a NUL character (a \\0 escape), "
    "which no program can be handed, so nothing was packed. Fix it in the .env or compose "
    "file and run this again."
)
RETRY_PROMPT = (
    "The password in the server folder was refused. "
    "Database root password (typed, not shown): "
)


def _not_wotlk(name: str) -> str:
    return (
        f"This server is {name}, not WoW WotLK. This script packs WoW WotLK servers only, so "
        "nothing was packed."
    )


def _other_game_in_names(names: set[str]) -> str | None:
    """The name of another game whose database container is among these names, if any."""
    for _game_id, name, database in OTHER_GAMES:
        if database in names:
            return name
    return None


def _other_game_in_compose(server_dir: Path | None) -> str | None:
    """The name of another game whose database container a compose file in the folder names."""
    if server_dir is None:
        return None
    text = ""
    for path in sorted(server_dir.glob("*compose*.y*ml")):
        try:
            text += path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    for _game_id, name, database in OTHER_GAMES:
        if re.search(rf"(?<![\w-]){re.escape(database)}(?![\w-])", text):
            return name
    return None


def _lit(value: str) -> str:
    """A SQL string literal's content: backslashes and quotes doubled, as Yu'lon writes them."""
    return value.replace("\\", "\\\\").replace("'", "''")


def _disk_full_message(message: str) -> bool:
    """Does a client's error text say the disk is full? (errno 28, or its strerror)"""
    return "errno 28" in message.lower() or "no space left" in message.lower()


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------- the database


_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)$")


def _choose_access(
    server_dir: Path | None, stopped_flag: bool, host: str, port: int
) -> Access:
    """Docker through the server's database container when it answers, else the MySQL client."""
    docker = shutil.which("docker")
    docker_silent = False
    if docker is not None:
        ready = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        docker_silent = ready.returncode != 0
        if ready.returncode == 0:
            inspect = subprocess.run(
                [docker, "inspect", "-f", "{{.State.Running}}", CONTAINERS["db"]],
                capture_output=True,
                text=True,
                check=False,
            )
            if inspect.returncode == 0:
                if inspect.stdout.strip() != "true":
                    raise Refusal(
                        f"The server's database container {CONTAINERS['db']} is stopped, so "
                        "there is nothing to read. Start only the database: in the "
                        "server folder (the one with docker-compose.yml) run "
                        f"docker compose start {CONTAINERS['db']}, and keep the world and auth "
                        "servers stopped, then run this again. Nothing was packed."
                    )
                for name in STOPPED_CONTAINERS:
                    state = subprocess.run(
                        [
                            docker,
                            "inspect",
                            "-f",
                            "{{.State.Running}}",
                            CONTAINERS[name],
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if state.returncode == 0 and state.stdout.strip() == "true":
                        raise Refusal(
                            "The server is running. Stop only the game servers: in the "
                            "server folder (the one with docker-compose.yml) run "
                            "docker compose stop "
                            f"{CONTAINERS['world']} {CONTAINERS['auth']}, and leave the database "
                            "running, so the copies agree, then run this again. Nothing was "
                            "packed."
                        )
                return Access(mode="docker", container=CONTAINERS["db"])
            listed = subprocess.run(
                [docker, "ps", "-a", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            other = (
                _other_game_in_names(set(listed.stdout.split()))
                if listed.returncode == 0
                else None
            )
            if other is not None:
                raise Refusal(_not_wotlk(other))
    other = _other_game_in_compose(server_dir)
    if other is not None:
        raise Refusal(_not_wotlk(other))
    if shutil.which("mysql") and shutil.which("mysqldump"):
        if not stopped_flag:
            raise Refusal(
                "This script can only see the database through the MySQL client, and that "
                "cannot tell whether the server is running. Stop the world and auth servers "
                "(leave the database running), then run this again with --server-stopped. "
                "Nothing was packed."
            )
        return Access(mode="host", host=host, port=port)
    raise Refusal(
        "Yu'lon's script found neither Docker with the server's database container nor the "
        "MySQL client programs (mysql and mysqldump) in this distro, so it cannot read the "
        "database. Start the server's Docker, or install the MySQL client with "
        "sudo apt install mysql-client, then run this again. Nothing was packed."
        + (DOCKER_SILENT_HINT if docker_silent else "")
    )


_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESCAPE = re.compile(r"\\(?:[abcfnrtv$\"\\]|0[0-9]{0,3})")
_ESCAPED = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    '"': '"',
    "\\": "\\",
}


def _expand(value: str, known: Mapping[str, str]) -> str:
    """Compose's variable substitution: `$$` is one `$`; `$NAME` and `${NAME}`; and the forms
    `${NAME:-d}` (unset or empty), `${NAME-d}` (unset), `${NAME:+d}` (set and not empty),
    `${NAME+d}` (set). A default is itself expanded. `known` holds final values: they are
    pasted as they are and never read again."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        following = value[index + 1 : index + 2]
        if char != "$":
            out.append(char)
            index += 1
        elif following == "$":
            out.append("$")
            index += 2
        elif following == "{":
            end = _closing_brace(value, index + 2)
            name = _VARIABLE_NAME.match(value, index + 2, end) if end != -1 else None
            if end == -1 or name is None:
                out.append(char)
                index += 1
                continue
            rest = value[name.end() : end]
            out.append(_substitute(name.group(0), rest, known))
            index = end + 1
        else:
            name = _VARIABLE_NAME.match(value, index + 1)
            if name is None:
                out.append(char)
                index += 1
            else:
                out.append(known.get(name.group(0), ""))
                index = name.end()
    return "".join(out)


def _closing_brace(value: str, start: int) -> int:
    """The `}` that closes a `${` whose body starts at `start`, nested `${...}` skipped."""
    depth = 1
    index = start
    while index < len(value):
        if value.startswith("${", index):
            depth += 1
            index += 2
            continue
        if value[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _substitute(name: str, rest: str, known: Mapping[str, str]) -> str:
    current = known.get(name)
    for operator in (":-", "-", ":+", "+", ":?", "?"):
        if rest.startswith(operator):
            operand = _expand(rest[len(operator) :], known)
            empty_counts = operator.startswith(":")
            missing = current is None or (empty_counts and current == "")
            if operator.endswith("-"):
                return operand if missing else str(current)
            if operator.endswith("+"):
                return "" if missing else operand
            return "" if missing else str(current)
    return current or ""


def _expand_escapes(text: str) -> str:
    """The escapes compose-go reads inside double quotes; `\\$` becomes the `$$` that stands
    for a literal dollar."""

    def one(match: re.Match[str]) -> str:
        escape = match.group(0)[1:]
        if escape == "$":
            return "$$"
        if escape[0] == "0":
            # compose-go rewrites `\0NNN` to `\NNN` for Go's strconv.UnquoteChar, which
            # takes exactly three octal digits worth at most 255; when it refuses, the
            # rewritten text stays as it is.
            digits = escape[1:]
            if (
                len(digits) == 3
                and set(digits) <= set("01234567")
                and int(digits, 8) <= 255
            ):
                return chr(int(digits, 8))
            return "\\" + digits
        return _ESCAPED[escape]

    return _ESCAPE.sub(one, text)


def _quoted_value(raw: str) -> str:
    """The inside of a quoted value, up to its closing quote: a backslash before the quote
    keeps the quote and goes; any other backslash stays, as compose-go reads it."""
    quote = raw[0]
    chars: list[str] = []
    escaped = False
    for char in raw[1:]:
        if char != quote:
            if not escaped and char == "\\":
                escaped = True
                continue
            if escaped:
                escaped = False
                chars.append("\\")
            chars.append(char)
        elif escaped:
            escaped = False
            chars.append(char)
        else:
            return "".join(chars)
    return "".join(chars)


def _dotenv_value(raw: str, known: Mapping[str, str]) -> str:
    """One `.env` value in its final form, as `docker compose` reads it (compose-go's dotenv):
    single quotes are literal; double quotes take escapes and `$` substitution; an unquoted
    value ends at a space followed by `#`, and takes `$` substitution."""
    raw = raw.strip()
    if raw[:1] == "'":
        return _quoted_value(raw)
    if raw[:1] == '"':
        return _expand(_expand_escapes(_quoted_value(raw)), known)
    return _expand(raw.split(" #", 1)[0].rstrip(), known)


def _dotenv(text: str) -> dict[str, str]:
    """The `.env` file as a map of final values. Later lines see earlier ones; the last
    setting of a key wins."""
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^export\s+", "", line)
        match = _ASSIGNMENT.match(line)
        if match is not None:
            found[match.group(1)] = _dotenv_value(match.group(2), found)
    return found


def _compose_assignments(text: str) -> dict[str, str]:
    """The `KEY: value`, `KEY=value` and `- KEY=value` lines of a compose file, as written (a
    quoted value loses its quotes; compose still substitutes inside them)."""
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.match(re.sub(r"^-\s+", "", line))
        if match is None:
            continue
        raw = match.group(2).strip()
        if raw[:1] in ("'", '"'):
            end = raw.find(raw[0], 1)
            found[match.group(1)] = raw[1:end] if end != -1 else raw[1:]
        else:
            found[match.group(1)] = raw.split(" #", 1)[0].rstrip()
    return found


def _found_password(server_dir: Path | None) -> str | None:
    """The first root password the server's own files name, in the order compose would use them."""
    if server_dir is None:
        return None
    try:
        env_file = _dotenv(
            (server_dir / ".env").read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        env_file = {}
    candidates: list[str] = []
    for path in sorted(server_dir.glob("*compose*.y*ml")):
        try:
            declared = _compose_assignments(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            continue
        candidates += [
            _expand(declared[key], env_file)
            for key in PASSWORD_VARIABLES
            if key in declared
        ]
    candidates += [env_file[key] for key in PASSWORD_VARIABLES if key in env_file]
    try:
        candidates.append(
            (server_dir / PASSWORD_FILE).read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError):
        pass
    return next((found for found in candidates if found), None)


def _typed_password(prompt: str) -> str:
    typed = getpass.getpass(prompt)
    if not typed:
        raise Refusal(NO_TYPED_PASSWORD)
    return typed


def _sql(access: Access, sql: str) -> str:
    argv = access.argv(
        "mysql", ["-uroot", "--batch", "--skip-column-names"], stdin=True
    )
    proc = subprocess.run(
        argv,
        input=sql.encode("utf-8") + b"\n",
        capture_output=True,
        env=access.env,
        check=False,
    )
    if proc.returncode != 0:
        message = _text(proc.stderr).strip()
        if "Access denied" in message or "ERROR 1045" in message:
            raise AccessDenied("The database refused the password.")
        raise Refusal(
            "The database did not answer the question the script asks, so nothing was packed "
            f"({message[:300] or 'no message'})."
        )
    return _text(proc.stdout)


def _databases(access: Access) -> list[str]:
    return [
        line.strip()
        for line in _sql(access, "SHOW DATABASES;").splitlines()
        if line.strip()
    ]


def _names(access: Access, sql: str) -> list[str]:
    return [line.strip() for line in _sql(access, sql).splitlines() if line.strip()]


def _digest(names: list[str]) -> str:
    """Same as Yu'lon's digest_of: SHA-256 of the sorted names, one per line."""
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


def _evidence(access: Access, schema: str) -> dict[str, object] | None:
    """What the schema says about its own version, as Yu'lon's `_read_version` reads it."""
    try:
        listed = _sql(
            access,
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{_lit(schema)}' AND TABLE_NAME IN "
            "('updates', 'migrations', 'character_db_version', 'realmd_db_version');",
        ).split()
        if "updates" not in listed:
            return None
        quoted = f"`{schema}`"
        items = _names(
            access,
            f"SELECT `name` FROM {quoted}.`updates` "
            "WHERE `state` IN ('RELEASED', 'ARCHIVED') ORDER BY `name`;",
        )
        modules = sorted(
            line.strip().replace("\t", "|")
            for line in _sql(
                access,
                f"SELECT `name`, `hash` FROM {quoted}.`updates` "
                "WHERE `state` NOT IN ('RELEASED', 'ARCHIVED') ORDER BY `name`;",
            ).splitlines()
            if line.strip()
        )
    except Refusal:
        return None
    if not items:
        return None
    return {
        "kind": "updates",
        "count": len(items),
        "digest": _digest(items),
        "modules": modules,
    }


def _bot_prefix(server_dir: Path | None) -> str:
    """The bot account prefix: the playerbots conf's one value, else the default; else refuse."""
    if server_dir is None:
        return DEFAULT_BOT_PREFIX
    try:
        text = (server_dir / BOT_CONF).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return DEFAULT_BOT_PREFIX
    except OSError as exc:
        raise Refusal(
            f"Yu'lon's script could not read {BOT_CONF}: {exc}. Nothing was packed."
        ) from exc
    values: list[str] = []
    for key in BOT_PREFIX_KEYS:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=(.*)$")
        for line in text.splitlines():
            hit = pattern.match(line)
            if hit:
                values.append(hit.group(1).strip().strip('"'))
    if not values:
        return DEFAULT_BOT_PREFIX
    if len(set(values)) > 1:
        raise Refusal(
            "The bot account prefix is set more than once in the playerbots conf, to different "
            "values, so the script cannot tell which one the server uses. Nothing was packed."
        )
    if not values[0]:
        raise Refusal(
            "The bot account prefix is blank in the playerbots conf. Set it, then run this again. "
            "Nothing was packed."
        )
    if _BOT_PREFIX.fullmatch(values[0]) is None:
        raise Refusal(
            f"The bot account prefix {values[0]!r} has characters a package cannot hold. "
            "Nothing was packed."
        )
    return values[0]


def _counts(
    access: Access, auth: str, chars: str, prefix: str
) -> tuple[int, int, int, int]:
    bot = f"LEFT(UPPER(`username`), {len(prefix)}) = '{_lit(prefix.upper())}'"
    app = f"LEFT(UPPER(`username`), {len(ACCOUNT_PREFIX)}) = '{ACCOUNT_PREFIX}'"
    out = _sql(
        access,
        "SELECT "
        f"(SELECT COUNT(*) FROM `{auth}`.`account` WHERE NOT ({bot}) AND NOT ({app})), "
        f"(SELECT COUNT(*) FROM `{chars}`.`{CHARACTERS_TABLE}` WHERE `{CHARACTERS_ACCOUNT}` NOT IN "
        f"(SELECT `id` FROM `{auth}`.`account` WHERE ({bot}) OR ({app}))), "
        f"(SELECT COUNT(*) FROM `{auth}`.`account` WHERE ({bot})), "
        f"(SELECT COUNT(*) FROM `{chars}`.`{CHARACTERS_TABLE}` WHERE `{CHARACTERS_ACCOUNT}` IN "
        f"(SELECT `id` FROM `{auth}`.`account` WHERE ({bot})));",
    )
    fields = out.strip().split("\t")
    if len(fields) != 4 or not all(f.strip().isdigit() for f in fields):
        raise Refusal(
            "The database returned the account counts in a shape the script does not "
            "read. Nothing was packed."
        )
    first, second, third, fourth = (int(f.strip()) for f in fields)
    return first, second, third, fourth


def _realm_name(access: Access, auth: str) -> str | None:
    try:
        out = _sql(
            access,
            f"SELECT `name` FROM `{auth}`.`{REALMLIST_TABLE}` WHERE `id` = {REALM_ID};",
        )
    except Refusal:
        return None
    name = out.strip("\r\n").replace("\\\\", "\\")
    return name or None


# ---------------------------------------------------------------- the dumps


def _private_open(path: str, flags: int) -> int:
    """Created 0600 and never reused: the dumps hold every verifier."""
    return os.open(path, flags | os.O_EXCL, 0o600)


def _only_dump_preamble(prefix: bytes) -> bool:
    """Is everything above the banner a comment a dump itself would write? (Yu'lon's rule.)"""
    index, end = 0, len(prefix)
    while index < end:
        if prefix[index : index + 1].isspace():
            index += 1
        elif prefix.startswith(b"--", index):
            newline = prefix.find(b"\n", index)
            if newline == -1:
                return True
            index = newline + 1
        elif prefix.startswith(b"/*", index):
            close = prefix.find(b"*/", index + 2)
            if close == -1:
                return False
            index = close + 2
        else:
            return False
    return True


def _banner_is_the_files_own(head: bytes) -> bool:
    match = _DUMP_HEADER.search(head)
    return match is not None and _only_dump_preamble(head[: match.start()])


def _databases_named_in(data: bytes) -> set[str]:
    names = {m.group(1).decode("utf-8", "replace") for m in _USE_LINE.finditer(data)}
    names |= {
        m.group(1).decode("utf-8", "replace") for m in _CREATE_DB_LINE.finditer(data)
    }
    return names


def _verify(path: Path, database: str) -> None:
    """Yu'lon's `verify_dump` on the file: non-empty, a dump's banner, the end marker, the name."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(_EDGE_BYTES)
        fh.seek(max(size - _EDGE_BYTES, 0))
        tail = fh.read(_EDGE_BYTES)
    if size == 0 or not _banner_is_the_files_own(head):
        raise Refusal(
            f"The dump of {database} does not start like a mysqldump. Nothing was packed."
        )
    if _DUMP_TRAILER not in tail:
        raise Refusal(
            f"The dump of {database} stops before mysqldump's end marker, so it was cut short. "
            "Nothing was packed."
        )
    if database not in _databases_named_in(head):
        raise Refusal(f"The dump does not name {database}. Nothing was packed.")


def _tables_of(path: Path, database: str) -> list[str]:
    """The tables and views of one database in a dump, read as Yu'lon's `tables_in_copy` does."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    with path.open("rb") as fh:
        for line in fh:
            if line.startswith(b"USE "):
                use = _USE_AT_START.match(line)
                if use is not None:
                    current = use.group(1).decode("utf-8", "replace")
                    found.setdefault(current, [])
                continue
            if current is None or not line.startswith((b"CREATE ", b"/*!")):
                continue
            table = _TABLE_LINE.match(line)
            if table is not None:
                name = table.group(1).decode("utf-8", "replace").replace("``", "`")
                if name not in found[current]:
                    found[current].append(name)
    tables = found.get(database, [])
    if not tables:
        raise Refusal(f"The dump of {database} lists no table, so nothing was packed.")
    return tables


def _dump(access: Access, database: str, target: Path) -> list[str]:
    """One database through mysqldump into `target`, with the record in front (T603's line)."""
    ignore = (
        [f"--ignore-table={database}.{REALMLIST_TABLE}"]
        if database == SCHEMA["auth"]
        else []
    )
    argv = access.argv(
        "mysqldump", ["-uroot", *DUMP_FLAGS, *ignore, "--databases", database]
    )
    record = (GAME_RECORD_TEMPLATE.format(game=GAME_ID) + "\n").encode("ascii")
    with os.fdopen(_private_open(str(target), os.O_WRONLY | os.O_CREAT), "wb") as sink:
        sink.write(record)
        sink.flush()
        proc = subprocess.run(
            argv, stdout=sink, stderr=subprocess.PIPE, env=access.env, check=False
        )
    if proc.returncode != 0:
        if _disk_full_message(_text(proc.stderr)):
            raise Refusal(DISK_FULL)
        raise Refusal(
            f"mysqldump could not dump {database} ({_text(proc.stderr).strip()[:300]}). "
            "Nothing was packed."
        )
    if target.stat().st_size == len(record):
        raise Refusal(f"mysqldump wrote nothing for {database}. Nothing was packed.")
    _verify(target, database)
    return _tables_of(target, database)


# ---------------------------------------------------------------- the package


def _stamp(made: datetime) -> str:
    return f"{made:%Y%m%d-%H%M}"


def _manifest_text(
    made: datetime,
    members: list[dict[str, object]],
    evidence: dict[str, dict[str, object]],
    realm: str | None,
    counts: tuple[int, int, int, int],
    bot_prefix: str,
    excluded: list[str],
) -> str:
    """The manifest in Yu'lon's own layout: the field order and the indent of its model."""
    accounts, characters, bot_accounts, bot_characters = counts
    manifest: dict[str, object] = {
        "format": FORMAT,
        "kind": "characters",
        "made_by": {"yulon": SCRIPT_TAG, "platform": "linux", "made": made.isoformat()},
        "game": {"id": GAME_ID, "name": GAME_NAME},
        "databases": members,
        "schema_evidence": evidence,
        "realm_name": realm,
        "channel_account": None,
        "bot_prefix": bot_prefix,
        "counts": {
            "accounts": accounts,
            "characters": characters,
            "bot_accounts": bot_accounts,
            "bot_characters": bot_characters,
        },
        "excluded": excluded,
        "secrets": SECRETS,
        "server": None,
    }
    return json.dumps(manifest, indent=2, ensure_ascii=False)


def _write_package(dest: Path, manifest: str, dumps: list[tuple[str, Path]]) -> None:
    """Write `dest` through a `.partial`, read it back against the manifest, then rename it.

    The `.partial` holds every account's verifier and salt, so any failure removes it before the
    refusal is raised, as Yu'lon's own write_package does.
    """
    partial = dest.with_name(dest.name + ".partial")
    # A leftover `.partial` goes first: reopening it would keep whatever mode it was made with.
    partial.unlink(missing_ok=True)
    try:
        fd = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w+b") as raw:
            with zipfile.ZipFile(
                raw,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                archive.writestr(MANIFEST_NAME, manifest)
                for schema, path in dumps:
                    with path.open("rb") as source:
                        with archive.open(
                            f"db/{schema}.sql", "w", force_zip64=True
                        ) as sink:
                            shutil.copyfileobj(source, sink, CHUNK)
        _read_back(partial, manifest)
        os.replace(partial, dest)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise Refusal(DISK_FULL) from exc
        raise Refusal(
            f"The package could not be written ({exc.strerror or exc}). Nothing was packed."
        ) from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, but belongs to someone else
    return True


def _sweep_leftovers(out_dir: Path, started: float) -> None:
    """Remove what an earlier run of this script left in the output folder when it was killed
    before it could clean up: its dump folder (the owner's process is gone) and its `.partial`
    package (older than this run and than a few minutes). Both hold every account's verifier.
    Anything else in the folder is left alone."""
    for work in out_dir.glob(f"{WORK_PREFIX}*"):
        pid = re.fullmatch(rf"{re.escape(WORK_PREFIX)}(\d+)-.*", work.name)
        if pid is None or not work.is_dir() or _pid_is_alive(int(pid.group(1))):
            continue
        shutil.rmtree(work, ignore_errors=True)
    for partial in out_dir.glob(f"yulon-move-{GAME_ID}-*{PARTIAL_SUFFIX}"):
        try:
            age = partial.stat().st_mtime
        except OSError:
            continue
        if age < started and age < time.time() - STALE_PARTIAL_SECONDS:
            partial.unlink(missing_ok=True)


def _sha256_of(path: Path) -> str:
    """SHA-256 of a file read in chunks, so a big dump is never held whole in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _read_back(partial: Path, manifest: str) -> None:
    """Every member of the written file is checked against the manifest it carries."""
    listed = {
        m["file"]: (m["sha256"], m["bytes"]) for m in json.loads(manifest)["databases"]
    }
    with zipfile.ZipFile(partial) as archive:
        if sorted(archive.namelist()) != sorted([MANIFEST_NAME, *listed]):
            raise Refusal(
                "The package did not read back as it was written. Nothing was packed."
            )
        if archive.read(MANIFEST_NAME).decode("utf-8") != manifest:
            raise Refusal(
                "The package's manifest did not read back as it was written. "
                "Nothing was packed."
            )
        for name, (digest, size) in listed.items():
            hashed = hashlib.sha256()
            length = 0
            with archive.open(name) as member:
                while chunk := member.read(CHUNK):
                    hashed.update(chunk)
                    length += len(chunk)
            if hashed.hexdigest() != digest or length != size:
                raise Refusal(
                    "A part of the package did not read back as it was written. "
                    "Nothing was packed."
                )


def _count_phrase(counts: tuple[int, int, int, int]) -> str:
    accounts, characters, bot_accounts, bot_characters = counts

    def noun(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    text = f"{noun(accounts, 'account')} and {noun(characters, 'character')} of players"
    if bot_accounts:
        bots = noun(bot_accounts, "bot account")
        text += f", plus {bots} with {noun(bot_characters, 'bot character')}"
    return text


# ---------------------------------------------------------------- the run


def pack(
    server_dir: Path | None,
    out_dir: Path,
    stopped_flag: bool,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> tuple[Path, tuple[int, int, int, int]]:
    started = time.time()
    access = _choose_access(server_dir, stopped_flag, host, port)
    found = _found_password(server_dir)
    typed = found is None
    if found is not None and "\0" in found:
        raise Refusal(NUL_PASSWORD)
    if found is None:
        if not sys.stdin.isatty():
            raise Refusal(NO_PASSWORD)
        found = _typed_password("Database root password (typed, not shown): ")
    access.env = {**os.environ, "MYSQL_PWD": found}
    try:
        databases = _databases(access)
    except AccessDenied as first:
        # A password found in the server folder may be stale: asked for once more, no more.
        if typed or not sys.stdin.isatty():
            raise Refusal(REFUSED_PASSWORD) from first
        access.env = {**os.environ, "MYSQL_PWD": _typed_password(RETRY_PROMPT)}
        try:
            databases = _databases(access)
        except AccessDenied as second:
            raise Refusal(REFUSED_PASSWORD) from second
    wanted: list[tuple[str, str]] = []
    for role in ROLES:
        schema = SCHEMA[role]
        if schema in databases:
            wanted.append((role, schema))
    schemas = dict(wanted)
    for role in EVIDENCE_ROLES:
        if role not in schemas:
            raise Refusal(
                f"This server has no {SCHEMA[role]}, so there are no accounts and characters to "
                "pack. Nothing was packed."
            )
    evidence: dict[str, dict[str, object]] = {}
    for role, schema in wanted:
        version = _evidence(access, schema)
        if version is None:
            if role in EVIDENCE_ROLES:
                raise Refusal(
                    f"The database version of {schema} could not be read, so a package made from "
                    "it could not be checked on the other computer. Nothing was packed."
                )
            continue
        evidence[schema] = version
    prefix = _bot_prefix(server_dir)
    counts = _counts(access, schemas["auth"], schemas["characters"], prefix)
    realm = _realm_name(access, schemas["auth"])
    excluded = [
        f"{name} (not part of accounts and characters)"
        for name in databases
        if name not in schemas.values() and name not in SYSTEM_SCHEMAS
    ]
    excluded.append(f"{REALMLIST_TABLE} of {schemas['auth']} (the realm's own address)")

    made = datetime.now()
    dest = out_dir / f"yulon-move-{GAME_ID}-{_stamp(made)}-{KEEP_PRIVATE}.zip"
    if dest.exists():
        raise Refusal(
            f"{dest.name} already exists, so the script did not overwrite it."
        )
    _sweep_leftovers(out_dir, started)
    work = Path(tempfile.mkdtemp(prefix=f"{WORK_PREFIX}{os.getpid()}-", dir=out_dir))
    try:
        members: list[dict[str, object]] = []
        dumps: list[tuple[str, Path]] = []
        for role, schema in wanted:
            target = work / f"{schema}.sql"
            tables = _dump(access, schema, target)
            digest = _sha256_of(target)
            members.append(
                {
                    "schema": schema,
                    "role": role,
                    "file": f"db/{schema}.sql",
                    "bytes": target.stat().st_size,
                    "sha256": digest,
                    "tables": tables,
                }
            )
            dumps.append((schema, target))
        manifest = _manifest_text(
            made, members, evidence, realm, counts, prefix, excluded
        )
        _write_package(dest, manifest, dumps)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest, counts


def _end_on_signal(number: int, _frame: object) -> None:
    """Closing the WSL window (SIGHUP) or a kill (SIGTERM) ends the run the way an exit does,
    so the dump folder and the `.partial` package are removed on the way out."""
    raise SystemExit(128 + number)


def main(argv: list[str] | None = None) -> int:
    if tuple(sys.version_info[:2]) < MIN_PYTHON:
        found = ".".join(str(part) for part in sys.version_info[:2])
        print(
            f"This script needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, and this is "
            f"Python {found}. Run it with a newer python3, then run this again. Nothing was "
            "packed.",
            file=sys.stderr,
        )
        return 2
    caught = [
        (number, signal.signal(number, _end_on_signal))
        for number in (
            getattr(signal, "SIGHUP", None),
            getattr(signal, "SIGTERM", None),
        )
        if number is not None
    ]
    try:
        return _main(argv)
    finally:
        for number, previous in caught:
            signal.signal(number, previous if previous is not None else signal.SIG_DFL)


def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(
        description="Pack a WotLK server's accounts and characters for a move to Yu'lon."
    )
    parser.add_argument(
        "--server-dir",
        type=Path,
        help="the server folder (for its password " "and bot prefix)",
    )
    parser.add_argument(
        "--out", type=Path, help="the folder for the package (default: Documents)"
    )
    parser.add_argument(
        "--server-stopped",
        action="store_true",
        help="say the server is stopped (needed when only the MySQL client is "
        "there)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="MySQL client mode only: the host (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="MySQL client mode only: the port (default 3306)",
    )
    args = parser.parse_args(argv)
    out_dir = args.out or (
        Path.home() / "Documents"
        if (Path.home() / "Documents").is_dir()
        else Path.home()
    )
    try:
        if not out_dir.is_dir():
            raise Refusal(f"The folder {out_dir} does not exist. Nothing was packed.")
        if not 0 < args.port < 65536:
            raise Refusal("The port must be between 1 and 65535. Nothing was packed.")
        dest, counts = pack(
            args.server_dir, out_dir, args.server_stopped, args.host, args.port
        )
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            print(DISK_FULL, file=sys.stderr)
            return 2
        print(
            "Yu'lon's script could not finish writing the package "
            f"({exc.strerror or exc}). Nothing was packed.",
            file=sys.stderr,
        )
        return 2
    print(f"Packed {_count_phrase(counts)} into {dest}.")
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro:
        print(
            f"In Windows Explorer: \\\\wsl.localhost\\{distro}{str(dest).replace('/', chr(92))}"
        )
    print(
        "Keep it private. On the new computer, open a fresh WotLK server's Maintenance tab in "
        "Yu'lon and choose Bring in accounts and characters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
