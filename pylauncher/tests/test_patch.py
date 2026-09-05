"""`families/patch.py`: the tolerant unified-diff apply behind `patch-sources`.

The fixture under `tests/fixtures/cmangos-vmap-8ec338a1/` is the four files of
`contrib/vmap_extractor/vmapextract/` as they are in `cmangos/mangos-classic`
at `8ec338a1` — the commit `wow-vanilla` pins — copied byte for byte on
2026-09-05 from the checkout on `m910q`; `patched/` beside it holds the same
four files after `git apply` of the shipped patch on that box. So the strongest
assertion here is not "the fix is in the file" but "this module produced the
bytes git produced", and every other test is a way the apply must refuse or
tolerate.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog.families import patch

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cmangos-vmap-8ec338a1"
PATCHED = FIXTURE / "patched"
SHIPPED = (
    resources.installers_dir()
    / "shared"
    / "cmangos"
    / "patches"
    / "vmap-extractor-doodad-name-case.patch"
)
ISSUE_DOC = Path(__file__).resolve().parents[2] / "pyplan" / "upstream-cmangos-doodad-issue.md"
"""The upstream report the owner posts under his own name, with the patch inline."""
SHIPPED_REL = (
    "pylauncher/catalog/installers/shared/cmangos/patches/vmap-extractor-doodad-name-case.patch"
)
"""What the doc says it is quoting; the doc has to go on naming the file it quotes."""
SUBDIR = Path("contrib") / "vmap_extractor" / "vmapextract"
FILES = ("gameobject_extract.cpp", "model.cpp", "vmapexport.cpp", "vmapexport.h")
ANCHOR = "    fixedName = GetPlainName(origPath.c_str());\n"
"""The line the first hunk is anchored on — the one the defect is about."""


def checkout(tmp_path: Path, source: Path = FIXTURE) -> Path:
    """A fake checkout holding the four fixture files where the patch expects them."""
    root = tmp_path / "src"
    (root / SUBDIR).mkdir(parents=True)
    for name in FILES:
        shutil.copyfile(source / name, root / SUBDIR / name)
    return root


def text() -> str:
    return SHIPPED.read_text(encoding="utf-8")


def test_the_fixture_is_the_pre_image_and_patched_is_a_different_post_image() -> None:
    """The control: a pair of identical fixtures would make every test below vacuous."""
    for name in FILES:
        assert (FIXTURE / name).read_bytes() != (PATCHED / name).read_bytes(), name
    assert ANCHOR in (FIXTURE / "gameobject_extract.cpp").read_text(encoding="utf-8")


def test_the_shipped_patch_parses_into_one_hunk_per_edit_over_the_four_files() -> None:
    hunks = patch.parse(text())
    assert [h.path.rsplit("/", 1)[1] for h in hunks] == [
        "gameobject_extract.cpp",
        "model.cpp",
        "vmapexport.cpp",
        "vmapexport.cpp",
        "vmapexport.h",
    ]
    first = hunks[0]
    assert first.path == "contrib/vmap_extractor/vmapextract/gameobject_extract.cpp"
    assert first.start == 24
    assert ANCHOR.rstrip("\n") in first.before
    assert "    fixnamen(&fixedName[0], fixedName.length());" in first.after
    assert "    fixnamen(&fixedName[0], fixedName.length());" not in first.before


def test_applying_the_shipped_patch_produces_the_bytes_git_apply_produced(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    results = patch.apply(text(), root, name="the patch")
    assert {r.path.rsplit("/", 1)[1]: (r.applied, r.present) for r in results} == {
        "gameobject_extract.cpp": (1, 0),
        "model.cpp": (1, 0),
        "vmapexport.cpp": (2, 0),
        "vmapexport.h": (1, 0),
    }
    assert all(r.outcome is patch.Outcome.APPLIED for r in results)
    for name in FILES:
        assert (root / SUBDIR / name).read_bytes() == (PATCHED / name).read_bytes(), name


def test_applying_it_again_finds_every_hunk_present_and_writes_nothing(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    patch.apply(text(), root, name="the patch")
    before = {name: (root / SUBDIR / name).stat().st_mtime_ns for name in FILES}
    results = patch.apply(text(), root, name="the patch")
    assert all(r.outcome is patch.Outcome.PRESENT for r in results)
    assert sum(r.present for r in results) == 5 and sum(r.applied for r in results) == 0
    assert {name: (root / SUBDIR / name).stat().st_mtime_ns for name in FILES} == before
    for name in FILES:
        assert (root / SUBDIR / name).read_bytes() == (PATCHED / name).read_bytes(), name


def test_a_checkout_that_already_carries_the_fix_is_left_alone(tmp_path: Path) -> None:
    """The day upstream lands the fix, the stage reads it as done rather than doubling it."""
    root = checkout(tmp_path, source=PATCHED)
    results = patch.apply(text(), root, name="the patch")
    assert all(r.outcome is patch.Outcome.PRESENT for r in results)
    for name in FILES:
        assert (root / SUBDIR / name).read_bytes() == (PATCHED / name).read_bytes(), name


def test_a_moved_context_refuses_by_file_and_line_and_writes_no_file_at_all(
    tmp_path: Path,
) -> None:
    """Refuse loudly, and atomically: model.cpp would apply, and must not be touched."""
    root = checkout(tmp_path)
    target = root / SUBDIR / "gameobject_extract.cpp"
    body = target.read_text(encoding="utf-8")
    assert body.count(ANCHOR) == 1
    target.write_text(body.replace(ANCHOR, "    fixedName = SomethingElse(origPath);\n"))
    untouched = {name: (root / SUBDIR / name).read_bytes() for name in FILES}
    with pytest.raises(patch.PatchError) as caught:
        patch.apply(text(), root, name="the doodad patch")
    message = str(caught.value)
    assert "the doodad patch" in message
    assert "contrib/vmap_extractor/vmapextract/gameobject_extract.cpp" in message
    assert "line 24" in message
    assert "fixedName = GetPlainName(origPath.c_str());" in message
    assert "nothing was changed" in message
    assert {name: (root / SUBDIR / name).read_bytes() for name in FILES} == untouched


def test_context_that_moved_down_the_file_still_applies(tmp_path: Path) -> None:
    """An include added above the function moves the line, not the context."""
    root = checkout(tmp_path)
    target = root / SUBDIR / "gameobject_extract.cpp"
    target.write_text("// one\n// two\n// three\n" + target.read_text(encoding="utf-8"))
    results = patch.apply(text(), root, name="the patch")
    by_name = {r.path.rsplit("/", 1)[1]: r for r in results}
    assert by_name["gameobject_extract.cpp"].outcome is patch.Outcome.APPLIED
    assert (
        target.read_bytes()
        == b"// one\n// two\n// three\n" + (PATCHED / "gameobject_extract.cpp").read_bytes()
    )


def test_a_pre_image_that_occurs_twice_is_ambiguous_and_refuses(tmp_path: Path) -> None:
    """Off the hinted line (three lines added above) and present twice: which one is unknowable."""
    root = checkout(tmp_path)
    target = root / SUBDIR / "vmapexport.h"
    body = target.read_text(encoding="utf-8")
    hunk = next(h for h in patch.parse(text()) if h.path.endswith("vmapexport.h"))
    block = "\n".join(hunk.before) + "\n"
    assert body.count(block) == 1
    target.write_text("// a\n// b\n// c\n" + body + "\n" + block)
    with pytest.raises(patch.PatchError, match="more than once"):
        patch.apply(text(), root, name="the patch")


def test_the_hinted_line_wins_when_the_file_repeats_the_block_below_it(tmp_path: Path) -> None:
    """At the stated line the match is taken outright; the copy further down is not consulted."""
    root = checkout(tmp_path)
    target = root / SUBDIR / "vmapexport.h"
    body = target.read_text(encoding="utf-8")
    hunk = next(h for h in patch.parse(text()) if h.path.endswith("vmapexport.h"))
    block = "\n".join(hunk.before) + "\n"
    target.write_text(body + "\n" + block)
    results = patch.apply(text(), root, name="the patch")
    assert next(r for r in results if r.path.endswith("vmapexport.h")).applied == 1
    got = target.read_text(encoding="utf-8")
    assert got.startswith((PATCHED / "vmapexport.h").read_text(encoding="utf-8"))
    assert got.endswith("\n" + block), "the lower copy is untouched"


def test_a_crlf_checkout_is_patched_and_stays_crlf(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    for name in FILES:
        target = root / SUBDIR / name
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    results = patch.apply(text(), root, name="the patch")
    assert all(r.outcome is patch.Outcome.APPLIED for r in results)
    for name in FILES:
        got = (root / SUBDIR / name).read_bytes()
        assert b"\r\n" in got and b"\n" not in got.replace(b"\r\n", b"")
        assert got == (PATCHED / name).read_bytes().replace(b"\n", b"\r\n"), name


def test_a_missing_file_refuses_and_names_the_checkout(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    (root / SUBDIR / "model.cpp").unlink()
    with pytest.raises(patch.PatchError, match=r"no such file") as caught:
        patch.apply(text(), root, name="the patch")
    assert "contrib/vmap_extractor/vmapextract/model.cpp" in str(caught.value)
    assert str(root) in str(caught.value)
    # Atomic here too: gameobject_extract.cpp resolved before model.cpp was reached.
    assert (root / SUBDIR / "gameobject_extract.cpp").read_bytes() == (
        FIXTURE / "gameobject_extract.cpp"
    ).read_bytes()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("--- /dev/null\n+++ b/new.cpp\n@@ -0,0 +1 @@\n+int x;\n", "creates or deletes"),
        ("--- a/old.cpp\n+++ b/new.cpp\n@@ -1 +1 @@\n-int x;\n+int y;\n", "renames"),
        ("diff --git a/x b/x\nBinary files differ\n", "binary"),
        ("just prose\n", "not a line"),
        ("diff --git a/x b/x\nindex 1..2 100644\n", "no hunks"),
        ("--- a/x.cpp\n+++ b/x.cpp\n@@ -1,2 +1,2 @@\n-int x;\n+int y;\n", "carries"),
        ("--- a/x.cpp\n+++ b/x.cpp\n@@ -1 +1 @@\n-int x;\n+int y;\nstray\n", "not a line"),
        (
            "--- a/x.cpp\n+++ b/x.cpp\n@@ -1 +1 @@\n-int x;\n+int y;\nrename from y\n",
            "nothing else",
        ),
        ("--- a/../x.cpp\n+++ b/../x.cpp\n@@ -1 +1 @@\n-int x;\n+int y;\n", "inside the checkout"),
    ],
)
def test_patches_this_module_does_not_apply_are_refused_by_name(
    tmp_path: Path, body: str, reason: str
) -> None:
    with pytest.raises(patch.PatchError, match=reason):
        patch.apply(body, tmp_path, name="p")


def test_a_hunk_with_more_lines_than_its_header_says_is_refused_not_truncated(
    tmp_path: Path,
) -> None:
    """The header counts are the parser's only proof it read the hunk it was meant to."""
    (tmp_path / "x.cpp").write_text("int x;\nint z;\n")
    body = "--- a/x.cpp\n+++ b/x.cpp\n@@ -1 +1 @@\n-int x;\n+int y;\n-int z;\n"
    with pytest.raises(patch.PatchError):
        patch.apply(body, tmp_path, name="p")
    assert (tmp_path / "x.cpp").read_text() == "int x;\nint z;\n"


