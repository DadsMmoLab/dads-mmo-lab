"""Tests for the WotLK console helper (`docker attach` over a pty) via a fake Popen.

The worldserver container runs with `tty: true`, so docker refuses to attach
unless its stdin is a terminal — the app therefore opens a pty and writes the
command to the master end (live-verified on Linux, 2026-08-21). Windows has no
pty, so `send_command()` refuses with an explanation instead of a raw docker
error; the transport tests below only run where a pty exists.

The parsing tests do not, and their fixtures are byte-exact captures from the
real playerbots worldserver on yulon-ubuntu, 2026-08-23 — the live gate that
found what they pin (`console._reply_lines()`).
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
from typing import Any

import pytest

from yulon import runner
from yulon.controller_wow_wotlk import console

needs_pty = pytest.mark.skipif(not console.pty_supported(), reason="no pty on this platform")


class _FakeProc:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        # A real subprocess.Popen dups the slave fd into the child, so the
        # master stays writable after send_command() closes its own copy. Dupe
        # here too, or os.write(master) fails with EIO once the slave is gone.
        stdin = kwargs.get("stdin")
        # Record the tty-ness NOW, while the fd is still live: isatty() on a
        # dup'd/teardown fd is not reliable across platforms (differs on Linux
        # vs macOS once the master is closed).
        self.stdin_was_tty = os.isatty(stdin) if isinstance(stdin, int) else False
        self.stdin = os.dup(stdin) if isinstance(stdin, int) else stdin
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
    # It's a dup of the original slave, so it mirrors the child's fd; its
    # tty-ness was captured while the slave was still live.
    assert isinstance(made[0].stdin, int)
    assert made[0].stdin_was_tty is True
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
def test_send_command_wraps_popen_failure(caplog: pytest.LogCaptureFixture) -> None:
    """A spawn that fails says what the other three docker modules say — errno kept.

    This used to re-raise the OSError's own text. The path that actually
    produces it is a `docker.exe` the resolution cache pinned and something then
    removed — a Docker Desktop uninstall or in-place upgrade mid-session, which
    the cache cannot follow because it deliberately remembers a hit. "No such
    file" is not something a user can act on; the shared sentence is.

    The real error is logged at WARNING FIRST, which is the half worth pinning:
    without it a docker.exe blocked by an ACL or by AV would be reported as
    "install Docker Desktop" with nothing in the log to contradict it
    (review finding, 2026-08-23).
    """

    def boom(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        raise OSError("no docker")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(console.ConsoleError, match="Docker could not be found"):
            console.send_command("server info", popen=boom)  # type: ignore[arg-type]
    assert any("no docker" in r.getMessage() for r in caplog.records), (
        "the real error was swallowed; an ACL or AV block would be indistinguishable "
        "from Docker not being installed"
    )


# ------------------------------------------- cutting the answer out of the window
# Every byte string below was captured from the real playerbots worldserver on
# yulon-ubuntu, 2026-08-23 (1843 characters in world), through a spy on
# `_reply_lines()`. Before the fix these three windows returned 2, 5 and 1
# lines respectively where the true answers are 1, 1 and 1.


def _reply(stdout: bytes, command: str) -> tuple[str, ...]:
    """Parse `stdout` the way `send_command()`'s reader thread and parser do.

    Not routed through `send_command()`, deliberately: that path needs a pty and
    would skip on Windows, and these three cases are about how an answer is cut
    out of a window — behaviour that is identical on every platform and that
    nobody would ever watch fail if the tests only ran on Linux. The one line
    borrowed from the reader thread is its `rstrip`, so the fixtures can stay
    byte-exact captures rather than hand-typed line lists.
    """
    pumped = [raw.decode("utf-8", errors="replace").rstrip("\r\n") for raw in io.BytesIO(stdout)]
    return console._reply_lines(pumped, command)


def test_the_reply_ends_where_the_console_prints_its_prompt_again() -> None:
    """A busy server writes its own log into the same window; that is not the answer."""
    captured = (
        b"\x1b[0mgm list\r\n"
        b"\x1b[?2004l\r\x1b[?2004hAC> No gamemasters.\r\n"
        b"AC> \x1b[36m[mod-city-bots] resetting stale city duel for Lareth (guid 9000012)\r\n"
        b"\x1b[0m\x1b[36m[mod-city-bots] completed pending teleport for Caelvyn (guid 9000207)\r\n"
    )
    assert _reply(captured, "gm list") == ("No gamemasters.",)


def test_log_lines_that_arrived_before_the_command_are_not_its_reply() -> None:
    """Three of these five lines landed while docker was still attaching."""
    captured = (
        b"\x1b[0m\x1b[36m[mod-city-bots] all 5 legs refused for Jixlock toward poi 8\r\n"
        b"\x1b[0m\x1b[36m[mod-city-bots] no walkable path for Jixlock to poi 8\r\n"
        b"\x1b[0m\x1b[36m[mod-city-bots] completed pending teleport for Wesmere\r\n"
        b"\x1b[0mflurbleblarg\r\n"
        b"\x1b[?2004l\r\x1b[?2004hAC> Command 'flurbleblarg' does not exist\r\n"
        b"AC> \x1b[36m[mod-city-bots] completed pending teleport for Selion\r\n"
    )
    assert _reply(captured, "flurbleblarg") == ("Command 'flurbleblarg' does not exist",)


def test_a_window_with_no_prompt_hands_back_everything_it_saw() -> None:
    """Docker's own failure never reaches a console, so it never carries a prompt.

    Both lines were captured live: the first by pointing `send_command()` at a
    container that does not exist, the second by pointing it at the real
    worldserver right after `stop_staged()` brought it down — which is the case
    a user actually hits, by pressing Send with the server stopped. Cutting
    between prompts would find none and return nothing, turning the one line
    that explains the failure into silence.
    """
    missing = b"Error response from daemon: No such container: yulon-no-such-container\r\n"
    assert _reply(missing, "server info") == (
        "Error response from daemon: No such container: yulon-no-such-container",
    )
    stopped = b"cannot attach to a stopped container, start it first\r\n"
    assert _reply(stopped, "server info") == (
        "cannot attach to a stopped container, start it first",
    )


def test_send_command_rejects_carriage_returns_too() -> None:
    """CR is a line control on the wire as much as LF - refuse both (review, 2026-08-21)."""
    with pytest.raises(ValueError):
        console.send_command("server info\rserver shutdown 1")


# --------------------------------------------------------- naming the docker CLI
# The console is where an unresolved `docker` hurts most. Elsewhere it is a
# command that failed and can be retried; here it is account creation and every
# GM command silently dead, on a machine where Docker is installed and running,
# because this process started before Docker Desktop's installer wrote its PATH.

OFF_PATH_EXE = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


def test_attach_uses_the_cli_this_host_can_actually_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argv[0] is whatever `platform.docker_program()` resolved, not the literal name."""
    monkeypatch.setattr(console.platform, "_resolved_docker_cli", OFF_PATH_EXE)
    assert console.attach_argv("ac-worldserver") == [
        OFF_PATH_EXE,
        "attach",
        "--sig-proxy=false",
        "ac-worldserver",
    ]
    assert console.attach("ac-worldserver")[0] == OFF_PATH_EXE


