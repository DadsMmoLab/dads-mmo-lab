"""Freeing Docker disk space from the Maintenance tab (T864).

Each WotLK/CoA rebuild leaves behind some 8-10 GB of BuildKit build cache and
some 1.5-2 GB of dangling db-import/client-data images (`_let_go` drops the
`-rollback` name while an exited one-shot container still holds the image, so
it dangles for ever), and Docker 28+'s own GC only trims at about 80 % disk.
This module is the plan-and-act half of the Maintenance tab's
"Free Docker space…" press, modelled on `backup_shelf`: `read_space()` reads
first, the dialog shows a checklist with sizes, and `carry_out()` acts under
the maintenance lease.

The three parts, in the order they are freed: (b) Yu'lon's old untagged
builds, ticked: dangling images whose compose project label starts with
`yulon-` (any install, removed ones included), removed one id at a time with
plain `docker image rm` (never `-f`); one a stopped container still holds is
left alone and counted. (c) This server's kept build (`-parked`), unticked:
removed through the kept-build route's `remove()`, which lets the names go
and then forgets the record. (a) Docker's build cache, ticked: the whole
unused cache with `docker builder prune -f`. Old database copies are not
here: one line points at Clean up…, which already owns them.

What is never offered: live tags, `-rollback`, volumes, anything without the
`yulon-` compose label, `image prune`/`system prune`. The build cache carries
no labels, so it can only be taken whole; it belongs to every Docker project
on this computer, and the next build of anything compiles from the start.

Windows wording rule (D3): never promise space back on C:. Docker reuses the
freed space at once, but the free space Windows shows may only grow once
Docker Desktop compacts its disk file.

Documented limit: the press is refused while a server build press runs in
this Yu'lon, but another Yu'lon's build on another server is not seen
(`builder prune` never removes cache a running build uses, and a build's old
image is named `-rollback`, not dangling, until the build ends).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from yulon import docker, forgetting, platform, server_build_presses
from yulon.catalog import composegen
from yulon.log import get_logger

if TYPE_CHECKING:
    from yulon.catalog.native import KeptBuildRoute

logger = get_logger(__name__)

GIB = 1024**3
CACHE_POINTER_BYTES = 20 * GIB
"""The build cache size above which a successful build points at the press (T864 D1)."""


def gb_text(n: int) -> str:
    """Gibibytes shown as GB with one decimal, as preflight sizes them (T864)."""
    return f"{max(n, 0) / GIB:.1f} GB"


def cache_pointer(cache: int) -> str:
    """The finish line when the cache is over `CACHE_POINTER_BYTES` (T864 D1)."""
    return (
        f"Docker's build cache now takes {gb_text(cache)} on this computer. "
        f"{server_build_presses.FREE_DOCKER_SPACE_PLACE} shows it and can remove it."
    )


OLD_IMAGES_NOT_REMOVED = (
    "Yu'lon could not remove this server's old images from Docker this time; the log says why. "
    "The new build is not affected."
)
CACHE_NOTE = (
    "Docker keeps the steps of every build so the next one is faster. It belongs to every Docker "
    "project on this computer, not only Yu'lon's servers: after removing it, the next build of any "
    "server compiles from the start and takes as long as a first install."
)
OLD_BUILDS_NOTE = (
    "Old images that earlier builds of Yu'lon's servers left behind with no name, including "
    "servers you removed. One is left alone while any container still uses it; an image held "
    "only by finished one-shot containers of that same server (the import and download steps) "
    "is removed with them, and the next start makes them again."
)
KEPT_NOTE = (
    f"The build this server kept from before its last rebuild. The next "
    f"“{server_build_presses.REBUILD}” can use it instead of compiling, so it is not ticked."
)
NEVER_NOTE = (
    "Never removed here: the images your servers run on, their databases, and anything in Docker "
    "that is not Yu'lon's, apart from the build cache."
)
WINDOWS_NOTE = (
    "On Windows, Docker Desktop keeps its data inside one disk file. Docker can reuse the space "
    "freed here straight away; the free space Windows shows may only grow once Docker Desktop "
    "compacts that file."
)


def cache_row(space: Space) -> str:
    """The dialog's build-cache row for `space` (T864)."""
    if space.cache_bytes is None:
        return "Docker's build cache: Docker did not say how big it is"
    return f"Docker's build cache: {gb_text(space.cache_bytes)}"


def old_builds_row(space: Space) -> str:
    """The dialog's old-builds row for `space` (T864)."""
    if space.old_builds is None:
        return "Yu'lon's old untagged builds: Docker did not list them"
    if not space.old_builds:
        return "Yu'lon's old untagged builds: none"
    n = len(space.old_builds)
    total = sum(build.size for build in space.old_builds)
    return f"Yu'lon's old untagged builds: {n} image{'' if n == 1 else 's'}, up to {gb_text(total)}"


