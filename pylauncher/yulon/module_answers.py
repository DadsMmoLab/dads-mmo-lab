"""The answers a player gave to a module's questions, kept per install (T104).

Until T104 no answer was kept anywhere: `Applier._values()` filled every prompt
from the manifest's default, so the Update dialog pre-selected the DEFAULT and
not what the player had picked. The T100 cold review measured it on
Hearthstone Tweaks -- installed with 5 minutes, Update + OK, back to 30. The
owner's decision of 2026-09-24 was "ask all, remember answers".

**Where it lives, and why there.** `<server_dir>/.yulon-module-answers.json`,
beside `.yulon-install.json` and `.yulon-network.json`, for the reasons
`networking.INTENT_FILE` gives for the second of those: a person's choice about
ONE install belongs with that install, and it is not a run's progress, so it
must not live inside the file the install engine rewrites whole after every
stage. It is not a field of `state.json` either: `AppState` is
`extra="forbid"`, so an older build reading a newer `state.json` would call it
corrupt and move it aside, forgetting every install it knew.

**Keyed `<type>/<id>`**, because a module id is only unique within its family
(`controller_view.selected_manifest()` looks manifests up by both for that
reason).

Reading is deliberately forgiving and writing deliberately careful. Any shape
this build cannot use reads as "nothing saved" -- the caller then offers the
manifest's defaults, which is exactly what every install made before T104 gets.
A write is atomic (a unique temp file + `os.replace`), so a half-written record is
never read as a record, and a file that is there but unusable is never overwritten.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yulon.log import get_logger
from yulon.manifest import Manifest

logger = get_logger(__name__)

ANSWERS_FILE = ".yulon-module-answers.json"
"""The record's name in the server folder. One of `catalog.native.OUR_OWN_FILES`."""

SCHEMA_VERSION = 1


def _key(manifest: Manifest) -> str:
    return f"{manifest.type}/{manifest.id}"


def _read_all(server_dir: Path) -> dict[str, dict[str, str]]:
    """Every module's saved answers, or `{}` for any file this build cannot use."""
    path = server_dir / ANSWERS_FILE
    try:
        with path.open(encoding="utf-8-sig") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"no usable module answers in {server_dir}: {exc}")
        return {}
    modules = parsed.get("modules") if isinstance(parsed, dict) else None
    if not isinstance(modules, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, answers in modules.items():
        if isinstance(key, str) and isinstance(answers, dict):
            out[key] = {
                k: v for k, v in answers.items() if isinstance(k, str) and isinstance(v, str)
            }
    return out


def read_answers(server_dir: Path, manifest: Manifest) -> dict[str, str]:
    """What was last answered for `manifest` on the install at `server_dir`, unchecked.

    Raw strings: whether each is still a usable answer to the manifest's
    question (a `choice` whose option was dropped, say) is
    `Applier.remembered_answers()`'s call, because it is `apply.check_answer()`'s.
    """
    return dict(_read_all(server_dir).get(_key(manifest), {}))


def _load_for_write(path: Path) -> tuple[dict[str, Any] | None, str]:
    """The whole record as it stands, to be written back with one entry changed.

    `({}, "")` for no file at all -- the first answer on this install. `(None,
    why)` for a file that is there and cannot be used: unreadable, not JSON, or
    a shape this build does not know. That file is LEFT ALONE rather than
    replaced: it may be a newer build's or a person's hand edit, and writing over
    it would lose every other module's answers to save one (fix wave, T104).
    """
    if not path.exists():
        return {}, ""
    try:
        with path.open(encoding="utf-8-sig") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"{path.name} is there but could not be read ({exc}); it was left as it is"
    if (
        not isinstance(parsed, dict)
        or not isinstance(parsed.get("modules", {}), dict)
        or not isinstance(parsed.get("applied", {}), dict)
        or not isinstance(parsed.get("pending", {}), dict)
    ):
        return None, f"{path.name} is not in a shape this version knows; it was left as it is"
    return parsed, ""


def record_answers(server_dir: Path, manifest: Manifest, answers: Mapping[str, str]) -> str:
    """Remember `answers` for `manifest` on this install; `""` if written, else why not.

    MERGED over what was saved for the module before, so an answer to a
    question this press did not ask (a configure-only one, say) is kept. Every
    other module's entry, every top-level field this build does not know, and a
    NEWER `schema_version` are written back as they were read.

    Through a uniquely named temp file in the same folder (`mkstemp`) and
    `os.replace`, as `module_source._write_atomically()` does: a rename across
    filesystems would be a copy, and a fixed temp name is one two writers could
    share. Two Yu'lon processes pressing Install on ONE server at once are out
    of scope -- there is no lock, and the later rename wins.

    It answers instead of raising, as `networking.record_network_intent()` does:
    the install it follows has already happened, and an unwritable folder means
    the next Update shows defaults -- worth a line in the report, not an
    exception over a successful install.
    """

    def change(everything: dict[str, Any]) -> None:
        modules = dict(everything.get("modules", {}))
        before = modules.get(_key(manifest))
        merged = dict(before) if isinstance(before, dict) else {}
        merged.update({str(k): str(v) for k, v in answers.items()})
        modules[_key(manifest)] = merged
        everything["modules"] = modules

    return _write_record(server_dir, change, f"the answers for {_key(manifest)}")


def read_applied(server_dir: Path, manifest: Manifest) -> dict[str, str] | None:
    """The values Yu'lon last put into the database for `manifest`, raw, or `None` (T115).

    Kept apart from the answers on purpose. The answers are what the player
    said last, and T104 keeps them after a Remove so a re-install is pre-filled;
    so they cannot also say whether a RELATIVE install (`HealthModifier*{hp}`)
    is in the database now. This entry can: an install of such a module writes
    it and its Remove deletes it.

    `None` for no entry, and also for no usable file. An entry that is there but
    is not an object of strings reads as `{}` -- present and unusable -- so the
    caller can tell "never applied" from "applied with values nobody can read".
    """
    path = server_dir / ANSWERS_FILE
    try:
        with path.open(encoding="utf-8-sig") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"no usable applied record in {server_dir}: {exc}")
        return None
    applied = parsed.get("applied") if isinstance(parsed, dict) else None
    if not isinstance(applied, dict) or _key(manifest) not in applied:
        return None
    entry = applied[_key(manifest)]
    if not isinstance(entry, dict):
        return {}
    return {k: v for k, v in entry.items() if isinstance(k, str) and isinstance(v, str)}


