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
a missing image) and `start -a <name>` (the CLI attached to it, as `run` is), and
`ps --filter name=<part>` lists the running ones. With `start-refused`, `start -a` fails
and leaves the container created, as a daemon that cannot start it does.

`compose ... build` (T376) prints one line, records its environment's
`BUILDX_CONFIG`, `WSLENV` and `FAKE_DOCKER_INHERITED` (`build_env()`; the last
is a variable a test sets in its own environment, to see the child inherit
it), and exits 17 for a service named by a `fail-build-<service>` file in the
state folder. It records `BUILDX_BUILDER` too (`build_builders()`, T413), and
`buildx inspect` names the builder a plain build would use (`buildx-current`
holds "<name> <driver>"; the default is the context's own `default`, driver
`docker`) or, with `buildx-inspect-fails`, refuses. Since T527 `context show`
answers the current context (`use_context()`), and a build handed a
BUILDX_BUILDER other than `default` is refused the way compose refuses it.

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
if args[:1] == ["run"] and "-i" in args and (
    "yulon-claim-" in " ".join(args) or "yulon-busy-" in " ".join(args)
):
    # T543: a folder claim, `docker run --rm -i --name yulon-claim-<id> ... cat`. The name
    # is taken atomically (the daemon's arbitration), refused with the daemon's Conflict
    # when it is in use; it runs until its stdin closes, and `--rm` then removes it.
    # `claim-refused` makes the daemon refuse it for another reason.
    name = args[args.index("--name") + 1]
    box = state / "containers" / name
    if (state / "claim-refused").exists():
        sys.stderr.write("docker: Error response from daemon: No such image: nope\\n")
        sys.exit(125)
    if (state / "claim-no-daemon").exists():
        sys.stderr.write("docker: Cannot connect to the Docker daemon at unix:///var/run/docker.sock\\n")
        sys.exit(125)
    image = args[args.index("--entrypoint") + 2] if "--entrypoint" in args else ""
    if image and (state / "missing-images").exists():
        # T568: the image after `--entrypoint sh` is one the daemon does not have.
        if image in (state / "missing-images").read_text(encoding="utf-8").split():
            sys.stderr.write(f"docker: Error response from daemon: No such image: {{image}}\\n")
            sys.exit(125)
    if image:
        with open(state / "claim-images.log", "a", encoding="utf-8") as tried:
            tried.write(image + "\\n")
    while (state / "claim-slow").exists():  # the daemon takes its time (cold review of T543)
        time.sleep(0.02)
    labels = state / "labels"
    labels.mkdir(exist_ok=True)
    given = [args[i + 1] for i, arg in enumerate(args) if arg == "--label"]
    # The name is taken with its state already in it (T567): an empty file read as
    # "running" in the instant before the "created" went in, and a claim that never
    # ran was held. A real daemon's container is `created` from the moment it exists.
    first = "created" if (state / "claim-dies").exists() else str(os.getpid())
    pending = state / f".{{name}}.{{os.getpid()}}"
    pending.write_text(first, encoding="ascii")
    try:
        os.link(pending, box)
    except FileExistsError:
        sys.stderr.write(
            f'docker: Error response from daemon: Conflict. The container name "/{{name}}" is '
            f'already in use by container "{{name}}-id". You have to remove (or rename) that '
            "container to be able to reuse that name.\\n"
        )
        sys.exit(125)
    finally:
        pending.unlink(missing_ok=True)
    (labels / name).write_text("\\n".join(given), encoding="utf-8")
    if (state / "claim-dies").exists():
        # Created, never running: its command failed to start, and `--rm` takes it.
        time.sleep(0.5)
        box.unlink(missing_ok=True)
        sys.stderr.write("docker: Error response from daemon: failed to create task\\n")
        sys.exit(127)
    sys.stdin.read()
    if (state / "claim-lingers").exists() and box.exists():
        # T568: `--rm` removal in progress on a loaded daemon: `removing`, then gone.
        import subprocess
        box.write_text("removing", encoding="utf-8")
        subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time, pathlib; time.sleep(0.6); p = pathlib.Path(sys.argv[1]); "
             "p.unlink() if p.exists() and p.read_text() == 'removing' else None",
             str(box)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sys.exit(0)
    box.unlink(missing_ok=True)
    sys.exit(0)
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
    # Made whole and put in place at once (T610): `write_text` truncates first, and a reader
    # in that instant found an empty file, which `running()` reads as started.
    pending = state / f".{{name}}.{{os.getpid()}}"
    pending.write_text("created", encoding="utf-8")
    os.replace(pending, box)
    if "--label" in args:
        # Cold review of the stop-paths branch: whose Yu'lon made it and which folders it
        # writes, one `key=value` per line, read back by `ps`.
        labels = state / "labels"
        labels.mkdir(exist_ok=True)
        given = [args[i + 1] for i, arg in enumerate(args) if arg == "--label"]
        (labels / name).write_text("\\n".join(given), encoding="utf-8")
    sys.stdout.write(name + "-id\\n")
    sys.exit(0)
