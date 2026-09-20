"""release.yml carries the T90 pieces; text pins, because nothing here parses YAML.

`tests/test_linux_artifact_prereqs.py` is the precedent: the release job's steps
are shell bodies, and the repository has no YAML parser among its dependencies,
so what a step must keep saying is pinned as text. The pieces are the version
stamp (before PyInstaller reads the package), `SHA256SUMS` over every artifact
the release carries, and a body made from CHANGELOG.md.

EVERY PIN HERE READS CODE, NOT COMMENTS. This file's first version asserted
`"sha256sum" in job`, and the job's own comment says the word "sha256sum" -- so
the pin held with the command deleted. `_code()` strips comment lines once, and
nothing below reads the raw text.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

RELEASE_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
WORKFLOW = RELEASE_WORKFLOW.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """`text` with its whole-line comments removed.

    A `#` inside a shell line is left alone -- no step here writes one, and
    guessing where a comment starts mid-line needs a shell parser.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _job(name: str) -> str:
    """The text of one job, comments stripped, up to the next job header."""
    start = WORKFLOW.index(f"\n  {name}:")
    rest = WORKFLOW[start + 1 :]
    following = re.search(r"^  [a-z][a-z0-9-]*:$", rest[1:], re.M)
    return _code(rest if following is None else rest[: following.start() + 1])


def _steps(job: str) -> list[str]:
    """One string per step of a job, each starting at its own `- `."""
    marks = [m.start() for m in re.finditer(r"^      - ", job, re.M)]
    assert marks, "no steps found; the slicing is broken, not the workflow"
    bounds = marks + [len(job)]
    return [job[a:b] for a, b in zip(bounds[:-1], bounds[1:], strict=True)]


def _step_with(job: str, needle: str) -> str:
    """The one step of `job` whose text contains `needle`."""
    found = [step for step in _steps(job) if needle in step]
    assert len(found) == 1, f"{len(found)} steps contain {needle!r}, expected exactly one"
    return found[0]


NOTES_JOB = _job("notes-and-checksums")
BUILD_JOB = _job("build")
ATTACH = "softprops/action-gh-release@v2"


def test_the_version_is_stamped_before_pyinstaller_runs() -> None:
    code = _code(WORKFLOW)
    assert code.index("python build/stamp_version.py") < code.index(
        "run: pyinstaller build/pylauncher.spec"
    )


def test_a_final_job_waits_for_the_builds() -> None:
    assert "needs: build" in NOTES_JOB
    assert (
        "fetch-depth: 0" in NOTES_JOB
    ), "release_notes.py reads other tags; a shallow clone has none"
    assert "actions/download-artifact@v4" in NOTES_JOB
    assert "sha256sum" in NOTES_JOB and "SHA256SUMS" in NOTES_JOB
    assert "build/release_notes.py" in NOTES_JOB
    assert "body_path" in NOTES_JOB


def test_the_final_job_runs_when_one_platform_failed() -> None:
    assert (
        "!cancelled()" in NOTES_JOB
    ), "fail-fast is off: two good artifacts still deserve checksums"


def test_generated_notes_stay_as_the_fallback() -> None:
    assert "generate_release_notes: true" in _code(WORKFLOW)


def test_nothing_is_attached_when_no_artifact_was_built() -> None:
    """All three builds failing must not publish an empty `SHA256SUMS`.

    `!cancelled()` starts this job even when every build job failed, and a
    checksum file with no lines would read as "this release lists no artifact"
    rather than as "there is nothing here".

    Per step, not per job: the guard occurs three times, so counting it would
    stay green with it deleted from both steps that publish.
    """
    guard = "steps.sums.outputs.have == 'true'"
    for step in _steps(NOTES_JOB):
        if ATTACH in step or "actions/upload-artifact@v4" in step:
            assert guard in step, f"a step publishes without {guard}:\n{step}"


def test_a_dispatch_run_proves_the_job_without_publishing() -> None:
    """The job itself is not tag-gated; the two steps that publish are.

    A job that only ever runs on a tag can only ever be proved by publishing a
    release, which is the thing the `workflow_dispatch` trigger exists to avoid.
    """
    assert (
        "actions/upload-artifact@v4" in NOTES_JOB
    ), "a dispatch run must keep SHA256SUMS somewhere"
    tag_gate = "startsWith(github.ref, 'refs/tags/v')"
    attaching = [step for step in _steps(NOTES_JOB) if ATTACH in step]
    assert len(attaching) == 2
    for step in attaching:
        assert tag_gate in step, f"a publish step is not tag-gated:\n{step}"


