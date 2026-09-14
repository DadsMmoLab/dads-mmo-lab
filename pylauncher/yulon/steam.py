"""8.8 — this install's two entries in the Steam library, on Linux and the Deck.

Two shortcuts per install and no more: the **server launcher** (this app, so a
Deck in Gaming Mode can open Yu'lon and press Start without leaving the couch)
and the **game client** (a Windows executable, so it needs a compatibility tool).
Both go into one binary file, `userdata/<id>/config/shortcuts.vdf`, with their
artwork beside it under `grid/` and the client's Proton named in the *other*,
text file, `config/config.vdf`.

Four things here are not obvious and each one cost a read to find out:

* **Steam owns that file while it is open.** It holds the shortcut list in
  memory and rewrites it on exit, discarding anything written underneath it.
  So this refuses while `pgrep -x steam` answers, and says so — a write that
  quietly evaporates twenty minutes later is worse than no button.
* **`~/.steam/steam` is a symlink to `~/.local/share/Steam`** on an ordinary
  box (measured on `yulon-arch`, 2026-09-10). The prior art walks three roots
  and appends a candidate per root, which is harmless for an add-only routine
  that takes the first. It is not harmless here, where "more than one profile"
  is a refusal: `find_profile()` de-duplicates by `Path.resolve()` before it
  counts, or every single-account machine gets told it has two accounts.
* **The stored `appid` is not always derivable.** Of the two entries captured
  off a real Deck, one still equals `crc32(Exe + AppName) | 0x80000000` and the
  other does not — Steam keeps the appid it first issued when a shortcut is
  renamed or re-pointed, and seven other derivations were tried against it
  without a match. Grid artwork is named `<appid>*.png` with no lookup table,
  so an upsert that recomputed the appid of an entry it is *replacing* would
  orphan four files and blank the tile. `gen_appid` is for entries this creates;
  a replace keeps what it finds.
* **The document ends `08 08`** — one END for the `shortcuts` map, one for the
  root — and a file missing that second byte makes Steam drop *every* non-Steam
  shortcut in the library, not just ours. `terminated()` is checked before the
  bytes reach the disk.

The prior art is `origin/rust-main:docs/reference/dmlpack/dmlpack.py` (the codec
at `:245-295`, `gen_appid` at `:297`, `register_shortcuts` at `:1558-1645`). The
codec and the appid are ports of it. Three things are new here because upstream
does not do them: the artwork, the compatibility tool, and replace-on-second-
press — upstream is add-only and says so out loud when it leaves an entry alone.

Nothing in this module runs on Windows or macOS. The button is not built there
at all (`controller_view._steam_seam()` answers `None`), and `add()` refuses as
well, because a seam that trusts its caller is a seam that gets called wrong.
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from yulon import platform, resources, runner
from yulon.log import get_logger

logger = get_logger(__name__)


class SteamRefusal(RuntimeError):
    """Why these two entries were not written, in a sentence that says what to do.

    A refusal and not a `False`: every one of these has a remedy the person at
    the keyboard can carry out, and a button that goes grey without saying which
    of five reasons applied is the thing 8.8's definition of done rules out.
    """


# --------------------------------------------------------------------------
# binary VDF -- Steam's shortcuts.vdf
# Types: 0x00 nested map, 0x01 string, 0x02 int32 LE, 0x08 end-of-map.
# --------------------------------------------------------------------------

VdfMap = dict[str, Any]
"""A parsed VDF map.

`Any` rather than a recursive union, and the reason is the format rather than
laziness: a value is a string, an int32 or another map, and every caller here
already knows which of the three it asked for -- `entry["appid"]` is an int
because the file says so. A recursive alias would put a `cast` on every one of
those reads and prove nothing that the round-trip test does not.
"""

MAP, STR, INT, END = 0x00, 0x01, 0x02, 0x08

TERMINATOR = bytes([END, END])
"""What a whole document ends with: END for `shortcuts`, END for the root."""


def _read_cstr(buf: bytes, i: int) -> tuple[str, int]:
    """A NUL-terminated string, with every byte kept.

    `surrogateescape`, not `replace`: this file also holds shortcuts somebody
    else made, and a code-page byte in one of their names would otherwise come
    back as U+FFFD and be written out as UTF-8 by the rewrite -- an unrelated
    shortcut silently altered by a press that never meant to touch it (Codex on
    T17, round 2). The bytes go back out exactly as they came in.
    """
    j = buf.index(b"\x00", i)
    return buf[i:j].decode("utf-8", "surrogateescape"), j + 1


def vdf_parse(buf: bytes, i: int = 0) -> tuple[VdfMap, int]:
    """One map out of `buf` starting at `i`, and the offset just past its END.

    The offset is returned rather than dropped so a caller can check that the
    whole file was consumed: a document with trailing rubbish parses perfectly
    and is not the document Steam wrote.
    """
    out: VdfMap = {}
    while i < len(buf):
        kind = buf[i]
        # Kept for the error below. The key is read BEFORE the type is dispatched,
        # so `i` has already moved past it by the time an unknown type is noticed,
        # and the offset the prior art reports is the one AFTER the name rather
        # than the byte anybody would look for with `xxd`.
        at = i
        i += 1
        if kind == END:
            return out, i
        key, i = _read_cstr(buf, i)
        if kind == MAP:
            out[key], i = vdf_parse(buf, i)
        elif kind == STR:
            out[key], i = _read_cstr(buf, i)
        elif kind == INT:
            out[key] = struct.unpack_from("<i", buf, i)[0]
            i += 4
        else:
            raise ValueError(f"unknown VDF type 0x{kind:02x} at offset {at}")
    return out, i


def vdf_serialize(d: VdfMap) -> bytes:
    """A map's contents, without its own END. Insertion order is the file order."""
    parts = bytearray()
    for key, val in d.items():
        kb = key.encode("utf-8", "surrogateescape") + b"\x00"
        if isinstance(val, dict):
            parts += bytes([MAP]) + kb + vdf_serialize(val) + bytes([END])
        elif isinstance(val, int):
            parts += bytes([INT]) + kb + struct.pack("<i", val)
        else:
            parts += bytes([STR]) + kb + str(val).encode("utf-8", "surrogateescape") + b"\x00"
    return bytes(parts)


