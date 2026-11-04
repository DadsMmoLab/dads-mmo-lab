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
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NoReturn, TypedDict

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
    "built and mod-ale was not changed{kept}. Update Yu'lon to get a fix that fits this mod-ale, "
    "or press Remove on mod-ale's row on the Modules tab and then {rebuild} to build without it. "
    "What no longer fits: {detail}"
)
"""The refusal for a patch that no longer fits the module on disk (T867).

Both ways out are named: updating Yu'lon, or removing the module. The T557
put-back is deliberately not started -- this is not a compile error, and its
own sentences would stack on top of this one (D5).

`{kept}` is "; your server keeps the build it has" only when a build exists
(the state record says the `build` stage finished, or the daemon holds the
images): on a first install there is no build to keep, and saying there was
would be a claim about a folder that has never compiled (T867-C C4).
"""

_KEPT_BUILD = "; your server keeps the build it has"

LINK_REFUSED = (
    "{link} is a link, and Yu'lon does not write or compile mod-ale through a link, so the "
    "build stopped before it began. Nothing was built and mod-ale was not changed. Put the file "
    "mod-ale ships back in its place (or remove the link), and press {rebuild} again."
)
"""The refusal for a mod-ale file or folder the patch names that is a link (T867-C C4).

Its own sentence, not `PATCH_REFUSED`'s: the module has not changed under the
fix, the folder is arranged in a way Yu'lon never writes through, and the two
asks (update/remove the module, put the file back) are different.
"""

_PUT_BACK = (
    "mod-ale's changes for the compile were taken back out ({count} {files}); its folder is "
    "as mod-ale ships it."
)
"""One line for a clean put-back (T867's OQ6): every file came back, so say it once."""

_PARTLY_BACK = (
    "mod-ale's changes for the compile were taken back out of {clean} of {total} {files}."
)
"""The same line when a row was kept (T867-C C3): it never claims the folder is as shipped."""

_CHANGED = (
    "{relative} was changed during the build, so Yu'lon left it as it is; Remove and add "
    "mod-ale again, or put it back by hand."
)
"""A row whose file is neither what the bridge wrote nor what mod-ale ships (T867-C C1)."""

_UNREADABLE = (
    "{relative} could not be read, so Yu'lon left it as it is; Remove and add mod-ale again, "
    "or put it back by hand."
)
"""A row whose file exists but cannot be read (T867-C C1); the row is kept."""

_MISPLACED = (
    "{relative} is not where the record says Yu'lon put it, so it was left as it is; Remove and "
    "add mod-ale again, or put it back by hand."
)
"""A record path that is not a safe slot under this server's own mod-ale (T867-C C1)."""

_NOT_PUT = (
    "Yu'lon could not put {relative} back as mod-ale ships it ({error}); the next build stops "
    "until mod-ale is removed and added again on the Modules tab."
)
"""A file the put-back tried to write and could not (T867-C C1); the row is kept."""


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
    mode: int = 0o644


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


def _link_on_the_way(server_dir: Path, relative: str) -> str | None:
    """The first part of `relative`, walked down from `server_dir`, that is a link."""
    here = server_dir
    for part in PurePosixPath(relative).parts:
        here = here / part
        if here.is_symlink():
            return str(here)
    return None


def _linked_on_the_way(server_dir: Path, relative: str) -> bool:
    """Is any part of `relative`, walked down from `server_dir`, a link? (cold review)"""
    return _link_on_the_way(server_dir, relative) is not None


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


def _refuse_patch(
    server: str, detail: str, has_build: Callable[[], bool] | None = None
) -> NoReturn:
    """Stop the press before the compile: mod-ale changed under a fix Yu'lon carries."""
    raise InstallerError(
        PATCH_REFUSED.format(
            server=server,
            rebuild=server_build_presses.under_server_build(server_build_presses.REBUILD),
            detail=detail,
            kept=_KEPT_BUILD if has_build is not None and has_build() else "",
        )
    )


