"""Tests for `yulon.catalog.build_context`: a fingerprint of what Docker would build from (T224).

Every fingerprint here is taken over REAL files in `tmp_path`, never over a fake
walk, and every "it changes" test first takes a fingerprint of the very same
tree without the change, so the difference can only come from the one thing
the test changed. Every "no answer" test first shows the same tree answering
with the limit or the fault taken away, so the None can only come from the
rule the test names (ten-ways item 1: one fixture, one rule).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import build_context, composegen
from yulon.catalog.build_context import fingerprint, parse_dockerignore
from yulon.catalog.catalog import load_catalog

TEMPLATES = resources.installers_dir()

# The Centurion checkout as the shipped catalogue names it, so the rendered
# `.dockerignore` below is the one a real Centurion server folder holds.
_CENTURION = load_catalog().get("wow-centurion")
assert _CENTURION is not None and _CENTURION.install.native is not None
assert _CENTURION.install.native.trinitycore is not None
CHECKOUT = _CENTURION.install.native.trinitycore.checkout

POSIX_MODES = pytest.mark.skipif(
    os.name == "nt", reason="Windows has no POSIX permission bits to change"
)


def _rendered(name: str, tokens: dict[str, str]) -> str:
    return composegen.fill((TEMPLATES / name).read_text(encoding="utf-8"), tokens)


def _write(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _centurion_server(root: Path) -> Path:
    """A server folder laid out as a Centurion install leaves it, with the shipped recipe files."""
    _write(
        root / ".dockerignore",
        _rendered("wow-centurion/native/dockerignore.tmpl", {"CHECKOUT": CHECKOUT}),
    )
    _write(root / "Dockerfile", f'FROM ubuntu:24.04\nCOPY ["{CHECKOUT}", "/src/core"]\n')
    _write(
        root / composegen.BUILD_FILE,
        _rendered(
            "shared/trinitycore/build.yml.tmpl", {"CONTAINER_PREFIX": "cent-", "BUILD_CONTEXT": "."}
        ),
    )
    _write(root / composegen.BASE_FILE, "services:\n  cent-worldserver:\n    image: cent:local\n")
    _write(root / composegen.OVERRIDE_FILE, "services: {}\n")
    core = root / CHECKOUT
    _write(core / "src/server/game/Spell.cpp", "int spell() { return 1; }\n")
    _write(core / "src/server/game/Spell.h", "int spell();\n")
    _write(core / "CMakeLists.txt", "project(centurion)\n")
    _write(core / ".git/HEAD", "ref: refs/heads/main\n")
    _write(core / "build/CMakeCache.txt", "cache\n")
    _write(core / "centurion/patches/patch-A.MPQ", b"\x00" * 64)
    _write(core / "centurion/sql/world.sql", "INSERT 1;\n")
    _write(core / "centurion/dbc/Spell.dbc", b"\x01" * 16)
    _write(core / "centurion/launcher/launcher.exe", b"MZ")
    _write(core / "sql/base/auth.sql", "CREATE TABLE a;\n")
    _write(core / "playerbot reference/notes.txt", "never compiled\n")
    _write(root / "client-data/maps/0000000.map", b"\x02" * 8)
    _write(root / "etc/worldserver.conf", "DataDir = /data\n")
    _write(root / ".env", "PASSWORD=x\n")
    return root


def _plain_server(root: Path, ignore: str) -> Path:
    """A server folder whose `.dockerignore` is `ignore` and whose recipe is the shipped overlay."""
    _write(root / ".dockerignore", ignore)
    _write(root / "Dockerfile", "FROM ubuntu:24.04\nCOPY . /src\n")
    _write(
        root / composegen.BUILD_FILE,
        _rendered(
            "shared/cmangos/build.yml.tmpl", {"CONTAINER_PREFIX": "tbc-", "BUILD_CONTEXT": "."}
        ),
    )
    return root


def _fp(root: Path, **kwargs: int) -> str:
    """A fingerprint that must answer; the tests about "no answer" call `fingerprint` directly."""
    answer = fingerprint(root, **kwargs)  # type: ignore[arg-type]
    assert answer is not None, "the fingerprint gave no answer on a tree it can read in full"
    return answer


# --- the .dockerignore matcher, against Docker's own rules ------------------------------------


@pytest.mark.parametrize(
    "template",
    sorted(
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.glob("*/native/dockerignore.tmpl")
    ),
)
def test_every_shipped_dockerignore_parses(template: str) -> None:
    text = _rendered(template, {"CHECKOUT": CHECKOUT})
    assert parse_dockerignore(text) is not None


def test_the_shipped_set_is_the_four_families_that_render_one() -> None:
    """The parametrisation above is not vacuous: it found the four templates the plan names."""
    found = {path.parent.parent.name for path in TEMPLATES.glob("*/native/dockerignore.tmpl")}
    assert found == {"wow-centurion", "wow-tbc", "wow-tortoise", "wow-vanilla"}


@pytest.mark.parametrize(
    ("pattern", "path", "excluded"),
    [
        # Root-relative: a pattern names a path from the context root, not a basename.
        ("foo", "foo", True),
        ("foo", "foo/deeper/file", True),
        ("foo", "a/foo", False),
        # A leading "/" is stripped (ignorefile.ReadAll), so it means the same thing.
        ("/foo", "foo/x", True),
        # ReadAll cleans BEFORE it strips the one leading "/", so "//foo" is "foo".
        ("//foo", "foo/x", True),
        # "*" and "?" never cross a "/".
        ("*.log", "x.log", True),
        ("*.log", "d/x.log", False),
        ("?.c", "a.c", True),
        ("?.c", "ab.c", False),
        # "**" crosses any number of directories, including none.
        ("**/*.log", "d/e/x.log", True),
        ("**/*.log", "x.log", True),
        ("d/**/x", "d/x", True),
        ("d/**/x", "d/e/f/x", True),
        # "foo/**" is Docker's prefix match on "foo/".
        ("foo/**", "foo/a/b", True),
        ("foo/**", "foobar", False),
        # A leading "**" with a literal rest is Docker's suffix match, which matches
        # "barfoo" too -- moby's own rule, reproduced rather than tidied.
        ("**foo", "barfoo", True),
        ("**/foo", "barfoo", False),
        ("**/foo", "foo", True),
        # Regex characters that mean nothing to a dockerignore are literal.
        ("a.c", "abc", False),
        ("a+(b)", "a+(b)", True),
        # Cleaned like filepath.Clean.
        ("./foo//bar/", "foo/bar", True),
        ("x/../foo", "foo", True),
    ],
)
def test_a_pattern_matches_as_docker_matches_it(pattern: str, path: str, excluded: bool) -> None:
    rules = parse_dockerignore(pattern + "\n")
    assert rules is not None
    assert rules.excludes(path) is excluded


def test_the_last_matching_pattern_wins() -> None:
    rules = parse_dockerignore("*\n!keep\nkeep/tmp\n")
    assert rules is not None
    assert rules.excludes("other") is True
    assert rules.excludes("keep/src.c") is False
    assert rules.excludes("keep/tmp/x") is True


def test_an_exception_is_cleaned_like_any_other_pattern() -> None:
    rules = parse_dockerignore("*\n!./keep\n")
    assert rules is not None
    assert rules.excludes("keep/src.c") is False
    assert rules.excludes("other") is True


def test_an_exception_reaches_into_an_excluded_folder() -> None:
    rules = parse_dockerignore("docs\n!docs/keep.md\n")
    assert rules is not None
    assert rules.excludes("docs/keep.md") is False
    assert rules.excludes("docs/other.md") is True


def test_only_a_hash_in_the_first_column_is_a_comment() -> None:
    rules = parse_dockerignore("# foo\n #bar\n")
    assert rules is not None
    assert rules.excludes("foo") is False
    # " #bar" is trimmed AFTER the comment check, so it is the pattern "#bar".
    assert rules.excludes("#bar") is True


def test_a_byte_order_mark_and_crlf_line_ends_are_read_as_docker_reads_them() -> None:
    rules = parse_dockerignore("\ufefffoo\r\nbar\r\n")
    assert rules is not None
    assert rules.excludes("foo") is True
    assert rules.excludes("bar") is True


@pytest.mark.parametrize(
    "text",
    [
        "src/[abc].c\n",  # a character class: Go's regexp and filepath.Match disagree on it
        "src/a\\*b\n",  # a backslash: an escape on Linux, a separator on Windows
        "!\n",  # Docker refuses a bare "!" (illegal exclusion pattern)
        "src/a^b\n",  # moby puts "^" into its regexp unescaped, as an anchor
    ],
)
def test_an_unsupported_or_illegal_pattern_answers_none(text: str) -> None:
    assert parse_dockerignore("*\n") is not None
    assert parse_dockerignore(text) is None


def test_a_path_the_two_docker_matchers_disagree_on_answers_none() -> None:
    """BuildKit's walk and the classic builder read `a/b` then `!a` differently; no guess is made.

    `MatchesOrParentMatches` re-admits `a/b` because `!a` matches its parent and
    comes last. `MatchesUsingParentResults` (what BuildKit's fsutil walks with)
    skipped `!a` at `a`, so `a/b` stays excluded.
    """
    rules = parse_dockerignore("a/b\n!a\n")
    assert rules is not None
    assert rules.excludes("a/c") is False
    assert rules.excludes("a/b") is None


# --- the fingerprint over a real tree ----------------------------------------------------------


def test_a_patch_applied_and_then_reverted_gives_the_same_fingerprint(tmp_path: Path) -> None:
    """The 2026-10-04 case: an uncommitted patch was built, then reverted under the same commit."""
    root = _centurion_server(tmp_path)
    spell = root / CHECKOUT / "src/server/game/Spell.cpp"
    before = _fp(root)
    original = spell.read_bytes()
    spell.write_bytes(original + b"// patched\n")
    patched = _fp(root)
    spell.write_bytes(original)
    os.utime(spell, ns=(1_000_000_000, 1_000_000_000))  # the revert leaves a new mtime
    assert patched != before
    assert _fp(root) == before


def test_a_content_change_of_the_same_size_changes_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    spell = root / CHECKOUT / "src/server/game/Spell.cpp"
    before = _fp(root)
    spell.write_text("int spell() { return 2; }\n", encoding="utf-8")
    assert _fp(root) != before


@POSIX_MODES
def test_a_mode_change_changes_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    spell = root / CHECKOUT / "src/server/game/Spell.cpp"
    before = _fp(root)
    spell.chmod(spell.stat().st_mode | stat.S_IXUSR)
    assert _fp(root) != before


@POSIX_MODES
def test_a_folder_mode_change_changes_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    game = root / CHECKOUT / "src/server/game"
    before = _fp(root)
    game.chmod(0o700)
    assert _fp(root) != before


def test_the_order_is_the_sorted_names_not_the_order_a_folder_lists_them_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ext4 lists by name hash and NTFS by name; the fingerprint must not care which.

    Only the listing ORDER is arranged: the real `os.scandir` runs and its real
    entries come back reversed.
    """
    root = _plain_server(tmp_path, "*\n!src\n")
    for i in range(40):
        _write(root / "src" / f"f{i:02d}.c", str(i))
    before = _fp(root)
    real_scandir = os.scandir

    class _Reversed:
        def __init__(self, path: str) -> None:
            with real_scandir(path) as listed:
                self.entries = list(listed)[::-1]

        def __enter__(self) -> Iterator[os.DirEntry[str]]:
            return iter(self.entries)

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(os, "scandir", _Reversed)
    with os.scandir(str(root / "src")) as listed:
        reversed_names = [e.name for e in listed]
    with real_scandir(root / "src") as listed:
        forward = [e.name for e in listed]
    assert reversed_names == forward[::-1] and reversed_names != forward
    assert _fp(root) == before


def test_a_rename_with_the_same_content_changes_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    game = root / CHECKOUT / "src/server/game"
    before = _fp(root)
    (game / "Spell.h").rename(game / "Spell.hpp")
    assert _fp(root) != before


def test_a_new_file_that_is_not_ignored_changes_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    before = _fp(root)
    _write(root / CHECKOUT / "src/server/game/Aura.cpp", "")
    assert _fp(root) != before


def test_a_new_empty_folder_that_is_not_ignored_changes_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    before = _fp(root)
    (root / CHECKOUT / "src/server/empty").mkdir()
    assert _fp(root) != before


@pytest.mark.parametrize(
    "ignored",
    [
        f"{CHECKOUT}/.git/HEAD",
        f"{CHECKOUT}/build/CMakeCache.txt",
        f"{CHECKOUT}/centurion/patches/patch-A.MPQ",
        f"{CHECKOUT}/centurion/sql/world.sql",
        f"{CHECKOUT}/centurion/dbc/Spell.dbc",
        f"{CHECKOUT}/centurion/launcher/launcher.exe",
        f"{CHECKOUT}/sql/base/auth.sql",
        f"{CHECKOUT}/playerbot reference/notes.txt",
        "client-data/maps/0000000.map",
        "etc/worldserver.conf",
        ".env",
    ],
)
def test_a_file_the_centurion_dockerignore_leaves_out_does_not_count(
    tmp_path: Path, ignored: str
) -> None:
    root = _centurion_server(tmp_path)
    before = _fp(root)
    target = root / ignored
    target.write_bytes(target.read_bytes() + b"changed")
    _write(target.parent / "a-new-neighbour", "new")
    assert _fp(root) == before


def test_the_centurion_core_tree_counts_where_the_dockerignore_admits_it(tmp_path: Path) -> None:
    """The neighbour of the test above: the same edit one folder over is counted."""
    root = _centurion_server(tmp_path)
    before = _fp(root)
    target = root / CHECKOUT / "CMakeLists.txt"
    target.write_bytes(target.read_bytes() + b"changed")
    assert _fp(root) != before


def test_an_exception_re_admits_a_file_inside_an_excluded_folder(tmp_path: Path) -> None:
    root = _plain_server(tmp_path, "*\n!src\nsrc/build\n!src/build/keep.txt\n")
    _write(root / "src/build/keep.txt", "kept")
    _write(root / "src/build/other.txt", "left out")
    _write(root / "src/main.c", "int main;")
    before = _fp(root)
    _write(root / "src/build/other.txt", "left out, changed")
    assert _fp(root) == before
    _write(root / "src/build/keep.txt", "kept, changed")
    assert _fp(root) != before


def test_an_exception_after_a_star_is_what_admits_the_tree(tmp_path: Path) -> None:
    """`*` alone sends nothing; `!src` is the one rule that makes `src/` count."""
    star_only = _plain_server(tmp_path / "a", "*\n")
    _write(star_only / "src/main.c", "one")
    before = _fp(star_only)
    _write(star_only / "src/main.c", "two")
    assert _fp(star_only) == before

    excepted = _plain_server(tmp_path / "b", "*\n!src\n")
    _write(excepted / "src/main.c", "one")
    before = _fp(excepted)
    _write(excepted / "src/main.c", "two")
    assert _fp(excepted) != before


def test_a_file_git_would_ignore_still_counts_if_docker_sends_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    _write(root / CHECKOUT / ".gitignore", "*.generated\n")
    _write(root / CHECKOUT / "src/out.generated", "one")
    before = _fp(root)
    _write(root / CHECKOUT / "src/out.generated", "two")
    assert _fp(root) != before


def test_a_nested_module_tree_counts_and_its_own_git_does_not(tmp_path: Path) -> None:
    root = _plain_server(
        tmp_path,
        _rendered("wow-tbc/native/dockerignore.tmpl", {"CHECKOUT": CHECKOUT}),
    )
    bots = root / "src/mangos-tbc/src/modules/Bots"
    _write(bots / "playerbot.cpp", "one")
    _write(bots / ".git/HEAD", "ref: a")
    before = _fp(root)
    _write(bots / ".git/HEAD", "ref: b")
    assert _fp(root) == before
    _write(bots / "playerbot.cpp", "two")
    assert _fp(root) != before


@pytest.mark.parametrize(
    "recipe", [".dockerignore", "Dockerfile", composegen.BUILD_FILE], ids=lambda n: n.lstrip(".")
)
def test_the_recipe_files_count_though_the_walk_leaves_them_out(
    tmp_path: Path, recipe: str
) -> None:
    """`*` excludes all three from the walk; each one is hashed in its own right."""
    root = _centurion_server(tmp_path)
    rules = parse_dockerignore((root / ".dockerignore").read_text(encoding="utf-8"))
    assert rules is not None and rules.excludes(recipe) is True
    before = _fp(root)
    target = root / recipe
    target.write_bytes(target.read_bytes() + b"\n# one more line\n")
    assert _fp(root) != before


def test_the_dockerfile_the_overlay_names_is_the_one_that_counts(tmp_path: Path) -> None:
    """WotLK's overlay names `apps/docker/Dockerfile`; that file counts, not a root `Dockerfile`."""
    root = _plain_server(tmp_path, "*\n")
    overlay = root / composegen.BUILD_FILE
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace(
            "dockerfile: Dockerfile", "dockerfile: apps/docker/Dockerfile"
        ),
        encoding="utf-8",
    )
    _write(root / "apps/docker/Dockerfile", "FROM ubuntu:24.04\n")
    before = _fp(root)
    _write(root / "Dockerfile", "FROM something-else\n")
    assert _fp(root) == before
    _write(root / "apps/docker/Dockerfile", "FROM ubuntu:26.04\n")
    assert _fp(root) != before


