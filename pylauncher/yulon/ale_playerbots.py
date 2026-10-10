"""mod-ale's Playerbots support, compiled against whichever names mod-playerbots' config has (T645).

azerothcore/mod-ale's Playerbots bindings (`src/LuaEngine/methods/Playerbots/`, since
03b106c #397) read two members of mod-playerbots' `PlayerbotAIConfig` by name:
`PlayerBotAIMethods.h` uses `sPlayerbotAIConfig.sightDistance` and
`sPlayerbotAIConfig.reactDistance` (mod-ale cead0cb, 2026-10-08). mod-playerbots
ed54b459 (#2854, 2026-10-06) renamed every public member of that class to
UpperCamelCase, so a server whose mod-playerbots is past that commit -- every
"Update the server to latest…" since -- failed to compile as soon as mod-ale was in
it: "no member named 'sightDistance' in 'PlayerbotAIConfig'; did you mean
'SightDistance'?".

**What this does.** For one compile, every `sPlayerbotAIConfig.<name>` in mod-ale's
sources whose name the checkout's `PlayerbotAIConfig.h` does not declare, and which
the header declares exactly once in another case, is written with the header's name.
Read from the two checkouts every time and never from a list of commits, so it works
in both directions (a mod-ale that renamed first, against a pinned mod-playerbots
that has not, is bridged back the same way) and it does nothing at all:

* when either module is not in the server folder;
* when mod-playerbots still has the old names (every catalog pin had them when this was
  written for T645; T655 then moved WotLK and Unbound to mod-playerbots 79bd4281, which
  has the new ones);
* when mod-ale has caught up (every name it uses is declared);
* for a name with no twin, or with two: the compiler's own error stands, and the
  failed build's note (`native.ale_playerbots_note()`) says what it means.

**Why only for the compile, and not left on disk like a carried core patch.** On
WotLK mod-ale is a Modules-tab clone, and its Update refuses a checkout with an
edited tracked file (T44/T47): a rewrite left behind would block the very update
that later fixes this upstream. On Unbound mod-ale is an emulator source, and the
update route's dirty-tree guard would refuse it the same way. So the files are
put back, byte for byte, as soon as the compile returns, and every git question
asked outside the compile sees upstream's file.

**A compile Yu'lon never saw finish.** The originals are written to `RECORD` in the
server folder BEFORE any file is rewritten, and the record goes only once every file
is back. The next bridge puts back what an old record names first -- but only a file
whose bytes are still exactly the ones the bridge wrote (their digest is in the
record); a file changed since is somebody's, and is left as it is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

ALE = "modules/mod-ale"
"""Where the Modules tab (WotLK) and the catalog (Unbound) put azerothcore/mod-ale."""

CONFIG_HEADER = "modules/mod-playerbots/src/PlayerbotAIConfig.h"
"""mod-playerbots' config class, the one header whose names decide (src/ at every commit read)."""

RECORD = ".yulon-ale-bridge.json"
"""The originals of the files a bridge rewrote, until they are back (a `.yulon*` name: never
compiled, and left out of the build-context fingerprint, `build_context`)."""

