"""The TortoiseBots dashboard's footprint in an install's files (T127). No Docker here.

TortoiseBots ships an optional web dashboard (`tools/observability`, a Go daemon;
upstream's `docs/guides/observability-dashboard.md`). The module sends its
telemetry over UDP to `AiPlayerbot.ObservabilityHost:ObservabilityPort` when
`AiPlayerbot.Observability = 1`, and the daemon serves the map, the roster, the
Armory and Prometheus `/metrics` on port 8095. Switching it on is two edits to
two files, and this module owns both:

* **three keys in `etc/modules/tortoise_bots.conf`.** The host is the dashboard
  SERVICE's name, not `127.0.0.1`: the world runs in its own container on the
  compose network, and the module resolves the name once, when it loads
  (`ObservabilityEmitter::Initialize`, `getaddrinfo`, falling back to
  127.0.0.1 for good if the name does not resolve). So the daemon has to be up
  before the world starts, and a world started before it sends nowhere until
  its next start.
* **one service block in `docker-compose.yml`**, between two marker lines this
  module writes and is the only thing that removes. Only those lines are ours
  (the lane rule: patch only the lines you own). The block follows the file's
  own conventions: named after its container, the image written in, the host
  bind carrying whatever SELinux label the file's other binds carry (read off
  the file, never probed), and the one published port pinned to loopback unless
  the player opted into their network.

**The block is the record of the switch.** "On" means the block is in the base
file. Every writer that replaces a file the switch touched carries the switch
over from what is on disk (T117's rule for the bot count): `composegen.render()`
re-appends a block it finds in the file it replaces, and the CMaNGOS conf stage
lays these three keys over the catalog's table while the block is there. The
Tortoise controller's Start re-asserts the keys too, for a writer this branch
does not have yet (the Tuning tab's Reset to default, T94).

**Secrets.** The daemon reads the database with this install's own `mangos`
user, whose password is the generated secret `.env` already holds as
`DB_ROOT_PASSWORD` (the import creates that user with the same value). It is
named by `${...}` here, so no password is ever written into the compose file,
and nothing in this module logs a value. The daemon's session cookie is HMAC'd
with `SESSION_SECRET`, whose compiled-in default is a public string: left
unset, anyone could forge a GM session. So each install gets its own random
secret, kept in `.env` beside the password.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from yulon.catalog.catalog import CatalogEntry, ConfPatch, ConfPatchTable
from yulon.log import get_logger

logger = get_logger(__name__)

HTTP_PORT = 8095
"""The dashboard's web port, upstream's default, published on the host."""

UDP_PORT = 9195
"""Where the module sends its telemetry. Container to container only: never published."""

URL = f"http://localhost:{HTTP_PORT}"
"""What "Open bot dashboard" opens."""

SWITCH_KEY = "AiPlayerbot.Observability"
"""The module's own switch. The catalog's conf table names the file that holds it."""

HOST_KEY = "AiPlayerbot.ObservabilityHost"
PORT_KEY = "AiPlayerbot.ObservabilityPort"

SERVICE_SUFFIX = "observability"
"""The service and container are `<the entry's container prefix>observability`."""

SECRET_VAR = "YULON_BOT_DASHBOARD_SECRET"
"""The `.env` variable holding this install's session secret."""

ETC_DIR = "etc"
"""Where the conf table's files live under the server dir (`cmangos.ETC_DIR`; a test pins it)."""

DBC_DIR = "data/dbc"
"""The DBC files the extract stage wrote, which the Armory reads to group class spells."""

MIN_GM_LEVEL = 2
"""The daemon's own default `--min-gm-level`: a sign-in needs a GM rank of at least this."""

CONF_BACKUP_SUFFIX = ".before-dashboard"
"""The copy of the conf the first switch-on takes, and the switch-off puts the keys back from."""

BEGIN = (
    "  # >>> Yu'lon: the bot dashboard (T127). The Bots tab's switch adds and removes this block."
)
END = "  # <<< Yu'lon: the bot dashboard (T127)."


class DashboardError(RuntimeError):
    """A refusal, worded for the person who pressed the switch. Nothing was written."""