def test_yulons_own_files_at_the_root_do_not_count(tmp_path: Path) -> None:
    """With nothing ignored, the sidecar T224 writes must not change the fingerprint it records."""
    root = _plain_server(tmp_path, "")
    _write(root / "src/main.c", "one")
    before = _fp(root)
    _write(root / ".yulon-parked-build.json", '{"version": 1}')
    _write(root / ".yulon-install.json", "{}")
    assert _fp(root) == before
    _write(root / "src/.yulon-not-at-the-root", "x")
    assert _fp(root) != before


def test_the_refs_are_part_of_it(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    assert fingerprint(root, refs=["a:local"]) != fingerprint(root, refs=["b:local"])
    assert fingerprint(root, refs=["a:local", "b:local"]) == fingerprint(
        root, refs=["b:local", "a:local"]
    )


def test_the_answer_is_sixty_four_hex_characters(tmp_path: Path) -> None:
    answer = _fp(_centurion_server(tmp_path))
    assert len(answer) == 64
    assert int(answer, 16) >= 0


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_a_symlink_counts_by_its_target_and_is_not_followed(tmp_path: Path) -> None:
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "outside/a.txt", "one")
    _write(root / "outside/b.txt", "one")
    _write(root / "src/main.c", "x")
    try:
        os.symlink("../outside/a.txt", root / "src/link")
    except OSError:
        pytest.skip("this account may not make symlinks")
    before = _fp(root)
    _write(root / "outside/a.txt", "two")  # the target's content: not followed
    assert _fp(root) == before
    (root / "src/link").unlink()
    os.symlink("../outside/b.txt", root / "src/link")  # same content, other target
    assert _fp(root) != before


