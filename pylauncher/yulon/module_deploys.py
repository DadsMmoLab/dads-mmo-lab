"""Which files each item's `deploy` steps wrote into the server folder, per server (T684).

Before this record, `Applier._undeploy()` worked out what a Remove deletes from the
item's CURRENT checkout, and for a folder it deleted each top-level name whole. Battle
Pass deploys `lua_scripts/` into the `lua_scripts` folder every ALE script shares, so
its Remove ran `rm -r lua_scripts/lib` and took the player's own Lua libraries with
it. An Update (a second install over the new checkout) only ever added files, so a
script upstream renamed or dropped stayed loaded, and a Remove missed it too.

**What it holds.** `items` maps `<type>/<id>` to the server-relative paths the item's
deploy wrote (`files`), the folders it deployed into (`roots`: an emptied folder below
one of them is taken back, the root itself never is) and the player's own files a
deploy found at one of its paths and moved aside first (`asides`: path -> where the
player's file is now). A Remove deletes exactly `files`, puts each aside back, and
drops the entry. An Update deletes the recorded files the new version no longer has.

**Where it lives.** `<server_dir>/.yulon-module-deploys.json`, beside
`.yulon-module-moves.json`, for that record's reason: the clone claim is rewritten
whole by every one of `install()`'s writes, and a key one of them misses is lost.

Reading fails closed. A file that is there and cannot be used reads as `None`, which
the applier treats as "not known": nothing is set aside or deleted on its word, and
writing leaves such a file alone and says why.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from yulon.log import get_logger

logger = get_logger(__name__)

_WRITING = threading.RLock()
"""One writer of the record at a time, in this process (as `module_moves._WRITING`)."""

DEPLOYS_FILE = ".yulon-module-deploys.json"
"""The record's name in the server folder."""

VERSION = 1


def key(family: str, item_id: str) -> str:
    """The record's key for one item: `<type>/<id>`, as `module_moves` keys its own."""
    return f"{family}/{item_id}"


@dataclass(frozen=True)
class Deployed:
    """One item's deployed files, the folders they went into, and the player's files set aside.

    `files` maps each server-relative path to the SHA-256 of the bytes this app last left
    there (after its own patches), `""` when not known: a file whose bytes differ was
    changed by the player and is never deleted on this record's word (review round 2).
    """

    files: Mapping[str, str] = field(default_factory=dict)
    roots: tuple[str, ...] = ()
    asides: Mapping[str, str] = field(default_factory=dict)

    def root_of(self, rel: str) -> str | None:
        """The deepest recorded root `rel` lies strictly below, or None."""
        parts = PurePosixPath(rel).parts
        best: str | None = None
        for root in self.roots:
            rparts = PurePosixPath(root).parts
            if len(rparts) < len(parts) and parts[: len(rparts)] == rparts:
                if best is None or len(rparts) > len(PurePosixPath(best).parts):
                    best = root
        return best

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "files": dict(sorted(self.files.items())),
            "roots": sorted(self.roots),
        }
        if self.asides:
            out["asides"] = dict(sorted(self.asides.items()))
        return out


_DRIVE = re.compile(r"^[A-Za-z]:")
"""A Windows drive at the start: `C:/x` is not absolute to `PurePosixPath`, and must not pass."""


def _inside(value: object) -> str:
    """A server-relative POSIX path that stays inside the server folder, or `ValueError`."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"not a path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or _DRIVE.match(value):
        raise ValueError(f"not a path inside the server folder: {value!r}")
    return path.as_posix()


def _paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"not a list: {value!r}")
    return tuple(_inside(v) for v in value)


def _files(value: object) -> dict[str, str]:
    """`{path: sha256}`; a plain list (the first shape) reads as paths of unknown bytes."""
    if isinstance(value, list):
        return {path: "" for path in _paths(value)}
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ValueError(f"files is not a map of hashes: {value!r}")
    return {_inside(k): v for k, v in value.items()}


def _item(said: object) -> Deployed:
    if not isinstance(said, dict):
        raise ValueError(f"not an item: {said!r}")
    asides = said.get("asides", {})
    if not isinstance(asides, dict):
        raise ValueError(f"asides is not a map: {asides!r}")
    return Deployed(
        files=_files(said.get("files")),
        roots=_paths(said.get("roots", [])),
        asides={_inside(k): _inside(v) for k, v in asides.items()},
    )


def _parse(raw: object) -> dict[str, Deployed]:
    """The record, or `ValueError` for anything this build cannot use whole."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    if raw.get("version") != VERSION:
        raise ValueError(f"version {raw.get('version')!r}, this build reads {VERSION}")
    items = raw.get("items", {})
    if not isinstance(items, dict):
        raise ValueError("items must be a map")
    return {str(k): _item(v) for k, v in items.items()}


def _load(path: Path) -> tuple[dict[str, Deployed] | None, str]:
    """The record and "" when usable; `({}, "")` when absent; `(None, why)` otherwise."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return None, f"{path} could not be read ({exc})"
    try:
        return _parse(json.loads(text)), ""
    except ValueError as exc:
        return None, f"{path} is not a record this Yu'lon can read ({exc}), so it was left as it is"


def folds_case(server_dir: Path) -> bool:
    """Does the filesystem under `server_dir` treat `a` and `A` as one name (Windows, macOS)?

    `os.path.normcase` says so on Windows; elsewhere the server folder itself is asked:
    its name with the case swapped names the same folder, or it does not.
    """
    if os.path.normcase("A") == "a" or sys.platform == "darwin":
        return True
    name = server_dir.name
    if name.swapcase() == name:
        return False
    try:
        return os.path.samefile(server_dir, server_dir.with_name(name.swapcase()))
    except OSError:
        return False


def read(server_dir: Path) -> dict[str, Deployed] | None:
    """Every item's record at `server_dir`: `{}` when there is none, `None` when unusable."""
    items, problem = _load(server_dir / DEPLOYS_FILE)
    if items is None:
        logger.warning(f"no usable deploy record: {problem}")
    return items


def write(server_dir: Path, item: str, deployed: Deployed | None) -> str:
    """Set (or, with None, drop) `item`'s entry in one rename. "" or why not; never raises."""
    with _WRITING:
        path = server_dir / DEPLOYS_FILE
        items, problem = _load(path)
        if items is None:
            logger.warning(f"did not record {item}'s deployed files: {problem}")
            return problem
        if deployed is None:
            if item not in items:
                return ""
            items.pop(item)
        else:
            items[item] = deployed
        payload = {
            "version": VERSION,
            "items": {k: items[k].as_json() for k in sorted(items)},
        }
        tmp: Path | None = None
        try:
            fd, name = tempfile.mkstemp(dir=server_dir, prefix=DEPLOYS_FILE + ".", suffix=".tmp")
            os.close(fd)
            tmp = Path(name)
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(payload, indent=2) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            logger.warning(f"could not record {item}'s deployed files in {path}: {exc}")
            return f"{path} could not be written ({exc})"
        return ""
