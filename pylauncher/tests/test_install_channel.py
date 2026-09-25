"""`InstallChannel` — the channel state machine as the Server tab holds it (8.2a).

The module beneath this is pure and was tested that way. What is tested here is
the object the tab actually talks to: where it gets its state from when the app
opens, what it does when the credential it saved stops working, and the one
thing it must never do on that path — create a second account.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yulon import channel_setup as setup
from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
INSTALL = "ab12cd34"


class _Answering:
    """A channel that answers the same way every time, and counts the asks."""

    def __init__(
        self,
        outcome: str,
        *,
        indeterminate: bool = False,
        text: str = "",
        denied: bool = False,
    ) -> None:
        self.outcome = outcome
        self.indeterminate = indeterminate
        self.text = text
        self.denied = denied
        self.asked = 0

    def send(self, _command: object) -> object:
        self.asked += 1
        return type(
            "Answer",
            (),
            {
                "outcome": self.outcome,
                "text": self.text,
                "indeterminate": self.indeterminate,
                "denied": self.denied,
            },
        )()


class _Scripted:
    """A channel whose answers are given in order, one per ask.

    A "no" here is the server REJECTING the credential -- `denied` -- because
    that is the only refusal `check()` acts on, and these scripts exist to
    drive it into the repair path.
    """

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)

    def send(self, _command: object) -> object:
        outcome = self.outcomes.pop(0) if self.outcomes else "no"
        return type(
            "Answer",
            (),
            {
                "outcome": outcome,
                "text": "",
                "indeterminate": False,
                "denied": outcome == "no",
            },
        )()


def _installed(tmp_path: Path) -> Path:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    plan = composegen.render(WOTLK, server_dir, templates_root=resources.installers_dir())
    composegen.write_plan(plan, server_dir)
    return server_dir


def _channel(
    tmp_path: Path,
    *,
    answering: _Answering,
    create: object = None,
    reset: object = None,
) -> setup.InstallChannel:
    def refuse_create(*_args: object) -> object:
        raise AssertionError("an account was created")

    return setup.InstallChannel(
        WOTLK,
        _installed(tmp_path),
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=create or refuse_create,  # type: ignore[arg-type]
        reset=reset or (lambda *_a: None),
        channel_for=lambda _endpoint: answering,
        config_dir=tmp_path / "config",
    )


def _save(
    tmp_path: Path, *, password: str = "saved-password", at: str | None = "2026-09-07 01:23 UTC"
) -> None:
    setup.save_credential(
        setup.Verified(setup.account_name(INSTALL), password, at=at),
        game=WOTLK.id,
        install_id=INSTALL,
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
        config_dir=tmp_path / "config",
    )


def test_with_no_credential_on_disk_it_starts_idle(tmp_path: Path) -> None:
    channel = _channel(tmp_path, answering=_Answering("yes"))

    assert isinstance(channel.setup_state(), setup.Idle)


def test_a_saved_credential_reads_as_verified_with_the_time_it_was_proved(
    tmp_path: Path,
) -> None:
    """Because a credential is written only after a round trip answered.

    The file IS the record of that answer, so the time in it is not decoration:
    it is the difference between "this works" and "this worked once".
    """
    _save(tmp_path)

    channel = _channel(tmp_path, answering=_Answering("yes"))

    state = channel.setup_state()
    assert isinstance(state, setup.Verified)
    assert state.at == "2026-09-07 01:23 UTC"


def test_a_credential_written_before_times_existed_reads_as_verified_without_one(
    tmp_path: Path,
) -> None:
    """It must not be repaired, and it must not claim it was proved just now."""
    _save(tmp_path)
    path = setup.credential_path(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["verified_at"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    state = _channel(tmp_path, answering=_Answering("yes")).setup_state()

    assert isinstance(state, setup.Verified)
    assert state.at is None


def test_checking_a_credential_the_server_rejects_downgrades_it_to_refused(
    tmp_path: Path,
) -> None:
    _save(tmp_path)
    channel = _channel(tmp_path, answering=_Answering("no", text="401", denied=True))

    state = channel.check()

    assert isinstance(state, setup.Refused)
    assert channel.setup_state() is state


def test_a_server_that_cannot_be_reached_leaves_the_credential_alone(
    tmp_path: Path,
) -> None:
    """ "I could not ask" is not "your password is wrong".

    A stopped world would otherwise offer the user a repair for a problem that
    is not theirs, and the repair would reset a working password.
    """
    _save(tmp_path)
    channel = _channel(tmp_path, answering=_Answering("no", indeterminate=True))

    state = channel.check()

    assert isinstance(state, setup.Verified)


def test_a_server_that_is_simply_not_there_does_not_offer_to_reset_anything(
    tmp_path: Path,
) -> None:
    """An adversarial review's first finding, 2026-09-07.

    `check()` used to downgrade on anything that was not a yes and not a
    timeout. Connection refused, a socket error and an unreadable reply all
    arrive as `unknown` with `indeterminate` false, so opening the tab against
    a stopped server read as "your password is wrong" and offered to rotate a
    GM account's password to fix a container that was not running.

    Only the server saying it does not accept this credential may downgrade,
    which is what `denied` names.
    """
    _save(tmp_path)
    channel = _channel(tmp_path, answering=_Answering("unknown", denied=False))

    assert isinstance(channel.check(), setup.Verified)


def test_a_repair_interrupted_after_the_reset_is_recoverable_by_repairing_again(
    tmp_path: Path,
) -> None:
    """The window the review named, and what actually closes it.

    Repair writes the database first and the credential file last, so a crash
    in between leaves the server accepting a password nothing on disk knows.
    That is not a dead end: the stale file is refused, which is exactly the
    state `repair()` exists for, and the account is this app's own -- nobody
    plays it and nothing else uses it. Rotating again is the recovery, and it
    is the same button.

    A journal was considered and not written: it would put a second copy of a
    live credential on disk to protect an account whose only recovery cost is
    one more reset.
    """
    _save(tmp_path, password="stale")
    resets: list[str] = []
    # The state is already Refused -- that is what the interrupted attempt
    # left behind -- so this repair asks once, with the password it has just
    # written, and the server accepts it.
    answering = _Scripted(["yes"])
    channel = _channel(
        tmp_path,
        answering=answering,
        reset=lambda name, _pw: resets.append(name),
    )
    channel._state = setup.Refused(
        setup.account_name(INSTALL), "stale", reason="left behind by an interrupted repair"
    )

    state = channel.repair()

    assert isinstance(state, setup.Verified)
    assert resets == [setup.account_name(INSTALL)]
    saved = setup.load_credential(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert saved is not None and saved.password != "stale"


def test_checking_an_install_with_no_credential_asks_the_server_nothing(
    tmp_path: Path,
) -> None:
    answering = _Answering("yes")
    channel = _channel(tmp_path, answering=answering)

    channel.check()

    assert answering.asked == 0
    assert isinstance(channel.setup_state(), setup.Idle)


def test_repair_resets_the_existing_account_and_never_creates_another(
    tmp_path: Path,
) -> None:
    """The `create` seam this fixture installs raises if it is ever called."""
    _save(tmp_path, password="stale")
    resets: list[tuple[str, str]] = []
    # No to the stale credential, yes to the one the reset writes -- which is
    # the only sequence that exercises a repair rather than a no-op.
    answering = _Scripted(["no", "yes"])
    channel = _channel(
        tmp_path,
        answering=answering,
        reset=lambda name, password: resets.append((name, password)),
    )
    channel.check()

    state = channel.repair()

    assert isinstance(state, setup.Verified)
    assert state.at is not None
    assert [name for name, _ in resets] == [setup.account_name(INSTALL)]
    saved = setup.load_credential(WOTLK.id, INSTALL, config_dir=tmp_path / "config")
    assert saved is not None
    assert saved.password == resets[0][1]
    assert saved.password != "stale"


def test_repair_from_a_state_that_is_not_refused_does_nothing(tmp_path: Path) -> None:
    """Repair is a button, and a button can be pressed at the wrong moment.

    Resetting the password of an account that is working would take a channel
    that answers and break it for as long as the reset takes to prove.
    """
    _save(tmp_path)
    resets: list[object] = []
    channel = _channel(tmp_path, answering=_Answering("yes"), reset=lambda *a: resets.append(a))

    state = channel.repair()

    assert isinstance(state, setup.Verified)
    assert resets == []


def test_rolling_back_from_the_install_undoes_its_own_press(tmp_path: Path) -> None:
    channel = _channel(tmp_path, answering=_Answering("yes"))
    override = channel.server_dir / composegen.OVERRIDE_FILE
    before = override.read_text(encoding="utf-8")
    channel.enable(world_running=False)

    assert channel.roll_back() is True
    assert override.read_text(encoding="utf-8") == before


def test_the_press_still_refuses_while_the_world_is_running(tmp_path: Path) -> None:
    channel = _channel(tmp_path, answering=_Answering("yes"))

    with pytest.raises(setup.EnableRefused, match="stopped"):
        channel.enable(world_running=True)


def test_settle_creates_and_proves_a_channel_that_has_never_been_set_up(
    tmp_path: Path,
) -> None:
    """What the tab calls after a start, and the only thing that ever creates.

    Without it the press writes a configuration nobody ever proves: the
    account is never made, so the channel the user turned on never works.
    """
    made: list[tuple[str, str, int]] = []
    channel = _channel(
        tmp_path,
        answering=_Answering("yes"),
        create=lambda name, pw, level: made.append((name, pw, level)),
    )

    state = channel.settle()

    assert isinstance(state, setup.Verified)
    assert [name for name, _, _ in made] == [setup.account_name(INSTALL)]
    assert made[0][2] == 3, "the channel account must be a full administrator"


def test_settle_on_a_working_channel_checks_it_rather_than_creating_again(
    tmp_path: Path,
) -> None:
    """The latch, at the seam the tab presses on every start."""
    _save(tmp_path)
    answering = _Answering("yes")
    channel = _channel(tmp_path, answering=answering)

    state = channel.settle()

    assert isinstance(state, setup.Verified)
    assert answering.asked == 1


def test_settle_leaves_a_channel_that_gave_up_alone(tmp_path: Path) -> None:
    channel = _channel(tmp_path, answering=_Answering("yes"))
    channel._state = setup.GaveUp(account="YULON_AB12CD34", reason="three tries")

    assert isinstance(channel.settle(), setup.GaveUp)


class _Captures:
    """Keeps the endpoint it was handed, and refuses to send anything."""

    def __init__(self) -> None:
        self.endpoints: list[object] = []

    def __call__(self, endpoint: object) -> object:
        self.endpoints.append(endpoint)
        return self

    def send(self, _command: object) -> object:
        """Silence, so `prove()` gets as far as building its endpoint and stops."""
        return type("Answer", (), {"outcome": "unknown", "denied": False, "text": ""})()


def test_the_endpoint_carries_this_install_s_own_namespace(tmp_path: Path) -> None:
    """`Endpoint.namespace` has a default, and this is what stops anything using it.

    The default exists so a credential file written before the field did stays
    readable. Nothing inside the app may lean on it: TBC's listener answers
    `urn:MaNGOS`, and an `urn:AC` envelope comes back HTTP 500 `method name or
    namespace not recognized` -- which from the caller's side is
    indistinguishable from a world that has not finished loading. Measured on
    m910q, 2026-09-07, against a live CMaNGOS worldserver.
    """
    tbc = load_catalog().get("wow-tbc")
    captures = _Captures()
    channel = setup.InstallChannel(
        tbc,
        tmp_path,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_args: None,
        channel_for=captures,
        config_dir=tmp_path / "config",
    )
    setup.save_credential(
        setup.Verified(account="YULON_AB12CD34", password="pw", at="2026-09-07 09:00 UTC"),
        game=tbc.id,
        install_id=INSTALL,
        host="127.0.0.1",
        port=7878,
        namespace="urn:MaNGOS",
        config_dir=tmp_path / "config",
    )

    assert channel.live_channel() is not None

    assert captures.endpoints, "no endpoint was built"
    assert captures.endpoints[0].namespace == "urn:MaNGOS", captures.endpoints[0]
    assert tbc.operations is not None and tbc.operations.namespace == "urn:MaNGOS"


def test_every_endpoint_this_channel_builds_carries_the_namespace(tmp_path: Path) -> None:
    """Not one call site: every one, found by walking them.

    `live_channel()` was asserted first and `prove()` was the path that actually
    ran on the gate box -- it built its own endpoint and kept the field default,
    so TBC got `urn:AC`, got HTTP 500, and read as a world that had not answered.
    The lesson is already filed as `reviews-check-functions-not-call-sites`; this
    test is the version of it that cannot rot, because it asks the object for
    every endpoint it hands out rather than naming the ones known today.
    """
    tbc = load_catalog().get("wow-tbc")
    captures = _Captures()
    channel = setup.InstallChannel(
        tbc,
        tmp_path,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_args: None,
        reset=lambda *_args: None,
        channel_for=captures,
        config_dir=tmp_path / "config",
    )

    channel.prove()
    setup.save_credential(
        setup.Verified(account="YULON_AB12CD34", password="pw", at="2026-09-07 09:00 UTC"),
        game=tbc.id,
        install_id=INSTALL,
        host="127.0.0.1",
        port=7878,
        namespace="urn:MaNGOS",
        config_dir=tmp_path / "config",
    )
    channel.live_channel()

    assert len(captures.endpoints) >= 2, captures.endpoints
    assert {e.namespace for e in captures.endpoints} == {"urn:MaNGOS"}, captures.endpoints


def test_the_saved_credential_records_the_namespace_it_was_proved_with(
    tmp_path: Path,
) -> None:
    """The app overrides it on read, and the FILE still has to be right.

    `live_channel()` replaces whatever the file carries with the entry's value,
    so the app is safe either way. Anything else that reads the file is not: on
    m910q the 8.2c gate loaded the saved credential, sent `urn:AC` and got HTTP
    500 back from a channel that had just verified. A credential file that
    records a fact should record the true one.
    """
    tbc = load_catalog().get("wow-tbc")
    captures = _Captures()
    channel = setup.InstallChannel(
        tbc,
        tmp_path,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_args: None,
        reset=lambda *_args: None,
        channel_for=lambda _endpoint: _Answers(),
        config_dir=tmp_path / "config",
    )

    assert isinstance(channel.prove(), setup.Verified)

    saved = setup.load_credential(tbc.id, INSTALL, config_dir=tmp_path / "config")
    assert saved is not None
    assert saved.namespace == "urn:MaNGOS", saved
    del captures


class _Answers:
    def send(self, _command: object) -> object:
        return type("Answer", (), {"outcome": "yes", "denied": False, "text": "ok"})()


def test_a_proven_namespace_beats_the_catalog_and_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Adversarial review, 2026-09-07: the file is evidence, the catalog is a claim.

    A namespace that completed a real round trip is the one that worked. If the
    catalog later disagrees -- an edit, a lineage change, a fork that moved --
    replacing the proven value would break a working channel with exactly the
    opaque failure this field exists to prevent: HTTP 500, indistinguishable
    from a world still loading.

    So the file wins where it has an answer, and the disagreement is said out
    loud rather than resolved in silence.
    """
    tbc = load_catalog().get("wow-tbc")
    captures = _Captures()
    channel = setup.InstallChannel(
        tbc,
        tmp_path,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_args: None,
        channel_for=captures,
        config_dir=tmp_path / "config",
    )
    setup.save_credential(
        setup.Verified(account="YULON_AB12CD34", password="pw", at="2026-09-07 09:00 UTC"),
        game=tbc.id,
        install_id=INSTALL,
        host="127.0.0.1",
        port=7878,
        namespace="urn:SomethingElse",
        config_dir=tmp_path / "config",
    )

    with caplog.at_level("WARNING"):
        channel.live_channel()

    assert captures.endpoints[0].namespace == "urn:SomethingElse", captures.endpoints[0]
    assert any("urn:SomethingElse" in record.message for record in caplog.records), caplog.text