# T375: what Windows reports for a junction, stood in over a real POSIX symlink so the
# target text `os.readlink` gives is real. `FILE_ATTRIBUTE_DIRECTORY | _REPARSE_POINT`.
_DIR_REPARSE = 0x10 | 0x400
_MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT: a junction
_CLOUD = 0x9000601A  # IO_REPARSE_TAG_CLOUD_6: a OneDrive files-on-demand folder


@dataclass(frozen=True)
class _WindowsLook:
    st_mode: int
    st_file_attributes: int
    st_reparse_tag: int


class _AsWindowsSees:
    """A real `DirEntry` whose own look (`stat(follow_symlinks=False)`) is Windows' answer.

    On Windows that look comes from the folder listing itself: a junction is a
    folder (`S_IFDIR`) carrying the reparse-point attribute and its tag, and is a
    symlink to neither `DirEntry.is_symlink()` nor `stat.S_ISLNK`. Python 3.11,
    which the Windows build ships, has no `DirEntry.is_junction()`.
    """

    def __init__(self, real: os.DirEntry[str], tag: int) -> None:
        self.name = real.name
        self.path = real.path
        self._tag = tag

    def stat(self, *, follow_symlinks: bool = True) -> _WindowsLook:
        assert not follow_symlinks, "the walk looked through the entry"
        return _WindowsLook(stat.S_IFDIR | 0o777, _DIR_REPARSE, self._tag)

    def is_symlink(self) -> bool:
        return False

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return True