_SOURCE_SUFFIXES = (".h", ".hpp", ".cpp")
_USE = re.compile(r"\bsPlayerbotAIConfig\.([A-Za-z_]\w*)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_CLASS = re.compile(r"\bclass\s+PlayerbotAIConfig\b")


@dataclass(frozen=True)
class _Bridged:
    """One file the bridge rewrites: where, upstream's text, and what it becomes."""

    path: str
    original: str
    bridged: str
    renames: Mapping[str, str]


def renames_for(header: str, source: str) -> dict[str, str]:
    """The `sPlayerbotAIConfig.<name>` names in `source` to write as the header spells them.

    A name is renamed only when the header (comments stripped) does not have it and has
    exactly one identifier equal to it ignoring case. Nothing when the header does not
    define `class PlayerbotAIConfig`: a file that is not the config is no evidence.
    """
    code = _COMMENT.sub(" ", header)
    if not _CLASS.search(code):
        return {}
    declared = set(_IDENTIFIER.findall(code))
    folded: dict[str, list[str]] = {}
    for name in declared:
        folded.setdefault(name.lower(), []).append(name)
    found: dict[str, str] = {}
    for name in _USE.findall(source):
        if name in declared or name in found:
            continue
        twins = folded.get(name.lower(), [])
        if len(twins) == 1:
            found[name] = twins[0]
    return found


def _read_text(path: Path) -> str | None:
    """A source file's text exactly as stored, or None for one that is not UTF-8 or unreadable."""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.info(f"left {path} out of the mod-ale bridge: {exc}")
        return None


def _linked_on_the_way(server_dir: Path, relative: str) -> bool:
    """Is any part of `relative`, walked down from `server_dir`, a link? (cold review)"""
    here = server_dir
    for part in PurePosixPath(relative).parts:
        here = here / part
        if here.is_symlink():
            return True
    return False


def _under_mod_ale(server_dir: Path, path: Path) -> bool:
    """Does `path` resolve inside this server folder's own `modules/mod-ale`? (cold review)

    Both halves resolved: `modules/mod-ale` itself must resolve inside the server folder
    (a junction there, which `is_symlink()` does not see on Windows' Python 3.11, moves
    it out), and `path` inside that.
    """
    try:
        server = server_dir.resolve(strict=True)
        root = (server_dir / ALE).resolve(strict=True)
        return root == server / ALE and path.resolve(strict=True).is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def _sources(server_dir: Path) -> Iterator[Path]:
    """mod-ale's C++ files under `src/`, never through a link (folder or file, anywhere)."""
    root = server_dir / ALE / "src"
    if _linked_on_the_way(server_dir, f"{ALE}/src") or not root.is_dir():
        return
    if not _under_mod_ale(server_dir, root):
        return
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(folder) / name
            if name.endswith(_SOURCE_SUFFIXES) and not path.is_symlink() and path.is_file():
                yield path


def _plan(server_dir: Path) -> tuple[_Bridged, ...]:
    """What a bridge would rewrite here, read-only; empty for every case the docstring lists."""
    header_path = server_dir / CONFIG_HEADER
    if header_path.is_symlink() or not header_path.is_file():
        return ()
    header = _read_text(header_path)
    if header is None:
        return ()
    planned: list[_Bridged] = []
    for path in _sources(server_dir):
        text = _read_text(path)
        if text is None or "sPlayerbotAIConfig." not in text:
            continue
        renames = renames_for(header, text)
        if not renames:
            continue
        bridged = _rewritten(text, renames)
        planned.append(
            _Bridged(path.relative_to(server_dir).as_posix(), text, bridged, dict(renames))
        )
    return tuple(planned)


def _rewritten(text: str, renames: Mapping[str, str]) -> str:
    """`text` with each `sPlayerbotAIConfig.<name>` in `renames` spelled the new way."""
    return _USE.sub(lambda m: f"sPlayerbotAIConfig.{renames.get(m[1], m[1])}", text)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_record(server_dir: Path) -> dict[str, dict[str, str]] | None:
    """The record's files, `{}` for none; None when one is there and cannot be read."""
    path = server_dir / RECORD
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        files = payload["files"]
        if not isinstance(files, dict) or not all(
            isinstance(row, dict)
            and isinstance(row.get("original"), str)
            and isinstance(row.get("bridged_sha256"), str)
            for row in files.values()
        ):
            raise ValueError("unexpected shape")
        return files
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(f"could not read {path}: {exc}")
        return None


def _inside(server_dir: Path, relative: str) -> Path | None:
    """A record's path as a file inside `server_dir`/modules/mod-ale, or None (cold review).

    The record is a file in the server folder, so its paths are not trusted: relative,
    under `modules/mod-ale/`, no `..`, no link on ANY part of the way down (a linked
    `modules/mod-ale` would otherwise be written through), and resolving inside the
    resolved `modules/mod-ale`.
    """
    parts = PurePosixPath(relative)
    if parts.is_absolute() or Path(relative).is_absolute():
        return None
    if not relative.startswith(f"{ALE}/") or ".." in parts.parts:
        return None
    if _linked_on_the_way(server_dir, relative):
        return None
    path = server_dir / relative
    if not path.is_file() or not _under_mod_ale(server_dir, path):
        return None
    return path


def put_back(server_dir: Path) -> list[str]:
    """Put every file the record names back as mod-ale ships it; never raises (T645).

    Called in the compile's `finally`, so it must not hide the build's own outcome: a
    file that cannot be written is said, and the record is kept for the next compile.
    A file whose bytes are no longer the ones the bridge wrote is somebody's: kept.
    """
    files = _read_record(server_dir)
    if not files:
        return []
    said: list[str] = []
    kept: dict[str, dict[str, str]] = {}
    for relative, row in files.items():
        path = _inside(server_dir, relative)
        now = _read_text(path) if path is not None else None
        if path is None or now is None:
            said.append(f"{relative} is not there as the bridge left it; nothing to put back.")
            continue
        if _digest(now) != row["bridged_sha256"]:
            said.append(
                f"{relative} has changed since Yu'lon bridged it for the compile, so it is "
                "left as it is."
            )
            continue
        try:
            conf.replace_file(path, row["original"])
        except InstallerError as exc:
            logger.warning(f"could not put {path} back: {exc}")
            kept[relative] = row
            said.append(
                f"Yu'lon could not put {relative} back as mod-ale ships it ({exc}); the next "
                "build puts it back first."
            )
            continue
        said.append(f"Put {relative} back as mod-ale ships it.")
    record = server_dir / RECORD
    try:
        if kept:
            conf.replace_file(record, json.dumps({"files": kept}, indent=1))
        else:
            record.unlink()
    except (OSError, InstallerError) as exc:
        logger.warning(f"could not update {record}: {exc}")
    return said


def bridge(server_dir: Path) -> list[str]:
    """Rewrite mod-ale's config names to mod-playerbots' for the compile that follows (T645).

    A record left by a compile that never finished is put back first. Then the plan is
    made from the files as they stand; nothing to bridge says nothing. A list and not a
    generator (cold review of db5da4df): every write is done when this returns, so the
    caller holds its `finally` around the lines and a consumer that closes the press at
    one of them still reaches `put_back()`.

    Raises:
        InstallerError: an old record that cannot be read, or a file that cannot be
            written; every file already rewritten is put back first. Nothing was built.
    """
    old = _read_record(server_dir)
    if old is None:
        raise InstallerError(
            f"Yu'lon could not read {server_dir / RECORD}, its note of mod-ale files it "
            "changed for an earlier build, so it cannot tell whether they are as mod-ale "
            "ships them. Put modules/mod-ale back as git has it, delete that file, and press "
            "this again. Nothing was built."
        )
    said = put_back(server_dir) if old else []
    planned = _plan(server_dir)
    if not planned:
        return said
    record = {
        "files": {
            item.path: {"original": item.original, "bridged_sha256": _digest(item.bridged)}
            for item in planned
        }
    }
    try:
        conf.replace_file(server_dir / RECORD, json.dumps(record, indent=1))
        for item in planned:
            conf.replace_file(server_dir / item.path, item.bridged)
    except InstallerError as exc:
        put_back(server_dir)
        raise InstallerError(
            f"Yu'lon could not change mod-ale's Playerbots names for this build: {exc} "
            "Nothing was built."
        ) from exc
    for item in planned:
        names = ", ".join(f"{old} -> {new}" for old, new in item.renames.items())
        said.append(
            f"mod-playerbots and mod-ale's Playerbots support spell some config names "
            f"differently ({names}); Yu'lon compiles {item.path} with mod-playerbots' "
            "spelling and puts the file back as mod-ale ships it once the compile ends."
        )
    return said
