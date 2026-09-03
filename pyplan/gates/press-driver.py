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

    note(f"running: {' '.join(argv)}")
    note(f"sudo password: {'supplied via YULON_SUDO_PASSWORD' if password else 'NOT SET'}")

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
    log.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