class _WithIsJunction(_AsWindowsSees):
    """The same entry on Python 3.12+, where `DirEntry.is_junction()` exists."""

    def is_junction(self) -> bool:
        return self._tag == _MOUNT_POINT


def _windows_listing(
    monkeypatch: pytest.MonkeyPatch,
    entry_type: type[_AsWindowsSees],
    path: Path,
    tag: int,
    *,
    enter_fails: bool,
) -> None:
    """`os.scandir` as Windows answers it for the one entry at `path`.

    With `enter_fails`, listing THROUGH that entry raises what Windows raised on
    yulon-win11 for a junction it could not follow (WinError 1920, live #312).
    """
    real_scandir = os.scandir

    class _Listing:
        def __init__(self, folder: str) -> None:
            if enter_fails and Path(folder) == path:
                raise OSError(22, "The file cannot be accessed by the system", folder)
            with real_scandir(folder) as listed:
                self.entries = [entry_type(e, tag) if Path(e.path) == path else e for e in listed]

        def __enter__(self) -> Iterator[object]:
            return iter(self.entries)

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(os, "scandir", _Listing)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
@pytest.mark.parametrize("entry_type", [_AsWindowsSees, _WithIsJunction], ids=["3.11", "3.12+"])
def test_a_junction_counts_by_its_target_and_is_not_entered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_type: type[_AsWindowsSees]
) -> None:
    """T375: one junction in the server folder used to cost the whole fingerprint.

    On 3.11 (the shipped Windows build) the walk entered it as a folder and the
    listing raised WinError 1920; on 3.12+ it was refused by name. Now it is a
    link like a symlink: its path, mode and target text count, and what is
    behind it is never read.
    """
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "outside/a/x.txt", "one")
    _write(root / "outside/b/x.txt", "one")
    _write(root / "src/main.c", "x")
    try:
        os.symlink("../outside/a", root / "src/junction")
    except OSError:
        pytest.skip("this account may not make symlinks")
    _windows_listing(monkeypatch, entry_type, root / "src/junction", _MOUNT_POINT, enter_fails=True)
    before = _fp(root)
    _write(root / "outside/a/x.txt", "two")  # what is behind it: not entered
    _write(root / "outside/a/new.txt", "new")
    assert _fp(root) == before
    (root / "src/junction").unlink()
    os.symlink("../outside/b", root / "src/junction")  # same content, other target
    assert _fp(root) != before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
@pytest.mark.parametrize("entry_type", [_AsWindowsSees, _WithIsJunction], ids=["3.11", "3.12+"])
def test_a_junction_the_dockerignore_leaves_out_is_not_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_type: type[_AsWindowsSees]
) -> None:
    """A junction in a folder that is walked, at a name the `.dockerignore` leaves out.

    3.12+ refused it by name before asking whether Docker sends it at all.
    """
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "src/main.c", "x")
    _write(root / "outside/x.txt", "one")
    without = _fp(root)
    try:
        os.symlink("outside", root / "junction")
    except OSError:
        pytest.skip("this account may not make symlinks")
    _windows_listing(monkeypatch, entry_type, root / "junction", _MOUNT_POINT, enter_fails=True)
    assert _fp(root) == without


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_a_reparse_point_with_no_tag_reported_counts_as_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cautious answer: an unknown reparse point is not entered."""
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "outside/x.txt", "one")
    _write(root / "src/main.c", "x")
    try:
        os.symlink("../outside", root / "src/unknown")
    except OSError:
        pytest.skip("this account may not make symlinks")
    _windows_listing(monkeypatch, _AsWindowsSees, root / "src/unknown", 0, enter_fails=True)
    before = _fp(root)
    _write(root / "outside/x.txt", "two")
    assert _fp(root) == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_a_link_whose_target_windows_cannot_read_answers_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WSL symlink: Python on Windows raises ValueError, not OSError, for its target.

    That must be "no answer", never an exception out of the build.
    """
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "outside/x.txt", "one")
    _write(root / "src/main.c", "x")
    link = root / "src/wsl-link"
    try:
        os.symlink("../outside", link)
    except OSError:
        pytest.skip("this account may not make symlinks")
    assert fingerprint(root) is not None
    _windows_listing(monkeypatch, _AsWindowsSees, link, 0xA000001D, enter_fails=True)
    real_readlink = os.readlink

    def readlink(path: str) -> str:
        if Path(path) == link:
            raise ValueError("not a symbolic link")  # CPython's words for another tag
        return real_readlink(path)

    monkeypatch.setattr(os, "readlink", readlink)
    assert fingerprint(root) is None


