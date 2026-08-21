"""Tests for `yulon.resources`: source-checkout and frozen (PyInstaller) layouts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import installer
from yulon.controller_wow_wotlk import modules


def test_source_layout_points_at_pylauncher_and_repo_root() -> None:
    assert resources.frozen() is False
    assert resources.bundle_root() == Path(__file__).resolve().parents[1]
    assert (resources.manifests_dir() / "wow-wotlk" / "modules.json").is_file()
    assert (resources.repo_root() / "archive" / "guides").is_dir()
    assert modules.BUNDLED_MANIFESTS_DIR == resources.manifests_dir()
    assert installer.DEFAULT_REPO_ROOT == resources.repo_root()


def test_frozen_layout_uses_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.frozen() is True
    assert resources.bundle_root() == tmp_path
    assert resources.manifests_dir() == tmp_path / "manifests"
    assert resources.repo_root() == tmp_path  # the spec copies archive/guides/ into the bundle
