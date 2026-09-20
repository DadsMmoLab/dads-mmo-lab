"""release.yml carries the three T90 pieces; text pins, because nothing here parses YAML.

`tests/test_linux_artifact_prereqs.py` is the precedent: the release job's steps
are shell bodies, and the repository has no YAML parser among its dependencies,
so what a step must keep saying is pinned as text. The three pieces are the
version stamp (before PyInstaller reads the package), `SHA256SUMS` over every
artifact the release carries, and a body made from CHANGELOG.md.
"""

from __future__ import annotations

from pathlib import Path

RELEASE_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
WORKFLOW = RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_the_version_is_stamped_before_pyinstaller_runs() -> None:
    stamp = WORKFLOW.index("python build/stamp_version.py")
    assert stamp < WORKFLOW.index("run: pyinstaller build/pylauncher.spec")


def test_a_final_job_waits_for_the_builds() -> None:
    job = WORKFLOW[WORKFLOW.index("\n  notes-and-checksums:") :]
    assert "needs: build" in job
    assert "fetch-depth: 0" in job, "release_notes.py reads other tags; a shallow clone has none"
    assert "actions/download-artifact@v4" in job
    assert "sha256sum" in job and "SHA256SUMS" in job
    assert "build/release_notes.py" in job
    assert "body_path" in job


def test_the_final_job_runs_when_one_platform_failed() -> None:
    job = WORKFLOW[WORKFLOW.index("\n  notes-and-checksums:") :]
    assert "!cancelled()" in job, "fail-fast is off: two good artifacts still deserve checksums"


def test_generated_notes_stay_as_the_fallback() -> None:
    assert "generate_release_notes: true" in WORKFLOW


def test_nothing_is_attached_when_no_artifact_was_built() -> None:
    """All three builds failing must not publish an empty `SHA256SUMS`.

    `!cancelled()` starts this job even when every build job failed, and
    `sha256sum -- *` in an empty directory would otherwise write a file with no
    lines - which the app would read as "this release lists no artifact" rather
    than as "there is nothing here".
    """
    job = WORKFLOW[WORKFLOW.index("\n  notes-and-checksums:") :]
    assert "steps.sums.outputs.have == 'true'" in job


def test_a_dispatch_run_proves_the_job_without_publishing() -> None:
    """The job itself is not tag-gated; the two steps that publish are.

    A job that only ever runs on a tag can only ever be proved by publishing a
    release, which is the thing the `workflow_dispatch` trigger exists to avoid.
    """
    job = WORKFLOW[WORKFLOW.index("\n  notes-and-checksums:") :]
    assert "actions/upload-artifact@v4" in job, "a dispatch run must keep SHA256SUMS somewhere"
    attaches = job.count("softprops/action-gh-release@v2")
    assert attaches == job.count("startsWith(github.ref, 'refs/tags/v')") == 2