def test_a_credential_older_than_the_field_is_bootstrapped_from_the_catalog(
    tmp_path: Path,
) -> None:
    """The other half: no answer in the file means the catalog is all there is.

    A file written before 8.2c has no namespace in it, and reading one as
    `urn:AC` would hand AzerothCore's answer to whatever tree the file belongs
    to. `None` there says "this was never recorded", which is a different fact
    from "it was recorded as urn:AC".
    """
    tbc = load_catalog().get("wow-tbc")
    captures = _Captures()
    channel = setup.InstallChannel(
        tbc,
        tmp_path,
        templates_root=resources.installers_dir(),
        install_id=INSTALL,
        create=lambda *_args: None,
        channel_for=captures,
        config_dir=tmp_path / "config",
    )
    path = setup.credential_path(tbc.id, INSTALL, config_dir=tmp_path / "config")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "account": "YULON_AB12CD34",
                "password": "pw",
                "host": "127.0.0.1",
                "port": 7878,
                "verified_at": "2026-09-06 09:00 UTC",
            }
        ),
        encoding="utf-8",
    )

    channel.live_channel()

    assert captures.endpoints[0].namespace == "urn:MaNGOS", captures.endpoints[0]


# -- an enforcing SELinux host (T102) -----------------------------------------


def _on_selinux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fedora as `yulon-fedora` answered on 2026-09-24: enforcing, on xfs.

    Patched on `platform` and not handed in, because every `InstallChannel` the
    app builds is constructed bare: this is the route the shipped one takes.
    """
    from yulon import platform

    monkeypatch.setattr(platform, "selinux_enforcing", lambda: True)
    monkeypatch.setattr(platform, "filesystem_type", lambda _path: "xfs")


def _labelled_channel(tmp_path: Path) -> setup.InstallChannel:
    """`_channel()` over an install rendered as that host renders it, `:z` on every bind."""
    channel = _channel(tmp_path, answering=_Answering("yes"))
    plan = composegen.render(
        WOTLK, channel.server_dir, templates_root=resources.installers_dir(), bind_label=":z"
    )
    for name, text in (
        (composegen.BASE_FILE, plan.base),
        (composegen.OVERRIDE_FILE, plan.override),
    ):
        (channel.server_dir / name).write_text(text, encoding="utf-8", newline="\n")
    return channel


def _channel_on(server_dir: Path, *, bind_label: str) -> str:
    """The override with the channel on, rendered with the given label."""
    operations = WOTLK.operations
    assert operations is not None
    return composegen.render(
        WOTLK,
        server_dir,
        templates_root=resources.installers_dir(),
        world_env=setup._world_env(WOTLK, operations.enable_env),
        bind_label=bind_label,
    ).override


def _rolled_all_the_way_back(channel: setup.InstallChannel, installed: str) -> None:
    override = channel.server_dir / composegen.OVERRIDE_FILE
    assert override.read_text(encoding="utf-8") == installed
    assert "AC_SOAP_ENABLED" not in override.read_text(encoding="utf-8")
    assert not override.with_name(override.name + setup.BACKUP_SUFFIX).exists()


def test_a_labelled_override_with_the_channel_on_is_still_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The channel must not stick on because the override carries the install's `:z`.

    `roll_back()` restores only an override that is exactly what the press
    would write now, and the comparison was rendered with no label. On an
    enforcing host, an override that says channel-on WITH the install's `:z`
    -- which is what the install's own renderer, and a settings reset through
    it, writes -- read as "somebody edited this". Measured on `yulon-fedora`
    2026-09-24: `AC_SOAP_ENABLED` stayed, the `.before-channel` backup stayed,
    and only the port was released.
    """
    _on_selinux(monkeypatch)
    channel = _labelled_channel(tmp_path)
    override = channel.server_dir / composegen.OVERRIDE_FILE
    installed = override.read_text(encoding="utf-8")
    channel.enable(world_running=False)
    override.write_text(_channel_on(channel.server_dir, bind_label=":z"), encoding="utf-8")

    assert channel.roll_back() is True

    _rolled_all_the_way_back(channel, installed)


