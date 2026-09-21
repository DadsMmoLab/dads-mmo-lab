"""`build/release_notes.py`: the release body is what CHANGELOG.md gained since the last tag.

"Since the last tag" means the previous `-Public` one: a `-fixtest` tag is still
measured against the last public release.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

import release_notes as rn  # noqa: E402

OLD = """# Changelog

Preamble paragraph.

## Unreleased
- Alpha can now do one.

### Fixed
- Beta no longer breaks.

## v0.6.59Public — 2026-08-29

The last release before this log existed.
"""

NEW = """# Changelog

Preamble paragraph.

## Unreleased
- Gamma is new.
- Alpha can now do one.

_Everything below landed on a branch._

### Operate a running server
- **Accounts** — create one.

### Fixed
- Delta is fixed.
  with a continuation line.
- Beta no longer breaks.

## v0.6.59Public — 2026-08-29

The last release before this log existed.
"""


def test_parse_keeps_heading_and_order() -> None:
    assert rn.parse_changelog(OLD) == [
        ("New", "- Alpha can now do one."),
        ("Fixed", "- Beta no longer breaks."),
    ]


def test_bullets_directly_under_a_release_heading_are_new() -> None:
    assert ("New", "- Gamma is new.") in rn.parse_changelog(NEW)


def test_a_continuation_line_belongs_to_its_bullet() -> None:
    assert ("Fixed", "- Delta is fixed.\n  with a continuation line.") in rn.parse_changelog(NEW)


def test_prose_that_is_not_a_bullet_is_not_an_entry() -> None:
    texts = [bullet for _, bullet in rn.parse_changelog(NEW)]
    assert not any("Everything below" in t or "last release before" in t for t in texts)


def test_new_entries_are_the_difference_grouped_in_file_order() -> None:
    assert rn.new_entries(OLD, NEW) == (
        "### New\n- Gamma is new.\n\n"
        "### Operate a running server\n- **Accounts** — create one.\n\n"
        "### Fixed\n- Delta is fixed.\n  with a continuation line.\n"
    )


def test_nothing_new_is_the_empty_string() -> None:
    assert rn.new_entries(NEW, NEW) == ""


def test_no_previous_changelog_means_everything_is_new() -> None:
    assert rn.new_entries("", OLD) == (
        "### New\n- Alpha can now do one.\n\n### Fixed\n- Beta no longer breaks.\n"
    )


TAGS = [
    "v0.6.59Public",
    "v0.8.0-Public",
    "v0.8.65-Public",
    "v0.8.66-fixtest",
    "v0.8.68-fixtest",
    "v0.8.5-DeckTest",
    "junk",
]


def test_previous_is_the_highest_public_tag_below_this_one() -> None:
    assert rn.pick_previous(TAGS, "v0.8.70-Public") == "v0.8.65-Public"
    assert rn.pick_previous(TAGS, "v0.8.65-Public") == "v0.8.0-Public"


def test_a_test_tag_still_gets_notes_against_the_last_public_tag() -> None:
    assert rn.pick_previous(TAGS, "v0.8.68-fixtest") == "v0.8.65-Public"


def test_no_lower_public_tag_is_none() -> None:
    assert rn.pick_previous(TAGS, "v0.8.0-Public") is None
    assert rn.pick_previous(TAGS, "not-a-version") is None


def test_public_is_case_insensitive_and_needs_the_dash() -> None:
    assert rn.pick_previous(["v0.1.0-public", "v0.2.0Public"], "v0.9.0-Public") == "v0.1.0-public"


REAL_TAGS = [
    "v0.6.5",
    "v0.6.51Public",
    "v0.6.52Public",
    "v0.6.55Public",
    "v0.6.57Public",
    "v0.6.58Public",
    "v0.6.59Public",
    "v0.6.60-phase8",
    "v0.8.0-Public",
    "v0.8.4-Public",
    "v0.8.5-DeckTest",
    "v0.8.6-DeckTest",
    "v0.8.65-DeckTest",
    "v0.8.65-Public",
    "v0.8.66-fixtest",
    "v0.8.67-fixtest",
    "v0.8.68-fixtest",
    "v0.8.69-fixtest",
    "v0.8.7-Public",
]
"""`git tag --list 'v*'` on 2026-09-21, the releases among them.

A literal and not a `git` call: CI checks out shallow, with no tags at all, so a
test that read them would pass here and be vacuous there. What it costs is that
this list is a copy, which is why the ordering below is asserted against the
dates too - `v0.8.7-Public` was cut on 2026-09-19, six days AFTER
`v0.8.65-Public`, and that is the fact the scheme has to reproduce.
"""


def test_the_last_number_orders_as_a_decimal_fraction() -> None:
    """Owner's rule, 2026-09-21: `.65` is sixty-five hundredths, not sixty-five."""
    ordered = ["v0.8.6", "v0.8.65", "v0.8.66", "v0.8.69", "v0.8.7"]
    keys = [rn.version_key(tag) for tag in ordered]
    assert keys == sorted(keys), f"decimal order broken: {list(zip(ordered, keys, strict=True))}"
    assert rn.version_key("v0.8.7") == rn.version_key("v0.8.70"), ".7 and .70 are one version"
    assert rn.version_key("v0.8.0") < rn.version_key("v0.8.01")
    assert rn.version_key("v0.9.0") > rn.version_key("v0.8.99")


