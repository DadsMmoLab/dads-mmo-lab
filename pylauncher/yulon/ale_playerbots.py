"""mod-ale's Playerbots support, built against the names the server's code has (T645, T732).

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

**The session check (T732).** mod-ale 5472013 (#408) calls `GetSession()->IsHeadless()`;
the playerbots core named that `WorldSession::IsBot()` until f19a1879 (v0.9.15's pin), and
Yu'lon never moves an existing server's core, so a server made by 0.9.14 or older failed
every Rebuild once mod-ale was in it. The same rule applies to the core's
`WorldSession.h`: in a file that defines `class WorldSession`, a `GetSession()->IsHeadless(`
(or `IsBot(`) in mod-ale's sources whose name the header does not declare as
`bool <Name>(`, while it declares the other, is written with the declared name, in
either direction. Only that call shape is touched: mod-ale's own `LuaPlayer::IsBot`
and its Lua name table keep their text.

**Why only for the compile, and not left on disk like a carried core patch.** On
WotLK mod-ale is a Modules-tab clone, and its Update refuses a checkout with an
edited tracked file (T44/T47): a rewrite left behind would block the very update
that later fixes this upstream. On Unbound mod-ale is an emulator source, and the
update route's dirty-tree guard would refuse it the same way. So the files are
put back, byte for byte, as soon as the compile returns, and every git question
asked outside the compile sees upstream's file.

**Yu'lon's own fixes (T867).** Some of mod-ale does not fit a server's core at
all, and no mod-ale release follows it: on Conquest of Azeroth the core's
`UnitScript::DealDamage` takes a fifth parameter, its `PlayerMenu` is not
copyable, and the core has no `IsHeadless()`. The catalog names a `ModulePatch`
for exactly that (`wow-coa/patches/mod-ale-coa.patch`), and this module lays it
into the same rewritten text, under the same record and put back by the same
`put_back()`: a patch left on disk would block the Modules tab's Update exactly
as a bridged file would (T44/T47). It is laid FIRST and the name bridge plans
over the patched text, because the two overlap in PlayerMethods.h
(`IsHeadless()` becomes `IsBot()` in the patch): bridged first, the patch's
pre-image would be gone and the patch would refuse. The patch is resolved in a
temporary mirror (`patch.py`'s reviewed code, unchanged) so every hunk is
checked and the record holds the digests of the FINAL bytes before anything in
the server folder is written; a hunk that fits neither way stops the build
before the compile, with nothing written.

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
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NoReturn

from yulon import server_build_presses
from yulon.catalog.families import conf, patch
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

ALE = "modules/mod-ale"
"""Where the Modules tab (WotLK) and the catalog (Unbound) put azerothcore/mod-ale."""

MODULE = PurePosixPath(ALE).name
"""The module's own folder name, the value a `ModulePatch` names (T867)."""

WORLD_SESSION = "src/server/game/Server/WorldSession.h"
"""The core's session class, the header whose names decide the session check (T732)."""

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
_SESSION_CLASS = re.compile(r"\bclass\s+WorldSession\s*[{:]")
_SESSION_NAMES = {"IsHeadless": "IsBot", "IsBot": "IsHeadless"}
_SESSION_USE = re.compile(r"(GetSession\s*\(\s*\)\s*->\s*)(IsHeadless|IsBot)(\s*\()")

PATCHED = (
    "Yu'lon builds mod-ale with its fix for {server} ({name}): {reason} mod-ale's own files "
    "are put back once the compile ends."
)
"""What the build log says when one of Yu'lon's own fixes is laid in for a compile (T867)."""

PATCH_REFUSED = (
    "mod-ale has changed since this version of Yu'lon was made, and the fix Yu'lon needs to "
    "build it on {server} no longer fits it, so the build stopped before it began. Nothing was "
    "built and mod-ale was not changed; your server keeps the build it has. Update Yu'lon to get "
    "a fix that fits this mod-ale, or press Remove on mod-ale's row on the Modules tab and then "
    "{rebuild} to build without it. What no longer fits: {detail}"
)
"""The refusal for a patch that no longer fits the module on disk (T867).

Both ways out are named: updating Yu'lon, or removing the module. The T557
put-back is deliberately not started -- this is not a compile error, and its
own sentences would stack on top of this one (D5).
"""

_PUT_BACK = (
    "mod-ale's changes for the compile were taken back out ({count} {files}); its folder is "
    "as mod-ale ships it."
)
"""One line for a clean put-back (T867's OQ6): every file came back, so say it once."""


@dataclass(frozen=True)
class CompilePatch:
    """A patch to lay into mod-ale for one compile (T867): its file name, text and reason."""

    name: str
    text: str
    reason: str


@dataclass(frozen=True)
class _Bridged:
    """One file the bridge rewrites: where, upstream's text, and what it becomes."""

    path: str
    original: str
    bridged: str
    renames: Mapping[str, str]
    session_renames: Mapping[str, str] = field(default_factory=dict)


