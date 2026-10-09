"""The worldserver log, saved before every stop, remove and uninstall (Phase 8.1a).

Why this exists at all. Stopping a server is the ordinary way to end a session,
and `remove()` and the uninstall take the container with them — and the
container's log with it. That log is the only account of *why* a server was
crash-looping, so the one moment the app must capture it is the moment before it
is destroyed. The Hypeer launcher records the same lesson from the other side:
its users hit a server that had been failing for a week with nothing left to
read.

Two rules this module exists to keep, both of them about not making things
worse:

* **It never prevents a stop.** Every failure here comes back as a `problem`
  string on the `Snapshot`, never as an exception, and every docker call is
  bounded by a timeout. A stop the user asked for happens whether or not the
  evidence could be collected.
* **It never prunes the file it just wrote.** Retention that sorts by
  modification time and deletes the oldest will delete *this* snapshot when the
  clock has gone backwards (a VM resumed from a checkpoint, an NTP step). The
  file just written is excluded by identity, not by its timestamp.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from yulon import docker
from yulon.catalog import composegen
from yulon.log import get_logger

if TYPE_CHECKING:
    from yulon.support.redact import Redactor

logger = get_logger(__name__)


MAX_BYTES = 2 * 1024 * 1024
"""The most a snapshot may occupy on disk, applied AFTER the line cap.

The line cap is not a size cap. `docker logs --tail 2000` bounds the number of
lines and says nothing about their length, and the lines a crash writes are the
long ones — a SQL error carries its whole statement, and a stack trace carries
every frame. Two thousand of those is megabytes, written into the user's config
directory, once per stop, forever.

Two MiB is past any plausible startup banner and still small enough to open in a
text editor and to attach to a bug report, which is what these files are for.
"""

KEEP = 10
"""How many snapshots are kept per install.

Per install, not per directory: two installs share one logs directory, and a
server that is stopped every day must not be able to evict the evidence from the
one that failed last month.
"""

_TRUNCATION_NOTICE = "[earlier lines dropped: this snapshot keeps the END of a longer log]\n"
"""What a truncated snapshot says at the top, in bytes rather than in lines.