if args[:2] == ["start", "-a"]:
    # T321: attach to a container `create` made; its exit code is the container's.
    box = state / "containers" / args[2]
    if not box.exists():
        sys.stderr.write(f"Error response from daemon: No such container: {{args[2]}}\\n")
        sys.exit(1)
    if (state / "start-refused").exists():
        # Codex review round 3: the daemon will not start it, so it never runs and
        # `--rm` never removes it: it stays, created.
        sys.stderr.write("Error response from daemon: failed to create task for container\\n")
        sys.exit(1)
    attached(box)
if args[:1] == ["run"]:
    attached(box)
if args[:1] == ["ps"]:
    # `docker ps --filter name=<part> --format '{{{{.Names}}}}<tab>{{{{.Label ...}}}}'` (Codex
    # review of T303): the RUNNING containers only, as the real `ps` lists without `-a` --
    # not one merely created -- each with the value of every label its format names, in
    # that order (empty without one): the keys are read from the format (T536).
    if (state / "no-answer").exists():
        sys.stderr.write("Cannot connect to the Docker daemon. Is the docker daemon running?\\n")
        sys.exit(1)
    part = args[args.index("--filter") + 1].split("=", 1)[1] if "--filter" in args else ""
    for box in sorted((state / "containers").iterdir()):
        if part in box.name and box.read_text(encoding="utf-8") != "created":
            label = state / "labels" / box.name
            given = label.read_text(encoding="utf-8").splitlines() if label.exists() else []
            values = dict(line.split("=", 1) for line in given if "=" in line)
            fmt = args[args.index("--format") + 1] if "--format" in args else ""
            keys = [piece.split('"')[1] for piece in fmt.split(".Label ")[1:]]
            sys.stdout.write("\\t".join([box.name, *(values.get(k, "") for k in keys)]) + "\\n")
    sys.exit(0)
