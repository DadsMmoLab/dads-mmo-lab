"""Where each compiled module's clone was before an Update moved it, per server (T557).

An Update resets `modules/<id>` onto the module's newest commit before anything
is compiled, and the Rebuild that follows is a separate press. When that build
fails inside the module, the old build keeps running but the clone stays on the
commit that does not compile, and every later Rebuild fails on it again. Putting
the clone back needs the commit it came from, and before T557 nothing kept it.

**Where it lives, and why there.** `<server_dir>/.yulon-module-moves.json`,
beside `.yulon-module-updates.json`, and not inside each clone's claim file.
`write_clone_claim()` writes the whole record on every call, so a new key there
has to be threaded through every one of `install()`'s claim writes, and one that
misses it erases it (`apply.py`, `_finish_claim()`). Settling every move after a
good build is one write here, and "which modules moved since the last good
build?" is one read with no walk over `modules/`.

**The shape.** `moves` maps `<type>/<id>` to the commit the clone left (`from`),
the one it landed on (`to`, empty until the second write), the release tags of
both for a module that follows its releases, the time, and whether the update
ran database changes (`sql`). `skipped` maps the same key to a tip that was put
back after it failed to build, which "Check for updates" does not offer again.
`built_unix` is when the last build that worked finished.

**An entry is only ever acted on while the clone's HEAD is still its `to`**
(`Move.unbuilt_at()`). A hand reset, a later Update or a Remove and reinstall all
move HEAD, and the entry is then simply not a match.

Reading fails closed. A file that is there and cannot be used (torn, an unknown
version, a wrong shape) reads as `None`, which the automatic put-back treats as
"no record": nothing is put back by itself. Writing leaves such a file alone and
says why, so a newer build's record is never overwritten by an older build.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from yulon.log import get_logger

logger = get_logger(__name__)

MOVES_FILE = ".yulon-module-moves.json"
"""The record's name in the server folder."""

VERSION = 1


def key(family: str, item_id: str) -> str:
    """The record's key for one module: `<type>/<id>`, as `module_answers` keys its own."""
    return f"{family}/{item_id}"


@dataclass(frozen=True)
class Move:
    """One Update that has not been built yet: the commit it left and the one it landed on."""

    from_sha: str
    to_sha: str | None
    from_release: str = ""
    to_release: str = ""
    at: str = ""
    sql: bool = False
    """The update ran database changes (`direct` SQL) that a put-back does not undo (D5)."""

    def unbuilt_at(self, head: str | None) -> bool:
        """Is this move still the one on disk, with the clone's HEAD at `head`?

        `to` empty is an app that died between the reset and the second write:
        the move happened if HEAD has left `from`. A HEAD git would not give is
        never a match, so nothing is done on it.
        """
        if head is None:
            return False
        if self.to_sha is None:
            return head != self.from_sha
        return head == self.to_sha


@dataclass(frozen=True)
class Skip:
    """A tip that was put back after it failed to build, and is not offered again."""

    tip: str
    at: str = ""


@dataclass(frozen=True)
class Ledger:
    """The whole record as read: every unbuilt move, every skipped tip, the last good build."""

    moves: Mapping[str, Move] = field(default_factory=dict)
    skipped: Mapping[str, Skip] = field(default_factory=dict)
    built_unix: int | None = None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"not a commit: {value!r}")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"not text: {value!r}")
    return value


def _move(said: object) -> Move:
    if not isinstance(said, dict):
        raise ValueError(f"not a move: {said!r}")
    to = said.get("to")
    sql = said.get("sql", False)
    if not isinstance(sql, bool):
        raise ValueError(f"not a yes/no: {sql!r}")
    return Move(
        from_sha=_sha(said.get("from")),
        to_sha=None if to is None else _sha(to),
        from_release=_text(said.get("from_release", "")),
        to_release=_text(said.get("to_release", "")),
        at=_text(said.get("at", "")),
        sql=sql,
    )


def _skip(said: object) -> Skip:
    if not isinstance(said, dict):
        raise ValueError(f"not a skip: {said!r}")
    return Skip(tip=_sha(said.get("tip")), at=_text(said.get("at", "")))


