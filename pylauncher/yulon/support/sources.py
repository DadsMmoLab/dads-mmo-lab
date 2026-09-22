"""What a support file is made of (T93): the files, the installs, and the passwords to remove.

Everything here READS. Writing the zip is `bundle.py`'s, and removing the
secrets is `redact.py`'s; this module only says where things are and which
values count as secrets. Every source is read on its own, so one that fails
becomes a line in the manifest rather than the end of the bundle.
"""

from __future__ import annotations

import os
import platform as host_platform
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import __version__, channel_setup, dbsecret, docker, log, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import Catalog, CatalogEntry
from yulon.state import KnownInstall
from yulon.support import runlog
from yulon.support.redact import database_info_passwords

FILE_CAP = 2 * 1024 * 1024
"""Each file keeps its last 2 MiB, a worldserver snapshot's budget (`logsnap.MAX_BYTES`)."""

LIVE_TAIL_LINES = 2000
LIVE_TIMEOUT_S = 20.0
"""Per `docker logs` read. Finding the container first is `docker.COMPOSE_PS_TIMEOUT`.
The first ask that takes its whole bound stops every later ask of that docker (see
`collect_live_logs`), so a wedged daemon costs one bound per docker, not per container."""

SKIPPED = "skipped: docker did not answer"
"""A container not asked about because its docker already ran into a bound this run."""

CONF_DIRS: tuple[str, ...] = ("etc", "etc/modules", "env/dist/etc", "env/dist/etc/modules")
"""Where an install's live confs are: CMaNGOS `etc/`, AzerothCore's `env/dist/etc/`.

A fixed list rather than the Tuning tab's (`ControllerView._tuning_files`): that
one belongs to a live tab, needs the manifests, and names only AzerothCore's
core files -- it never sees `mangosd.conf`, which is where CMaNGOS keeps its
`DatabaseInfo` lines.
"""

CREDENTIALS_DIR = "credentials"
"""`channel_setup.credential_path()`'s folder under the config dir."""

APP_LOG_BACKUPS = 3
"""`log.configure(backup_count=3)`: `yulon.log.1` to `.3`."""

_TRUNCATION_NOTICE = "[earlier lines dropped: Yu'lon keeps the last 2 MiB of this file]\n"


@dataclass(frozen=True)
class InstallFacts:
    """One remembered server, as a bundle needs it."""

    game: str
    install_id: str
    server_dir: Path
    wsl_distro: str | None
    entry: CatalogEntry | None
    """None when this version's catalog does not know the game (an old `state.json`)."""

    @property
    def label(self) -> str:
        """`<game>-<install id>`: how every per-install file of this app is named."""
        return f"{self.game}-{self.install_id}"


@dataclass(frozen=True)
class Sources:
    """Where everything is, decided once on the GUI thread and handed to the worker."""

    config_dir: Path
    app_log: Path | None
    installs: tuple[InstallFacts, ...]
    public_passwords: frozenset[str] = frozenset()
    """The catalog's fixed passwords. Published in `catalog.json`, so not secrets to hunt for."""
    qt_version: str = ""


@dataclass(frozen=True)
class Known:
    """The passwords to remove, and the stores that could not be read for them."""

    values: frozenset[str]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class LiveLog:
    """One container's fresh log tail, or why there is none."""

    container: str
    text: str | None
    problem: str = ""


@dataclass(frozen=True)
class Viewable:
    """One file the Logs tab can show."""

    label: str
    path: Path


def sources_for_app(
    installs: Sequence[KnownInstall], catalog: Catalog, *, qt_version: str = ""
) -> Sources:
    """The window's installs and this process's files, as a `Sources`.

    Takes the LIVE install list rather than calling `state.load_state()`, which
    moves a broken `state.json` aside -- a support button must never be the
    thing that loses the install list.
    """
    facts: list[InstallFacts] = []
    for install in installs:
        try:
            entry: CatalogEntry | None = catalog.get(install.game)
        except KeyError:
            entry = None
        facts.append(
            InstallFacts(
                game=install.game,
                install_id=composegen.install_id(install.server_dir),
                server_dir=install.server_dir,
                wsl_distro=install.wsl_distro,
                entry=entry,
            )
        )
    public = frozenset(
        value
        for entry in catalog.games
        if entry.install.password.mode == "fixed" and (value := entry.install.password.value)
    )
    config = platform.config_dir()
    return Sources(
        config_dir=config,
        app_log=app_log_path(config),
        installs=tuple(facts),
        public_passwords=public,
        qt_version=qt_version,
    )