def record_applied(server_dir: Path, manifest: Manifest, values: Mapping[str, str] | None) -> str:
    """Record `values` as what the database now holds for `manifest`; `None` deletes it (T115).

    Called once the SQL has COMMITTED, so it also drops this module's `pending`
    mark (`record_pending()`). `""` if written (or if there was nothing to
    change), else why not. The same careful write as `record_answers()`: every
    other entry and field is kept.
    """
    if values is None and read_applied(server_dir, manifest) is None:
        if not is_pending(server_dir, manifest):
            return ""

    def change(everything: dict[str, Any]) -> None:
        applied = everything.get("applied", {})
        applied = dict(applied) if isinstance(applied, dict) else {}
        if values is None:
            applied.pop(_key(manifest), None)
        else:
            applied[_key(manifest)] = {str(k): str(v) for k, v in values.items()}
        everything["applied"] = applied
        _drop_pending(everything, manifest)

    return _write_record(server_dir, change, f"what was applied for {_key(manifest)}")


@dataclass(frozen=True)
class RecordedKeys:
    """The answers file's `applied` and `pending` keys (`<type>/<id>`), or why it cannot be read.

    THREE answers, not two (T121 fix wave, Codex high). No file is "nothing
    recorded". A file that is there and cannot be read -- not JSON, no
    permission, a shape this build does not know -- is `unreadable`, never
    "nothing recorded": read that way, every mob multiplier's row said Not
    installed and the rule that two of them cannot be installed together
    passed, because the one place that says one IS installed could not be read.
    """

    applied: frozenset[str] = frozenset()
    pending: frozenset[str] = frozenset()
    unreadable: str = ""
    """Why the file could not be read, or `""` for a usable file or no file at all."""


def recorded_keys(server_dir: Path) -> RecordedKeys:
    """Every `<type>/<id>` with an `applied` entry, every one with a `pending` mark (T121).

    One read of the file for the whole install, because the Modules tab asks it
    on every reload, on the GUI thread, beside the clone-folder listing. The
    four mob multipliers leave no folder, so this record is the only thing that
    can say one of them is in the database (`apply.installed_modules()`).

    An `applied` entry that is there but unusable still counts as applied: it
    says a Remove is owed, which is the reading the Remove dialog and T115's
    re-run refusal already give it.
    """
    path = server_dir / ANSWERS_FILE
    try:
        with path.open(encoding="utf-8-sig") as fh:
            parsed = json.load(fh)
    except FileNotFoundError:
        return RecordedKeys()
    except (OSError, ValueError) as exc:
        return RecordedKeys(unreadable=f"{type(exc).__name__}: {exc}")
    if not isinstance(parsed, dict):
        return RecordedKeys(unreadable="it is not in a shape this version knows")
    found: dict[str, frozenset[str]] = {}
    for field in ("applied", "pending"):
        entries = parsed.get(field, {})
        if not isinstance(entries, dict):
            return RecordedKeys(unreadable=f"its `{field}` is not in a shape this version knows")
        found[field] = frozenset(key for key in entries if isinstance(key, str))
    return RecordedKeys(applied=found["applied"], pending=found["pending"])