@dataclass(frozen=True)
class State:
    """What this install's files say about the dashboard. Read off the GUI thread."""

    on: bool = False
    lan: bool = False
    problem: str = ""
    """Why the state could not be read; `on`/`lan` are then meaningless."""


# -- names, read off the entry ------------------------------------------------


def conf_file(entry: CatalogEntry) -> str | None:
    """The conf (relative to `etc/`) the catalog's table puts `SWITCH_KEY` in, or None.

    The catalog is where this game says which file carries the module's switch
    (`"AiPlayerbot.Observability": "0"` in the table since T30), so this module
    names no file of its own. An entry whose table does not mention the key has
    no dashboard, and every caller gates on this answer.
    """
    native = entry.install.native
    data = native.cmangos if native is not None else None
    if data is None:
        return None
    for name, patch in data.conf.files.items():
        if SWITCH_KEY in patch.keys:
            return name
    return None


def service(entry: CatalogEntry) -> str:
    """The dashboard's compose service and container name, `tortoise-observability`."""
    from yulon.catalog import composegen  # noqa: PLC0415 - composegen imports this module

    return composegen.entry_tokens(entry)["CONTAINER_PREFIX"] + SERVICE_SUFFIX


def image_ref(entry: CatalogEntry, server_dir: Path) -> str:
    """The dashboard image's reference, in this install's own image namespace and tag."""
    from yulon.catalog import composegen  # noqa: PLC0415 - composegen imports this module

    native = entry.install.native
    prefix = native.image_prefix if native is not None else "yulon.local/"
    return f"{prefix}{SERVICE_SUFFIX}:{composegen.image_tag(server_dir)}"


def conf_keys(entry: CatalogEntry) -> dict[str, str]:
    """The three keys that send the module's telemetry to the dashboard service."""
    return {SWITCH_KEY: "1", HOST_KEY: service(entry), PORT_KEY: str(UDP_PORT)}


def conf_path(entry: CatalogEntry, server_dir: Path) -> Path | None:
    name = conf_file(entry)
    return None if name is None else server_dir / ETC_DIR / name


def base_path(server_dir: Path) -> Path:
    from yulon.catalog import composegen  # noqa: PLC0415 - composegen imports this module

    return server_dir / composegen.BASE_FILE


# -- the compose block ----------------------------------------------------------


_HOST_BIND = re.compile(r"""^\s*-\s*["']?\./[^"'\s]*["']?\s*$""")


def bind_label(text: str) -> str:
    """The SELinux label the file's own host binds carry: `":z"`, `""`, or a refusal.

    Read off the installed file and never probed (lane rule, 2026-09-25): three
    lanes re-asked `getenforce` when they rewrote a compose file, and a
    permissive moment or a failing probe stripped `:z` from an enforcing
    install. All binds labelled means label ours; none means none; a mix is a
    file somebody changed by hand, and this module will not guess which half is
    right.
    """
    binds = [
        line.strip().lstrip("-").strip().strip("\"'")
        for line in remove(text).splitlines()
        if _HOST_BIND.match(line)
    ]
    labelled = [b for b in binds if b.endswith(":z") or b.endswith(",z")]
    if not binds or not labelled:
        return ""
    if len(labelled) == len(binds):
        return ":z"
    raise DashboardError(
        "Some of the folders docker-compose.yml shares with the server carry an SELinux label "
        "and some do not, so Yu'lon cannot tell which way this one should go. Nothing was "
        "changed."
    )


