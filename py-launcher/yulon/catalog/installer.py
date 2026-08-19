"""Install orchestrator: dependencies -> clone -> build -> configure.

Phase 3 (see pyplan/README.md). Phase 3a shells out to the existing
install-*.sh scripts; Phase 3b reimplements them natively in Python.
"""

from __future__ import annotations


class Installer:
    """Coordinate a full server install for a single catalog game."""

    def __init__(self, entry: dict) -> None:
        self.entry = entry

    def run(self) -> None:
        """Begin the install. Placeholder — see Phase 3 in pyplan/README.md."""
        raise NotImplementedError