def kept_row(space: Space) -> str:
    """The dialog's kept-build row for `space` (T864)."""
    if space.kept_build_bytes is None:
        return "This server's kept build: size unknown"
    return f"This server's kept build: up to {gb_text(space.kept_build_bytes)}"


_refusal_cls: type | None = None


def _refusal_class() -> type:
    """`DockerSpaceRefusal`, binding its `MaintenanceError` base on first use (T864).

    A top-level import would cycle: `maintenance -> docker_ctl -> native ->
    docker_space -> maintenance`. By call time every module is loaded, so the
    base binds then; `__getattr__` below exposes the same class as
    `docker_space.DockerSpaceRefusal`.
    """
    global _refusal_cls
    if _refusal_cls is None:
        from yulon.controller_wow_wotlk.maintenance import MaintenanceError

        class DockerSpaceRefusal(MaintenanceError):
            """Docker space that was not freed, in a sentence for the player.

            A `MaintenanceError`, so the Maintenance tab shows it as it shows a refused
            restore, with nothing removed.
            """

        _refusal_cls = DockerSpaceRefusal
        globals()["DockerSpaceRefusal"] = DockerSpaceRefusal
    return _refusal_cls


def __getattr__(name: str) -> object:
    if name == "DockerSpaceRefusal":
        return _refusal_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class OldBuild:
    """One dangling image of a Yu'lon server, still on disk with no name (T864)."""

    image_id: str
    size: int
    project: str


@dataclass(frozen=True)
class Space:
    """What `read_space()` found Docker holding (T864)."""

    cache_bytes: int | None
    old_builds: tuple[OldBuild, ...] | None
    """None when Docker would not list them."""
    kept_build: bool
    """Whether this install keeps a build now."""
    kept_build_bytes: int | None


@dataclass(frozen=True)
class Choice:
    """What the dialog ticked (T864 D2: the kept build is not ticked)."""

    cache: bool = True
    old_builds: bool = True
    kept_build: bool = False


@dataclass(frozen=True)
class Freed:
    """The press outcome, one player sentence per part (T864)."""

    lines: tuple[str, ...]


def _old_builds(wsl_distro: str | None) -> tuple[OldBuild, ...] | None:
    """Yu'lon's old untagged builds on this daemon, or None when unlisted (T864).

    Lists dangling images by the compose project label KEY, then keeps only the
    facts whose project starts with the `yulon-` prefix: compose stamps every
    image it builds, while `alpine/git` (pinned by digest) carries no compose
    label and is never among them.
    """
    ids = docker.dangling_image_ids(docker.PROJECT_LABEL, wsl_distro=wsl_distro)
    if ids is None:
        return None
    if not ids:
        return ()
    facts = docker.image_facts(ids, wsl_distro=wsl_distro)
    if facts is None:
        return None
    return tuple(
        OldBuild(image_id=fact.image_id, size=fact.size, project=fact.project)
        for fact in facts
        if fact.project is not None and fact.project.startswith(composegen.PROJECT_PREFIX)
    )


def remove_finished_holders_on(
    image_id: str,
    project: str | None,
    *,
    listing: Callable[[str], tuple[docker.ContainerRef, ...] | None],
    remove: Callable[[str], str],
) -> int:
    """Remove every finished one-shot container of `project` holding `image_id` (T864-E).

    ALL or NOTHING: the image is removed only when EVERY container the daemon
    lists for it is a finished one-shot container of the image's own compose
    project. That is three rules per holder, and one failure removes nothing:

    * `state == "exited"` -- a running, paused, created or restarting container
      holds its image on purpose;
    * `project == project` -- the image's own `com.docker.compose.project`; an
      image id is not proof of ownership;
    * `service in composegen.ONE_SHOT_SERVICES` -- the generated services that
      run once and exit. A worldserver or authserver container is never one of
      them, whatever its state: removing it would end a server somebody runs.

    A holder with no project or no service label, a listing the daemon would not
    answer, and the image having no project at all all leave every container and
    the image alone. Removal is by `ref.id`, never by name: the next `compose
    up` can take a stopped container's name, and the id cannot be taken.

    **Why this is safe.** A one-shot service (`ac-db-import`,
    `ac-client-data-init`, ...) runs once and exits; its container keeps the
    pre-rebuild image referenced for ever because `compose up --force-recreate`
    never selects that service, and nothing else was going to remove it. It has
    no state of its own: what it wrote lives in the project's named volumes and
    databases, and `docker rm` without `-v` does not touch those. The next
    `compose up` recreates it from the current image if it is needed again.

    Returns:
        How many containers were actually removed. Zero means the caller leaves
        the image where it is, exactly as before this ticket.
    """
    if project is None:
        return 0
    listed = listing(image_id)
    if not listed:
        return 0
    holding = ", ".join(f"{ref.name}={ref.project!r}/{ref.service!r}/{ref.state}" for ref in listed)
    if any(ref.state != "exited" for ref in listed):
        logger.info(
            f"left the containers holding image {image_id}: not every one is finished ({holding})"
        )
        return 0
    if any(ref.project != project for ref in listed):
        logger.info(
            f"left the containers holding image {image_id}: not every one is of project {project} "
            f"({holding})"
        )
        return 0
    if any(ref.service not in composegen.ONE_SHOT_SERVICES for ref in listed):
        logger.info(
            f"left the containers holding image {image_id}: not every one is a one-shot service "
            f"({holding})"
        )
        return 0
    removed = 0
    for ref in listed:
        said = remove(ref.id)
        if said:
            logger.warning(f"could not remove the finished container {ref.name} ({ref.id}): {said}")
            continue
        logger.info(f"removed the finished container {ref.name}, which held old image {image_id}")
        removed += 1
    return removed


