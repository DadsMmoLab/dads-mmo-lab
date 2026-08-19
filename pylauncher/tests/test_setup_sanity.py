"""Sanity check that the project scaffolding and tooling are wired up correctly.

Not a real feature test — Phase 1 (see pyplan/roadmap.md) adds the real suite for
runner.py / docker_ctl.py.
"""

from __future__ import annotations

import yulon


def test_package_imports() -> None:
    """The top-level `yulon` package should import and expose a version string."""
    assert isinstance(yulon.__version__, str)
    assert yulon.__version__