def vdf_dump(root: VdfMap) -> bytes:
    """A whole document: the root map's contents plus the root's own END."""
    return vdf_serialize(root) + bytes([END])


def terminated(payload: bytes) -> bool:
    """Does this end the way Steam's own file ends? See the module docstring."""
    return payload.endswith(TERMINATOR)


def gen_appid(quoted_exe: str, appname: str) -> int:
    """Steam's non-Steam appid: `crc32(Exe + AppName)` with the high bit set.

    Unsigned. The file stores it signed, so `to_signed()` sits between this and
    an entry. Re-creating a shortcut under the same name and exe regenerates the
    same appid, which is why the grid artwork survives a reinstall for free.
    """
    return (zlib.crc32((quoted_exe + appname).encode("utf-8")) | 0x80000000) & 0xFFFFFFFF


def to_signed(value: int) -> int:
    """The int32 the file holds, from the unsigned appid."""
    return value - 0x100000000 if value >= 0x80000000 else value


def to_unsigned(value: int) -> int:
    """The other direction: `CompatToolMapping` keys the *unsigned* value."""
    return value + 0x100000000 if value < 0 else value


def quoted(value: str) -> str:
    """`Exe` is stored quoted. `StartDir` sometimes is and sometimes is not.

    Both spellings came off the same Deck, from Steam itself, so the quoting of
    `StartDir` is not a rule to enforce — this quotes what it creates and leaves
    what it finds alone.
    """
    return value if value.startswith('"') else f'"{value}"'


# --------------------------------------------------------------------------
# the refusals, spelled once so the tests and the README quote the same string
# --------------------------------------------------------------------------

RUNNING = (
    "Steam is running. Steam keeps the shortcut list in memory and rewrites "
    "shortcuts.vdf when it exits, so anything written now would be thrown away "
    "without a word. Quit Steam completely — check the tray — and press "
    "Add to Steam… again."
)

NO_PROFILE = (
    "No Steam profile was found on this machine: nothing under {roots} holds a "
    "userdata/<id>/config folder. Sign in to Steam once so it creates one, then "
    "press Add to Steam… again."
)

TWO_PROFILES = (
    "Two Steam accounts have profiles on this machine (userdata ids {ids}). "
    "Yu'lon will not guess which library these two entries belong in. Remove the "
    "profile you do not want from {root}, or sign in as the account you do want "
    "and press Add to Steam… again."
)

NOT_LINUX = (
    "Adding to Steam is a Linux and Steam Deck feature ({platform_id} was "
    "detected). Nothing was written."
)

NO_CLIENT = (
    "This install has no client folder recorded, so there is nothing to point a "
    "client entry at. Yu'lon writes the path you gave it and never guesses one. "
    "Re-add this server with its client folder, then press Add to Steam… again."
)

NO_PROTON = (
    "No Proton is installed for this Steam, and the client entry is a Windows "
    "executable that cannot start without one. Install Proton from Steam itself "
    "(Library → filter by Tools → Proton → Install), or unpack a Proton "
    "build into {tools}, then press Add to Steam… again."
)

UNREADABLE = (
    "{path} is not a shortcuts file this can read: {problem}. Yu'lon will not guess "
    "at bytes it does not understand in a file that also holds shortcuts somebody "
    "else made — a wrong guess there loses them. Nothing was written. Close Steam, "
    "move that file aside (Steam writes a fresh one), and press Add to Steam… again."
)

