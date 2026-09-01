"""Tests for `tests/support_bash.py` — the skip helper the shell-driving tests share.

The property under test is that the probe RUNS something. `shutil.which("bash")`
alone answers True on a Windows box where `bash.exe` is the Store alias for WSL
and every invocation dies with `execvpe(/bin/bash)` (Windows test VM, measured
2026-08-21). A probe that trusted PATH there skipped nothing and the shell tests
failed instead of skipping.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests import support_bash
from tests.support_bash import bash_available


def test_bash_available_probes_that_bash_actually_runs() -> None:
    """`which bash` is not enough — the probe must execute something."""
    calls: list[list[str]] = []

    def ok(argv: list[str], *a: object, **k: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def broken(argv: list[str], *a: object, **k: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "execvpe(/bin/bash) failed")

    if shutil.which("bash") is None:
        assert bash_available(ok) is False  # nothing to probe
        return
    assert bash_available(ok) is True
    assert calls[-1] == ["bash", "-c", "exit 0"]
    assert bash_available(broken) is False


def test_bash_available_is_false_when_nothing_is_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `bash` on PATH is the whole answer: the probe must not try to run one."""

    def never(argv: list[str], *a: object, **k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"the probe ran {argv!r} with no bash on PATH")

    monkeypatch.setattr(support_bash.shutil, "which", lambda _name: None)
    assert bash_available(never) is False


def test_bash_available_is_false_when_the_probe_cannot_be_launched() -> None:
    """A `bash` that fails to exec raises rather than returning — still "no usable bash"."""
    if shutil.which("bash") is None:
        pytest.skip("no bash on PATH, so the launch never happens")

    def unlaunchable(argv: list[str], *a: object, **k: object) -> subprocess.CompletedProcess[str]:
        raise OSError("[WinError 2] The system cannot find the file specified")

    assert bash_available(unlaunchable) is False