def block(entry: CatalogEntry, server_dir: Path, *, lan: bool, label: str, dbc: bool) -> str:
    """The service block, marker lines included, ending in a newline.

    `dbc` is whether `data/dbc` exists: the Armory groups a bot's class spells
    from those files and falls back to guessing from spell names without them,
    so a missing folder drops the mount rather than letting Docker create an
    empty, root-owned one in the server folder.
    """
    from yulon.catalog import composegen  # noqa: PLC0415 - composegen imports this module

    if label not in ("", ":z"):
        raise DashboardError(f"{label!r} is not a bind label this app writes")
    tokens = composegen.entry_tokens(entry)
    name = service(entry)
    bind = "0.0.0.0" if lan else "127.0.0.1"
    lines = [
        BEGIN,
        "  # The world sends its bot telemetry here by this service's NAME over the",
        f"  # compose network (UDP {UDP_PORT}, never published); only the web page is.",
        "  # The database password and the session secret come from .env, never from",
        "  # this file.",
        f"  {name}:",
        f"    container_name: {name}",
        f"    image: {image_ref(entry, server_dir)}",
        f"    networks: [{tokens['CONTAINER_PREFIX']}net]",
        "    restart: unless-stopped",
        "    environment:",
        f'      HTTP_PORT: "{HTTP_PORT}"',
        "      UDP_HOST: 0.0.0.0",
        f'      UDP_PORT: "{UDP_PORT}"',
        f"      DB_HOST: {tokens['DB_HOST']}",
        '      DB_PORT: "3306"',
        f"      DB_USER: {tokens['DB_USER']}",
        "      DB_PASSWORD: ${DB_ROOT_PASSWORD:?Yu'lon .env is missing}",
        f"      DB_LOGIN: {tokens['AUTH_DB']}",
        f"      DB_CHAR: {tokens['CHAR_DB']}",
        f"      DB_WORLD: {tokens['WORLD_DB']}",
        f'      MIN_GM_LEVEL: "{MIN_GM_LEVEL}"',
    ]
    if dbc:
        lines.append("      DBC_DIR: /dbc")
    lines += [
        f"      SESSION_SECRET: ${{{SECRET_VAR}:?Yu'lon .env is missing}}",
        "    ports:",
        f'      - "{bind}:{HTTP_PORT}:{HTTP_PORT}"',
    ]
    if dbc:
        options = "ro,z" if label else "ro"
        lines += ["    volumes:", f"      - ./{DBC_DIR}:/dbc:{options}"]
    lines.append(END)
    return "\n".join(lines) + "\n"


def block_in(text: str) -> str | None:
    """The block exactly as it stands in `text`, marker lines included, or None.

    A BEGIN with no END after it is not a block this module can remove safely,
    so it reads as absent and `add()` refuses to write a second one on top.
    """
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN)
        stop = next(i for i in range(start, len(lines)) if lines[i].rstrip("\r\n") == END)
    except StopIteration:
        return None
    return "".join(lines[start : stop + 1])


def has_half_a_block(text: str) -> bool:
    lines = [line.rstrip("\r") for line in text.split("\n")]
    return (BEGIN in lines or END in lines) and block_in(text) is None


def add(text: str, new_block: str) -> str:
    """`text` with the block as the last entry under `services:`; every other byte kept.

    Inserted before the first top-level key that follows `services:` (the base
    file's `networks:`), or at the end if `services:` is the last one.
    """
    if block_in(text) is not None or has_half_a_block(text):
        raise DashboardError("docker-compose.yml already has a bot dashboard block in it")
    lines = text.splitlines(keepends=True)
    try:
        at = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == "services:")
    except StopIteration:
        raise DashboardError("docker-compose.yml has no `services:` section to add to") from None
    insert = len(lines)
    for index in range(at + 1, len(lines)):
        line = lines[index]
        if line[:1] not in ("", " ", "\t", "#", "\n", "\r"):
            insert = index
            break
    if insert == len(lines) and lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return "".join([*lines[:insert], new_block, *lines[insert:]])


def remove(text: str) -> str:
    """`text` without the block; unchanged when there is none."""
    found = block_in(text)
    return text if found is None else text.replace(found, "", 1)


def lan_of(found_block: str) -> bool:
    return f'"0.0.0.0:{HTTP_PORT}:' in found_block


def carry(rendered: str, on_disk: str | None) -> str:
    """A freshly rendered base file with the dashboard block the replaced file had.

    `composegen.render()` calls this, so every writer of the base file -- the
    install's generate-compose stage on a resume, and anything that re-renders
    to repair it -- keeps the switch the player set (T117's carry-over rule).
    """
    found = None if on_disk is None else block_in(on_disk)
    if found is None or block_in(rendered) is not None:
        return rendered
    try:
        return add(rendered, found)
    except DashboardError as exc:
        logger.warning(f"the bot dashboard block could not be carried over: {exc}")
        return rendered


