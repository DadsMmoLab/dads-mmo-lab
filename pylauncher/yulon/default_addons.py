"""A server's default client add-ons: put in by themselves, never again once removed (T612).

Tortoise's two Sagiroth add-ons (TortoiseBots Manager, Tortoise GM Manager) are Modules-tab
items whose whole content is one `client` step. This module is the part that runs them without
a press, in two modes of `put_in()`:

* **At Play** (`clone=False`): no git and no network. An add-on whose clone is on disk but whose
  files are missing from the client Play uses (a ready-to-play client made again, an add-on
  folder deleted by hand) is copied back from that clone. Nothing is cloned, fetched or updated:
  a stalled network must not hold Play, and the Applier's update always fetches. Updates stay
  the Modules tab's own Check for updates and Update.
* **At a fresh install, and once when a tab opens** (`clone=True`): an add-on with no clone is
  installed (cloned) through the Modules tab's own Install, the same press a player makes. A
  failure is remembered for a day (`FAILED_BACKOFF_SECONDS`) so it is not retried at every
  start.

Either way a removed add-on is left alone, and what is installed is exactly what the tab shows
as installed and what its Remove takes back. Nothing here writes to the server or restarts it:
the manifests carry no server step (T30), and the report says so.

**The memory.** A Remove deletes the add-on's clone, which is what the tab (and `put_in()`)
read as "not installed", so without a note the next Play would put it back. `remember()` keeps
that note, per server, in one small file in the server folder (`DECLINED_FILE`), written when a
Remove report comes back and lifted when the player installs the add-on again by hand. A file
nobody can read is "do not know", and `put_in()` then installs nothing: guessing would put back
what a player removed.

No Qt here.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from yulon.apply import Applier, ApplyReport, installed_clones
from yulon.log import get_logger
from yulon.manifest import Manifest

logger = get_logger(__name__)

DECLINED_FILE = ".yulon-declined-addons.json"
"""The server folder's note of the default add-ons its player removed (and failed installs)."""

FAILED_BACKOFF_SECONDS = 24 * 60 * 60
"""How long a failed install is left alone before a start tries it again."""


def _read(server_dir: Path) -> tuple[frozenset[str], dict[str, float]] | None:
    """The note: removed ids and failure times; empty for no note; None for one nobody can read."""
    try:
        raw = json.loads((server_dir / DECLINED_FILE).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return frozenset(), {}
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    names = raw.get("declined")
    if not isinstance(names, list) or not all(
        isinstance(n, str) and n and "/" not in n and "\\" not in n for n in names
    ):
        return None
    failed = raw.get("failed", {})
    if not isinstance(failed, dict) or not all(
        isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
        for k, v in failed.items()
    ):
        return None
    return frozenset(names), {k: float(v) for k, v in failed.items()}


def declined(server_dir: Path) -> frozenset[str] | None:
    """The add-ons this server's player removed; empty for no note; None for one nobody can read."""
    read = _read(server_dir)
    return None if read is None else read[0]


def _write(server_dir: Path, names: Collection[str], failed: dict[str, float]) -> None:
    path = server_dir / DECLINED_FILE
    temp = path.with_name(path.name + ".tmp")
    note: dict[str, object] = {"declined": sorted(names)}
    if failed:
        note["failed"] = dict(sorted(failed.items()))
    temp.write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _change(
    server_dir: Path,
    *,
    declined_add: str | None = None,
    declined_drop: str | None = None,
    failed_set: tuple[str, float] | None = None,
    failed_drop: str | None = None,
) -> None:
    read = _read(server_dir)
    names, failed = (frozenset(), {}) if read is None else (read[0], dict(read[1]))
    new_names = (names | {declined_add} if declined_add else names) - {declined_drop}
    if failed_set is not None:
        failed[failed_set[0]] = failed_set[1]
    failed.pop(failed_drop or "", None)
    if read is None or new_names != names or failed != read[1]:
        _write(server_dir, new_names, failed)


def decline(server_dir: Path, item_id: str) -> None:
    """Note that this server's player removed `item_id`. An unreadable note is replaced."""
    _change(server_dir, declined_add=item_id, failed_drop=item_id)


def undecline(server_dir: Path, item_id: str) -> None:
    """Lift the note for `item_id`: the player installed it again. No note, nothing written."""
    _change(server_dir, declined_drop=item_id, failed_drop=item_id)


def remember(server_dir: Path, report: ApplyReport, ids: Collection[str]) -> None:
    """Keep the Modules tab's own Remove and Install of a default add-on in the memory."""
    if report.item_id not in ids or report.family != "mod":
        return
    try:
        if report.action == "remove":
            decline(server_dir, report.item_id)
        elif report.action == "install":
            undecline(server_dir, report.item_id)
    except OSError as exc:
        logger.warning(f"could not note {report.item_id}'s {report.action} for Play: {exc}")


_NAMES = {
    "tortoise-bots-manager": "TortoiseBots Manager",
    "tortoise-gm-manager": "Tortoise GM Manager",
}


@dataclass
class Outcome:
    """What one `put_in()` did."""

    installed: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)
    restart_recommended: bool = False
    rebuild_required: bool = False
    names: dict[str, str] = field(default_factory=dict)
    """Display names, for an add-on the player brought (the two defaults have `_NAMES`)."""

    def _name(self, item: str) -> str:
        return _NAMES.get(item, self.names.get(item, item))

    def notes(self) -> tuple[str, ...]:
        """Plain sentences for the Play log; empty when nothing happened."""
        name = self._name
        out = [f"Put {name(item)} into your game client." for item in self.installed]
        out += [
            f"Put the missing files of {name(item)} back into your game client."
            for item in self.restored
        ]
        out += [
            f"Could not set up {name(item)} in your game client ({why}). "
            "Play goes on without it."
            for item, why in self.failed.items()
        ]
        return tuple(out)