UNWRITABLE = (
    "{path} cannot be written ({reason}). That folder belongs to the Steam account "
    "signed in on this machine, and nothing was changed. On a Steam Deck this is NOT "
    "the read-only root: the Steam profile lives under /home, which is a separate "
    "writable partition, so no `steamos-readonly disable` belongs here."
)


# --------------------------------------------------------------------------
# the machine: is Steam up, where is the profile, which Proton
# --------------------------------------------------------------------------


def steam_running(run: Callable[..., subprocess.CompletedProcess[str]] = runner.run) -> bool:
    """True while a process named exactly `steam` is alive.

    `-x` and not a substring match: `steamwebhelper`, `steam-runtime-launcher`
    and half a dozen other children are always up alongside it, and every one of
    them stays behind for a second or two after Steam itself has gone. Matching
    loosely would mean the button refuses for a minute after the user did as
    they were told.

    Off Linux this is `False` rather than a `pgrep` that does not exist —
    `add()` has already refused by then, and this is a plain question.
    """
    if platform.detect() != "linux":
        return False
    return run(["pgrep", "-x", "steam"]).returncode == 0


def steam_roots(home: Path) -> tuple[Path, ...]:
    """Every place a Steam userdata folder is known to live, native and flatpak."""
    return (
        home / ".local/share/Steam/userdata",
        home / ".steam/steam/userdata",
        home / ".var/app/com.valvesoftware.Steam/data/Steam/userdata",
    )


def find_profile(home: Path) -> Path:
    """The one `userdata/<id>/config` on this machine, or a refusal naming why.

    De-duplicated by `Path.resolve()` — see the module docstring; on the box this
    was read from, two of the three roots are the same directory through a
    symlink and a naive count answers "two profiles" for one account.
    """
    found: dict[Path, Path] = {}
    for root in steam_roots(home):
        if not root.is_dir():
            continue
        for account in sorted(root.iterdir()):
            if not account.is_dir() or not account.name.isdigit() or account.name == "0":
                continue
            config = account / "config"
            if config.is_dir():
                found.setdefault(config.resolve(), config)
    if not found:
        raise SteamRefusal(NO_PROFILE.format(roots=", ".join(str(r) for r in steam_roots(home))))
    if len(found) > 1:
        ids = ", ".join(sorted(path.parent.name for path in found.values()))
        root = sorted(found.values())[0].parent.parent
        raise SteamRefusal(TWO_PROFILES.format(ids=ids, root=root))
    return next(iter(found.values()))


_OFFICIAL_PROTON = {
    "Proton - Experimental": "proton_experimental",
    "Proton Experimental": "proton_experimental",
    "Proton Hotfix": "proton_hotfix",
    "Proton Next": "proton_next",
}
"""What Steam calls its own Proton builds in `CompatToolMapping`.

Convention, not read from disk: a folder under `steamapps/common` carries no
file naming what Steam maps it as. That is exactly why a tool in
`compatibilitytools.d`, whose internal name IS on disk in its own manifest, is
preferred over one of these.
"""

_PROTON_VERSION = re.compile(r"^Proton (\d+)\.")

_TOOL_VERSION = re.compile(r"Proton[- ]?(\d+)")
"""The major version out of a compatibility tool's folder name, official or not.

`GE-Proton11-6-x86_64` -> 11, `GE-Proton9-27` -> 9, `Proton 9.0` -> 9.
"""

_DIGITS = re.compile(r"\d+")


def _tool_order(name: str) -> tuple[int, list[int], str]:
    """Sort key for a compatibility tool, newest first when reversed.

    A plain lexicographic sort puts `GE-Proton9-27` above `GE-Proton11-6`, because
    `9` > `1` one character at a time — so a box with both installed would have
    its client launched under the older Proton, silently. This is the same
    version-awareness `_PROTON_VERSION` gives the official builds, applied to the
    folder names in `compatibilitytools.d`, which are not Valve's to shape.

    The major version leads; the remaining numbers break ties (`11-6` above
    `11-2`); the name settles the rest, so the order is total and stable.
    """
    match = _TOOL_VERSION.search(name)
    major = int(match.group(1)) if match else -1
    return (major, [int(n) for n in _DIGITS.findall(name)], name)


def _tool_name_from_manifest(manifest: Path) -> str | None:
    """The internal name out of a `compatibilitytool.vdf`, comments stripped.

    The internal name is the first bare quoted key inside `compat_tools`, and on
    a real GE-Proton manifest it carries a trailing `// Internal name of this
    tool` comment, which is why the comment is stripped before the match.
    """
    try:
        lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    inside = False
    for raw in lines:
        line = raw.split("//", 1)[0].strip()
        if not inside:
            inside = line == '"compat_tools"'
            continue
        match = re.fullmatch(r'"([^"]+)"', line)
        if match:
            return match.group(1)
    return None