def state(server_dir: Path) -> State:
    """Is the dashboard switched on in this install's files? Never raises."""
    path = base_path(server_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return State()
    except (OSError, UnicodeDecodeError) as exc:
        return State(problem=f"{path.name} could not be read: {exc}")
    found = block_in(text)
    if found is None:
        if has_half_a_block(text):
            return State(problem=f"{path.name} has half a bot dashboard block in it")
        return State()
    return State(on=True, lan=lan_of(found))


def is_on(server_dir: Path) -> bool:
    return state(server_dir).on


# -- the conf -----------------------------------------------------------------


def overlay(table: ConfPatchTable, entry: CatalogEntry, server_dir: Path) -> ConfPatchTable:
    """The catalog's conf table with the dashboard's keys laid over it while the switch is on.

    The CMaNGOS conf stage patches every key in the table on every run, and the
    table says `AiPlayerbot.Observability = 0`. Without this a resumed install
    would switch the module's telemetry off under a running dashboard.
    """
    name = conf_file(entry)
    if name is None or name not in table.files or not is_on(server_dir):
        return table
    patch = table.files[name]
    keys = {**patch.keys, **conf_keys(entry)}
    files = {**table.files, name: patch.model_copy(update={"keys": keys})}
    return table.model_copy(update={"files": files})


def conf_values(text: str, keys: tuple[str, ...]) -> dict[str, str | None]:
    """The value each key has at column 0 (the line the server reads), or None."""
    found: dict[str, str | None] = dict.fromkeys(keys)
    for line in text.splitlines():
        for key in keys:
            if found[key] is None and re.match(rf"^{re.escape(key)}\s*=", line):
                found[key] = line.split("=", 1)[1].strip()
    return found


def patch_text(text: str, keys: dict[str, str]) -> str:
    """`families.conf.patch()` over these keys: the install's own writer for this file."""
    from yulon.catalog.families import conf  # noqa: PLC0415 - families import the catalog

    return conf.patch(text, ConfPatch(keys=keys), tokens={})


def without_keys(text: str, keys: list[str]) -> str:
    kept = []
    for line in text.splitlines(keepends=True):
        if any(re.match(rf"^{re.escape(key)}\s*=", line) for key in keys):
            continue
        kept.append(line)
    return "".join(kept)


def write_keeping_mode(path: Path, text: str) -> None:
    """Replace `path` with `text` atomically, with the mode the file already had.

    A temp file created owner-only with O_EXCL, given the old file's mode, then
    renamed over it: a 0600 conf holding the database password stays 0600 (the
    T116 defect, a save that widened it to the umask's 0644, is not repeated
    here), and a half-written file never replaces a whole one.
    """
    mode = path.stat().st_mode & 0o777
    temp = path.with_name(f"{path.name}.yulon-dashboard-new")
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def read_exact(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def new_secret() -> str:
    return secrets.token_hex(32)


# -- the GM hint --------------------------------------------------------------


@dataclass(frozen=True)
class GmHint:
    """Which of the player's accounts can sign in to the dashboard."""

    names: tuple[str, ...]
    text: str


def gm_hint(accounts: object) -> GmHint:
    """The sentence shown before the dashboard opens, from the Accounts tab's own listing.

    `accounts` is `useraccounts.Listing`: the player's accounts, without the bots
    and without the app's own. Nothing is changed here, ever -- granting a rank
    is the Accounts tab's press, made by the player.
    """
    problem = getattr(accounts, "problem", "")
    if problem:
        return GmHint(
            (),
            f"The dashboard asks you to sign in with an account of GM rank {MIN_GM_LEVEL} or "
            f"higher. Yu'lon could not read this server's accounts to say which ({problem}).",
        )
    able = tuple(
        f"{a.username} (GM {a.gm_level})"
        for a in getattr(accounts, "accounts", [])
        if a.gm_level >= MIN_GM_LEVEL
    )
    if not able:
        return GmHint(
            (),
            f"None of your accounts has GM rank {MIN_GM_LEVEL} or higher, and the dashboard "
            f"only lets such an account sign in. On the Accounts tab, pick one of yours and use "
            f"Set GM level to give it {MIN_GM_LEVEL} or more.",
        )
    return GmHint(
        able,
        "Sign in to the dashboard with one of your accounts that has GM rank "
        f"{MIN_GM_LEVEL} or higher: {', '.join(able)}.",
    )
