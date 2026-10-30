"""Which keys of an AzerothCore server's own confs Yu'lon sets itself (T819).

The worldserver and the login server read these files when they start, so the
Tuning tab may show them -- but every row Yu'lon carries in the generated
compose override beats the file (`Config.cpp:540-552`), and a handful of other
keys are Yu'lon's by construction (the published ports, the realm row, the
console the Console tab types into). A Save that changes one of those keys is
refused before anything is written; everything else stays editable.

Pure and Qt-free: computed per open AND per save from the entry, so the list
cannot drift from what the install really carries.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from yulon import playerbots_keys, tuning
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger
from yulon.support import redact

logger = get_logger(__name__)

BASE_WORLD_ENV: tuple[str, ...] = (
    "AC_DATA_DIR",
    "AC_LOGS_DIR",
    "AC_LOGIN_DATABASE_INFO",
    "AC_WORLD_DATABASE_INFO",
    "AC_CHARACTER_DATABASE_INFO",
    "AC_PLAYERBOTS_DATABASE_INFO",
)
"""Every `AC_*` name the base template sets on the worldserver service.

Pinned by `test_every_ac_name_in_every_azerothcore_base_template_is_declared`
against every azerothcore base template: a name added to the template without
being added here silently unlocks a key Yu'lon sets.
"""

BASE_AUTH_ENV: tuple[str, ...] = (
    "AC_LOGS_DIR",
    "AC_TEMP_DIR",
    "AC_LOGIN_DATABASE_INFO",
)
"""Every `AC_*` name the base template sets on the authserver service (as above)."""

FIXED: Mapping[str, Mapping[str, str]] = {
    "worldserver.conf": {
        "WorldServerPort": (
            "Yu'lon publishes this server's port onto the world port in its container"
        ),
        "BindIP": "Yu'lon binds the server to every interface in its container",
        "SOAP.Port": "Yu'lon publishes the command channel port in its container",
        "SOAP.IP": "the Server tab's command channel switches this",
        "SOAP.Enabled": "the Server tab's command channel switches this",
        "Ra.Enable": "the remote console stays off",
        "Ra.IP": "the remote console stays off",
        "Ra.Port": "the remote console stays off",
        "RealmID": "Yu'lon sets this realm's port by this id",
        "Console.Enable": "the Console tab types into the world console",
        "DataDir": "Yu'lon mounts the game data where the container looks for it",
        "LogsDir": "Yu'lon keeps the logs beside the server folder",
        "TempDir": "Yu'lon keeps the temporary files beside the server folder",
    },
    "authserver.conf": {
        "RealmServerPort": "Yu'lon publishes the login port in its container",
        "BindIP": "Yu'lon binds the server to every interface in its container",
        "LogsDir": "Yu'lon keeps the logs beside the server folder",
        "TempDir": "Yu'lon keeps the temporary files beside the server folder",
    },
    "playerbots.conf": {},
}
"""Keys no edit may touch although no env row sets them, by file basename.

Reasons are player sentences: no backticks, no commands.
"""

KNOWN_CONF_KEYS: tuple[str, ...] = (
    "AiPlayerbot.MinRandomBots",
    "AiPlayerbot.MaxRandomBots",
    "AiPlayerbot.RandomBotAutologin",
    "Playerbots.MinRandomBots",
    "Playerbots.MaxRandomBots",
    "Playerbots.RandomBotAutologin",
    "Playerbots.Updates.EnableDatabases",
    "Updates.EnableDatabases",
    "SOAP.Enabled",
    "SOAP.IP",
    "AiPlayerbot.CommandServerPort",
    "Ra.Enable",
    "WorldServerPort",
    "RealmServerPort",
    "RealmID",
    "BindIP",
    "SOAP.Port",
    "Ra.IP",
    "Ra.Port",
    "Console.Enable",
    "DataDir",
    "LogsDir",
    "TempDir",
    "LoginDatabaseInfo",
    "WorldDatabaseInfo",
    "CharacterDatabaseInfo",
    "Playerbots.DatabaseInfo",
    "ValidateSkillLearnedBySpells",
    "Ale.Enabled",
    "Ale.ScriptPath",
    "Ale.AutoReload",
)
"""One conf key behind every pinned env name, so the pin test means something.

A name in `BASE_WORLD_ENV` or `BASE_AUTH_ENV` with no key spelling to it is a
row nothing in any conf could carry -- and so a lock that could never fire.
"""


def ENV_REASON(name: str) -> str:
    """Why a key an env row beats is locked: the row, and where it lives."""
    return f"the generated compose override sets {name} and the server reads that first"


SECRET_REASON = "it holds a password, which is never edited here"
"""Why a secret key is locked: the editor masks it, so no edit could mean what it says."""


def is_secret(key: str) -> bool:
    """Whether `key` carries a credential: a database login or a password setting."""
    folded = key.casefold()
    return key.endswith("DatabaseInfo") or key.endswith("Database.Info") or "password" in folded


def is_editable_core(entry: CatalogEntry) -> bool:
    """Whether this game owns editable core confs: the azerothcore family does."""
    native = entry.install.native
    return native is not None and native.family == "azerothcore"


def _world_env(entry: CatalogEntry, server_dir: Path) -> dict[str, str]:
    """The env rows that beat the worldserver side, in this install's own spellings."""
    operations = entry.operations
    enable = dict(operations.enable_env) if operations is not None else {}
    merged = {
        **composegen.world_env(entry),
        **enable,
        **{name: "" for name in BASE_WORLD_ENV},
    }
    return playerbots_keys.env_map(merged, playerbots_keys.module_prefix(server_dir))