def test_a_reparse_point_that_names_no_other_file_is_walked_as_a_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A OneDrive files-on-demand folder is a reparse point but no link: its files count.

    Only a tag with the name-surrogate bit (junctions, symlinks, WSL symlinks)
    stands for another file; treating every reparse point as a link would leave
    a synced folder's sources out of the fingerprint.
    """
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "src/synced/x.txt", "one")
    _windows_listing(monkeypatch, _WithIsJunction, root / "src/synced", _CLOUD, enter_fails=False)
    before = _fp(root)
    _write(root / "src/synced/x.txt", "two")
    assert _fp(root) != before


def test_a_real_junction_counts_by_its_target_and_is_not_entered(tmp_path: Path) -> None:
    """T375 on Windows itself: a real junction, also once its target is gone."""
    if sys.platform != "win32":
        pytest.skip("junctions are Windows'")
    import _winapi

    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "outside/a/x.txt", "one")
    _write(root / "outside/b/x.txt", "one")
    _write(root / "src/main.c", "x")
    junction = root / "src/junction"
    _winapi.CreateJunction(str(root / "outside/a"), str(junction))
    before = _fp(root)
    _write(root / "outside/a/x.txt", "two")
    assert _fp(root) == before
    os.rmdir(junction)  # removes the junction, never its target
    _winapi.CreateJunction(str(root / "outside/b"), str(junction))
    retargeted = _fp(root)
    assert retargeted != before
    shutil.rmtree(root / "outside/b")  # the junction now names nothing
    assert _fp(root) == retargeted


# --- no answer ---------------------------------------------------------------------------------


def _fifo(path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("named pipes are POSIX")
    os.mkfifo(path)


def _unreadable(path: Path) -> None:
    path.chmod(0)
    try:
        if path.is_dir():
            os.listdir(path)
        else:
            path.open("rb").close()
    except PermissionError:
        return
    path.chmod(0o755)
    pytest.skip("this account reads a file with no permissions (root, or Windows)")


def test_an_unreadable_file_answers_none(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    spell = root / CHECKOUT / "src/server/game/Spell.cpp"
    assert fingerprint(root) is not None
    _unreadable(spell)
    try:
        assert fingerprint(root) is None
    finally:
        spell.chmod(0o644)


def test_an_unreadable_folder_that_docker_sends_answers_none(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    game = root / CHECKOUT / "src/server/game"
    assert fingerprint(root) is not None
    _unreadable(game)
    try:
        assert fingerprint(root) is None
    finally:
        game.chmod(0o755)


def test_an_unreadable_file_docker_does_not_send_is_not_read(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    before = _fp(root)
    patch = root / CHECKOUT / "centurion/patches/patch-A.MPQ"
    _unreadable(patch)
    try:
        assert fingerprint(root) == before
    finally:
        patch.chmod(0o644)


def test_a_special_file_docker_would_send_answers_none(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    assert fingerprint(root) is not None
    _fifo(root / CHECKOUT / "src/pipe")
    assert fingerprint(root) is None


def test_a_file_that_changes_while_it_is_read_answers_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real append lands between the read and the check after it; the answer is then None.

    Only the moment is arranged: `os.fstat` is wrapped so that, for the one file
    under test, the bytes on disk really change after they were hashed and before
    the second look. Every other file goes through untouched.
    """
    root = _centurion_server(tmp_path)
    spell = root / CHECKOUT / "src/server/game/Spell.cpp"
    assert fingerprint(root) is not None
    real_open, real_fstat = os.open, os.fstat
    opened: dict[int, int] = {}

    def open_(path: str | os.PathLike[str], flags: int, *args: int) -> int:
        fd = real_open(path, flags, *args)
        if Path(path) == spell:
            opened[fd] = 0
        return fd

    def fstat(fd: int) -> os.stat_result:
        if fd in opened:
            opened[fd] += 1
            if opened[fd] == 2:
                with spell.open("ab") as handle:
                    handle.write(b"// appended during the read\n")
        return real_fstat(fd)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "fstat", fstat)
    assert fingerprint(root) is None
    assert list(opened.values()) == [2], "the file under test was not read exactly once"


def test_a_name_that_is_not_utf8_answers_none(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip("only Linux keeps a file name that is not UTF-8")
    root = _centurion_server(tmp_path)
    assert fingerprint(root) is not None
    game = os.fsencode(root / CHECKOUT / "src/server/game")
    os.close(os.open(game + b"/caf\xe9.cpp", os.O_CREAT | os.O_WRONLY, 0o644))
    assert fingerprint(root) is None


def _counted(root: Path) -> int:
    """How many entries the walk looks at, counted from the tree rather than from the code."""
    n = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        n += len(dirnames) + len(filenames)
    return n


def test_the_entry_cap_answers_none(tmp_path: Path) -> None:
    root = _plain_server(tmp_path, "")
    for i in range(5):
        _write(root / f"src/f{i}.c", "x")
    total = _counted(root)
    assert fingerprint(root, max_entries=total) is not None
    assert fingerprint(root, max_entries=total - 1) is None


def test_the_byte_cap_answers_none(tmp_path: Path) -> None:
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "src/big.bin", b"\x00" * 10_000)
    recipe = sum(
        (root / name).stat().st_size
        for name in (".dockerignore", "Dockerfile", composegen.BUILD_FILE)
    )
    assert fingerprint(root, max_bytes=recipe + 10_000) is not None
    assert fingerprint(root, max_bytes=recipe + 9_999) is None


def test_the_default_caps_are_the_plans() -> None:
    assert build_context.MAX_BYTES == 4 * 1024**3
    assert build_context.MAX_ENTRIES == 200_000


def test_an_unsupported_pattern_answers_none(tmp_path: Path) -> None:
    root = _plain_server(tmp_path, "*\n!src\n")
    _write(root / "src/main.c", "x")
    assert fingerprint(root) is not None
    _write(root / ".dockerignore", "*\n!src\nsrc/[ab].c\n")
    assert fingerprint(root) is None


