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
    # Added 2026-09-02: this pin read the two rows 7.2's plan named and stopped
    # there, so the `catalog/native.py` row went on describing "the same `run()`
    # contract as `Installer`" for as long as F.3 had deleted that class. The row
    # now names the two symbols that survive, and both resolve in the code.
    native = rows["`catalog/native.py`"]
    assert "StagedInstaller" in native and "InstallEngine" in native
    assert "contract as `Installer`" not in native


def _plans_whose_phase_the_checklist_ticks(pyplan: Path = PYPLAN) -> list[Path]:
    """The `phase7-plans/` pages whose phase line in `checklist.md` is `- [x]`.

    The phase number is the plan filename's own prefix (`7.2-retire-bash.md` ->
    `7.2`), matched against the ticked top-level lines of the checklist. Reading
    the checklist rather than keeping a list here is deliberate: a list would be
    a second place to remember, and this guard exists because a second place to
    remember is what let the dead citations accumulate.

    `pyplan` is a parameter so the rule itself can be driven against a fixture
    that ticks a box — see the test below. Without that, "the guard widens when a
    phase closes" is a sentence in a docstring and nothing more, because on this
    branch 7.1, 7.2 and 7.3 are all unticked and the widened set is empty.
    """
    checklist = (pyplan / "checklist.md").read_text(encoding="utf-8")
    ticked = set(re.findall(r"^- \[x\] (\d+\.\d+[a-z]?) ", checklist, re.M))
    plans = sorted((pyplan / "phase7-plans").glob("*.md"))
    return [p for p in plans if p.name.split("-")[0] in ticked]


def test_the_citation_guard_widens_to_a_plan_the_moment_its_phase_is_ticked(tmp_path: Path) -> None:
    """The scoping RULE, driven, because against the real tree it selected nothing.

    Measured 2026-09-02 at `f6ed1b9a`: every phase-7 box was `- [ ]`, so
    `_plans_whose_phase_the_checklist_ticks()` answered `[]` and the guard above
    was scoped exactly as it had been. That is correct and it is also
    unobservable — a rule that answers empty says nothing about what it would
    answer otherwise, which is the standing "assert the value ARRIVES" rule. So
    the same function is run here over a fixture with one box ticked and one not.
    """
    (tmp_path / "phase7-plans").mkdir()
    for name in ("7.2-retire-bash.md", "7.3-cmangos-family.md"):
        (tmp_path / "phase7-plans" / name).write_text("x\n", encoding="utf-8")
    (tmp_path / "checklist.md").write_text(
        "- [x] 7.2 Delete the bash lineage — done\n- [ ] 7.3 CMaNGOS data model — open\n",
        encoding="utf-8",
    )
    assert [p.name for p in _plans_whose_phase_the_checklist_ticks(tmp_path)] == [
        "7.2-retire-bash.md"
    ]


GONE = re.compile(r"\bdeletes?\b|\bdeleted\b|never written", re.I)
"""How a page says that a name it spells is not supposed to resolve.

Deliberately literal and deliberately narrow; see `_cited_as_live()`.
"""


def _cited_as_live(text: str) -> set[str]:
    """Every test name a page presents as EXISTING — which is not every name it spells.

    The rule this guard enforces is "a reader who follows this citation finds the
    test". A page that says a test was deleted is not making that promise: it is
    recording history, and the reader who follows it finds exactly what the page
    told them to expect.

    Holding those to the same rule turns this guard into the opposite of what it
    is for. 7.3's plan instructs a task to DELETE two named tests, records a
    deleted module, and carries one code block for a test that was specified and
    never written. Under a blind scan the only way to make a ticked phase pass is
    to repoint those names at live tests — which would make the plan say that
    F.4 deleted a test that is alive today. A guard whose remedy is to falsify
    the record is a guard that gets deleted.

    So a name is exempt when the LINE THAT SPELLS IT also says it is gone. Line
    scope and not paragraph, because a paragraph that mentions a deletion
    anywhere would exempt every name in it.

    WHAT THIS GIVES UP, said here rather than discovered later: a page can now
    write "F.4 deletes `test_x`" about a test that is alive, and this guard will
    not catch it. That hole is real, and it is narrow — the claim has to sit on
    the same line as the name and has to be a claim of removal. The alternative
    was a guard that a phase tick converts into an instruction to rewrite
    history, which is the worse failure.
    """
    live: set[str] = set()
    for line in text.splitlines():
        names = set(re.findall(r"\btest_[a-z0-9_]+\b", line))
        if names and not GONE.search(line):
            live |= names
    return live


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

    Scoped to the pages 7.2 owns, PLUS every phase plan whose checklist box is
    ticked — read off `checklist.md` here rather than listed, so nobody has to
    remember to add one. A plan cites tests it intends a future task to WRITE, so
    a dead citation there is a forward reference while the phase is open and a
    claim about the tree once it closes. The tick is what flips it.

    Measured 2026-09-02 at `f6ed1b9a`, when 7.1, 7.2 and 7.3 were all still
    unticked and so all still out of scope: `7.1-spine-azerothcore-linux.md` had
    13 of 141 cited names unresolved, `7.2-retire-bash.md` 27 of 70, and
    `7.3-cmangos-family.md` 41 of 224. Most are in-flight renames rather than
    aspirations, so ticking a box will cost a cleanup pass — which is the point.

    Widening to all of `pyplan/` instead does NOT reduce to "the plans are the
    problem". On the same measurement, `pyplan/` with the plans excluded still
    had unresolved names: `checklist.md` 5, `bug-checklist.md` 3 and
    `phase7-decisions.md` 2. Those three are dated records of what a run said on
    a day, and a test deleted afterwards is a different failure from a citation
    that never resolved; they are left out until someone decides which rule they
    are under, not because they are clean.
    """
    pages = [PYPLAN / "contribution.md", PYPLAN / "style-guide.md"]
    pages += _plans_whose_phase_the_checklist_ticks()
    named: dict[str, set[str]] = {}
    for path in pages:
        found = _cited_as_live(path.read_text(encoding="utf-8"))
        if found:
            named[path.name] = found
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
