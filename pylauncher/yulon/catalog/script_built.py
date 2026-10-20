"""Rebuilding a WotLK server the old Dad's MMO Lab bash script built inside WSL (T804).

The old launcher's install script cloned `mod-playerbots/azerothcore-wotlk`, wrote one
`docker-compose.override.yml` of its own beside upstream's `docker-compose.yml`, and ran
`docker compose up -d --build`. It left no `.yulon-install.json`, so `StagedInstaller`
refuses a Rebuild there ("has no .yulon-install.json ... rebuild that one the way it was
built") and the old launcher no longer exists to do it.

This module is that "way it was built", and nothing wider:

* **No marker, no file written in the folder, no takeover.** Nothing here calls
  `write_state()` or touches a compose file; Uninstall and "Update to latest" stay
  unavailable for the folder because the folder still carries no record of Yu'lon's.
* **Never `compose down`, never `-v`.** The characters live in the `<project>_ac-database`
  volume; the one command that touches containers is `up -d --pull never --no-build`.
* **A folder qualifies only when every rule of `check()` holds**, because "looks like the
  script's" is not a thing to guess at on a database that holds somebody's characters. A
  rule that cannot be asked (git, docker) fails the folder, never passes it.

The override is read by `parse_override()`, a deliberately tiny parser for the shape the
script writes (mappings, lists of plain scalars, nothing else): PyYAML is a dev dependency
only, and a file in any other shape is a refusal, which is the safe answer here.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yulon import docker, git, platform
from yulon.catalog import composegen
from yulon.catalog.installer import InstallerError, InstallStopped
from yulon.log import get_logger
from yulon.ownership import Ownership

logger = get_logger(__name__)

WORLD = "ac-worldserver"
AUTH = "ac-authserver"
IMPORT = "ac-db-import"
CLIENT_DATA = "ac-client-data-init"
DATABASE = "ac-database"

BUILT_SERVICES: dict[str, tuple[str, str]] = {
    WORLD: ("worldserver", "acore/ac-wotlk-worldserver:master"),
    AUTH: ("authserver", "acore/ac-wotlk-authserver:master"),
    IMPORT: ("db-import", "acore/ac-wotlk-db-import:master"),
    CLIENT_DATA: ("client-data", "acore/ac-wotlk-client-data:master"),
}
"""service -> (its `build.target`, the image the script's compose file tags it as)."""

IMAGE_REFS: tuple[str, ...] = tuple(ref for _target, ref in BUILT_SERVICES.values())

MODULES_MOUNTS: tuple[str, ...] = (
    "./modules:/azerothcore/modules",
    "./modules:/azerothcore/modules:Z",
)
"""The worldserver's one mount: the script's, and the Fedora script's SELinux spelling of it."""

CORE_REPO = "https://github.com/mod-playerbots/azerothcore-wotlk"
MODULE_REPO = "https://github.com/mod-playerbots/mod-playerbots"
MODULE_FOLDER = "modules/mod-playerbots"

STRANGER_FILES: tuple[str, ...] = (
    composegen.BUILD_FILE,
    "compose.yml",
    "compose.yaml",
    "docker-compose.yaml",
)
"""Files whose presence means the folder is not the script's bare pair (or has a third recipe)."""

OLD_LAUNCHER_RECORDS: tuple[str, ...] = (".dml-install.json", ".dml-migrate.json")

UP_ARGV: tuple[str, ...] = ("compose", "up", "-d", "--pull", "never", "--no-build")
"""The only command that touches containers: never pulls, never builds, never removes."""

WORLD_CONTAINER = WORLD


QUESTION_TITLE = "Rebuild the way it was built?"


def question(server_dir: Path, wsl_distro: str) -> str:
    """The question the Rebuild press asks INSTEAD of the ordinary confirmation (T804)."""
    return (
        f"{server_dir} in {wsl_distro} was built by the old Dad's MMO Lab script, not by "
        "Yu'lon. Yu'lon can rebuild it the way that script did: compile the server again from "
        "this folder's own files (2-4 hours), then start it on the new build.\n\n"
        "Yu'lon will not change, add or remove any file in that folder, and it will not take "
        "the server over: Uninstall and Update to latest stay unavailable for it.\n\n"
        "When the server starts, its database applies any SQL the new modules bring, and that "
        "cannot be undone without a backup. If you have none, press No and take one on the "
        "Maintenance tab first.\n\n"
        "Rebuild it now?"
    )


# --------------------------------------------------------------------------- the override


class NotTheScriptsFile(ValueError):
    """The override is not in the narrow YAML shape the script writes."""


_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_PLAIN = re.compile(r"^[^\s#{}\[\],&*!|>'\"%@`-][^#]*$|^-[^\s#][^#]*$")


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        if text[0] in inner or "\\" in inner:
            raise NotTheScriptsFile(f"a quoted value this check does not read: {text}")
        return inner
    if not _PLAIN.match(text) or ": " in text or text.endswith(":"):
        raise NotTheScriptsFile(f"a value this check does not read: {text}")
    return text


def parse_override(text: str) -> dict[str, Any]:
    """The script's override as nested dicts of `str` and lists of `str`; raises when it is not.

    Accepts only: `key:` with a deeper block under it, `key: scalar`, and `- scalar` list
    items, indented with spaces, with whole-line comments. Tabs, flow style, anchors, block
    scalars, inline comments, multiple documents and duplicate keys are all refused.
    """
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw:
            raise NotTheScriptsFile("a tab in the file")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rows.append((len(raw) - len(raw.lstrip(" ")), stripped))
    if not rows:
        raise NotTheScriptsFile("the file is empty")
    value, end = _node(rows, 0, rows[0][0])
    if end != len(rows) or not isinstance(value, dict):
        raise NotTheScriptsFile("the file is not one block of settings")
    return value


def _node(rows: list[tuple[int, str]], at: int, indent: int) -> tuple[Any, int]:
    if rows[at][1] == "-" or rows[at][1].startswith("- "):
        items: list[str] = []
        while at < len(rows) and rows[at][0] == indent:
            body = rows[at][1]
            if not (body == "-" or body.startswith("- ")):
                raise NotTheScriptsFile(f"a list mixed with settings: {body}")
            items.append(_scalar(body[1:]))
            at += 1
        return items, at
    found: dict[str, Any] = {}
    while at < len(rows) and rows[at][0] == indent:
        body = rows[at][1]
        key, colon, rest = body.partition(":")
        if not colon or not _KEY.match(key) or (rest and not rest.startswith(" ")):
            raise NotTheScriptsFile(f"a line this check does not read: {body}")
        if key in found:
            raise NotTheScriptsFile(f"{key} is set twice")
        if rest.strip():
            found[key] = _scalar(rest)
            at += 1
            continue
        if at + 1 >= len(rows) or rows[at + 1][0] <= indent:
            raise NotTheScriptsFile(f"{key} has nothing under it")
        found[key], at = _node(rows, at + 1, rows[at + 1][0])
    if at < len(rows) and rows[at][0] > indent:
        raise NotTheScriptsFile(f"a line indented out of place: {rows[at][1]}")
    return found, at


def override_problem(text: str) -> str | None:
    """Why `text` is not an override the old script writes (R4), or None when it is."""
    try:
        parsed = parse_override(text)
    except NotTheScriptsFile as exc:
        return str(exc)
    if set(parsed) != {"services"}:
        return f"it holds {', '.join(sorted(parsed))} besides services"
    services = parsed["services"]
    if not isinstance(services, dict) or set(services) != set(BUILT_SERVICES):
        return "it does not hold exactly the script's four services"
    for name, (target, _ref) in BUILT_SERVICES.items():
        service = services[name]
        allowed = {"build", "volumes", "environment"} if name == WORLD else {"build"}
        if not isinstance(service, dict) or "build" not in service or set(service) - allowed:
            return f"{name} carries settings the script never wrote"
        build = service["build"]
        if build != {"context": ".", "target": target}:
            return f"{name}'s build is not the script's (context . and target {target})"
        if "volumes" in service and service["volumes"] not in [[m] for m in MODULES_MOUNTS]:
            return f"{name}'s volumes are not just the modules folder"
        if "environment" in service:
            env = service["environment"]
            if not isinstance(env, dict) or not env:
                return f"{name}'s environment is not a list of AC_ settings"
            for key in env:
                if not key.startswith("AC_"):
                    return f"{name}'s environment holds {key}, which is not an AC_ setting"
    return None


# --------------------------------------------------------------------------- the seams


@dataclass(frozen=True)
class ScriptBuiltSeams:
    """Everything `check()` and `rebuild_as_built()` reach outside themselves through."""

    linux_path: Callable[[Path, str], str | None]
    claim: Callable[[Path], Ownership]
    remote_url: Callable[[Path], str | None]
    file_unmodified: Callable[[Path, str], bool | None]
    compose_config: Callable[[Path], dict[str, Any] | None]
    container_exists: Callable[[str], bool]
    container_project: Callable[[str], str | None]
    container_working_dir: Callable[[str], str | None]
    world_running: Callable[[str], bool | None]
    daemon_ready: Callable[[], bool]
    build: Callable[..., docker.AttachedRun]
    images_built: Callable[[Sequence[str]], bool | None]
    run_attached: Callable[..., docker.AttachedRun]

    @classmethod
    def in_wsl(cls, distro: str) -> ScriptBuiltSeams:
        """The real answers, every docker and git question addressed to `distro`'s own Docker."""
        from yulon.catalog import native  # lazy: native imports this module for its refusal

        repo = git.ContainerGit(wsl_distro=distro)

        def claim(server_dir: Path) -> Ownership:
            return native.read_claim(server_dir, valid=()).ownership

        def build(
            server_dir: Path,
            files: Sequence[str],
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            return docker.build_staged(
                server_dir, files, wsl_distro=distro, sink=sink, cancel=cancel
            )

        def run_attached(
            argv: list[str],
            cwd: Path,
            sink: docker.OutputSink,
            cancel: threading.Event | None,
        ) -> docker.AttachedRun:
            return docker.run_attached(
                argv, cwd, wsl_distro=distro, sink=sink, cancel=cancel, merge_stderr=True
            )

        return cls(
            linux_path=platform.wsl_linux_path_in,
            claim=claim,
            remote_url=repo.remote_url,
            file_unmodified=repo.is_unmodified,
            compose_config=lambda folder: docker.compose_config(folder, wsl_distro=distro),
            container_exists=lambda name: docker.container_exists(name, wsl_distro=distro),
            container_project=lambda name: docker.container_project(name, wsl_distro=distro),
            container_working_dir=lambda name: docker.container_working_dir(
                name, wsl_distro=distro
            ),
            world_running=lambda name: docker.world_running(name, wsl_distro=distro),
            daemon_ready=lambda: docker.daemon_ready(wsl_distro=distro),
            build=build,
            images_built=lambda refs: docker.images_built(refs, wsl_distro=distro),
            run_attached=run_attached,
        )


# --------------------------------------------------------------------------- the rules


@dataclass(frozen=True)
class ScriptBuiltCheck:
    """`check()`'s answer: the project and Linux folder it verified, or the sentence refusing."""

    project: str
    linux_dir: str
    refusal: str | None


def _refused(server_dir: Path, rule: str, what: str) -> ScriptBuiltCheck:
    return ScriptBuiltCheck(
        "",
        "",
        f"{server_dir} did not match a server built by the old Dad's MMO Lab script "
        f"(rule {rule}: {what}).",
    )


def looks_script_built(server_dir: Path) -> bool:
    """The cheap file half of the rules (R2, R3): may this folder be worth the full check?

    Reads the folder only. Never raises. It is what decides whether the Rebuild press asks
    the script-built question; the press itself runs the whole of `check()` again.
    """
    try:
        if (server_dir / ".yulon-install.json").exists():
            return False
        if any((server_dir / name).exists() for name in (*OLD_LAUNCHER_RECORDS, *STRANGER_FILES)):
            return False
        for name in (composegen.BASE_FILE, composegen.OVERRIDE_FILE):
            path = server_dir / name
            if not path.is_file() or composegen.is_ours(path):
                return False
    except OSError:
        return False
    return True


def check(server_dir: Path, wsl_distro: str | None, *, seams: ScriptBuiltSeams) -> ScriptBuiltCheck:
    """Rules R1-R9 in order; the first that fails is the answer. Runs docker: not for the GUI."""
    # R1: a folder inside the distro this tab remembers.
    if not wsl_distro:
        return _refused(server_dir, "R1", "it is not a server inside a WSL distro")
    try:
        linux_dir = seams.linux_path(server_dir, wsl_distro)
    except platform.WslDistroMismatch as exc:
        return _refused(server_dir, "R1", str(exc))
    if linux_dir is None:
        return _refused(server_dir, "R1", f"it is not a folder inside the WSL distro {wsl_distro}")

    # R2: no record of anyone's: Yu'lon's own folders take the normal Rebuild.
    claim = seams.claim(server_dir)
    if claim is Ownership.OWNED:
        return _refused(server_dir, "R2", "it carries Yu'lon's own install record")
    if claim is not Ownership.UNCLAIMED:
        return _refused(server_dir, "R2", "its install record cannot be read")
    for name in OLD_LAUNCHER_RECORDS:
        if (server_dir / name).exists():
            return _refused(server_dir, "R2", f"it carries the old launcher's {name}")

    # R3: upstream's compose file and the script's override, both unmarked, and nothing else.
    for name in (composegen.BASE_FILE, composegen.OVERRIDE_FILE):
        path = server_dir / name
        if not path.is_file():
            return _refused(server_dir, "R3", f"{name} is missing")
        if composegen.is_ours(path):
            return _refused(server_dir, "R3", f"{name} was written by Yu'lon")
    for name in STRANGER_FILES:
        if (server_dir / name).exists():
            return _refused(server_dir, "R3", f"{name} is there too")

    # R4: the override is exactly what the script writes.
    try:
        override = (server_dir / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _refused(server_dir, "R4", f"{composegen.OVERRIDE_FILE} cannot be read ({exc})")
    problem = override_problem(override)
    if problem is not None:
        return _refused(server_dir, "R4", f"{composegen.OVERRIDE_FILE}: {problem}")

    # R5: the script's two clones, and upstream's compose file untouched.
    origin = seams.remote_url(server_dir)
    if origin is None or not git.same_repo(origin, CORE_REPO):
        return _refused(server_dir, "R5", f"its checkout does not come from {CORE_REPO}")
    if seams.file_unmodified(server_dir, composegen.BASE_FILE) is not True:
        return _refused(
            server_dir, "R5", f"{composegen.BASE_FILE} is not exactly what the checkout committed"
        )
    module_origin = seams.remote_url(server_dir / MODULE_FOLDER)
    if module_origin is None or not git.same_repo(module_origin, MODULE_REPO):
        return _refused(server_dir, "R5", f"{MODULE_FOLDER} does not come from {MODULE_REPO}")

    # R6: what compose itself makes of the two files.
    config = seams.compose_config(server_dir)
    if config is None:
        return _refused(server_dir, "R6", "`docker compose config` did not answer")
    project = config.get("name")
    if not isinstance(project, str) or not project:
        return _refused(server_dir, "R6", "compose names no project for it")
    if project.startswith(composegen.PROJECT_PREFIX):
        return _refused(server_dir, "R6", f"its compose project {project} is one Yu'lon names")
    services = config.get("services")
    if not isinstance(services, dict):
        return _refused(server_dir, "R6", "compose lists no services")
    for name, body in services.items():
        if name.startswith("ac-") and (
            not isinstance(body, dict) or body.get("container_name") != name
        ):
            return _refused(server_dir, "R6", f"{name} is not named {name} as a container")
    for name, (_target, ref) in BUILT_SERVICES.items():
        body = services.get(name)
        if not isinstance(body, dict) or body.get("image") != ref:
            return _refused(server_dir, "R6", f"{name} does not run the image {ref}")
    volumes = config.get("volumes")
    if not isinstance(volumes, dict) or DATABASE not in volumes:
        return _refused(server_dir, "R6", f"compose declares no {DATABASE} volume")

    # R7: containers that already exist belong to this project and this folder.
    for name in (DATABASE, WORLD, AUTH):
        if not seams.container_exists(name):
            continue
        owner = seams.container_project(name)
        if owner != project:
            said = (
                "a project Docker would not name"
                if owner == docker.UNREADABLE
                else f"the project {owner}" if owner else "no compose project"
            )
            return _refused(server_dir, "R7", f"the container {name} belongs to {said}")
        where = seams.container_working_dir(name)
        if where != linux_dir:
            said = (
                "a folder Docker would not name"
                if where == docker.UNREADABLE
                else where or "no folder"
            )
            return _refused(server_dir, "R7", f"the container {name} was started from {said}")

    # R8: nothing to take down: the world is not running, and Docker said so.
    running = seams.world_running(WORLD_CONTAINER)
    if running is None:
        return _refused(server_dir, "R8", "Docker could not say whether the world server runs")
    if running:
        return _refused(server_dir, "R8", "the world server is running; stop it first")

    # R9: Docker answers.
    if not seams.daemon_ready():
        return _refused(server_dir, "R9", "Docker in the distro is not answering")
    return ScriptBuiltCheck(project, linux_dir, None)


# --------------------------------------------------------------------------- the rebuild


def rebuild_as_built(
    server_dir: Path,
    wsl_distro: str,
    cancel: threading.Event | None = None,
    *,
    seams: ScriptBuiltSeams | None = None,
) -> Iterator[str]:
    """Compile the folder's own files again and start the server on the result. Lines out.

    Writes nothing in `server_dir`. A build that fails or is stopped never reaches `up`, and
    `up` is only reached when all four tags exist (with `--pull never`, a missing tag would
    otherwise be answered by Docker Hub's stock `:master`). Raises `InstallerError`.
    """
    from yulon.catalog.native import pump_lines  # lazy: native imports this module

    use = seams if seams is not None else ScriptBuiltSeams.in_wsl(wsl_distro)
    verdict = check(server_dir, wsl_distro, seams=use)
    if verdict.refusal is not None:
        raise InstallerError(f"{verdict.refusal} Nothing was started.")
    yield (
        f"Rebuilding {server_dir} the way the old script built it: compiling the folder's own "
        "files, then starting the server on the result. Nothing in the folder is changed."
    )
    files = (composegen.BASE_FILE, composegen.OVERRIDE_FILE)
    built = yield from pump_lines(
        lambda sink: use.build(server_dir, files, sink, cancel), cancel=cancel, stage="build"
    )
    stopped = built.returncode == docker.CANCELLED_RETURNCODE or (
        cancel is not None and cancel.is_set()
    )
    if stopped:
        raise InstallStopped(
            "The rebuild was stopped. No container was touched, so the server was left on "
            "its old build."
        )
    if built.returncode != 0:
        raise InstallerError(
            f"The build failed (exit {built.returncode}), so the server was left on its old "
            "build and nothing was started. Its last words were: "
            f"{docker.last_words(built.tail, from_build=True)}"
        )
    if use.images_built(IMAGE_REFS) is not True:
        raise InstallerError(
            "The build finished, but Docker does not hold all four server images "
            f"({', '.join(IMAGE_REFS)}), so nothing was started: starting now could run a "
            "stock image instead of yours. The server was left on its old build."
        )
    yield "Compiled. Starting the server on the new build."
    started = yield from pump_lines(
        lambda sink: use.run_attached(list(UP_ARGV), server_dir, sink, cancel),
        cancel=cancel,
        stage="start",
    )
    if started.returncode == docker.CANCELLED_RETURNCODE or (
        cancel is not None and cancel.is_set()
    ):
        raise InstallStopped(
            "The start was stopped. The new build is compiled; press Rebuild again to start it."
        )
    if started.returncode != 0:
        raise InstallerError(
            f"The new build is compiled, but starting the server failed (exit "
            f"{started.returncode}). Its last words were: "
            f"{docker.last_words(started.tail, from_build=False)}"
        )
    yield (
        "Done. The server is starting on the new build; its world takes several minutes to be "
        "ready, because the database applies the new modules' SQL first."
    )