def read_space(*, kept_build: KeptBuildRoute | None, wsl_distro: str | None) -> Space:
    """Read what Docker holds, for the dialog. Never raises for Docker's answers (T864)."""
    cache = docker.build_cache_bytes(wsl_distro=wsl_distro)
    old = _old_builds(wsl_distro)
    kept = kept_build is not None and kept_build.check() is not None
    kept_bytes = kept_build.size() if kept and kept_build is not None else None
    return Space(
        cache_bytes=cache,
        old_builds=old,
        kept_build=kept,
        kept_build_bytes=kept_bytes,
    )


def build_press_refusal() -> str | None:
    """Why the press must not run now, or None when no build press runs here (T864 D2).

    Rebuild, Update and Return to the pin hold their server under their own
    names, and each compiles, so freeing beside one is refused with its own
    sentence. An install is seen through `docker.builds_running()` instead
    (`_staged(builds=True)`, T864 R3), never through the reservation's "Install"
    label: an install only takes its reservation at `start-db`, after `build`,
    while a CoA repair, update or adopt re-takes the hold under that same label
    with no compile at all, and naming it here refused the press with a
    sentence about an install building when nothing was (T864 review round 3).
    """
    from yulon.catalog import native  # noqa: PLC0415

    watched = {
        server_build_presses.REBUILD,
        server_build_presses.UPDATE_TO_LATEST,
        server_build_presses.RETURN_TO_PIN,
    }
    for press in docker.presses_running():
        if press in watched:
            return (
                f"“{press}” is running in this Yu'lon, and it builds with Docker. "
                "Wait for it to finish, then try again. Nothing was removed."
            )
    if docker.builds_running():
        return (
            f"“{native.INSTALL_PRESS}” is running in this Yu'lon, and it builds with Docker. "
            "Wait for it to finish, then try again. Nothing was removed."
        )
    return None


def carry_out(
    server_dir: Path,
    choice: Choice,
    *,
    kept_build: KeptBuildRoute | None,
    spec: docker.ContainerSpec | None,
    wsl_distro: str | None,
) -> Freed:
    """Free what `choice` ticks, if no build press runs and the lease is ours (T864).

    Worker-thread work. The reservation is asked about first (inspect only, so
    the refusal can name who holds the server), then the maintenance lease is
    taken and the build-press check runs AGAIN under it, and only then is
    anything removed.

    Raises:
        DockerSpaceRefusal: with nothing removed.
    """
    if not (choice.cache or choice.old_builds or choice.kept_build):
        return Freed(())
    refused = build_press_refusal()
    Refusal = _refusal_class()
    if refused is not None:
        raise Refusal(refused)
    if spec is not None:
        holder = docker.reservation_holder(server_dir, wsl_distro=wsl_distro)
        if holder is not None and not holder.here:
            raise _refusal_class()(_holder_refusal(holder, server_dir))
    try:
        with docker.maintenance_lease(
            server_dir,
            forgetting.FREE_DOCKER_SPACE_HOLDS,
            press=forgetting.PRESS_FREE_DOCKER_SPACE,
            spec=spec,
            wsl_distro=wsl_distro,
        ):
            refused = build_press_refusal()
            if refused is not None:
                raise _refusal_class()(refused)
            return _free_under_the_lease(choice, kept_build, wsl_distro)
    except (docker.MaintenanceLeaseTaken, docker.ServerHeldError) as exc:
        raise _refusal_class()(f"{exc} Nothing was removed.") from exc


