"""What a support file is made of (T93): the files, the installs, the passwords to remove."""

from __future__ import annotations

import itertools
import json
import secrets
import subprocess
from pathlib import Path

import pytest

from yulon import docker, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog
from yulon.state import KnownInstall
from yulon.support import runlog
from yulon.support.sources import (
    SKIPPED,
    InstallFacts,
    Sources,
    collect_live_logs,
    conf_files,
    gather_known,
    keep_tail,
    read_tail,
    sources_for_app,
    system_info,
    viewables,
)

CATALOG = load_catalog()
TBC = CATALOG.get("wow-tbc")


def _tbc_install(tmp_path: Path) -> InstallFacts:
    server_dir = tmp_path / "tbc"
    return InstallFacts(
        game="wow-tbc",
        install_id=composegen.install_id(server_dir),
        server_dir=server_dir,
        wsl_distro=None,
        entry=TBC,
    )


def test_known_values_come_from_every_store_and_skip_the_public_default(tmp_path: Path) -> None:
    config = platform.config_dir()
    install = _tbc_install(tmp_path)
    generated = "tbc-" + secrets.token_hex(8)
    in_conf = "Conf" + secrets.token_hex(6)
    dotted = "Dot" + secrets.token_hex(6)
    soap = "Soap" + secrets.token_hex(6)
    kept = "vanilla-" + secrets.token_hex(8)
    (install.server_dir / "etc").mkdir(parents=True)
    (install.server_dir / ".db_password").write_text(generated + "\n", encoding="utf-8")
    (install.server_dir / "etc" / "mangosd.conf").write_text(
        f'LoginDatabaseInfo = "tbc-db;3306;mangos;{in_conf};tbcrealmd"\n'
        'WorldDatabaseInfo = "tbc-db;3306;mangos;password;tbcmangos"\n'
        f'CharacterDatabase.Info = "tbc-db;3306;mangos;{dotted};tbccharacters"\n',
        encoding="utf-8",
    )
    (config / "credentials").mkdir(parents=True)
    (config / "credentials" / f"wow-tbc-{install.install_id}.json").write_text(
        json.dumps({"account": "OWNER", "password": soap, "host": "localhost", "port": 7878}),
        encoding="utf-8",
    )
    (config / "db-secrets").mkdir()
    (config / "db-secrets" / "wow-vanilla-0badc0de.json").write_text(
        json.dumps({"volume": "v", "password": kept}), encoding="utf-8"
    )
    sources = Sources(
        config_dir=config,
        app_log=None,
        installs=(install,),
        public_passwords=frozenset({"password"}),
    )
    known = gather_known(sources)
    assert known.values == {generated, in_conf, dotted, soap, kept}
    assert known.missing == ()


def test_known_values_are_stripped_and_a_blank_one_is_not_a_password(tmp_path: Path) -> None:
    """`Redactor.build` keeps any 4+ char value as given: a newline or a blank must not reach it."""
    config = platform.config_dir()
    install = _tbc_install(tmp_path)
    padded = "Pad" + secrets.token_hex(6)
    install.server_dir.mkdir(parents=True)
    (install.server_dir / ".db_password").write_text(" \n\t\n", encoding="utf-8")
    (config / "db-secrets").mkdir(parents=True)
    (config / "db-secrets" / "wow-vanilla-0badc0de.json").write_text(
        json.dumps({"volume": "v", "password": f"  {padded}\n"}), encoding="utf-8"
    )
    (config / "credentials").mkdir()
    (config / "credentials" / "wow-tbc-0badc0de.json").write_text(
        json.dumps({"account": "OWNER", "password": "    \n", "host": "localhost", "port": 7878}),
        encoding="utf-8",
    )
    known = gather_known(Sources(config_dir=config, app_log=None, installs=(install,)))
    assert known.values == {padded}
    assert known.missing == (
        "credentials/wow-tbc-0badc0de.json could not be read",
        f"{install.label}: its generated database password could not be read",
    )


def test_an_unreadable_known_source_is_named_not_fatal(tmp_path: Path) -> None:
    config = platform.config_dir()
    (config / "credentials").mkdir(parents=True)
    (config / "credentials" / "wow-tbc-0badc0de.json").write_text("{not json", encoding="utf-8")
    known = gather_known(Sources(config_dir=config, app_log=None, installs=()))
    assert known.values == frozenset()
    assert known.missing == ("credentials/wow-tbc-0badc0de.json could not be read",)


