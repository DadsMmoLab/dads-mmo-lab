"""A channel account made before the app closed is still this app's after it opens (T138).

Enable, Start, and the settle that follows creates the channel's account with
a password generated for it. The world answers SOAP about 40 s after it says
it is up, so that first round trip usually times out and the setup sits in
`Pending` -- and until T138 the password lived only in memory there. Closing
the app in that window threw it away: the next launch started from `Idle`,
minted a second password, `create` kept the row's first one (it never re-salts
a row that exists), and every round trip from then on was a 401 read as
`Refused`. Repair got out of it; nothing should have needed to.

Every test here drives the real `InstallChannel` over the real credential
store in a scratch config dir, through the real `SoapChannel`, against a fake
world that is the only fake: an auth table that behaves as `create_account`
and `reset_own_password` do, and a SOAP listener that times out while the
world is loading and then checks the password it is sent.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from yulon import channel as channel_module
from yulon import channel_setup as setup
from yulon import docker, resources, soap
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
INSTALL = "ab12cd34"
ACCOUNT = setup.account_name(INSTALL)


class _World:
    """One server's auth table and its SOAP listener, and nothing else.

    `create` keeps the password of a row that exists -- the one property of
    `create_account` the bug turns on -- and `reset` rewrites only a row that
    exists, as `reset_own_password`'s `UPDATE` does.
    """

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.loading = True
        self.creates: list[str] = []
        self.resets = 0

    def create(self, name: str, password: str, _level: int) -> None:
        self.creates.append(name)
        self.rows.setdefault(name, password)

    def reset(self, name: str, password: str) -> None:
        self.resets += 1
        if name in self.rows:
            self.rows[name] = password

    def send(self, endpoint: soap.Endpoint, _command: str, *, timeout: float) -> soap.Reply:
        _ = timeout
        if self.loading:
            return soap.Reply("timeout")
        if self.rows.get(endpoint.account) == endpoint.password:
            return soap.Reply("answered", "Players online: 0.")
        return soap.Reply("unauthorised", http_status=401)


def _server(tmp_path: Path) -> Path:
    server_dir = tmp_path / "server"
    if not server_dir.is_dir():
        server_dir.mkdir()
        plan = composegen.render(WOTLK, server_dir, templates_root=resources.installers_dir())
        composegen.write_plan(plan, server_dir)
    return server_dir


def _launch(tmp_path: Path, world: _World) -> setup.InstallChannel:
    """What `_build_services` wires for one run of the app, over one world."""
    return setup.InstallChannel(
        WOTLK,
        _server(tmp_path),
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=world.create,
        reset=world.reset,
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=endpoint,
            state_of=lambda: docker.ContainerState(status="running"),
            send=world.send,
        ),
        config_dir=tmp_path / "config",
    )


def _enabled_and_started(tmp_path: Path, world: _World) -> setup.InstallChannel:
    """Enable while stopped, then the settle a finished Start runs, into a loading world."""
    first = _launch(tmp_path, world)
    first.enable(world_running=False)
    assert isinstance(first.settle(), setup.Pending), "the world was loading: still pending"
    assert world.creates == [ACCOUNT]
    return first


# -- the path the ticket names -------------------------------------------------


def test_a_launch_after_closing_while_pending_verifies_on_opening_the_tab(
    tmp_path: Path,
) -> None:
    """Enable, Start, close inside the window, reopen: the tab's own check proves it.

    The server was left running (closing the app stops nothing), so the check
    the tab makes on opening is the only thing that asks. It asks with the
    password the first run minted -- the one the row has.
    """
    world = _World()
    _enabled_and_started(tmp_path, world)
    world.loading = False  # the ~40 s pass while the app is closed

    reopened = _launch(tmp_path, world)
    state = reopened.check()

    assert isinstance(state, setup.Verified), state
    saved = setup.load_credential(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert saved is not None and saved.password == world.rows[ACCOUNT]
    assert world.creates == [ACCOUNT], "the reopened app created again"
    assert world.resets == 0, "a repair was needed"


def test_a_launch_after_closing_while_pending_verifies_on_the_next_start(
    tmp_path: Path,
) -> None:
    """The same close, then the settle a Start runs: Verified, not Refused.

    The unfixed code minted a second password here, `create` kept the row's
    first, and the round trip was a 401 -- the `Refused` the ticket reports.
    """
    world = _World()
    _enabled_and_started(tmp_path, world)
    world.loading = False

    state = _launch(tmp_path, world).settle()

    assert isinstance(state, setup.Verified), state
    assert world.creates == [ACCOUNT]


def test_a_reopened_app_that_still_cannot_ask_stays_pending_and_never_creates(
    tmp_path: Path,
) -> None:
    """Across launches the latch holds: re-verify, never re-create.

    Asked twice after the reopen, because "does not create" is a claim about
    every ask and not the first one.
    """
    world = _World()
    _enabled_and_started(tmp_path, world)

    reopened = _launch(tmp_path, world)
    assert isinstance(reopened.setup_state(), setup.Pending)
    assert isinstance(reopened.check(), setup.Pending)
    assert isinstance(reopened.settle(), setup.Pending)

    assert world.creates == [ACCOUNT]
    assert setup.load_credential(WOTLK.id, INSTALL, config_dir=tmp_path / "config") is None


def test_a_check_that_gets_no_answer_costs_a_pending_channel_none_of_its_tries(
    tmp_path: Path,
) -> None:
    """Opening the tab is a look, not a setup step.

    `MAX_VERIFY_TRIES` checks against a server that cannot answer yet -- one
    per reopen of the app -- and the channel is still `Pending`, not given up,
    so the Start that follows still proves it.
    """
    world = _World()
    _enabled_and_started(tmp_path, world)

    reopened = _launch(tmp_path, world)
    for _ in range(setup.MAX_VERIFY_TRIES):
        assert isinstance(reopened.check(), setup.Pending)
    world.loading = False

    assert isinstance(reopened.settle(), setup.Verified)
    assert world.creates == [ACCOUNT]


def test_a_pending_credential_the_server_rejects_points_to_repair_and_repair_works(
    tmp_path: Path,
) -> None:
    """A rejection is still `Refused`, and Repair still gets out of it.

    Someone changed the row's password while the app was closed. The pending
    record is then wrong, and the way out is the one a stale verified
    credential has -- reset the account the app already owns.
    """
    world = _World()
    _enabled_and_started(tmp_path, world)
    world.loading = False
    world.rows[ACCOUNT] = "someone-else-set-this"

    reopened = _launch(tmp_path, world)
    assert isinstance(reopened.check(), setup.Refused)
    state = reopened.repair()

    assert isinstance(state, setup.Verified)
    assert world.creates == [ACCOUNT]
    saved = setup.load_credential(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert saved is not None and saved.password == world.rows[ACCOUNT]
    assert not setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config").exists()


# -- the pending record itself -------------------------------------------------


def test_a_create_that_fails_leaves_nothing_a_later_launch_would_trust(
    tmp_path: Path,
) -> None:
    """The record says the row exists, so it is written only after `create` returned.

    Written before it, a create that failed would leave a launch that reads the
    record, skips the create and asks with a password no row has.
    """
    world = _World()

    def fails(_name: str, _password: str, _level: int) -> None:
        raise RuntimeError("the database refused the statement")

    first = _launch(tmp_path, world)
    first._create = fails
    with pytest.raises(RuntimeError):
        first.settle()

    assert not setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config").exists()
    assert isinstance(_launch(tmp_path, world).setup_state(), setup.Idle)


def test_proving_it_removes_the_pending_record_and_the_next_launch_reads_verified(
    tmp_path: Path,
) -> None:
    """Promoted once, and read back as the verified credential after that."""
    world = _World()
    _enabled_and_started(tmp_path, world)
    pending = setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert pending.is_file(), "nothing was kept for the next launch"
    world.loading = False

    assert isinstance(_launch(tmp_path, world).check(), setup.Verified)
    assert not pending.exists()

    third = _launch(tmp_path, world)
    assert isinstance(third.setup_state(), setup.Verified)


def test_a_verified_credential_wins_over_a_pending_record_left_beside_it(
    tmp_path: Path,
) -> None:
    """A crash between writing the verified file and removing the pending one."""
    config = tmp_path / "config"
    setup.save_pending(
        setup.Pending(account=ACCOUNT, password="the-older-password"),
        game=WOTLK.id,
        install_id=INSTALL,
        config_dir=config,
    )
    verified = setup.credential_path(WOTLK.id, INSTALL, config_dir=config)
    verified.write_text(
        json.dumps(
            {
                "account": ACCOUNT,
                "password": "the-proved-password",
                "namespace": "urn:AC",
                "host": "127.0.0.1",
                "port": 7878,
                "verified_at": "2026-09-27 01:00 UTC",
            }
        ),
        encoding="utf-8",
    )

    state = _launch(tmp_path, _World()).setup_state()

    assert isinstance(state, setup.Verified)
    assert state.password == "the-proved-password"


def test_the_pending_record_is_created_private_by_its_open_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verified file's rule (`CREDENTIAL_MODE`), for the same password a minute earlier."""
    seen: list[tuple[str, int, int]] = []
    real_open = os.open

    def watched(path, flags, mode=0o777, **kwargs):  # noqa: ANN001, ANN202
        seen.append((str(path), flags, mode))
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(setup.os, "open", watched)

    _enabled_and_started(tmp_path, _World())

    pending_dir = str(setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config").parent)
    made = [
        (flags, mode)
        for path, flags, mode in seen
        if path.startswith(pending_dir) and flags & os.O_CREAT
    ]
    assert made, "the pending record was not created through os.open"
    assert all(mode == setup.CREDENTIAL_MODE for _, mode in made)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not enforced on Windows")
def test_and_on_a_posix_box_the_pending_record_really_is_private(tmp_path: Path) -> None:
    _enabled_and_started(tmp_path, _World())

    path = setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_write_that_dies_part_way_leaves_the_record_it_was_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic: the old record or the new one, never a half-written file."""
    config = tmp_path / "config"
    setup.save_pending(
        setup.Pending(account=ACCOUNT, password="first-password-1"),
        game=WOTLK.id,
        install_id=INSTALL,
        config_dir=config,
    )

    def dies(_fd: int) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(setup.os, "fsync", dies)
    with pytest.raises(OSError):
        setup.save_pending(
            setup.Pending(account=ACCOUNT, password="second-password-2"),
            game=WOTLK.id,
            install_id=INSTALL,
            config_dir=config,
        )

    kept = setup.load_pending(WOTLK.id, INSTALL, config_dir=config)
    assert kept is not None and kept.password == "first-password-1"
    leftovers = [
        p.name for p in setup.pending_path(WOTLK.id, INSTALL, config_dir=config).parent.iterdir()
    ]
    assert leftovers == [f"{WOTLK.id}-{INSTALL}.json"], leftovers


def test_a_pending_record_that_cannot_be_written_does_not_stop_the_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """This run still holds the password; losing the record costs only a later launch."""
    world = _World()

    def full(*_a: object, **_k: object) -> object:
        raise OSError("no space left on device")

    monkeypatch.setattr(setup, "save_pending", full)
    first = _launch(tmp_path, world)
    with caplog.at_level(logging.INFO):
        assert isinstance(first.settle(), setup.Pending)
    world.loading = False

    assert isinstance(first.settle(), setup.Verified)
    assert "could not keep" in caplog.text


def test_no_password_reaches_a_log_on_the_whole_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World()
    with caplog.at_level(logging.DEBUG):
        _enabled_and_started(tmp_path, world)
        world.loading = False
        _launch(tmp_path, world).check()

    assert world.rows[ACCOUNT] not in caplog.text


def test_an_unreadable_pending_record_reads_as_nothing_rather_than_raising(
    tmp_path: Path,
) -> None:
    """A stale or hand-edited file must not stop the app opening (`load_credential`'s rule)."""
    path = setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    assert setup.load_pending(WOTLK.id, INSTALL, config_dir=tmp_path / "config") is None
    assert isinstance(_launch(tmp_path, _World()).setup_state(), setup.Idle)


# -- round 2: the writer under contention, and a promotion that cannot land ------


def _pending(password: str) -> setup.Pending:
    return setup.Pending(account=ACCOUNT, password=password)


def test_two_writers_at_once_never_remove_each_others_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check and a settle, or two app windows, writing the same record together.

    The second writer runs in full while the first is between its write and
    its rename. With one shared temporary name the second removed the first's
    file, and the first's rename then found nothing to rename.
    """
    config = tmp_path / "config"
    real_fsync = os.fsync
    inside: list[bool] = []

    def second_writer_arrives(fd: int) -> None:
        real_fsync(fd)
        if not inside:
            inside.append(True)
            setup.save_pending(
                _pending("second-writer-22"), game=WOTLK.id, install_id=INSTALL, config_dir=config
            )

    monkeypatch.setattr(setup.os, "fsync", second_writer_arrives)
    setup.save_pending(
        _pending("first-writer-111"), game=WOTLK.id, install_id=INSTALL, config_dir=config
    )

    kept = setup.load_pending(WOTLK.id, INSTALL, config_dir=config)
    assert kept is not None and kept.password == "first-writer-111", "the last rename wins"
    folder = setup.pending_path(WOTLK.id, INSTALL, config_dir=config).parent
    assert [p.name for p in folder.iterdir()] == [f"{WOTLK.id}-{INSTALL}.json"]


@pytest.mark.skipif(os.name == "nt", reason="a folder cannot be fsync'd through os.open on Windows")
def test_the_rename_is_made_durable_by_syncing_the_folder_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename lives in the folder's entry, and a power cut can lose an unsynced one."""
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        events.append("fsync-folder" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-file")
        real_fsync(fd)

    def replace(src: object, dst: object) -> None:
        events.append("replace")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(setup.os, "fsync", fsync)
    monkeypatch.setattr(setup.os, "replace", replace)
    setup.save_pending(
        _pending("durable-password"), game=WOTLK.id, install_id=INSTALL, config_dir=tmp_path
    )

    assert events == ["fsync-file", "replace", "fsync-folder"], events


def _refusing_replace(
    monkeypatch: pytest.MonkeyPatch, *, times: int, only: Path | None = None
) -> list[str]:
    """`os.replace` refused as Windows refuses it while a reader holds the target."""
    real_replace = os.replace
    refused: list[str] = []

    def replace(src: object, dst: object) -> None:
        if (only is None or Path(str(dst)) == only) and len(refused) < times:
            refused.append(str(dst))
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(setup.os, "replace", replace)
    return refused


def test_on_windows_a_rename_refused_once_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup, "_ON_WINDOWS", True, raising=False)
    monkeypatch.setattr(setup, "REPLACE_PAUSE", 0.0, raising=False)
    refused = _refusing_replace(monkeypatch, times=1)

    setup.save_pending(
        _pending("second-try-works"), game=WOTLK.id, install_id=INSTALL, config_dir=tmp_path
    )

    assert len(refused) == 1
    kept = setup.load_pending(WOTLK.id, INSTALL, config_dir=tmp_path)
    assert kept is not None and kept.password == "second-try-works"


def test_on_windows_a_rename_refused_every_time_gives_up_after_a_bounded_number_of_tries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup, "_ON_WINDOWS", True, raising=False)
    monkeypatch.setattr(setup, "REPLACE_PAUSE", 0.0, raising=False)
    refused = _refusing_replace(monkeypatch, times=1000)

    with pytest.raises(PermissionError):
        setup.save_pending(
            _pending("never-lands-1234"), game=WOTLK.id, install_id=INSTALL, config_dir=tmp_path
        )

    assert len(refused) == setup.REPLACE_TRIES > 1
    folder = setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path).parent
    assert list(folder.iterdir()) == [], "the failed write left its temporary file"