def forget_applied(server_dir: Path, manifest: Manifest) -> str:
    """Drop `manifest`'s `applied` entry and `pending` mark, running no SQL (T121 fix wave).

    The Modules tab's "Forget Yu'lon's record…", for a record that no longer
    describes the database: a restored `acore_world` backup, say. The only other
    way out was Remove, which divides whatever is there -- base values, on a
    restored database -- and so halves every creature. The saved answers stay.
    `""` if written (or nothing to drop), else why not.
    """
    key = _key(manifest)
    read = recorded_keys(server_dir)
    if not read.unreadable and key not in read.applied | read.pending:
        return ""

    def change(everything: dict[str, Any]) -> None:
        for field in ("applied", "pending"):
            entries = everything.get(field)
            if isinstance(entries, dict) and key in entries:
                entries = dict(entries)
                entries.pop(key)
                everything[field] = entries

    return _write_record(server_dir, change, f"the record of what was applied for {key}")


def forget_database_records(server_dir: Path) -> tuple[bool, str]:
    """Drop the whole `applied` and `pending` maps, for a database imported fresh (T121 fix wave).

    The file is one of `catalog.native.OUR_OWN_FILES`, so it survives into a new
    install in the same folder; its `applied` map then describes a database that
    no longer exists, and the tab read Baby Mobs as installed on stock
    creatures. An import onto an empty database is proof that nothing is
    applied. The saved answers are kept: they are only what to pre-fill.

    `(changed, "")`, or `(False, why)` for a file that cannot be written.
    """
    read = recorded_keys(server_dir)
    if not read.unreadable and not (read.applied or read.pending):
        return False, ""

    def change(everything: dict[str, Any]) -> None:
        everything.pop("applied", None)
        everything.pop("pending", None)

    problem = _write_record(server_dir, change, "what was applied to the old database")
    return (not problem), problem


def is_pending(server_dir: Path, manifest: Manifest) -> bool:
    """Whether a press marked `manifest`'s relative SQL as being sent and never finished (T115).

    A mark left behind means the app stopped while the statement was in flight:
    it may have committed or not, so the applied record cannot be trusted.
    `Applier.applied_record()` reads that as unusable -- a re-run is refused and
    Remove asks -- rather than as either answer. Unreadable file: `False`, as
    `read_applied()` answers `None` for it.
    """
    path = server_dir / ANSWERS_FILE
    try:
        with path.open(encoding="utf-8-sig") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError):
        return False
    pending = parsed.get("pending") if isinstance(parsed, dict) else None
    return isinstance(pending, dict) and _key(manifest) in pending


def record_pending(server_dir: Path, manifest: Manifest, values: Mapping[str, str] | None) -> str:
    """Mark `manifest`'s relative SQL as about to be sent, towards `values` (`None`: a Remove).

    Written immediately before the statement -- after the running-world guards
    and the database start, which can wait up to 180 s (cold review, T115 fix
    wave) -- and replaced by `record_applied()` once it has committed. The
    applied record itself is not touched here. A failure PROVEN to be before
    the server (`apply.SqlNotSent`) needs only `clear_pending()`; any other
    failure leaves the mark, since the COMMIT may have landed. `""` if written,
    else why not; the caller refuses the press on that, having sent nothing.
    """

    def change(everything: dict[str, Any]) -> None:
        pending = everything.get("pending", {})
        pending = dict(pending) if isinstance(pending, dict) else {}
        target = None if values is None else {str(k): str(v) for k, v in values.items()}
        pending[_key(manifest)] = {"to": target}
        everything["pending"] = pending

    return _write_record(server_dir, change, f"the SQL about to run for {_key(manifest)}")


def clear_pending(server_dir: Path, manifest: Manifest) -> str:
    """Drop the `pending` mark when nothing reached the server (`apply.SqlNotSent`)."""
    if not is_pending(server_dir, manifest):
        return ""
    return _write_record(
        server_dir,
        lambda everything: _drop_pending(everything, manifest),
        f"the SQL mark for {_key(manifest)}",
    )


def _drop_pending(everything: dict[str, Any], manifest: Manifest) -> None:
    pending = everything.get("pending")
    if isinstance(pending, dict) and _key(manifest) in pending:
        pending = dict(pending)
        pending.pop(_key(manifest))
        everything["pending"] = pending


def _write_record(server_dir: Path, change: Callable[[dict[str, Any]], None], what: str) -> str:
    """Load the whole record, apply `change` to it, and write it back atomically.

    Through a uniquely named temp file in the same folder (`mkstemp`) and
    `os.replace`, as `module_source._write_atomically()` does: a rename across
    filesystems would be a copy, and a fixed temp name is one two writers could
    share. A file that is there and cannot be used is left alone
    (`_load_for_write()`), and a NEWER `schema_version` is written back as read.
    """
    path = server_dir / ANSWERS_FILE
    everything, problem = _load_for_write(path)
    if everything is None:
        logger.warning(f"did not record {what}: {problem}")
        return problem
    payload = dict(everything)
    change(payload)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=server_dir, prefix=ANSWERS_FILE + ".", suffix=".tmp")
        os.close(fd)
        tmp = Path(name)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        logger.warning(f"could not record {what} in {path}: {exc}")
        return str(exc)
    return ""
