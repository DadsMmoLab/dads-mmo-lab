"""One zip to send when something is wrong (T93).

`save()` gathers the known passwords, builds a `Redactor`, and hands both to
`build()`, which reads every source on its own -- the app log, the run logs,
the snapshots, a fresh tail of each container's log, each install's confs --
redacts each one and its NAME, and writes:

    MANIFEST.txt   what is in the zip, what was skipped and why, what was left out for size
    system-info.txt
    app/  runs/  snapshots/  live/  conf/

**One bad source never kills the bundle.** Each read is its own try, and a
failure is a manifest line, so a missing log never reads as "nothing was wrong".
**A wedged docker is asked once per bundle**: one `silent_targets` set goes to
every install's `collect_live_logs` and then to `system_info`, so a daemon that
ran into a bound is not asked again by a later install or for its version.
**It stays under 8 MB** (Discord's free limit is 10): each file keeps its last
2 MiB, and over the cap the oldest snapshots go first, then the oldest runs;
then what is left is cut shorter, its END kept -- the container logs first,
then the app log's rotations, the app log, and last the confs -- halving the
largest of the earliest kind each time, never below `MIN_TAIL` (Codex T93
review: dropping alone left a zip twice the cap). Every drop and cut is a
manifest line. Only when every file is at its floor is a zip written over the
cap, and then the manifest and the tab say so.
**A very short password is named, never promised away**: one under the
redactor's `TOKEN_FLOOR` is masked only where a password is written, so the
manifest says where one is set and asks the user to look before sharing.
**It is replaced atomically**: built in memory, written to `<dest>.partial`,
`os.replace`d -- so a failure leaves the previous file, never half of one.
Runs on a worker thread; nothing here touches Qt.
"""

from __future__ import annotations

import io
import os
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from yulon import __version__
from yulon.log import get_logger
from yulon.support import runlog
from yulon.support import sources as src
from yulon.support.redact import TOKEN_FLOOR, Redactor
from yulon.support.sources import InstallFacts, LiveLog, Sources

logger = get_logger(__name__)

ZIP_CAP = 8_000_000
"""Bytes. Discord's free upload limit is 10 MB; this leaves room for its own overhead."""

MIN_TAIL = 32 * 1024
"""Bytes. A file is never cut shorter than this to fit the cap: its last lines are the point."""

_ENTRY_OVERHEAD = 128
"""Local header + central directory record per member, rounded up, for the size estimate."""

_NO_SERVERS = "Yu'lon knows no servers"

_WHAT: dict[str, str] = {
    "app": "Yu'lon's own log",
    "runs": "an install or rebuild, as it ran",
    "snapshots": "worldserver log saved when the server was stopped, removed or uninstalled",
    "live": f"container log, last {src.LIVE_TAIL_LINES} lines",
    "conf": "server settings file",
    "info": "versions, Docker, and the servers Yu'lon knows",
}
"""What each kind of member is, for its `MANIFEST.txt` line.

A container's log is read whether or not the container runs (a stopped one
after a crash is the point), so its line says what it is and never that it ran.
"""

_CUTTABLE = frozenset({"live", "app", "conf"})
"""The groups `_fit()` may cut shorter; their members keep their text before redaction."""

SilentTargets = set[str | None]
"""The dockers (`None` = this machine's, else a WSL distro) that already ran into a bound."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _collect_live_logs(install: InstallFacts, silent: SilentTargets) -> list[LiveLog]:
    """`src.collect_live_logs`, with the bundle's one `silent_targets` set."""
    return src.collect_live_logs(install, silent_targets=silent)


@dataclass(frozen=True)
class Seams:
    """The docker reads and the clock. Real by default; a test hands its own over.

    `live_logs` is given the bundle's one `silent_targets` set with every
    install, and `system_info` reads the same set afterwards.
    """

    live_logs: Callable[[InstallFacts, SilentTargets], list[LiveLog]] = _collect_live_logs
    docker_version: Callable[[str | None], str | None] = src.docker_version
    now: Callable[[], datetime] = _utc_now


@dataclass(frozen=True)
class BundleReport:
    """What was written. Every string in it but `path` is already redacted."""

    path: Path
    included: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]
    dropped: tuple[str, ...]
    size: int
    cut: tuple[str, ...] = ()
    """Members cut shorter than their 2 MiB to fit the cap."""
    short_passwords: tuple[str, ...] = ()
    """Where a password too short to take out of free text is set (`sources.Known.short`)."""


@dataclass(frozen=True)
class _Member:
    name: str
    data: bytes
    group: str
    mtime: float = 0.0
    raw: str | None = None
    """The text before redaction, for `_shorter()`: a cut is made BEFORE redaction, on
    a line boundary, so a secret is never split and left half-recognisable."""