def app_log_path(config_dir: Path) -> Path | None:
    """The live `yulon.log`, else the one in `config_dir` if it exists, else None."""
    live = log.file_path()
    if live is not None:
        return live
    fallback = config_dir / f"{log.APP_NAME}.log"
    return fallback if fallback.is_file() else None


def app_log_files(app_log: Path | None) -> list[Path]:
    """`yulon.log` and its rotated copies that exist, newest first."""
    if app_log is None:
        return []
    names = [app_log] + [
        app_log.with_name(f"{app_log.name}.{n}") for n in range(1, APP_LOG_BACKUPS + 1)
    ]
    return [path for path in names if path.is_file()]


def _newest_first(paths: Iterable[Path]) -> list[Path]:
    dated: list[tuple[float, Path]] = []
    for path in paths:
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _mtime, path in sorted(dated, key=lambda pair: pair[0], reverse=True)]


def _logs_in(folder: Path) -> list[Path]:
    """The `*.log` files directly in `folder`, newest first; none if it cannot be listed."""
    try:
        return _newest_first(path for path in folder.iterdir() if path.suffix == ".log")
    except OSError:
        return []


def run_logs(config_dir: Path) -> list[Path]:
    """Every kept run log, newest first."""
    return _logs_in(runlog.runs_dir(config_dir))


def snapshots(config_dir: Path) -> list[Path]:
    """Every worldserver snapshot (`logsnap`), newest first. Non-recursive: runs are apart."""
    return _logs_in(runlog.logs_dir(config_dir))


def _through_a_link(server_dir: Path, folder: str) -> bool:
    """Whether any folder from `server_dir` down to `server_dir/folder` is a link."""
    here = server_dir
    for part in Path(folder).parts:
        here = here / part
        if here.is_symlink():
            return True
    return False


def _conf_listing(server_dir: Path) -> tuple[list[Path], list[Path]]:
    """`CONF_DIRS`' `*.conf` files, split into real files and links. Raises `OSError`.

    A conf reached through a link -- the file itself, or a folder on the way to
    it -- is a link: Task 5's zip copies `conf_files()` by content, and a link
    could bring a file from anywhere on the machine into it.
    """
    files: list[Path] = []
    links: list[Path] = []
    for folder in CONF_DIRS:
        here = server_dir / folder
        if not here.is_dir():
            continue
        linked_folder = _through_a_link(server_dir, folder)
        for path in sorted(p for p in here.iterdir() if p.suffix == ".conf" and p.is_file()):
            (links if linked_folder or path.is_symlink() else files).append(path)
    return files, links


def conf_files(server_dir: Path) -> list[Path]:
    """This install's live `*.conf` files under `CONF_DIRS`, links left out. Raises `OSError`.

    `gather_known()` names every link it leaves out in `Known.missing`.
    """
    return _conf_listing(server_dir)[0]


def _secret(value: str | None) -> str | None:
    """`value` without surrounding whitespace, or None when nothing is left.

    `Redactor.build` keeps any value of four characters or more exactly as
    given, so a password read with its file's newline would never match the
    password as it appears in a log, and a blank one would mask every run of
    spaces.
    """
    stripped = value.strip() if value is not None else ""
    return stripped or None


def _kept_password(game: str, install_id: str, config_dir: Path) -> str | None:
    kept = dbsecret.recall(game, install_id, config_dir=config_dir)
    return kept.password if kept is not None else None


def _channel_password(game: str, install_id: str, config_dir: Path) -> str | None:
    endpoint = channel_setup.load_credential(game, install_id, config_dir=config_dir)
    return endpoint.password if endpoint is not None else None


