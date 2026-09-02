"""Pins on the pyplan pages 7.2 rewrites, so they cannot drift back to the bash path."""

from __future__ import annotations

import re
from pathlib import Path

PYPLAN = Path(__file__).resolve().parents[2] / "pyplan"
TESTS = Path(__file__).resolve().parent


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


def test_every_test_these_pages_name_by_hand_actually_exists() -> None:
    """A document that cites a test by name acquires a dependency nothing enforced.

    Written 2026-09-02 after the third instance in one night. A doc on this branch
    cited `test_every_shipped_entry_is_installable_somewhere_and_names_its_family`
    an hour before a review renamed it; K.7 cited
    `test_every_stage_before_the_build_that_writes_into_the_build_context`, which
    has never existed; and a plan instructed an implementer not to touch
    style-guide rows that do not exist either.

    The two pins above check that the CLAIMS match the code. They say nothing
    about whether the citations resolve, which is a different failure and the more
    embarrassing one: a reader who follows a dead citation concludes the property
    is untested, and a reader who follows a renamed one lands somewhere else
    entirely.

    Scoped to the pages 7.2 owns. Widening it to all of `pyplan/` would fail on
    the plan documents, which cite tests they intend a future task to WRITE — a
    forward reference is legitimate there and a defect here.
    """
    named: dict[str, set[str]] = {}
    for page in ("contribution.md", "style-guide.md"):
        text = (PYPLAN / page).read_text(encoding="utf-8")
        found = set(re.findall(r"\btest_[a-z0-9_]+\b", text))
        if found:
            named[page] = found
    assert named, "no page cites a test by name; this guard has gone vacuous"

    # Both kinds of citation resolve: a test FUNCTION and a test MODULE. The first
    # version of this guard collected functions only and reported `test_catalog`,
    # `test_docker_live` and two others as missing -- they are files. A guard that
    # cries wolf on a legitimate citation gets deleted, which is worse than not
    # having one.
    defined: set[str] = set()
    for path in TESTS.rglob("test_*.py"):
        defined.add(path.stem)
        defined |= set(
            re.findall(r"^def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.M)
        )
    assert len(defined) > 500, f"only {len(defined)} test names found; the scan is broken"

    missing = {page: sorted(names - defined) for page, names in named.items()}
    assert not any(missing.values()), "these pages name tests that do not exist: " + "; ".join(
        f"{page}: {names}" for page, names in missing.items() if names
    )
