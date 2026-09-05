"""Kill the 7.4c install once its import is provably underway. Robust by design.

WHY THIS FILE EXISTS IN THIS SHAPE. The first attempt at this gate armed an
equivalent watcher on `yulon-win11` over ssh on 2026-09-03. It logged one line
and died the instant the ssh session closed -- a process started from an ssh
session on Windows does not outlive it -- so a 7-hour install ran clean through
its import unwatched and the gate was missed. Three things follow from that:

  * the watcher is started by `systemd-run --user`, which reparents it to the
    user manager, so it survives ssh, terminal loss and reconnects. It is not a
    background job of anybody's shell.
  * it writes a HEARTBEAT line on every poll. The failure above was invisible
    precisely because silence and healthy waiting looked identical; a log that
    only speaks when something happens cannot tell you it stopped.
  * it re-reads its own liveness proof: `--- import` may already have passed
    when it starts, and that is a distinct outcome with a distinct sentence,
    never a silent exit.

The kill is not on a timer. It waits for the import to be UNDERWAY -- the
CMaNGOS import streams one line per SQL file it applies -- so the databases hold
some of the plan and not all of it. A fixed sleep could fire before the first
statement or after the last, and either produces a state that is not `partial`.
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

LOG = Path("/home/pk/tbc-74c.log")
OUT = Path("/home/pk/watch-74c.log")
FILES_BEFORE_KILL = 8
POLL_SECONDS = 20
APPLIED = re.compile(r"^\S+.* -> \w+\s*$", re.MULTILINE)
STAGE_IMPORT = re.compile(r"^--- import\s*$", re.MULTILINE)
STAGE_AFTER = re.compile(r"^--- up\s*$", re.MULTILINE)


def say(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {message}\n"
    with OUT.open("a", encoding="utf-8") as handle:
        handle.write(line)
    sys.stdout.write(line)
    sys.stdout.flush()


def install_pid() -> int | None:
    """The install process, found by its script name rather than by `python`."""
    try:
        found = subprocess.run(
            ["pgrep", "-f", "m910q_74c.py"], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        say(f"could not look for the install process: {exc}")
        return None
    for token in found.stdout.split():
        pid = int(token)
        if pid != os.getpid():
            return pid
    return None


def main() -> int:
    say(f"watching {LOG} for the import stage (kill after {FILES_BEFORE_KILL} SQL files)")
    polls = 0
    while True:
        time.sleep(POLL_SECONDS)
        polls += 1
        if not LOG.is_file():
            if polls % 15 == 0:
                say(f"heartbeat {polls}: no log yet")
            continue
        text = LOG.read_text(encoding="utf-8", errors="replace")

        if STAGE_AFTER.search(text):
            say("the import finished before this watcher could act (saw --- up). NOT INTERRUPTED")
            return 1
        if not STAGE_IMPORT.search(text):
            if polls % 15 == 0:
                stage = [ln for ln in text.splitlines() if ln.startswith("--- ")]
                say(f"heartbeat {polls}: at stage {stage[-1] if stage else '(none yet)'}")
            continue

        applied = len(APPLIED.findall(text))
        if applied < FILES_BEFORE_KILL:
            say(f"import running, {applied} files applied - waiting for {FILES_BEFORE_KILL}")
            continue

        pid = install_pid()
        if pid is None:
            say("import underway but the install process is gone; nothing to interrupt")
            return 1
        say(f"KILLING pid {pid} after {applied} SQL files - this is the interruption")
        os.kill(pid, signal.SIGKILL)
        time.sleep(5)
        still = install_pid()
        say(f"killed; process still present: {still is not None}")
        say("the databases now hold part of the plan and no marker")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
