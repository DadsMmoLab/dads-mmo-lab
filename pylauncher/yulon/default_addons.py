"""A server's default client add-ons: put in by themselves, never again once removed (T612).

Tortoise's two Sagiroth add-ons (TortoiseBots Manager, Tortoise GM Manager) are Modules-tab
items whose whole content is one `client` step. This module is the part that runs them without
a press: a new install and every Play ask `put_in()`, which installs an add-on that is not
there, updates one a count says is behind, and leaves alone any the player removed.

It reuses the Modules tab's own `Applier` for all of it, so what is installed is exactly what
the tab shows as installed and what its Remove takes back. Nothing here writes to the server
or restarts it: the manifests carry no server step (T30), and the report says so.

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
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from yulon.apply import Applier, ApplyReport, ModuleUpdate, installed_clones
from yulon.git import is_behind
from yulon.log import get_logger
from yulon.manifest import Manifest

logger = get_logger(__name__)

DECLINED_FILE = ".yulon-declined-addons.json"
"""The server folder's note of the default add-ons its player removed."""


def declined(server_dir: Path) -> frozenset[str] | None:
    """The add-ons this server's player removed; empty for no note; None for one nobody can read."""
    try:
        raw = json.loads((server_dir / DECLINED_FILE).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    names = raw.get("declined") if isinstance(raw, dict) else None
    if not isinstance(names, list) or not all(
        isinstance(n, str) and n and "/" not in n and "\\" not in n for n in names
    ):
        return None
    return frozenset(names)


def _write(server_dir: Path, names: Collection[str]) -> None:
    path = server_dir / DECLINED_FILE
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({"declined": sorted(names)}, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def decline(server_dir: Path, item_id: str) -> None:
    """Note that this server's player removed `item_id`. Unreadable notes are replaced."""
    _write(server_dir, (declined(server_dir) or frozenset()) | {item_id})


def undecline(server_dir: Path, item_id: str) -> None:
    """Lift the note for `item_id`: the player installed it again. No note, nothing written."""
    now = declined(server_dir)
    if now is None or item_id in now:
        _write(server_dir, (now or frozenset()) - {item_id})


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
    updated: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)
    restart_recommended: bool = False
    rebuild_required: bool = False

    def notes(self) -> tuple[str, ...]:
        """Plain sentences for the Play log; empty when nothing happened."""
        out = [f"Put {_NAMES.get(item, item)} into your game client." for item in self.installed]
        out += [f"Updated {_NAMES.get(item, item)} in your game client." for item in self.updated]
        out += [
            f"Could not set up {_NAMES.get(item, item)} in your game client ({why}). "
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
    behind: Collection[str] = (),
    say: Callable[[str], None] = lambda _line: None,
) -> Outcome:
    """Install each of `ids` that is not there, update each in `behind`, skip each removed.

    Never raises for one add-on: a failure is in `Outcome.failed` and the next add-on goes on,
    because a game that cannot reach GitHub must still start. Nothing is cloned without a
    client folder to put the files in, or the clone would read as installed with no files.
    """
    out = Outcome()
    if applier.client_dir is None:
        return out
    removed = declined(server_dir)
    if removed is None:
        logger.warning(f"{server_dir / DECLINED_FILE} cannot be read; no default add-on is put in")
        return out
    by_id = {m.id: m for m in manifests if m.type == "mod"}
    present = installed_clones(server_dir).get("mod", frozenset())
    installed: list[str] = []
    updated: list[str] = []
    for item in ids:
        manifest = by_id.get(item)
        if manifest is None or item in removed:
            continue
        try:
            if item not in present:
                say(f"Putting {manifest.name} into your game client…")
                report = applier.install(manifest)
                installed.append(item)
            elif item in behind:
                say(f"Updating {manifest.name} in your game client…")
                report = applier.update(manifest)
                updated.append(item)
            else:
                continue
            out.restart_recommended |= report.restart_recommended
            out.rebuild_required |= report.rebuild_required
        except Exception as exc:  # boundary: one add-on's failure must not stop the others or Play
            logger.warning(f"default add-on {item}: {exc}")
            out.failed[item] = str(exc)
            for kept in (installed, updated):
                if item in kept:
                    kept.remove(item)
    out.installed, out.updated = tuple(installed), tuple(updated)
    return out


def keep_in_step(
    server_dir: Path,
    applier: Applier,
    manifests: Iterable[Manifest],
    ids: Sequence[str],
    *,
    updates: Callable[[], Iterable[ModuleUpdate]] | None = None,
    say: Callable[[str], None] = lambda _line: None,
) -> Outcome:
    """`put_in()` with `behind` read from the Modules tab's own cached count (`updates`).

    The count is kept a day per clone (`apply.cached_module_updates`), so Play costs GitHub
    nothing while an add-on has not moved. A count that cannot be asked leaves nothing behind:
    the add-on is put in if it is missing and otherwise left as it is.
    """
    behind: set[str] = set()
    if updates is not None:
        try:
            behind = {
                row.key
                for row in updates()
                if row.family == "mod" and row.key in ids and is_behind(row.behind)
            }
        except Exception as exc:  # boundary: no network must not stop Play
            logger.info(f"default add-ons: could not count what is behind ({exc})")
    return put_in(server_dir, applier, manifests, ids, behind=behind, say=say)
