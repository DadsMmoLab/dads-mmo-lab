"""My Party's server-side bridge: deploy it, then ask whether it arrived (8.6).

## The route, and why it needs a bridge at all

`.playerbots bot addclass <class>` builds the party. It is declared
`SEC_PLAYER, Console::No` (`mod-playerbots PlayerbotCommandScript.cpp:36`, read
on `yulon-ubuntu` 2026-09-08 at `b949b50b`), and AzerothCore filters its command
table by `IsInvokerVisible(handler)` before it walks it. So a console or SOAP
caller cannot see the subcommand, never mind run it — asked over SOAP that day,
`playerbots bot addclass mage` came back with a USAGE list naming only the four
`Console::Yes` siblings. It also resolves its master from a **live session**, so
there is no console spelling of it waiting to be found.

The route that remains is a Lua script inside the world process: ALE's
`PLAYER_EVENT_ON_COMMAND` hook hands a script every command string, including
the ones the command table refused, and `Player:RunCommand` runs the playerbot
command **as that player**, in the session it needs. That is the project's own
bridge, and the scripts under `pylauncher/lua/party/` are it.

## Why this module is mostly about proving the bridge is there

Deploying the scripts is a directory copy and it always works. Whether the
server ever READS them has four independent ways to be false, and on
2026-08-20 one of them was: the deploy reported success and every bridge command
answered *"Command does not exist"*. That failure is the reason this box exists,
so the shape of this module is: gather facts, say which precondition is unmet in
its own words, and prove arrival **by the script answering** rather than by the
copy succeeding.

The four ways, each with its own sentence in `preconditions()`:

1. **The Lua engine is not compiled into the image.** A conf file cannot see
   this and neither can a module marker — `modules/mod-ale` can be cloned and
   the running binary still have no Lua in it, because the module is C++ and
   arrives at a rebuild. Read from the binary (`read_engine_in_binary`).
2. **`ALE.Enabled` is off.** Its compiled default is `false`
   (`ALE src/LuaEngine/ALEConfig.cpp:20`) while the shipped conf's own comment
   says `true` — the code wins, so a conf that never sets the key runs no
   scripts at all.
3. **`ALE.ScriptPath` points elsewhere.** It ships as `"lua_scripts"`, relative,
   resolved against the worldserver's cwd where nothing is. This is the
   2026-08-20 bug by name (`rust-main:crates/dml-wow/src/bridge.rs:189-195`).
4. **The world has not restarted since the copy.** ALE reads the directory when
   the engine starts, which is why `deploy()` reports `changed`.

## What this module deliberately does not do

It does not write `mod_ale.conf`. The Rust launcher's `ensure_ale_conf` did, and
it was right for a launcher whose installer created that file; here the file
belongs to `mod-ale.json`, and two writers with different ideas of a working
conf is exactly the drift `style-guide` §4 forbids. The manifest was corrected
instead — it named `ALE.EnableLuaEngine`, a key that appears in neither the
module's `conf/mod_ale.conf.dist` nor its `ALEConfig.cpp`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon import dbreads, platform, play, resources, runner
from yulon.actions import Outcome
from yulon.catalog.catalog import CatalogEntry
from yulon.channel import Answer
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

BRIDGE_SCRIPTS = (
    "dml_addclass.lua",
    "dml_botadd.lua",
    "dml_bridge_ping.lua",
    "dml_login.lua",
    "dml_uninvite.lua",
    "dml_whisper.lua",
)
"""Every script the bridge is made of. All of them, or the bridge is partial."""

PROBE_COMMAND = "dml_bridge_ping"
PROBE_TOKEN = "DML-BRIDGE-READY"
"""The word only `dml_bridge_ping.lua` says. See its header for why not "ok"."""

ALE_SCRIPT_PATH = "/azerothcore/env/dist/etc/modules/lua_scripts"
"""Where ALE must be told to look: the container-visible path of the same
directory `dest_dir()` writes on the host. A constant of the compose layout."""

ALE_CONF = "env/dist/etc/modules/mod_ale.conf"

WORLDSERVER_BINARY = "/azerothcore/env/dist/bin/worldserver"

ALE_MARKER = "ALE.ScriptPath"
"""A string the Lua engine puts in the binary and nothing else does."""

CONTROL_MARKER = "AiPlayerbot.Enabled"
"""A string the binary MUST have, so a zero for `ALE_MARKER` can be told apart
from a reading that never happened. Both were measured on `yulon-ubuntu`
2026-09-08: `ALE.ScriptPath` 0, `AiPlayerbot.Enabled` 1."""


class NothingToDeploy(RuntimeError):
    """The deploy source held no scripts. Never reported as a successful no-op."""


@dataclass(frozen=True)
class Deployment:
    """What one deploy did. `changed` is what owes the user a world restart."""

    changed: bool
    names: tuple[str, ...]


def lua_root() -> Path:
    """The bundled deploy source: one subdirectory per bridge family."""
    return resources.lua_dir()


def dest_dir(server_dir: Path) -> Path:
    """The host side of the directory the container mounts at `ALE_SCRIPT_PATH`."""
    return server_dir / "env" / "dist" / "etc" / "modules" / "lua_scripts"


def deploy(root: Path, dest: Path) -> Deployment:
    """Copy every family's `*.lua` under `root` into the flat `dest`.

    Flat because ALE scans one directory. Content-compared rather than
    timestamped, so a redeploy of identical bytes reports `changed=False` and
    does not ask for a world restart nobody needs.

    **Finding nothing raises.** The Rust launcher shipped the other behaviour
    and documented the cost in its own source: with the source root wrong it
    emitted a success envelope for a no-op, so "Enable My Party" appeared to
    work and My Party simply did not function, with nothing pointing at the
    cause (`rust-main:crates/dml-wow/src/bridge.rs:56-70`). Both ways of finding
    nothing raise — an unreadable root, and a root holding no `.lua` at all.
    """
    dest.mkdir(parents=True, exist_ok=True)
    try:
        families = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as exc:
        raise NothingToDeploy(_nothing(root)) from exc
    copied: list[str] = []
    changed = False
    for family in families:
        for src in sorted(family.glob("*.lua")):
            target = dest / src.name
            wanted = src.read_bytes()
            if not target.is_file() or target.read_bytes() != wanted:
                shutil.copy2(src, target)
                changed = True
            copied.append(src.name)
    if not copied:
        raise NothingToDeploy(_nothing(root))
    logger.info(f"deployed {len(copied)} bridge scripts to {dest} (changed={changed})")
    # Sorted by NAME, not by the family walk that produced them: the flat
    # directory is what the engine sees, and a report ordered by a source
    # layout the destination does not have would move a script the day a
    # family is renamed.
    return Deployment(changed, tuple(sorted(copied)))


def _nothing(root: Path) -> str:
    return (
        f"no bridge scripts were found under {root}. Nothing was deployed, so the "
        "bridge is not there — this is reported rather than passed over, because a "
        "deploy that copies nothing and says it succeeded is how My Party last failed."
    )


# -- reading the binary ----------------------------------------------------


@dataclass(frozen=True)
class BinaryRead:
    """Whether the Lua engine is in the running server's own executable.

    `engine` is `None` for "could not be read", which is a third answer and not
    a quiet `False`: a wrong path, a stripped binary or a container that would
    not take an `exec` all look exactly like an absent engine to a bare grep.
    """

    engine: bool | None
    sentence: str


def read_engine_in_binary(
    container: str,
    *,
    binary: str = WORLDSERVER_BINARY,
    wsl_distro: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = runner.run,
) -> BinaryRead:
    """Ask the image itself, because no conf file knows the answer.

    Owner instruction, 2026-09-08: *"Read the binary, not a conf file."* The two
    disagree in the case that matters — `mod-ale` cloned into `modules/` and the
    running worldserver still built without it, which is a rebuild away and not
    a config away.

    Two strings are counted in one pass. `ALE_MARKER` is the question;
    `CONTROL_MARKER` is a string the binary must have, and a zero there means
    the reading is worthless rather than negative.
    """
    prefix = platform.docker_prefix(wsl_distro)
    if prefix is None:
        return BinaryRead(None, "the Lua engine could not be read: there is no docker command here")
    script = (
        f'printf "ALE %s\\n" "$(grep -c -a -F "{ALE_MARKER}" "{binary}" || true)"; '
        f'printf "CONTROL %s\\n" "$(grep -c -a -F "{CONTROL_MARKER}" "{binary}" || true)"'
    )
    argv = [*prefix, "exec", container, "sh", "-c", script]
    try:
        proc = run(argv, timeout=60.0)
    except OSError as exc:
        logger.warning(f"could not read {binary} in {container}: {exc}")
        return BinaryRead(None, _unreadable("docker could not be started"))
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        why = detail[-1] if detail else f"exit {proc.returncode}"
        return BinaryRead(None, _unreadable(why))
    counts = _counts(proc.stdout)
    if counts.get("CONTROL", 0) < 1:
        return BinaryRead(
            None,
            _unreadable(
                f"the reading found no {CONTROL_MARKER} either, and every build of this "
                "server has that — so what was read was not this server's executable"
            ),
        )
    if counts.get("ALE", 0) < 1:
        return BinaryRead(
            False,
            "the Lua engine is not built into this server's executable. It arrives at the "
            "next rebuild; installing the module alone does not put it there.",
        )
    return BinaryRead(True, "the Lua engine is built into this server's executable.")


def _unreadable(why: str) -> str:
    return f"the Lua engine could not be read out of this server's executable: {why}"


def _counts(stdout: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in stdout.splitlines():
        name, _, value = line.strip().partition(" ")
        try:
            out[name] = int(value.strip())
        except ValueError:
            continue
    return out


# -- the preconditions -----------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """Everything the sentences are decided from, read once and passed in.

    A dataclass rather than nine arguments so the ordering logic can be tested
    without a server, and so a fact nobody could read arrives as `None` instead
    of being flattened into a `False` that reads as an accusation.
    """

    server_installed: bool
    world_running: bool
    engine_cloned: bool
    engine_in_binary: bool | None
    conf_present: bool
    engine_enabled: bool | None
    script_path: str | None
    deployed: tuple[str, ...]
    bridge_answered: bool | None


@dataclass(frozen=True)
class Precondition:
    """One thing that must hold, and the sentence said when it does not."""

    name: str
    met: bool
    sentence: str


def preconditions(facts: Facts) -> tuple[Precondition, ...]:
    """Every precondition in the order a person would have to fix them.

    The order is the point. With the server absent everything below it is
    unreadable too, and naming the deployed scripts there would send somebody to
    a folder that cannot exist. `blocker()` reads the first unmet one.
    """
    return (
        Precondition(
            "server_installed",
            facts.server_installed,
            "this game is not installed yet, so there is no server folder to put the bridge in.",
        ),
        Precondition(
            "world_running",
            facts.world_running,
            "the world server is not running, so nothing can be asked of it and no bot can "
            "join a party.",
        ),
        Precondition(
            "engine_cloned",
            facts.engine_cloned,
            "the Lua engine (ALE) is not installed. Install it on the Modules page — My Party "
            "runs through it and there is no other route to it on this server.",
        ),
        Precondition(
            "engine_in_binary",
            facts.engine_in_binary is True,
            _binary_sentence(facts.engine_in_binary),
        ),
        Precondition(
            "conf_present",
            facts.conf_present,
            f"the Lua engine has no mod_ale.conf in {ALE_CONF}, so it is running on its "
            "compiled defaults — and the compiled default for ALE.Enabled is off.",
        ),
        Precondition(
            "engine_enabled",
            facts.engine_enabled is True,
            _enabled_sentence(facts.engine_enabled),
        ),
        Precondition(
            "script_path",
            facts.script_path == ALE_SCRIPT_PATH,
            _script_path_sentence(facts.script_path),
        ),
        Precondition(
            "deployed",
            # Every one of OURS is there — not "these files and no others".
            # `mod-ale` installs its own example `LootPet.lua` into the same
            # directory (measured on `yulon-ubuntu2` 2026-09-09, on the only
            # install where the engine has ever run), and set equality made a
            # complete bridge report as a partial one naming nothing missing.
            set(BRIDGE_SCRIPTS) <= set(facts.deployed),
            _deployed_sentence(facts.deployed),
        ),
        Precondition(
            "bridge_answered",
            facts.bridge_answered is True,
            _answered_sentence(facts.bridge_answered),
        ),
    )


def _binary_sentence(engine: bool | None) -> str:
    if engine is None:
        return (
            "whether the Lua engine is built into this server could not be read from its own "
            "executable, so My Party is not offered — the answer is unknown, not no."
        )
    # NOT "the Lua engine is installed but not built in". Every precondition
    # below the first unmet one is still shown, and that wording states as fact
    # something the precondition above it may have just denied -- printed on
    # `yulon-ubuntu` on 2026-09-08 over an install where mod-ale was not cloned
    # at all. A sentence describes its own fact and never somebody else's.
    return (
        "the Lua engine is not built into the server that is running. It is C++, so it arrives "
        "at the next rebuild of this game's image and not at the next restart."
    )


def _enabled_sentence(enabled: bool | None) -> str:
    if enabled is None:
        return (
            "the value of ALE.Enabled could not be read from mod_ale.conf, so whether the Lua "
            "engine will run any script is unknown."
        )
    return (
        "the Lua engine is switched off: ALE.Enabled is not set to 1 in mod_ale.conf. Its "
        "compiled default is off, whatever the comment beside it in the shipped file says."
    )


def _script_path_sentence(path: str | None) -> str:
    if path is None:
        return "ALE.ScriptPath could not be read from mod_ale.conf."
    if not path.startswith("/"):
        return (
            f"the Lua engine is looking for scripts in {path!r}, a relative path it resolves "
            "against the server's own working directory, where the bridge is not. It must be "
            f"{ALE_SCRIPT_PATH}."
        )
    return (
        f"the Lua engine is looking for scripts in {path!r}, which is not where the bridge was "
        f"deployed. It must be {ALE_SCRIPT_PATH}."
    )


def _deployed_sentence(deployed: tuple[str, ...]) -> str:
    missing = sorted(set(BRIDGE_SCRIPTS) - set(deployed))
    if len(missing) == len(BRIDGE_SCRIPTS):
        return "the bridge scripts are not in the server folder yet."
    return (
        "some of the bridge scripts are missing from the server folder "
        f"({', '.join(missing)}), so part of My Party would answer and part would not."
    )


def _answered_sentence(answered: bool | None) -> str:
    if answered is None:
        return (
            "the server could not be asked whether the bridge is there, so My Party is not "
            "offered — nothing here says it is missing."
        )
    # Same correction as `_binary_sentence`, same morning: this used to open
    # with "The scripts are in place", which the precondition directly above it
    # had just said they were not.
    return (
        "the bridge does not answer: this server does not know the bridge's own commands. If "
        "everything above this line is met, the world server has not read the scripts yet — "
        "restart it, and if it still does not answer, the Lua engine is not reading that "
        "directory."
    )


def blocker(facts: Facts) -> str | None:
    """The first unmet precondition's sentence, or `None` when all are met."""
    for check in preconditions(facts):
        if not check.met:
            return check.sentence
    return None


def ready(facts: Facts) -> bool:
    """True only when every precondition is met. Never a default."""
    return all(check.met for check in preconditions(facts))


# -- reading the facts off one install -------------------------------------


@dataclass(frozen=True)
class ConfRead:
    """`mod_ale.conf` as it is on disk, or the absence of it.

    `enabled` and `script_path` are `None` when the file is not there at all:
    a file nobody wrote has said nothing about the engine, and reporting that
    as "switched off" sends a person to edit a key that does not exist. The
    `conf_present` precondition above them is what names the real fix.
    """

    present: bool
    enabled: bool | None
    script_path: str | None