def test_a_per_dockerfile_ignore_answers_none(tmp_path: Path) -> None:
    root = _plain_server(tmp_path, "*\n")
    assert fingerprint(root) is not None
    _write(root / "Dockerfile.dockerignore", "*\n")
    assert fingerprint(root) is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # Each edit trips exactly one rule: the files each one names exist, and the
        # recipe line the others need is kept.
        ("context: .", "context: ./src"),  # a context that is not the server folder
        ("dockerfile: Dockerfile", "dockerfile: ../Dockerfile"),  # a recipe outside it
        # a value interpolated from the environment
        ("dockerfile: Dockerfile", "dockerfile: Dockerfile\n      target: ${TARGET}"),
        # an inline recipe beside the file one
        ("dockerfile: Dockerfile", "dockerfile: Dockerfile\n      dockerfile_inline: FROM x"),
        (
            "dockerfile: Dockerfile",
            "dockerfile: Dockerfile\n      additional_contexts:\n        x: ./x",
        ),
    ],
)
def test_an_overlay_the_fingerprint_cannot_cover_answers_none(
    tmp_path: Path, old: str, new: str
) -> None:
    root = _plain_server(tmp_path / "server", "*\n")
    _write(tmp_path / "Dockerfile", "FROM ubuntu:24.04\n")  # what "../Dockerfile" names
    _write(root / "src/Dockerfile", "FROM ubuntu:24.04\n")
    assert fingerprint(root) is not None
    overlay = root / composegen.BUILD_FILE
    text = overlay.read_text(encoding="utf-8")
    assert old in text
    overlay.write_text(text.replace(old, new), encoding="utf-8")
    assert fingerprint(root) is None