def session_renames_for(header: str, source: str) -> dict[str, str]:
    """The `GetSession()-><name>(` names in `source` to write as the core's header spells them.

    A called name is renamed only when the header (comments stripped) defines
    `class WorldSession`, does not declare `bool <name>(`, and declares its twin
    (`IsHeadless` <-> `IsBot`). Nothing otherwise: a core with neither, or both, is left
    to the compiler, and a file that is not the session class is no evidence.
    """
    code = _COMMENT.sub(" ", header)
    if not _SESSION_CLASS.search(code):
        return {}

    def declared(name: str) -> bool:
        return re.search(rf"\bbool\s+{name}\s*\(", code) is not None

    found: dict[str, str] = {}
    for match in _SESSION_USE.finditer(source):
        name = match[2]
        twin = _SESSION_NAMES[name]
        if name not in found and not declared(name) and declared(twin):
            found[name] = twin
    return found


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


def _header_text(server_dir: Path, relative: str) -> str | None:
    """A core or module header's text, None when absent, a link (no evidence) or unreadable."""
    path = server_dir / relative
    if path.is_symlink() or not path.is_file():
        return None
    return _read_text(path)


def _refuse_patch(server: str, detail: str) -> NoReturn:
    """Stop the press before the compile: mod-ale changed under a fix Yu'lon carries."""
    raise InstallerError(
        PATCH_REFUSED.format(
            server=server,
            rebuild=server_build_presses.under_server_build(server_build_presses.REBUILD),
            detail=detail,
        )
    )


