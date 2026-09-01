"""Tests for `yulon.resources`: source-checkout and frozen (PyInstaller) layouts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import installer
from yulon.controller_wow_wotlk import modules


def test_source_layout_points_at_pylauncher() -> None:
    assert resources.frozen() is False
    assert resources.bundle_root() == Path(__file__).resolve().parents[1]
    assert (resources.manifests_dir() / "wow-wotlk" / "modules.json").is_file()
    # Roadmap 6.0 put the app's executable data under pylauncher/; 7.2 left
    # only the compose templates there (F.5 adds the one surviving script).
    assert (resources.installers_dir() / "wow-wotlk" / "native" / "base.yml.tmpl").is_file()
    assert modules.BUNDLED_MANIFESTS_DIR == resources.manifests_dir()
    assert installer.DEFAULT_INSTALLERS_ROOT == resources.installers_dir()


def test_no_bash_installer_ships() -> None:
    """Phase 7 exit criterion: no `install-*.sh` remains, nor the two helpers it shipped.

    The three names are the shapes 7.2 deleted: the six `install-*.sh`, plus
    `dml-start.sh` and `wow-manage.sh`, which were helpers those installers
    wrote into the user's server folder rather than installers themselves.
    """
    root = resources.installers_dir()
    leftovers = sorted(
        p.relative_to(root).as_posix()
        for pattern in ("install-*.sh", "dml-start.sh", "wow-manage.sh")
        for p in root.rglob(pattern)
    )
    assert leftovers == [], leftovers


def test_frozen_layout_uses_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.frozen() is True
    assert resources.bundle_root() == tmp_path
    assert resources.manifests_dir() == tmp_path / "manifests"
    # The spec copies catalog/installers/ into the bundle under the same name.
    assert resources.installers_dir() == tmp_path / "catalog" / "installers"