def test_a_build_key_in_the_base_compose_file_answers_none(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    assert fingerprint(root) is not None
    _write(
        root / composegen.BASE_FILE,
        "services:\n  cent-worldserver:\n    image: cent:local\n    build: .\n",
    )
    assert fingerprint(root) is None


def test_a_missing_recipe_answers_none(tmp_path: Path) -> None:
    root = _centurion_server(tmp_path)
    assert fingerprint(root) is not None
    (root / "Dockerfile").unlink()
    assert fingerprint(root) is None


def test_a_missing_dockerignore_sends_everything(tmp_path: Path) -> None:
    """Docker sends the whole folder when there is no `.dockerignore`; so does the walk."""
    root = _plain_server(tmp_path, "")
    (root / ".dockerignore").unlink()
    _write(root / "anything/at/all.txt", "one")
    before = _fp(root)
    _write(root / "anything/at/all.txt", "two")
    assert _fp(root) != before


def test_the_walk_does_not_enter_a_folder_no_exception_can_reach(tmp_path: Path) -> None:
    """`centurion/patches` (1.47 GB on a real checkout) is never listed: unreadable is fine.

    The neighbour that shows the walk would notice is
    `test_an_unreadable_folder_that_docker_sends_answers_none`.
    """
    root = _centurion_server(tmp_path)
    before = _fp(root)
    patches = root / CHECKOUT / "centurion/patches"
    _unreadable(patches)
    try:
        assert fingerprint(root) == before
    finally:
        patches.chmod(0o755)


# --- T230: a recipe recognised by its bytes is walked only where its stages read -------------

WOTLK_DATA = Path(__file__).resolve().parent / "data" / "azerothcore-wotlk-f19a1879"
"""mod-playerbots/azerothcore-wotlk at the catalog pin, byte for byte: `apps/docker/Dockerfile`,
`src/cmake/genrev.cmake` and the root `.dockerignore` (read 2026-10-05, T230 plan §1; the three
files were the same bytes at f19a1879 when the pin moved there, 2026-10-08)."""
WOTLK_RECIPE_SHA = "e87bc1bd18f94bbf1705b81438b0caf7a1c8c8c8a0330cdaf08511f0f3ae7964"
GENREV_SHA = "a27f319585605516ed82601d87a7785a135ae3b3d0b725d1bf4f24615b6d342b"
WOTLK_READS = (".git", "CMakeLists.txt", "apps", "conf", "data", "deps", "modules", "src")
"""What the stages Yu'lon builds COPY or bind from the context, read by hand (plan §1)."""
WOTLK_RECIPE = "apps/docker/Dockerfile"
GENREV = "src/cmake/genrev.cmake"
MODULE = "modules/mod-playerbots"
ROOT_COMMIT = "1" * 40
MODULE_COMMIT = "2" * 40
OTHER_COMMIT = "3" * 40


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_vendored_wotlk_recipe_is_known() -> None:
    assert _sha256(WOTLK_DATA / "Dockerfile") == WOTLK_RECIPE_SHA, "the vendored copy drifted"
    assert _sha256(WOTLK_DATA / "genrev.cmake") == GENREV_SHA, "the vendored copy drifted"
    known = build_context.KNOWN_RECIPES[WOTLK_RECIPE_SHA]
    assert known.reads == WOTLK_READS
    assert known.git_reader == (GENREV, GENREV_SHA)


def test_the_vendored_wotlk_recipe_is_the_catalog_pins() -> None:
    """A pin bump fails here until someone reads the new Dockerfile and genrev again (plan §1)."""
    entry = load_catalog().get("wow-wotlk")
    assert entry is not None
    core = next(source for source in entry.emulator.sources if source.dest == ".")
    assert core.rev is not None
    assert core.rev.startswith(WOTLK_DATA.name.rsplit("-", 1)[1]), core.rev


def _wotlk_server(root: Path) -> Path:
    """A WotLK server folder with upstream's recipe files and something under every read."""
    _write(root / WOTLK_RECIPE, (WOTLK_DATA / "Dockerfile").read_bytes())
    _write(root / ".dockerignore", (WOTLK_DATA / "dockerignore").read_bytes())
    _write(root / GENREV, (WOTLK_DATA / "genrev.cmake").read_bytes())
    _write(
        root / composegen.BUILD_FILE,
        _rendered(
            "wow-wotlk/native/build.yml.tmpl", {"CONTAINER_PREFIX": "ac-", "BUILD_CONTEXT": "."}
        ),
    )
    _write(root / composegen.BASE_FILE, "services:\n  ac-worldserver:\n    image: ac:local\n")
    _write(root / composegen.OVERRIDE_FILE, "services: {}\n")
    _write(root / "CMakeLists.txt", "project(AzerothCore)\n")
    _write(root / "apps/docker/entrypoint.sh", "#!/bin/sh\n")
    _write(root / "conf/dist/config.sh", "CTYPE=Release\n")
    _write(root / "data/sql/base/db_world/creature.sql", "CREATE TABLE creature;\n")
    _write(root / "deps/boost/config.h", "#define BOOST 1\n")
    _write(root / "src/server/game/World.cpp", "void World::Update() {}\n")
    _write(root / MODULE / "src/Bot.cpp", "void Bot() {}\n")
    # The root `.git`, detached, as `checkout --detach` leaves it.
    _write(root / ".git/HEAD", f"{ROOT_COMMIT}\n")
    _write(root / ".git/objects/pack/pack-1.pack", b"PACK1")
    _write(root / ".git/index", b"DIRC1")
    _write(root / ".git/FETCH_HEAD", f"{ROOT_COMMIT}\t\tbranch 'Playerbot'\n")
    # The module's `.git`, on a branch whose ref is only in packed-refs.
    _write(root / MODULE / ".git/HEAD", "ref: refs/heads/master\n")
    _write(root / MODULE / ".git/packed-refs", f"{MODULE_COMMIT} refs/heads/master\n")
    _write(root / MODULE / ".git/index", b"DIRC2")
    # Outside every read.
    _write(root / "sql_scripts/backups/20261005_010000_acore_characters.sql", "-- dump\n")
    _write(root / "sql_scripts/clones/m/x.sql", "-- clone\n")
    _write(root / "ale_scripts/x.lua", "-- ale\n")
    return root


def _off_by_a_byte(root: Path, name: str) -> None:
    target = root / name
    target.write_bytes(target.read_bytes() + b"\n")


@pytest.mark.parametrize(
    "unread",
    [
        "sql_scripts/backups/20261005_010000_acore_characters.sql",
        "sql_scripts/backups/20261005_020000_before-new-build_acore_world.sql",
        "sql_scripts/backups/restore-in-progress.json",
        "sql_scripts/clones/m/x.sql",
        "ale_scripts/x.lua",
        composegen.OVERRIDE_FILE,
        composegen.BASE_FILE,
        "PreLoad.cmake",
        "tools/x",
        "datadump/x",
        "src-old/x",
    ],
)
def test_on_a_known_recipe_what_no_stage_reads_does_not_count(tmp_path: Path, unread: str) -> None:
    root = _wotlk_server(tmp_path)
    rules = parse_dockerignore((root / ".dockerignore").read_text(encoding="utf-8"))
    # The one rule that leaves it out is the reads: upstream's .dockerignore admits it.
    assert rules is not None and rules.excludes(unread) is False
    before = _fp(root)
    target = root / unread
    _write(target, (target.read_bytes() if target.exists() else b"") + b"# changed\n")
    assert _fp(root) == before


@pytest.mark.parametrize(
    "read",
    [
        "CMakeLists.txt",
        "apps/docker/entrypoint.sh",
        "conf/dist/config.sh",
        "data/sql/base/db_world/creature.sql",
        "deps/boost/config.h",
        f"{MODULE}/src/Bot.cpp",
        "src/server/game/World.cpp",
    ],
)
def test_on_a_known_recipe_every_path_a_stage_reads_counts(tmp_path: Path, read: str) -> None:
    root = _wotlk_server(tmp_path)
    before = _fp(root)
    _off_by_a_byte(root, read)
    assert _fp(root) != before


def test_on_a_known_recipe_the_dockerignore_still_applies_inside_what_is_read(
    tmp_path: Path,
) -> None:
    root = _wotlk_server(tmp_path)
    _off_by_a_byte(root, ".dockerignore")
    with (root / ".dockerignore").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("src/generated\n")
    _write(root / "src/generated/x.h", "one")
    _write(root / "src/other/x.h", "one")
    before = _fp(root)
    _write(root / "src/generated/x.h", "two")
    assert _fp(root) == before
    _write(root / "src/other/x.h", "two")
    assert _fp(root) != before


@pytest.mark.parametrize("unread", ["sql_scripts", "sql_scripts/backups"])
def test_on_a_known_recipe_an_unreadable_backup_is_not_read(tmp_path: Path, unread: str) -> None:
    """Mirrors `test_an_unreadable_file_docker_does_not_send_is_not_read`: nothing is listed.

    `sql_scripts` is the case that shows the filter comes before a folder is
    listed: the backups folder sits one level under a path no stage reads.
    """
    root = _wotlk_server(tmp_path)
    before = _fp(root)
    folder = root / unread
    _unreadable(folder)
    try:
        assert fingerprint(root) == before
    finally:
        folder.chmod(0o755)


def test_a_recipe_one_byte_off_walks_the_whole_context_as_before(tmp_path: Path) -> None:
    root = _wotlk_server(tmp_path)
    _off_by_a_byte(root, WOTLK_RECIPE)
    before = _fp(root)
    _write(root / "sql_scripts/backups/new.sql", "-- dump\n")
    assert _fp(root) != before


@POSIX_MODES
def test_an_unknown_recipe_gives_the_same_fingerprint_as_before(tmp_path: Path) -> None:
    """Centurion, TBC, Vanilla and Tortoise keep their kept builds across T230.

    The digest was taken at 5e8da91a (T224, before T230) over this very tree, so
    a whole-walk stream that changed by one byte fails here. Modes are set
    explicitly, because they are hashed and a umask would otherwise decide them.
    """
    root = tmp_path / "server"
    _write(root / ".dockerignore", "build\n")
    _write(root / "Dockerfile", "FROM ubuntu:24.04\nCOPY . /src\n")
    _write(
        root / composegen.BUILD_FILE,
        "services:\n  s:\n    build:\n      context: .\n      dockerfile: Dockerfile\n",
    )
    _write(root / "src/main.c", "int main;\n")
    _write(root / ".git/HEAD", "a" * 40 + "\n")
    _write(root / ".git/FETCH_HEAD", "x\n")
    _write(root / "sql_scripts/backups/x.sql", "-- dump\n")
    _write(root / "build/x.o", "o")
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            os.chmod(os.path.join(dirpath, name), 0o755)
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), 0o644)
    assert fingerprint(root, refs=["ac-worldserver:local"]) == (
        "bdf9978b63f95fb5afd126dfa52a2634af5a455e50bd604f87ab1902f8b382b1"
    )