def _laid(
    server_dir: Path, patches: Sequence[CompilePatch], server: str
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Lay `patches` over the files on disk, in memory, before anything is written (T867).

    `patch.apply()` stays the reviewed code it is: each patch is applied to a
    temporary mirror holding the CURRENT text of the files it names (on disk, or
    an earlier patch's result), so every hunk is resolved before a byte of the
    server folder is touched, and patches chain through the overlay. The record
    `bridge()` writes must hold the digests of the FINAL bytes, which is why the
    patched text is read back here rather than written into the module.

    Every path is asked of `_inside()`, which is the record's own rule: under
    `modules/mod-ale/`, no `..`, no link on the way, a file that exists. A file
    the patch CREATES is refused: `put_back()` could only unlink it, and a
    tracked clone would still call that a change.

    Returns:
        The overlay (server-relative posix path -> patched text, only the files
        that changed) and the names of the patches that changed at least one
        file, in the order given.
    """
    overlay: dict[str, str] = {}
    changed: list[str] = []
    for spec in patches:
        try:
            hunks = patch.parse(spec.text)
        except patch.PatchError as exc:
            _refuse_patch(server, str(exc))
        for hunk in hunks:
            if hunk.creates:
                _refuse_patch(
                    server,
                    f"{spec.name} would create {hunk.path}, which Yu'lon cannot put back",
                )
        named: dict[str, str] = {}
        for hunk in hunks:
            relative = f"{ALE}/{hunk.path}"
            path = _inside(server_dir, relative)
            if path is None:
                _refuse_patch(server, f"mod-ale has no {hunk.path} as Yu'lon expects it")
            if hunk.path in named:
                continue
            text = overlay.get(relative)
            if text is None:
                text = _read_text(path)
            if text is None:
                _refuse_patch(server, f"{relative} is not a text file Yu'lon can read")
            named[hunk.path] = text
        with tempfile.TemporaryDirectory(prefix="yulon-ale-patch-") as folder:
            mirror = Path(folder)
            for inside in named:
                target = mirror.joinpath(*PurePosixPath(inside).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(named[inside].encode("utf-8"))
            try:
                patch.apply(spec.text, mirror, name=spec.name)
            except patch.PatchError as exc:
                _refuse_patch(server, str(exc))
            for inside, was in named.items():
                target = mirror.joinpath(*PurePosixPath(inside).parts)
                after = target.read_bytes().decode("utf-8")
                if after == was:
                    continue
                overlay[f"{ALE}/{inside}"] = after
                if spec.name not in changed:
                    changed.append(spec.name)
    return overlay, tuple(changed)


def _plan(server_dir: Path, overlay: Mapping[str, str]) -> tuple[_Bridged, ...]:
    """What a bridge would rewrite here, read-only; empty for every case the docstring lists.

    `overlay` is `_laid()`'s patched text. Each file's base is the overlay's
    text where there is one and the disk's otherwise; `_Bridged.original` is
    ALWAYS the disk (upstream) text, because that is what `put_back()` restores.
    A file is planned when the bridge's result differs from that upstream text,
    so a patched file is planned even when no name changes, and a file the
    patch left alone is not. A file the patch names outside `src/` (which
    `_sources()` never yields) is planned too, after the walk.
    """
    config = _header_text(server_dir, CONFIG_HEADER)
    session = _header_text(server_dir, WORLD_SESSION)
    planned: list[_Bridged] = []
    seen: set[str] = set()
    for path in _sources(server_dir):
        relative = path.relative_to(server_dir).as_posix()
        seen.add(relative)
        original = _read_text(path)
        if original is None:
            continue
        base = overlay.get(relative, original)
        renames = renames_for(config, base) if config is not None else {}
        session_renames = session_renames_for(session, base) if session is not None else {}
        bridged = _rewritten(base, renames, session_renames)
        if bridged == original:
            continue
        planned.append(
            _Bridged(
                relative,
                original,
                bridged,
                dict(renames),
                dict(session_renames),
            )
        )
    for relative in sorted(set(overlay) - seen):
        named = _inside(server_dir, relative)
        original = _read_text(named) if named is not None else None
        if original is None:
            continue
        planned.append(_Bridged(relative, original, overlay[relative], {}, {}))
    return tuple(planned)


def _rewritten(
    text: str, renames: Mapping[str, str], session_renames: Mapping[str, str] | None = None
) -> str:
    """`text` with each `sPlayerbotAIConfig.<name>` and `GetSession()-><name>(` spelled anew."""
    text = _USE.sub(lambda m: f"sPlayerbotAIConfig.{renames.get(m[1], m[1])}", text)
    sessions = session_renames or {}
    return _SESSION_USE.sub(lambda m: f"{m[1]}{sessions.get(m[2], m[2])}{m[3]}", text)


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

    Files that came back clean fold into ONE line (T867): a module with eleven
    patched files would otherwise write eleven lines after every compile. A file
    that could not be put back keeps its own line, after that summary.
    """
    files = _read_record(server_dir)
    if not files:
        return []
    problems: list[str] = []
    kept: dict[str, dict[str, str]] = {}
    clean = 0
    for relative, row in files.items():
        path = _inside(server_dir, relative)
        now = _read_text(path) if path is not None else None
        if path is None or now is None:
            problems.append(f"{relative} is not there as the bridge left it; nothing to put back.")
            continue
        if _digest(now) != row["bridged_sha256"]:
            problems.append(
                f"{relative} has changed since Yu'lon bridged it for the compile, so it is "
                "left as it is."
            )
            continue
        try:
            conf.replace_file(path, row["original"])
        except InstallerError as exc:
            logger.warning(f"could not put {path} back: {exc}")
            kept[relative] = row
            problems.append(
                f"Yu'lon could not put {relative} back as mod-ale ships it ({exc}); the next "
                "build puts it back first."
            )
            continue
        clean += 1
    said = [_PUT_BACK.format(count=clean, files="file" if clean == 1 else "files")] if clean else []
    said.extend(problems)
    record = server_dir / RECORD
    try:
        if kept:
            conf.replace_file(record, json.dumps({"files": kept}, indent=1))
        else:
            record.unlink()
    except (OSError, InstallerError) as exc:
        logger.warning(f"could not update {record}: {exc}")
    return said


def bridge(
    server_dir: Path, patches: Sequence[CompilePatch] = (), *, server: str = "this server"
) -> list[str]:
    """Rewrite mod-ale's config and session-check names for the compile (T732), and lay
    Yu'lon's own fixes into it first (T867).

    A record left by a compile that never finished is put back first. Then every
    `patches` entry is laid over the text on disk, in memory, and the name bridge
    plans over the result, so the two share one record and one `put_back()`. The
    patch goes first because PlayerMethods.h is touched by both, and the bridge
    would destroy the patch's pre-image (`IsHeadless()` -> `IsBot()`). Nothing to
    lay and nothing to bridge says nothing. A list and not a generator (cold review
    of db5da4df): every write is done when this returns, so the caller holds its
    `finally` around the lines and a consumer that closes the press at one of them
    still reaches `put_back()`.

    The patch file is never read here: a caller that has no mod-ale passes none
    (D3), and one with a patch it cannot read learns that before this is called.

    Raises:
        InstallerError: an old record that cannot be read, a patch whose hunks no
            longer fit mod-ale (nothing written), or a file that cannot be written;
            every file already rewritten is put back first. Nothing was built.
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
    overlay: dict[str, str] = {}
    changed: tuple[str, ...] = ()
    if patches and (server_dir / ALE).is_dir():
        overlay, changed = _laid(server_dir, patches, server)
    planned = _plan(server_dir, overlay)
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
    reasons = {spec.name: spec.reason for spec in patches}
    for name in changed:
        said.append(PATCHED.format(server=server, name=name, reason=reasons[name]))
    for item in planned:
        if item.renames:
            names = ", ".join(f"{old} -> {new}" for old, new in item.renames.items())
            said.append(
                f"mod-playerbots and mod-ale's Playerbots support spell some config names "
                f"differently ({names}); Yu'lon compiles {item.path} with mod-playerbots' "
                "spelling and puts the file back as mod-ale ships it once the compile ends."
            )
        if item.session_renames:
            names = ", ".join(f"{old} -> {new}" for old, new in item.session_renames.items())
            said.append(
                f"This server's core and mod-ale name a session check differently ({names}); "
                f"Yu'lon compiles {item.path} with the core's name and puts the file back "
                "as mod-ale ships it once the compile ends."
            )
    return said