def read_conf(path: Path) -> ConfRead:
    """Read both of ALE's keys out of one conf file, column 0 only.

    Column 0 is `conf.patch()`'s rule and it is here for the reason that rule
    exists: the shipped `mod_ale.conf.dist` carries a commented `ALE.Enabled =
    true` beside a compiled default of `false` (`ALEConfig.cpp:20`), so a
    pattern that matches indented or commented lines reads the file's own
    prose as its settings and reports an engine that is off as on.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ConfRead(False, None, None)
    enabled = _conf_value(text, "ALE.Enabled")
    script = _conf_value(text, "ALE.ScriptPath")
    return ConfRead(True, None if enabled is None else enabled.strip() == "1", script)


def _conf_value(text: str, key: str) -> str | None:
    """The LAST active setting of `key`, unquoted, or `None`.

    The last rather than the first: a conf file read top to bottom by the
    server takes the last assignment, and an applier that appends a corrected
    key leaves the old one above it.
    """
    found: str | None = None
    for line in text.splitlines():
        head, sep, tail = line.partition("=")
        if sep and head.strip() == key and head[:1] not in ("#", " ", "\t"):
            found = tail.strip().strip('"')
    return found


def read_facts(
    server_dir: Path,
    *,
    world_running: bool,
    engine: BinaryRead,
    probe: Probe,
) -> Facts:
    """Every fact the sentences are decided from, read once off one install.

    The two answers this cannot get from the disk are passed in, because both
    cost a subprocess and the caller is what knows whether it may spend one:
    `engine` comes from `read_engine_in_binary` and `probe` from asking the
    SERVER `dml_bridge_ping`. Asking the server is not decoration — the deploy
    reporting success is precisely what was believed on 2026-08-20, and the
    scripts were on disk that day too.
    """
    installed = server_dir.is_dir()
    conf = read_conf(server_dir / ALE_CONF)
    try:
        deployed = tuple(sorted(p.name for p in dest_dir(server_dir).glob("*.lua")))
    except OSError:
        deployed = ()
    return Facts(
        server_installed=installed,
        world_running=world_running,
        engine_cloned=(server_dir / "modules" / "mod-ale").is_dir(),
        engine_in_binary=engine.engine,
        conf_present=conf.present,
        engine_enabled=conf.enabled,
        script_path=conf.script_path,
        deployed=deployed,
        bridge_answered=probe.arrived,
    )


# -- the proof -------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """What the server said when asked whether the bridge is there.

    `arrived` is three-valued for the reason every answer in this phase is: a
    server that could not be reached has said nothing about its bridge, and
    reporting that as "the bridge is missing" sends somebody to reinstall a
    module over a server that is simply down.
    """

    arrived: bool | None
    sentence: str
    text: str


def read_probe(answer: Answer) -> Probe:
    """Classify one reply to `PROBE_COMMAND`.

    The discriminator was measured on `yulon-ubuntu` on 2026-09-08 and is a
    clean flip rather than a text match on a hopeful substring. With no bridge,
    `_ParseCommands` falls through to `SendErrorMessage(LANG_CMD_INVALID)`
    (`AC Chat.cpp:245`), which sets the error flag, and `ACSoap` turns that into
    a `<faultstring>`: *Command 'dml_bridge_ping' does not exist*. With the
    bridge, the hook returns false, `TryExecuteCommand` reports the command
    handled, no error flag is set, and the same buffer comes back as a
    `<result>` (`ACSoap.cpp:133-140`) — carrying whatever
    `handler:SendSysMessage` put in it.

    So a `yes` is necessary and not sufficient: it must carry the script's own
    word. A deploy reporting success is what fooled 2026-08-20, and so would
    any other command on the server answering to this name.
    """
    if answer.outcome == "unknown":
        why = answer.reason or "the server did not answer"
        return Probe(None, f"the bridge could not be asked about: {why}", answer.text)
    if answer.outcome == "no":
        if "does not exist" in answer.text:
            return Probe(
                False,
                f"the server does not know {PROBE_COMMAND}, so the bridge has not arrived. "
                "The scripts may be on disk; nothing has read them.",
                answer.text,
            )
        return Probe(
            False,
            f"the server refused {PROBE_COMMAND} in its own words: {answer.text.strip()}",
            answer.text,
        )
    if PROBE_TOKEN not in answer.text:
        return Probe(
            False,
            f"the server ran {PROBE_COMMAND} and did not answer with the bridge's own word "
            f"({PROBE_TOKEN}), so what answered was not this bridge.",
            answer.text,
        )
    return Probe(True, "the bridge answered, in its own words.", answer.text)


# -- the party itself ------------------------------------------------------
#
# Everything above proves the bridge is there. This is what the bridge is FOR:
# a bot of a chosen class in the party frame, geared and specced, and gone
# again on request.
#
# The command strings are `rust-main`'s, cited at their lines, because the Lua
# on the other side of the wire is the same Lua and a second spelling of
# `dml_addclass` would be a second protocol. The class LIST is not inherited:
# `dk` is a tenth class this tree's `addclass` accepts (`mod-playerbots
# PlayerbotMgr.cpp:1089`, read on `yulon-ubuntu` 2026-09-08, gated on the
# master reaching the heroic start level at `:1156`), and the bash launcher's
# `_valid_bot_class` deliberately excluded it. That was its tree's fact.


class BadRequest(ValueError):
    """A class or a name that must never reach the channel.

    Raised rather than returned, and raised BEFORE anything is sent: these
    strings are interpolated into a command line the world server parses, so
    the check is a boundary and not a courtesy.
    """


BOT_CLASS_IDS = {
    "warrior": 1,
    "paladin": 2,
    "hunter": 3,
    "rogue": 4,
    "priest": 5,
    "shaman": 7,
    "mage": 8,
    "warlock": 9,
    "druid": 11,
    "dk": 6,
}
"""Every class this tree's `addclass` takes, and the id the module gives it.

Both halves come from the same forty lines: `mod-playerbots
PlayerbotMgr.cpp:1093-1134`, read on `yulon-ubuntu2` on 2026-09-09. The ids are
not decoration — the premade specs are keyed by class NUMBER
(`AiPlayerbot.PremadeSpecName.<class>.<specno>`), so a spec picker has to turn
the word a person chose into the module's own number, and there is exactly one
place that mapping may live. See the note above for `dk`, which is 6 and which
the bash launcher's list deliberately excluded."""

BOT_CLASSES = tuple(BOT_CLASS_IDS)
"""The classes THIS tree's `addclass` accepts, derived rather than written twice.

A second tuple beside the mapping is a tuple that will one day offer a class the
specs are not keyed by, which is a picker offering a class whose spec list is
silently empty."""

PLAYERBOTS_CONF = "env/dist/etc/modules/playerbots.conf"
"""The module conf the premade spec names are read out of. The DEPLOYED file only.

**Measured on `yulon-ubuntu2`, 2026-09-09, and it cost this feature a live gate.**
The first version of this read fell back to the shipped `playerbots.conf.dist`
when no conf was deployed, on the prior art's example
(`rust-main:cli/src/50-party.sh:137-145`) and on a sentence written here that
nobody had checked: *"and so does the server"*. The server does not. That box has
a `.dist` and no `playerbots.conf`, and asked through the bridge what specs it
had, the module answered:

    To [Michaelah]: talents spec list
    [Michaelah] whispers: Total 0 specs found

Zero. `sConfigMgr` loads the deployed file; a `.dist` beside it is a template on
disk and not configuration. So the picker offered 63 names read out of a file the
running server had never read, and every one of them came back
`Spec <name> not found` — in the game window, where nothing this app can hear
would ever have said so. A `.dist` is not a fallback here: with no deployed conf
this install HAS no premade specs, `specs()` answers `()`, and `_spec_refusal`
says exactly that."""

SPEC_NAME_KEY = "AiPlayerbot.PremadeSpecName."

WORLD_CONF = "env/dist/etc/worldserver.conf"
MAX_LEVEL_KEY = "MaxPlayerLevel"
"""Where this install's own level cap is, and the key that holds it.

`MaxPlayerLevel = 80` on `yulon-ubuntu2` (`worldserver.conf:2128`, 2026-09-09).
Eighty is nowhere in this app: it is one conf value on one install of one tree,
and a constant would silently mis-bound the fork that ships 60 or 255."""

SPEC_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
"""What may go in the tail of `dml_whisper <master> <bot> talents spec <name>`.

The prior art's charset (`50-party.sh:208-215`) and deliberately WIDER than the
shipped names' lowercase-and-spaces: `playerbots.conf` is hand-editable, the
picker offers whatever it says, and refusing `Arms PvE` here would refuse a name
the module accepts. What it must never admit is a quote, a backslash, a newline
or anything else the world server's parser would read as a second command."""

MAX_NAME = 12
"""A WoW character name's own limit. `characters.name` is `varchar(12)`."""

POLL_TRIES = 12
POLL_SLEEP = 0.5
"""The poll window a bot has to appear in: 12 reads half a second apart, six
seconds. The prior art's numbers (`rust-main:crates/dml-wow/src/party.rs:321`
and `:327`, both env-overridable there) rather than a guess of our own; what
this tree actually took is recorded in 8.6's gate folder beside the press."""

SAVE_COMMAND = "saveall"
"""The server's own "write every online character's row now", and the reason
the level step needs one at all.

**T16, measured on `yulon-ubuntu2` 2026-09-10**
(`.notes/gates/8.6-level-holds-yulon-ubuntu2-2026-09-10/`). `.character level`
on an ONLINE character calls `Player::GiveLevel` and writes no row at all — the
row is written only on the offline arm
(`src/server/scripts/Commands/cs_character.cpp:252-281`, read on the box at
AzerothCore `413bea61a85e`, captured in `01b-source.log`). So an online
character's `characters.level` keeps whatever its last save wrote, and the next
save of its own accord is up to `PlayerSaveInterval`, 900 000 ms on this install
(`01b-source.log`, SOURCE D). That is the whole of what T5 photographed: the
level was accepted, the world held it, and the row had not been written.

`saveall` is `ObjectAccessor::SaveAllPlayers` (`cs_misc.cpp:1402-1407` ->
`ObjectAccessor.cpp:286-293`), which walks every `Player` in the world — bots
included, since a playerbot is a `Player` in that map even though it holds no
session — and calls `SaveToDB` on each. It is what the world does to itself
every quarter of an hour; asking for it early is the only route measured to make
`characters.level` answerable inside one press."""

LEVEL_TRIES = 30
LEVEL_SLEEP = 1.0
"""How long the row gets to catch up after a save, and why it is not the join
poll's six seconds.

Measured across all seven trials in that folder, and MEASURED rather than derived
— the driver stamped each one: a `saveall` on this install writes 500 online
characters, and the row the trial was watching first agreed **1.4 to 8.2 seconds
after the `saveall` came back** (`trial-5.txt` at the fast end, `fill-1.txt` at
the slow one). Thirty reads a second apart is that with room for a world under
more load.

It is a CEILING and not a wait: the poll returns the moment the row agrees, and
on the second press the whole step — send, save, read — took 3.2 seconds and then
4.4. **The step's own worst case is two of these ceilings**, about sixty seconds
plus the two sends, because a written row that disagrees earns a second send and
a second save; that path was never measured live and is pinned only in the unit
tests."""


MASTER = "character's"
BOT = "bot's"
"""What a rejected name is called in its own refusal, so "Invalid bot name" is
not said about the master. The prior art keeps two error builders for the same
reason (`rust-main:.../party.rs:138,143`)."""

UNINVITE_COMMAND_PATTERN = "^dml_uninvite%s+(%S+)%s+(%S+)$"
"""The Lua pattern `dml_uninvite.lua` parses its own command with, pinned here
so the wire grammar `uninvite_command` builds for exists in exactly one place
that can drift (T13, round 1's must-fix 3 — `test_the_probe_command_is_the_
one_the_shipped_script_registers` is the pin this copies)."""

UNINVITE_REFUSAL_FORMAT = "%s is not in %s's party now"
"""The base of `dml_uninvite.lua`'s refusal message — `bot`, then `player` —
common to every branch that does not remove the bot; each appends its own
`(<reason>)` after it. Pinned beside `UNINVITE_COMMAND_PATTERN` for the same
reason."""

BOTADD_COMMAND_PATTERN = "^dml_botadd%s+(%S+)%s+(%S+)$"
"""The Lua pattern `dml_botadd.lua` parses its own command with (T26).

Pinned here for `UNINVITE_COMMAND_PATTERN`'s reason: the wire grammar
`botadd_command` builds for exists in exactly one place that can drift."""

BOTADD_REFUSAL_FORMAT = "%s was not added to %s's party"
"""`dml_botadd.lua`'s refusal — the character, the master, then the reason.

One marker for every branch that does not issue the command (the master not in
the world; the character logged in), each carrying its own reason after it, the
shape T13 chose for `dml_uninvite.lua`. `add_named()` recognises the marker and
relays the script's own words; it does not enumerate the branches again on this
side of the wire."""

BOTADD_ISSUED_FORMAT = "%s asked the server to add %s"
"""What `dml_botadd.lua` says when it DID run the command — master, character.

Not "added". `.playerbots bot add` reports what it did through the master's own
session (`PlayerbotMgr.cpp:889-892`, `PSendSysMessage`), which for a
`RunCommand` caller is that player's game window and never the SOAP `<result>`
-- measured on `yulon-ubuntu2` 2026-09-10, `8.6-altbot-measure-…/README.md` §10.
So this sentence is the relay saying what IT did, and whether a bot arrived is
the group table's answer, polled by `add_named()`."""


def valid_name(name: str) -> bool:
    """A character name, and nothing that is also a command separator."""
    return 1 <= len(name) <= MAX_NAME and name.isalpha()


def _check_name(name: str, what: str) -> str:
    if not valid_name(name):
        raise BadRequest(
            f"{name!r} is not a {what} name: a character's name is 1 to {MAX_NAME} letters, and "
            "this one goes into a command the world server parses."
        )
    return name


def add_command(player: str, klass: str, *, gender: str = "") -> str:
    """`dml_addclass <player> <class> [gender]`
    (`rust-main:crates/dml-wow/src/party.rs:163`).

    NOT `.playerbots bot addclass`. That one is `SEC_PLAYER, Console::No` and a
    console or SOAP caller cannot even see it — sending it over this channel is
    the 2026-08-20 failure spelled out by hand. The bridge exists to run it
    inside the master's own session; see this module's header.
    """
    _check_name(player, MASTER)
    if klass.strip().lower() not in BOT_CLASSES:
        raise BadRequest(
            f"{klass!r} is not a class this server's addclass accepts. It has: "
            f"{', '.join(BOT_CLASSES)}."
        )
    if gender not in ("", "male", "female"):
        raise BadRequest(f"{gender!r} is not a gender: it is male, female, or left out.")
    tail = f" {gender}" if gender else ""
    return f"dml_addclass {player} {klass.strip().lower()}{tail}"


def uninvite_command(player: str, bot: str) -> str:
    """`dml_uninvite <player> <bot>`.

    T13 (T5's round-3 Codex review): wider than `rust-main:.../party.rs:184`'s
    `dml_uninvite <bot>` — that tree's own bridge script never checked who it
    removed a bot FROM, and the window that costs is real: `remove_all`
    re-reads the group table and refuses unless the fresh guid set is exactly
    what was confirmed, but the interval between that read and this whisper
    landing is one the bot manager's own timers can still move a bot across,
    into a party nobody confirmed. `player` travels here now so
    `dml_uninvite.lua` can check it at the moment it acts — the only place
    left that can still see the group when the whisper lands.
    """
    return f"dml_uninvite {_check_name(player, MASTER)} {_check_name(bot, BOT)}"


def _uninvite_refusal_marker(player: str, bot: str) -> str:
    """The base of the words `dml_uninvite.lua` answers with on EVERY branch
    that does not remove `bot` (T13, round 1's must-fix 2): `player` not
    found or offline, `bot` not found or offline, `bot` ungrouped, or `bot`
    grouped with someone else. One marker for all four, each carrying its own
    `(<reason>)` after it — `dismiss()` only needs to recognise the marker,
    not enumerate the branches a second time on this side of the wire.

    Read out of `answer.text`, not `answer.outcome`: this bridge's hook never
    sets the core's error flag, on a refusal any more than on a success, so
    `SoapChannel` reports "yes" either way — the same reason `read_probe`
    reads `PROBE_TOKEN` out of a reply that is also always "yes" on its own
    transport layer, rather than trusting the flag.
    """
    return UNINVITE_REFUSAL_FORMAT % (bot, player)


def logout_command(player: str, bot: str) -> str:
    """The logout whisper that follows an uninvite (`.../party.rs:192`).

    Best-effort at every call site in the prior art and here: an uninvited bot
    has already left the party, and a whisper that does not land leaves it
    standing in the world rather than leaving it in the group.
    """
    return f"{_whisper(player, bot)} logout"


def autogear_command(player: str, bot: str) -> str:
    """`.../party.rs:249`. "Geared" is this whisper, not `addclass`."""
    return f"{_whisper(player, bot)} autogear"


def talents_command(player: str, bot: str) -> str:
    """`.../party.rs:246`. "Specced" is this whisper, not `addclass`."""
    return f"{_whisper(player, bot)} talents autopick"


def valid_spec(spec: str) -> bool:
    """A premade spec name that cannot break out of the whisper it goes into."""
    return bool(SPEC_SHAPE.match(spec))


def spec_command(player: str, bot: str, spec: str) -> str:
    """`dml_whisper <master> <bot> talents spec <name>`
    (`rust-main:crates/dml-wow/src/party.rs:253`, `cli/src/90-main.sh:3976`).

    The whisper `talents autopick` REPLACES rather than joins. Measured in the
    module (`ChangeTalentsAction.cpp:57-59` and `:146`, read on `yulon-ubuntu2`
    2026-09-09): `autopick` runs `InitTalentsTree(true)` and a chosen spec runs
    `InitTalentsBySpecNo`, so sending both leaves the bot with whichever went
    last. `add_bot` sends one or the other and never the pair.
    """
    if not valid_spec(spec):
        raise BadRequest(
            f"{spec!r} is not a premade spec name: it goes into the tail of a command the world "
            "server parses, so it may hold only letters, digits, spaces and . _ -"
        )
    return f"{_whisper(player, bot)} talents spec {spec}"


