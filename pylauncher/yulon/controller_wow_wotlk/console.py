"""Worldserver console access for WotLK: send a command over `docker attach`.

AzerothCore's GM commands (`server info`, `gm list`, `lookup item ...`) are
typed at the worldserver's console. The container keeps that console on its
stdin, so `docker attach` reaches it; `--sig-proxy=false` makes sure detaching
never forwards a signal into the worldserver (the guide's "never press Ctrl+C"
rule, enforced by the transport instead of the user). `send_command()`
attaches, writes ONE command, listens for a fixed window, detaches, and cuts
that command's answer out of the window using the console's prompt. It does
NOT stop early at the prompt — nothing here reads the stream while it is
arriving, so every command costs the full window (review, 2026-08-23: the
header used to say "reads until the console prints its prompt again", which
`send_command()`'s own docstring already contradicted correctly). The app no
longer creates accounts through here — that is `accounts.py`'s SRP6 path, which
needs no pty and works on Windows — though `tests/integration/test_accounts_live`
still drives `account create` this way on purpose, because that gate needs the
SERVER to write the row.

**Live-gated on the Ubuntu VM against a real AzerothCore playerbots install,
2026-08-23** (1843 characters in world, 1845 bots, ~40 attach/detach cycles):
the worldserver kept the same PID and `RestartCount` 0 throughout, `server
info` / `server motd` / `gm list` / `lookup item` / `account onlinelist` each
answered, a rejected command came back as `Command 'flurbleblarg' does not
exist` rather than as silence or an exception, and `docker.follow_logs()` still
streamed after every detach. What the gate broke is recorded on
`_parse_reply()`: a fixed time window on a busy server does not delimit an
answer, and this one was handing back several times more log noise than reply.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass

from yulon import platform, runner
from yulon.controller_wow_wotlk import docker_ctl
from yulon.log import get_logger

logger = get_logger(__name__)

_ATTACH_SETTLE_SECONDS = 0.6
"""How long docker gets to attach before the command is written.

Kept, but no longer believed to be load-bearing. Measured on the Ubuntu VM
against the live playerbots worldserver, 2026-08-23: settle values of 0.0,
0.05, 0.2, 0.6 and 1.5 s each answered `gm list` 4 times out of 4. A write to
the pty master sits in the terminal's buffer until something reads the slave,
so being early costs nothing — which is why 0.0 s works. The sleep is a margin
against a slower host and against `docker attach` printing an error before it
ever reaches the console, not a fix for a race that was observed here.
"""

_PROMPT = "AC>"
"""What AzerothCore's console prints when it is ready for the next command.

Printed twice per command, which is what makes it a delimiter and not just
noise to strip: once after the console reads the line (immediately in front of
the first line of the answer) and once after the command finishes. See
`_parse_reply()`.