def test_conf_files_lists_both_families_folders_and_nothing_else(tmp_path: Path) -> None:
    for rel in (
        "etc/mangosd.conf",
        "etc/modules/tortoise_bots.conf",
        "env/dist/etc/worldserver.conf",
        "env/dist/etc/modules/playerbots.conf",
        "etc/mangosd.conf.dist",
        "etc/mangosd.conf.20260922.bak",
        "src/other.conf",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x = 1\n", encoding="utf-8")
    assert [p.relative_to(tmp_path).as_posix() for p in conf_files(tmp_path)] == [
        "etc/mangosd.conf",
        "etc/modules/tortoise_bots.conf",
        "env/dist/etc/worldserver.conf",
        "env/dist/etc/modules/playerbots.conf",
    ]


def test_a_long_file_keeps_its_end_from_a_whole_line(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("".join(f"line {n}\n" for n in range(1000)), encoding="utf-8")
    tail = read_tail(path, limit=100)
    kept = tail.splitlines()
    assert kept[0].startswith("[earlier lines dropped")
    assert kept[-1] == "line 999"
    assert all(line.startswith("line ") for line in kept[1:]), "a cut line survived"
    assert keep_tail("short\n", limit=100) == "short\n"


def test_live_logs_name_each_container_and_say_why_one_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = TBC.container_spec()
    unnamed, unread = spec.service_for(spec.db), spec.service_for(spec.auth)

    def container_id(
        service: str, server_dir: Path, *, wsl_distro: str | None = None
    ) -> str | None:
        return None if service == unnamed else f"id-{service}"

    def tail(
        container: str, lines: int = 0, *, wsl_distro: str | None = None, timeout: float = 0.0
    ) -> str | None:
        assert lines == 2000 and timeout == 20.0
        return None if container == f"id-{unread}" else f"log of {container}\n"

    monkeypatch.setattr(docker, "compose_container_id", container_id)
    monkeypatch.setattr(docker, "log_tail", tail)
    got = {live.container: live for live in collect_live_logs(_tbc_install(tmp_path))}
    assert got[spec.world].text == f"log of id-{spec.service_for(spec.world)}\n"
    assert got[spec.auth].text is None and got[spec.auth].problem == "docker could not read its log"
    assert got[spec.db].text is None and "did not name a container" in got[spec.db].problem


def test_a_tail_that_took_the_whole_bound_is_reported_as_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "compose_container_id", lambda s, d, *, wsl_distro=None: "id")
    monkeypatch.setattr(docker, "log_tail", lambda *a, **k: None)
    clock = itertools.cycle([0.0, 1.0, 0.0, 21.0])  # compose ps: 1 s; docker logs: 21 s
    got = collect_live_logs(_tbc_install(tmp_path), monotonic=lambda: next(clock))
    assert [live.problem for live in got] == ["timed out after 20 s"] + [SKIPPED] * 2


def test_a_wedged_docker_is_asked_once_per_target_across_every_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung daemon must cost one bound, not three per install (T93 review, fix round 1)."""
    now = [0.0]
    asked: list[tuple[str, str | None]] = []

    def container_id(
        service: str, server_dir: Path, *, wsl_distro: str | None = None
    ) -> str | None:
        asked.append(("ps", wsl_distro))
        if wsl_distro is None:
            now[0] += 31.0  # `compose ps` ran into its own 30 s bound
            return None
        return f"id-{service}"

    def tail(
        container: str, lines: int = 0, *, wsl_distro: str | None = None, timeout: float = 0.0
    ) -> str | None:
        asked.append(("logs", wsl_distro))
        return f"log of {container}\n"

    monkeypatch.setattr(docker, "compose_container_id", container_id)
    monkeypatch.setattr(docker, "log_tail", tail)
    native = [_tbc_install(tmp_path / "a"), _tbc_install(tmp_path / "b")]
    in_wsl = InstallFacts("wow-tbc", "0badc0de", tmp_path / "w", "Ubuntu", TBC)
    silent: set[str | None] = set()
    got = [
        collect_live_logs(install, monotonic=lambda: now[0], silent_targets=silent)
        for install in (*native, in_wsl)
    ]
    assert [call for call in asked if call[1] is None] == [("ps", None)]
    first, *rest = [live.problem for live in got[0] + got[1]]
    assert first == "timed out after 30 s finding its container"
    assert rest == [SKIPPED] * 5
    assert [live.text is not None for live in got[2]] == [True, True, True]
    assert asked.count(("ps", "Ubuntu")) == 3 and asked.count(("logs", "Ubuntu")) == 3
    assert silent == {None}


def test_a_linked_conf_is_left_out_of_the_bundle_list_and_named(tmp_path: Path) -> None:
    """Task 5 zips `conf_files()` by content: a link could bring in a file from anywhere."""
    install = _tbc_install(tmp_path)
    etc = install.server_dir / "etc"
    etc.mkdir(parents=True)
    (etc / "mangosd.conf").write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.conf"
    linked_pw = "Link" + secrets.token_hex(6)
    outside.write_text(
        f'LoginDatabaseInfo = "tbc-db;3306;mangos;{linked_pw};tbcrealmd"\n', encoding="utf-8"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "bots.conf").write_text("x = 1\n", encoding="utf-8")
    try:
        (etc / "realmd.conf").symlink_to(outside)
        (etc / "modules").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot make a symlink")
    assert conf_files(install.server_dir) == [etc / "mangosd.conf"]
    (install.server_dir / ".db_password").write_text("tbc-pw-1234\n", encoding="utf-8")
    known = gather_known(
        Sources(config_dir=platform.config_dir(), app_log=None, installs=(install,))
    )
    assert known.missing == (
        f"{install.label}: etc/realmd.conf is a link, not a file: left out",
        f"{install.label}: etc/modules/bots.conf is a link, not a file: left out",
    )
    assert linked_pw in known.values, "masking reads the linked file all the same"


def test_the_docker_seams_carry_their_bound_and_ask_the_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[list[str], float | None, str | None]] = []

    def fake(
        argv: list[str],
        cwd: Path | None = None,
        timeout: float | None = None,
        *,
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        seen.append((argv, timeout, wsl_distro))
        return subprocess.CompletedProcess(argv, 0, "27.0.1\n", "")

    monkeypatch.setattr(docker, "_docker", fake)
    assert docker.server_version(wsl_distro="Ubuntu", timeout=20.0) == "27.0.1"
    assert docker.log_tail("abc", 5, timeout=20.0) == "27.0.1\n"
    assert seen == [
        (["version", "--format", "{{.Server.Version}}"], 20.0, "Ubuntu"),
        (["logs", "--tail", "5", "abc"], 20.0, None),
    ]


def test_system_info_names_the_versions_and_every_install_and_survives_a_raising_seam(
    tmp_path: Path,
) -> None:
    install = _tbc_install(tmp_path)
    wsl = InstallFacts("wow-wotlk", "0badc0de", tmp_path / "w", "Ubuntu", None)
    sources = Sources(platform.config_dir(), None, (install, wsl), qt_version="6.11.2")

    def version(distro: str | None) -> str | None:
        if distro is not None:
            raise RuntimeError("no distro")
        return "27.0.1"

    text = system_info(sources, version)
    assert "Qt: 6.11.2" in text
    assert "Docker on this machine: 27.0.1" in text
    assert "Docker in WSL distro Ubuntu: not reachable (RuntimeError)" in text
    assert f"wow-tbc, id {install.install_id}, folder {install.server_dir}" in text
    assert "(not in this version's catalog)" in text


def test_system_info_does_not_ask_a_docker_that_already_went_silent(tmp_path: Path) -> None:
    """A daemon that ran into a bound during the live logs costs no second 20 s here."""
    wsl = InstallFacts("wow-tbc", "0badc0de", tmp_path / "w", "Ubuntu", TBC)
    sources = Sources(platform.config_dir(), None, (wsl,))
    asked: list[str | None] = []

    def version(distro: str | None) -> str | None:
        asked.append(distro)
        return "27.0.1"

    text = system_info(sources, version, silent_targets={None})
    assert asked == ["Ubuntu"]
    assert f"Docker on this machine: {SKIPPED}" in text
    assert "Docker in WSL distro Ubuntu: 27.0.1" in text


def test_sources_for_app_hashes_each_folder_and_learns_the_public_passwords(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "srv"
    sources = sources_for_app([KnownInstall(game="wow-tbc", server_dir=server_dir)], CATALOG)
    assert sources.installs[0].install_id == composegen.install_id(server_dir)
    assert sources.installs[0].entry is TBC
    assert "password" in sources.public_passwords  # wow-wotlk's fixed value in catalog.json
    assert sources.config_dir == platform.config_dir()


def test_viewables_list_the_app_log_then_runs_then_snapshots_newest_first() -> None:
    config = platform.config_dir()
    runs = runlog.runs_dir(config)
    runs.mkdir(parents=True)
    (config / "yulon.log").write_text("app\n", encoding="utf-8")
    (runs / "install-wow-tbc-20260922T101010Z.log").write_text("run\n", encoding="utf-8")
    (config / "logs" / "wow-tbc-0badc0de-20260922T101010Z.log").write_text(
        "snap\n", encoding="utf-8"
    )
    labels = [item.label for item in viewables(Sources(config, config / "yulon.log", ()))]
    assert labels == [
        "App log (yulon.log)",
        "Run: install-wow-tbc-20260922T101010Z.log",
        "Worldserver snapshot: wow-tbc-0badc0de-20260922T101010Z.log",
    ]
