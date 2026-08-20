"""Tests for the WotLK console helper (`docker attach` transport) via a fake Popen."""

from __future__ import annotations

import io
import subprocess
from typing import Any

import pytest

from yulon.controller_wow_wotlk import console


class _FakeProc:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"AC> \r\nAccount created: dad\r\n")
        self._rc: int | None = None

    def terminate(self) -> None:
        self._rc = 0

    def kill(self) -> None:
        self._rc = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._rc if self._rc is not None else 0

    def poll(self) -> int | None:
        return self._rc


def test_send_command_attaches_writes_once_and_collects_output() -> None:
    made: list[_FakeProc] = []

    def popen(argv: list[str], **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(argv, **kwargs)
        made.append(proc)
        return proc

    reply = console.send_command(
        "account create dad pw", container="ac-worldserver", window=0.01, popen=popen  # type: ignore[arg-type]
    )
    assert made[0].argv == ["docker", "attach", "--sig-proxy=false", "ac-worldserver"]
    assert made[0].stdin.getvalue() == b"account create dad pw\n"
    assert reply.lines == ("AC> ", "Account created: dad")
    assert reply.command == "account create dad pw"


def test_send_command_rejects_multiline_or_empty() -> None:
    with pytest.raises(ValueError):
        console.send_command("a\nb", popen=_FakeProc)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        console.send_command("   ", popen=_FakeProc)  # type: ignore[arg-type]


def test_send_command_wraps_popen_failure() -> None:
    def boom(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        raise OSError("no docker")

    with pytest.raises(console.ConsoleError, match="no docker"):
        console.send_command("server info", popen=boom)  # type: ignore[arg-type]


def test_send_command_rejects_carriage_returns_too() -> None:
    """CR is a line control on the wire as much as LF - refuse both (review, 2026-08-21)."""
    with pytest.raises(ValueError):
        console.send_command("server info\rserver shutdown 1")