def gather_known(sources: Sources) -> Known:
    """Every password this machine can tell us about. Never raises.

    `db-secrets/` (kept copies), `credentials/` (channel accounts), each
    generated install's `.db_password`, and the `DatabaseInfo` passwords in each
    install's confs, minus the catalog's public fixed values. Every value is
    stripped and a blank one dropped (see `_secret`). A store that cannot be
    read is named in `missing`; the patterns still run without it.
    """
    values: set[str] = set()
    missing: list[str] = []
    readers: tuple[tuple[str, Callable[[str, str, Path], str | None]], ...] = (
        (dbsecret.DIR_NAME, _kept_password),
        (CREDENTIALS_DIR, _channel_password),
    )
    for folder, read in readers:
        try:
            files = sorted(
                path for path in (sources.config_dir / folder).iterdir() if path.suffix == ".json"
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            missing.append(f"{folder}/ could not be listed ({type(exc).__name__})")
            continue
        for path in files:
            game, _, install_id = path.stem.rpartition("-")
            password = _secret(read(game, install_id, sources.config_dir))
            if password is not None:
                values.add(password)
            else:
                missing.append(f"{folder}/{path.name} could not be read")
    for install in sources.installs:
        entry = install.entry
        if entry is not None and entry.install.password.mode == "generated":
            generated = _secret(entry.install.db_password(install.server_dir))
            if generated is not None:
                values.add(generated)
            else:
                missing.append(
                    f"{install.label}: its generated database password could not be read"
                )
        try:
            confs, links = _conf_listing(install.server_dir)
        except OSError as exc:
            missing.append(
                f"{install.label}: its conf folders could not be listed "
                f"({exc.strerror or type(exc).__name__})"
            )
            continue
        missing += [
            f"{install.label}: {path.relative_to(install.server_dir).as_posix()} "
            "is a link, not a file: left out"
            for path in links
        ]
        # A link is still READ for its passwords: masking more is harmless, and
        # the value it holds may well turn up in a log that is bundled.
        for path in confs + links:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                missing.append(f"{install.label}: {path.name} could not be read")
                continue
            values.update(
                found
                for value in database_info_passwords(text)
                if (found := _secret(value)) is not None
            )
    return Known(values=frozenset(values - sources.public_passwords), missing=tuple(missing))


def _room(limit: int) -> int:
    """How many bytes of the file fit beside the notice. At least one."""
    return max(limit - len(_TRUNCATION_NOTICE.encode("utf-8")), 1)


def _cut(data: bytes) -> str:
    """Bytes from the middle of a file, as text starting on a whole line, with a notice.

    `errors="ignore"` because a byte cut can land inside a multi-byte
    character; the partial line it produces is then dropped with `partition`
    (`logsnap._trim`'s reasoning).
    """
    tail = data.decode("utf-8", errors="ignore")
    _, newline, whole = tail.partition("\n")
    return _TRUNCATION_NOTICE + (whole if newline else tail)


def read_tail(path: Path, limit: int = FILE_CAP) -> str:
    """The file's last `limit` bytes as text, never cutting a line. Raises `OSError`.

    Cut at a line boundary BEFORE redaction, so a secret is never split across
    the cut and left half-recognisable.
    """
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size <= limit:
            handle.seek(0)
            return handle.read().decode("utf-8", errors="replace")
        handle.seek(size - _room(limit))
        return _cut(handle.read())


def keep_tail(text: str, limit: int = FILE_CAP) -> str:
    """`read_tail()` for text already in memory (a container's log)."""
    data = text.encode("utf-8")
    return text if len(data) <= limit else _cut(data[len(data) - _room(limit) :])


def collect_live_logs(
    install: InstallFacts,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    silent_targets: set[str | None] | None = None,
) -> list[LiveLog]:
    """A fresh `docker logs --tail 2000` of each of this install's containers.

    Found through the install's own compose project, as `logsnap.capture()`
    does, because AzerothCore-derived games share container names across
    installs. A container that cannot be found or read is a `LiveLog` with a
    problem, never an exception. An install this catalog does not know has no
    container names to ask for, and gives none.

    A wedged daemon is when people make a support file, and every ask of one
    costs a whole bound. So the first ask that takes its whole bound marks the
    docker it went to -- this machine's (`None`) or one WSL distro's -- in
    `silent_targets`, and every later container on that docker is `SKIPPED`
    without asking. Pass one set for every install of a bundle; the default is
    a fresh set, which still spares the rest of this install.
    """
    if install.entry is None:
        return []
    silent = silent_targets if silent_targets is not None else set()
    target = install.wsl_distro
    spec = install.entry.container_spec()
    found: list[LiveLog] = []
    for name in (spec.world, spec.auth, spec.db):
        if target in silent:
            found.append(LiveLog(name, None, SKIPPED))
            continue
        started = monotonic()
        container = docker.compose_container_id(
            spec.service_for(name), install.server_dir, wsl_distro=target
        )
        took = monotonic() - started
        if container is None and took >= docker.COMPOSE_PS_TIMEOUT:
            silent.add(target)
            found.append(
                LiveLog(
                    name,
                    None,
                    f"timed out after {docker.COMPOSE_PS_TIMEOUT:.0f} s finding its container",
                )
            )
            continue
        if container is None:
            found.append(
                LiveLog(
                    name,
                    None,
                    "docker did not name a container for it here (docker not reachable, "
                    "or this server was never started)",
                )
            )
            continue
        started = monotonic()
        text = docker.log_tail(
            container, LIVE_TAIL_LINES, wsl_distro=target, timeout=LIVE_TIMEOUT_S
        )
        if text is None:
            if monotonic() - started >= LIVE_TIMEOUT_S:
                silent.add(target)
                found.append(LiveLog(name, None, f"timed out after {LIVE_TIMEOUT_S:.0f} s"))
            else:
                found.append(LiveLog(name, None, "docker could not read its log"))
            continue
        found.append(LiveLog(name, text))
    return found


def docker_version(wsl_distro: str | None) -> str | None:
    """The daemon's version on this machine or inside `wsl_distro`, or None."""
    return docker.server_version(wsl_distro=wsl_distro, timeout=LIVE_TIMEOUT_S)


def _ask_version(ask: Callable[[str | None], str | None], distro: str | None) -> str:
    try:
        answer = ask(distro)
    except Exception as exc:  # boundary: system-info.txt is always written
        return f"not reachable ({type(exc).__name__})"
    return answer or "not reachable"


def system_info(sources: Sources, docker_version: Callable[[str | None], str | None]) -> str:
    """`system-info.txt`: versions, Docker, and every install in place of `state.json`."""
    lines = [
        f"Yu'lon {__version__}",
        f"Operating system: {host_platform.platform()}",
        f"Python: {sys.version.split()[0]}",
        f"Qt: {sources.qt_version or 'not reported'}",
        f"Docker on this machine: {_ask_version(docker_version, None)}",
    ]
    distros = sorted({distro for install in sources.installs if (distro := install.wsl_distro)})
    lines += [
        f"Docker in WSL distro {distro}: {_ask_version(docker_version, distro)}"
        for distro in distros
    ]
    lines += ["", f"Servers Yu'lon knows about: {len(sources.installs)}"]
    for install in sources.installs:
        where = f", WSL distro {install.wsl_distro}" if install.wsl_distro else ""
        unknown = "" if install.entry is not None else " (not in this version's catalog)"
        folder = f"folder {install.server_dir}{where}{unknown}"
        lines.append(f"  {install.game}, id {install.install_id}, {folder}")
    return "\n".join(lines) + "\n"


def viewables(sources: Sources) -> list[Viewable]:
    """What the Logs tab offers: the app log, then runs, then snapshots, newest first."""
    items = [Viewable(f"App log ({path.name})", path) for path in app_log_files(sources.app_log)]
    items += [Viewable(f"Run: {path.name}", path) for path in run_logs(sources.config_dir)]
    items += [
        Viewable(f"Worldserver snapshot: {path.name}", path)
        for path in snapshots(sources.config_dir)
    ]
    return items