def test_a_press_made_before_the_label_was_kept_is_still_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix must not strand the installs the unfixed press already wrote.

    Before T102 the press wrote the channel on WITHOUT the label, so an
    enforcing host upgraded from that version has exactly that file on disk. A
    rollback comparing only against the labelled text would call it edited and
    leave the channel on -- the same stuck state, reached from the other side.
    """
    _on_selinux(monkeypatch)
    channel = _labelled_channel(tmp_path)
    override = channel.server_dir / composegen.OVERRIDE_FILE
    installed = override.read_text(encoding="utf-8")
    override.with_name(override.name + setup.BACKUP_SUFFIX).write_text(installed, encoding="utf-8")
    override.write_text(_channel_on(channel.server_dir, bind_label=""), encoding="utf-8")

    assert channel.roll_back() is True

    _rolled_all_the_way_back(channel, installed)


def test_the_press_from_the_install_keeps_its_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tab's own route: `InstallChannel.enable()` on an enforcing host keeps `:z`."""
    _on_selinux(monkeypatch)
    channel = _labelled_channel(tmp_path)
    installed = channel.server_dir / composegen.OVERRIDE_FILE

    channel.enable(world_running=False)

    assert installed.read_text(encoding="utf-8") == _channel_on(channel.server_dir, bind_label=":z")