That "twice" is a single-writer property, not a property of the console. It is
readline redisplaying the line it just read and then prompting for the next
one, with exactly one client feeding it. `docker attach` allows several clients
on one tty container at the same time, and this module hands users a second
one: `NO_TTY_HELP` tells every Windows user to run `docker attach` in a
terminal, and `attach()` exists to build that argv for them. A human typing at
the console while the app sends a command puts foreign prompts AND foreign
echoes inside the app's window, which is the general case of the off-by-one
`_parse_reply()`'s echo anchor exists to survive (review, 2026-08-23).
"""

_DEFAULT_WINDOW_SECONDS = 3.0


class ConsoleError(RuntimeError):
    """`docker attach` could not be started (daemon down, container missing, ...)."""


@dataclass(frozen=True)
class ConsoleReply:
    """One command's answer, cut out of everything the console printed.

    `lines` is what the console printed between its prompt and its next prompt
    — not everything that arrived in the reply window. On a server with
    playerbots running, those are very different things; see `_parse_reply()`.
    """

    command: str
    lines: tuple[str, ...]
    prompted: bool = True
    """Did the console's prompt appear in the window at all?

    False means nothing here was delimited, so `lines` is the raw window rather
    than an answer. Two very different situations produce it and the transport
    cannot tell them apart: `docker attach` failing before it ever reaches a
    console (`No such container`, `cannot attach to a stopped container`), and a
    worldserver that is up but still loading maps — AzerothCore prints no `AC> `
    until the world is ready, which takes minutes. The Console tab's Send button
    is live throughout that, so the second case is not an edge (review,
    2026-08-23). Carried out of the parser rather than inferred by the caller so
    the UI can say "no prompt was seen" instead of presenting a startup log as
    an answer.
    """


# Both live in `yulon.runner` now: the installer needs a terminal too (sudo
# reads its password from /dev/tty), and a helper two features share does not
# belong in one game's package (style-guide §4). Re-exported so this module's
# own callers and tests keep reading naturally.
pty_supported = runner.pty_supported
_open_pty = runner.open_pty


NO_TTY_HELP = (
    "The worldserver console needs a terminal. Docker refuses `docker attach` "
    "when its input is not a TTY, and this platform has no pseudo-terminal, so "
    "the app cannot send console commands here yet. Use the worldserver console "
    "in a terminal for now: `docker attach --sig-proxy=false {container}` "
    "(Ctrl+P then Ctrl+Q to leave it running)."
)


DETACH_KEYS = "ctrl-p,ctrl-q"
"""Docker's default detach sequence, pinned rather than assumed.

The in-distro transport DEPENDS on it: that path leaves the console by typing
these keys, so a `detachKeys` set in the distro's own ~/.docker/config.json
would otherwise mean the keys we send are not the keys that detach, and the
fallback below is `kill()` - which stops the container.
"""

DETACH_SEQUENCE = b""
"""Ctrl+P, Ctrl+Q as bytes: what `DETACH_KEYS` spells on the wire."""

_DETACH_GRACE_SECONDS = 5.0
"""How long the client gets to leave on its own before it is killed.