def find_compat_tool(steam_root: Path) -> str | None:
    """What to name in `CompatToolMapping`, or `None` if this Steam has no Proton.

    `compatibilitytools.d` first because its answer is read off the tool's own
    manifest; an official Proton under `steamapps/common` second, because its
    name is Steam's convention rather than a fact on disk.
    """
    tools = steam_root / "compatibilitytools.d"
    if tools.is_dir():
        installed = sorted(
            (e for e in tools.iterdir() if e.is_dir()),
            key=lambda e: _tool_order(e.name),
            reverse=True,
        )
        for entry in installed:
            # A directory whose manifest cannot be read or names no tool is not a
            # tool Steam can resolve, whatever its name says; naming it in
            # `CompatToolMapping` leaves the client unable to launch (Codex on
            # T17, round 2). Skipped, and the next one asked.
            name = _tool_name_from_manifest(entry / "compatibilitytool.vdf")
            if name:
                return name
    common = steam_root / "steamapps" / "common"
    if common.is_dir():
        numbered: list[tuple[int, str]] = []
        named: list[str] = []
        for entry in sorted(common.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("Proton"):
                continue
            match = _PROTON_VERSION.match(entry.name)
            if match:
                numbered.append((int(match.group(1)), f"proton_{match.group(1)}"))
            elif entry.name in _OFFICIAL_PROTON:
                named.append(_OFFICIAL_PROTON[entry.name])
        # A numbered build is the one Valve ships as stable, so it wins; the
        # Experimental and Hotfix builds are picked only when they are all there is.
        if numbered:
            return max(numbered)[1]
        if named:
            return sorted(named)[0]
    return None


_CLIENT_EXE_PREFERENCE = ("wow.exe",)
"""Which executable in a client folder is the game.

Measured on the box's own TurtleWoW folder, which holds three: `WoW.exe` (the
game), `TurtleWoW.exe` (the launcher-and-updater) and `TWPatcher.exe`. Matched
case-insensitively because the same folder shipped from Windows spells it
`Wow.exe`.
"""


def client_executable(client_dir: Path) -> Path:
    """The game binary inside a client folder, or where it would be if absent.

    Falling back to `WoW.exe` rather than refusing is deliberate and narrow: the
    folder is one the user picked and Yu'lon validated, and an entry pointing at
    the conventional name inside it is a thing they can see and correct. A
    folder Yu'lon was never given is a different case and refuses (`NO_CLIENT`).
    """
    for candidate in sorted(client_dir.glob("*")):
        if candidate.is_file() and candidate.name.lower() in _CLIENT_EXE_PREFERENCE:
            return candidate
    return client_dir / "WoW.exe"


def launcher_command() -> tuple[str, str]:
    """How to start THIS Yu'lon again: `(exe, launch options)`.

    Frozen, `sys.executable` is the `yulon` binary and there is nothing else to
    say. From source it is the interpreter, and `main.py` — the real entry point,
    beside the package rather than in it — goes in the launch options, which is
    where Steam puts arguments.
    """
    if resources.frozen():
        return sys.executable, ""
    return sys.executable, str(resources.bundle_root() / "main.py")


# --------------------------------------------------------------------------
# entries
# --------------------------------------------------------------------------


def new_entry(
    *,
    name: str,
    exe: str,
    startdir: str,
    launchopts: str = "",
    appid: int | None = None,
) -> VdfMap:
    """One `shortcuts.vdf` entry, with the eighteen fields a live one has.

    The field set and their order are copied from the two entries captured off a
    real Steam Deck (`.notes/gates/8.8-steam-read-yulon-arch-2026-09-10/`), not
    from a wiki page: the codec writes a dict in insertion order, so the order
    here is the order in the file.
    """
    quoted_exe = quoted(exe)
    return {
        "appid": to_signed(appid if appid is not None else gen_appid(quoted_exe, name)),
        "AppName": name,
        "Exe": quoted_exe,
        "StartDir": quoted(startdir),
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launchopts,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "sortas": "",
        "tags": {},
    }


def _existing(shortcuts: VdfMap, name: str) -> tuple[str, VdfMap] | None:
    """The slot holding the entry called `name`, if there is one.

    Keyed by `AppName` because that is the only field a person recognises, and
    because keying by appid would key by a value that changes the moment the
    client folder moves — which is precisely the press this has to replace.
    """
    for slot, entry in shortcuts.items():
        if (entry.get("AppName") or entry.get("appname")) == name:
            return slot, entry
    return None


KEPT_ON_REPLACE = ("appid", "LastPlayTime", "tags")
"""What a replace takes from the entry it is replacing rather than rewriting.

Each of the three is something the second press has no business knowing better
than Steam does, and all three were learned on the box rather than reasoned:

* **`appid`** — Steam keeps the one it first issued when a shortcut is renamed
  or re-pointed, so it is not always `gen_appid(Exe, AppName)`. The grid art on
  disk is named after the appid Steam is actually using, so recomputing it
  orphans the artwork and blanks the tile.
* **`LastPlayTime`** — Steam's own record of when this was last played. Measured
  on `yulon-arch` 2026-09-10: launching the two entries once each left
  `1789071688` and `1789071433` in the file, and the first version of this
  function put `0` back over both of them. A button that quietly erased "last
  played" every time it was pressed would be a button nobody presses twice.
* **`tags`** — the collections the user has filed this entry under. Theirs, not
  ours.
"""


def upsert(shortcuts: VdfMap, entry: VdfMap) -> tuple[bool, VdfMap]:
    """Put `entry` in, replacing one of the same `AppName`.

    Answers `(replaced, the entry as it now stands in the file)`. The second half
    is not a convenience: on a replace the stored entry keeps the OLD appid, and
    the artwork has to be written under that name or it lands beside the tile
    instead of on it. The first version returned only the flag, wrote the art
    under the derived appid, and its test caught it.

    A replace MERGES over the entry it finds rather than swapping it out. Two
    reasons, and the second is the one that is easy to miss: `KEPT_ON_REPLACE`
    holds three fields back, and merging also keeps any field Steam writes that
    this module has never heard of — the format has gained fields before
    (`sortas` and `FlatpakAppID` are not in older captures) and a writer that
    replaced wholesale would silently drop the next one.
    """
    hit = _existing(shortcuts, str(entry["AppName"]))
    if hit is None:
        slot = str(max((int(k) for k in shortcuts if k.isdigit()), default=-1) + 1)
        shortcuts[slot] = entry
        return False, entry
    slot, old = hit
    merged = dict(old)
    merged.update(entry)
    for key in KEPT_ON_REPLACE:
        if key in old:
            merged[key] = old[key]
    shortcuts[slot] = merged
    return True, merged


# --------------------------------------------------------------------------
# CompatToolMapping -- the text VDF, and a minimal edit of it
# --------------------------------------------------------------------------

_STEAM_KEY_INDENT = "\t\t\t"
"""`InstallConfigStore / Software / Valve / Steam` — three tabs at the last one."""


def compat_mapping(text: str, *, appid: int, tool: str) -> tuple[str, str]:
    """`config.vdf` with `<appid> -> tool` mapped, and the block that was inserted.

    Returns the block as well as the text so a caller — and the test that proves
    it — can take it back out again and compare with what was there before.
    Every other byte is preserved: this is line surgery on a 17 KB file of
    Steam's own state (websocket tables, shader-cache buckets, the depot list),
    and a writer that round-tripped it through a VDF parser would reformat all
    of it to fix one line.

    Three cases, in order: the appid is already mapped (change the name), the
    section exists but not this appid (add it), the section does not exist at all
    (open one straight after the `"Steam"` map, which is where Steam puts it).
    On `yulon-arch`, 2026-09-10, the section did not exist: `grep -c` answered 0.
    """
    # `text` reaches here through `open(..., newline="")`, so the line endings are
    # the file's own. Read with universal newlines this branch is DEAD -- Python
    # hands back `\n` whatever the file holds, a CRLF `config.vdf` is rewritten
    # LF-only, and the ledger's "every other byte preserved" is a false claim
    # about several hundred lines.
    newline = "\r\n" if "\r\n" in text else "\n"
    key = f'"{to_unsigned(appid)}"'
    ind = _STEAM_KEY_INDENT

    entry_block = newline.join(
        (
            f"{ind}\t\t{key}",
            f"{ind}\t\t{{",
            f'{ind}\t\t\t"name"\t\t"{tool}"',
            f'{ind}\t\t\t"config"\t\t""',
            f'{ind}\t\t\t"priority"\t\t"250"',
            f"{ind}\t\t}}",
            "",
        )
    )
    section_block = (
        newline.join((f'{ind}\t"CompatToolMapping"', f"{ind}\t{{", ""))
        + entry_block
        + f"{ind}\t}}{newline}"
    )

    lines = text.splitlines(keepends=True)
    mapped = _find_mapped_appid(lines, key)
    if mapped is not None:
        start, stop = mapped
        replacement = entry_block.splitlines(keepends=True)
        return "".join(lines[:start] + replacement + lines[stop:]), entry_block
    section = _find_section_open(lines, f'{ind}\t"CompatToolMapping"')
    if section is not None:
        return "".join(lines[:section] + [entry_block] + lines[section:]), entry_block
    steam = _find_section_open(lines, f'{ind}"Steam"')
    if steam is None:
        raise SteamRefusal(
            "config.vdf does not have the InstallConfigStore/Software/Valve/Steam map "
            "this edit goes into; nothing was changed."
        )
    return "".join(lines[:steam] + [section_block] + lines[steam:]), section_block


def _find_section_open(lines: Sequence[str], key_line: str) -> int | None:
    """The index just after `key_line`'s opening brace, or None."""
    for i, line in enumerate(lines[:-1]):
        if line.rstrip("\r\n") == key_line and lines[i + 1].strip() == "{":
            return i + 2
    return None


def _find_mapped_appid(lines: Sequence[str], key: str) -> tuple[int, int] | None:
    """The half-open line range of an existing `"<appid>" { ... }` block, or None."""
    for i, line in enumerate(lines[:-1]):
        if line.strip() != key or lines[i + 1].strip() != "{":
            continue
        for j in range(i + 2, len(lines)):
            if lines[j].strip() == "}":
                return i, j + 1
    return None


# --------------------------------------------------------------------------
# artwork -- drawn, because this app ships no images at all
# --------------------------------------------------------------------------

GRID_SLOTS = ("", "p", "_hero", "_logo")
"""Steam's four artwork slots, all four, for the record.

`<appid>.png` is the wide header, `<appid>p.png` the library portrait,
`<appid>_hero.png` the banner behind the game page and `<appid>_logo.png` the
transparent logo laid over that banner. Under `config/grid/`, keyed by the appid
with no lookup table anywhere.
"""

GRID_SUFFIXES = ("", "p", "_hero")
"""The three this writes, and `_logo` is left out on purpose.

Measured on `yulon-arch`, 2026-09-10: with a `_logo.png` present Steam draws it
INSTEAD of the entry's name on the game page, so an entry whose logo is a mark
with no wordmark in it loses its title altogether — and these two entries differ
by exactly one word (`… Server`). The frame is
`.notes/gates/8.8-steam-shortcuts-yulon-arch-2026-09-10/1-logo-slot-hid-the-name.png`:
a page with a Play button,
a jade Y and nothing to say which of the two it is. Leaving the slot empty makes
Steam fall back to the name, which is the whole point of writing them.
"""

_ICON_SIZE = 512
_INK = (14, 26, 32)
_DEEP = (18, 48, 56)
_JADE = (47, 174, 143)
_EMBER = (198, 138, 63)
_CREAM = (244, 236, 216)

CLIENT_ACCENT = _JADE
"""The game client's disc. Yu'lon's own jade."""

SERVER_ACCENT = _EMBER
"""The server launcher's disc, a different colour on purpose.

Both entries carry the app's mark because this tree ships no game artwork, and
two identical tiles side by side in a library grid — which is where the name is
not drawn at all — would be two tiles nobody can tell apart.
"""

_ICON_CACHE: dict[tuple[int, int, int], bytes] = {}


def _png(width: int, height: int, pixels: bytearray) -> bytes:
    """A minimal 8-bit RGB PNG. `pixels` is `width * height * 3` bytes, row major."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter: none
        raw += pixels[y * stride : (y + 1) * stride]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _coverage(distance: float, edge: float) -> float:
    """Anti-aliasing: 1 inside, 0 outside, a pixel and a half of ramp between."""
    return min(1.0, max(0.0, 0.5 + (edge - distance) / 1.5))


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return float(((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5)


def icon_png(accent: tuple[int, int, int] = CLIENT_ACCENT) -> bytes:
    """The Yu'lon mark: a jade disc on ink, with a cream Y across it.

    Drawn here rather than shipped as a file, because this app ships no images
    at all — `pylauncher/` contains no `.png`, `.ico` or `.svg`, and the macOS
    bundle sets `icon=None`. A mark in code is a mark that can be read in a
    review and reproduced byte for byte in a test; a committed blob is neither.

    One 512-pixel square, cached, written under all four grid names for both
    entries. Steam scales it into each slot. It is the app's mark and not the
    game's, for both entries, because no game artwork ships in this tree either
    and inventing one would be putting somebody else's logo in a file.
    """
    if accent in _ICON_CACHE:
        return _ICON_CACHE[accent]
    size = _ICON_SIZE
    centre = size / 2.0
    radius = size * 0.34
    thickness = radius * 0.17
    arm = radius * 0.46
    pixels = bytearray(size * size * 3)
    for y in range(size):
        top = y / (size - 1)
        base = tuple(round(_INK[c] + (_DEEP[c] - _INK[c]) * top) for c in range(3))
        for x in range(size):
            dx, dy = x + 0.5 - centre, y + 0.5 - centre
            disc = _coverage((dx * dx + dy * dy) ** 0.5, radius)
            colour = base
            if disc > 0.0:
                colour = tuple(round(base[c] + (accent[c] - base[c]) * disc) for c in range(3))
                glyph = _coverage(
                    min(
                        _segment_distance(dx, dy, -arm, -arm, 0.0, 0.0),
                        _segment_distance(dx, dy, arm, -arm, 0.0, 0.0),
                        _segment_distance(dx, dy, 0.0, 0.0, 0.0, arm * 1.2),
                    ),
                    thickness,
                )
                if glyph > 0.0:
                    colour = tuple(
                        round(colour[c] + (_CREAM[c] - colour[c]) * glyph) for c in range(3)
                    )
            offset = (y * size + x) * 3
            pixels[offset : offset + 3] = bytes(colour)
    _ICON_CACHE[accent] = _png(size, size, pixels)
    return _ICON_CACHE[accent]


def artwork_paths(grid: Path, appid: int) -> tuple[Path, ...]:
    """The slots this writes, for one entry. Unsigned appid, as the names are on disk."""
    unsigned = to_unsigned(appid)
    return tuple(grid / f"{unsigned}{suffix}.png" for suffix in GRID_SUFFIXES)


# --------------------------------------------------------------------------
# the writes. One call each, so the write ledger has one row each.
# --------------------------------------------------------------------------


def _backup(path: Path, stamp: str) -> Path | None:
    """A copy beside the original before it is touched, or None if there is none yet."""
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.yulon-bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


TMP_SUFFIX = ".yulon-tmp"
"""The sibling a file is built in before it is moved onto its own name.

`Path.write_bytes` opens for writing, which TRUNCATES, and then writes. Between
those two the file exists and is empty or half a file, and for these two files
that window is not academic:

* a truncated `shortcuts.vdf` makes Steam drop **every** non-Steam shortcut in
  the library, ours and the user's alike -- the same damage as a missing
  terminator, which this module already refuses to risk;
* a truncated `config/config.vdf` is Steam's whole global configuration.

ENOSPC, a SIGKILL, a laptop lid: the write becomes a rename, which is atomic on
every filesystem this app runs on, and an interruption leaves the target exactly
as it was with the debris under a name nothing reads. `state.py:126-131` is the
same pattern and made the same argument first.
"""


def _write_shortcuts(path: Path, payload: bytes) -> None:
    """The whole document, through a sibling temp file and a rename."""
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_bytes(payload)
    tmp.replace(path)


def _write_artwork(paths: Sequence[Path], payload: bytes) -> None:
    """The grid PNGs, in place.

    NOT through a temp file, and the asymmetry is deliberate: a half-written PNG
    is a tile that does not draw, which the next press replaces. Neither of the
    other two files has a failure that small.
    """
    for path in paths:
        path.write_bytes(payload)


def _write_compat(path: Path, text: str) -> None:
    """Steam's global config, through a sibling temp file and a rename.

    `errors="surrogateescape"` because that is how it was READ. A file with one
    byte that is not UTF-8 in it -- a game name from a Windows code page, say --
    decodes into surrogates and a strict write raises `UnicodeEncodeError` on
    them. Written in place that exception arrived AFTER the truncation, so the
    one file this app has no business damaging was left empty by a writer that
    only meant to add three lines. `newline=""` so the line endings written are
    the ones `compat_mapping()` chose from the file's own.
    """
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8", errors="surrogateescape", newline="")
    tmp.replace(path)


# --------------------------------------------------------------------------
# the press
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AddReport:
    """What one press changed, in the words the confirmation is built from."""

    entries: tuple[str, str]
    path: Path
    backup: Path | None
    replaced: bool
    artwork: tuple[Path, ...]
    compat_tool: str
    compat_path: Path
    compat_backup: Path | None


def confirmation(report: AddReport) -> str:
    """The sentence the button shows: every file it touched, by name.

    Long on purpose. This writes inside the user's own Steam profile, and the
    only honest confirmation is one they can check by hand afterwards.
    """
    verb = "Replaced" if report.replaced else "Added"
    client, server = report.entries
    backup = (
        f" It was backed up first as {report.backup.name}."
        if report.backup is not None
        else " There was no shortcuts.vdf before this, so there was nothing to back up."
    )
    compat_backup = (
        f", backed up as {report.compat_backup.name}" if report.compat_backup is not None else ""
    )
    return (
        f"{verb} two entries in the Steam library: “{client}” (the game client, "
        f"through the compatibility tool {report.compat_tool}) and “{server}” "
        f"(this launcher). Written to {report.path}.{backup} "
        f"{len(report.artwork)} artwork files were written under "
        f"{report.path.parent / 'grid'}. The compatibility tool was set in "
        f"{report.compat_path}{compat_backup}. "
        "Restart Steam to see them."
    )


@dataclass
class SteamShortcuts:
    """This install's two Steam entries. One method, and every seam injectable.

    The seams are here rather than read from the environment because the tests
    for this run on a laptop with a Steam profile of its own on it: a test that
    reached `Path.home()` would be a test that edits the developer's library.
    """

    game: str
    server_dir: Path
    client_dir: Path | None
    home: Path = field(default_factory=Path.home)
    is_running: Callable[[], bool] = steam_running
    now: Callable[[], datetime] = datetime.now
    platform_id: Callable[[], str] = platform.detect
    launcher: Callable[[], tuple[str, str]] = launcher_command

    def add(self) -> AddReport:
        """Write the two entries, the artwork and the compatibility tool.

        Order matters and is checked by tests: every refusal happens before the
        first byte, and both backups are taken before either file is written.
        """
        detected = self.platform_id()
        if detected != "linux":
            raise SteamRefusal(NOT_LINUX.format(platform_id=detected))
        if self.client_dir is None:
            raise SteamRefusal(NO_CLIENT)
        if self.is_running():
            raise SteamRefusal(RUNNING)

        config = find_profile(self.home)
        steam_root = config.parent.parent.parent
        tool = find_compat_tool(steam_root)
        if tool is None:
            raise SteamRefusal(NO_PROTON.format(tools=steam_root / "compatibilitytools.d"))

        client_exe = client_executable(self.client_dir)
        launcher_exe, launcher_opts = self.launcher()
        client_entry = new_entry(
            name=self.game,
            exe=str(client_exe),
            startdir=str(self.client_dir),
        )
        server_entry = new_entry(
            name=f"{self.game} Server",
            exe=launcher_exe,
            startdir=str(Path(launcher_exe).parent),
            launchopts=launcher_opts,
        )

        path = config / "shortcuts.vdf"
        root: VdfMap = {"shortcuts": {}}
        if path.exists():
            # `vdf_parse` is right to raise on a type byte it has never seen — the
            # alternative is guessing at a file full of somebody else's shortcuts.
            # It is not right to let "unknown VDF type 0x07 at offset 24" reach a
            # button, which is the one message this feature can produce with no
            # remedy in it. The offset is kept: it is the only thing anybody
            # looking at the file with `xxd` can use.
            try:
                root, _ = vdf_parse(path.read_bytes())
            except ValueError as exc:
                raise SteamRefusal(UNREADABLE.format(path=path, problem=exc)) from exc
            root.setdefault("shortcuts", {})
        shortcuts = root["shortcuts"]
        before = len(shortcuts)
        # `|` and not `or`: both sides must run, and a short-circuit would write
        # only the client entry the second time round.
        replaced_client, client_entry = upsert(shortcuts, client_entry)
        replaced_server, server_entry = upsert(shortcuts, server_entry)
        replaced = replaced_client | replaced_server
        expected = before + (not replaced_client) + (not replaced_server)

        payload = vdf_dump(root)
        reparsed, consumed = vdf_parse(payload)
        if consumed != len(payload) or not terminated(payload):
            raise SteamRefusal(
                "The shortcuts file this would write does not end the way Steam's own "
                "does, and a bad terminator makes Steam drop every non-Steam shortcut "
                "in the library. Nothing was written."
            )
        if len(reparsed["shortcuts"]) != expected:
            raise SteamRefusal(
                "The shortcuts file this would write does not round-trip through its "
                "own parser. Nothing was written."
            )

        compat_path = steam_root / "config" / "config.vdf"
        # `newline=""` and not `read_text`: universal newlines hand back `\n`
        # whatever the file holds, which would make `compat_mapping()`'s CRLF
        # branch unreachable and rewrite a CRLF file LF-only — several hundred
        # changed lines under a ledger row promising every other byte preserved.
        with compat_path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            compat_text, _ = compat_mapping(fh.read(), appid=int(client_entry["appid"]), tool=tool)

        stamp = self.now().strftime("%Y%m%d-%H%M%S")
        try:
            backup = _backup(path, stamp)
            compat_backup = _backup(compat_path, stamp)
            grid = config / "grid"
            grid.mkdir(parents=True, exist_ok=True)
            client_art = artwork_paths(grid, int(client_entry["appid"]))
            server_art = artwork_paths(grid, int(server_entry["appid"]))
            art = client_art + server_art
            _write_artwork(client_art, icon_png(CLIENT_ACCENT))
            _write_artwork(server_art, icon_png(SERVER_ACCENT))
            _write_compat(compat_path, compat_text)
            _write_shortcuts(path, payload)
        except OSError as exc:
            # `OSError` and not `PermissionError`: a read-only filesystem answers
            # EROFS, which is `OSError` and not a subclass of `PermissionError`,
            # so the one case the checklist's gate line asks about — "the writer
            # must not need an unlock" — was the one that reached the panel as a
            # traceback instead of as the sentence that answers it. ENOSPC and a
            # vanished directory land here too, and with the writes atomic the
            # files they interrupt are still whole.
            raise SteamRefusal(
                UNWRITABLE.format(path=path.parent, reason=exc.strerror or exc)
            ) from exc

        logger.info(
            f"steam: wrote {len(reparsed['shortcuts'])} shortcut(s) to {path} "
            f"({'replaced' if replaced else 'added'} ours), compat tool {tool}"
        )
        return AddReport(
            entries=(self.game, f"{self.game} Server"),
            path=path,
            backup=backup,
            replaced=replaced,
            artwork=art,
            compat_tool=tool,
            compat_path=compat_path,
            compat_backup=compat_backup,
        )
