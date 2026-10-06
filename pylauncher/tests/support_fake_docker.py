"""A docker CLI on disk whose `run` is a container that outlives the CLI (T240).

Test support, beside `support_native.py`. Docker Desktop's CLI does not pass a
terminate on to the container it is attached to, so a Stop that ends the CLI
leaves the container running; on yulon-win11 (2026-10-04) a clone's container
went on for minutes as an orphan. This fake has the same shape: the CONTAINER
is a file under `containers/`, the CLI only watches it, and killing the CLI
leaves the file exactly where the daemon would leave the container. `rm -f
<name>` removes it, and the CLI watching it then exits 137; with a file named
`refuse-rm` in the state folder, `rm -f` is refused as a daemon would refuse it.
With `late-create`, `run` makes no container at all until the test calls
`finish_late_create()`: the daemon that finishes a create after the Stop.

Since T321 it also answers `create --rm --name <name>` (the container exists,
not yet running; `slow-create` holds the answer, `create-refused` refuses it as
a missing image) and `start -a <name>` (the CLI attached to it, as `run` is).

Its argv0 is `fake-docker`, so `conftest`'s real-daemon guard lets it run: it
is a script in the test's own folder and talks to nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

FAKE_DOCKER = """#!{python}
import os, pathlib, sys, time

state = pathlib.Path({state!r})
args = sys.argv[1:]
with open(state / "calls.log", "a", encoding="utf-8") as calls:
    calls.write(" ".join(args) + "\\n")
def attached(box):
    # The CLI attached to a running container: it prints, then only watches.
    box.write_text(str(os.getpid()), encoding="utf-8")
    sys.stderr.write("Cloning into '.'...\\n")
    sys.stderr.write("Receiving objects:   9% (21504/230316)\\r")
    sys.stderr.flush()
    deadline = time.monotonic() + {lasts}
    while box.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    sys.exit(137 if not box.exists() else 0)
if args[:1] in (["run"], ["create"]):
    name = args[args.index("--name") + 1] if "--name" in args else "unnamed"
    box = state / "containers" / name
    if (state / "late-create").exists():
        # The daemon has the create request but not the name yet, so there is
        # no container for a Stop to find. The TEST finishes the create, with
        # `finish_late_create()`, at the moment it chooses (T305).
        sys.stderr.write("Unable to find image locally; pulling\\n")
        sys.stderr.flush()
        time.sleep({lasts})
        sys.exit(0)
if args[:1] == ["create"]:
    # T321: `docker create` answers once the container exists. With `slow-create`
    # the daemon takes its time: the CLI says it was asked (`create-asked`) and
    # answers when the test removes `slow-create`.
    if (state / "slow-create").exists():
        (state / "create-asked").write_text(name, encoding="utf-8")
        while (state / "slow-create").exists():
            time.sleep(0.02)
    if (state / "create-refused").exists():
        sys.stderr.write("Unable to find image 'yulon.local/nope:native' locally\\n")
        sys.stderr.write("Error response from daemon: pull access denied\\n")
        sys.exit(125)
    box.write_text("created", encoding="utf-8")
    sys.stdout.write(name + "-id\\n")
    sys.exit(0)
if args[:2] == ["start", "-a"]:
    # T321: attach to a container `create` made; its exit code is the container's.
    box = state / "containers" / args[2]
    if not box.exists():
        sys.stderr.write(f"Error response from daemon: No such container: {{args[2]}}\\n")
        sys.exit(1)
    attached(box)
if args[:1] == ["run"]:
    attached(box)
if args[:1] == ["inspect"]:
    # `docker.container_exit()`'s question (T303): a container still there is running.
    if (state / "no-answer").exists():
        sys.stderr.write("Cannot connect to the Docker daemon. Is the docker daemon running?\\n")
        sys.exit(1)
    if (state / "containers" / args[1]).exists():
        sys.stdout.write(f"{{args[1]}}-id\\trunning\\t0\\t\\n")
        sys.exit(0)
    sys.stderr.write(f"Error: No such object: {{args[1]}}\\n")
    sys.exit(1)
if args[:1] == ["logs"]:
    # `docker logs [--tail N] <name>`: the container's stdout on stdout and its
    # stderr on stderr, as the real CLI splits them (T249).
    name = args[-1]
    out, err = state / "logs" / (name + ".out"), state / "logs" / (name + ".err")
    if not out.exists():
        sys.stderr.write(f"Error response from daemon: No such container: {{name}}\\n")
        sys.exit(1)
    tail = int(args[args.index("--tail") + 1]) if "--tail" in args else None
    for path, stream in ((out, sys.stdout), (err, sys.stderr)):
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        kept = text.splitlines(keepends=True)
        stream.write("".join(kept if tail is None else kept[-tail:] if tail else []))
    sys.exit(0)
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


def set_fake_log(state: Path, name: str, *, stdout: str, stderr: str = "") -> None:
    """Give the fake daemon a container `name` whose log is `stdout` and `stderr` (T249)."""
    folder = state / "logs"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.out").write_text(stdout, encoding="utf-8")
    (folder / f"{name}.err").write_text(stderr, encoding="utf-8")


def containers(state: Path) -> list[str]:
    """The names of the fake containers still running."""
    return sorted(box.name for box in (state / "containers").iterdir())


def running(state: Path) -> list[str]:
    """The fake containers a `start -a` (or `run`) has started, not merely created (T321)."""
    return [
        name
        for name in containers(state)
        if (state / "containers" / name).read_text(encoding="utf-8") != "created"
    ]


def calls(state: Path) -> list[str]:
    """Every argv the fake CLI was run with, one line each, in order."""
    log = state / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def end_fake_containers(state: Path) -> None:
    """Remove every container left, which ends its CLI: an orphan must not outlive the test."""
    for box in (state / "containers").iterdir():
        box.unlink(missing_ok=True)


def finish_late_create(state: Path) -> str:
    """With `late-create`: the daemon finishes the create now. Returns the container's name.

    Called by the test, not by a process of its own: the daemon this stands in
    for used to be a second process polling `calls.log`, and it raced the very
    `rm -f` it waited for (T305). The create is a `run` or, since T321, a `create`.
    """
    (run,) = [call.split() for call in calls(state) if call.startswith(("run ", "create "))]
    name = run[run.index("--name") + 1]
    (state / "containers" / name).write_text("created", encoding="utf-8")
    return name