# -- the copy that leaves this repository ------------------------------------


def fenced_diff(doc: str) -> str:
    """The one ```diff block in the issue text; more than one and this test is wrong."""
    fences = re.findall(r"^```diff\n(.*?)^```", doc, re.S | re.M)
    assert len(fences) == 1, f"{len(fences)} diff fences in {ISSUE_DOC.name}"
    return fences[0]


def test_the_issue_docs_fenced_diff_is_the_shipped_patch_byte_for_byte() -> None:
    """The doc says "byte-for-byte", and until 2026-09-05 it was not, in three places.

    The fence carried the v1 patch: `vmapexport.cpp` anchored at
    `@@ -58,6 +58,7 @@ std::set<std::string> gameobjectFiles;` with
    `char szWorkDirWmo[1024];` for context, against a tree whose declaration
    block had moved. Measured on m910q 2026-09-05, in the two checkouts the
    catalog pins: `git apply --check` on the fenced version exits 1 on
    `mangos-classic` `8ec338a1` AND on `mangos-tbc` `f82e7d67` with
    `patch failed: contrib/vmap_extractor/vmapextract/vmapexport.cpp:58`,
    while the shipped file exits 0 on both. A maintainer's first act on an
    issue is to apply the patch, so a fence that cannot apply on the commit
    the issue names is the whole report wasted.

    Equality, not "applies too": the fence is what a stranger copies out, and
    two diffs that both apply can still differ in a comment or an index line.
    """
    doc = ISSUE_DOC.read_text(encoding="utf-8")
    assert SHIPPED_REL in doc, "the doc no longer names the file it claims to quote"
    assert fenced_diff(doc) == text()


