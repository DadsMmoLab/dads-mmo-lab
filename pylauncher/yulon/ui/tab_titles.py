"""What the controller tabs are called, when more than one of them exists.

The whole title used to be the game plus `server_dir.name`, and the leaf folder
is the one part of the path that repeats: the installer suggests the same name
to everybody, so a second server put on another disk gave the tab strip two
tabs reading "WoW WotLK — DadsMmoLab". The full path is not the answer either -
a tab is a few centimetres wide, and a path dump in it is unreadable at exactly
the moment it matters.

So the title carries the SHORTEST tail of the folder chain that no other open
install shares: one folder while nothing collides, two the moment something
does, and the whole path only if two installs somehow agree the whole way up.
That is a fact about the SET of open tabs, not about one path, which is why the
whole strip is re-titled whenever a tab is added - opening the second install
is what makes the first one's title wrong.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QTabWidget

    from yulon.ui.controller_view import ControllerView


def _tail(path: Path, depth: int) -> str:
    """The last `depth` folder names of `path`; the whole path once they run out."""
    parts = path.parts
    if depth >= len(parts):
        # Past the anchor there is nothing left to add, and str() spells the
        # drive or UNC share the way the user typed it.
        return str(path)
    return os.sep.join(parts[-depth:])


def folder_labels(dirs: Sequence[Path]) -> list[str]:
    """For each path, the shortest trailing run of folder names the others do not share.

    Two labels can never collide: paths that agree at some depth all go one
    deeper together, and a label carries its own depth in its separator count,
    so a shallow label cannot equal a deep one.

    A REPEAT of the same path is not a collision and is skipped. Tabs are keyed
    by (game, dir), so one folder can legitimately hold two games; widening
    against a path identical to this one can never separate them, and would
    only spell out the whole path for a pair the game names already tell apart.
    """
    labels: list[str] = []
    for index, path in enumerate(dirs):
        others = [
            other for position, other in enumerate(dirs) if position != index and other != path
        ]
        depth = 1
        while depth <= len(path.parts) and any(
            _tail(other, depth) == _tail(path, depth) for other in others
        ):
            depth += 1
        labels.append(_tail(path, depth))
    return labels


def controller_tab_titles(installs: Sequence[tuple[str, Path]]) -> list[str]:
    """One title per (game name, server dir), in the order given."""
    labels = folder_labels([server_dir for _, server_dir in installs])
    return [f"{name} — {label}" for (name, _), label in zip(installs, labels, strict=True)]


def retitle_controller_tabs(tabs: QTabWidget, views: Iterable[ControllerView]) -> None:
    """Re-title every controller tab from the set of them, leaving other tabs alone."""
    open_views = list(views)
    titles = controller_tab_titles(
        [(view.entry.name, view.services.controller.server_dir) for view in open_views]
    )
    for view, title in zip(open_views, titles, strict=True):
        index = tabs.indexOf(view)
        if index != -1:
            tabs.setTabText(index, title)