if args[:1] == ["inspect"]:
    if (state / "inspect-hangs").exists():
        time.sleep(3)  # T568: a daemon that is starting or paused: the caller's bound ends it
    # `docker.container_exit()`'s question (T303): a container still there is running.
    if (state / "no-answer").exists():
        sys.stderr.write("Cannot connect to the Docker daemon. Is the docker daemon running?\\n")
        sys.exit(1)
    fmt = args[args.index("--format") + 1] if "--format" in args else ""
    if (state / "containers" / args[1]).exists() and ".Config.Labels" in fmt:
        # T543: `{{{{.Id}}}}`, `{{{{.State.Status}}}}` when asked, then each
        # `{{{{index .Config.Labels "<key>"}}}}`, tab-separated.
        label = state / "labels" / args[1]
        given = label.read_text(encoding="utf-8").splitlines() if label.exists() else []
        values = dict(line.split("=", 1) for line in given if "=" in line)
        keys = [piece.split('"')[1] for piece in fmt.split(".Config.Labels ")[1:]]
        made = (state / "containers" / args[1]).read_text(encoding="utf-8")
        state_word = made if made in ("created", "removing") else "running"
        status = [state_word] if ".State.Status" in fmt else []
        # T568: `{{{{.Created}}}}` is the daemon's own stamp (a `created-at` file, else fixed).
        when = state / "created-at"
        made_at = (
            [when.read_text(encoding="utf-8").strip() if when.exists() else "2026-10-09T12:00:00Z"]
            if ".Created" in fmt
            else []
        )
        sys.stdout.write(
            "\\t".join([args[1] + "-id", *status, *made_at, *(values.get(k, "") for k in keys)])
            + "\\n"
        )
        sys.exit(0)
    if fmt == "{{{{.Image}}}}":
        # T568: the image a container runs (`images/<container>` holds it), as the daemon
        # answers for `docker inspect --format {{{{.Image}}}}`.
        pinned = state / "images" / args[1]
        if pinned.exists():
            sys.stdout.write(pinned.read_text(encoding="utf-8").strip() + "\\n")
            sys.exit(0)
        sys.stderr.write(f"Error: No such object: {{args[1]}}\\n")
        sys.exit(1)
    if (state / "containers" / args[1]).exists():
        sys.stdout.write(f"{{args[1]}}-id\\trunning\\t0\\t\\n")
        sys.exit(0)
    sys.stderr.write(f"Error: No such object: {{args[1]}}\\n")
    sys.exit(1)
if args[:2] == ["image", "ls"]:
    # T568: `image ls --format {{{{.Repository}}}}:{{{{.Tag}}}}`: the refs in `images-listed`.
    listed = state / "images-listed"
    sys.stdout.write(listed.read_text(encoding="utf-8") if listed.exists() else "")
    sys.exit(0)
if args[:2] == ["buildx", "inspect"]:
    # T413: the builder a plain build would use, in buildx's own text shape:
    # the builder's Name and Driver first, then its nodes, each with a Name of
    # its own. `buildx-current` holds "<name> <driver>"; without it, the
    # docker context's own builder. `buildx-inspect-fails` makes it refuse.
    if (state / "buildx-inspect-fails").exists():
        sys.stderr.write("ERROR: failed to find instance: no such builder\\n")
        sys.exit(1)
    current = state / "buildx-current"
    name, driver = (
        current.read_text(encoding="utf-8").split() if current.exists() else ("default", "docker")
    )
    sys.stdout.write(
        f"Name:          {{name}}\\nDriver:        {{driver}}\\n"
        "Last Activity: 2026-10-06 00:00:00 +0000 UTC\\n\\nNodes:\\n"
        f"Name:      {{name}}0\\nEndpoint:  unix:///var/run/docker.sock\\nStatus:    running\\n"
    )
    sys.exit(0)
if args[:2] == ["context", "show"]:
    # T527: the current docker context; `docker-context` holds its name, else
    # `default`, and `context-show-fails` makes it refuse.
    if (state / "context-show-fails").exists():
        sys.stderr.write("error: context not found\\n")
        sys.exit(1)
    current = state / "docker-context"
    name = current.read_text(encoding="utf-8") if current.exists() else "default"
    sys.stdout.write(name + "\\n")
    sys.exit(0)