def test_the_fenced_diff_parses_into_the_same_hunks_the_shipped_file_does() -> None:
    """Where the two versions differed: the file, the line, and the counts of every hunk."""
    fenced = patch.parse(fenced_diff(ISSUE_DOC.read_text(encoding="utf-8")))
    shipped = patch.parse(text())
    assert [(h.path, h.start, len(h.before), len(h.after)) for h in fenced] == [
        (h.path, h.start, len(h.before), len(h.after)) for h in shipped
    ]


# -- a hunk that inserts and removes nothing ---------------------------------


INSERT_AT_TAIL = "--- a/x.c\n+++ b/x.c\n@@ -1,3 +1,4 @@\n int a;\n int b;\n int c;\n+int d;\n"
INSERT_AT_HEAD = "--- a/x.c\n+++ b/x.c\n@@ -1,3 +1,4 @@\n+int d;\n int a;\n int b;\n int c;\n"


@pytest.mark.parametrize(
    ("body", "once"),
    [
        (INSERT_AT_TAIL, "int a;\nint b;\nint c;\nint d;\n"),
        (INSERT_AT_HEAD, "int d;\nint a;\nint b;\nint c;\n"),
    ],
    ids=("tail", "head"),
)
def test_an_insertion_only_hunk_applies_once_however_often_it_is_pressed(
    tmp_path: Path, body: str, once: str
) -> None:
    """A hunk with no `-` lines whose insertion sits at the edge of its own context.

    Reproduced with this module on 2026-09-05: press 1 gave
    `int a;\nint b;\nint c;\nint d;\n`, press 2 appended a second `int d;` and
    press 3 a third. The pre-image is pure context, so after the insert it is
    STILL contiguous -- `_find(lines, hunk.before)` hits, the post-image check
    below it never runs, and every press adds another copy. `patch-sources`
    reads the files on every press by design, so this is the ordinary path,
    not an edge.

    Three presses, not two: the second press is where the duplicate appears
    and the third is where an "idempotent after the second" fix would show.
    """
    target = tmp_path / "x.c"
    target.write_text("int a;\nint b;\nint c;\n")
    first = patch.apply(body, tmp_path, name="p")
    assert [(r.applied, r.present) for r in first] == [(1, 0)]
    assert target.read_text() == once
    for press in (2, 3):
        results = patch.apply(body, tmp_path, name="p")
        assert [(r.applied, r.present) for r in results] == [(0, 1)], press
        assert target.read_text() == once, press


def test_every_hunk_the_shipped_patch_carries_inserts_and_removes_nothing() -> None:
    """Why the case above is this patch's ordinary shape rather than a hypothetical.

    All five hunks are insertions; not one deletes a line. The shipped hunks
    escape the double-apply only because each insertion happens to sit in the
    MIDDLE of its context, which is a property of where the upstream code put
    its blank lines and not a property anyone chose.
    """
    hunks = patch.parse(text())
    assert [h.removals for h in hunks] == [0, 0, 0, 0, 0]
    assert all(len(h.after) > len(h.before) for h in hunks)
