"""The support bundle (T93): one bad source never kills it, and it stays under the cap."""

from __future__ import annotations

import os
import secrets
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yulon import docker, platform
from yulon.catalog.catalog import load_catalog
from yulon.support import bundle
from yulon.support import sources as src
from yulon.support.redact import Redactor
from yulon.support.sources import InstallFacts, LiveLog, Sources

TBC = load_catalog().get("wow-tbc")


def _seams(**overrides: object) -> bundle.Seams:
    base: dict[str, object] = {
        "live_logs": lambda install, silent: [LiveLog("tbc-mangosd", "world up\n")],
        "docker_version": lambda distro: "27.0.1",
        "now": lambda: datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return bundle.Seams(**base)  # type: ignore[arg-type]


def _install(tmp_path: Path, install_id: str = "0badc0de") -> InstallFacts:
    server_dir = tmp_path / f"srv-{install_id}"
    (server_dir / "etc").mkdir(parents=True)
    (server_dir / "etc" / "mangosd.conf").write_text("SOAP.Enabled = 1\n", encoding="utf-8")
    return InstallFacts("wow-tbc", install_id, server_dir, None, TBC)


def _read(dest: Path) -> dict[str, str]:
    with zipfile.ZipFile(dest) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def test_a_docker_seam_that_raises_is_a_manifest_line_and_the_zip_is_still_written(
    tmp_path: Path,
) -> None:
    def broken(install: InstallFacts, silent: set[str | None]) -> list[LiveLog]:
        raise RuntimeError("daemon gone")

    sources = Sources(platform.config_dir(), None, (_install(tmp_path),))
    dest = tmp_path / "support.zip"
    report = bundle.build(dest, sources, Redactor.build([]), seams=_seams(live_logs=broken))
    members = _read(dest)
    assert "conf/wow-tbc-0badc0de/etc/mangosd.conf" in members
    assert (
        "live/wow-tbc-0badc0de  docker not reachable (RuntimeError: daemon gone)"
        in members["MANIFEST.txt"]
    )
    assert (
        "live/wow-tbc-0badc0de",
        "docker not reachable (RuntimeError: daemon gone)",
    ) in report.skipped


def test_a_container_that_timed_out_is_named_with_its_reason(tmp_path: Path) -> None:
    seams = _seams(live_logs=lambda i, s: [LiveLog("tbc-db", None, "timed out after 20 s")])
    sources = Sources(platform.config_dir(), None, (_install(tmp_path),))
    bundle.build(tmp_path / "s.zip", sources, Redactor.build([]), seams=seams)
    manifest = _read(tmp_path / "s.zip")["MANIFEST.txt"]
    assert "live/wow-tbc-0badc0de-tbc-db.log  timed out after 20 s" in manifest


def test_a_container_log_is_called_a_log_and_never_said_to_be_running(tmp_path: Path) -> None:
    """Stopped containers are read too (lead ruling): the manifest must not claim one ran."""
    sources = Sources(platform.config_dir(), None, (_install(tmp_path),))
    bundle.build(tmp_path / "s.zip", sources, Redactor.build([]), seams=_seams())
    members = _read(tmp_path / "s.zip")
    assert members["live/wow-tbc-0badc0de-tbc-mangosd.log"] == "world up\n"
    line = next(line for line in members["MANIFEST.txt"].splitlines() if "tbc-mangosd.log" in line)
    assert "container log, last 2000 lines" in line
    assert "running" not in members["MANIFEST.txt"].lower()


def test_empty_folders_and_a_missing_app_log_are_said_not_silent(tmp_path: Path) -> None:
    sources = Sources(platform.config_dir(), None, ())
    bundle.build(tmp_path / "s.zip", sources, Redactor.build([]), seams=_seams())
    manifest = _read(tmp_path / "s.zip")["MANIFEST.txt"]
    for line in (
        "app/yulon.log  this session keeps no log file",
        "runs/  no install or rebuild has been recorded yet",
        "snapshots/  no server has been stopped",
        "live/  Yu'lon knows no servers",
        "conf/  Yu'lon knows no servers",
    ):
        assert line in manifest, line


def _unlistable(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    """`folder.iterdir()` raises what an unreadable folder raises; every other folder lists."""
    real = Path.iterdir

    def iterdir(self: Path) -> object:
        if self == folder:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)


def test_an_unreadable_runs_folder_is_named_with_its_reason_never_said_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Could not look' must never read as 'nothing was recorded' (T93 final review)."""
    config = platform.config_dir()
    runs = src.runlog.runs_dir(config)
    runs.mkdir(parents=True)
    (runs / "install-wow-tbc-20260922T101010Z.log").write_text("x\n", encoding="utf-8")
    _unlistable(monkeypatch, runs)
    report = bundle.build(
        tmp_path / "s.zip", Sources(config, None, ()), Redactor.build([]), seams=_seams()
    )
    manifest = _read(tmp_path / "s.zip")["MANIFEST.txt"]
    assert "no install or rebuild has been recorded yet" not in manifest
    assert ("runs/", f"{runs} could not be listed (Permission denied)") in report.skipped
    assert f"runs/  {runs} could not be listed (Permission denied)" in manifest
    # The snapshots folder above it still lists: one bad source never kills the rest.
    assert "snapshots/  no server has been stopped" in manifest


def test_an_unreadable_logs_folder_is_named_for_the_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = platform.config_dir()
    logs = src.runlog.logs_dir(config)
    logs.mkdir(parents=True)
    _unlistable(monkeypatch, logs)
    report = bundle.build(
        tmp_path / "s.zip", Sources(config, None, ()), Redactor.build([]), seams=_seams()
    )
    assert ("snapshots/", f"{logs} could not be listed (Permission denied)") in report.skipped
    assert not any("no server has been stopped" in why for _name, why in report.skipped)


def test_a_file_that_vanishes_before_it_is_read_is_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = platform.config_dir()
    runs = src.runlog.runs_dir(config)
    runs.mkdir(parents=True)
    gone = runs / "install-wow-tbc-20260922T101010Z.log"
    gone.write_text("x\n", encoding="utf-8")
    real = src.read_tail

    def vanishing(path: Path, limit: int = src.FILE_CAP) -> str:
        if path == gone:
            raise FileNotFoundError(2, "No such file", str(path))
        return real(path, limit)

    monkeypatch.setattr(src, "read_tail", vanishing)
    report = bundle.build(
        tmp_path / "s.zip", Sources(config, None, ()), Redactor.build([]), seams=_seams()
    )
    assert (f"runs/{gone.name}", "file vanished before it could be read") in report.skipped


def test_the_size_cap_leaves_out_the_oldest_snapshot_first(tmp_path: Path) -> None:
    config = platform.config_dir()
    logs = config / "logs"
    logs.mkdir(parents=True)
    names = []
    for n in range(3):
        path = logs / f"wow-tbc-0badc0de-2026092{n}T101010Z.log"
        # ~200 KB, hardly compressible
        path.write_text(secrets.token_urlsafe(150_000), encoding="utf-8")
        os.utime(path, (1_000_000 + n, 1_000_000 + n))
        names.append(f"snapshots/{path.name}")
    dest = tmp_path / "s.zip"
    report = bundle.build(
        dest, Sources(config, None, ()), Redactor.build([]), seams=_seams(), cap_bytes=400_000
    )
    assert report.dropped == (names[0],)
    assert set(names[1:]) <= set(report.included)
    assert os.path.getsize(dest) <= 400_000
    assert names[0] in _read(dest)["MANIFEST.txt"]


def test_the_zip_replaces_an_old_one_and_leaves_no_partial(tmp_path: Path) -> None:
    dest = tmp_path / "s.zip"
    dest.write_bytes(b"an older support file")
    bundle.build(dest, Sources(platform.config_dir(), None, ()), Redactor.build([]), seams=_seams())
    assert zipfile.is_zipfile(dest)
    assert not (tmp_path / "s.zip.partial").exists()


def test_an_unwritable_destination_raises_and_leaves_no_partial(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        bundle.build(
            blocker / "s.zip",
            Sources(platform.config_dir(), None, ()),
            Redactor.build([]),
            seams=_seams(),
        )
    assert not (blocker / "s.zip.partial").exists()


def test_save_names_an_unreadable_known_store_and_the_patterns_still_run(tmp_path: Path) -> None:
    config = platform.config_dir()
    (config / "credentials").mkdir(parents=True)
    (config / "credentials" / "wow-tbc-0badc0de.json").write_text("{broken", encoding="utf-8")
    (config / "yulon.log").write_text("db;3306;mangos;Hidden-Value-9;realmd\n", encoding="utf-8")
    dest = tmp_path / "s.zip"
    bundle.save(dest, Sources(config, config / "yulon.log", ()), seams=_seams(), home=tmp_path)
    members = _read(dest)
    assert "credentials/wow-tbc-0badc0de.json could not be read" in members["MANIFEST.txt"]
    assert "Hidden-Value-9" not in members["app/yulon.log"]


def test_a_linked_conf_is_named_in_the_manifest_and_left_out_of_the_zip(tmp_path: Path) -> None:
    install = _install(tmp_path)
    outside = tmp_path / "outside.conf"
    outside.write_text("Anything = 1\n", encoding="utf-8")
    try:
        (install.server_dir / "etc" / "realmd.conf").symlink_to(outside)
    except OSError:
        pytest.skip("this machine cannot make a symlink")
    dest = tmp_path / "s.zip"
    bundle.save(
        dest, Sources(platform.config_dir(), None, (install,)), seams=_seams(), home=tmp_path
    )
    members = _read(dest)
    assert "conf/wow-tbc-0badc0de/etc/mangosd.conf" in members
    assert not any(name.endswith("realmd.conf") for name in members)
    assert (
        "wow-tbc-0badc0de: etc/realmd.conf is a link, not a file: left out"
        in members["MANIFEST.txt"]
    )


def test_a_wedged_docker_is_asked_once_per_bundle_not_once_per_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two installs on one hung daemon: one docker ask in the whole bundle, the version too."""
    asked: list[tuple[str, str | None]] = []

    def container_id(
        service: str, server_dir: Path, *, wsl_distro: str | None = None
    ) -> str | None:
        asked.append(("ps", wsl_distro))
        return None

    def version(distro: str | None) -> str | None:
        asked.append(("version", distro))
        return None

    monkeypatch.setattr(docker, "compose_container_id", container_id)
    monkeypatch.setattr(docker, "log_tail", lambda *a, **k: pytest.fail("logs asked"))
    # Every `None` now took the whole bound: the first ask marks this docker silent.
    monkeypatch.setattr(docker, "COMPOSE_PS_TIMEOUT", 0.0)
    installs = (_install(tmp_path, "0badc0de"), _install(tmp_path, "0badf00d"))
    seams = bundle.Seams(docker_version=version)
    bundle.build(
        tmp_path / "s.zip",
        Sources(platform.config_dir(), None, installs),
        Redactor.build([]),
        seams=seams,
    )
    assert asked == [("ps", None)]
    members = _read(tmp_path / "s.zip")
    assert f"Docker on this machine: {src.SKIPPED}" in members["system-info.txt"]
    assert f"live/wow-tbc-0badf00d-{TBC.container_spec().world}.log  {src.SKIPPED}" in (
        members["MANIFEST.txt"]
    )


def test_no_known_secret_survives_in_any_member_name_content_or_the_manifest(
    tmp_path: Path,
) -> None:
    secret = "Sekrit" + secrets.token_hex(6)
    install = _install(tmp_path)
    (install.server_dir / "etc" / "realmd.conf").write_text(
        f'LoginDatabaseInfo = "tbc-db;3306;mangos;{secret};tbcrealmd"\n', encoding="utf-8"
    )
    config = platform.config_dir()
    runs = src.runlog.runs_dir(config)
    runs.mkdir(parents=True)
    (runs / f"install-{secret}.log").write_text(f"user {secret} logged in\n", encoding="utf-8")

    def logs(install: InstallFacts, silent: set[str | None]) -> list[LiveLog]:
        return [LiveLog("tbc-mangosd", f"auth with {secret}\n"), LiveLog("tbc-db", None, secret)]

    def version(distro: str | None) -> str | None:
        raise RuntimeError(f"refused {secret}")

    report = bundle.save(
        tmp_path / "s.zip",
        Sources(config, None, (install,)),
        seams=_seams(live_logs=logs, docker_version=version),
        home=tmp_path,
    )
    with zipfile.ZipFile(tmp_path / "s.zip") as archive:
        for name in archive.namelist():
            assert secret not in name
            assert secret not in archive.read(name).decode("utf-8"), name
    assert secret not in repr(report.included + report.dropped) + repr(report.skipped)


def test_two_names_that_differ_only_in_a_secret_stay_two_members(tmp_path: Path) -> None:
    first, second = "Alpha" + secrets.token_hex(6), "Bravo" + secrets.token_hex(6)
    config = platform.config_dir()
    runs = src.runlog.runs_dir(config)
    runs.mkdir(parents=True)
    for value in (first, second):
        (runs / f"install-{value}.log").write_text(f"{value}\n", encoding="utf-8")
    report = bundle.build(
        tmp_path / "s.zip",
        Sources(config, None, ()),
        Redactor.build([first, second]),
        seams=_seams(),
    )
    assert sorted(n for n in report.included if n.startswith("runs/")) == [
        "runs/install-*** (2).log",
        "runs/install-***.log",
    ]
    assert len([n for n in _read(tmp_path / "s.zip") if n.startswith("runs/")]) == 2


def test_a_short_password_is_named_in_the_manifest_and_the_promise_is_not_made(
    tmp_path: Path,
) -> None:
    """Codex T93 review: a 1-3 character password stays in free text, so say so."""
    config = platform.config_dir()
    (config / "credentials").mkdir(parents=True)
    (config / "credentials" / "wow-tbc-0badc0de.json").write_text(
        '{"account": "OWNER", "password": "Zq", "host": "localhost", "port": 7878}',
        encoding="utf-8",
    )
    report = bundle.save(
        tmp_path / "s.zip", Sources(config, None, ()), seams=_seams(), home=tmp_path
    )
    manifest = _read(tmp_path / "s.zip")["MANIFEST.txt"]
    assert report.short_passwords == ("credentials/wow-tbc-0badc0de.json",)
    assert "WARNING" in manifest and "very short password" in manifest
    assert "credentials/wow-tbc-0badc0de.json" in manifest
    assert "longer password" in manifest
    assert "Every password Yu'lon knows of" not in manifest
    assert "Zq" not in manifest


def test_without_a_short_password_the_manifest_makes_its_promise_and_warns_nothing(
    tmp_path: Path,
) -> None:
    report = bundle.save(
        tmp_path / "s.zip", Sources(platform.config_dir(), None, ()), seams=_seams(), home=tmp_path
    )
    manifest = _read(tmp_path / "s.zip")["MANIFEST.txt"]
    assert report.short_passwords == ()
    assert "Every password Yu'lon knows of" in manifest
    assert "WARNING" not in manifest


def _noise(size: int, tag: str) -> str:
    """`size` bytes of base64 lines that deflate hardly at all, the last one tagged."""
    lines = []
    total = 0
    while total < size:
        line = secrets.token_urlsafe(57) + "\n"
        lines.append(line)
        total += len(line)
    lines.append(f"LAST {tag}\n")
    return "".join(lines)


def _noisy_installs(tmp_path: Path, count: int, conf_bytes: int) -> tuple[InstallFacts, ...]:
    installs = []
    for n in range(count):
        install = _install(tmp_path, f"0badc0d{n}")
        for name in ("mangosd.conf", "realmd.conf"):
            (install.server_dir / "etc" / name).write_text(
                _noise(conf_bytes, f"{install.install_id}-{name}"), encoding="utf-8"
            )
        installs.append(install)
    return tuple(installs)


def _noisy_live(size: int) -> Callable[[InstallFacts, set[str | None]], list[LiveLog]]:
    """A `live_logs` seam giving each of three containers `size` bytes of noise."""

    def live_logs(install: InstallFacts, silent: set[str | None]) -> list[LiveLog]:
        return [
            LiveLog(name, _noise(size, f"{install.install_id}-{name}"))
            for name in ("tbc-mangosd", "tbc-realmd", "tbc-db")
        ]

    return live_logs


def test_live_logs_are_cut_shorter_until_the_zip_fits_the_cap(tmp_path: Path) -> None:
    """Codex T93 review: dropping snapshots and runs alone left a zip far over the cap.

    Three installs of hardly compressible container logs (9 x 1.25 MiB, ~9 MB
    deflated) and confs (6 x 256 KiB): after the run log and snapshot go, the live logs are cut --
    their END kept -- and the confs, which fit once the live logs are cut, are not.
    """
    config = platform.config_dir()
    runs = src.runlog.runs_dir(config)
    runs.mkdir(parents=True)
    (runs / "install-wow-tbc-20260922T101010Z.log").write_text(
        _noise(500_000, "run"), encoding="utf-8"
    )
    (config / "logs" / "wow-tbc-0badc0de-20260922T101010Z.log").write_text(
        _noise(500_000, "snap"), encoding="utf-8"
    )
    installs = _noisy_installs(tmp_path, 3, 256 * 1024)
    dest = tmp_path / "s.zip"
    report = bundle.build(
        dest,
        Sources(config, None, installs),
        Redactor.build([]),
        seams=_seams(live_logs=_noisy_live(1280 * 1024)),
    )
    assert os.path.getsize(dest) <= bundle.ZIP_CAP
    assert report.size == os.path.getsize(dest)
    members = _read(dest)
    manifest = members["MANIFEST.txt"]
    assert "Still larger" not in manifest
    assert report.dropped == (
        "snapshots/wow-tbc-0badc0de-20260922T101010Z.log",
        "runs/install-wow-tbc-20260922T101010Z.log",
    )
    live = [name for name in members if name.startswith("live/")]
    assert len(live) == 9
    assert report.cut and set(report.cut) <= set(live)
    assert "Cut shorter" in manifest
    for name in report.cut:
        assert f"  {name}  " in manifest.split("Cut shorter", 1)[1], name
        # The END is kept, from a whole line, with a notice that says it was cut.
        assert members[name].startswith("[earlier lines dropped"), name
        assert members[name].splitlines()[-1].startswith("LAST "), name
    assert not any(name.startswith("conf/") for name in report.cut)


def test_confs_are_cut_after_the_live_logs_are_at_their_floor(tmp_path: Path) -> None:
    installs = _noisy_installs(tmp_path, 3, 256 * 1024)
    dest = tmp_path / "s.zip"
    cap = 500_000
    report = bundle.build(
        dest,
        Sources(platform.config_dir(), None, installs),
        Redactor.build([]),
        seams=_seams(live_logs=_noisy_live(256 * 1024)),
        cap_bytes=cap,
    )
    assert os.path.getsize(dest) <= cap
    members = _read(dest)
    for name in (n for n in members if n.startswith("live/")):
        assert len(members[name].encode()) <= bundle.MIN_TAIL, name
    assert any(name.startswith("conf/") for name in report.cut)
    assert "Still larger" not in members["MANIFEST.txt"]


def test_a_bundle_still_over_the_cap_at_every_floor_says_so(tmp_path: Path) -> None:
    installs = _noisy_installs(tmp_path, 3, 96 * 1024)
    dest = tmp_path / "s.zip"
    cap = 100_000
    report = bundle.build(
        dest,
        Sources(platform.config_dir(), None, installs),
        Redactor.build([]),
        seams=_seams(live_logs=_noisy_live(96 * 1024)),
        cap_bytes=cap,
    )
    assert report.size > cap
    members = _read(dest)
    assert "Still larger than the cap" in members["MANIFEST.txt"]
    for name in members:
        if name.startswith(("live/", "conf/")):
            assert len(members[name].encode()) <= bundle.MIN_TAIL, name