def _parse(raw: object) -> Ledger:
    """The record, or `ValueError` for anything this build cannot use whole."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    if raw.get("version") != VERSION:
        raise ValueError(f"version {raw.get('version')!r}, this build reads {VERSION}")
    moves = raw.get("moves", {})
    skipped = raw.get("skipped", {})
    built = raw.get("built_unix")
    if not isinstance(moves, dict) or not isinstance(skipped, dict):
        raise ValueError("moves and skipped must be maps")
    if built is not None and (not isinstance(built, int) or isinstance(built, bool)):
        raise ValueError(f"built_unix is not a time: {built!r}")
    return Ledger(
        moves={_text(k): _move(v) for k, v in moves.items()},
        skipped={_text(k): _skip(v) for k, v in skipped.items()},
        built_unix=built,
    )


def _load(path: Path) -> tuple[dict[str, Any] | None, str]:
    """The raw record and "" when usable; `({}, "")` when absent; `(None, why)` otherwise."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return None, f"{path} could not be read ({exc})"
    try:
        raw = json.loads(text)
        _parse(raw)
    except ValueError as exc:
        return None, f"{path} is not a record this Yu'lon can read ({exc}), so it was left as it is"
    return raw, ""


def read(server_dir: Path) -> Ledger | None:
    """The record at `server_dir`: empty when there is none, `None` when it cannot be used."""
    raw, problem = _load(server_dir / MOVES_FILE)
    if raw is None:
        logger.warning(f"no usable module-update record: {problem}")
        return None
    if not raw:
        return Ledger()
    return _parse(raw)


def _write(server_dir: Path, change: Callable[[dict[str, Any]], None], what: str) -> str:
    """Load the record, apply `change`, and put it back in one rename. "" or why not.

    Never raises: the record is this app's bookkeeping, and the press that
    writes it (an Update, a Rebuild, a Remove) has done its work either way. A
    temp file of this writer's own in the same folder, flushed, renamed over the
    record, and the folder flushed, as `write_clone_claim()` writes the claim.
    A record that is there and cannot be used is left alone.
    """
    path = server_dir / MOVES_FILE
    raw, problem = _load(path)
    if raw is None:
        logger.warning(f"did not record {what}: {problem}")
        return problem
    payload: dict[str, Any] = {
        "version": VERSION,
        "moves": dict(raw.get("moves", {})),
        "skipped": dict(raw.get("skipped", {})),
    }
    if raw.get("built_unix") is not None:
        payload["built_unix"] = raw["built_unix"]
    before = json.dumps(payload, sort_keys=True)
    change(payload)
    if json.dumps(payload, sort_keys=True) == before:
        # Nothing to say: a Remove of a module with no entry, an end with no
        # start. No file appears on a server that never had a record.
        return ""
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=server_dir, prefix=MOVES_FILE + ".", suffix=".tmp")
        os.close(fd)
        tmp = Path(name)
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        logger.warning(f"could not record {what} in {path}: {exc}")
        return f"{path} could not be written ({exc})"
    _fsync_folder(server_dir)
    return ""


def _fsync_folder(folder: Path) -> None:
    """Flush the rename into `folder`; POSIX only, best effort (NTFS journals the rename)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def record_start(server_dir: Path, item: str, *, head: str, release: str) -> str:
    """The write BEFORE an Update moves `item`'s clone, from HEAD as it is now.

    **The first `from` is kept.** An entry that is still unbuilt at `head` (an
    earlier Update nobody has built) keeps its `from`: A→B unbuilt, then B→C,
    must put back to A, the commit the running server was built from, not to B,
    which was never built. Any other entry is replaced.
    """

    def change(payload: dict[str, Any]) -> None:
        moves = payload["moves"]
        before = moves.get(item)
        if before is not None and _move(before).unbuilt_at(head):
            entry = dict(before)
        else:
            entry = {"from": head, "from_release": release, "sql": False}
        entry.update({"to": None, "to_release": "", "at": _now()})
        moves[item] = entry

    return _write(server_dir, change, f"where {item} was before its update")


def record_end(server_dir: Path, item: str, *, head: str, release: str) -> str:
    """The write AFTER the clone step returned: where it landed. Nothing moved, nothing kept."""

    def change(payload: dict[str, Any]) -> None:
        moves = payload["moves"]
        before = moves.get(item)
        if before is None:
            return
        if before.get("from") == head:
            del moves[item]
            return
        moves[item] = {**before, "to": head, "to_release": release}

    return _write(server_dir, change, f"where {item}'s update landed")


def mark_sql(server_dir: Path, item: str) -> str:
    """Say that `item`'s unbuilt update ran database changes, which a put-back keeps (D5)."""

    def change(payload: dict[str, Any]) -> None:
        before = payload["moves"].get(item)
        if before is not None:
            payload["moves"][item] = {**before, "sql": True}

    return _write(server_dir, change, f"that {item}'s update changed the database")