def read_spec_names(text: str) -> dict[int, tuple[str, ...]]:
    """Every premade spec this conf defines, per class id, in the module's order.

    Two measured rules, both from `mod-playerbots` on `yulon-ubuntu2` 2026-09-09,
    and neither of them obvious from the file alone:

    * **The conf IS the accepted list.** `PlayerbotAIConfig.cpp:487-493` loads
      `AiPlayerbot.PremadeSpecName.<class>.<specno>` and `SpecPick` compares the
      whispered text to those values with `==`
      (`ChangeTalentsAction.cpp:144`). A list kept by hand in this app could only
      ever drift from it, and the drift is invisible: a name the module does not
      have is answered `Spec <x> not found` in the GAME window
      (`:157`, through `TellMasterNoFacing`), so nothing comes back over the
      channel and a wrong spec looks exactly like a right one.
    * **A gap ends the class.** `SpecPick` walks specno upwards from 0 and
      **breaks** at the first empty name (`:138-142`), so a conf that defines
      8.0, 8.1 and 8.3 has an unreachable 8.3 whatever it says. Only the
      contiguous run from 0 is returned — offering the rest would be offering
      names this server cannot be made to take.

    Column 0 only, which is `read_conf`'s rule for `read_conf`'s reason: the
    shipped file carries commented keys and a pattern that matched them would
    read the file's prose as its settings.

    **A value is taken verbatim to the end of the line, `#` included**, and
    whether the core's own reader would drop a trailing comment is NOT measured
    on this tree — so `… = arcane pve # the good one` is read here as the whole
    string, which is very probably not what the module has. What that costs is
    bounded, and it is bounded the safe way round: `#` is outside `SPEC_SHAPE`,
    so `InstallParty.specs` filters such a name out and the picker never offers
    it. The result of the unmeasured case is a spec missing from the list, not a
    spec offered that the server would refuse in the game window where nothing
    can hear it. Measuring `sConfigMgr`'s comment handling is what would let this
    read the value the module actually holds.
    """
    seen: dict[int, dict[int, str]] = {}
    for line in text.splitlines():
        head, sep, tail = line.partition("=")
        if not sep or head[:1] in ("#", " ", "\t"):
            continue
        key = head.strip()
        if not key.startswith(SPEC_NAME_KEY):
            continue
        parts = key[len(SPEC_NAME_KEY) :].split(".")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            continue
        value = tail.strip().strip('"')
        if value:
            seen.setdefault(int(parts[0]), {})[int(parts[1])] = value
    out: dict[int, tuple[str, ...]] = {}
    for klass, numbered in seen.items():
        run: list[str] = []
        number = 0
        while number in numbered:
            run.append(numbered[number])
            number += 1
        out[klass] = tuple(run)
    return out


def spec_names(server_dir: Path) -> dict[int, tuple[str, ...]]:
    """`read_spec_names` over the conf this install DEPLOYED, and no other.

    See `PLAYERBOTS_CONF` for what reading the `.dist` instead cost: a picker
    full of names the running server had never loaded, refused one at a time in
    a chat window nothing here can read.
    """
    return read_spec_names(_conf_text(server_dir / PLAYERBOTS_CONF))


def max_player_level(server_dir: Path) -> int | None:
    """This install's own `MaxPlayerLevel`, or `None` for "nobody could read it".

    `None` rather than the compiled default, and that is a deliberate refusal
    rather than an omission: AzerothCore's built-in cap has not been measured on
    this tree, and answering 80 for a conf that never sets the key would be this
    app inventing a fact about somebody's fork — the same mistake `ALE.Enabled`'s
    shipped comment makes about ITS compiled default. A cap nobody could read
    means no level is offered, and the sentence says which key to set.
    """
    text = _conf_text(server_dir / WORLD_CONF)
    value = _conf_value(text, MAX_LEVEL_KEY)
    return int(value) if value is not None and value.isdigit() else None


def _conf_text(path: Path) -> str:
    """A conf file's text, or "" — the DEPLOYED file, never the `.dist` template.

    The server reads what is deployed. A `.dist` is what the module ships so a
    person has something to copy, and reading it back as though it were in force
    is how this app came to offer 63 premade specs to a server that had loaded
    none (`PLAYERBOTS_CONF`, measured 2026-09-09).
    """
    for candidate in (path,):
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _whisper(player: str, bot: str) -> str:
    """`dml_whisper <master> <bot>`, with BOTH names checked.

    One place, because the three whispers differ only in their tail and a name
    check spelled three times is a name check that will one day be spelled
    twice.
    """
    return f"dml_whisper {_check_name(player, MASTER)} {_check_name(bot, BOT)}"


@dataclass(frozen=True)
class Member:
    """One bot in the master's party, as the group table has it."""

    name: str
    guid: int
    klass: int
    level: int


def group_rows_sql(
    entry: CatalogEntry, marker: dbreads.Marker, *, master_guid: int, also: tuple[str, ...] = ()
) -> str:
    """The bot members of the group `master_guid` is in.

    Two things this query is careful about, and both are the reason it is not
    `SELECT * FROM group_member`:

    * **`group_member.guid` is the GROUP, `memberGuid` is the member** on this
      tree (`describe group_member`, `yulon-ubuntu2` 2026-09-08). The sub-select
      turns the master's guid into his group's id; without it this reads every
      group on the server.
    * **The marker's clause decides which rows are bots.** The master has a
      `group_member` row of his own, and a read that returned him would report a
      party of one before any bot joined. `dbreads.bot_clause` is the same
      two-armed clause the Bots tab counts with — registry OR account prefix —
      so a party of bots cannot read back as empty the way `preset-save`'s
      registry-only version did (`rust-main:.../party.rs:277`).
    * **`also` is the second arm, and T26's live half is why it exists.** The
      marker answers for the module's own pool -- a bot-registry entry, or an
      account whose username begins with `RandomBotAccountPrefix`. The
      characters `add_named` puts in a party are the opposite by definition:
      somebody's own alt, a guild mate's, a friend's, every one of them on a
      PERSON's account that no marker will ever match. Measured on
      `yulon-ubuntu2` 2026-09-11 03:04
      (`8.6-altbot-live-yulon-ubuntu2-2026-09-11/22-discriminator.log`), the
      server records such an add NOWHERE this app can read: all thirty
      `acore_playerbots` tables hold identical row counts before and after one,
      the module's only `PlayerbotsDatabase` writes are its four account-link
      statements, `.playerbots bot list` is `Console::No` and answers a SOAP
      caller with the USAGE list of its three `Console::Yes` siblings, and the
      only `characters` column that moves is `online` -- which is what a person
      logging in moves too. So the names this app itself added come in by name,
      out of `AltbotMemory`, and the master is dropped by GUID as well, so the
      union can never hand back the master's own row.

    A name that is not a character's name is DROPPED rather than escaped, which
    is `dbreads._SAFE_PREFIX`'s rule: this is the one value in the query that
    reaches a quoted literal out of a file on disk rather than out of a press.
    """
    schemas = entry.schema_map()
    chars = schemas["characters"]
    clause = dbreads.bot_clause(entry, marker)
    remembered = tuple(name for name in also if valid_name(name))
    if remembered:
        names = ", ".join(f"'{name}'" for name in remembered)
        clause = f"({clause}) OR c.name IN ({names})"
    return (
        "SELECT c.name, c.guid, c.class, c.level "
        f"FROM {chars}.group_member gm "
        f"JOIN {chars}.characters c ON c.guid = gm.memberGuid "
        f"WHERE gm.guid = (SELECT guid FROM {chars}.group_member "
        f"WHERE memberGuid = {int(master_guid)} LIMIT 1) "
        f"AND gm.memberGuid <> {int(master_guid)} "
        f"AND ({clause}) "
        "ORDER BY c.name"
    )


def party_rows_sql(entry: CatalogEntry, *, master_guid: int) -> str:
    """EVERY member of the group `master_guid` is in, except the master.

    **The same query as `group_rows_sql` with the bot marker's clause taken
    out**, and it exists because of what the live half measured on
    `yulon-ubuntu2` on 2026-09-11 (`.notes/gates/8.6-altbot-live-yulon-ubuntu2-
    2026-09-11/`, §4): `add_named`'s poll could not see the character it had
    just added.

    `dbreads.bot_clause` asks "is this row on an account the bot marker
    recognises" -- a registry entry, or a username beginning with
    `RandomBotAccountPrefix`. For `add_bot` that is exactly right: `addclass`
    logs in a character out of the module's own pool and those characters live
    on `RNDBOT*` accounts. For THIS route it is exactly wrong: the characters it
    adds are the owner's own alt, a guild mate's, a friend's -- every one of them
    on a PERSON's account, which no bot marker will ever match. Pressed live,
    `Tsixalt` came online, the module answered `add: Tsixalt - ok` and
    `Tsixalt joins the party.` in the master's own client, `group_member` held
    the master and the alt in one group -- and this app said "no bot by that name
    joined the party within 6 seconds", because the row was filtered out of the
    read it was polling.

    The master's own row is what the marker was keeping out on this path
    (`group_rows_sql`'s docstring says so), so it is dropped by guid instead.
    That is the whole difference: no clause about accounts, one clause about the
    master.
    """
    schemas = entry.schema_map()
    chars = schemas["characters"]
    return (
        "SELECT c.name, c.guid, c.class, c.level "
        f"FROM {chars}.group_member gm "
        f"JOIN {chars}.characters c ON c.guid = gm.memberGuid "
        f"WHERE gm.guid = (SELECT guid FROM {chars}.group_member "
        f"WHERE memberGuid = {int(master_guid)} LIMIT 1) "
        f"AND gm.memberGuid <> {int(master_guid)} "
        "ORDER BY c.name"
    )


