"""The accounts Play makes on a LandSandBoat server, and the passwords Yu'lon keeps for them.

The player names an account and Yu'lon does the rest (`catalog.LoaderLogin`):

* **It makes the account on the server.** LandSandBoat's connect server makes accounts
  on its auth port: a TLS connection, one JSON request, one JSON answer
  (`auth_session.cpp`, `login_cmd::LOGIN_CREATE` = 0x20). The request has the same
  shape as xiloader 2.2's own, and it carries the loader version the server checks.
  Asking for a name that exists changes nothing and answers `taken`, so Play asks every
  time. If the server's database was made again since (a reinstall), the account comes
  back with the kept password; if the account is already there, the server says so.
* **It keeps the password.** It is made here from letters and digits and kept in
  `accounts/<game>.json` under the config dir, owner-only like `db-secrets/`
  (`winacl.secure_folder`, mode 0o600), under the server's address and the name. It is
  kept BEFORE it is sent, so an account is never made with a password Yu'lon failed to
  write down. A refusal forgets it again.
* **It hands the account to the loader.** `write_login_file()` renders the catalog's
  login file (xiloader's `--json`: username, password, hide) beside it, owner-only too.
  Play names that file on the loader's command line in place of the password.

The server's certificate is not checked. A LandSandBoat server makes its own, signed by
no one, and xiloader accepts it the same way; the address is the one the tab plays on.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import ssl
import string
from collections.abc import Callable
from pathlib import Path

from yulon import platform, winacl
from yulon.catalog import composegen
from yulon.catalog.catalog import LoaderLogin
from yulon.log import get_logger

logger = get_logger(__name__)

DIR_NAME = "accounts"
"""Where the kept passwords and the login files live under `platform.config_dir()`."""

SECRET_MODE = 0o600
"""Owner-only from creation, as `dbsecret.SECRET_MODE` (on Windows the folder's DACL)."""

NAME_CHARS = re.compile(r"^[A-Za-z0-9]+$")
"""Letters and digits only: what xiloader's own account prompt and a JSON file both carry."""

PASSWORD_CHARS = string.ascii_letters + string.digits

COMMAND_CREATE = 0x20
"""`login_cmd::LOGIN_CREATE`."""

CREATED = 3
TAKEN = 4
CREATE_DISABLED = 8
CREATE_FAILED = 9
VERSION_UNSUPPORTED = 0x0B
"""The `login_result` answers a create can get (`login_helpers.h`)."""

TIMEOUT_SECONDS = 10.0
"""How long one request may take, connect included; a running server answers at once."""

REPLY_LIMIT = 4096
"""The most of an answer read: the server's buffer, and far more than a result needs."""

Ask = Callable[[str, int, bytes], bytes]
"""Send one request to `host:port` and return the answer's bytes. Raises `OSError`."""


class AccountRefusal(RuntimeError):
    """The account could not be made or used; the message says what to do next."""


def check_name(login: LoaderLogin, name: str) -> str | None:
    """Why `name` cannot be an account name, or None."""
    if not (login.name_min <= len(name) <= login.name_max) or not NAME_CHARS.match(name):
        return (
            f"An account name is {login.name_min} to {login.name_max} letters or digits, "
            "with no spaces."
        )
    return None


def make_password(length: int, *, choice: Callable[[str], str] = secrets.choice) -> str:
    """A new password of letters and digits, from the OS's cryptographic source."""
    return "".join(choice(PASSWORD_CHARS) for _ in range(length))


def ask_tls(host: str, port: int, payload: bytes) -> bytes:
    """One request over TLS, the way xiloader asks: send, then read until the server closes."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # the server's own certificate; see the module
    with (
        socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as raw,
        context.wrap_socket(raw) as conn,
    ):
        conn.sendall(payload)
        reply = b""
        while len(reply) < REPLY_LIMIT:
            try:
                chunk = conn.recv(REPLY_LIMIT - len(reply))
            except ssl.SSLEOFError:
                break
            if not chunk:
                break
            reply += chunk
            if _parsed(reply) is not None:
                break
    return reply


def _parsed(reply: bytes) -> dict[str, object] | None:
    """The answer as JSON, or None while it is incomplete (the server pads with NULs)."""
    try:
        value = json.loads(reply.rstrip(b"\0").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def create(
    login: LoaderLogin, host: str, port: int, name: str, password: str, *, ask: Ask = ask_tls
) -> int:
    """Ask the server to make `name`; its `result` code. Raises `OSError` or `ValueError`."""
    request = {
        "command": COMMAND_CREATE,
        "username": name,
        "password": password,
        "version": list(login.loader_version),
    }
    answer = _parsed(ask(host, port, json.dumps(request).encode("utf-8")))
    if answer is None or not isinstance(answer.get("result"), int):
        raise ValueError("the server's answer was not a result")
    result = answer["result"]
    assert isinstance(result, int)
    return result


# -- the kept passwords ------------------------------------------------------------


def accounts_path(game: str, *, config_dir: Path | None = None) -> Path:
    """Where `game`'s kept accounts are: `accounts/<game>.json` under the config dir."""
    root = config_dir if config_dir is not None else platform.config_dir()
    return root / DIR_NAME / f"{game}.json"


def login_file_path(game: str, *, config_dir: Path | None = None) -> Path:
    """Where the loader's login file is written: `accounts/<game>-login.json`."""
    root = config_dir if config_dir is not None else platform.config_dir()
    return root / DIR_NAME / f"{game}-login.json"


def _kept(path: Path) -> list[dict[str, str]]:
    """Every account kept in `path`; nothing for a missing or unreadable file."""
    winacl.secure_folder(path.parent, reading=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug(f"no kept accounts at {path}: {type(exc).__name__}")
        return []
    rows = raw.get("accounts") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    return [
        {"server": str(r["server"]), "name": str(r["name"]), "password": str(r["password"])}
        for r in rows
        if isinstance(r, dict)
        and all(isinstance(r.get(k), str) for k in ("server", "name", "password"))
    ]


def _same(row: dict[str, str], server: str, name: str) -> bool:
    """One account: the server's address, and the name as the server compares it (any case)."""
    return row["server"] == server and row["name"].casefold() == name.casefold()


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path`, owner-only, replacing it whole. Raises `OSError`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    winacl.secure_folder(path.parent)
    partial = path.with_name(path.name + ".partial")
    handle = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_MODE)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(partial, path)


def _save(path: Path, rows: list[dict[str, str]]) -> None:
    _write_private(path, json.dumps({"accounts": rows}, indent=2) + "\n")


def recall(game: str, server: str, name: str, *, config_dir: Path | None = None) -> str | None:
    """The password kept for `name` on `server`, or None."""
    for row in _kept(accounts_path(game, config_dir=config_dir)):
        if _same(row, server, name):
            return row["password"]
    return None


def remember(
    game: str, server: str, name: str, password: str, *, config_dir: Path | None = None
) -> None:
    """Keep `password` for `name` on `server`, in place of any kept before. Raises `OSError`."""
    path = accounts_path(game, config_dir=config_dir)
    rows = [row for row in _kept(path) if not _same(row, server, name)]
    rows.append({"server": server, "name": name, "password": password})
    _save(path, rows)


def forget(game: str, server: str, name: str, *, config_dir: Path | None = None) -> None:
    """Drop what is kept for `name` on `server`. Never raises: it only undoes a `remember`."""
    path = accounts_path(game, config_dir=config_dir)
    rows = _kept(path)
    kept = [row for row in rows if not _same(row, server, name)]
    if len(kept) == len(rows):
        return
    try:
        _save(path, kept)
    except OSError as exc:
        logger.warning(f"could not drop {name}'s unused password from {path}: {exc}")


# -- what Play calls ---------------------------------------------------------------


def ensure(
    game: str,
    login: LoaderLogin,
    *,
    host: str,
    port: int,
    name: str,
    loader: str,
    config_dir: Path | None = None,
    ask: Ask = ask_tls,
    choice: Callable[[str], str] = secrets.choice,
) -> str:
    """Make sure `name` is an account on the server whose password Yu'lon has; that password.

    Worker thread: one network request. Raises `AccountRefusal` with what to do.
    """
    kept = recall(game, host, name, config_dir=config_dir)
    password = kept if kept is not None else make_password(login.password_length, choice=choice)
    if kept is None:
        try:
            remember(game, host, name, password, config_dir=config_dir)
        except OSError as exc:
            raise AccountRefusal(
                f"Yu'lon could not keep the password for {name}, so the account was not made: "
                f"{exc.strerror or exc}"
            ) from exc
    try:
        result = create(login, host, port, name, password, ask=ask)
    except (OSError, ValueError) as exc:
        if kept is None:
            forget(game, host, name, config_dir=config_dir)
        logger.info(f"no answer from {host}:{port} while making {name}: {exc}")
        raise AccountRefusal(
            f"Nothing answered on {host}:{port}, where the server makes accounts. Press Start, "
            "wait for it to say running, then press Play again."
        ) from exc
    if result == CREATED:
        logger.info(f"made the account {name} on {host}:{port}")
        return password
    if result == TAKEN and kept is not None:
        return password
    if kept is None:
        forget(game, host, name, config_dir=config_dir)
    raise AccountRefusal(_refusal(result, name=name, loader=loader, login=login))


def _refusal(result: int, *, name: str, loader: str, login: LoaderLogin) -> str:
    if result == TAKEN:
        return (
            f"{name} is already an account on this server that Yu'lon did not make, so Yu'lon "
            f"does not know its password. Choose another name, or empty the account box and "
            f"press Play to log in to it at {loader}'s own prompt."
        )
    if result == CREATE_DISABLED:
        return "This server does not make new accounts (its login.ACCOUNT_CREATION is off)."
    if result == VERSION_UNSUPPORTED:
        version = ".".join(str(n) for n in login.loader_version)
        return f"The server refused the request: it expects another {loader} than {version}."
    return (
        f"The server could not make the account {name} (it answered {result}). The server "
        "console on this tab says why."
    )


def write_login_file(
    game: str,
    login: LoaderLogin,
    *,
    name: str,
    password: str,
    config_dir: Path | None = None,
) -> Path:
    """Write the loader's login file for `name`; its path. Raises `OSError`."""
    tokens = {"ACCOUNT": name, "PASSWORD": password}
    body = {
        key: composegen.fill(value, tokens) if isinstance(value, str) else value
        for key, value in login.file.items()
    }
    path = login_file_path(game, config_dir=config_dir)
    _write_private(path, json.dumps(body, indent=2) + "\n")
    return path