def test_a_re_run_cannot_checksum_its_own_previous_answer() -> None:
    """`download-artifact` sees EVERY artifact of the run, this job's included.

    On "Re-run failed jobs" the first attempt's `SHA256SUMS` is still an
    artifact of the run, so an unfiltered `merge-multiple` download drops it
    back into the release directory and the new file lists the old file under
    the hash it had before - a line for a name no release carries.

    Three independent stops, because each one alone is a single point of
    failure: download only the build artifacts, delete any stale file before
    hashing, and never hash the name being written.
    """
    download = _step_with(NOTES_JOB, "actions/download-artifact@v4")
    pattern = re.search(r"pattern: (\S+)", download)
    assert pattern is not None, "an unfiltered download takes this job's own artifact back"

    upload = _step_with(NOTES_JOB, "actions/upload-artifact@v4")
    # From `with:` onwards, or the first `name:` found is the STEP's name. That
    # is not a hypothetical: written the short way, this assertion passed with
    # the artifact renamed to exactly the thing it exists to forbid, because
    # `fnmatch("Keep SHA256SUMS where...", "yulon-*")` is false for its own
    # reason. Found by mutating the workflow, not by reading the test.
    inputs = upload[upload.index("with:") :]
    uploaded = re.search(r"name: (\S+)", inputs)
    assert uploaded is not None
    assert not fnmatch(uploaded[1], pattern[1]), (
        f"this job uploads {uploaded[1]!r}, which its own download pattern "
        f"{pattern[1]!r} matches: a re-run would take it back"
    )

    sums = _step_with(NOTES_JOB, "sha256sum")
    assert "rm -f SHA256SUMS" in sums
    assert "! -name SHA256SUMS" in sums


def test_the_download_pattern_still_matches_every_artifact_the_builds_upload() -> None:
    """The other half of the pin above: a filter that filters everything out.

    `pattern:` is only safe while it still names the three build artifacts. They
    are uploaded as `yulon-${{ matrix.artifact }}`, so this reads the matrix.
    """
    download = _step_with(NOTES_JOB, "actions/download-artifact@v4")
    pattern = re.search(r"pattern: (\S+)", download)
    assert pattern is not None
    built = re.search(r"name: yulon-\$\{\{ matrix\.artifact \}\}", BUILD_JOB)
    assert built is not None, "the build job no longer names its artifacts after the matrix"
    kinds = re.findall(r"^\s+artifact: (\S+)$", BUILD_JOB, re.M)
    assert len(kinds) == 3, f"expected three build artifacts, found {kinds}"
    for kind in kinds:
        assert fnmatch(f"yulon-{kind}", pattern[1]), (
            f"the build job uploads yulon-{kind}, which {pattern[1]!r} does not match: "
            "the release would carry checksums for nothing"
        )


def test_a_second_attempt_can_replace_the_artifact_the_first_one_left() -> None:
    """`upload-artifact@v4` refuses a name the run already holds unless told."""
    upload = _step_with(NOTES_JOB, "actions/upload-artifact@v4")
    assert "overwrite: true" in upload


def test_this_job_cannot_turn_a_published_release_red() -> None:
    """The artifacts are attached by the build jobs; everything here is additive.

    A directory in the release folder, or a transient GitHub API error in either
    attach step, must not mark a release that is already published as failed.
    The app refuses to self-install an artifact `SHA256SUMS` does not list, so a
    missing checksum file fails safe on the other side too.
    """
    header = NOTES_JOB[: NOTES_JOB.index("    steps:")]
    assert "continue-on-error: true" in header


def test_the_build_job_asks_the_built_bundle_what_version_it_is() -> None:
    """The check that would have caught v0.8.69-fixtest, on every runner.

    That tag stamped correctly on all three and shipped an app reporting the
    fallback, with every job green: nothing in the workflow ever asked the
    artifact what it thought it was.
    """
    step = _step_with(BUILD_JOB, "--version")
    assert "build/stamp_version.py --derive" in step, "compare against the tag, not a literal"
    assert "RUNNER_OS" in step, "the Windows bundle is yulon.exe"
    # THE LAST BRANCH, not the step: the step holds two `::warning` lines - one
    # for a bundle that did not answer at all - and `"::warning" in step` stayed
    # green with the disagreement itself downgraded to a plain echo, which is
    # exactly the line that had to be loud. Found by mutating.
    disagreement = step[step.rindex("          else") :]
    assert "::warning" in disagreement, f"a mismatch must annotate the run:\n{disagreement}"
    assert "${built}" in disagreement and "${expected}" in disagreement, "name both versions"


def test_the_version_check_cannot_fail_a_tag() -> None:
    """The owner's rule: a tag never fails over its version.

    Three ways it could have: a non-zero exit from the step, `set -e` catching a
    bundle that will not start, and the job stopping on the step's own result.
    """
    step = _step_with(BUILD_JOB, "--version")
    assert "continue-on-error: true" in step
    assert "exit 1" not in step
    assert "set -euo" not in step, "-e would fail the build on a bundle that cannot start"


def test_the_version_is_checked_after_the_build_and_before_packaging() -> None:
    """A check that runs before PyInstaller has nothing to ask."""
    built = BUILD_JOB.index("run: pyinstaller build/pylauncher.spec")
    asked = BUILD_JOB.index('"$exe" --version')
    packaged = BUILD_JOB.index("name: Package AppImage")
    assert built < asked < packaged


def test_the_checksums_are_taken_over_regular_files_only() -> None:
    """`sha256sum -- *` exits 1 on a directory, and `set -e` would fail the job."""
    sums = _step_with(NOTES_JOB, "sha256sum")
    assert "-type f" in sums
    assert "sha256sum -- *" not in sums
