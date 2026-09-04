"""Drive the install harness over a real pty, answering its questions as a person would.

WHY A PTY AND NOT A HEREDOC. `install_wiring._terminal_prompter()` checks
`sys.stdin.isatty()` and DECLINES every question when stdin is not a terminal
(`install_wiring.py:182`), so a gate run over plain ssh answers "no" to the
docker-group consent and hands sudo an empty password -- which is a real code
path, but not the one 7.1's gate is about. Feeding a pty from a heredoc is the
other trap: the harness reads a line at a time and the writer is gone before the
second question arrives, so the run parks forever. So this driver keeps the pty
open for the whole run, watches what comes out, and writes an answer only when a
question it recognises has actually appeared.

WHAT IT ANSWERS, and nothing else. Two questions, both matched on their LAST
line rather than the whole paragraph, because both are multi-line and the tail
is the part that is stable:

  * `platform.DOCKER_GROUP_QUESTION` -- ends "(grants root-equivalent access)? (y/n): "
  * `platform.SUDO_PASSWORD_QUESTION` -- ends "(leave it empty to skip the steps that need it):"

Anything else that looks like a question is LOGGED AND LEFT ALONE. A driver that
guesses at an unrecognised prompt would answer a question nobody read, and the
transcript would not show that it had happened.

WHAT IT DOES NOT DO. It does not decide whether the gate passed. It writes a
faithful transcript and exits with the harness's own status; the criteria are
read off the transcript afterwards by a person or by a separate check. A driver
that graded itself would be the only witness to its own verdict.

WHAT IT RECORDS ABOUT THE BOX, once before the command and again after it. The
audit of the 7.1 Ubuntu gate (`7.1-ubuntu-2026-08-31/AUDIT-2026-09-04.md`,
section 1 clause 1) found "yulon-ubuntu clean checkpoint" UNFALSIFIABLE: the one
line offered for the "no Docker" half was the engine's `Docker is not answering
yet`, which is a daemon-REACHABILITY probe and reads identically for "Docker is
installed but this user is outside the docker group" -- which was in fact the
case. The strings `clean-ssh` and `checkpoint` occurred in none of that run's
four logs. So the driver now asks the box itself, before it can be changed, and
writes the answers into the same transcript:

  * `docker --version` -- the CLIENT binary printing its own version. It never
    contacts the daemon, which is what makes it separate "not installed" from
    "installed but unreachable" -- the distinction nobody could recover from the
    08-31 logs afterwards.
  * `systemctl is-active docker` -- whether the unit is running, which is a
    different question from whether this user can reach it.
  * `id -Gn` -- this process's supplementary groups: the docker-group question
    asked directly. It is also the list that does NOT change when the harness
    runs `usermod` mid-run (`platform.py:1348-1354`), so a before and an after
    that agree is the expected reading here, not a sign nothing happened.
  * `ls -ld ~/wowserver` -- whether an install is already sitting there. `-l`
    rather than the audit's bare `-d` because the directory's timestamp
    separates "left by an earlier press" from "made by this one". The path is
    fixed at the one the 7.1/7.2 briefs use; a run pointed elsewhere by
    `--server-dir` gets an answer about the wrong directory, and its own
    `Using <dir>` line is what covers that case.
  * `df -h ~` -- free disk. Free space falling across presses is what showed the
    audit (section 3) that four claimed "re-runs" were continuations of one
    accumulating install and not runs from one checkpoint.

A PROBE THAT FAILS IS A FINDING, NOT AN ERROR. A box with no `docker` executable
records `NOT INSTALLED`; a box with no systemd records the same about
`systemctl`; `ls` on an absent directory records its exit status and its own
message. Each of those is state worth having, and each is what a genuinely clean
checkpoint should look like. The price of a broken probe is one line in the
transcript saying it broke: every probe is caught, catch-all included, and the
driver's exit status stays the harness's, because a driver that died inside its
own bookkeeping would destroy the run it exists to record.

WHY THESE FIVE ARE READ-ONLY, and how they stay that way. Each is a query form:
`--version` prints and exits; `systemctl is-active` reports a unit's state and,
unlike `systemctl start`, does not enter it; `id`, `ls` and `df` read. The list
is a constant in this file, each entry an argv LIST run without a shell, so
nothing from the environment or the command line reaches it -- putting a
mutating probe in takes an edit here, where a reviewer sees it.

Every one of those lines goes through `note()`, so it carries the `[driver]`
prefix that neither the harness's output nor the installed app's ever has, and
each is tagged `state-before` or `state-after` so a later reader can grep out one
end of the run. A probe's own output is indented behind a `|` so the box's words
are never mistaken for the driver's.

RUN IT:
    python press-driver.py <log-path> -- <command to run under the pty...>

e.g.
    python press-driver.py ~/press1.log -- \
        /home/pk/venv/bin/python -m yulon.install_wiring wow-wotlk \
        --server-dir /home/pk/wow-server --installers-root /home/pk/checkout/pylauncher

The sudo password is read from the environment variable YULON_SUDO_PASSWORD so
it is never a command-line argument (an argv is world-readable in /proc). If it
is unset, the driver answers the sudo question with an empty line -- which is
the harness's own documented "skip the steps that need it" path, and it says so
in the transcript rather than pretending it typed something.
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

GROUP_TAIL = "(grants root-equivalent access)? (y/n):"
SUDO_TAIL = "(leave it empty to skip the steps that need it):"

# A prompt this driver does not know. Deliberately broad: it is used only to
# LOG, never to answer, so a false positive costs a line in the transcript and
# a false negative costs a hang somebody has to explain.
UNKNOWN_HINTS = ("(y/n)", "password", "[Y/n]", "[y/N]", "Enter ", "? ")

IDLE_REPORT_SECONDS = 300.0
"""How often to note that nothing has arrived. A compile is quiet for minutes at
a time, so silence is not failure -- but a transcript with a four-hour gap and
no explanation cannot be told apart from a hang after the fact."""

DEFAULT_SERVER_DIR = "~/wowserver"
"""Where to look for an install when the driven command does not say.