Measured 2026-08-27: a real client answered the keys within a second
(`read escape sequence`, exit 1). The margin is for a loaded host, not for a
client that is going to ignore them.
"""

NO_SCRIPT_HELP = (
    "The worldserver console needs `script` inside {distro}, and there is none. "
    "It comes with util-linux (`sudo apt install bsdutils` on Debian/Ubuntu, "
    "`sudo pacman -S util-linux` on Arch). Until then use the console in a "
    "terminal: `wsl -d {distro} -- docker attach --sig-proxy=false {container}` "
    "(Ctrl+P then Ctrl+Q to leave it running)."
)


def can_send(wsl_distro: str | None = None) -> bool:
    """Can this host type at that server's console?

    Two ways to satisfy docker's "stdin must be a terminal": open a pty here
    (POSIX), or have the distro open one (`distro_attach_argv()`). The second
    is why this is not simply `pty_supported()` — a Windows box managing a
    WSL-resident server has no pty of its own and can still send.
    """
    return wsl_distro is not None or pty_supported()


def distro_attach_argv(container: str, wsl_distro: str) -> list[str]:
    """`docker attach`, with the pseudo-terminal allocated INSIDE the distro.

    Windows cannot hand docker a terminal, so it borrows the distro's:
    `script(1)` opens a pty there, runs `docker attach` on it, and relays this
    process's ordinary pipe through it. Measured 2026-08-27 against a tty
    container, from Windows: the bare `wsl -d D -- docker attach` answers
    "cannot attach stdin to a TTY-enabled container because stdin is not a
    terminal", and this argv answers the command.

    `/dev/null` is `script`'s transcript file — the typescript is not wanted,
    only the terminal it has to create in order to write one.

    The container name is `shlex.quote`d because it lands inside a string that
    the distro's shell parses, which is a different place than the argv list
    every other docker call in this app builds.

    Raises:
        ConsoleError: no wsl.exe on this host, so the distro cannot be reached
            at all — the same sentence `attach_argv()` raises, for the same
            reason (it is `docker_prefix()`'s None, one layer down).
    """
    prefix = platform.wsl_prefix(wsl_distro)
    if prefix is None:
        raise ConsoleError(platform.DOCKER_CLI_MISSING_HELP)
    inner = " ".join(
        shlex.quote(part)
        for part in (
            "docker",
            "attach",
            "--sig-proxy=false",
            f"--detach-keys={DETACH_KEYS}",
            container,
        )
    )
    return [*prefix, "script", "-qec", inner, "/dev/null"]


def attach_argv(container: str, *, wsl_distro: str | None = None) -> list[str]:
    """The exact `docker attach` invocation used (pinned by tests).

    argv[0] comes from `platform.docker_prefix()`, not the literal `docker`, and this
    is the site where getting that wrong is worst. Everywhere else an
    unresolved CLI is a command that failed and can be retried; here it is the
    GM console — account creation, `.server info`, every gameplay command the
    app offers — coming up dead on a machine where Docker is installed and
    running, because this process was started before the installer wrote its
    PATH entry (see `platform.docker_program()`).

    Raises:
        ConsoleError: this host has no docker CLI at all. Raised rather than
            returned as an unusable argv so `send_command()` fails before it
            opens a pty, and so the user gets `DOCKER_CLI_MISSING_HELP` instead
            of a `FileNotFoundError` from `Popen`.
    """
    prefix = platform.docker_prefix(wsl_distro)
    if prefix is None:
        raise ConsoleError(platform.DOCKER_CLI_MISSING_HELP)
    return [*prefix, "attach", "--sig-proxy=false", container]


def send_command(
    command: str,
    *,
    container: str = docker_ctl.SPEC.world,
    wsl_distro: str | None = None,
    window: float = _DEFAULT_WINDOW_SECONDS,
    prompt: str = _PROMPT,
    prompt_precedes_answer: bool = True,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
) -> ConsoleReply:
    """Send one console line to the worldserver and return that command's answer.

    `window` bounds how long the console is listened to, not what counts as the
    reply — `_parse_reply()` cuts the answer out of the window using the
    console's own prompt. A command whose output outlives the window is
    truncated; nothing waits for it, because a detached attach client cannot ask
    the console whether it has finished.

    The detach is the part with teeth, and it was the point of the live gate.
    Measured against the real playerbots worldserver on 2026-08-23: ~40
    attach/detach cycles left the container's `State.Pid` at 69960,
    `RestartCount` at 0 and `StartedAt` unchanged, `docker logs -f` still
    streamed afterwards, and no `docker attach` client was left behind.

    Raises:
        ConsoleError: no pty on this platform, no docker CLI, or the pty could
            not be written to. A command the *server* rejects is not an error
            here — AzerothCore answers it (`Command 'x' does not exist`, or the
            subcommand usage), and that answer is returned like any other.
        ValueError: `command` is empty or carries more than one line.
    """
    if any(ch in command.strip("\n") for ch in ("\n", "\r")) or not command.strip():
        raise ValueError("send_command() takes exactly one non-empty command line")
    logger.info(f"console → {container}: {command.split(' ', 2)[0:2]}")  # never log passwords
    if not can_send(wsl_distro):
        raise ConsoleError(NO_TTY_HELP.format(container=container))
    if wsl_distro is not None:
        # A pty this process cannot open, opened where it can be: inside the
        # distro. Everything after the transport — the window, the prompt, the
        # parse — is shared with the pty path below.
        raw = _send_inside_distro(
            command, container=container, wsl_distro=wsl_distro, window=window, popen=popen
        )
        return _parse_reply(
            raw, command, prompt=prompt, prompt_precedes_answer=prompt_precedes_answer
        )
    # Resolve the CLI BEFORE the pty exists. `attach_argv()` can raise, and the
    # `except OSError` below would not catch a ConsoleError — the master/slave
    # pair would leak one fd per attempt.
    argv = attach_argv(container, wsl_distro=wsl_distro)
    # The worldserver container runs with tty=true, so docker REFUSES to attach
    # unless its stdin is a terminal ("the input device is not a TTY"). Writing
    # to /proc/1/fd/0 does not help either: that fd is the terminal, so a write
    # only prints text, it never reaches the console's input. A real pty does
    # (live-verified on the Ubuntu VM, 2026-08-21).
    master, slave = _open_pty()
    try:
        proc = popen(
            argv,
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=runner.child_env(),
            creationflags=runner.creationflags(),
        )
    except OSError as exc:
        os.close(master)
        os.close(slave)
        # One of the modules that has to say this; `DOCKER_CLI_MISSING_HELP`'s
        # own docstring names them all. Deliberately not "the Nth" any more — it
        # said "the fourth" until a fifth module started raising it and nothing
        # noticed, which is what a positional count in a comment is for.
        #
        # `attach_argv()` above covers the never-resolved
        # case; this covers the pinned-then-removed one, where argv[0] is an
        # absolute path that has since gone (a Docker Desktop uninstall or in-place
        # upgrade mid-session), which used to surface as a bare [Errno 2].
        # Logged with the real errno first, the way `docker._docker()` does, so a
        # docker.exe blocked by an ACL or by AV leaves evidence instead of being
        # reported to the user as "install Docker Desktop" with nothing in the log
        # to contradict it (review finding, 2026-08-23).
        logger.warning(f"{argv[0]} could not be started: {exc}")
        raise ConsoleError(platform.DOCKER_CLI_MISSING_HELP) from exc
    os.close(slave)  # the child holds its own copy
    out, reader = _pump(proc)
    # Give docker a moment to attach, or the first command is written into the
    # void before the console is listening.
    time.sleep(_ATTACH_SETTLE_SECONDS)
    try:
        os.write(master, (command.strip("\n") + "\n").encode("utf-8"))
    except OSError as exc:
        _close_console(proc, master, reader)
        raise ConsoleError(f"could not write to the worldserver console: {exc}") from exc
    time.sleep(window)
    # Detach without stopping the server. `docker attach` ignores SIGTERM here,
    # so kill our client outright — the container is untouched either way. Both
    # halves were measured on the Ubuntu VM, 2026-08-23: an attach client sent
    # SIGTERM was still alive two seconds later (`poll()` None) and only exited
    # on SIGKILL, and the worldserver kept its PID through every cycle.
    _close_console(proc, master, reader)
    # Copied, not parsed in place: `_close_console()` joins the reader with a
    # timeout, and on a timeout that thread is still appending to `out`. A list
    # this module's docstrings make precise claims about should be frozen before
    # it is read (review, 2026-08-23).
    return _parse_reply(
        list(out), command, prompt=prompt, prompt_precedes_answer=prompt_precedes_answer
    )


def _pump(proc: subprocess.Popen[bytes]) -> tuple[list[str], threading.Thread]:
    """Drain the client's output into a list on a daemon thread.

    Shared by both transports: nothing here reads the stream while it is
    arriving, so the window is a clock either way (see `_parse_reply()`).
    """
    assert proc.stdout is not None
    out: list[str] = []

    def _drain() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            out.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    return out, reader


def _send_inside_distro(
    command: str,
    *,
    container: str,
    wsl_distro: str,
    window: float,
    popen: type[subprocess.Popen[bytes]],
) -> list[str]:
    """One command through a pty the distro opens; returns the raw window.

    Stdin here is an ordinary pipe, not a pty — `script(1)` on the other side
    of wsl.exe is what makes docker see a terminal, and it relays this pipe
    into it. That is the whole reason this path works on a host with no
    `os.openpty()`.
    """
    argv = distro_attach_argv(container, wsl_distro)
    try:
        proc = popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=runner.child_env(),
            creationflags=runner.creationflags(),
        )
    except OSError as exc:
        logger.warning(f"{argv[0]} could not be started: {exc}")
        raise ConsoleError(platform.DOCKER_CLI_MISSING_HELP) from exc
    out, reader = _pump(proc)
    time.sleep(_ATTACH_SETTLE_SECONDS)
    try:
        _write(proc, (command.strip("\n") + "\n").encode("utf-8"))
    except OSError as exc:
        _detach_console(proc, reader)
        raise ConsoleError(f"could not write to the worldserver console: {exc}") from exc
    time.sleep(window)
    _detach_console(proc, reader)
    lines = list(out)
    # The one failure this transport has that the pty path does not: a distro
    # with no util-linux. Recognised rather than handed back as a reply,
    # because "script: command not found" in the console panel reads as the
    # SERVER refusing something.
    if any("script" in line and "not found" in line for line in lines):
        raise ConsoleError(NO_SCRIPT_HELP.format(distro=wsl_distro, container=container))
    return lines


def _write(proc: subprocess.Popen[bytes], data: bytes) -> None:
    """Write to the client's stdin pipe and flush it (raises `OSError`)."""
    assert proc.stdin is not None
    proc.stdin.write(data)
    proc.stdin.flush()


def _detach_console(proc: subprocess.Popen[bytes], reader: threading.Thread) -> None:
    """Leave the console by typing docker's detach keys — never by killing it.

    This is the opposite of `_close_console()` below, and the difference was
    measured rather than reasoned about (2026-08-27, Windows → WSL, against a
    container whose PID 1 reads its tty): killing the client stopped the
    container, even on a run that had written nothing to it at all, while the
    detach keys left it running on the same PID. The same kill is harmless on
    Linux, where the pty path still uses it — which is exactly why this had to
    be measured on the platform it ships to rather than reasoned from the one
    it was written on.

    `kill()` stays as the fallback, because leaving a client attached is not
    the safer option it looks like: a second client on the same tty puts
    foreign prompts and echoes inside the next command's window (see
    `_PROMPT`). The warning names the risk that is then being taken.
    """
    if proc.poll() is None:
        try:
            _write(proc, DETACH_SEQUENCE)
        except OSError as exc:  # pragma: no cover - the pipe is already gone
            logger.warning(f"the detach keys could not be sent: {exc}")
    try:
        proc.wait(timeout=_DETACH_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "the console client did not detach; killing it, which through wsl.exe has been "
            "measured to stop the container as well"
        )
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - kill() not honoured
            logger.warning("docker attach did not exit after kill()")
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:  # pragma: no cover - already closed
            pass
    reader.join(timeout=2)


def _close_console(proc: subprocess.Popen[bytes], master: int, reader: threading.Thread) -> None:
    """Kill the attach client, close the pty and join the reader (never raises)."""
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - kill() not honoured
        logger.warning("docker attach did not exit after kill()")
    try:
        os.close(master)
    except OSError:  # pragma: no cover - already closed
        pass
    reader.join(timeout=2)


def _parse_reply(
    raw: list[str],
    command: str,
    *,
    prompt: str = _PROMPT,
    prompt_precedes_answer: bool = True,
) -> ConsoleReply:
    """This command's answer: the lines between the console's prompt and its next one.

    The window is a clock, and a clock does not know when an answer ends. This
    used to return every line that arrived inside it, ANSI stripped and with our
    own echo dropped, which reads correctly on an idle server and falls apart on
    a populated one — the worldserver prints its own log to the same console.
    Measured against the real playerbots install on 2026-08-23, in a 3-second
    window: `gm list` came back as its one real line plus one unrelated
    `[mod-city-bots]` line; `flurbleblarg` as one real line plus **four** noise
    lines, three of which had arrived *before* the command was even sent;
    `server info`'s nine lines arrived under six to forty-four lines of bot
    logins. The Console tab then printed all of it a second time, because the
    same panel is streaming `docker logs -f` alongside.

    The delimiter is the console's own prompt, which the same measurement showed
    is printed exactly twice per command and never in between — verified across
    eight commands including `account onlinelist`, whose 1849-line window had
    its two prompts at index 1 and index 1848. So the answer is everything after
    the FIRST prompt and before the SECOND: leading noise falls before the
    first, trailing noise after the second, and neither is claimed as a reply.

    Counted from our own echo, not from the start of the window, and that is the
    whole difference between this working and destroying the answer. The console
    closes a command with `AC> ` and no newline, so whatever it prints next
    continues that same physical line; a window that opens on such a line starts
    with a STALE prompt that belongs to somebody else's command. Counting from
    the top then shifts every index by one and the reply comes back either empty
    (the user sees only their own echo) or as a single unrelated bot-log line
    presented as the answer to `server info` — worse than the unbounded window
    this replaced, which at least still contained the answer. Both shapes were
    reproduced against this function. The echo is the one line in the window we
    know the console printed for US, so the first line equal to the command
    resets the count and drops anything collected before it. Reachable three
    ways: an answer that outlives the window so its closing prompt lands after
    the detach (measured at the edge — `reload config` closed at index 163 of
    164), two overlapping sends, and a second `docker attach` client, which
    `_PROMPT` explains (review, 2026-08-23).

    A window with no prompt at all is not parsed this way, because that is the
    shape of a failure rather than of an answer: `docker attach` against a
    missing container prints `Error response from daemon: No such container:
    ...` and never reaches a console. Handing back nothing there would turn the
    one message that explains the failure into silence, so an unprompted window
    returns everything it saw, as before — flagged `prompted=False` so the
    caller can say so rather than pass a not-yet-ready worldserver's startup log
    off as a reply. See `ConsoleReply.prompted`.

    What it still cannot do, twice over. Async output that lands *between* the
    two prompts is inside the answer and stays there; that is much rarer than
    the head and tail cases — those are the whole idle stretch of the window —
    and separating it would need the worldserver to mark its own log lines,
    which it does not. And the closing prompt is only recognised at the START of
    a line: if anything ever reaches the tty without a trailing newline just
    before it, the prompt glues to the end of that line, the count stays at 1
    and every later line in the window is claimed as answer — the pre-fix
    defect, silently. Searching for `AC>` anywhere in the line would catch that
    and was deliberately not done: a false prompt destroys the answer outright
    (the paragraph above), a missed one only over-reports, and the console
    carries player and mod text that can contain the string. Not observed live.
    """
    sent = command.strip()
    # How many prompts have gone by while the answer is arriving. One for a
    # readline console (AzerothCore), which redisplays the prompt in FRONT of
    # what it is about to print; zero for an `fgets` console (CMaNGOS and
    # tortoise), which prints its prompt only from `commandFinished()` - after
    # the answer. Same window, same delimiter, the answer on the other side of
    # it. Setting only the string would have made every CMaNGOS reply an empty
    # tuple flagged `prompted=True` (research, 2026-08-26).
    wanted = 1 if prompt_precedes_answer else 0
    answer: list[str] = []
    everything: list[str] = []
    prompts = 0
    anchored = False
    for line in raw:
        text = runner.strip_ansi(line).replace("\x1b", "").strip()
        while text.startswith(prompt):
            prompts += 1
            text = text[len(prompt) :].lstrip()
        if not text:
            continue
        if text == sent and not anchored:
            anchored = True
            prompts = 0
            answer.clear()
            continue
        if text == sent:
            continue
        everything.append(text)
        if prompts == wanted:
            answer.append(text)
    return ConsoleReply(
        command=command,
        lines=tuple(answer) if prompts else tuple(everything),
        prompted=bool(prompts),
    )


def attach(container: str = docker_ctl.SPEC.world) -> list[str]:
    """The interactive attach argv for a terminal; Ctrl+P, Ctrl+Q detaches.

    No `--detach-keys` is passed, so that sequence is docker's default rather
    than something this argv pins — a user who has set `detachKeys` in
    `~/.docker/config.json` has their own. `send_command()` does not depend on
    it either way: it detaches by killing its own client.
    """
    return attach_argv(container)