def read_members(raw: str) -> tuple[Member, ...] | str:
    """The group read's rows, or the sentence saying why they are not rows.

    A row that does not parse is REPORTED and not skipped: a party silently one
    bot short reads exactly like a party that never filled, and telling those
    two apart is the only thing this whole module is for.
    """
    out: list[Member] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 4 or not all(f.strip().lstrip("-").isdigit() for f in fields[1:]):
            return f"a party row came back as {line.strip()!r}, which is not a group member"
        out.append(
            Member(
                name=fields[0].strip(),
                guid=int(fields[1]),
                klass=int(fields[2]),
                level=int(fields[3]),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class Addition:
    """What one press of "add a bot" did, in four states rather than two.

    `added` and `joined` are separate because the server can accept the command
    and the bot can still not be in the party six seconds later: that is a
    third outcome, and reporting it as success is what "My Party works" used to
    mean. `blocker` is set only when nothing was sent at all.
    """

    added: bool
    joined: bool
    bot: str | None
    geared: bool
    specced: bool
    sentence: str
    blocker: str = ""
    spec: str = ""
    """The premade spec asked for, or "" for "let the server pick".

    `specced` is what happened to it, and it means the same thing either way: the
    WHISPER was accepted. It is never "the talents are now this", because the
    module answers a spec only in the game window and nothing this app can read
    sees that answer."""
    level: int | None = None
    level_before: int | None = None
    level_after: int | None = None
    """The level asked for, and `characters.level` on either side of the press.

    Both readings come from the group query this press already makes, so the
    "after" is the database and not the level command's own `yes`. `before` is
    what makes a chosen level provable: a bot the server already made at 60,
    asked for 60, has proved nothing, and `_level_note` says so rather than
    reporting a change that did not happen."""
    level_resent: bool = False
    """Whether the level had to be sent a SECOND time (T16).

    Set only when the row disagreed with what was asked for AFTER the row was
    written — which is the one reading that means the world, and not the
    database, is behind. A resend on an unwritten row would fire on every press
    and prove nothing, so `_level_step` never does it."""


LevelSetter = Callable[[str, int], Outcome]
"""How a level is set: `play.InstallPlay.set_level`, and never a second path.

A bot is a character and 8.4a already owns that command — including the refusal,
in the entry's own measured words, on the trees whose console has no route to a
level. A seam here rather than an import at the call site for the same reason
`engine` is one: the tests for these sentences must not be tests of a console."""


def add_bot(
    *,
    facts: Facts,
    player: str,
    klass: str,
    send: Callable[[str], Answer],
    members: Callable[[], tuple[Member, ...]],
    gender: str = "",
    spec: str = "",
    specs: tuple[str, ...] = (),
    level: int | None = None,
    max_level: int | None = None,
    set_level: LevelSetter | None = None,
    tries: int = POLL_TRIES,
    pause: float = POLL_SLEEP,
    level_tries: int = LEVEL_TRIES,
    level_pause: float = LEVEL_SLEEP,
    sleep: Callable[[float], None] = time.sleep,
) -> Addition:
    """Add one bot of `klass` to `player`'s party, and say what actually happened.

    **The ground is read before the press.** `members()` is called once before
    the command goes out and the new bot is the guid that was not in that
    reading — not "there is a bot in the party", which is already true on a
    server where one is. A poll whose assertion holds before its action is a
    poll that proves nothing, and 500 of the characters on this install are
    bots.

    **Nothing is sent while a precondition is unmet.** `blocker()` names the
    first one in its own words and this returns without touching the channel.
    That is the whole difference from 2026-08-20, when a control pressed
    happily into a bridge that was not there and reported success.

    **A spec and a level are checked before the join, not after it.** Both
    refusals are `blocker`s — nothing at all was sent — because the alternative
    is a bot standing in the party that the press then refuses to finish. The
    order once the bot IS there is level, then spec, then gear, and each step is
    a measurement rather than a preference: `SpecPick` applies the premade build
    through `PlayerbotFactory factory(bot, bot->GetLevel())`
    (`ChangeTalentsAction.cpp:146-149`), so a spec chosen before the level is a
    level-1 build on a level-60 bot; and `autogear` follows the spec because gear
    must match the new talents (`90-main.sh:3975-3977`, whose own comment says
    so).

    **The level step reads a row that has been written (T16).** It is
    `_level_step`, and the whole of why it is not one send and one read is in
    that function's docstring: on this core `.character level` changes an online
    character in memory and writes no row, so the readback T5 shipped was
    reading the last save and reporting it as the level. The step asks the
    server to write the rows before it believes what they say, and sends the
    level a second time only when a written row disagrees.
    """
    stop = blocker(facts)
    if stop is not None:
        return Addition(False, False, None, False, False, stop, blocker=stop)
    command = add_command(player, klass, gender=gender)
    if spec:
        refusal = _spec_refusal(spec, klass, specs)
        if refusal:
            return Addition(False, False, None, False, False, refusal, blocker=refusal, spec=spec)
    if level is not None:
        if set_level is None:
            raise BadRequest(
                "a level was asked for with no way to set one. A level goes through the "
                "Characters tab's own seam and this call was handed none."
            )
        refusal = _level_refusal(level, max_level)
        if refusal:
            return Addition(False, False, None, False, False, refusal, blocker=refusal, level=level)
    before = {member.guid for member in members()}
    answer = send(command)
    if answer.outcome != "yes":
        said = (answer.text or answer.reason).strip()
        return Addition(
            False,
            False,
            None,
            False,
            False,
            f"the server did not run {command!r}: {said}",
        )
    joined: Member | None = None
    for attempt in range(tries):
        if attempt:
            sleep(pause)
        joined = next((m for m in members() if m.guid not in before), None)
        if joined is not None:
            break
    if joined is None:
        window = tries * pause
        return Addition(
            True,
            False,
            None,
            False,
            False,
            f"the server accepted the command and no bot joined the party within {window:g} "
            "seconds. It may still arrive; nothing here says it will.",
        )
    level_before = joined.level
    level_after: int | None = level_before
    level_problem = ""
    level_resent = False
    if level is not None and set_level is not None:
        step = _level_step(
            bot=joined.name,
            guid=joined.guid,
            level=level,
            set_level=set_level,
            send=send,
            members=members,
            tries=level_tries,
            pause=level_pause,
            sleep=sleep,
        )
        level_after = step.after
        level_problem = step.problem
        level_resent = step.resent
    if spec:
        # One or the other, never both: see `spec_command`'s docstring for the
        # measurement. The gear follows, because it must match the new talents.
        specced = send(spec_command(player, joined.name, spec)).outcome == "yes"
        geared = send(autogear_command(player, joined.name)).outcome == "yes"
    else:
        # Byte-identical to the pair that was proved live on 2026-09-09. The
        # prior art keeps its own no-spec branch unchanged for the same reason
        # (`90-main.sh:3973`): a reorder nobody measured is a change to a
        # working path.
        geared = send(autogear_command(player, joined.name)).outcome == "yes"
        specced = send(talents_command(player, joined.name)).outcome == "yes"
    return Addition(
        True,
        True,
        joined.name,
        geared,
        specced,
        f"{joined.name} joined the party"
        + _finish(geared, specced, spec)
        + _level_note(level, level_before, level_after, level_problem, resent=level_resent),
        spec=spec,
        level=level,
        level_before=level_before,
        level_after=level_after,
        level_resent=level_resent,
    )


@dataclass(frozen=True)
class LevelStep:
    """What the level step did, in the states a caller has to tell apart.

    `after` is `characters.level`, always — never the level that was asked for
    and never the console's own `You changed level of <bot> to N.`, which this
    module has watched be true of the world and false of the row at the same
    moment.
    """

    after: int | None
    problem: str = ""
    """A complete sentence, or "". Each producer writes the whole of its own,
    because "the level was not set" is true of a refused level command and false
    of a level the server took and a `saveall` that never ran (round 2's
    reviewer)."""
    resent: bool = False
    saved: bool = False
    """Whether the row was written by a save the server CONFIRMED, after the last
    send. A reading taken without one is a reading of the previous save and says
    nothing about this press — so a `saveall` that came back `no`, or came back
    not at all, leaves this False and sets `problem` instead."""


def _save_refusal(answer: Answer) -> str:
    """Why the row could not be written, or "" for a save the server ran.

    **Round 2's must-fix 1.** The first version of the level step threw this
    answer away, and every other send in `add_bot` reads its own outcome
    (`:1187`, `:1240-1241`, `:1247-1248`). Thrown away, a stopping world or a
    SOAP timeout bought thirty seconds of polling a row nobody had written, then
    a resend, then another ignored save, and finally a sentence claiming the row
    HAD been written and the level sent twice — the one shape of lie this whole
    module exists to delete.

    `indeterminate` is its own arm and not folded into `no`: a SOAP timeout means
    the command was sent and the answer never came (`channel.Answer`), so the
    rows may be written and may not, and "the server did not run it" would be a
    claim nothing here can make.
    """
    if answer.outcome == "yes":
        return ""
    said = (answer.text or answer.reason).strip()
    tail = f": {said}" if said else ""
    if answer.indeterminate:
        return (
            f"The level was sent and the server accepted it. The {SAVE_COMMAND} that would "
            f"write characters.level did not come back{tail}, so it may have run and may not, "
            "and the row still reads what its last save wrote. Nothing here says whether the "
            "level took."
        )
    return (
        f"The level was sent and the server accepted it, and then the server did not run "
        f"{SAVE_COMMAND}{tail} — so characters.level was never written and nothing here says "
        "whether the level took."
    )


def _row_level(members: Callable[[], tuple[Member, ...]], guid: int) -> int | None:
    """`characters.level` for one guid, out of the group read this press makes.

    `None` is "that guid is not in the group table now", which is a third answer
    and not a level: a bot the manager's own timer moved out of the party
    between the send and the read has not proved anything about its level.
    """
    row = next((member for member in members() if member.guid == guid), None)
    return None if row is None else row.level


def _saved_row_level(
    *,
    level: int,
    guid: int,
    send: Callable[[str], Answer],
    members: Callable[[], tuple[Member, ...]],
    tries: int,
    pause: float,
    sleep: Callable[[float], None],
) -> tuple[int | None, str]:
    """Ask the server to write the rows, then poll `characters.level` for it.

    Returns the row and the save's own refusal, and the refusal is checked BEFORE
    the poll: polling for a write nobody made is thirty seconds spent proving
    nothing, and reporting its result as a reading of this press is
    `_save_refusal`'s docstring.

    The poll is the join poll's own machinery — read, sleep, read — for the same
    reason it is used there: the answer arrives when the server gets to it and
    not on a schedule this app sets, so it returns the moment the row agrees and
    the ceiling is the ceiling rather than the wait.
    """
    refusal = _save_refusal(send(SAVE_COMMAND))
    if refusal:
        return (_row_level(members, guid), refusal)
    after: int | None = None
    for attempt in range(tries):
        if attempt:
            sleep(pause)
        after = _row_level(members, guid)
        if after == level or after is None:
            return (after, "")
    return (after, "")


def _level_step(
    *,
    bot: str,
    guid: int,
    level: int,
    set_level: LevelSetter,
    send: Callable[[str], Answer],
    members: Callable[[], tuple[Member, ...]],
    tries: int,
    pause: float,
    sleep: Callable[[float], None],
) -> LevelStep:
    """Set the level, make `characters.level` answerable, and read it back.

    **The measurement this is built on** is
    `.notes/gates/8.6-level-holds-yulon-ubuntu2-2026-09-10/`, and it overturns
    the reading T5 left behind rather than adding to it. On a bot freshly joined
    to a master's party, the level sent at +0, +5, +10, +20 and +30 seconds
    after the group row appeared LANDED every time and stayed landed: the
    world's own `.pinfo` read the chosen level within a second of the send and
    never stopped reading it — ninety seconds later in the three rows whose tail
    ran that long, fifteen to forty-six in the other four. There is no window.
    What T5 photographed is
    `characters.level` not having been written: `.character level` on an ONLINE
    character calls `GiveLevel` and writes no row
    (`cs_character.cpp:252-281`), and a character writes its own row at
    `PlayerSaveInterval` — a quarter of an hour here — so the panel's readback
    a second later was reading the last save and reporting it as the level.

    So what closes the window is not a wait for the module and not a resend: it
    is a SAVE, which is what `SAVE_COMMAND`'s docstring cites and what
    `_saved_row_level` sends. The row is then a reading of this press — and only
    if the SERVER SAID IT SAVED. A save that came back `no`, or did not come back
    at all, ends the step in `_save_refusal`'s own sentence rather than in a
    verdict about the level: nothing was written, so nothing was read.

    **The resend is what a real disagreement earns, and only that.** Once the
    row has been written, a row that still disagrees is the world disagreeing —
    the case the ticket was filed for — and that one gets the level sent a
    second time and the row written and read again. A resend before the save
    would fire on every press, on a row that had simply not been written yet,
    and prove nothing; this never does it.

    The order — send, save, read, resend, save, read — is also why the first
    reading is not skipped: a bot the server already made at the level that was
    asked for needs no save and no resend, and `add_bot` says so in
    `_level_note`'s "already" arm.
    """
    outcome = set_level(bot, level)
    if not outcome.done:
        said = (outcome.problem or outcome.text).strip()
        return LevelStep(_row_level(members, guid), f"The level was not set: {said}")
    after = _row_level(members, guid)
    if after == level or after is None:
        return LevelStep(after)
    after, refusal = _saved_row_level(
        level=level, guid=guid, send=send, members=members, tries=tries, pause=pause, sleep=sleep
    )
    if refusal:
        return LevelStep(after, refusal)
    if after == level or after is None:
        return LevelStep(after, saved=True)
    outcome = set_level(bot, level)
    if not outcome.done:
        said = (outcome.problem or outcome.text).strip()
        return LevelStep(
            after,
            f"The server took the level and characters.level read {after}, not {level}, after the "
            f"row was written. The second send was refused: {said}",
            resent=True,
        )
    after, refusal = _saved_row_level(
        level=level, guid=guid, send=send, members=members, tries=tries, pause=pause, sleep=sleep
    )
    if refusal:
        return LevelStep(after, refusal, resent=True)
    return LevelStep(after, resent=True, saved=True)


def _spec_refusal(spec: str, klass: str, specs: tuple[str, ...]) -> str:
    """Why this spec was not sent, or "" for one this server has.

    Refused HERE rather than by the server, because the server does not refuse
    it anywhere this app can hear: `Spec <x> not found` goes to the master's
    chat window (`ChangeTalentsAction.cpp:157`) and the channel sees a command
    that ran. An unchecked spec is a bot that is silently not specced under a
    sentence saying it is.
    """
    if not valid_spec(spec):
        return (
            f"{spec!r} is not a premade spec name — it may hold only letters, digits, spaces "
            "and . _ - because it goes into a command the world server parses. Nothing was sent."
        )
    if not specs:
        return (
            f"this install's {PLAYERBOTS_CONF} lists no premade specs for {klass}, so there is "
            "no spec to ask for and nothing was sent. The module answers an unknown spec only "
            "in the game window, so a spec sent on a guess would look exactly like one that "
            "worked."
        )
    if spec not in specs:
        return (
            f"{spec!r} is not one of the premade specs this server has for {klass}: "
            f"{', '.join(specs)}. Nothing was sent."
        )
    return ""


def _level_refusal(level: int, max_level: int | None) -> str:
    """Why this level was not sent, or "" for one inside this server's own range."""
    if max_level is None:
        return (
            f"this server's own top level could not be read: {MAX_LEVEL_KEY} is not set in "
            f"{WORLD_CONF}, so there is nothing to bound a level by and nothing was sent."
        )
    if not 1 <= level <= max_level:
        return (
            f"level {level} is outside 1 to {max_level}, which is this server's own "
            f"{MAX_LEVEL_KEY}. Nothing was sent."
        )
    return ""


def _finish(geared: bool, specced: bool, spec: str = "") -> str:
    if spec:
        caveat = (
            " The module answers a spec only in the game window, so that is the whisper being "
            "accepted and not the talents changing."
        )
        if specced and geared:
            return f", geared, and asked for the {spec} spec.{caveat}"
        if specced:
            return (
                f", and asked for the {spec} spec. The autogear whisper was refused, so it is "
                f"not geared.{caveat}"
            )
        if geared:
            return (
                f", geared. The {spec} spec whisper was refused, so its talents are whatever "
                "the server picked."
            )
        return f". Neither the {spec} spec whisper nor the autogear whisper was accepted."
    if geared and specced:
        return ", geared and specced."
    if geared:
        return ", geared. The talents whisper was refused, so it is not specced."
    if specced:
        return ", specced. The autogear whisper was refused, so it is not geared."
    return ". Neither the autogear nor the talents whisper was accepted."


def _level_note(
    level: int | None,
    before: int | None,
    after: int | None,
    problem: str,
    *,
    resent: bool = False,
) -> str:
    """What the chosen level did, read out of `characters.level` on both sides.

    The "already" arm is the gate rule in the app's own voice: a step whose
    assertion was true before its action has proved nothing, and a bot the server
    happened to make at the level that was asked for is exactly that step.

    **The "still reads" arm was rewritten by T16, and this is what changed.**
    Until 2026-09-10 it said the row "has been seen to stay unwritten for as long
    as this panel watched it" and told the reader to take the level as not set.
    That was true of what
    `.notes/gates/8.6-spec-level-dismiss-yulon-ubuntu2-2026-09-09` photographed —
    a level of 42 at 13:12 (`panel-8-…png`) and one of 55 at 13:17 (`panel-9-…png`
    with `panel-transcript.log:61-130`), the row reading 1 in both — and it was
    the wrong conclusion to draw from it. `.notes/gates/8.6-level-holds-yulon-ubuntu2-2026-09-10`
    measured the same press with the world's own `.pinfo` beside the row: the
    level had landed within a second every time, and the row was simply not
    written yet (`cs_character.cpp:252-281`; see `SAVE_COMMAND`). So the panel
    was reading the last save and calling it the level.

    The arm survives, because it is still reachable and still means something —
    but only now that `_level_step` writes the row before reading it and has
    sent the level twice. It is no longer "the row has not caught up"; it is the
    world disagreeing, after being asked twice and asked to write it down.

    **The order of the arms is round 2's reviewer's**, and it is not cosmetic:
    the "already" arm used to be tested before the readback, so a bot that WAS at
    the level asked for and whose written row came back at some other level was
    told "nothing about the level changed" — the one reading that says something
    did. A disagreeing row now wins over "already", and `problem` carries a
    complete sentence from whichever step produced it rather than being wrapped
    in "the level was not set" here, because a refused `saveall` is not a level
    that was not set.
    """
    if level is None:
        return ""
    if problem:
        return f" {problem}"
    if after is None:
        return (
            " The level was sent and this bot is no longer in the group table, so no level "
            "could be read back."
        )
    if after != level:
        return (
            f" The server accepted the level and characters.level still reads {after}, not "
            f"{level}, after the row was written and the level was sent a second time. Take "
            "the level as not set."
        )
    if before == level:
        return (
            f" characters.level already read {level} before the press, so nothing about the "
            "level changed and this says nothing about whether it would have."
        )
    if resent:
        return (
            f" characters.level read {before} before the press and {after} after — the level "
            "had to be sent a second time to get there."
        )
    return f" characters.level read {before} before the press and {after} after."


@dataclass(frozen=True)
class Dismissal:
    """What one press of "dismiss" did. `removed` is read back, not assumed."""

    removed: bool
    logged_out: bool
    sentence: str
    bot: str = ""
    """Which bot this is about, so a list of them needs no parallel list of names.

    Defaulted rather than positional so the field could be added without moving
    the three that were already there."""
    unreadable: bool = False
    """`removed` is False, but not because a row was seen and it was still
    there — the poll's last read never confirmed either answer (T21, round 2's
    must-fix 2). `_mass_sentence` needs this to keep "the group table could
    not be read" out of "Still here", which asserts a row somebody actually
    saw."""


def dismiss(
    *,
    player: str,
    bot: str,
    send: Callable[[str], Answer],
    members: Callable[[], tuple[Member, ...] | str],
    tries: int = POLL_TRIES,
    pause: float = POLL_SLEEP,
    sleep: Callable[[float], None] = time.sleep,
) -> Dismissal:
    """Uninvite `bot`, whisper it to log out, and read the group table back.

    The whisper is best-effort, as it is at every call site in the prior art
    whose own comment says its failure "never aborts the caller"
    (`rust-main:.../party.rs:189-192`): the uninvite is what empties the row,
    and a bot that stays logged in is standing in the world, not in the group.

    `removed` is the group table read AFTER, never the uninvite's own `yes`.

    **T13.** `dml_uninvite.lua` now checks `bot`'s CURRENT group against
    `player` — membership, the same relationship `group_rows_sql` reads, not
    leadership (round 1's must-fix 1: a master grouped with another human, or
    any leader hand-off, is still `player`'s party) — and refuses every
    non-removing branch (player not found, bot not found, bot ungrouped, bot
    grouped with someone else) in words this reads back
    (`_uninvite_refusal_marker`) rather than the core's error flag — the flag
    never trips either way, so `answer.outcome` alone cannot tell a refusal
    from a success here. Recognised, nothing more is sent: a bot the bridge
    just refused to remove gets neither a logout whisper naming the wrong
    master nor a poll of a group it was never confirmed against. The sentence
    is bounded to what the server actually said — round 1 cut the earlier
    "the group table moved it" wording, which was this app inventing a
    mechanism the bridge never reported.

    **T21** (T13 round 2 review, note 5). `members` answers `tuple[Member,
    ...] | str` here, the group read's own shape, rather than the folded
    `tuple[Member, ...]` `add_bot` polls with. The two polls are not the same
    question: `add_bot` watches for a guid to APPEAR, so a read that could not
    be done and a read that found nothing both mean "not yet" — `_rows_only`
    folding a failure into `()` is correct there. This poll watches for a guid
    to VANISH, so `()` cannot mean both "the row is gone" and "the row could
    not be read" — round 2 of T13 closed the one route that made a failed
    read look like silence (the Lua no longer answers an empty SOAP reply),
    so what is left on a failed read here is a genuine uninvite whose
    follow-up read errored, and folding it to `()` would report `removed`
    on a read that never happened. A failed read here neither confirms nor
    denies, so it keeps the poll going the same as an unchanged row would;
    only the LAST read decides what expiry reports.
    """
    answer = send(uninvite_command(player, bot))
    if answer.outcome != "yes":
        said = (answer.text or answer.reason).strip()
        return Dismissal(False, False, f"the server did not uninvite {bot}: {said}", bot=bot)
    marker = _uninvite_refusal_marker(player, bot)
    if marker in answer.text:
        return Dismissal(
            False,
            False,
            f"{bot} was not removed: the server says {answer.text.strip()}",
            bot=bot,
        )
    logged_out = send(logout_command(player, bot)).outcome == "yes"
    unreadable = ""
    for attempt in range(tries):
        if attempt:
            sleep(pause)
        rows = members()
        if isinstance(rows, str):
            unreadable = rows
            continue
        unreadable = ""
        if all(member.name != bot for member in rows):
            return Dismissal(
                True,
                logged_out,
                f"{bot} left the party" + ("." if logged_out else ", and is still logged in."),
                bot=bot,
            )
    if unreadable:
        return Dismissal(
            False,
            logged_out,
            f"{bot} may or may not have left: the group table could not be read "
            f"({unreadable.strip()})",
            bot=bot,
            unreadable=True,
        )
    window = tries * pause
    return Dismissal(
        False,
        logged_out,
        f"the server accepted the uninvite and {bot} is still in the group table "
        f"{window:g} seconds later.",
        bot=bot,
    )


@dataclass(frozen=True)
class MassDismissal:
    """What one press of "dismiss all" did, per bot rather than as a count.

    `dismissals` is every attempt in the order it was made, each carrying its own
    `removed` and its own sentence, because a count is a report nobody can check
    against the party frame in front of them — and because one bot that refuses
    must not hide the ones that went. `blocker` means the channel was never
    touched, which is this module's word for it everywhere else.
    """

    attempted: int
    dismissals: tuple[Dismissal, ...]
    sentence: str
    blocker: str = ""


def dismiss_all(
    *,
    player: str,
    bots: tuple[str, ...],
    send: Callable[[str], Answer],
    members: Callable[[], tuple[Member, ...] | str],
    tries: int = POLL_TRIES,
    pause: float = POLL_SLEEP,
    sleep: Callable[[float], None] = time.sleep,
) -> MassDismissal:
    """`dismiss` per bot, and every one of them attempted.

    The prior art's own review finding, in both directions: one unreachable bot
    must not strand the rest of the party (`90-main.sh:4076`), and an attempt is
    not a dismissal (`:4079-4083` — SOAP down with the database up used to report
    "dismissed: N" while every bot was still in the group). Here the second half
    is free, because `dismiss` reads the group table back rather than trusting
    the uninvite's `yes`.

    It costs what that readback costs: a bot whose row does not clear polls for
    the full window before it is reported as still there.

    **T21, round 2.** `members` is `tuple[Member, ...] | str` here too, forwarded
    to each `dismiss()` call unfolded: round 1 fixed `InstallParty.remove` but
    left `InstallParty.remove_all` handing this the `_rows_only`-folded read, so
    a database outage during a batch dismiss still reported every bot in it
    `removed=True` — the whole finding, on the one path a batch press actually
    takes.
    """
    if not bots:
        stop = (
            f"{player}'s party has no bots in it, so there is nothing to dismiss and nothing "
            "was sent."
        )
        return MassDismissal(0, (), stop, blocker=stop)
    done = tuple(
        dismiss(
            player=player,
            bot=bot,
            send=send,
            members=members,
            tries=tries,
            pause=pause,
            sleep=sleep,
        )
        for bot in bots
    )
    return MassDismissal(len(done), done, _mass_sentence(done))


def _mass_sentence(done: tuple[Dismissal, ...]) -> str:
    """Three buckets, not two (T21, round 2's must-fix 2).

    A bot the poll actually saw stay (the bridge refused it, or its row was
    still there at the deadline) is not the same finding as one whose table
    could not be read at all — "Still here" asserts a row somebody looked at,
    and a read that never happened cannot say that. Folding `unreadable` into
    `stayed` is the same false-confidence `_rows_only` was fixed out of the
    poll itself; this is the summary line making the same mistake back in.
    """
    gone = [one.bot for one in done if one.removed]
    stayed = [one for one in done if not one.removed and not one.unreadable]
    unread = [one for one in done if not one.removed and one.unreadable]
    if not stayed and not unread:
        return f"{bots_word(len(done))} left the party: {', '.join(gone)}."
    if unread:
        # A count over a batch some of whose reads never happened is a count
        # of what was CONFIRMED, and the head says so (Codex, round 2): "None
        # of 2 bots left" over two unreadable polls asserted an outcome no
        # read established, when all two may have left.
        head = (
            f"{len(gone)} of {bots_word(len(done))} confirmed left the party: {', '.join(gone)}. "
            if gone
            else f"None of {bots_word(len(done))} were confirmed to have left the party. "
        )
    else:
        head = (
            f"{len(gone)} of {bots_word(len(done))} left the party: {', '.join(gone)}. "
            if gone
            else f"None of {bots_word(len(done))} left the party. "
        )
    tail = []
    if stayed:
        # Every refusal in its own words. Summarising them ("2 failed") is how
        # one bot that is simply logged out reads the same as a channel that
        # is down.
        tail.append("Still here — " + "; ".join(f"{one.bot}: {one.sentence}" for one in stayed))
    if unread:
        tail.append(
            "Could not be confirmed — " + "; ".join(f"{one.bot}: {one.sentence}" for one in unread)
        )
    return head + " ".join(tail)


def bots_word(count: int) -> str:
    """ "1 bot" or "N bots", in one place.

    Public because the panel says the same thing on its armed button, and the
    live gate found "1 bots" in this feature's own summary line once already
    (`test_one_bot_is_a_bot_and_not_one_bots`)."""
    return "1 bot" if count == 1 else f"{count} bots"


# -- T26: adding a NAMED character as a bot --------------------------------
#
# The other route into a party, and a different command: `.playerbots bot add
# <Name>` puts an EXISTING character -- the player's own alt, a guild mate, a
# friend's character on a linked account -- into the master's group as a bot.
# Measured on `yulon-ubuntu2` 2026-09-10, the record is
# `.notes/gates/8.6-altbot-measure-yulon-ubuntu2-2026-09-10/`:
#
# * there is no `.bot add`; the command is `.playerbots bot add <Name>`,
#   `SEC_PLAYER, Console::No` (`PlayerbotCommandScript.cpp:36`), so SOAP cannot
#   see it any more than it can see `addclass` -- four spellings were asked over
#   the app's own channel and every one came back with the USAGE list of the
#   three `Console::Yes` siblings, with the world logging nothing (§4);
# * the master is always the calling session's own player
#   (`PlayerbotMgr.cpp:860-874`), and the session must be a real one -- every
#   character online on that box is a bot, which is why neither route could put
#   the permission question to the module at all (§5);
# * everything the module says about the add goes to the MASTER's client
#   (`PlayerbotMgr.cpp:889-892`), never to the SOAP result -- so the bridge
#   script answers in its own words and this side polls the group table.


def botadd_command(player: str, name: str) -> str:
    """`dml_botadd <master> <character>`.

    Both names are checked, and `name` is checked as a character's rather than
    as a bot's: it is not a bot yet -- it is somebody's existing character, and
    whether the module will accept it is what `candidates()` works out before
    this is ever built.
    """
    return f"dml_botadd {_check_name(player, MASTER)} {_check_name(name, MASTER)}"


def _botadd_refusal_marker(player: str, name: str) -> str:
    """The base of the words `dml_botadd.lua` answers with on the two branches
    that do NOT issue the command -- the master not in the world, and the
    character logged in -- each carrying its own `(<reason>)` after it.

    Read out of `answer.text` and not `answer.outcome`, for
    `_uninvite_refusal_marker`'s reason: this bridge's hook never sets the
    core's error flag, on a refusal any more than on a success, so the channel
    reports "yes" either way.
    """
    return BOTADD_REFUSAL_FORMAT % (name, player)


def _botadd_issued_marker(player: str, name: str) -> str:
    """The words the script says when it DID run the command. Necessary and not
    sufficient: it is the relay reporting itself, and the bot is the group
    table's answer."""
    return BOTADD_ISSUED_FORMAT % (player, name)


@dataclass(frozen=True)
class NamedAddition:
    """What one press of "Add this character" did.

    **Its own dataclass rather than `Addition`, deliberately.** `Addition`
    carries `geared`, `specced`, `spec`, `level`, `level_before` and
    `level_after`, and this route sends none of those whispers: `bot add` logs
    the character in with its own gear and its own talents -- it is somebody's
    character, not a bot the server just made -- and `_finish()` would end every
    sentence here with "Neither the autogear nor the talents whisper was
    accepted", which is true and meaningless. Shaped after `Dismissal` instead:
    an outcome read back from the group table, with `unreadable` for the read
    that never happened (T21).

    `added` is the bridge saying it ran the command; `joined` is the group table
    saying a bot by that name is in the party. They are separate for
    `Addition`'s reason -- the server can take the command and the character can
    still not arrive, and on this route it is the ordinary case: every one of
    the module's own refusals goes to the master's game window.
    """

    added: bool
    joined: bool
    name: str
    sentence: str
    blocker: str = ""
    """Set only when nothing was sent at all, this module's word for it."""
    unreadable: bool = False
    """`joined` is False, and not because a table was read and the row was not
    in it: the poll's last read never happened (T21)."""


def add_named(
    *,
    master: str,
    name: str,
    send: Callable[[str], Answer],
    members: Callable[[], tuple[Member, ...] | str],
    tries: int = POLL_TRIES,
    pause: float = POLL_SLEEP,
    sleep: Callable[[float], None] = time.sleep,
) -> NamedAddition:
    """Ask the bridge to add `name` to `master`'s party, and read the table back.

    **The ground is read first and a failure there stops the press.** `members`
    answers `tuple[Member, ...] | str` -- the group read's own shape, unfolded,
    as `dismiss()` takes it and unlike `add_bot`'s poll. The two are not the
    same question: `add_bot` watches for ANY new guid and a read that could not
    be done adds nothing to either side of that comparison, so folding it into
    `()` is right there. Here the ground read decides what "joined" means for
    one NAMED character, and `()` from a read that never happened would let a
    character already standing in the party read as one this press brought
    (T21's rule, on the adding side).

    **Nothing the module says can reach this function.** Its replies -- the
    permission refusal at `PlayerbotMgr.cpp:115`, the cap at `:134`, "player
    already logged in" at `:686` -- are `PSendSysMessage` into the master's own
    session (`:889-892`), which for a `RunCommand` caller is that player's game
    window. So the script's own sentence says only whether the command was
    issued, and the group table says whether a bot arrived. A press that times
    out says exactly that and no more.
    """
    command = botadd_command(master, name)
    rows = members()
    if isinstance(rows, str):
        stop = (
            f"{master}'s party could not be read, so there is nothing to measure a new bot "
            f"against and nothing was sent ({rows.strip()})"
        )
        return NamedAddition(False, False, name, stop, blocker=stop)
    if any(member.name == name for member in rows):
        stop = (
            f"{name} is already in {master}'s party, so nothing was sent. Adding a character "
            "that is already there could not be shown to have done anything."
        )
        return NamedAddition(False, False, name, stop, blocker=stop)
    before = {member.guid for member in rows}
    answer = send(command)
    if answer.outcome != "yes":
        said = (answer.text or answer.reason).strip()
        return NamedAddition(False, False, name, f"the server did not run {command!r}: {said}")
    if _botadd_refusal_marker(master, name) in answer.text:
        return NamedAddition(
            False, False, name, f"{name} was not added: the server says {answer.text.strip()}"
        )
    if _botadd_issued_marker(master, name) not in answer.text:
        return NamedAddition(
            False,
            False,
            name,
            f"the server answered dml_botadd without the bridge's own words, so what answered "
            f"was not this bridge: {answer.text.strip()!r}",
        )
    joined: Member | None = None
    unreadable = ""
    for attempt in range(tries):
        if attempt:
            sleep(pause)
        rows_now = members()
        if isinstance(rows_now, str):
            unreadable = rows_now
            continue
        unreadable = ""
        joined = next(
            (row for row in rows_now if row.name == name and row.guid not in before), None
        )
        if joined is not None:
            break
    if joined is not None:
        return NamedAddition(True, True, name, f"{name} joined the party.")
    if unreadable:
        return NamedAddition(
            True,
            False,
            name,
            f"the server was asked to add {name} and the group table could not be read "
            f"afterwards ({unreadable.strip()}), so whether it joined is unknown.",
            unreadable=True,
        )
    window = tries * pause
    return NamedAddition(
        True,
        False,
        name,
        f"the server was asked to add {name} and no bot by that name joined the party within "
        f"{window:g} seconds. This command answers only in {master}'s own game window, so if "
        "the server refused it, its words are there and not here.",
    )


# -- T26: the picker -------------------------------------------------------
#
# Which of this server's characters the module would accept for THIS master,
# with the refusal named where it would not. Every rule below is transcribed
# from `mod-playerbots` at `b949b50b`, read on `yulon-ubuntu2` 2026-09-10; the
# record is `.notes/gates/8.6-altbot-measure-yulon-ubuntu2-2026-09-10/`, §2 for
# the rules and §6 for the columns.
#
# The picker exists because none of the module's refusals can be heard from
# here: "Failure: You are not allowed to control bot <Name>"
# (`PlayerbotMgr.cpp:115`), the cap (`:134`) and "player already logged in"
# (`:686`) are all `PSendSysMessage` into the master's own chat window. An
# unfiltered list would be a list of presses that time out with no reason
# anywhere -- the same shape T5 fixed for premade specs, whose refusal is
# in-game only too.

MAX_ADDED_BOTS_KEY = "AiPlayerbot.MaxAddedBots"
"""The module's own cap on how many bots one master may have added at once.

`40` in the shipped `playerbots.conf.dist` on `yulon-ubuntu2` (`:136`), read
here from the DEPLOYED conf and never from the `.dist` -- `PLAYERBOTS_CONF`'s
docstring records what reading a template as though it were configuration cost
this feature once already."""

ALLOW_ACCOUNT_KEY = "AiPlayerbot.AllowAccountBots"
ALLOW_GUILD_KEY = "AiPlayerbot.AllowGuildBots"
ALLOW_LINKED_KEY = "AiPlayerbot.AllowTrustedAccountBots"
"""The three flags that switch three of the four rules off, and the round-1
review's first finding (2026-09-10).

`PlayerbotMgr.cpp:101-116` does not test membership on its own -- each arm is
`sPlayerbotAIConfig.allow<X>Bots && <the membership test>`:

    bool sameAccount = sPlayerbotAIConfig.allowAccountBots && accountId == masterAccountId;
    bool sameGuild = sPlayerbotAIConfig.allowGuildBots && guild && guild->GetMember(playerGuid);
    bool linkedAccount = sPlayerbotAIConfig.allowTrustedAccountBots && IsAccountLinked(…);

so an install that sets one to 0 refuses a character this picker used to offer
-- in the master's client alone (`:115`), where nothing this app can read sees
it, after this app has already said the command was issued. The addclass pool
(rule 3) has no flag and is not gated. All three ship `1` in
`playerbots.conf.dist` on `yulon-ubuntu2` (`:155`, `:158`, `:161`, measured
2026-09-10), which is exactly why they must be READ rather than assumed: the
value that matters is the one this install deployed."""

ADDCLASS_ACCOUNT_TYPE = 2
"""`playerbots_account_type.account_type` for the addclass pool.

Type 1 is the random-bot pool and type 2 is the addclass pool
(`RandomPlayerbotMgr.cpp:1719-1751` builds the addclass cache from the type-2
accounts; 50 accounts of each on the measured box). Rule 3 lets ANYBODY add a
character from the type-2 pool, which is the one bucket a stock install has
rows in."""

REFUSED_ONLINE = "logged in — the module refuses a character that is in the world"
"""`PlayerbotMgr.cpp:685-686`, and it is checked before any permission rule."""

ALLOWED_SAME_ACCOUNT = "on this character's own account"
ALLOWED_GUILD = "in this character's guild"
ALLOWED_POOL = "one of the server's addclass bots, which anybody may add"
ALLOWED_LINK = "on an account linked to this one"
"""The four rules of `PlayerbotMgr.cpp:101-116` in the app's voice, in the order
the module evaluates them. Each row says which one let it in, because "why is
this one offered and that one not" is the whole question a picker answers."""

FLAG_OFF = "%s, but this server's %s is 0"
FLAG_UNREADABLE = "%s, but this server's %s could not be read"
"""Why a row that a rule WOULD have let in is greyed anyway -- the rule in the
app's own words, then the key that decides it.

The unreadable case is greyed and never offered (the lead's round-1 rejection):
an arm whose flag nobody could read is indeterminate, and an indeterminate row
offered is a press that may end in a refusal only the master's client sees."""

REFUSED_NO_RULE = (
    "not on this character's account, not in its guild, not an addclass bot, and not on a "
    "linked account"
)
"""The `else` of that same `if`. All four named, because the answer to it is a
different action for each: play the alt on your own account, join the guild, or
link the accounts."""


def max_added_bots(server_dir: Path) -> int | None:
    """This install's own `MaxAddedBots`, or `None` for "nobody could read it".

    `None` rather than 40, for `max_player_level`'s reason: 40 is one conf
    value on one install of one fork, and answering it for a conf that never
    sets the key would be this app inventing a fact about somebody's server. A
    cap nobody could read means no row is refused for it here -- the server
    still enforces whatever it has -- and the picker's note says so.
    """
    value = _conf_value(_conf_text(server_dir / PLAYERBOTS_CONF), MAX_ADDED_BOTS_KEY)
    return int(value) if value is not None and value.isdigit() else None


@dataclass(frozen=True)
class AllowFlags:
    """The three allow-flags as this install's DEPLOYED conf has them.

    Three-valued for the reason every reading in this module is: `None` is "the
    key could not be read", which is neither on nor off. A `.dist`-only install
    (which is what `yulon-ubuntu2` is) has said nothing about these keys, and
    the compiled defaults behind them were never measured on this fork -- the
    same trap `ALE.Enabled` set, where the shipped comment says one thing and
    the compiled default says the other. So `None` greys the rows that arm
    would have offered rather than offering or refusing them on a guess.
    """

    account: bool | None
    guild: bool | None
    linked: bool | None

    @classmethod
    def all_on(cls) -> AllowFlags:
        """The three as `playerbots.conf.dist` ships them. Test-side only: the
        app never assumes this, which is the whole point of the reader."""
        return cls(True, True, True)


def allow_flags(server_dir: Path) -> AllowFlags:
    """The three keys out of the deployed `playerbots.conf`, `None` where absent.

    The same reader `max_added_bots()` uses, on the same file, for the same
    measured reason (`PLAYERBOTS_CONF`): `sConfigMgr` loads the deployed file,
    a `.dist` beside it is a template, and this feature has already paid once
    for reading one as though it were configuration.
    """
    text = _conf_text(server_dir / PLAYERBOTS_CONF)
    return AllowFlags(
        _flag(_conf_value(text, ALLOW_ACCOUNT_KEY)),
        _flag(_conf_value(text, ALLOW_GUILD_KEY)),
        _flag(_conf_value(text, ALLOW_LINKED_KEY)),
    )


_FLAG_ON = ("1", "y", "on", "yes", "true")
_FLAG_OFF = ("0", "n", "off", "no", "false")


def _flag(value: str | None) -> bool | None:
    """The core's own boolean grammar, read off the pinned tree; anything else is unread.

    The module reads these keys through `sConfigMgr.GetOption<bool>`, whose
    non-strict `StringTo<bool>` (`src/common/Utilities/StringConvert.h:110-121`
    on `yulon-ubuntu2`, read 2026-09-10) takes `1`, `y`, `on`, `yes`, `true` as
    on and `0`, `n`, `off`, `no`, `false` as off, the words case-insensitively,
    and answers nothing for any other text. Round 2 read `1`/`0` alone and
    called `true` unmeasured, which greyed rows the module allows (Codex on
    T26, round 2); this is the measured grammar. Absent stays absent: a key the
    deployed file does not carry is not folded into off.
    """
    if value is None:
        return None
    text = value.strip().strip('"')
    if text == "1" or text.lower() in _FLAG_ON:
        return True
    if text == "0" or text.lower() in _FLAG_OFF:
        return False
    return None


@dataclass(frozen=True)
class CharacterRow:
    """One row of the picker's first read: a character as `characters` has it.

    `guild_id` and `guild` come from `guild_member` + `guild` in the same read
    -- there is no guild column on `characters`, which the measurement checked
    by grep rather than assumed (§6).
    """

    guid: int
    name: str
    level: int
    klass: int
    account: int
    online: bool
    guild_id: int
    guild: str


@dataclass(frozen=True)
class Candidate:
    """One row of the list a person chooses from, with its rule named.

    `allowed_by` and `refused_because` are exclusive and exactly one of them is
    set: a row is offered because a rule let it in, or greyed because one shut
    it out, and there is no third state a list can draw honestly.
    """

    name: str
    level: int
    klass: int
    account_or_guild: str
    allowed_by: str = ""
    refused_because: str = ""

    @property
    def allowed(self) -> bool:
        return bool(self.allowed_by)


def candidates_sql(entry: CatalogEntry, master: str) -> tuple[str, str, str]:
    """The picker's three reads, in order, for `master`.

    **Three and not one join**: `DockerSql` connects to one database at a time,
    and the columns live in three (§6's table) -- the characters, their guild
    and its name in `acore_characters`; the account NAME in `acore_auth`; the
    addclass pool and the account links in `acore_playerbots`.

    The third is filtered to this master's account through a cross-schema
    sub-select on his name -- the same idiom `group_rows_sql` uses to turn a
    guid into a group id, and for the same reason: the alternative is reading a
    link table that belongs to everybody. Both directions are asked, because
    `.playerbots account link` writes both (`PlayerbotMgr.cpp:1840-1885`,
    `INSERT IGNORE` twice) and so does `link_account_sql`.

    `master` goes into a quoted literal, so it is checked first: `_check_name`
    is a boundary here exactly as it is in the command builders.
    """
    _check_name(master, MASTER)
    schemas = entry.schema_map()
    chars = schemas["characters"]
    auth = schemas["auth"]
    bots = schemas["playerbots"]
    mine = f"(SELECT m.account FROM {chars}.characters m WHERE m.name = '{master}' LIMIT 1)"
    characters = (
        "SELECT c.guid, c.name, c.level, c.class, c.account, c.online, "
        "COALESCE(gm.guildid, 0), COALESCE(g.name, '') "
        f"FROM {chars}.characters c "
        f"LEFT JOIN {chars}.guild_member gm ON gm.guid = c.guid "
        f"LEFT JOIN {chars}.guild g ON g.guildid = gm.guildid "
        "ORDER BY c.name"
    )
    accounts = f"SELECT a.id, a.username FROM {auth}.account a"
    permissions = (
        f"SELECT 'pool', t.account_id FROM {bots}.playerbots_account_type t "
        f"WHERE t.account_type = {ADDCLASS_ACCOUNT_TYPE} "
        f"UNION ALL SELECT 'link', l.linked_account_id FROM {bots}.playerbots_account_links l "
        f"WHERE l.account_id = {mine} "
        f"UNION ALL SELECT 'link', l.account_id FROM {bots}.playerbots_account_links l "
        f"WHERE l.linked_account_id = {mine}"
    )
    return characters, accounts, permissions


def read_characters(raw: str) -> tuple[CharacterRow, ...] | str:
    """The first read's rows, or the sentence saying why they are not rows.

    A row that does not parse is REPORTED and not skipped, which is
    `read_members`' rule for `read_members`' reason: a picker silently one
    character short reads exactly like a picker that never had that character
    to offer.
    """
    out: list[CharacterRow] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 8 or not all(
            field.strip().lstrip("-").isdigit()
            for field in (fields[0], fields[2], fields[3], fields[4], fields[5], fields[6])
        ):
            return f"a character row came back as {line.strip()!r}, which is not a character"
        out.append(
            CharacterRow(
                guid=int(fields[0]),
                name=fields[1].strip(),
                level=int(fields[2]),
                klass=int(fields[3]),
                account=int(fields[4]),
                online=int(fields[5]) != 0,
                guild_id=int(fields[6]),
                guild=fields[7].strip(),
            )
        )
    return tuple(out)


def read_account_names(raw: str) -> dict[int, str]:
    """The second read: account id to account name.

    A row that does not parse is dropped rather than reported, and that is the
    one place in this module where it is: this read decides only what a row
    SAYS about itself, never whether it may be added. A missing name shows as
    `account <id>`, which is still true.
    """
    out: dict[int, str] = {}
    for line in raw.splitlines():
        number, _, name = line.partition("\t")
        if number.strip().isdigit():
            out[int(number)] = name.strip()
    return out


def read_permissions(raw: str) -> tuple[frozenset[int], frozenset[int]]:
    """The third read, split into its two answers: the addclass pool's accounts
    and the accounts linked to this master's."""
    pool: set[int] = set()
    linked: set[int] = set()
    for line in raw.splitlines():
        kind, _, number = line.partition("\t")
        if not number.strip().isdigit():
            continue
        (pool if kind.strip() == "pool" else linked).add(int(number))
    return frozenset(pool), frozenset(linked)


def _cap_note(master: str, max_added: int | None) -> str:
    """What this app can and cannot say about the cap -- always said, never used.

    Round 1's second finding is why this is a note and not a rule. The module
    counts `mgr->GetPlayerbotsCount() + loadingForMaster` (`PlayerbotMgr.cpp:
    125-135`), which is state inside the world process: bots it controls, plus
    bots loading for that account. The group table cannot stand in for it --
    a bot uninvited but still controlled counts there and not here, and another
    player's bots in a shared group count here and not there -- and nothing in
    the measurement record shows ALE exposing the manager or a count to Lua
    (the record quotes exactly two ALE functions, `Player:RunCommand` at
    `PlayerMethods.h:3531-3547` and the global `RunCommand` at
    `GlobalMethods.h:1405-1417`; no step in it read mod-ale's method tables, and
    nothing in this tree pins them). So the cap is not checkable from here, and
    this says which of the two facts is missing.
    """
    if max_added is None:
        return (
            f"This install has no deployed {PLAYERBOTS_CONF}, so its own {MAX_ADDED_BOTS_KEY} "
            "could not be read and no row here is greyed for it. The server still refuses an "
            "add once whatever cap it has is reached, and it says so in the game window."
        )
    return (
        f"The server's added-bot cap ({max_added}) could not be checked from here: the module "
        "counts the bots it controls, which is not what the group table holds. A refusal "
        f"arrives in {master}'s game window."
    )


def _who(row: CharacterRow, accounts: dict[int, str]) -> str:
    """The account this character is on, and its guild where it has one."""
    named = accounts.get(row.account) or f"account {row.account}"
    return f"{named} — {row.guild}" if row.guild else named


def candidates(
    rows: tuple[CharacterRow, ...],
    *,
    master: str,
    accounts: dict[int, str],
    pool: frozenset[int],
    linked: frozenset[int],
    flags: AllowFlags,
) -> tuple[Candidate, ...] | str:
    """Every character, with the rule that lets it in or the one that shuts it out.

    The order is the module's own (`PlayerbotMgr.cpp`, and §6's transcription of
    it), and it is the order because each step changes what the next one may
    say:

    1. **The master's own row is dropped**, `:1314`. Before the online arm and
       not after it: the master is necessarily in the world, so an online-first
       order would grey his own name in his own picker and explain it with a
       rule that is not the reason.
    2. **Online is refused**, `:685-686` -- before any permission rule, so a
       character on the master's own account is refused too while it is logged
       in.
    3. **The four rules in the module's own order**: own account, guild mate,
       addclass pool, linked account (`:101-116`). The first that answers is the
       one the row names; a row that reaches none of them is greyed with all
       four named.
    **Three of those four arms are gated on a conf key** and the gate is INSIDE
    the arm, not in front of the loop -- round 1's first finding, and the shape
    is the module's own: `sameAccount`, `sameGuild` and `linkedAccount` are each
    `allow<X>Bots && <the membership test>`, and the four are then OR'd
    (`:101-116`). So a character on the master's own account with
    `AllowAccountBots = 0` is not refused outright: it falls through and the
    addclass pool or a link may still let it in, exactly as the module lets it.
    A row that reaches no arm says WHICH gate stopped it where one did --
    "on this character's own account, but this server's
    AiPlayerbot.AllowAccountBots is 0" is a different thing to fix from "no rule
    reached it", and an unreadable key is a third thing again: indeterminate,
    greyed, never offered.

    **The cap is not here at all** (round 1's second finding). The module counts
    `mgr->GetPlayerbotsCount() + loadingForMaster` (`:125-135`) -- bots it
    controls, plus bots loading for that account, both of them state inside the
    world process. The group table is a different number in both directions: a
    bot uninvited but still controlled counts for the module and not for the
    table, and another player's bots in a shared group count for the table and
    not for the module. So no row here is allowed or refused on a cap this app
    cannot read; `_cap_note` says so beside the list instead.
    """
    me = next((row for row in rows if row.name == master), None)
    if me is None:
        return (
            f"there is no character called {master} on this server, so which characters it "
            "could add as bots could not be worked out."
        )
    out: list[Candidate] = []
    for row in rows:
        if row.guid == me.guid:
            continue
        seat = _who(row, accounts)
        if row.online:
            out.append(
                Candidate(row.name, row.level, row.klass, seat, refused_because=REFUSED_ONLINE)
            )
            continue
        rule = ""
        # The arms a membership test matched and a conf key then shut, in the
        # module's own order. Collected rather than returned from, because the
        # arms are OR'd: a flag that is off ends that arm and not the row.
        gated: list[tuple[str, str, bool | None]] = []
        if row.account == me.account:
            if flags.account:
                rule = ALLOWED_SAME_ACCOUNT
            else:
                gated.append((ALLOWED_SAME_ACCOUNT, ALLOW_ACCOUNT_KEY, flags.account))
        if not rule and me.guild_id and row.guild_id == me.guild_id:
            if flags.guild:
                rule = ALLOWED_GUILD
            else:
                gated.append((ALLOWED_GUILD, ALLOW_GUILD_KEY, flags.guild))
        if not rule and row.account in pool:
            # Rule 3 has no flag: `IsAddclassBot` is pure cache membership
            # (`RandomPlayerbotMgr.cpp:2154-2175`) and nothing gates it.
            rule = ALLOWED_POOL
        if not rule and row.account in linked:
            if flags.linked:
                rule = ALLOWED_LINK
            else:
                gated.append((ALLOWED_LINK, ALLOW_LINKED_KEY, flags.linked))
        if rule:
            out.append(Candidate(row.name, row.level, row.klass, seat, allowed_by=rule))
            continue
        out.append(
            Candidate(row.name, row.level, row.klass, seat, refused_because=_gated_reason(gated))
        )
    return tuple(out)


def _gated_reason(gated: list[tuple[str, str, bool | None]]) -> str:
    """Why a row no arm reached is greyed: the first gate that shut it, or none.

    Unknown beats off. A row with one arm switched off and another whose key
    could not be read is a row nobody can say anything certain about, and the
    sentence that names the unreadable key is the one that sends a person to
    the thing they can actually settle.
    """
    for rule, key, state in gated:
        if state is None:
            return FLAG_UNREADABLE % (rule, key)
    for rule, key, _ in gated:
        return FLAG_OFF % (rule, key)
    return REFUSED_NO_RULE


@dataclass(frozen=True)
class Picker:
    """What "Add this character" shows: the rows, the cap, and what could not be read.

    `problem` is a read that did not happen and it empties the list, because a
    picker drawn from a failed read is a shorter list with nothing saying so --
    the same reason `read_characters` reports an unparsable row instead of
    skipping it. `note` is what is missing around the rows rather than instead
    of them: an unreadable `MaxAddedBots` does not stop anybody choosing, it
    only means this app cannot say when the server will start refusing.
    """

    rows: tuple[Candidate, ...] = ()
    added: int = 0
    """How many bots the GROUP TABLE holds for this master, and nothing decides
    on it (round 1's second finding).

    It is not the module's own added-bot count and cannot be made into one --
    `_cap_note` says which number is missing. Kept because the read was made
    and the panel's summary line is drawn from the same reading; used by no
    rule, no gate and no sentence about the cap."""
    max_added: int | None = None
    note: str = ""
    problem: str = ""


# -- T26: linking two accounts ---------------------------------------------
#
# The friends-and-family route, and the only write this feature makes.
# `.playerbots account link <accountName> <securityKey>` is the in-game half
# (`PlayerbotCommandScript.cpp:29-32`, `SEC_PLAYER, Console::No` like the rest):
# it SHA-256s the key against `playerbots_account_keys.security_key` and, on a
# match, writes BOTH directions into `acore_playerbots.playerbots_account_links`
# with `INSERT IGNORE` (`PlayerbotMgr.cpp:1840-1885`). The check that reads it
# back is one `SELECT 1` (`IsAccountLinked`, `:191-196`) and nothing in it cares
# which end wrote the row -- measured on `yulon-ubuntu2` 2026-09-10, §2 of the
# record. So the launcher can open this route itself, and asking two people to
# type a shared secret at each other in game is not the only way.

LINKS_TABLE = "playerbots_account_links"
KEYS_TABLE = "playerbots_account_keys"
"""The two-column link table and the key table beside it. Both empty on the
measured box. This app writes the first and never the second: a key is what the
in-game command needs to trust a stranger, and a person pressing a button in
their own launcher has already answered that question."""

MAX_ACCOUNT_NAME = 32
"""`account.username` is `varchar(32)` (§6's column table)."""

ACCOUNT_SHAPE = re.compile(r"^[A-Za-z0-9._@-]{1,32}$")
"""What may go into the quoted literal an account name is looked up by.

A refusal and not an escape, `dbreads._SAFE_PREFIX`'s rule: the set of
characters a real account name needs does not include a quote, a semicolon or a
backslash, and a name that carries one is answered with "I will not use this"
rather than with an escaping rule that has to be right on two SQL modes."""


@dataclass(frozen=True)
class AccountLink:
    """What one press of "Link an account" did -- or, on the first press, would do.

    One shape for both because they are the same question asked twice: the
    confirmation has to name exactly what the write will name, and two classes
    is two places for those sentences to drift apart. `linked` is what tells
    them apart, and it is the row being written and read back as `INSERT
    IGNORE`'s own effect -- never the press being made.
    """

    linked: bool
    master_account: str
    other_account: str
    sentence: str
    blocker: str = ""
    """Set when nothing was written and nothing could be: this module's word."""


def account_of_sql(entry: CatalogEntry, master: str) -> str:
    """The account a character is on, id and name, in one cross-schema read.

    `characters.account` is in `acore_characters` and the name is in
    `acore_auth`; the sub-select spans them the way `candidates_sql`'s third
    read does, because two reads for one answer is two ways to be half-answered.
    """
    _check_name(master, MASTER)
    schemas = entry.schema_map()
    return (
        f"SELECT a.id, a.username FROM {schemas['auth']}.account a "
        f"WHERE a.id = (SELECT c.account FROM {schemas['characters']}.characters c "
        f"WHERE c.name = '{master}' LIMIT 1) LIMIT 1"
    )


def account_named_sql(entry: CatalogEntry, username: str) -> str:
    """One account by name, returning the name AS STORED.

    The comparison is the column's own collation (case-insensitive on both
    cores' shipped schemas), so a person may type what they remember; the
    sentence then names the account the way the server spells it, which is what
    they will see in game.
    """
    return (
        f"SELECT a.id, a.username FROM {entry.schema_map()['auth']}.account a "
        f"WHERE a.username = '{username}' LIMIT 1"
    )


def account_link_read_sql(entry: CatalogEntry, *, master_account: int, other_account: int) -> str:
    """Which of the two directional rows are there, if either.

    Both columns and not `SELECT 1` (round 1's third finding): one row is a
    third state, not "already linked". The module's own command writes both
    (`PlayerbotMgr.cpp:1840-1885`), so a table holding one of them is a half
    link -- and reading it as done is how a pair stays half linked for good.
    """
    bots = entry.schema_map()["playerbots"]
    return (
        f"SELECT account_id, linked_account_id FROM {bots}.{LINKS_TABLE} "
        f"WHERE (account_id = {int(master_account)} "
        f"AND linked_account_id = {int(other_account)}) "
        f"OR (account_id = {int(other_account)} "
        f"AND linked_account_id = {int(master_account)})"
    )


def link_directions(raw: str, *, master_account: int, other_account: int) -> tuple[bool, bool]:
    """That read folded into the two directions: (master->other, other->master)."""
    pairs = set()
    for line in raw.splitlines():
        left, _, right = line.partition("\t")
        if left.strip().isdigit() and right.strip().isdigit():
            pairs.add((int(left), int(right)))
    return (master_account, other_account) in pairs, (other_account, master_account) in pairs


def link_account_sql(entry: CatalogEntry, *, master_account: int, other_account: int) -> str:
    """The write: both directions, `INSERT IGNORE`, exactly as the module's own
    command writes them (`PlayerbotMgr.cpp:1840-1885`).

    Ids and not names, though the ticket named it after the name: the three
    refusals this write is gated by -- an account this server does not have,
    the master's own, a pair already linked -- are decided by reads that can say
    WHICH of them happened. An `INSERT ... SELECT` that resolved the name in the
    statement would write nothing for an unknown name and report success, and a
    write that reports a row it did not make is the failure this whole feature
    is a correction of.
    """
    bots = entry.schema_map()["playerbots"]
    return (
        f"INSERT IGNORE INTO {bots}.{LINKS_TABLE} (account_id, linked_account_id) VALUES "
        f"({int(master_account)}, {int(other_account)}), "
        f"({int(other_account)}, {int(master_account)});"
    )


WROTE_TAG = "wrote"
ROW_TAG = "row"
"""The two result sets of the link transaction, each tagged by a literal first
column so one stdout can be told apart into two answers.

`mysql --batch --skip-column-names` concatenates result sets with nothing
between them, and "how many rows did the insert write" and "which rows are
there now" are both single-column-ish answers that would otherwise be
indistinguishable lines."""


def link_transaction_sql(entry: CatalogEntry, *, master_account: int, other_account: int) -> str:
    """The whole write as one script: transaction, insert, count, readback, commit.

    Round 2, must-fix 3. The old shape was check-then-write with no readback,
    and it lied in two ways: a concurrent insert made `INSERT IGNORE` write
    nothing while the press still said "are linked", and a table holding one of
    the two rows was read as already linked and never repaired.

    One script because one `DockerSql` call is one `docker exec` is one mysql
    session, and a transaction that spans two of them is not a transaction. In
    order:

    * `START TRANSACTION` / `COMMIT` -- both rows or neither, so a press that
      dies half way leaves nothing to repair;
    * the `INSERT IGNORE` of both directions, which is the module's own
      statement (`PlayerbotMgr.cpp:1840-1885`);
    * `SELECT ROW_COUNT()` -- how many rows THIS insert actually wrote, which is
      the only thing that can tell "this press wrote them" from "they were
      already there by the time it ran". `ROW_COUNT()` is session state, so it
      is readable here and nowhere else;
    * the readback of both directional rows, inside the transaction, so
      `linked=True` is a row that was seen and not an insert that was sent.
    """
    return (
        "START TRANSACTION; "
        + link_account_sql(entry, master_account=master_account, other_account=other_account)
        + f" SELECT '{WROTE_TAG}', ROW_COUNT(); "
        + f"SELECT '{ROW_TAG}', account_id, linked_account_id "
        + f"FROM {entry.schema_map()['playerbots']}.{LINKS_TABLE} "
        + f"WHERE (account_id = {int(master_account)} "
        + f"AND linked_account_id = {int(other_account)}) "
        + f"OR (account_id = {int(other_account)} "
        + f"AND linked_account_id = {int(master_account)}); "
        + "COMMIT;"
    )


def read_link_write(raw: str) -> tuple[int, frozenset[tuple[int, int]]] | str:
    """The transaction's own output: rows written, and the rows there now.

    A line that parses as neither is REPORTED rather than skipped -- the same
    rule `read_members` and `read_characters` keep, and it matters more here
    than anywhere else in this module: this output is the only evidence the
    write happened, and dropping a line one is `linked=True` said over a
    reading nobody understood.
    """
    wrote: int | None = None
    pairs: set[tuple[int, int]] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("\t")]
        if fields[0] == WROTE_TAG and len(fields) == 2 and fields[1].isdigit():
            wrote = int(fields[1])
            continue
        if fields[0] == ROW_TAG and len(fields) == 3 and all(f.isdigit() for f in fields[1:]):
            pairs.add((int(fields[1]), int(fields[2])))
            continue
        return f"the link write answered {line.strip()!r}, which is not what it was asked for"
    if wrote is None:
        return "the link write did not say how many rows it wrote, so nothing about it is known"
    return wrote, frozenset(pairs)


def _link_confirmation(master_account: str, other_account: str) -> str:
    return (
        f"This links the account {master_account} to the account {other_account}, both ways. "
        f"From then on this server lets {master_account} add every character of "
        f"{other_account} as a bot, and lets {other_account} add every character of "
        f"{master_account}. It is two rows in {LINKS_TABLE} and nothing else; press again to "
        "write them."
    )


def _link_written(master_account: str, other_account: str, wrote: int, half: bool) -> str:
    """What the readback saw, and which press put it there.

    Three endings and the row count decides between them (round 2's must-fix 3):
    a press that wrote both rows, a press that wrote the ONE that was missing
    from a half-linked pair, and a press whose insert wrote nothing because
    somebody else's had already landed between this app's pre-read and its
    write. All three end with the two rows in the table, and only the first may
    say this press put them there.
    """
    if wrote == 0:
        did = (
            f"{master_account} and {other_account} were already linked by the time this press "
            "ran: both rows were there and this one wrote neither."
        )
    elif half:
        did = (
            f"{master_account} and {other_account} were linked one way only, and this press "
            f"wrote the missing row. A link the module trusts is both rows, and both are there "
            "now."
        )
    else:
        did = f"{master_account} and {other_account} are linked: this press wrote both rows."
    return (
        f"{did} This server will now let either account add every character of the other as a "
        f"bot -- two rows in {LINKS_TABLE}, and nothing else about either account changed."
    )


def _link_unwritten(master_account: str, other_account: str, said: str) -> str:
    return (
        f"{master_account} and {other_account} are NOT linked: {said} The module trusts a link "
        f"only when both rows of {LINKS_TABLE} are there, and this press could not show that "
        "they are."
    )


# -- the tab's seam --------------------------------------------------------


class SqlWriter(Protocol):
    """The ONE write this feature makes, and a type that says exactly that.

    Not `dbreads.SqlReader` widened, and not `apply.DockerSql` imported: the
    read seam is deliberately unable to reach the write half ("what is not in
    the type cannot be called through it", `dbreads.SqlReader`), and widening it
    for a link table would take that guarantee away from every read in this
    module. So the write arrives through a seam of its own, held in its own
    attribute, `None` on an install that has no route to one -- and exactly one
    function in this module holds it.

    **The method is `query` and that is round 2's change** (the lead's
    rejection, must-fix 3). The write has to be transactional and read back in
    the SAME database session -- `START TRANSACTION`, the `INSERT IGNORE`, the
    row count, the readback, `COMMIT` -- and `apply.DockerSql` runs one
    statement per `docker exec`, so the whole script goes out as one call and
    the answer comes back on its stdout. `run_statement()` discards stdout and
    could not carry the readback; `query()` is the returning runner the lead
    asked for, and structural typing means it must be spelled the way the real
    object spells it.

    **The boundary still holds**, and it is worth saying how: it is not the
    METHOD NAME that keeps this module's reads from writing, it is which object
    each of them is called on. Every picker read goes through `self._sql`, whose
    type is `dbreads.SqlReader`; nothing in this module can send a statement
    through that seam and have it write, because `DockerSql.query` is one
    statement and `SqlReader` is what the picker holds. The link script goes
    through `self._link_writer`, which is `None` unless a caller wired one, is
    used by one function, and is what the ledger's own paragraph is written
    about.
    """

    def query(self, db: Db, statement: str) -> str: ...


SqlReader = dbreads.SqlReader
"""The database read seam, and it is `dbreads`' own rather than a second one.

A Protocol re-declared here with `db: str` looks identical and is not: the real
reader's `db` is a `Literal` of the five schema aliases, and a Protocol widening
it to `str` is not satisfied by the object every caller actually has -- mypy
said so on all three platforms, and the wrong fix was to widen the real one.
The read half is all that is wanted (`run_statement` is not reachable through
it), which is exactly what `dbreads.SqlReader` already is."""


ALTBOT_STORE = "party-altbots.json"
"""The app's own record of the characters it added as bots through `add_named`.

**It exists because the server keeps no such record anywhere readable**, which
was measured rather than assumed -- `group_rows_sql`'s `also` paragraph carries
the readings and the capture. Without it the one control this ticket ships
contradicts itself: the panel says "X joined the party." and, under it, "This
character's party has no bots in it yet.", because the party it draws is
marker-filtered and X is on a person's account.

It lives under `platform.config_dir()` and not in the server folder: it is this
app's bookkeeping, not the install's, so an uninstall has nothing extra to purge
and a server directory nobody gave us gains no dotfile."""


def altbot_store_path(config_dir: Path | None = None) -> Path:
    """Where `AltbotMemory` keeps its file. `dbsecret.py:127`'s shape."""
    root = config_dir if config_dir is not None else platform.config_dir()
    return root / ALTBOT_STORE


class AltbotMemory:
    """Which characters this app added as bots, per install and per master.

    A name goes in when `add_named` has SEEN it join the group table -- never
    when the command was merely issued -- and comes out when the group table no
    longer holds it or when the app dismisses it. So it is a record of what this
    app believes is standing in a party right now, pruned against the world on
    every successful read, and not a history.

    `path=None` is a memory that lives as long as the object, which is what the
    tests use and what an install with no config directory falls back to. A file
    that cannot be read or written is an EMPTY record and never an exception:
    the panel has to draw either way, and the cost of a lost record is a party
    row the marker cannot see -- the same place this started, not worse.
    """

    def __init__(self, path: Path | None, install_id: str = "") -> None:
        self._path = path
        self._install = install_id or "unknown"
        self._loose: dict[str, list[str]] = {}

    def _table(self) -> dict[str, dict[str, list[str]]]:
        if self._path is None:
            return {self._install: self._loose}
        try:
            raw = json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning(f"could not read {self._path}: {exc}")
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, table: dict[str, dict[str, list[str]]], masters: dict[str, list[str]]) -> None:
        if self._path is None:
            self._loose = masters
            return
        table[self._install] = masters
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(table, indent=2, sort_keys=True))
        except OSError as exc:
            logger.warning(f"could not write {self._path}: {exc}")

    def _masters(self, table: dict[str, dict[str, list[str]]]) -> dict[str, list[str]]:
        mine = table.get(self._install)
        return dict(mine) if isinstance(mine, dict) else {}

    def names(self, master: str) -> tuple[str, ...]:
        """The characters this app added to `master`'s party, in the order kept."""
        held = self._masters(self._table()).get(master)
        return tuple(name for name in held if isinstance(name, str)) if held else ()

    def remember(self, master: str, name: str) -> None:
        table = self._table()
        masters = self._masters(table)
        held = list(masters.get(master, ()))
        if name not in held:
            held.append(name)
        masters[master] = held
        self._save(table, masters)

    def keep_only(self, master: str, present: Iterable[str]) -> None:
        """Drop every remembered name that is not in `present`.

        Called with the group table's own rows after a read that SUCCEEDED, and
        never after one that failed: a read that could not be done says nothing
        about who is in the party, and pruning on it would throw the record away
        on a database hiccup.
        """
        keeping = set(present)
        held = self.names(master)
        kept = [name for name in held if name in keeping]
        if len(kept) == len(held):
            return
        table = self._table()
        masters = self._masters(table)
        if kept:
            masters[master] = kept
        else:
            masters.pop(master, None)
        self._save(table, masters)