@pytest.mark.parametrize("later", [False, None], ids=["permissive-now", "cannot-tell-now"])
def test_a_labelled_press_is_rolled_back_after_the_host_stops_enforcing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, later: bool | None
) -> None:
    """The label on disk is what the press wrote THEN, not what the host says NOW.

    Pressed while Enforcing, the override carries `:z`. A later `setenforce 0`,
    or a probe that cannot answer, makes the host's label `""` -- and a rollback
    that compared only against the host's current label read the press's own
    file as edited and left the channel on (review of `70d0d642`).
    """
    from yulon import platform

    _on_selinux(monkeypatch)
    channel = _labelled_channel(tmp_path)
    override = channel.server_dir / composegen.OVERRIDE_FILE
    installed = override.read_text(encoding="utf-8")
    channel.enable(world_running=False)
    assert override.read_text(encoding="utf-8") == _channel_on(channel.server_dir, bind_label=":z")
    monkeypatch.setattr(platform, "selinux_enforcing", lambda: later)

    assert channel.roll_back() is True

    _rolled_all_the_way_back(channel, installed)


@pytest.mark.parametrize("label", [":z", ""], ids=["labelled", "unlabelled"])
def test_a_channel_override_edited_in_another_way_is_still_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    """Accepting both labels must not widen into accepting any file.

    The two texts the rollback recognises differ only in the `:z` token, so a
    change anywhere else -- here a line a person added -- matches neither: the
    override is kept, and only the port is released.
    """
    _on_selinux(monkeypatch)
    channel = _labelled_channel(tmp_path)
    override = channel.server_dir / composegen.OVERRIDE_FILE
    channel.enable(world_running=False)
    edited = _channel_on(channel.server_dir, bind_label=label) + "# a person was here\n"
    override.write_text(edited, encoding="utf-8")

    assert channel.roll_back() is True

    assert override.read_text(encoding="utf-8") == edited
    assert override.with_name(override.name + setup.BACKUP_SUFFIX).exists()
    env = (channel.server_dir / ".env").read_text(encoding="utf-8")
    assert f"{setup.HOST_PORT_VAR}={setup.RELEASED_HOST_PORT}" in env


def test_the_two_recognised_texts_differ_only_in_the_label() -> None:
    """The claim the two tests above rest on, checked on the rendered text itself.

    Remove every `:z` that ends a host bind from the labelled channel-on
    override and what is left must be the unlabelled one, byte for byte -- so no
    other difference can ride in on the second candidate.
    """
    server_dir = Path("/srv/wotlk")
    labelled = _channel_on(server_dir, bind_label=":z")
    plain = _channel_on(server_dir, bind_label="")
    binds = [line for line in labelled.splitlines() if line.strip().startswith("- ./")]
    assert binds and all(line.endswith(":z") for line in binds)

    stripped = "\n".join(
        line[: -len(":z")] if line.strip().startswith("- ./") else line
        for line in labelled.splitlines()
    )

    assert stripped + "\n" == plain