if args[:1] == ["compose"] and "build" in args:
    # T376: a build is a line of output and an exit status, per call. What the
    # test reads is what the CLI was HANDED: its argv in `calls.log`, and the
    # BUILDX_CONFIG and WSLENV of its own environment in `build-env.log`; since
    # T413 its BUILDX_BUILDER in `build-builder.log`.
    with open(state / "build-env.log", "a", encoding="utf-8") as seen:
        seen.write(
            os.environ.get("BUILDX_CONFIG", "<unset>") + "\\t"
            + os.environ.get("WSLENV", "<unset>") + "\\t"
            + os.environ.get("FAKE_DOCKER_INHERITED", "<unset>") + "\\n"
        )
    with open(state / "build-builder.log", "a", encoding="utf-8") as seen:
        seen.write(os.environ.get("BUILDX_BUILDER", "<unset>") + "\\n")
    # T527: compose (v5.4.0 on Docker Desktop, v5.5.0 on docker-ce, measured
    # 2026-10-07) refuses a BUILDX_BUILDER that names a docker context other
    # than `default`, current or not; plain buildx accepts it. The contexts are
    # the current one (`docker-context`) and any in `docker-contexts`.
    builder = os.environ.get("BUILDX_BUILDER", "")
    known = {{"default"}}
    for listed in ("docker-context", "docker-contexts"):
        if (state / listed).exists():
            known.update((state / listed).read_text(encoding="utf-8").split())
    if builder and builder != "default" and builder in known:
        sys.stderr.write(
            f"use `docker --context={{builder}} buildx` to switch to context \\"{{builder}}\\"\\n"
        )
        sys.exit(1)
    target = args[-1] if args[-1] != "plain" else "<every service>"
    sys.stderr.write(f"#1 building {{target}}\\n")
    sys.stderr.flush()
    if (state / f"fail-build-{{target}}").exists():
        sys.stderr.write(f"ERROR: failed to solve: target {{target}}: exit code 2\\n")
        sys.exit(17)
    sys.exit(0)
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
    if not box.exists() and args[2].endswith("-id"):  # by the id `inspect` answered (T543)
        box = state / "containers" / args[2][: -len("-id")]
    if (state / "refuse-rm").exists():
        sys.stderr.write("Error response from daemon: the daemon is shutting down\\n")
        sys.exit(1)
    if (state / "rm-lingers").exists() and box.exists():
        # T568: a loaded daemon (measured on yulon-ubuntu, load 9): `rm -f` answers "already
        # in progress" and the container stays, `removing`, for a moment before it is gone.
        box.write_text("removing", encoding="utf-8")
        import subprocess
        subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time, pathlib; time.sleep(0.6); p = pathlib.Path(sys.argv[1]); "
             "p.unlink() if p.exists() and p.read_text() == 'removing' else None",
             str(box)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sys.stderr.write(
            f"Error response from daemon: removal of container {{args[2]}} "
            "is already in progress\\n"
        )
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


def build_env(state: Path) -> list[tuple[str, str, str]]:
    """(BUILDX_CONFIG, WSLENV, FAKE_DOCKER_INHERITED) per `compose build` the CLI ran (T376)."""
    log = state / "build-env.log"
    if not log.exists():
        return []
    rows = [line.split("\t") for line in log.read_text(encoding="utf-8").splitlines()]
    return [(row[0], row[1], row[2]) for row in rows]


def build_builders(state: Path) -> list[str]:
    """The BUILDX_BUILDER per `compose build` the CLI ran, `<unset>` when it had none (T413)."""
    log = state / "build-builder.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def use_builder(state: Path, name: str, driver: str) -> None:
    """What `docker buildx use <name>` leaves behind: the builder a plain build now uses (T413)."""
    (state / "buildx-current").write_text(f"{name} {driver}", encoding="utf-8")


def add_context(state: Path, name: str) -> None:
    """`docker context create <name>`: a context compose knows, not the current one (T527)."""
    with open(state / "docker-contexts", "a", encoding="utf-8") as contexts:
        contexts.write(name + "\n")


def use_context(state: Path, name: str) -> None:
    """What `docker context use <name>` leaves behind: `context show` answers it (T527).

    A context's own builder is a `docker`-driver builder of the same name, which
    `buildx inspect` then names, as on a stock Docker Desktop (`desktop-linux`).
    """
    (state / "docker-context").write_text(name, encoding="utf-8")
    use_builder(state, name, "docker")