@dataclass(frozen=True)
class PartyState:
    """What the My Party group shows: the rows, or which precondition stopped it.

    `ready` is never a default. `checks` carries ALL nine rather than only the
    blocking one, because a person fixing this wants to see how far down the
    list they have got — but `blocker` is the FIRST unmet one, because that is
    the only one worth acting on next.
    """

    ready: bool
    blocker: str
    checks: tuple[Precondition, ...]
    members: tuple[Member, ...] = ()
    problem: str = ""


class InstallParty:
    """One install's My Party, as the tab presses it.

    Reads go to the database and every write goes to the SERVER over the
    command channel, which is owner answer 7 and also the only route there is:
    `.playerbots bot addclass` resolves its master from a live session, so
    there is no SQL spelling of adding a bot to a party.

    **Nothing is pressed until the server has said the bridge is there.** The
    facts are re-read on every press rather than cached at start-up: the world
    can be restarted, the module reinstalled and `mod_ale.conf` edited while
    the tab is open, and a control that presses on a five-minute-old reading is
    the 2026-08-20 failure with a delay in front of it.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        sql: SqlReader,
        channel_for_saved: Callable[[], object | None],
        container: str,
        world_running: Callable[[], bool],
        wsl_distro: str | None = None,
        engine: Callable[[], BinaryRead] | None = None,
        level_setter: LevelSetter | None = None,
        link_writer: SqlWriter | None = None,
        altbots: AltbotMemory | None = None,
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self._sql = sql
        self._channel_for_saved = channel_for_saved
        self._container = container
        self._world_running = world_running
        self._wsl_distro = wsl_distro
        # A seam for the same reason `world_running` is one: reading it costs a
        # `docker exec`, and the tests for every sentence this object says must
        # not be tests that need a container. The default is the real reader.
        self._engine = engine or (lambda: read_engine_in_binary(container, wsl_distro=wsl_distro))
        # T26. `None` is an install with no route to the one write this feature
        # makes, and the control says so rather than failing at the press. It is
        # the same `DockerSql` the reads go through where the factory has one;
        # a second seam beside `sql` rather than a wider `sql`, so the read half
        # keeps the guarantee its own type makes.
        self._link_writer = link_writer
        # T26 round 2. The record `members()` unions with the bot marker, and
        # the reason it is a seam is the reason `link_writer` is one: the tests
        # for every sentence this object says must not be tests that need a
        # config directory. `None` is a record that lives as long as this
        # object, which is honest rather than convenient -- the panel is rebuilt
        # on every tab switch, so a factory that forgets to wire the file loses
        # the party list the moment the tab is left. `controller_view` wires it.
        self._altbots = altbots or AltbotMemory(None)
        # 8.6/T5. The level goes through the Characters tab's own seam, built
        # here over the same four things it would be built over in the factory —
        # a bot is a character, and a second `character level` would be a second
        # place to fix the day a fork renames it. It is also the half that
        # already refuses the trees with no such command, in the entry's own
        # measured words (`play._no_set_level`), which is a sentence this module
        # would otherwise have to write about somebody else's server.
        self.level_setter: LevelSetter = (
            level_setter
            or play.InstallPlay(
                entry, server_dir, sql=sql, channel_for_saved=channel_for_saved
            ).set_level
        )

    @staticmethod
    def for_entry_is_possible(entry: CatalogEntry) -> bool:
        """Whether this tree has measured what My Party would need.

        Two things, and neither is optional: a bot marker (the group read has
        to know which rows are bots) and the `mod-ale` route itself, which is
        an AzerothCore module. A CMaNGOS tree gets no My Party control rather
        than a control that sends AzerothCore's commands at it.
        """
        return entry.observability is not None and entry.id == "wow-wotlk"

    # -- reads ---------------------------------------------------------------

    def facts(self) -> Facts:
        """The nine facts, read fresh, and the two subprocesses they cost.

        Ordered so the expensive halves are skipped once something above them
        has already decided the answer: a stopped world is asked nothing, and a
        server folder that is not there is not grepped for a Lua engine.
        """
        if not self.server_dir.is_dir() or not self._world_running():
            return read_facts(
                self.server_dir,
                world_running=self._world_running(),
                engine=BinaryRead(None, _unreadable("the world server is not running")),
                probe=Probe(None, "the server was not asked: it is not running", ""),
            )
        return read_facts(
            self.server_dir,
            world_running=True,
            engine=self._engine(),
            probe=self._probe(),
        )

    def _probe(self) -> Probe:
        channel = self._channel_for_saved()
        if channel is None:
            return Probe(
                None,
                "the bridge could not be asked about: this install has no command channel set "
                "up yet, and My Party has no other way to reach the server.",
                "",
            )
        try:
            answer = channel.send(PROBE_COMMAND)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - one answer for every seam failure
            logger.warning(f"could not ask {self.entry.id} about the bridge: {exc}")
            return Probe(None, f"the bridge could not be asked about: {exc}", "")
        return read_probe(answer)

    def state(self, master: str) -> PartyState:
        """The group, or the first precondition that is not met.

        The rows are read only when everything above them is met. A group table
        drawn under an unmet precondition would be an empty list beside a
        sentence saying why — and an empty list is what a working party with no
        bots in it looks like.
        """
        facts = self.facts()
        checks = preconditions(facts)
        stop = blocker(facts)
        if stop is not None:
            return PartyState(False, stop, checks)
        rows = self.members(master)
        if isinstance(rows, str):
            return PartyState(True, "", checks, problem=rows)
        return PartyState(True, "", checks, members=rows)

    def online_guid(self, name: str) -> int | None:
        """This character's guid while it is logged in, or `None`.

        `online = 1` is part of the question and not a detail: the bridge
        resolves its master with `GetPlayerByName` and prints
        "player not found/offline" for a character who is not there, which
        arrives back over the channel as a command that succeeded and a bot
        that never comes.
        """
        if not valid_name(name):
            return None
        chars = self.entry.schema_map()["characters"]
        raw = self._sql.query(
            "characters",
            f"SELECT guid FROM {chars}.characters WHERE name = '{name}' AND online = 1 LIMIT 1;",
        )
        text = raw.strip()
        return int(text) if text.isdigit() else None

    def members(self, master: str) -> tuple[Member, ...] | str:
        """The bots in `master`'s party, or the sentence saying why not.

        **The marker's bots UNION the characters this app added through
        `add_named`** (T26 round 2). One set, and every caller reads this one:
        `state()` draws it, `remove_all` confirms against it, `remove()` reads
        its ground from it and `candidates()` counts it. Round 1 shipped a
        control whose party list could not contain what the control had just
        added, so the panel said "X joined the party." and "This character's
        party has no bots in it yet." in the same breath, and `remove_all`
        handed `dismiss_all` an empty list while the altbots stood in the party.

        A human in the party is in neither arm and is never in this list --
        `group_rows_sql`'s two clauses are "on a bot account" and "a name this
        app put there", and a person is neither.
        """
        answer = dbreads.resolve_marker(self.entry, self.server_dir)
        if answer.marker is None:
            return answer.problem or "this install's bot marker is unreadable"
        guid = self.online_guid(master)
        if guid is None:
            return _not_online(master)
        try:
            raw = self._sql.query(
                "characters",
                group_rows_sql(
                    self.entry,
                    answer.marker,
                    master_guid=guid,
                    also=self._altbots.names(master),
                )
                + ";",
            )
        except Exception as exc:  # noqa: BLE001 - one answer for every seam failure
            logger.warning(f"could not read {master}'s party: {exc}")
            return f"could not read this character's party: {exc}"
        rows = read_members(raw)
        if not isinstance(rows, str):
            # Only after a read that succeeded: `keep_only`'s docstring says why.
            self._altbots.keep_only(master, [row.name for row in rows])
        return rows

    def remembered_altbots(self, master: str) -> tuple[str, ...]:
        """The names this app is keeping for `master` -- the record, not the party."""
        return self._altbots.names(master)

    def party_members(self, master: str) -> tuple[Member, ...] | str:
        """Everyone in `master`'s party except `master`, bot marker or not.

        The read `add_named` polls, and `members()`'s docstring says why it is
        not that one: this route adds characters on people's accounts, which the
        bot marker cannot recognise and must not have to. `members()` stays as
        it is -- it is what the panel draws and what `remove_all` confirms, and a
        party list that offered to dismiss a real person's character would be a
        worse defect than the one this fixes.
        """
        guid = self.online_guid(master)
        if guid is None:
            return _not_online(master)
        try:
            raw = self._sql.query("characters", party_rows_sql(self.entry, master_guid=guid) + ";")
        except Exception as exc:  # noqa: BLE001 - one answer for every seam failure
            logger.warning(f"could not read {master}'s party: {exc}")
            return f"could not read this character's party: {exc}"
        return read_members(raw)

    # -- writes --------------------------------------------------------------

    def specs(self, klass: str) -> tuple[str, ...]:
        """The premade specs this install lists for `klass`, in the module's order.

        Filtered by `valid_spec`, which the conf's own values are NOT guaranteed
        to pass: the file is hand-editable and raw-writable from the Modules
        editor. The prior art offered every conf name verbatim and then had a
        validator refuse some of them (`50-party.sh:208-215`'s own note); a
        picker that cannot offer a name this app would refuse to send is the
        version of that fix with nothing left to disagree about.
        """
        number = BOT_CLASS_IDS.get(klass.strip().lower())
        if number is None:
            return ()
        listed = spec_names(self.server_dir).get(number, ())
        return tuple(name for name in listed if valid_spec(name))

    def max_level(self) -> int | None:
        """This install's own `MaxPlayerLevel`, or `None` for "could not be read"."""
        return max_player_level(self.server_dir)

    def add(
        self,
        master: str,
        klass: str,
        *,
        gender: str = "",
        spec: str = "",
        level: int | None = None,
    ) -> Addition:
        """Add one bot of `klass` to `master`'s party, over the channel."""
        facts = self.facts()
        stop = blocker(facts)
        if stop is not None:
            return Addition(False, False, None, False, False, stop, blocker=stop)
        if self.online_guid(master) is None:
            return Addition(False, False, None, False, False, _not_online(master))
        send = self._send_or_none()
        if send is None:
            return Addition(False, False, None, False, False, _no_channel())
        return add_bot(
            facts=facts,
            player=master,
            klass=klass,
            gender=gender,
            spec=spec,
            specs=self.specs(klass),
            level=level,
            max_level=self.max_level(),
            set_level=self.level_setter,
            send=send,
            members=lambda: _rows_only(self.members(master)),
        )

    def remove_all(self, master: str, confirmed: tuple[int, ...]) -> MassDismissal:
        """Send `confirmed` away — and nothing else, whatever the party holds now.

        `confirmed` is the guids a person agreed to, and it is required rather
        than optional because it is the whole point of the method. Round 2's
        must-fix: a caller that checks its own screen and then asks for "the
        party" has guarded the wrong side of the door — the two presses of a
        confirmation are seconds apart, a bot can join in between, and the
        screen has nothing to say about it. So this re-reads the group table and
        refuses unless the fresh set is EXACTLY the confirmed one, in both
        directions: a party that gained a bot is not the party that was agreed
        to, and neither is one that lost one. A subset that "obviously" still
        works is how a confirmation quietly becomes a suggestion.

        Guids and not names, for the reason the group read selects them: a name
        is what a display shows, and `group_member` keys on the guid.

        The list still comes from the table rather than from the caller — the
        bot manager logs bots in and out on a timer, and acting on a list read
        minutes ago is 8.4d's finding again. What the caller supplies is what it
        is allowed to act on, not what is there.

        **T21, round 2's must-fix 1.** Round 1 unfolded `InstallParty.remove`
        but left this one wired through `_rows_only`, so a database outage
        during a batch dismiss still folded every failed post-uninvite read
        into `()` and reported the whole batch `removed=True` — the false
        success this ticket exists to delete, surviving on the one path a
        batch press actually takes. `dismiss_all` gets the unfolded read here
        too.
        """
        send = self._send_or_none()
        if send is None:
            return MassDismissal(0, (), _no_channel(), blocker=_no_channel())
        rows = self.members(master)
        if isinstance(rows, str):
            return MassDismissal(0, (), rows, blocker=rows)
        if {row.guid for row in rows} != set(confirmed):
            moved = _not_the_confirmed_party(master, rows, confirmed)
            return MassDismissal(0, (), moved, blocker=moved)
        return dismiss_all(
            player=master,
            bots=tuple(row.name for row in rows),
            send=send,
            members=lambda: self.members(master),
        )

    def remove(self, master: str, bot: str) -> Dismissal:
        """Uninvite `bot` from `master`'s party and read the group table back.

        Unfolded — `self.members(master)` reaches `dismiss()`'s poll as the
        `tuple[Member, ...] | str` it actually is, not `_rows_only`'s "empty
        party" reading of a failure (T21): this poll needs to tell a row that
        cleared apart from a table it could not read.

        **The party is read BEFORE anything is sent** (T26 round 2, must-fix 3).
        `dismiss()` polls for a name to VANISH, so a name that was not in the
        read it polls comes back `removed=True` on the first read having watched
        nothing at all -- the false-success shape T21 exists to delete, which is
        what round 1's own teardown printed for every altbot it dismissed. The
        ground read makes the readback mean something: either the character is
        there and the poll watches it go, or it is not and nothing is sent. It
        is `add_named`'s "already in the party" refusal, from the other end.
        """
        send = self._send_or_none()
        if send is None:
            return Dismissal(False, False, _no_channel())
        standing = self.members(master)
        if isinstance(standing, str):
            return Dismissal(False, False, standing, bot=bot, unreadable=True)
        if all(row.name != bot for row in standing):
            stop = (
                f"{bot} is not in {master}'s party, so nothing was sent. A dismissal is read "
                "back by watching the row leave the group table, and there is no row to watch."
            )
            return Dismissal(False, False, stop, bot=bot)
        # Nothing is "forgotten" here on purpose: `dismiss()`'s poll goes
        # through `members()`, whose prune drops every remembered name the group
        # table no longer holds -- so the character this call removes is out of
        # the record by the time the poll has seen it leave. A second call that
        # did the same thing would be a line no mutation could kill.
        return dismiss(
            player=master,
            bot=bot,
            send=send,
            members=lambda: self.members(master),
        )

    # -- T26: the named add, the picker and the link -------------------------

    def candidates(self, master: str) -> Picker:
        """Which characters this server would let `master` add, and why not.

        Four reads: the group table (which also refuses a master who is not
        logged in), then the picker's own three. `problem` is any of them
        failing, and it empties the list rather than shortening it -- a picker
        missing the rows a failed read would have held is a picker that quietly
        offers less than the server does.

        Two conf readings ride with them, both off the DEPLOYED
        `playerbots.conf` and both three-valued: the three allow-flags that gate
        three of the four rules (`allow_flags`), and the cap (`max_added_bots`).
        The flags decide rows; the cap decides nothing and is reported --
        `_cap_note` says why.
        """
        rows = self.members(master)
        if isinstance(rows, str):
            return Picker(problem=rows)
        cap = max_added_bots(self.server_dir)
        try:
            statements = candidates_sql(self.entry, master)
        except BadRequest as exc:
            return Picker(problem=str(exc), added=len(rows), max_added=cap)
        try:
            raw = self._sql.query("characters", statements[0] + ";")
            named = self._sql.query("auth", statements[1] + ";")
            allowed = self._sql.query("playerbots", statements[2] + ";")
        except Exception as exc:  # noqa: BLE001 - one answer for every seam failure
            logger.warning(f"could not read what {master} may add: {exc}")
            return Picker(
                problem=f"could not read which characters could be added: {exc}",
                added=len(rows),
                max_added=cap,
            )
        parsed = read_characters(raw)
        if isinstance(parsed, str):
            return Picker(problem=parsed, added=len(rows), max_added=cap)
        pool, linked = read_permissions(allowed)
        offered = candidates(
            parsed,
            master=master,
            accounts=read_account_names(named),
            pool=pool,
            linked=linked,
            flags=allow_flags(self.server_dir),
        )
        if isinstance(offered, str):
            return Picker(problem=offered, added=len(rows), max_added=cap)
        return Picker(offered, len(rows), cap, note=_cap_note(master, cap))

    def add_named(self, master: str, name: str) -> NamedAddition:
        """Add the existing character `name` to `master`'s party, over the channel.

        The same three refusals `add()` makes before it touches anything -- the
        preconditions, a master who is not logged in, no command channel -- and
        then the module-level `add_named()` below does the press and the poll.
        """
        facts = self.facts()
        stop = blocker(facts)
        if stop is not None:
            return NamedAddition(False, False, name, stop, blocker=stop)
        if self.online_guid(master) is None:
            gone = _not_online(master)
            return NamedAddition(False, False, name, gone, blocker=gone)
        send = self._send_or_none()
        if send is None:
            return NamedAddition(False, False, name, _no_channel(), blocker=_no_channel())
        # The module-level function, not this method: the press and the poll are
        # testable without an install, exactly as `add_bot` and `dismiss` are.
        result = add_named(
            master=master,
            name=name,
            send=send,
            # `party_members`, not `members`: the poll asks whether THIS named
            # character arrived, and there is nothing remembered about it yet --
            # that is what this press decides. `members()` answers the other
            # question, "what is standing in the party", and reads the union.
            members=lambda: self.party_members(master),
        )
        if result.joined:
            # Remembered on the JOIN and never on the send: the command being
            # issued says nothing, and every one of the module's refusals goes
            # to the master's game window (`add_named`'s docstring).
            self._altbots.remember(master, result.name)
        return result

    def link_plan(self, master: str, account: str) -> AccountLink:
        """What "Link an account" WOULD write, named in full, writing nothing."""
        found = self._resolve_link(master, account)
        if isinstance(found, AccountLink):
            return found
        (_, mine), (_, theirs), half = found
        said = _link_confirmation(mine, theirs)
        if half:
            said = (
                f"{mine} and {theirs} are linked one way only on this server, which is not a "
                f"link the module trusts. " + said
            )
        return AccountLink(False, mine, theirs, said)

    def link_account(self, master: str, account: str) -> AccountLink:
        """Write the two link rows, after the three refusals that stand in front.

        **This writes while the world is running, and that is the measurement's
        answer rather than an omission.** Owner answer 7 keeps this app out of
        `characters` and `world` under a live server, and `apply.WORLD_HELD_DBS`
        extends that to `playerbots` for the applier's bulk SQL. This one table
        is different in the three ways that argument turns on: the module itself
        writes it from inside the running world (`.playerbots account link`,
        `PlayerbotMgr.cpp:1840-1885`), it reads it back with a fresh `SELECT 1`
        on every add (`IsAccountLinked`, `:191-196`) so there is no cached copy
        to fall out of step with, and an `INSERT IGNORE` of two integer pairs
        neither updates nor deletes anything anybody owns. The write ledger's
        row carries the same argument and names it as an open question for the
        owner rather than settled by this ticket.
        """
        if self._link_writer is None:
            stop = (
                "this install has no route to write the account link table, so nothing was "
                "written. The two of you can still do it in game with .playerbots account "
                "setKey and .playerbots account link."
            )
            return AccountLink(False, "", "", stop, blocker=stop)
        found = self._resolve_link(master, account)
        if isinstance(found, AccountLink):
            return found
        (my_id, mine), (their_id, theirs), half = found
        script = link_transaction_sql(self.entry, master_account=my_id, other_account=their_id)
        try:
            answered = self._link_writer.query("playerbots", script)
        except Exception as exc:  # noqa: BLE001 - one answer for every seam failure
            logger.warning(f"could not link {mine} and {theirs}: {exc}")
            return AccountLink(
                False,
                mine,
                theirs,
                _link_unwritten(mine, theirs, f"the write could not be run ({exc})."),
            )
        read = read_link_write(answered)
        if isinstance(read, str):
            return AccountLink(False, mine, theirs, _link_unwritten(mine, theirs, f"{read}."))
        wrote, pairs = read
        if (my_id, their_id) not in pairs or (their_id, my_id) not in pairs:
            # `linked` is the two rows read back inside the transaction, never
            # the insert being sent -- round 2's must-fix. `INSERT IGNORE`
            # cannot fail loudly, so "it did not raise" says nothing at all.
            return AccountLink(
                False,
                mine,
                theirs,
                _link_unwritten(
                    mine,
                    theirs,
                    f"the write ran and the readback found {len(pairs)} of the two rows.",
                ),
            )
        logger.info(f"linked accounts {mine} and {theirs} for playerbots (rows written: {wrote})")
        return AccountLink(True, mine, theirs, _link_written(mine, theirs, wrote, half))

    def _resolve_link(
        self, master: str, account: str
    ) -> AccountLink | tuple[tuple[int, str], tuple[int, str], bool]:
        """Both accounts by id and by name and the ground under them, or the
        refusal that stops the write.

        The third element is "exactly one of the two rows is already there" --
        a half-linked pair, which is a REPAIR and not a refusal (round 1's third
        finding). Only both rows refuse.

        Shared by the confirmation and the write so the two cannot disagree
        about which accounts they are about -- and re-run by the write, because
        the confirmation was read at the first press and an account can be
        renamed, deleted or linked in between.
        """
        if not ACCOUNT_SHAPE.match(account):
            stop = (
                f"{account!r} is not an account name: it is 1 to {MAX_ACCOUNT_NAME} letters, "
                "digits and . _ - @, because it goes into a query this app sends to the "
                "database. Nothing was read and nothing was written."
            )
            return AccountLink(False, "", "", stop, blocker=stop)
        mine = self._one_account(account_of_sql(self.entry, master))
        if mine is None:
            stop = (
                f"the account {master} is on could not be read, so there is nothing to link "
                "and nothing was written."
            )
            return AccountLink(False, "", "", stop, blocker=stop)
        theirs = self._one_account(account_named_sql(self.entry, account))
        if theirs is None:
            stop = (
                f"there is no account called {account} on this server, so nothing was written. "
                "It is the account name, not a character's name."
            )
            return AccountLink(False, mine[1], "", stop, blocker=stop)
        if theirs[0] == mine[0]:
            stop = (
                f"{theirs[1]} is {master}'s own account, so there is nothing to link -- this "
                "server already lets a character add every other character on its own account. "
                "Nothing was written."
            )
            return AccountLink(False, mine[1], theirs[1], stop, blocker=stop)
        already = self._sql.query(
            "playerbots",
            account_link_read_sql(self.entry, master_account=mine[0], other_account=theirs[0])
            + ";",
        )
        forward, back = link_directions(already, master_account=mine[0], other_account=theirs[0])
        if forward and back:
            stop = (
                f"{mine[1]} and {theirs[1]} are already linked on this server, so nothing was "
                "written."
            )
            return AccountLink(False, mine[1], theirs[1], stop, blocker=stop)
        return mine, theirs, forward or back

    def _one_account(self, statement: str) -> tuple[int, str] | None:
        """One `id\tusername` row, or `None` for "no such row, or no answer".

        The two are folded here on purpose: every caller of this refuses either
        way, and each refusal names the account it could not find rather than
        the read that could not be done.
        """
        try:
            raw = self._sql.query("auth", statement + ";")
        except Exception as exc:  # noqa: BLE001 - one answer for every seam failure
            logger.warning(f"could not read an account: {exc}")
            return None
        number, _, name = raw.strip().partition("\t")
        return (int(number), name.strip()) if number.isdigit() else None

    def _send_or_none(self) -> Callable[[str], Answer] | None:
        channel = self._channel_for_saved()
        if channel is None:
            return None
        return channel.send  # type: ignore[attr-defined,no-any-return]


def _rows_only(answer: tuple[Member, ...] | str) -> tuple[Member, ...]:
    """A read that failed is an EMPTY party for polling purposes, deliberately.

    The poll is looking for a guid that was not there before; a read that could
    not be done adds nothing to either side of that comparison, and treating it
    as "the bot is not here yet" makes the press time out and say so rather
    than announce a bot it never saw.

    For `add_bot`'s poll only — it is watching for a guid to APPEAR, so a read
    that could not be done and one that found nothing both mean "not yet".
    `dismiss()`'s own poll (`InstallParty.remove` and, since round 2,
    `InstallParty.remove_all`) watches for a guid to VANISH, where `()` already
    means "gone"; folding a failure into it there would report a row as
    removed on a read that never happened, so both hand it the unfolded answer
    instead (T21).
    """
    return () if isinstance(answer, str) else answer


def _not_online(name: str) -> str:
    return (
        f"{name} is not logged in. A bot is added to a live session — the server resolves the "
        "master by name in the world, so log the character in and press again."
    )


def _not_the_confirmed_party(
    master: str, rows: tuple[Member, ...], confirmed: tuple[int, ...]
) -> str:
    """Why nothing was dismissed, naming the party as it is NOW.

    As it is now rather than as the difference between the two: a person who has
    just been refused wants to know what to confirm next, and "one more bot than
    you agreed to" is a sentence they cannot check against the party frame in
    front of them.
    """
    holds = ", ".join(row.name for row in rows) if rows else "no bots at all"
    return (
        f"{master}'s party is not the one that was confirmed: {bots_word(len(confirmed))} were "
        f"agreed to and the group table now holds {holds}. Nothing was sent — show the party "
        "again and confirm what is there now."
    )


def _no_channel() -> str:
    return (
        "this install has no command channel set up yet, and My Party has no other way to reach "
        "the server. Set it up on the Server tab."
    )
