"""T137: a WotLK install writes `playerbots.conf` from its `.dist`, and Repair offers it.

Measured on a Fedora gate box 2026-09-26 (T134's lane): a fresh WotLK install
had `env/dist/etc/modules/playerbots.conf.dist` and no `playerbots.conf`, so the
world log carried ~1964 "Missing property AiPlayerbot.*" lines and the module ran
on its compiled defaults, while everything in Yu'lon that reads the deployed
file -- My Party's specs and allow-flags, the bot prefix, the Tuning tab's
shadowed-key warning -- found nothing. The `.dist` is put there by the image's
own entrypoint (`cp -rnv /azerothcore/env/ref/etc/* "$CONF_DIR"`, captured in
the T134 install log as `ac-db-import | '/azerothcore/env/ref/etc/modules/
playerbots.conf.dist' -> ...`), which copies a `.conf.dist` to its `.conf` for the
component's own conf only.

The owner's decision (2026-09-27): the install writes `playerbots.conf` from its
`.dist`; "Repair server files…" offers the same for an existing install.

The `.dist` in these tests is `tests/data/playerbots.conf.dist-excerpt`: real
lines of the pinned module's file, non-ASCII included, so "byte for byte" is
checked on bytes a text-mode copy would get wrong.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support_native import ENTRY, IMPORTED, TBC, Recorder, install
from tests.test_controller_view import _Ps, _services
from yulon import dbreads, docker, party, runner, wsl
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import azerothcore
from yulon.catalog.installer import InstallerError
from yulon.install_wiring import repair_confs_for_app
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import TUNING_RESTART_LABEL, ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

WOTLK = ENTRY
CONF = "env/dist/etc/modules/playerbots.conf"
DIST = CONF + ".dist"
SHIPPED = (Path(__file__).parent / "data" / "playerbots.conf.dist-excerpt").read_bytes()


def put_dist(server_dir: Path, mode: int = 0o644) -> Path:
    """What the image's entrypoint leaves in the bound-out etc folder: the `.dist` alone."""
    dist = server_dir / DIST
    dist.parent.mkdir(parents=True, exist_ok=True)
    dist.write_bytes(SHIPPED)
    os.chmod(dist, mode)
    return dist