# --- T230: a `.git` folder inside what is read counts as HEAD and its commit ------------------

_HOUSEKEEPING = {
    "FETCH_HEAD": ("FETCH_HEAD", b"feedface\t\tbranch 'x'\n"),
    "index": ("index", b"DIRC-refreshed"),
    "pack": ("objects/pack/pack-2.pack", b"PACK2"),
    "remote ref": ("refs/remotes/origin/master", f"{OTHER_COMMIT}\n".encode()),
    "ORIG_HEAD": ("ORIG_HEAD", f"{OTHER_COMMIT}\n".encode()),
    "logs": ("logs/HEAD", b"0000 1111 fetch\n"),
    "packed-refs line": ("packed-refs", f"{OTHER_COMMIT} refs/remotes/origin/x\n".encode()),
}


@pytest.mark.parametrize("gitdir", [".git", f"{MODULE}/.git"], ids=["root", "module"])
@pytest.mark.parametrize("kind", sorted(_HOUSEKEEPING))
def test_on_a_known_recipe_git_housekeeping_does_not_count(
    tmp_path: Path, gitdir: str, kind: str
) -> None:
    name, data = _HOUSEKEEPING[kind]
    root = _wotlk_server(tmp_path / "known")
    before = _fp(root)
    target = root / gitdir / name
    _write(target, (target.read_bytes() if target.exists() else b"") + data)
    assert _fp(root) == before
    # The neighbour: a byte walk of the same folder would have counted it.
    whole = _wotlk_server(tmp_path / "whole")
    _off_by_a_byte(whole, WOTLK_RECIPE)
    before = _fp(whole)
    target = whole / gitdir / name
    _write(target, (target.read_bytes() if target.exists() else b"") + data)
    assert _fp(whole) != before


def test_on_a_known_recipe_head_moving_to_another_commit_counts(tmp_path: Path) -> None:
    """Same files, same branch name, another commit: only the resolved commit differs."""
    root = _wotlk_server(tmp_path)
    _write(root / ".git/HEAD", "ref: refs/heads/Playerbot\n")
    _write(root / ".git/refs/heads/Playerbot", f"{ROOT_COMMIT}\n")
    before = _fp(root)
    _write(root / ".git/refs/heads/Playerbot", f"{OTHER_COMMIT}\n")
    assert _fp(root) != before


def test_on_a_known_recipe_the_branch_name_counts(tmp_path: Path) -> None:
    """One commit, two branch names: genrev bakes the name into the version line."""
    root = _wotlk_server(tmp_path)
    _write(root / ".git/refs/heads/a", f"{ROOT_COMMIT}\n")
    _write(root / ".git/refs/heads/b", f"{ROOT_COMMIT}\n")
    _write(root / ".git/HEAD", "ref: refs/heads/a\n")
    before = _fp(root)
    _write(root / ".git/HEAD", "ref: refs/heads/b\n")
    assert _fp(root) != before


def test_on_a_known_recipe_a_branch_head_resolves_through_packed_refs(tmp_path: Path) -> None:
    root = _wotlk_server(tmp_path)
    before = _fp(root)
    # Resolved, so housekeeping does not count: the HEAD digest is what is in use here.
    _write(root / MODULE / ".git/FETCH_HEAD", "x\n")
    assert _fp(root) == before
    _write(root / MODULE / ".git/packed-refs", f"{OTHER_COMMIT} refs/heads/master\n")
    assert _fp(root) != before


@pytest.mark.parametrize(
    "unresolvable",
    [
        {"HEAD": "ref: refs/heads/gone\n"},
        {"HEAD": "ref: refs/heads/main\n", "refs/heads/main": "ref: refs/heads/other\n"},
        {"commondir": "../..\n"},
        {"reftable/tables.list": "0x01.ref\n"},
    ],
    ids=["missing ref", "symref chain", "worktree commondir", "reftable"],
)
def test_a_git_folder_whose_head_cannot_be_read_is_hashed_byte_for_byte(
    tmp_path: Path, unresolvable: dict[str, str]
) -> None:
    root = _wotlk_server(tmp_path)
    for name, text in unresolvable.items():
        _write(root / ".git" / name, text)
    before = _fp(root)
    _write(root / ".git/FETCH_HEAD", "fetched again\n")
    assert _fp(root) != before


def test_a_changed_git_reader_hashes_git_byte_for_byte(tmp_path: Path) -> None:
    root = _wotlk_server(tmp_path)
    _off_by_a_byte(root, GENREV)
    before = _fp(root)
    _write(root / "sql_scripts/backups/new.sql", "-- dump\n")
    assert _fp(root) == before, "the recipe is still known: backups still do not count"
    _write(root / ".git/FETCH_HEAD", "fetched again\n")
    assert _fp(root) != before


def test_a_missing_git_reader_hashes_git_byte_for_byte(tmp_path: Path) -> None:
    root = _wotlk_server(tmp_path)
    (root / GENREV).unlink()
    before = _fp(root)
    _write(root / ".git/FETCH_HEAD", "fetched again\n")
    assert _fp(root) != before


def test_a_git_file_inside_what_is_read_counts_by_its_bytes(tmp_path: Path) -> None:
    """A submodule leaves a `.git` FILE; it is a file like any other, not a git folder."""
    root = _wotlk_server(tmp_path)
    _write(root / "modules/mod-ale/.git", "gitdir: ../../.git/modules/mod-ale\n")
    before = _fp(root)
    _write(root / "modules/mod-ale/.git", "gitdir: ../../.git/modules/mod-ale-2\n")
    assert _fp(root) != before


def test_an_unknown_recipe_still_hashes_git_byte_for_byte(tmp_path: Path) -> None:
    root = _wotlk_server(tmp_path)
    _off_by_a_byte(root, WOTLK_RECIPE)
    before = _fp(root)
    _write(root / ".git/FETCH_HEAD", "fetched again\n")
    assert _fp(root) != before
