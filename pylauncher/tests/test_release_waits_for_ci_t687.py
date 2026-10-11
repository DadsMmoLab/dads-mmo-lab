"""A release is built only from a commit that passed CI in the same run (T687).

Measured on v0.9.16 (669eaaba): `release.yml` attached SHA256SUMS at 21:32:57Z
and Discord announced the release at 21:38Z, while `ci.yml`'s tests on that
SHA finished at 21:55Z. Nothing waited for them. Since T687 `release.yml` calls
the whole of `ci.yml` as a job and everything that can write the release needs
it; these tests hold that relationship, and the smaller pieces T687 added with
it (Windows/macOS smoke, least-privilege token, pinned tools).

PARSED, not text-pinned. The other workflow tests here pin step text because
step bodies are shell; what matters in this file is the job graph (`needs`,
`if`, `permissions`, `uses`), which is YAML structure, and pyyaml is already a
test dependency (requirements-dev.txt).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PYLAUNCHER = Path(__file__).resolve().parents[1]

CALL = "./.github/workflows/ci.yml"


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(workflow: dict[str, Any]) -> dict[str, Any]:
    """The trigger map. YAML 1.1 reads a bare `on:` key as the boolean True."""
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), "triggers are written as a map in this repository"
    return on


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _ancestors(jobs: dict[str, Any], name: str) -> set[str]:
    seen: set[str] = set()
    todo = _needs(jobs[name])
    while todo:
        job = todo.pop()
        if job not in seen:
            seen.add(job)
            todo.extend(_needs(jobs[job]))
    return seen


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps", []))


RELEASE = _load("release.yml")
JOBS: dict[str, Any] = RELEASE["jobs"]
CI = _load("ci.yml")


def _ci_jobs() -> list[str]:
    return [name for name, job in JOBS.items() if job.get("uses") == CALL]


def test_release_calls_the_ci_workflow_as_a_job() -> None:
    assert _ci_jobs() == ["ci"], "release.yml must call ci.yml exactly once, as job `ci`"


def test_ci_can_be_called() -> None:
    """Without `workflow_call` the `uses:` above is refused when the run starts."""
    assert "workflow_call" in _on(CI)


def test_the_build_waits_for_ci() -> None:
    assert "ci" in _needs(JOBS["build"])
    assert "tag-order" in _needs(JOBS["build"]), "T669's guard still gates the build"


def test_every_job_that_can_write_the_release_waits_for_ci() -> None:
    """Whatever holds `contents: write` or attaches to the release must need `ci`.

    Transitively: `needs` is what makes a job wait, and a job that needs the
    build waits for CI through it.
    """
    writers = [
        name
        for name, job in JOBS.items()
        if (job.get("permissions") or {}).get("contents") == "write"
        or any("action-gh-release" in str(step.get("uses", "")) for step in _steps(job))
    ]
    assert set(writers) == {"build", "notes-and-checksums"}, writers
    for name in writers:
        assert "ci" in _ancestors(JOBS, name), f"{name} can write the release without CI"


def test_the_final_job_does_not_run_after_a_red_ci() -> None:
    """`!cancelled()` starts it after a failed dependency, so it has to ask about CI.

    A red CI skips `build`; it does not cancel the run, and `!cancelled()` alone
    would still start the job that writes SHA256SUMS and the release body.
    """
    job = JOBS["notes-and-checksums"]
    assert "ci" in _needs(job), "`needs.ci` can only be read by a job that needs ci"
    condition = str(job.get("if", ""))
    assert "!cancelled()" in condition
    assert re.search(r"needs\.ci\.result\s*==\s*'success'", condition), condition


def test_nothing_skips_the_called_ci() -> None:
    """A job-level `if` in ci.yml could turn the gate into a green no-op under a call.

    A skipped job counts as success for `needs`. So no ci.yml job may carry an
    `if`, and the call itself carries none either.
    """
    for name, job in CI["jobs"].items():
        assert "if" not in job, f"ci.yml job {name} has an if: and can be skipped"
    assert "if" not in JOBS["ci"]


def test_the_called_ci_holds_a_read_only_token() -> None:
    assert JOBS["ci"].get("permissions") == {"contents": "read"}


def test_the_workflow_token_is_read_only_by_default() -> None:
    assert RELEASE["permissions"] == {"contents": "read"}


def test_the_called_ci_still_tests() -> None:
    """The gate is only as good as what it calls: the suite, the live suite, both OSes."""
    jobs = CI["jobs"]
    for name in ("lint-type-test", "integration", "platform-smoke"):
        assert name in jobs, f"ci.yml lost {name}"
    runs = [str(step.get("run", "")) for step in _steps(jobs["lint-type-test"])]
    assert any("python -m pytest" in run for run in runs)


def test_discord_announces_only_a_green_release_run() -> None:
    """The post follows the release, which follows CI - but only on `success`."""
    discord = _load("discord-merged.yml")
    run = _on(discord)["workflow_run"]
    assert run["workflows"] == ["release"]
    condition = str(discord["jobs"]["release"]["if"])
    assert "github.event.workflow_run.conclusion == 'success'" in condition


# -- Windows and macOS --------------------------------------------------------


SMOKE = CI["jobs"]["platform-smoke"]


def _smoke_files() -> list[str]:
    runs = [str(step.get("run", "")) for step in _steps(SMOKE) if "pytest" in str(step.get("run"))]
    assert len(runs) == 1, runs
    return re.findall(r"tests/\S+\.py", runs[0])


def test_the_smoke_runs_on_the_platforms_the_release_ships() -> None:
    assert SMOKE["runs-on"] == "${{ matrix.os }}"
    oses = set(SMOKE["strategy"]["matrix"]["os"])
    built = {entry["os"] for entry in JOBS["build"]["strategy"]["matrix"]["include"]}
    assert oses == built - {"ubuntu-22.04"}, (oses, built)


def test_the_smoke_runs_the_os_specific_tests() -> None:
    files = _smoke_files()
    for needed in (
        "tests/test_every_module_imports.py",
        "tests/test_platform.py",
        "tests/test_winacl.py",
        "tests/test_winjob.py",
    ):
        assert needed in files, f"{needed} is not in the smoke set"


def test_every_smoke_file_exists() -> None:
    """pytest exits 4 on a missing path, but a rename would turn the job red for a
    reason nobody reads as "the list is stale"; this names it."""
    for name in _smoke_files():
        assert (PYLAUNCHER / name).is_file(), f"{name} is in the smoke set and does not exist"


# -- tools the release runs while it can write --------------------------------


def _build_runs() -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(JOBS["build"]))


def test_appimagetool_is_a_fixed_release_checked_by_sha256() -> None:
    runs = _build_runs()
    urls = re.findall(r"https://\S*appimagetool\S*", runs)
    assert len(urls) == 1, urls
    assert "/continuous/" not in urls[0]
    assert re.search(r"[0-9a-f]{64}  appimagetool\" \| sha256sum -c -", runs)
    assert runs.index("sha256sum -c") < runs.index("chmod +x appimagetool")


def test_the_release_action_is_pinned_by_commit() -> None:
    uses = [
        str(step["uses"])
        for job in JOBS.values()
        for step in _steps(job)
        if "action-gh-release" in str(step.get("uses", ""))
    ]
    assert len(uses) == 3
    for use in uses:
        assert re.fullmatch(r"softprops/action-gh-release@[0-9a-f]{40}", use), use