def test_off_windows_a_refused_rename_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A POSIX rename is not refused for a reader holding the file; a refusal there is real."""
    monkeypatch.setattr(setup, "_ON_WINDOWS", False, raising=False)
    refused = _refusing_replace(monkeypatch, times=1000)

    with pytest.raises(PermissionError):
        setup.save_pending(
            _pending("posix-refusal-12"), game=WOTLK.id, install_id=INSTALL, config_dir=tmp_path
        )

    assert len(refused) == 1


def test_a_proved_channel_whose_credential_cannot_be_written_stays_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The round trip answered and the file would not land: the settle does not fail.

    It stays `Pending` with its record intact, so the next ask -- or the next
    launch -- promotes it with the password the row has.
    """
    world = _World()
    first = _enabled_and_started(tmp_path, world)
    world.loading = False
    monkeypatch.setattr(setup, "_ON_WINDOWS", False, raising=False)
    target = setup.credential_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    refused = _refusing_replace(monkeypatch, times=1000, only=target)
    before = first.setup_state()
    assert isinstance(before, setup.Pending)

    with caplog.at_level(logging.WARNING):
        state = first.settle()

    assert refused, "the promotion never tried to write"
    assert isinstance(state, setup.Pending), state
    assert state.tries == before.tries, "the server answered: no try is spent"
    assert "could not save" in caplog.text
    assert world.rows[ACCOUNT] not in caplog.text
    kept = setup.load_pending(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert kept is not None and kept.password == world.rows[ACCOUNT]

    monkeypatch.undo()
    assert isinstance(first.settle(), setup.Verified)
    assert world.creates == [ACCOUNT]


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a folder's write bit stops no unlink on Windows, nor for root",
)
def test_a_promotion_whose_pending_record_will_not_go_is_still_verified(tmp_path: Path) -> None:
    """The verified file landed; a record that outlives it is read second and is harmless.

    Only the purge needs the strict removal. Here a failure to remove it must
    not turn a proved channel back into a pending one.
    """
    world = _World()
    first = _enabled_and_started(tmp_path, world)
    world.loading = False
    folder = setup.pending_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config").parent
    folder.chmod(0o500)
    try:
        state = first.settle()
    finally:
        folder.chmod(0o700)

    assert isinstance(state, setup.Verified), state
    assert isinstance(_launch(tmp_path, world).setup_state(), setup.Verified)
