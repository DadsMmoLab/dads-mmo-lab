"""Tests for `yulon.catalog.build_context`: a fingerprint of what Docker would build from (T224).

Every fingerprint here is taken over REAL files in `tmp_path`, never over a fake
walk, and every "it changes" test first takes a fingerprint of the very same
tree without the change, so the difference can only come from the one thing
the test changed. Every "no answer" test first shows the same tree answering
with the limit or the fault taken away, so the None can only come from the
rule the test names (ten-ways item 1: one fixture, one rule).
"""

from __future__ import annotations

import os
import stat
import sys
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
    """A FIFO under `centurion/patches` would answer None if the walk looked at it; it does not."""
    root = _centurion_server(tmp_path)
    before = _fp(root)
    _fifo(root / CHECKOUT / "centurion/patches/pipe")
    assert _fp(root) == before
