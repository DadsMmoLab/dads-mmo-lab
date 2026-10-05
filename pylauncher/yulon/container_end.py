"""Ending a container this app started under a name of its own, after a Stop (T240, T303).

On Docker Desktop a Stop that ends the docker CLI does not end the container it
was attached to: a clone's container went on cloning as an orphan (T240), and an
extraction tool's went on writing into the server's `data/` (T303). So both
name their containers and end them here by that name: `docker rm -f`, which
kills and removes in one command.

The clone (`git.py`) and the extraction tools (`docker.run_container()`) share
this rather than each spelling it, because the second look below is the part a
copy would lose.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from yulon import runner
from yulon.log import get_logger

logger = get_logger(__name__)

REMOVED = "removed"
GONE = "gone"

LATE_CREATE_SETTLE = 1.0
"""How long a stopped run waits before asking a second time for a container that was gone.

A create request the daemon received before the Stop killed its CLI finishes
in milliseconds; a second has room for a slow daemon and costs the player one
second on a Stop that has already been answered. Not a guarantee, and said as
a bounded guess rather than a proof.
"""

END_CONTAINER_TIMEOUT = 60.0
"""How long `docker rm -f` of a stopped run's container may take before it is given up.

A deadlock breaker: the kill and the removal take a second or two on a healthy
daemon, and a daemon that does not answer must not hold a Stop's run open.
"""


def remove_once(launcher: Sequence[str], name: str) -> str:
    """One `docker rm -f name`: `REMOVED`, `GONE`, or the refusal in words (T240).

    `GONE` is "No such container", an exit 0 that named nothing (a CLI whose
    `-f` ignores a missing name), and Moby's "removal of container ... is already
    in progress", which is `--rm` getting there first: the container is going.
    """
    try:
        done = runner.run([*launcher, "rm", "-f", name], timeout=END_CONTAINER_TIMEOUT)
    except OSError as exc:
        return str(exc)
    said = done.stderr.lower()
    if done.returncode == 0:
        return REMOVED if done.stdout.strip() else GONE
    if "no such container" in said or "already in progress" in said:
        return GONE
    return done.stderr.strip() or f"docker rm exited {done.returncode}"


def end_container(launcher: Sequence[str], name: str, *, what: str) -> str | None:
    """Kill the container `name` and remove it: `docker rm -f`. Never raises.

    `what` names the run in the log ("clone", "extraction tool"). Returns None
    once it is gone, or why it could not be removed; a refusal is logged and
    returned rather than raised, because the Stop it serves has already happened
    and the run must still end.

    **"Gone" is asked twice (T240 cold review).** It is the usual answer: `--rm`
    removed a container whose program exited, or Moby is already removing it.
    But it is also the answer while the daemon is still creating a container
    whose CLI the Stop killed mid-request: the name is not there yet, and a
    never-started container appears a moment later. So a "gone" is asked
    again once, `LATE_CREATE_SETTLE` later, and the log says what it saw. A
    container that appears later than that is not caught, and the log does
    not claim it was ruled out.
    """
    first = remove_once(launcher, name)
    if first is REMOVED:
        logger.info(f"the {what} container {name} was ended and removed")
        return None
    if first is not GONE:
        logger.warning(f"could not remove the {what} container {name}: {first}")
        return first
    time.sleep(LATE_CREATE_SETTLE)
    second = remove_once(launcher, name)
    if second is REMOVED:
        logger.info(f"the {what} container {name} was created after the Stop and was removed")
        return None
    if second is not GONE:
        logger.warning(f"could not remove the {what} container {name}: {second}")
        return second
    # Not "gone": a container the daemon creates later than the second look
    # is not ruled out, and the line says only what was seen.
    logger.info(
        f"the {what} container {name} was not there when Yu'lon looked, after the Stop "
        f"and again {LATE_CREATE_SETTLE:g} s later"
    )
    return None