def _holder_refusal(holder: docker.ServerHolder, server_dir: Path) -> str:
    label = server_dir.name or "this server"
    if holder.ours and not holder.here and holder.live_here() is not True:
        return forgetting.server_reservation_left(
            label, holder.name, forgetting.PRESS_FREE_DOCKER_SPACE
        )
    if not holder.known:
        return forgetting.server_reservation_unsaid(
            label, holder.name, forgetting.PRESS_FREE_DOCKER_SPACE
        )
    return forgetting.server_busy_elsewhere(
        label,
        holder.press,
        holder.since(),
        holder.who,
        forgetting.PRESS_FREE_DOCKER_SPACE,
    )


def _free_under_the_lease(
    choice: Choice,
    kept_build: KeptBuildRoute | None,
    wsl_distro: str | None,
) -> Freed:
    """Remove what `choice` ticks: old builds, then the kept build, then the cache (T864 D2)."""
    lines: list[str] = []
    removed_any = False
    if choice.old_builds:
        listed = _old_builds(wsl_distro)
        if listed is None:
            lines.append("Yu'lon's old untagged builds were not removed: Docker did not list them.")
        elif not listed:
            lines.append("Yu'lon's old untagged builds: there were none left to remove.")
        else:
            removed = 0
            freed_bytes = 0
            held = 0
            failed = 0
            for build in listed:
                said = docker.remove_unused_image(build.image_id, wsl_distro=wsl_distro)
                if said == docker.IMAGE_IN_USE:
                    # T864-E: a REBUILD never recreates the one-shot services (import,
                    # client-data), so their EXITED containers keep the old image
                    # referenced and every pass left it. Cleared here only when every
                    # holder is a finished one-shot container of this image's project, and
                    # then the image is asked for once more. Counted in the log, never in
                    # the player's line: a container is not space.
                    cleared = remove_finished_holders_on(
                        build.image_id,
                        build.project,
                        listing=lambda image: docker.containers_on(image, wsl_distro=wsl_distro),
                        remove=lambda name: docker.remove_finished_container(
                            name, wsl_distro=wsl_distro
                        ),
                    )
                    if cleared:
                        said = docker.remove_unused_image(build.image_id, wsl_distro=wsl_distro)
                if said == "" or said == docker.IMAGE_GONE:
                    if said == "":
                        removed += 1
                        freed_bytes += build.size
                elif said == docker.IMAGE_IN_USE:
                    held += 1
                    logger.debug(
                        f"{build.image_id} is still held by a container; left for a later pass"
                    )
                else:
                    failed += 1
                    logger.warning(
                        f"{build.image_id} could not be removed ({said}); left for a later pass"
                    )
            line = (
                f"Yu'lon's old untagged builds: removed {removed}, "
                f"up to {gb_text(freed_bytes)}."
            )
            if held:
                line += f" {held} still used by a container {'was' if held == 1 else 'were'} left."
            if failed:
                line += f" {failed} could not be removed; the log says why."
            lines.append(line)
            removed_any = removed_any or removed > 0
    if choice.kept_build and kept_build is not None:
        outcome = kept_build.remove()
        lines.append(outcome.line)
        removed_any = removed_any or outcome.ok
    if choice.cache:
        before = docker.build_cache_bytes(wsl_distro=wsl_distro)
        said = docker.prune_build_cache(wsl_distro=wsl_distro)
        if said:
            lines.append(f"Docker's build cache was not removed: {said}")
        else:
            after = docker.build_cache_bytes(wsl_distro=wsl_distro)
            if before is not None and after is not None and before >= after:
                lines.append(f"Docker's build cache: removed, {gb_text(before - after)} freed.")
            else:
                lines.append("Docker's build cache: removed.")
            removed_any = True
    if platform.detect() == "windows" and removed_any:
        lines.append(WINDOWS_NOTE)
    logger.info("; ".join(lines))
    return Freed(tuple(lines))


@dataclass(frozen=True)
class Seam:
    """The Maintenance tab's handle on one install's Docker space (T864)."""

    server_dir: Path
    spec: docker.ContainerSpec | None = None
    wsl_distro: str | None = None
    kept_build: KeptBuildRoute | None = None

    def read(self) -> Space:
        """What Docker holds now, for the dialog (T864)."""
        return read_space(kept_build=self.kept_build, wsl_distro=self.wsl_distro)

    def free(self, choice: Choice) -> Freed:
        """Free what `choice` ticks (T864)."""
        return carry_out(
            self.server_dir,
            choice,
            kept_build=self.kept_build,
            spec=self.spec,
            wsl_distro=self.wsl_distro,
        )