def locked(entry: CatalogEntry, server_dir: Path, file: str, *texts: str) -> dict[str, str]:
    """Every key of this core conf no Save may change, and why (T819).

    Asked of the raw AND the edited text, so a key added by the edit is locked
    too. The worldserver side locks what the override carries; the authserver
    side only what reaches the login server; secrets and the fixed list lock on
    every file.
    """
    name = file.rsplit("/", 1)[-1]
    keys = {*FIXED.get(name, {})}
    for text in texts:
        keys.update(tuning.conf_keys(text))
    if name == "authserver.conf":
        env = {name_: "" for name_ in BASE_AUTH_ENV}
    else:
        env = _world_env(entry, server_dir)
    fixed = FIXED.get(name, {})
    found: dict[str, str] = {}
    for key in keys:
        env_name = composegen.env_name_for(key)
        if env_name in env:
            found[key] = ENV_REASON(env_name)
        elif key in fixed:
            found[key] = fixed[key]
        elif is_secret(key):
            found[key] = SECRET_REASON
    return found


_KEY_LINE = re.compile(r"^\s*([^#\[=\s][^=]*?)\s*=")


def _active_lines(text: str) -> list[str]:
    """The editor view's lines, without endings."""
    return text.split("\n")


def masked_view(raw: str) -> str:
    """The text the raw editor is given for a core conf whose exact text is `raw`.

    `tuning.editor_view()` with the secrets taken out: the password field of
    every active `*DatabaseInfo` line and the value of every `*password*` key
    become the mask. The diff dialog and every report show this text only.
    """
    shown = tuning.editor_view(raw)
    shown = redact.database_info_passwords_replaced(shown, redact.MASK)
    out: list[str] = []
    for line in _active_lines(shown):
        match = _KEY_LINE.match(line)
        if match is not None and "password" in match.group(1).casefold():
            head, sep, _tail = line.partition("=")
            out.append(head + sep + " " + redact.MASK if sep else line)
        else:
            out.append(line)
    return "\n".join(out)


def unmask(edited: str, raw: str) -> str:
    """`edited` with every untouched masked line put back to its original bytes.

    Lines are paired first-come, so duplicates survive: an edited line equal to
    a masked line takes back its original line, in order. A line whose mask was
    touched is left as typed, and the lock check then refuses it.
    """
    masked = masked_view(raw).split("\n")
    original = tuning.editor_view(raw).split("\n")
    typed = edited.split("\n")
    waiting: dict[str, list[str]] = {}
    for shown, source in zip(masked, original, strict=True):
        if shown != source:
            waiting.setdefault(shown, []).append(source)
    out: list[str] = []
    for line in typed:
        pool = waiting.get(line)
        out.append(pool.pop(0) if pool else line)
    return "\n".join(out)


def locked_edits(raw: str, edited: str, locked: Mapping[str, str]) -> tuple[str, ...]:
    """One entry per locked key whose value the core reads changed (T819).

    `tuning.conf_value()`'s own rule -- the first active line wins -- applied
    to both sides, so commenting a key out or adding one counts as changing it.
    """
    found: list[str] = []
    for key in locked:
        if tuning.conf_value(raw, key) != tuning.conf_value(edited, key):
            found.append(key)
    return tuple(found)


def changed_keys(raw: str, edited: str) -> tuple[tuple[str, str, str], ...]:
    """Every key whose read value changed, as `(key, old, new)` -- secrets masked.

    What the confirm dialog lists: at most the shape, never a password.
    """
    shown_raw = masked_view(raw)
    shown_edited = masked_view(edited)
    names: list[str] = []
    for text in (shown_raw, shown_edited):
        for key in tuning.conf_keys(text):
            if key not in names:
                names.append(key)
    found: list[tuple[str, str, str]] = []
    for key in names:
        old = tuning.conf_value(shown_raw, key)
        new = tuning.conf_value(shown_edited, key)
        if old != new:
            found.append((key, old if old is not None else "", new if new is not None else ""))
    return tuple(found)


def default_lines(dist_text: str) -> dict[str, str]:
    """The `.dist`'s first active line per key: what Default for this line offers."""
    found: dict[str, str] = {}
    for line in dist_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[:1] in ("#", "["):
            continue
        head, sep, _tail = stripped.partition("=")
        if sep and head.strip() and head.strip() not in found:
            found[head.strip()] = stripped
    return found