def _laid(
    server_dir: Path,
    patches: Sequence[CompilePatch],
    server: str,
    has_build: Callable[[], bool] | None = None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Lay `patches` over the files on disk, in memory, before anything is written (T867).

    `patch.apply()` stays the reviewed code it is: each patch is applied to a
    temporary mirror holding the CURRENT text of the files it names (on disk, or
    an earlier patch's result), so every hunk is resolved before a byte of the
    server folder is touched, and patches chain through the overlay. The record
    `bridge()` writes must hold the digests of the FINAL bytes, which is why the
    patched text is read back here rather than written into the module.

    Every path is asked of `_inside()`, which is the record's own rule: under
    `modules/mod-ale/`, no `..`, no link on the way, a file that exists. A path
    that is absolute, or carries `..`, is refused before the mirror is made
    (T867-C C4): a crafted patch never gets a write outside the temporary folder.
    A link on the way refuses with `LINK_REFUSED`, its own sentence naming the
    link, rather than `PATCH_REFUSED`'s "mod-ale has changed" (T867-C C4). A file
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
            _refuse_patch(server, str(exc), has_build)
        for hunk in hunks:
            if hunk.creates:
                _refuse_patch(
                    server,
                    f"{spec.name} would create {hunk.path}, which Yu'lon cannot put back",
                    has_build,
                )
            named_path = PurePosixPath(hunk.path)
            if (
                named_path.is_absolute()
                or Path(hunk.path).is_absolute()
                or ".." in named_path.parts
            ):
                _refuse_patch(
                    server,
                    f"{spec.name} names {hunk.path}, which is outside mod-ale",
                    has_build,
                )
        named: dict[str, str] = {}
        for hunk in hunks:
            relative = f"{ALE}/{hunk.path}"
            link = _link_on_the_way(server_dir, relative)
            if link is not None:
                raise InstallerError(
                    LINK_REFUSED.format(
                        link=link,
                        rebuild=server_build_presses.under_server_build(
                            server_build_presses.REBUILD
                        ),
                    )
                )
            path = _inside(server_dir, relative)
            if path is None:
                _refuse_patch(server, f"mod-ale has no {hunk.path} as Yu'lon expects it", has_build)
            if hunk.path in named:
                continue
            text = overlay.get(relative)
            if text is None:
                text = _read_text(path)
            if text is None:
                _refuse_patch(server, f"{relative} is not a text file Yu'lon can read", has_build)
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
                _refuse_patch(server, str(exc), has_build)
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
                _mode_of(path),
            )
        )
    for relative in sorted(set(overlay) - seen):
        named = _inside(server_dir, relative)
        if named is None:
            continue
        original = _read_text(named)
        if original is None:
            continue
        planned.append(_Bridged(relative, original, overlay[relative], {}, {}, _mode_of(named)))
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


def _mode_of(path: Path) -> int:
    """The file's permission bits, for the record to put them back (T867-D D2).

    A file the put-back has to RECREATE gets this mode; one still on disk keeps
    its own. 0644 when it cannot be read: the mode files in a checkout have.
    """
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return 0o644


class _Row(TypedDict):
    """The two fields every record row has always had."""

    original: str
    bridged_sha256: str


class _StoredRow(_Row, total=False):
    """A row as stored: `mode` is absent in records written before T867-D."""

    mode: int


def _read_record(server_dir: Path) -> dict[str, _StoredRow] | None:
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
            and (row.get("mode") is None or isinstance(row.get("mode"), int))
            for row in files.values()
        ):
            raise ValueError("unexpected shape")
        return files
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(f"could not read {path}: {exc}")
        return None


def _slot(server_dir: Path, relative: str) -> Path | None:
    """Where a record's path belongs inside `server_dir`/modules/mod-ale, or None.

    The record is a file in the server folder, so its paths are not trusted: relative,
    under `modules/mod-ale/`, no `..`, no link on ANY part of the way down (a linked
    `modules/mod-ale` would otherwise be written through), and its folder resolving
    inside the resolved `modules/mod-ale`. Unlike `_inside()`, the file may be ABSENT:
    a row whose file was deleted during the compile still has a place to go back to
    (T867-C C1).
    """
    parts = PurePosixPath(relative)
    if parts.is_absolute() or Path(relative).is_absolute():
        return None
    if not relative.startswith(f"{ALE}/") or ".." in parts.parts:
        return None
    if _linked_on_the_way(server_dir, relative):
        return None
    path = server_dir / relative
    if not _under_mod_ale(server_dir, path.parent):
        return None
    if path.exists() and not path.is_file():
        return None
    return path


def _inside(server_dir: Path, relative: str) -> Path | None:
    """A record's path as a file inside `server_dir`/modules/mod-ale, or None (cold review).

    The slot's rules, and the file must be there: this is the read side. Writing a
    row whose file is gone goes through `_slot()`.
    """
    path = _slot(server_dir, relative)
    if path is None or not path.is_file():
        return None
    return path


