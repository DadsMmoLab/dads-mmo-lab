"""CI cuts pytest into six slices per Python version; together they are the whole suite.

`ci.yml` runs `pytest --shard-id N --num-shards 6` for N in 0..5 (`tests/conftest.py`). The
split is only safe if every test lands in exactly one slice, in EVERY process of a run (under
xdist each worker collects and filters on its own), and if a run without the options is
untouched. Collected for real, in a subprocess, on a small file with many parametrized tests.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT.parent / ".github" / "workflows" / "ci.yml"
TARGET = "tests/test_slow_marker_t183.py"
SHARDS = 6


def _collect(*options: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", TARGET]
        + list(options),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line and " " not in line]


@pytest.fixture(scope="module")
def whole() -> list[str]:
    ids = _collect()
    assert len(ids) >= 3 * SHARDS, "the probe file is too small to tell a split from none"
    return ids


@pytest.fixture(scope="module")
def slices() -> list[list[str]]:
    return [_collect("--shard-id", str(n), "--num-shards", str(SHARDS)) for n in range(SHARDS)]


def test_the_six_shards_together_cover_every_test_exactly_once(
    whole: list[str], slices: list[list[str]]
) -> None:
    joined = [nodeid for part in slices for nodeid in part]
    assert sorted(joined) == sorted(whole)
    assert len(set(joined)) == len(joined), "a test is in two shards"


def test_every_shard_gets_some_tests_and_none_gets_most(
    whole: list[str], slices: list[list[str]]
) -> None:
    assert all(slices), "a shard that runs nothing would pass for free"
    assert max(len(part) for part in slices) < len(whole) * 0.6


def test_a_test_belongs_to_the_shard_its_node_id_hashes_to(slices: list[list[str]]) -> None:
    for number, part in enumerate(slices):
        for nodeid in part:
            assert zlib.crc32(nodeid.encode()) % SHARDS == number


def test_without_the_options_nothing_is_deselected(whole: list[str]) -> None:
    assert _collect("--shard-id", "0", "--num-shards", "1") == whole


def test_a_shard_is_the_same_under_xdist(whole: list[str], slices: list[list[str]]) -> None:
    """Each worker filters on its own; the controller reports what they agreed on."""
    assert sorted(_collect("-n", "2", "--shard-id", "1", "--num-shards", str(SHARDS))) == sorted(
        slices[1]
    )


def test_a_shard_outside_the_count_is_refused() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", TARGET, "--shard-id", "4"]
        + ["--num-shards", "4"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode != 0
    assert "--shard-id 4" in result.stdout + result.stderr


def test_ci_runs_exactly_the_shards_this_file_tests() -> None:
    """The matrix and the pytest line cannot drift from SHARDS without a red test."""
    ci = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job = ci["jobs"]["test"]
    assert job["strategy"]["matrix"]["shard"] == list(range(SHARDS))
    runs = [str(step.get("run", "")) for step in job["steps"]]
    sharded = [run for run in runs if "--shard-id" in run]
    assert len(sharded) == 1, runs
    expected = re.escape(f"--shard-id ${{{{ matrix.shard }}}} --num-shards {SHARDS}")
    assert re.search(rf"{expected}\b", sharded[0]), sharded[0]


def test_the_checker_job_runs_on_both_pythons_with_every_pass() -> None:
    """Before T868 mypy ran on 3.11 and 3.13; splitting the job must not drop either."""
    ci = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job = ci["jobs"]["lint-type"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.13"]
    runs = [str(step.get("run", "")) for step in job["steps"]]
    for command in (
        "python -m ruff check .",
        "python -m black --check .",
        "python -m mypy yulon main.py",
        "python -m mypy --platform win32 yulon main.py",
        "python -m mypy --platform darwin yulon main.py",
    ):
        assert command in runs, (command, runs)