def test_previous_across_the_real_tag_history() -> None:
    """The case that made this a bug: 0.8.7 came after 0.8.65, not before it."""
    assert rn.pick_previous(REAL_TAGS, "v0.8.7-Public") == "v0.8.65-Public"
    assert rn.pick_previous(REAL_TAGS, "v0.8.71-Public") == "v0.8.7-Public"
    assert rn.pick_previous(REAL_TAGS, "v0.8.69-fixtest") == "v0.8.65-Public"
    assert rn.pick_previous(REAL_TAGS, "v0.8.65-Public") == "v0.8.4-Public"


def test_a_version_equal_to_this_one_is_not_a_previous_release() -> None:
    """`.70` and `.7` are the same version, so 0.8.70's previous is 0.8.65."""
    assert rn.pick_previous(REAL_TAGS, "v0.8.70-Public") == "v0.8.65-Public"


def _git(answers: dict[tuple[str, ...], str]) -> Callable[[list[str]], str]:
    def run(argv: list[str]) -> str:
        key = tuple(argv)
        if key not in answers:
            raise OSError(f"git {argv} failed")
        return answers[key]

    return run


def test_main_writes_the_notes(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    run = _git(
        {
            ("tag", "--list", "v*"): "\n".join(TAGS),
            ("show", "v0.8.65-Public:CHANGELOG.md"): OLD,
            ("show", "v0.8.70-Public:CHANGELOG.md"): NEW,
        }
    )
    assert rn.main(["--tag", "v0.8.70-Public", "--out", str(out)], run_git=run) == 0
    assert out.read_text(encoding="utf-8").startswith("### New\n- Gamma is new.")


def test_main_never_fails_the_release(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    assert rn.main(["--tag", "v0.8.70-Public", "--out", str(out)], run_git=_git({})) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_a_failure_that_is_not_an_oserror_still_leaves_a_release(tmp_path: Path) -> None:
    """`--out` must exist and the exit code must be 0 whatever `run_git` raises.

    "It exits 0 whatever happens" was only true for `OSError` when this was
    written, and the one failure the script is actually likely to meet -- a
    changelog blob that is not valid UTF-8 -- raises `UnicodeDecodeError`,
    which is a `ValueError`.
    """
    for raised in (ValueError("not utf-8"), RuntimeError("something else")):

        def run(argv: list[str], exc: BaseException = raised) -> str:
            raise exc

        out = tmp_path / f"notes-{type(raised).__name__}.md"
        assert rn.main(["--tag", "v0.8.70-Public", "--out", str(out)], run_git=run) == 0
        assert out.read_text(encoding="utf-8") == ""


def test_a_cancelled_run_is_not_swallowed(tmp_path: Path) -> None:
    """`Exception`, not `BaseException`: a cancel stops, it does not publish notes."""

    def cancelled(argv: list[str]) -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        rn.main(["--tag", "v0.8.70-Public", "--out", str(tmp_path / "x.md")], run_git=cancelled)


def _repo_with_changelog(root: Path, raw: bytes) -> None:
    """A throwaway repository holding `raw` as CHANGELOG.md at tag v0.9.0-Public."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }

    def run(*argv: str) -> None:
        subprocess.run(["git", *argv], cwd=root, env=env, check=True, capture_output=True)

    run("init", "-q")
    (root / "CHANGELOG.md").write_bytes(raw)
    run("add", "CHANGELOG.md")
    run("commit", "-qm", "one")
    run("tag", "v0.9.0-Public")


def test_a_changelog_that_is_not_utf8_still_becomes_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `_git`, not the injected one: nothing else exercises it.

    A single byte `0xe9` (latin-1 "é") is all it takes; with `text=True` and no
    `errors=`, `subprocess` raises inside the decode and the step ends in a
    traceback with the release body never written.
    """
    if shutil.which("git") is None:  # pragma: no cover - git is present everywhere this runs
        pytest.skip("no git on this machine")
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo_with_changelog(repo, b"# Changelog\n\n## Unreleased\n- Caf\xe9 au lait is fixed.\n")
    monkeypatch.chdir(repo)

    out = tmp_path / "notes.md"
    assert rn.main(["--tag", "v0.9.0-Public", "--out", str(out)]) == 0
    written = out.read_text(encoding="utf-8")
    assert written.startswith("### New\n- Caf")
    assert "au lait is fixed." in written


def test_a_tag_whose_changelog_is_missing_at_the_previous_ref_uses_everything(
    tmp_path: Path,
) -> None:
    out = tmp_path / "notes.md"
    run = _git(
        {
            ("tag", "--list", "v*"): "v0.8.65-Public",
            ("show", "v0.8.70-Public:CHANGELOG.md"): OLD,
        }
    )
    assert rn.main(["--tag", "v0.8.70-Public", "--out", str(out)], run_git=run) == 0
    assert "Alpha can now do one." in out.read_text(encoding="utf-8")
