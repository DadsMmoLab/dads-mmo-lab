"""Tests for the WotLK console helper (`docker attach` over a pty) via a fake Popen.

The worldserver container runs with `tty: true`, so docker refuses to attach
unless its stdin is a terminal — the app therefore opens a pty and writes the
command to the master end (live-verified on Linux, 2026-08-21). Windows has no
pty, so `send_command()` refuses with an explanation instead of a raw docker
error; the behaviour tests below only run where a pty exists.
"""

from __future__ import annotations

import io
import os
import subprocess
from typing import Any

import pytest

from yulon.controller_wow_wotlk import console

needs_pty = pytest.mark.skipif(not console.pty_supported(), reason="no pty on this platform")


class _FakeProc:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.stdin = kwargs.get("stdin")
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


@needs_pty
def test_send_command_attaches_over_a_pty_and_collects_the_reply() -> None:
    made: list[_FakeProc] = []

    def popen(argv: list[str], **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(argv, **kwargs)
        made.append(proc)
        return proc

    reply = console.send_command(
        "account create dad pw",
        container="ac-worldserver",
        window=0.01,
        popen=popen,  # type: ignore[arg-type]
    )
    assert made[0].argv == ["docker", "attach", "--sig-proxy=false", "ac-worldserver"]
    # stdin is the pty's slave fd — a terminal, which is what docker demands.
    assert isinstance(made[0].stdin, int) and os.isatty(made[0].stdin) is False  # closed by now
    # The prompt and our own echo are not part of the answer.
    assert reply.lines == ("Account created: dad",)
    assert reply.command == "account create dad pw"


@pytest.mark.skipif(console.pty_supported(), reason="POSIX has a pty; this is the Windows path")
def test_send_command_explains_itself_where_there_is_no_pty() -> None:
    with pytest.raises(console.ConsoleError, match="needs a terminal"):
        console.send_command("server info", container="ac-worldserver")


def test_send_command_rejects_multiline_or_empty() -> None:
    with pytest.raises(ValueError):
        console.send_command("a\nb", popen=_FakeProc)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        console.send_command("   ", popen=_FakeProc)  # type: ignore[arg-type]


@needs_pty
def test_send_command_wraps_popen_failure() -> None:
    def boom(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        raise OSError("no docker")

    with pytest.raises(console.ConsoleError, match="no docker"):
        console.send_command("server info", popen=boom)  # type: ignore[arg-type]


def test_send_command_rejects_carriage_returns_too() -> None:
    """CR is a line control on the wire as much as LF - refuse both (review, 2026-08-21)."""
    with pytest.raises(ValueError):
        console.send_command("server info\rserver shutdown 1")