def settle(server_dir: Path, *, now_unix: int | None = None) -> str:
    """A build worked: every clone on disk was just compiled, so no move is unbuilt any more.

    A server with no record gets none: there is no move to settle, and every
    game's Rebuild calls this.
    """
    stamp = int(time.time()) if now_unix is None else now_unix
    if not (server_dir / MOVES_FILE).exists():
        return ""

    def change(payload: dict[str, Any]) -> None:
        payload["moves"] = {}
        payload["built_unix"] = stamp

    return _write(server_dir, change, "the build that worked")


def skip(server_dir: Path, item: str, *, tip: str) -> str:
    """`item` was put back off `tip`: drop its move and do not offer `tip` again (D2)."""

    def change(payload: dict[str, Any]) -> None:
        payload["moves"].pop(item, None)
        payload["skipped"][item] = {"tip": tip, "at": _now()}

    return _write(server_dir, change, f"the version of {item} that was put back")


def clear_skip(server_dir: Path, item: str) -> str:
    """Offer `item`'s skipped tip again: the player said try it anyway."""

    def change(payload: dict[str, Any]) -> None:
        payload["skipped"].pop(item, None)

    return _write(server_dir, change, f"that {item}'s skipped version may be offered again")


def drop(server_dir: Path, item: str) -> str:
    """`item` was removed: forget its move and its skip."""

    def change(payload: dict[str, Any]) -> None:
        payload["moves"].pop(item, None)
        payload["skipped"].pop(item, None)

    return _write(server_dir, change, f"that {item} was removed")


# -- which module a failed build names --------------------------------------

_MODULE = r"/azerothcore/modules/(?P<id>[^/\s:'\"]+)/[^\s:'\"]*"
_ERRORS = (
    # The compiler: `<path>:<line>[:<col>]: [fatal ]error:`.
    re.compile(_MODULE + r":\d+(?::\d+)?: (?:fatal )?error:"),
    # The linker: `<path>:<line>: undefined reference to`.
    re.compile(_MODULE + r":\d+: undefined reference to"),
    # CMake, configuring the module.
    re.compile(r"CMake Error at " + _MODULE),
)
"""The three error shapes that name a module folder, and only those.

Anchored on `:<line>:` and the word `error` (or the linker's and CMake's own
words) on purpose: worldserver's `Applying of file '/azerothcore/modules/<id>/
data/sql/…'` also names a module folder, and a startup SQL problem must never put
a module back. Searched anywhere in the line, because what the engine yields is
BuildKit's `#12 512.3 ` prefix and the panel's tool marker in front of the
compiler's words.
"""


class BuildErrorScanner:
    """The module folders a build's compiler, linker or CMake errors name, in the order met.

    Fed every line the rebuild yields. It reads the stream and not the error's
    last words: those are five lines and 400 characters, and often lose the path.
    """

    def __init__(self) -> None:
        self._named: dict[str, None] = {}

    def feed(self, line: str) -> None:
        for pattern in _ERRORS:
            for found in pattern.finditer(line):
                self._named.setdefault(found.group("id"), None)

    @property
    def named(self) -> tuple[str, ...]:
        return tuple(self._named)