def importing(rec: Recorder, mode: int = 0o644) -> Callable[..., docker.AttachedRun]:
    """The `one_shot` seam, with `ac-db-import` doing what the real one does to the etc folder."""

    def one_shot(
        service: str, where: Path, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        if service == WOTLK.containers.db_import:
            put_dist(where, mode)
        return rec.one_shot_result

    return one_shot


# -- the catalog ---------------------------------------------------------------


def test_wotlk_names_playerbots_conf_as_written_from_its_dist() -> None:
    assert azerothcore.confs_from_dist(WOTLK) == (CONF,)
    assert azerothcore.confs_from_dist(TBC) == ()


@pytest.mark.parametrize("bad", ["/etc/playerbots.conf", "env/../../playerbots.conf", "a.dist"])
def test_the_catalog_refuses_a_conf_path_that_is_not_one(bad: str) -> None:
    """Absolute, climbing out of the server folder, or itself a `.dist`: each is one error."""
    raw = load_catalog().model_dump(mode="json")
    wotlk = next(game for game in raw["games"] if game["id"] == "wow-wotlk")
    wotlk["install"]["native"]["azerothcore"]["confs_from_dist"] = [bad]
    with pytest.raises(ValueError, match="confs_from_dist"):
        type(load_catalog()).model_validate(raw)


# -- the install -----------------------------------------------------------------


def test_the_install_writes_playerbots_conf_from_its_dist_byte_for_byte(tmp_path: Path) -> None:
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    lines = install(rec, server_dir, one_shot=importing(rec, mode=0o640))
    conf = server_dir / CONF
    assert conf.read_bytes() == SHIPPED
    # The .dist's mode made readable by everyone, never conf.CONF_MODE's 0600:
    # the world runs as the image's `acore` user, which is not the host user
    # everywhere, and the file holds no secret.
    assert stat.S_IMODE(conf.stat().st_mode) == 0o644
    assert any("playerbots.conf" in line and "written from" in line for line in lines), lines


def test_the_conf_is_there_before_the_world_first_starts(tmp_path: Path) -> None:
    """Written in `up` before the start, because the world reads it only when it starts."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    seen: list[bool] = []

    def start(spec: docker.ContainerSpec, where: Path) -> bool:
        seen.append((where / CONF).is_file())
        return True

    install(rec, server_dir, one_shot=importing(rec), start=start)
    assert seen == [True]


def test_a_resume_never_overwrites_the_conf_it_or_a_person_wrote(tmp_path: Path) -> None:
    """The first run writes it; the second finds a file that says something else and keeps it."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    install(rec, server_dir, one_shot=importing(rec))
    edited = SHIPPED.replace(b"AiPlayerbot.MaxAddedBots = 40", b"AiPlayerbot.MaxAddedBots = 3")
    assert edited != SHIPPED
    (server_dir / CONF).write_bytes(edited)

    again = Recorder(images=True, probe_answers=[IMPORTED])
    again.remotes[server_dir] = WOTLK.emulator.sources[0].url
    again.remotes[server_dir / "modules" / "mod-playerbots"] = WOTLK.emulator.sources[1].url
    lines = install(again, server_dir)
    assert (server_dir / CONF).read_bytes() == edited
    assert any("playerbots.conf" in line and "left as it is" in line for line in lines), lines


def test_with_no_dist_the_install_still_finishes_and_says_what_it_could_not_write(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "wow"
    lines = install(Recorder(images=False), server_dir)
    assert not (server_dir / CONF).exists()
    assert lines[-1].endswith(f"is installed and running in {server_dir}")
    assert any("playerbots.conf.dist" in line and "Repair" in line for line in lines), lines


def test_a_conf_that_cannot_be_written_does_not_fail_the_install(tmp_path: Path) -> None:
    """A real refusal: the etc folder read-only to this user. The install finishes and says so."""
    if os.geteuid() == 0:
        pytest.skip("root writes into a read-only folder")
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)

    def one_shot(
        service: str, where: Path, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        if service == WOTLK.containers.db_import:
            put_dist(where)
            os.chmod(where / DIST, 0o644)
            os.chmod((where / DIST).parent, 0o555)
        return rec.one_shot_result

    try:
        lines = install(rec, server_dir, one_shot=one_shot)
    finally:
        os.chmod((server_dir / DIST).parent, 0o755)
    assert not (server_dir / CONF).exists()
    assert lines[-1].endswith(f"is installed and running in {server_dir}")
    assert any("could not be written" in line for line in lines), lines
    assert [p.name for p in (server_dir / DIST).parent.iterdir()] == ["playerbots.conf.dist"]


# -- what reads it -----------------------------------------------------------------


def test_every_reader_of_the_deployed_conf_reads_the_installed_one(tmp_path: Path) -> None:
    """My Party, the bot prefix and the Tuning tab's shadow warning, on the file the install wrote.

    Each of these answered "nothing deployed" on a WotLK install before T137.
    """
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    install(rec, server_dir, one_shot=importing(rec))

    warrior = ("arms pve", "fury pve", "prot pve", "arms pvp", "fury pvp")
    assert party.spec_names(server_dir)[1] == warrior
    assert party.allow_flags(server_dir) == party.AllowFlags(True, True, True)
    assert party.max_added_bots(server_dir) == 40
    marker = dbreads.resolve_marker(WOTLK, server_dir).marker
    assert marker == dbreads.Marker("rndbot", "conf")
    text = (server_dir / CONF).read_text(encoding="utf-8")
    # The bot population is Yu'lon's, set in the override's environment, which
    # wins over this file: the Tuning tab now has a real file to warn about.
    assert composegen.shadowed_by_env(text, composegen.world_env(WOTLK)) == (
        ("AiPlayerbot.RandomBotAutologin", "AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN"),
        ("AiPlayerbot.MinRandomBots", "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"),
        ("AiPlayerbot.MaxRandomBots", "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"),
    )


# -- Repair server files ---------------------------------------------------------


def test_repair_is_wired_for_wotlk_and_for_no_cmangos_game(tmp_path: Path) -> None:
    assert repair_confs_for_app(WOTLK, tmp_path) is not None
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        assert repair_confs_for_app(load_catalog().get(game), tmp_path) is None


def test_an_install_with_the_dist_and_no_conf_is_offered_it(tmp_path: Path) -> None:
    put_dist(tmp_path)
    route = repair_confs_for_app(WOTLK, tmp_path)
    assert route is not None
    assert route.check() == native.ConfCheck(missing=(CONF,))


def test_an_install_with_its_conf_is_offered_nothing(tmp_path: Path) -> None:
    put_dist(tmp_path)
    (tmp_path / CONF).write_bytes(b"AiPlayerbot.MaxAddedBots = 3\n")
    route = repair_confs_for_app(WOTLK, tmp_path)
    assert route is not None
    assert route.check() == native.ConfCheck()


def test_an_install_with_no_dist_is_offered_nothing(tmp_path: Path) -> None:
    """Nothing to make it from, so a press could only fail."""
    route = repair_confs_for_app(WOTLK, tmp_path)
    assert route is not None
    assert route.check() == native.ConfCheck()


def test_repair_writes_the_dist_copy_and_is_then_offered_nothing(tmp_path: Path) -> None:
    put_dist(tmp_path, mode=0o640)
    route = repair_confs_for_app(WOTLK, tmp_path)
    assert route is not None
    assert route.repair() == native.ConfRepaired(written=(CONF,))
    assert (tmp_path / CONF).read_bytes() == SHIPPED
    assert stat.S_IMODE((tmp_path / CONF).stat().st_mode) == 0o644
    assert route.check() == native.ConfCheck()


def test_a_second_repair_writes_nothing_and_keeps_the_file(tmp_path: Path) -> None:
    put_dist(tmp_path)
    route = repair_confs_for_app(WOTLK, tmp_path)
    assert route is not None
    route.repair()
    (tmp_path / CONF).write_bytes(b"AiPlayerbot.MaxAddedBots = 3\n")
    assert route.repair() == native.ConfRepaired(written=())
    assert (tmp_path / CONF).read_bytes() == b"AiPlayerbot.MaxAddedBots = 3\n"


def test_a_repair_that_cannot_write_says_why_and_leaves_nothing_behind(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root writes into a read-only folder")
    folder = put_dist(tmp_path).parent
    os.chmod(folder, 0o555)
    route = repair_confs_for_app(WOTLK, tmp_path)
    assert route is not None
    try:
        with pytest.raises(InstallerError, match="could not be written"):
            route.repair()
    finally:
        os.chmod(folder, 0o755)
    assert [p.name for p in folder.iterdir()] == ["playerbots.conf.dist"]


def test_a_conf_that_is_there_is_left_alone_even_with_no_dist(tmp_path: Path) -> None:
    """Asked before the `.dist` is read: a person's conf is "already there", not "no .dist"."""
    conf = tmp_path / CONF
    conf.parent.mkdir(parents=True)
    conf.write_bytes(b"AiPlayerbot.MaxAddedBots = 3\n")
    assert azerothcore.write_from_dist(tmp_path, CONF) is False
    assert conf.read_bytes() == b"AiPlayerbot.MaxAddedBots = 3\n"


def test_a_failed_write_removes_its_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file's fsync fails: no temp, no conf, the `.dist` untouched."""
    folder = put_dist(tmp_path).parent

    def full(fd: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(OSError, match="No space left"):
        azerothcore.write_from_dist(tmp_path, CONF)
    assert [p.name for p in folder.iterdir()] == ["playerbots.conf.dist"]


PERSONS = b"AiPlayerbot.MaxAddedBots = 3\n"


def _appears_before_publication(monkeypatch: pytest.MonkeyPatch, conf: Path) -> None:
    """Somebody writes `conf` in the last step before the copy is published (Codex, round 2).

    The hook is the chmod of the finished temp file, which is the last thing
    before the publish: a check made before this moment cannot see the file.
    """
    real = os.chmod

    def chmod(path: object, mode: int, *a: object, **k: object) -> None:
        if str(path).endswith(".yulon-new") and not conf.exists():
            conf.write_bytes(PERSONS)
        real(path, mode, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "chmod", chmod)


def test_a_conf_that_appears_just_before_publication_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = put_dist(tmp_path).parent
    _appears_before_publication(monkeypatch, tmp_path / CONF)
    assert azerothcore.write_from_dist(tmp_path, CONF) is False
    assert (tmp_path / CONF).read_bytes() == PERSONS
    assert sorted(p.name for p in folder.iterdir()) == ["playerbots.conf", "playerbots.conf.dist"]


def _no_hard_links(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """A filesystem that refuses `link()`, as vfat does with EPERM."""
    asked: list[object] = []

    def link(src: object, dst: object, *a: object, **k: object) -> None:
        asked.append(dst)
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", link)
    return asked


def test_where_hard_links_are_refused_the_conf_is_created_exclusively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = put_dist(tmp_path, mode=0o640).parent
    asked = _no_hard_links(monkeypatch)
    # A strict umask, so the mode `os.open` asks for is cut to 0600 and only the
    # chmod on the new file's descriptor can make it readable by everyone.
    before = os.umask(0o077)
    try:
        assert azerothcore.write_from_dist(tmp_path, CONF) is True
    finally:
        os.umask(before)
    assert asked == [tmp_path / CONF], "the link was never tried"
    assert (tmp_path / CONF).read_bytes() == SHIPPED
    assert stat.S_IMODE((tmp_path / CONF).stat().st_mode) == 0o644
    assert sorted(p.name for p in folder.iterdir()) == ["playerbots.conf", "playerbots.conf.dist"]


def test_without_hard_links_a_conf_that_appears_first_is_still_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = put_dist(tmp_path).parent
    _no_hard_links(monkeypatch)
    _appears_before_publication(monkeypatch, tmp_path / CONF)
    assert azerothcore.write_from_dist(tmp_path, CONF) is False
    assert (tmp_path / CONF).read_bytes() == PERSONS
    assert sorted(p.name for p in folder.iterdir()) == ["playerbots.conf", "playerbots.conf.dist"]


def test_an_exclusive_create_that_fails_part_way_leaves_no_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half a conf would read as the person's own and never be offered again: it is removed."""
    folder = put_dist(tmp_path).parent
    _no_hard_links(monkeypatch)
    real = os.fsync
    calls: list[int] = []

    def fsync(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 2:  # the conf's own, after the temp file's
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="No space left"):
        azerothcore.write_from_dist(tmp_path, CONF)
    assert [p.name for p in folder.iterdir()] == ["playerbots.conf.dist"]


def test_a_failed_exclusive_create_never_removes_a_file_swapped_in_at_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex, round 3: the clean-up removed `target` BY NAME. Somebody who moves the half
    file away and puts their own conf at that name before the failure lands would lose it.
    Only the file this call created (its device and inode) is ever removed."""
    folder = put_dist(tmp_path).parent
    _no_hard_links(monkeypatch)
    conf = tmp_path / CONF
    real = os.fsync
    calls: list[int] = []

    def fsync(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 2:  # the conf's own, after the temp file's
            conf.rename(conf.with_name("moved-aside"))
            conf.write_bytes(PERSONS)
            raise OSError(errno.ENOSPC, "No space left on device")
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="No space left"):
        azerothcore.write_from_dist(tmp_path, CONF)
    assert conf.read_bytes() == PERSONS
    assert sorted(p.name for p in folder.iterdir()) == [
        "moved-aside",
        "playerbots.conf",
        "playerbots.conf.dist",
    ]


def test_a_folder_that_cannot_be_searched_is_offered_nothing_and_does_not_raise(
    tmp_path: Path,
) -> None:
    """Measured: `Path.exists()` raises PermissionError here on both CI legs (3.11 and 3.13)."""
    if os.geteuid() == 0:
        pytest.skip("root searches any folder")
    folder = put_dist(tmp_path).parent
    os.chmod(folder, 0o000)
    try:
        assert azerothcore.conf_check(WOTLK, tmp_path) == native.ConfCheck()
    finally:
        os.chmod(folder, 0o755)


def test_a_stopped_wsl_distro_is_not_read_and_gets_no_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading `\\wsl.localhost\\…` boots a stopped distro (T133), so the check waits for it.

    Through the real services builder, so the tab's wiring passes the distro on.
    """
    put_dist(tmp_path)
    asked: list[str] = []
    stopped = [True]

    def distro_state(distro: str) -> str:
        asked.append(distro)
        return "stopped" if stopped[0] else "running"

    monkeypatch.setattr(wsl, "distro_state", distro_state)
    route = ControllerServices.for_entry(WOTLK, tmp_path, wsl_distro="Ubuntu").repair_confs
    assert route is not None
    assert route.check() == native.ConfCheck()
    assert asked == ["Ubuntu"]
    stopped[0] = False
    assert route.check() == native.ConfCheck(missing=(CONF,))


# -- the Server tab ----------------------------------------------------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, tmp_path: Path) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.repair_confs = repair_confs_for_app(WOTLK, tmp_path)
    return ControllerView(WOTLK, services, status_poll_ms=0)


def _answer(monkeypatch: pytest.MonkeyPatch, yes: bool) -> list[str]:
    asked: list[str] = []
    from PySide6.QtWidgets import QMessageBox

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> int:
        asked.append(text)
        button = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        return int(button.value)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_a_wotlk_tab_without_the_conf_shows_the_repair_banner(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    put_dist(tmp_path)
    view = _view(ps, tmp_path)
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.text() == controller_view_module.REPAIR_FILES_LABEL
    said = view.compose_banner_label.text()
    assert "playerbots.conf" in said and "playerbots.conf.dist" in said
    assert "docker-compose.yml" not in said


def test_a_wotlk_tab_with_the_conf_shows_no_banner(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    put_dist(tmp_path)
    (tmp_path / CONF).write_bytes(SHIPPED)
    view = _view(ps, tmp_path)
    assert view.compose_banner.isHidden()


def test_pressing_repair_asks_first_and_no_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_dist(tmp_path)
    view = _view(ps, tmp_path)
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    assert len(asked) == 1
    assert "playerbots.conf.dist" in asked[0] and "restart" in asked[0].lower(), asked[0]
    assert not (tmp_path / CONF).exists()


def test_yes_writes_the_conf_then_offers_the_restart_that_loads_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_dist(tmp_path)
    view = _view(ps, tmp_path)
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert (tmp_path / CONF).read_bytes() == SHIPPED
    assert view.compose_banner_button.text() == TUNING_RESTART_LABEL
    assert "restart" in view.compose_banner_label.text().lower()
    assert CONF in view._tuning_owed.get("restart", set())
    assert not view.tuning_banner.isHidden()

    stopped: list[int] = []
    started: list[int] = []
    view.services.controller.stop = lambda: stopped.append(1) or True  # type: ignore
    view.services.controller.start = lambda: started.append(1)  # type: ignore
    view.compose_banner_button.click()
    assert (stopped, started) == ([1], [1]), "the banner's Restart did not restart"
    assert view.compose_banner.isHidden(), "the banner outlived the restart that applied it"


def test_a_failed_repair_says_why_and_still_offers_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root writes into a read-only folder")
    folder = put_dist(tmp_path).parent
    view = _view(ps, tmp_path)
    _answer(monkeypatch, yes=True)
    os.chmod(folder, 0o555)
    try:
        view.compose_banner_button.click()
    finally:
        os.chmod(folder, 0o755)
    assert "could not be written" in view.problem_label.text()
    assert view.compose_banner_button.text() == controller_view_module.REPAIR_FILES_LABEL
    assert CONF not in view._tuning_owed.get("restart", set())


def test_the_app_wires_the_route_into_a_wotlk_tab(tmp_path: Path) -> None:
    """The real services builder, not a test's assignment, gives the tab its route."""
    wired = ControllerServices.for_entry(WOTLK, tmp_path)
    assert wired.repair_confs is not None
    put_dist(tmp_path)
    assert wired.repair_confs.check() == native.ConfCheck(missing=(CONF,))


@pytest.mark.parametrize("channel_on", [False, True], ids=["channel-off", "channel-on"])
def test_the_tuning_tab_names_the_keys_the_channel_overrides_too(
    qapp: object, ps: _Ps, tmp_path: Path, channel_on: bool
) -> None:
    """Review round 2: with the channel on, the override also sets
    `AC_AI_PLAYERBOT_COMMAND_SERVER_PORT=0` (`operations.enable_env`), which the
    shipped `AiPlayerbot.CommandServerPort = 8888` loses to. The warning is asked
    of the environment the override really carries, `channel_world_env()`."""
    put_dist(tmp_path)
    (tmp_path / CONF).write_bytes(SHIPPED)
    if channel_on:
        # The channel's own record that its press stands (`composegen.channel_is_on`).
        (tmp_path / (composegen.OVERRIDE_FILE + composegen.CHANNEL_BACKUP_SUFFIX)).write_text(
            "services: {}\n", encoding="utf-8"
        )
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    view.open_tuning_file(CONF)
    said = view.tuning_panel.shadow_warning.text()
    assert "AiPlayerbot.MaxRandomBots" in said
    assert ("AiPlayerbot.CommandServerPort" in said) is channel_on, said