def put_back(server_dir: Path) -> list[str]:
    """Put every file the record names back as mod-ale ships it; never raises (T645).

    Called in the compile's `finally`, so it must not hide the build's own outcome.
    Every row that did not end with mod-ale's text on disk stays in the record
    (T867-C C1), so the next compile cannot quietly drop it:

    * a file still holding what the bridge wrote is put back;
    * a file already holding mod-ale's text is back, and is not written again;
    * a file that is gone is written back from the record -- a clone needs it, and
      the record is the only copy left;
    * a file that is unreadable, no longer either text (somebody changed it during
      the compile), or no longer where the record put it is LEFT AS IT IS and its
      row is kept. `bridge()` refuses to compile over such a record.

    With `modules/mod-ale` itself gone there is nothing left to protect, so the
    record is deleted and nothing is said (T867-D D1). That is what makes the
    way out every kept row names -- Remove and add mod-ale again -- work: Remove
    deletes the clone, and the record must not outlive it.

    Files that came back clean fold into ONE line (T867): a module with eleven
    patched files would otherwise write eleven lines after every compile. Only when
    every row is back does that line say the folder is as mod-ale ships it; a kept
    row makes it say how many came back instead, followed by the per-file lines
    (T867-C C3).
    """
    files = _read_record(server_dir)
    if not files:
        return []
    record = server_dir / RECORD
    if not (server_dir / ALE).is_dir():
        try:
            record.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(f"could not remove {record}: {exc}")
        return []
    problems: list[str] = []
    kept: dict[str, _StoredRow] = {}
    clean = 0
    total = len(files)
    for relative, row in files.items():
        path = _inside(server_dir, relative)
        if path is None:
            slot = _slot(server_dir, relative)
            if slot is None:
                kept[relative] = row
                problems.append(_MISPLACED.format(relative=relative))
                continue
            try:
                conf.replace_file(slot, row["original"], mode=row.get("mode", 0o644))
            except InstallerError as exc:
                logger.warning(f"could not put {slot} back: {exc}")
                kept[relative] = row
                problems.append(_NOT_PUT.format(relative=relative, error=exc))
                continue
            clean += 1
            continue
        now = _read_text(path)
        if now is None:
            kept[relative] = row
            problems.append(_UNREADABLE.format(relative=relative))
            continue
        digest = _digest(now)
        if digest == _digest(row["original"]):
            clean += 1
            continue
        if digest != row["bridged_sha256"]:
            kept[relative] = row
            problems.append(_CHANGED.format(relative=relative))
            continue
        try:
            conf.replace_file(path, row["original"])
        except InstallerError as exc:
            logger.warning(f"could not put {path} back: {exc}")
            kept[relative] = row
            problems.append(_NOT_PUT.format(relative=relative, error=exc))
            continue
        clean += 1
    files_word = "file" if total == 1 else "files"
    if kept:
        said = [_PARTLY_BACK.format(clean=clean, total=total, files=files_word)]
    elif clean:
        said = [_PUT_BACK.format(count=clean, files="file" if clean == 1 else "files")]
    else:
        said = []
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
    server_dir: Path,
    patches: Sequence[CompilePatch] = (),
    *,
    server: str = "this server",
    has_build: Callable[[], bool] | None = None,
) -> list[str]:
    """Rewrite mod-ale's config and session-check names for the compile (T732), and lay
    Yu'lon's own fixes into it first (T867).

    A record left by a compile that never finished is put back first. If a row STAYS in
    it -- a file somebody changed, or one Yu'lon could not read or write -- this refuses
    before laying anything (T867-C C2): the next compile would otherwise overwrite the
    record that put-back just narrowed, forgetting the row it could not settle. The
    refusal carries that row's own sentence and names the file. Then every `patches`
    entry is laid over the text on disk, in memory, and the name bridge plans over the
    result, so the two share one record and one `put_back()`. The patch goes first
    because PlayerMethods.h is touched by both, and the bridge would destroy the
    patch's pre-image (`IsHeadless()` -> `IsBot()`). Nothing to lay and nothing to
    bridge says nothing. A list and not a generator (cold review of db5da4df): every
    write is done when this returns, so the caller holds its `finally` around the lines
    and a consumer that closes the press at one of them still reaches `put_back()`.

    The patch file is never read here: a caller that has no mod-ale passes none
    (D3), and one with a patch it cannot read learns that before this is called.
    `has_build` is only for the refusal's sentence: a callable, asked only when a
    refusal needs the clause, that says whether a build already exists (the state
    record or an image), so the sentence may say the server keeps it (T867-C C4,
    T867-D D3). Never asked on a build that goes ahead: no `docker image inspect`
    is worth paying for a sentence nobody will read.

    Raises:
        InstallerError: an old record that cannot be read, a record a put-back could
            not clear (nothing written), a patch whose hunks no longer fit mod-ale or
            name a link (nothing written), or a file that cannot be written; every
            file already rewritten is put back first. Nothing was built.
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
    left = _read_record(server_dir)
    if left is None:
        raise InstallerError(
            f"Yu'lon could not read {server_dir / RECORD} after putting mod-ale back, so it "
            "cannot tell whether a file is still changed. Put modules/mod-ale back as git has "
            "it, delete that file, and press this again. Nothing was built."
        )
    if left:
        first = next(iter(left))
        reason = next(
            (line for line in said if line.startswith(first)),
            _CHANGED.format(relative=first),
        )
        raise InstallerError(
            f"Yu'lon could not put all of mod-ale back after an earlier build, so it stopped "
            f"before this one: {reason} Nothing was built."
        )
    overlay: dict[str, str] = {}
    changed: tuple[str, ...] = ()
    if patches and (server_dir / ALE).is_dir():
        overlay, changed = _laid(server_dir, patches, server, has_build)
    planned = _plan(server_dir, overlay)
    if not planned:
        return said
    record = {
        "files": {
            item.path: {
                "original": item.original,
                "bridged_sha256": _digest(item.bridged),
                "mode": item.mode,
            }
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