The path the 2026-08-31 gate used. It is only a fallback: `server_dir_of()`
prefers what the command itself was given, because a probe that describes a
directory the run does not touch is worse than no probe -- it reads like
evidence and is not.
"""


def server_dir_of(argv: Sequence[str]) -> str:
    """The `--server-dir` the driven command was given, or the default.

    Both spellings, because the harness accepts both and a gate script may use
    either: `--server-dir /path` and `--server-dir=/path`.

    This is the one probe target that cannot be a constant. The install
    directory is the caller's choice -- three spellings appear across this
    project's own gate records (`/home/pk/wowserver`, `~/wow-server-playerbots`,
    and the Windows gate's own gate/vanilla-server) -- so a hardcoded `~/wowserver` would have
    answered confidently about the wrong folder on two of the three, which is
    exactly the failure this whole capture exists to prevent (review,
    2026-09-04).
    """
    for i, word in enumerate(argv):
        if word == "--server-dir" and i + 1 < len(argv):
            return argv[i + 1]
        if word.startswith("--server-dir="):
            return word.split("=", 1)[1]
    return DEFAULT_SERVER_DIR


def state_probes(server_dir: str) -> tuple[list[str], ...]:
    """The five questions asked of the box, for this run's install directory.

    A function rather than a constant so the `ls` can name the directory this
    run will actually use. Everything else here is fixed: each entry is an argv
    LIST run without a shell, so nothing from the environment reaches it, and
    putting a mutating probe in takes an edit to this function where a reviewer
    sees it.
    """
    return (
        ["docker", "--version"],
        ["systemctl", "is-active", "docker"],
        ["id", "-Gn"],
        ["ls", "-ld", os.path.expanduser(server_dir)],
        ["df", "-h", os.path.expanduser("~")],
    )

STATE_PROBE_TIMEOUT = 20.0
"""Seconds any one probe gets. `systemctl is-active` talks to systemd over a
socket, and a wedged systemd is a state a gate box can genuinely be in; a probe
allowed to hang would cost the run it was only meant to describe."""

STATE_PROBE_MAX_LINES = 20
"""Lines of a probe's own output to keep. All five are short today -- `df -h ~`
is the longest at two lines -- but a bounded transcript is worth more than the
tail of something that unexpectedly prints a thousand."""


def capture_state(note: Callable[[str], None], when: str, server_dir: str) -> None:
    """Ask the box what state it is in and write the answers into the transcript.

    `when` is "before" or "after" and becomes the grep handle on every line the
    call produces. Never raises: see the module docstring on why a failed probe
    is a finding rather than an error.
    """
    probes = state_probes(server_dir)
    note(f"state-{when}: {len(probes)} read-only probes, install dir {server_dir}")
    for argv in probes:
        pretty = " ".join(argv)
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=STATE_PROBE_TIMEOUT,
            )
        except FileNotFoundError:
            # State, not trouble. "This box has no docker" is precisely the
            # sentence the 08-31 logs were unable to produce.
            note(f"state-{when}: {pretty}: NOT INSTALLED (no such executable)")
            continue
        except subprocess.TimeoutExpired:
            note(f"state-{when}: {pretty}: NO ANSWER within {STATE_PROBE_TIMEOUT:.0f}s")
            continue
        except Exception as exc:
            # Deliberately broad, and the reason is in the module docstring: the
            # transcript is worth more than any one probe, and the harness's
            # exit status must not become a fact about this bookkeeping.
            note(f"state-{when}: {pretty}: probe itself failed: {type(exc).__name__}: {exc}")
            continue

        note(f"state-{when}: {pretty}: exit {done.returncode}")
        # stderr is kept and not discarded: `ls` on an absent directory says what
        # it could not find only there, and that message is half the answer.
        output = (done.stdout + done.stderr).splitlines()
        for line in output[:STATE_PROBE_MAX_LINES]:
            note(f"state-{when}: {pretty}: | {line.rstrip()}")
        if len(output) > STATE_PROBE_MAX_LINES:
            dropped = len(output) - STATE_PROBE_MAX_LINES
            note(f"state-{when}: {pretty}: | ... {dropped} further lines not kept")
        if not output:
            note(f"state-{when}: {pretty}: | (said nothing)")


def main() -> int:
    if "--" not in sys.argv:
        sys.stderr.write(__doc__ or "")
        return 2
    split = sys.argv.index("--")
    log_path = sys.argv[1]
    argv = sys.argv[split + 1 :]
    password = os.environ.get("YULON_SUDO_PASSWORD")

    log = open(log_path, "ab", buffering=0)

    def note(message: str) -> None:
        """A line from the driver, marked so it is never mistaken for the harness's."""
        line = f"[driver] {message}\n".encode()
        log.write(line)
        sys.stdout.write(line.decode())
        sys.stdout.flush()

    server_dir = server_dir_of(argv)
    note(f"running: {' '.join(argv)}")
    note(f"sudo password: {'supplied via YULON_SUDO_PASSWORD' if password else 'NOT SET'}")

    # Before anything is spawned, so the transcript carries its own proof of
    # where the run started instead of leaning on a checkpoint name nobody can
    # check afterwards.
    capture_state(note, "before", server_dir)

    primary, secondary = pty.openpty()
    proc = subprocess.Popen(
        argv,
        stdin=secondary,
        stdout=secondary,
        stderr=secondary,
        close_fds=True,
    )
    os.close(secondary)

    pending = ""
    answered = {"group": 0, "sudo": 0}
    last_output = time.monotonic()
    last_note = time.monotonic()

    try:
        while True:
            ready, _, _ = select.select([primary], [], [], 5.0)
            now = time.monotonic()
            if ready:
                try:
                    chunk = os.read(primary, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                log.write(chunk)
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                last_output = now
                # Keep only the tail: a question is matched on its final line,
                # and holding the whole run in memory would grow without bound
                # on a build that prints a hundred thousand lines.
                pending = (pending + chunk.decode("utf-8", "replace"))[-4000:]

                if GROUP_TAIL in pending:
                    note("saw the docker-group consent question; answering y")
                    os.write(primary, b"y\n")
                    answered["group"] += 1
                    pending = ""
                elif SUDO_TAIL in pending:
                    if password:
                        note("saw the sudo password question; answering with the supplied password")
                        os.write(primary, password.encode() + b"\n")
                    else:
                        note(
                            "saw the sudo password question and have no password; answering EMPTY, "
                            "which is the harness's documented skip path"
                        )
                        os.write(primary, b"\n")
                    answered["sudo"] += 1
                    pending = ""
                else:
                    tail = pending.rsplit("\n", 1)[-1]
                    if tail.rstrip().endswith(":") or any(h in tail for h in UNKNOWN_HINTS):
                        if len(tail.strip()) > 3 and not tail.startswith("["):
                            note(f"UNRECOGNISED prompt, NOT answering: {tail.strip()[:200]!r}")
                            pending = ""
            else:
                if proc.poll() is not None:
                    break
                if now - last_note > IDLE_REPORT_SECONDS:
                    note(f"still running; {int(now - last_output)}s since the last output")
                    last_note = now
    finally:
        os.close(primary)

    status = proc.wait()
    note(f"exit status {status}; answered group x{answered['group']}, sudo x{answered['sudo']}")
    # The same probes again. A transcript that shows both ends says what the run
    # did to the box; one that shows neither end says only that it ran.
    capture_state(note, "after", server_dir)
    log.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