def put_in(
    server_dir: Path,
    applier: Applier,
    manifests: Iterable[Manifest],
    ids: Sequence[str],
    *,
    clone: bool,
    say: Callable[[str], None] = lambda _line: None,
    now: Callable[[], float] = time.time,
) -> Outcome:
    """Put each of `ids` that is not removed into the client; see the module docstring.

    `clone=False` is Play: files only, from a clone already on disk, no git. `clone=True`
    also installs an add-on that has no clone, unless it failed within the last
    `FAILED_BACKOFF_SECONDS`.

    Never raises for one add-on: a failure is in `Outcome.failed` and the next add-on goes on,
    because a game that cannot reach GitHub must still start. Nothing is done without a client
    folder to put the files in, or a clone would read as installed with no files.
    """
    manifests = list(manifests)
    out = Outcome(names={m.id: m.name for m in manifests})
    if applier.client_dir is None:
        return out
    read = _read(server_dir)
    if read is None:
        logger.warning(f"{server_dir / DECLINED_FILE} cannot be read; no default add-on is put in")
        return out
    removed, failed_at = read
    by_id = {m.id: m for m in manifests if m.type == "mod"}
    present = installed_clones(server_dir).get("mod", frozenset())
    installed: list[str] = []
    restored: list[str] = []
    for item in ids:
        manifest = by_id.get(item)
        if manifest is None or item in removed:
            continue
        try:
            if item in present:
                if applier.client_files_missing(manifest):
                    say(f"Putting the missing files of {manifest.name} back…")
                    applier.put_back_client_files(manifest)
                    restored.append(item)
                continue
            if not clone or now() - failed_at.get(item, -FAILED_BACKOFF_SECONDS) < (
                FAILED_BACKOFF_SECONDS
            ):
                continue
            say(f"Putting {manifest.name} into your game client…")
            report = applier.install(manifest)
            installed.append(item)
            out.restart_recommended |= report.restart_recommended
            out.rebuild_required |= report.rebuild_required
            if item in failed_at:
                _change(server_dir, failed_drop=item)
        except Exception as exc:  # boundary: one add-on's failure must not stop the others or Play
            logger.warning(f"default add-on {item}: {exc}")
            out.failed[item] = str(exc)
            if item in installed:
                installed.remove(item)
            if not clone:
                continue
            try:
                _change(server_dir, failed_set=(item, now()))
            except OSError as note_exc:
                logger.warning(f"could not note that {item} failed: {note_exc}")
    out.installed, out.restored = tuple(installed), tuple(restored)
    return out
