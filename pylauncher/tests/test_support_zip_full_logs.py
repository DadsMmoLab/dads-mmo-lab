"""The support file reads every stream of a container, and a failed install's servers (T350, T351).

T350: `docker logs` hands a container's stderr back on its own stderr, and
`docker.log_tail()` kept stdout alone. A container without a tty (Tortoise's
realmd, every database) writes its errors to stderr, so the zip's `live/`
members and the stop-time worldserver snapshot never held them.

T351: an install that fails is never remembered, so the bundle read no
container for it. A folder that holds a failed install's state file is now read
like a remembered install, so its passwords are masked and its live logs kept.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import zipfile
from pathlib import Path

import pytest

from tests.support_fake_docker import lay_fake_docker, set_fake_log
from yulon import docker, logsnap, platform, runner
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import default_server_dir
from yulon.catalog.native import ERROR_RUN_INSTALL, ERROR_RUN_REBUILD, STATE_FILE
from yulon.state import KnownInstall
from yulon.support import bundle
from yulon.support.redact import Redactor
from yulon.support.sources import InstallFacts, Sources, collect_live_logs, sources_for_app

CATALOG = load_catalog()
TORTOISE = CATALOG.get("wow-tortoise")
SPEC = TORTOISE.container_spec()
ERR = "Could not connect to MySQL database at tortoise-db"


def _facts(tmp_path: Path) -> InstallFacts:
    server_dir = tmp_path / "srv"
    return InstallFacts(
        "wow-tortoise", composegen.install_id(server_dir), server_dir, None, TORTOISE
    )


def test_the_live_logs_keep_what_a_container_wrote_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "compose_container_id", lambda s, d, *, wsl_distro=None: f"id-{s}")
    for name in (SPEC.world, SPEC.auth, SPEC.db):
        set_fake_log(state, f"id-{SPEC.service_for(name)}", stdout="out\n", stderr=ERR + "\n")

    got = collect_live_logs(_facts(tmp_path))

    assert [live.text for live in got] == [f"out\n{ERR}\n"] * 3


def test_the_stop_snapshot_keeps_what_the_world_container_wrote_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
        if cmd[:3] == ["docker", "compose", "ps"]:
            return subprocess.CompletedProcess(cmd, 0, "deadbeef\n", "")
        return subprocess.CompletedProcess(cmd, 0, "out line\n", ERR + "\n")

    monkeypatch.setattr(runner, "run", run)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    logs_dir = tmp_path / "logs"

    snap = logsnap.capture(SPEC, server_dir, game="wow-tortoise", logs_dir=logs_dir)

    assert snap.problem is None or snap.problem == ""
    [saved] = logs_dir.glob("*.log")
    assert ERR in saved.read_text(encoding="utf-8")


def _failed_folder(home: Path, *, run: str = ERROR_RUN_INSTALL, password: str = "") -> Path:
    folder = default_server_dir(TORTOISE, home)
    folder.mkdir(parents=True)
    (folder / STATE_FILE).write_text(
        json.dumps({"game_id": "wow-tortoise", "last_error": "ready failed", "error_run": run}),
        encoding="utf-8",
    )
    if password:
        (folder / ".db_password").write_text(password + "\n", encoding="utf-8")
    return folder


def test_a_folder_holding_a_failed_install_is_read_like_a_remembered_one(tmp_path: Path) -> None:
    folder = _failed_folder(tmp_path)

    sources = sources_for_app([], CATALOG, home=tmp_path)

    assert [(i.game, i.server_dir, i.entry) for i in sources.installs] == [
        ("wow-tortoise", folder, TORTOISE)
    ]


def test_only_a_failed_install_run_makes_a_folder_count(tmp_path: Path) -> None:
    _failed_folder(tmp_path, run=ERROR_RUN_REBUILD)
    wotlk = default_server_dir(CATALOG.get("wow-wotlk"), tmp_path)
    wotlk.mkdir(parents=True)  # a folder with no state file at all

    assert sources_for_app([], CATALOG, home=tmp_path).installs == ()


def test_a_remembered_install_is_not_listed_twice(tmp_path: Path) -> None:
    folder = _failed_folder(tmp_path)

    remembered = [KnownInstall(game="wow-tortoise", server_dir=folder)]
    sources = sources_for_app(remembered, CATALOG, home=tmp_path)

    assert len(sources.installs) == 1


def test_a_failed_installs_password_is_masked_and_its_containers_are_in_the_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "tortoise-" + secrets.token_hex(8)
    _failed_folder(tmp_path, password=secret)
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "compose_container_id", lambda s, d, *, wsl_distro=None: f"id-{s}")
    for name in (SPEC.world, SPEC.auth, SPEC.db):
        set_fake_log(
            state,
            f"id-{SPEC.service_for(name)}",
            stdout="up\n",
            stderr=f"{name} failed, password {secret}\n",
        )
    sources = sources_for_app([], CATALOG, home=tmp_path)
    known = src_known(sources)
    dest = tmp_path / "s.zip"

    bundle.build(
        dest,
        sources,
        Redactor.build(known),
        seams=bundle.Seams(docker_version=lambda distro: None),
    )

    with zipfile.ZipFile(dest) as archive:
        members = {n: archive.read(n).decode("utf-8") for n in archive.namelist()}
    live = [text for name, text in members.items() if name.startswith("live/")]
    assert len(live) == 3 and all("failed, password" in text for text in live)
    assert not any(secret in text for text in members.values())
    assert not any(secret in name for name in members)


def src_known(sources: Sources) -> frozenset[str]:
    from yulon.support.sources import gather_known

    return gather_known(sources).values


def test_a_test_that_names_no_home_never_reads_the_real_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conftest default: a failed install under `Path.home()` is invisible to the suite."""
    _failed_folder(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert sources_for_app([], CATALOG).installs == ()


def test_a_remembered_install_spelled_through_a_link_is_not_listed_twice(tmp_path: Path) -> None:
    """T353: a symlinked home spells one folder two ways."""
    home = tmp_path / "home"
    folder = _failed_folder(home)
    linked_home = tmp_path / "linked"
    linked_home.symlink_to(home, target_is_directory=True)
    spelled = linked_home / folder.relative_to(home)

    remembered = [KnownInstall(game="wow-tortoise", server_dir=spelled)]
    sources = sources_for_app(remembered, CATALOG, home=home)

    assert len(sources.installs) == 1


def test_a_remembered_install_in_another_letter_case_is_not_listed_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T353: Windows is case-insensitive, so `C:\\Users\\Me` and `c:\\users\\me` are one folder."""
    import ntpath

    folder = _failed_folder(tmp_path)
    monkeypatch.setattr("os.path.normcase", ntpath.normcase)
    shouted = Path(str(folder).upper())

    remembered = [KnownInstall(game="wow-tortoise", server_dir=shouted)]
    sources = sources_for_app(remembered, CATALOG, home=tmp_path)

    assert len(sources.installs) == 1


def test_a_state_file_too_big_to_be_one_is_not_read(tmp_path: Path) -> None:
    """T353: a failed-install check reads a file the player may have replaced; cap it."""
    from yulon.support import sources as src

    folder = _failed_folder(tmp_path)
    state = {"game_id": "wow-tortoise", "last_error": "x" * (src.STATE_CAP + 10)}
    (folder / STATE_FILE).write_text(
        json.dumps({**state, "error_run": ERROR_RUN_INSTALL}), encoding="utf-8"
    )

    assert sources_for_app([], CATALOG, home=tmp_path).installs == ()