It does not claim a number. The obvious wording — "the last N bytes" — would be
wrong by the length of the partial line the cut lands in and by the notice
itself, and a file whose first line is a slightly false measurement is worse
than one that says plainly which end it kept.
"""


@dataclass(frozen=True)
class Snapshot:
    """Where the log was saved, or why it was not.

    `problem` is empty exactly when `path` is set: a caller shows one or the
    other, and never has to decide which of two half-answers to believe.
    """

    path: Path | None = None
    problem: str = ""


SNAPSHOT_READ_TIMEOUT = 30.0
"""Seconds the stop-time read of the world container may take."""


def capture(
    spec: docker.ContainerSpec,
    server_dir: Path,
    *,
    game: str,
    logs_dir: Path,
    wsl_distro: str | None = None,
) -> Snapshot:
    """Save this install's worldserver log; never raise.

    The container is resolved through this install's own compose project rather
    than by name, because every AzerothCore-derived game names its containers
    identically: asking by name on a machine with two installs of one game can
    save the wrong server's log under this server's name.
    """
    container, said = docker.compose_container_lookup(
        spec.service_for(spec.world), server_dir, wsl_distro=wsl_distro
    )
    if said:
        # Not "could not find": Docker did not answer, which says nothing about
        # whether the container is there (T211 5: it timed out mid-stop).
        return Snapshot(problem=f"Docker did not say which container is {spec.world}: {said}")
    if not container:
        return Snapshot(problem=f"could not find {spec.world} in the compose project here")
    # Both streams (T350): a container without a tty writes its errors to stderr.
    raw = docker.last_lines(
        container, docker.LOG_TAIL_LINES, wsl_distro=wsl_distro, timeout=SNAPSHOT_READ_TIMEOUT
    )
    if raw is None:
        # The clause this box was ticked on: "a snapshot that fails or hangs is
        # reported and the stop still happens". The fail arm was gated live; the
        # hang arm shipped as a zero-byte file called a snapshot, because a
        # failed read came back as an empty log (audit, 2026-09-08).
        return Snapshot(
            problem=(
                f"could not read {spec.world}'s log, so no snapshot was saved -- the "
                f"stop goes ahead without it"
            )
        )
    text = _trim(_masked(raw, server_dir, game, wsl_distro, logs_dir))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{game}-{composegen.install_id(server_dir)}"
    target = logs_dir / f"{prefix}-{stamp}.log"
    partial = target.with_name(target.name + ".partial")
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, target)
    except OSError as exc:
        # Not a raise: this runs in front of a stop the user asked for. The
        # cleanup goes through `_discard()` because the tidy-up in a handler can
        # fail for the same reason the write did, and an exception raised while
        # handling one is the exception that escapes.
        _discard(partial)
        logger.warning(f"could not save {spec.world}'s log to {target}: {exc}")
        return Snapshot(problem=f"could not save the server's log to {target}: {exc}")
    logger.info(f"saved {spec.world}'s log to {target}")
    _prune(logs_dir, prefix, keep=target)
    return Snapshot(path=target)


def _masked(text: str, server_dir: Path, game: str, wsl_distro: str | None, logs_dir: Path) -> str:
    """`text` with this install's passwords masked, as the support zip masks them (T352).

    The file stays on disk and is attached to bug reports, and since T350 it also
    holds stderr (wsl.exe and docker CLI warnings). Gathering never raises: if the known
    values cannot be gathered, the patterns alone still run, and a stop is never
    prevented over redaction.
    """
    # Lazy: `yulon.support` imports modules that import this one.
    from yulon.support import sources
    from yulon.support.redact import Redactor

    values: frozenset[str] = frozenset()
    homes: list[str] = sources.other_homes(
        [sources.InstallFacts("", "", server_dir, wsl_distro, None)]
    )
    try:
        from yulon.catalog.catalog import load_catalog

        catalog = load_catalog()
        try:
            entry = catalog.get(game)
        except KeyError:
            entry = None
        facts = sources.InstallFacts(
            game=game,
            install_id=composegen.install_id(server_dir),
            server_dir=server_dir,
            wsl_distro=wsl_distro,
            entry=entry,
        )
        # `logs_dir` is `<config>/logs` (`support.runlog.logs_dir`).
        values = sources.gather_known(
            sources.Sources(
                config_dir=logs_dir.parent,
                app_log=None,
                installs=(facts,),
                public_passwords=sources.public_passwords(catalog),
            )
        ).values
    except Exception as exc:  # noqa: BLE001 - redaction must never stop a stop
        logger.warning(f"could not gather this install's passwords for the snapshot: {exc}")
    return _vouched(Redactor.build(values, home=Path.home(), also_home=homes), text)


_REMOVED_LINE = "[a line was removed: Yu'lon could not be sure it was free of passwords]\n"
"""Stands where a line the cleaner could not vouch for was (T606)."""
_REMOVED_ALL = (
    "[this log was removed: Yu'lon could not be sure it was free of passwords. "
    "Read it in the container with docker logs.]\n"
)


def _vouched(redactor: Redactor, text: str) -> str:
    """`redactor.checked(text)`, line by line when the whole text is refused (T606).

    The file stays on disk and is attached to bug reports by hand, so it is as strict
    as the support zip (T595): a line the cleaner half-masked is replaced by a marker,
    never written. If even the lines together are refused, the whole log goes.
    """
    from yulon.support.redact import Unredactable

    try:
        return redactor.checked(text)
    except Unredactable:
        pass
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        try:
            kept.append(redactor.checked(line))
        except Unredactable:
            kept.append(_REMOVED_LINE)
    joined = "".join(kept)
    try:
        return redactor.checked(joined)
    except Unredactable:
        return _REMOVED_ALL


class Recorder:
    """A `capture()` bound to one install, which remembers its own last answer.

    `Controller.pre_stop` is a plain callable on purpose — the controller must
    not learn where a snapshot goes or that this module exists. The tab, though,
    wants to name the file that was just written, and the controller's return
    value is about stopping rather than about evidence. So the callable the
    wiring hands down keeps its own answer, and the tab reads it after the stop
    job has finished.

    Never raises: `capture()` answers with a `problem` rather than an exception,
    and `Controller._save_evidence()` swallows even the unexpected.
    """

    def __init__(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        *,
        game: str,
        logs_dir: Path,
        wsl_distro: str | None = None,
    ) -> None:
        self.spec = spec
        self.server_dir = server_dir
        self.game = game
        self.logs_dir = logs_dir
        self.wsl_distro = wsl_distro
        self.last: Snapshot | None = None

    def __call__(self) -> Snapshot:
        self.last = capture(
            self.spec,
            self.server_dir,
            game=self.game,
            logs_dir=self.logs_dir,
            wsl_distro=self.wsl_distro,
        )
        return self.last


def _discard(path: Path) -> None:
    """Remove a half-written file, and never raise while doing it.

    `unlink(missing_ok=True)` is not enough on its own. When the failure being
    cleaned up is "the logs directory is a file", the partial's own PARENT is
    that file, and Linux answers the unlink with `NotADirectoryError` rather
    than the `FileNotFoundError` `missing_ok` swallows — so the handler raised
    out of a function whose whole contract is that it does not. Windows answers
    the same situation differently, which is why the local run was green and CI
    was red (2026-09-06).
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug(f"could not remove {path}: {exc}")


def _trim(text: str) -> str:
    """Cut `text` to `MAX_BYTES`, keeping its END and starting on a whole line."""
    data = text.encode("utf-8")
    if len(data) <= MAX_BYTES:
        return text
    notice = _TRUNCATION_NOTICE
    room = MAX_BYTES - len(notice.encode("utf-8"))
    # `errors="ignore"` because a byte cut can land inside a multi-byte
    # character; the partial line it produces is then dropped with `partition`.
    tail = data[-room:].decode("utf-8", errors="ignore")
    _, newline, whole = tail.partition("\n")
    return notice + (whole if newline else tail)


def _prune(logs_dir: Path, prefix: str, *, keep: Path) -> None:
    """Keep the newest `KEEP` snapshots of this install, and always `keep`.

    `keep` is excluded by identity rather than by being newest. Sorting by
    modification time and deleting the oldest is the obvious implementation and
    it deletes the file it has just written whenever the clock has gone
    backwards — a VM resumed from a checkpoint, an NTP step, a container host
    whose time was wrong until it synced. The Rust launcher shipped that bug
    (`logsnap.rs:183-190`); the fix is not to trust the clock for a fact that is
    already known.
    """
    try:
        others = [p for p in logs_dir.glob(f"{prefix}-*.log") if p != keep]
        others.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in others[KEEP - 1 :]:
            stale.unlink()
    except OSError as exc:
        # Retention failing is untidy; failing a stop over it is not acceptable.
        logger.warning(f"could not prune old snapshots in {logs_dir}: {exc}")