def _why(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "file vanished before it could be read"
    if isinstance(exc, PermissionError):
        return "not readable (permission denied)"
    return f"could not be read ({exc.strerror or type(exc).__name__})"


class _Collector:
    """Members and skips, every string through the redactor on the way in."""

    def __init__(self, redactor: Redactor) -> None:
        self._redact = redactor.redact
        self.members: list[_Member] = []
        self.skipped: list[tuple[str, str]] = []
        self._names: set[str] = set()

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((self._redact(name), self._redact(why)))

    def text(self, name: str, text: str, group: str, mtime: float = 0.0) -> None:
        data = self._redact(text).encode("utf-8")
        name = self._unique(self._redact(name))
        raw = text if group in _CUTTABLE else None
        self.members.append(_Member(name, data, group, mtime, raw=raw))

    def _unique(self, name: str) -> str:
        """`name`, or `name` with ` (2)` before its suffix when that is already taken.

        Two names that differed only in a secret are the same name once it is
        masked, and a zip with two members of one name opens as either.
        """
        stem, dot, suffix = name.rpartition(".")
        if not dot or "/" in suffix:
            stem, dot, suffix = name, "", ""
        candidate, n = name, 1
        while candidate in self._names:
            n += 1
            candidate = f"{stem} ({n}){dot}{suffix}"
        self._names.add(candidate)
        return candidate

    def file(self, name: str, path: Path, group: str) -> None:
        try:
            mtime = path.stat().st_mtime
            text = src.read_tail(path)
        except OSError as exc:
            self.skip(name, _why(exc))
            return
        self.text(name, text, group, mtime)

    def files(self, group: str, paths: Sequence[Path], *, empty: str = "") -> None:
        if not paths and empty:
            self.skip(f"{group}/", empty)
        for path in paths:
            self.file(f"{group}/{path.name}", path, group)

    def logs(
        self, group: str, folder: Path, list_logs: Callable[[], list[Path]], *, empty: str
    ) -> None:
        """`files()` of a folder of the app's logs. One it cannot list is named, never empty."""
        try:
            paths = list_logs()
        except OSError as exc:
            self.skip(
                f"{group}/", f"{folder} could not be listed ({exc.strerror or type(exc).__name__})"
            )
            return
        self.files(group, paths, empty=empty)

    def live(
        self,
        install: InstallFacts,
        ask: Callable[[InstallFacts, SilentTargets], list[LiveLog]],
        silent: SilentTargets,
    ) -> None:
        if install.entry is None:
            self.skip(f"live/{install.label}", f"{install.game} is not in this version's catalog")
            return
        try:
            logs = ask(install, silent)
        except Exception as exc:  # boundary: one bad source never kills the bundle
            why = f"docker not reachable ({type(exc).__name__}: {exc})"
            self.skip(f"live/{install.label}", why)
            return
        for live in logs:
            name = f"live/{install.label}-{live.container}.log"
            if live.text is None:
                self.skip(name, live.problem or "docker could not read its log")
            else:
                self.text(name, src.keep_tail(live.text), "live")

    def confs(self, install: InstallFacts) -> None:
        folder = f"conf/{install.label}/"
        try:
            paths = src.conf_files(install.server_dir)
        except OSError as exc:
            self.skip(folder, _why(exc))
            return
        if not paths:
            self.skip(folder, "no .conf files under etc/ or env/dist/etc/")
        for path in paths:
            rel = path.relative_to(install.server_dir).as_posix()
            self.file(f"{folder}{rel}", path, "conf")


def save(
    dest: Path,
    sources: Sources,
    *,
    seams: Seams | None = None,
    home: Path | None = None,
    cap_bytes: int = ZIP_CAP,
) -> BundleReport:
    """Gather the known passwords, then `build()`. Raises `OSError` only if `dest` is unwritable.

    `gather_known`'s `missing` lines -- a store that could not be read, a conf
    left out because it is a link -- go to `MANIFEST.txt` as `build()`'s `gaps`,
    and its `short` places as `build()`'s `short`.
    """
    known = src.gather_known(sources)
    redactor = Redactor.build(known.values, home=home if home is not None else Path.home())
    return build(
        dest,
        sources,
        redactor,
        seams=seams,
        gaps=known.missing,
        short=known.short,
        cap_bytes=cap_bytes,
    )


def build(
    dest: Path,
    sources: Sources,
    redactor: Redactor,
    *,
    seams: Seams | None = None,
    gaps: Sequence[str] = (),
    short: Sequence[str] = (),
    cap_bytes: int = ZIP_CAP,
) -> BundleReport:
    """Collect every source independently, redact, fit under the cap, replace `dest`.

    `short` names where a password under `TOKEN_FLOOR` is set; the manifest
    then warns instead of promising every password is gone.
    """
    seams = seams if seams is not None else Seams()
    collector = _Collector(redactor)
    collector.files("app", src.app_log_files(sources.app_log))
    if sources.app_log is None:
        collector.skip("app/yulon.log", "this session keeps no log file")
    config = sources.config_dir
    collector.logs(
        "runs",
        runlog.runs_dir(config),
        lambda: src.run_logs(config),
        empty="no install or rebuild has been recorded yet",
    )
    collector.logs(
        "snapshots",
        runlog.logs_dir(config),
        lambda: src.snapshots(config),
        empty="no server has been stopped, removed or uninstalled since snapshots began",
    )
    if not sources.installs:
        collector.skip("live/", _NO_SERVERS)
        collector.skip("conf/", _NO_SERVERS)
    silent: SilentTargets = set()
    for install in sources.installs:
        collector.live(install, seams.live_logs, silent)
        collector.confs(install)
    # After the live logs, so a docker that went silent there is not asked for its version.
    info = src.system_info(sources, seams.docker_version, silent_targets=silent)
    collector.text("system-info.txt", info, "info")
    stamp = seams.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    def manifest(fit: _Fit, over: bool) -> _Member:
        text = _manifest_text(stamp, fit, collector.skipped, gaps, short, cap_bytes, over=over)
        return _Member("MANIFEST.txt", redactor.redact(text).encode("utf-8"), "manifest")

    fit, data = _fit(collector.members, cap_bytes, manifest, redactor.redact)
    _write_atomically(dest, data)
    logger.info(f"support file written: {len(fit.kept)} files, {len(data)} bytes")
    return BundleReport(
        path=dest,
        included=tuple(member.name for member in fit.kept),
        skipped=tuple(collector.skipped),
        dropped=tuple(member.name for member in fit.dropped),
        size=len(data),
        cut=tuple(fit.cuts),
        short_passwords=tuple(redactor.redact(where) for where in short),
    )


def short_password_warning(where: Sequence[str]) -> str:
    """One paragraph for the Logs tab's status line: where a very short password is set.

    Never the value. `where` is `sources.Known.short`, already free of it.
    """
    return (
        f"A very short password (under {TOKEN_FLOOR} characters) is set for "
        f"{', '.join(where)}. Yu'lon takes it out where a password is written, but not "
        "from ordinary log lines, where it would take out everyday words too: look for it "
        "before you share anything from here, and consider a longer password."
    )


def _manifest_text(
    stamp: str,
    fit: _Fit,
    skipped: Sequence[tuple[str, str]],
    gaps: Sequence[str],
    short: Sequence[str],
    cap: int,
    *,
    over: bool,
) -> str:
    lines = [f"Yu'lon support file, made {stamp} by Yu'lon {__version__}."]
    if short:
        lines += [
            "",
            f"WARNING: a very short password (under {TOKEN_FLOOR} characters) is set for:",
            *(f"  {where}" for where in short),
            "Yu'lon took it out wherever a password is written (settings, database lines),",
            "but not from ordinary log lines, where it would take out everyday words too.",
            "Look through this file for it before you share it, and consider setting a",
            "longer password.",
            "",
            f"Passwords Yu'lon knows of that are {TOKEN_FLOOR} characters or longer, anything",
            "shaped like a password, and your home folder were replaced with *** and ~",
            "before this file was written.",
        ]
    else:
        lines += [
            "Every password Yu'lon knows of, anything shaped like one, and your home folder",
            "were replaced with *** and ~ before this file was written.",
        ]
    lines += ["", "Included:"]
    lines += [
        f"  {m.name}  ({_WHAT[m.group]}{', cut shorter' if m.name in fit.cuts else ''}, "
        f"{len(m.data)} bytes)"
        for m in fit.kept
    ] or ["  nothing"]
    lines += ["", "Skipped, with the reason:"]
    lines += [f"  {name}  {why}" for name, why in skipped] or ["  nothing"]
    megabytes = cap // 1_000_000
    if fit.dropped:
        lines += ["", f"Left out to keep the file under {megabytes} MB, oldest first:"]
        lines += [f"  {m.name}" for m in fit.dropped]
    if fit.cuts:
        lines += [
            "",
            f"Cut shorter to keep the file under {megabytes} MB (the END of each is kept):",
        ]
        lines += [f"  {name}  last {size} bytes kept" for name, size in sorted(fit.cuts.items())]
    if over:
        lines += [
            "",
            "Still larger than the cap after leaving out every snapshot and run log and cutting",
            f"every other file down to its last {MIN_TAIL // 1024} KiB.",
        ]
    if gaps:
        lines += ["", "Noticed while gathering the passwords to remove (the patterns still ran):"]
        lines += [f"  {gap}" for gap in gaps]
    return "\n".join(lines) + "\n"


def _estimate(member: _Member) -> int:
    return len(zlib.compress(member.data, 6)) + _ENTRY_OVERHEAD + 2 * len(member.name.encode())


def _zip(members: Sequence[_Member]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for member in members:
            archive.writestr(member.name, member.data)
    return buffer.getvalue()


@dataclass
class _Fit:
    """What `_fit()` kept, left out and cut, as it goes."""

    kept: list[_Member]
    dropped: list[_Member]
    cuts: dict[str, int]
    """Member name -> the bytes of its text still kept, in the order first cut."""


def _cut_tier(member: _Member) -> int | None:
    """Which files are cut shorter first: container logs, then the app log's rotations,
    then the app log itself, then the confs. None: never cut (dropped whole, or tiny)."""
    if member.group == "live":
        return 0
    if member.group == "app":
        return 1 if member.name.rpartition(".")[2].isdigit() else 2
    if member.group == "conf":
        return 3
    return None


def _raw_size(member: _Member) -> int:
    return len(member.raw.encode("utf-8")) if member.raw is not None else 0


def _next_cut(kept: Sequence[_Member], weight: Mapping[str, int]) -> int | None:
    """Index of the heaviest member of the earliest tier that can still be cut, or None."""
    candidates = [
        (tier, -weight[member.name], index)
        for index, member in enumerate(kept)
        if (tier := _cut_tier(member)) is not None and _raw_size(member) > MIN_TAIL
    ]
    return min(candidates)[2] if candidates else None


def _shorter(member: _Member, redact: Callable[[str], str]) -> _Member:
    """`member` keeping half its text (never under `MIN_TAIL`), cut BEFORE redaction."""
    assert member.raw is not None  # `_next_cut` picks only members that have it
    limit = max(_raw_size(member) // 2, MIN_TAIL)
    notice = (
        f"[earlier lines dropped: Yu'lon kept the last {limit // 1024} KiB of this file "
        "to keep the support file under its size cap]\n"
    )
    text = src.keep_tail(member.raw, limit, notice=notice)
    return replace(member, data=redact(text).encode("utf-8"), raw=text)


def _fit(
    members: list[_Member],
    cap: int,
    manifest: Callable[[_Fit, bool], _Member],
    redact: Callable[[str], str],
) -> tuple[_Fit, bytes]:
    """Leave out the oldest snapshots, then the oldest runs, then cut the rest shorter,
    until the zip fits `cap` or nothing can give any more."""
    queue = sorted((m for m in members if m.group == "snapshots"), key=lambda m: m.mtime)
    queue += sorted((m for m in members if m.group == "runs"), key=lambda m: m.mtime)
    weight = {m.name: _estimate(m) for m in members}
    fit = _Fit(kept=list(members), dropped=[], cuts={})

    def give() -> bool:
        """Drop or cut one member. False when nothing is left to give."""
        if queue:
            victim = queue.pop(0)
            fit.kept.remove(victim)
            fit.dropped.append(victim)
            return True
        index = _next_cut(fit.kept, weight)
        if index is None:
            return False
        shorter = _shorter(fit.kept[index], redact)
        fit.kept[index] = shorter
        weight[shorter.name] = _estimate(shorter)
        fit.cuts[shorter.name] = _raw_size(shorter)
        return True

    def estimated() -> int:
        return sum(weight[m.name] for m in fit.kept) + _estimate(manifest(fit, False))

    while estimated() > cap and give():
        pass
    while True:
        data = _zip([manifest(fit, False), *fit.kept])
        if len(data) <= cap:
            return fit, data
        if not give():
            return fit, _zip([manifest(fit, True), *fit.kept])


def _discard(path: Path) -> None:
    """Remove a half-written file and never raise doing it (`logsnap._discard`'s reason)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug(f"could not remove {path}: {exc}")


def _write_atomically(dest: Path, data: bytes) -> None:
    """`<dest>.partial`, then `os.replace`. Raises `OSError`; leaves no partial behind."""
    partial = dest.with_name(dest.name + ".partial")
    try:
        partial.write_bytes(data)
        os.replace(partial, dest)
    except OSError:
        _discard(partial)
        raise
