"""Pins on the two pyplan pages 7.2 rewrites, so they cannot drift back to the bash path."""

from __future__ import annotations

from pathlib import Path

PYPLAN = Path(__file__).resolve().parents[2] / "pyplan"


def test_the_contribution_harness_is_the_engine_not_the_scripts() -> None:
    text = (PYPLAN / "contribution.md").read_text(encoding="utf-8")
    assert "python -m yulon.install_wiring wow-wotlk" in text
    assert "python -m yulon.catalog.installer" not in text
    assert "sudo -v" not in text
    assert "bash-script path" not in text
    assert "dml-start.sh" not in text


def test_the_style_guide_rows_describe_the_post_7_2_modules() -> None:
    text = (PYPLAN / "style-guide.md").read_text(encoding="utf-8")
    rows = {
        ln.split("|")[1].strip(): ln for ln in text.splitlines() if ln.startswith("| `catalog/")
    }
    installer = rows["`catalog/installer.py`"]
    assert "installer_for()" in installer and "never runs a subprocess" in installer
    assert "deps → clone → build → config" not in installer
    catalog = rows["`catalog/catalog.py`"]
    assert "install script" not in catalog and "family" in catalog
