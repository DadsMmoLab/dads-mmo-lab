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
