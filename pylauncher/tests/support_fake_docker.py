"""A docker CLI on disk whose `run` is a container that outlives the CLI (T240).

Test support, beside `support_native.py`. Docker Desktop's CLI does not pass a
terminate on to the container it is attached to, so a Stop that ends the CLI
leaves the container running; on yulon-win11 (2026-10-04) a clone's container
went on for minutes as an orphan. This fake has the same shape: the CONTAINER
is a file under `containers/`, the CLI only watches it, and killing the CLI
leaves the file exactly where the daemon would leave the container. `rm -f
<name>` removes it, and the CLI watching it then exits 137; with a file named
`refuse-rm` in the state folder, `rm -f` is refused as a daemon would refuse it.

Its argv0 is `fake-docker`, so `conftest`'s real-daemon guard lets it run: it
is a script in the test's own folder and talks to nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

FAKE_DOCKER = """#!{python}
import os, pathlib, subprocess, sys, time

state = pathlib.Path({state!r})
args = sys.argv[1:]
with open(state / "calls.log", "a", encoding="utf-8") as calls:
    calls.write(" ".join(args) + "\\n")
if args[:1] == ["run"]:
    name = args[args.index("--name") + 1] if "--name" in args else "unnamed"
    box = state / "containers" / name
    if (state / "late-create").exists():
        # The daemon has the create request but not the name yet. It finishes
        # the create only after the CLI is gone AND the first `rm -f` has asked
        # for the name, which is the exact window a single `rm -f` misses.
        daemon = (
            "import os, pathlib, time\\n"
            f"state = pathlib.Path({{str(state)!r}}); cli = {{os.getpid()}}\\n"
            "deadline = time.monotonic() + 120\\n"
            "def alive():\\n"
            "    try:\\n"
            "        os.kill(cli, 0)\\n"
            "    except OSError:\\n"
            "        return False\\n"
            "    return True\\n"
            f"wanted = 'rm -f {{name}}'\\n"
            "while time.monotonic() < deadline:\\n"
            "    log = (state / 'calls.log').read_text(encoding='utf-8')\\n"
            "    asked = wanted in log.splitlines()\\n"
            "    if not alive() and asked:\\n"
            f"        made = state / 'containers' / {{name!r}}\\n"
            "        made.write_text('created', encoding='utf-8')\\n"
            "        break\\n"
            "    time.sleep(0.01)\\n"
        )
        subprocess.Popen(
            [sys.executable, "-c", daemon],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sys.stderr.write("Unable to find image locally; pulling\\n")
        sys.stderr.flush()
        time.sleep({lasts})
        sys.exit(0)
    box.write_text(str(os.getpid()), encoding="utf-8")
    sys.stderr.write("Cloning into '.'...\\n")
    sys.stderr.write("Receiving objects:   9% (21504/230316)\\r")
    sys.stderr.flush()
    deadline = time.monotonic() + {lasts}
    while box.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    sys.exit(137 if not box.exists() else 0)
if args[:2] == ["rm", "-f"]:
    box = state / "containers" / args[2]
    if (state / "refuse-rm").exists():
        sys.stderr.write("Error response from daemon: the daemon is shutting down\\n")
        sys.exit(1)
    if (state / "rm-in-progress").exists() and box.exists():
        # `--rm` got there first: Moby answers the second removal like this,
        # and the first one finishes on its own.
        box.unlink()
        sys.stderr.write(
            "Error response from daemon: removal of container "
            f"{{args[2]}} is already in progress\\n"
        )
        sys.exit(1)
    if not box.exists():
        sys.stderr.write(f"Error response from daemon: No such container: {{args[2]}}\\n")
        sys.exit(1)
    box.unlink()
    sys.stdout.write(args[2] + "\\n")
sys.exit(0)
"""

CONTAINER_LASTS = 600
"""How long the fake container clones for on its own: the FAKE'S WORK, not a bound.

Ten minutes, far past `HANG_BOUND`, so a run that waited for the container to
finish by itself fails its wait instead of passing slowly.
"""


def lay_fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    """Write the fake CLI and its state folder. Returns (the CLI, the state folder)."""
    state = tmp_path / "fake-docker-state"
    (state / "containers").mkdir(parents=True)
    cli = tmp_path / "fake-docker"
    cli.write_text(
        FAKE_DOCKER.format(python=sys.executable, state=str(state), lasts=CONTAINER_LASTS),
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return cli, state


def containers(state: Path) -> list[str]:
    """The names of the fake containers still running."""
    return sorted(box.name for box in (state / "containers").iterdir())


def calls(state: Path) -> list[str]:
    """Every argv the fake CLI was run with, one line each, in order."""
    log = state / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def end_fake_containers(state: Path) -> None:
    """Remove every container left, which ends its CLI: an orphan must not outlive the test."""
    for box in (state / "containers").iterdir():
        box.unlink(missing_ok=True)