def _no_docker_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console.platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(console.platform, "docker_programs", lambda: ("docker",))
    monkeypatch.setattr(console.platform, "_which", lambda name, path=None: None)


def test_no_docker_at_all_is_a_console_error_not_a_popen_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_docker_anywhere(monkeypatch)
    with pytest.raises(console.ConsoleError, match="Docker could not be found"):
        console.attach_argv("ac-worldserver")


def test_a_missing_cli_never_opens_a_pty_it_cannot_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The argv is resolved before the pty exists, or every attempt leaks two fds.

    `send_command()` opens the pty and then builds the argv inside a `try` that
    only catches `OSError`; a `ConsoleError` raised in there would escape past
    the `os.close()` pair. Resolving first is what makes that unreachable.

    `pty_supported` is faked rather than skipped, because the ordering is the
    same on both platforms and Windows is where a machine actually ends up with
    no resolvable docker.
    """
    _no_docker_anywhere(monkeypatch)
    monkeypatch.setattr(console, "pty_supported", lambda: True)
    opened: list[tuple[int, int]] = []

    def _watched_pty() -> tuple[int, int]:
        pair: tuple[int, int] = runner.open_pty()
        opened.append(pair)
        return pair

    monkeypatch.setattr(console, "_open_pty", _watched_pty)
    with pytest.raises(console.ConsoleError, match="Docker could not be found"):
        console.send_command("server info", container="ac-worldserver")
    assert opened == [], "a pty was opened for a command that could never run"
