"""The enable press: writing the channel on, with the world stopped (8.2a).

The press is the risky half of this box, and its shape came out of the
adversarial review. The version before it probed that the port was free and then
wrote — check-then-act, with a window between the probe and the bind, and a bind
that can fail for reasons a probe cannot see. On the CMaNGOS trees that costs
`exit(-1)` with no character saves.

Requiring the world **stopped** removes the hazard instead of warning about it,
and it is smaller than the guard it replaced: the configuration is written while
the server is down, the user's ordinary Start brings it up through the staged
start Phase 7 already proved, and there is nothing running to interrupt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import channel_setup as setup
from yulon import platform, resources
from yulon.catalog.catalog import load_catalog
from yulon.catalog.composegen import BASE_FILE, BUILD_FILE, OVERRIDE_FILE

WOTLK = load_catalog().get("wow-wotlk")
TBC = load_catalog().get("wow-tbc")


@pytest.fixture(autouse=True)
def _a_host_without_selinux(monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine these tests describe has no SELinux, unless a test says it has.

    The press asks the host for the install's bind label (T102), so without
    this every byte comparison below would mean something different on a
    Fedora test box than on the Ubuntu CI runner.
    """
    monkeypatch.setattr(platform, "selinux_enforcing", lambda: False)


CONF_BEFORE = (
    "# a mangosd.conf, as the install leaves it\n"
    "Console.Enable = 1\n"
    "Ra.Enable = 0\n"
    "SOAP.Enabled = 0\n"
    "SOAP.IP = 127.0.0.1\n"
    "SOAP.Port = 7878\n"
)


def _installed(tmp_path: Path) -> Path:
    """A server dir with the three compose files, as an install has them."""
    from yulon.catalog import composegen

    plan = composegen.render(WOTLK, tmp_path, templates_root=resources.installers_dir())
    composegen.write_plan(plan, tmp_path)
    return tmp_path


def test_the_press_refuses_while_the_world_is_running_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """The whole shape of this step, in one assertion.

    A failed bind is not something a probe can prevent, so the press does not
    try: it declines to run at all while there is a world to lose.
    """
    server_dir = _installed(tmp_path)
    before = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")

    with pytest.raises(setup.EnableRefused, match="stopped"):
        setup.enable(
            WOTLK,
            server_dir,
            templates_root=resources.installers_dir(),
            world_running=True,
        )

    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") == before


def test_the_press_writes_the_keys_that_turn_the_channel_on(tmp_path: Path) -> None:
    server_dir = _installed(tmp_path)

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    written = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    assert 'AC_SOAP_ENABLED: "1"' in written
    assert 'AC_SOAP_IP: "0.0.0.0"' in written
    assert 'AC_AI_PLAYERBOT_COMMAND_SERVER_PORT: "0"' in written
    assert 'AC_RA_ENABLE: "0"' in written


def test_the_press_keeps_what_the_install_already_had(tmp_path: Path) -> None:
    """The bot population is in that same block, and losing it is a different server."""
    server_dir = _installed(tmp_path)
    before = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    kept = [line for line in before.splitlines() if "AC_AI_PLAYERBOT" in line]
    assert kept, "the fixture has no existing env to preserve; this test proves nothing"

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    written = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    for line in kept:
        if "COMMAND_SERVER_PORT" in line:
            continue  # this one the press deliberately changes
        assert line in written


def test_a_second_press_changes_nothing(tmp_path: Path) -> None:
    """Idempotent, and it says so: `changed` is False the second time."""
    server_dir = _installed(tmp_path)

    first = setup.enable(
        WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False
    )
    after_first = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    second = setup.enable(
        WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False
    )

    assert first.changed is True
    assert second.changed is False
    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") == after_first


def test_the_press_touches_only_the_override(tmp_path: Path) -> None:
    """The reason the SOAP environment lives in this block and not in the install's.

    Phase 7.1's compose fixtures assert byte-identical rendered files. If this
    press rewrote the base or the build file, that assertion would start
    meaning something else — and nobody would notice until it mattered.
    """
    server_dir = _installed(tmp_path)
    untouched = {
        name: (server_dir / name).read_text(encoding="utf-8") for name in (BASE_FILE, BUILD_FILE)
    }

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    for name, text in untouched.items():
        assert (server_dir / name).read_text(encoding="utf-8") == text, f"{name} was rewritten"


def test_an_entry_with_no_operations_block_is_refused_rather_than_guessed_at(
    tmp_path: Path,
) -> None:
    """8.2d and 8.2e each measure their own; nothing is inherited early.

    Against a SYNTHETIC entry since 8.2e, and that is the end of a migration:
    this test named wow-tbc, then wow-vanilla, then wow-tortoise, moving each
    time a tree gained a channel -- because a test asserting the refusal against
    a real entry starts asserting that the feature is missing from the tree it
    was just added to. Every tree has one now. The refusal is still worth
    pinning, so what it is pinned against is an entry with the block removed
    rather than whichever game happens to be last.
    """
    unmeasured = WOTLK.model_copy(update={"operations": None})

    with pytest.raises(setup.EnableRefused, match="wow-wotlk"):
        setup.enable(
            unmeasured, tmp_path, templates_root=resources.installers_dir(), world_running=False
        )


# -- the port claim, and rolling it back (8.2a) ------------------------------


def test_the_press_claims_the_host_port_in_dotenv_so_the_claim_can_be_released(
    tmp_path: Path,
) -> None:
    """The claim is written even though it equals the compose default.

    The base compose publishes SOAP at `127.0.0.1:7878` by its DEFAULT VALUE,
    so before this the port was claimed by every install whether or not it had
    a channel — and nothing could give it back. Writing the claim down is what
    makes releasing it possible: `roll_back()` has a key to change.
    """
    server_dir = _installed(tmp_path)

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    env = (server_dir / ".env").read_text(encoding="utf-8")
    assert f"{setup.HOST_PORT_VAR}=127.0.0.1:7878" in env


def test_rolling_back_restores_the_override_the_press_replaced(tmp_path: Path) -> None:
    server_dir = _installed(tmp_path)
    before = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)
    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") != before

    rolled = setup.roll_back(WOTLK, server_dir)

    assert rolled is True
    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") == before


def test_rolling_back_releases_the_host_port_so_the_server_starts_again(
    tmp_path: Path,
) -> None:
    """The clause this exists for.

    An occupied 7878 stops the CONTAINER from being created at all — Docker
    refuses to publish a port something else holds — so restoring the
    environment alone would leave the install exactly as unstartable as it was.
    The claim goes back to `127.0.0.1:0`, which asks the daemon for any free
    port: the server starts, and the channel is not reachable at a port it does
    not own, which is what rolled back means.
    """
    server_dir = _installed(tmp_path)
    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    setup.roll_back(WOTLK, server_dir)

    env = (server_dir / ".env").read_text(encoding="utf-8")
    assert f"{setup.HOST_PORT_VAR}=127.0.0.1:0" in env
    assert f"{setup.HOST_PORT_VAR}=127.0.0.1:7878" not in env


def test_rolling_back_with_nothing_to_undo_says_so_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """A rollback that never had a press behind it must not invent one.

    Restoring "the state before" from a backup that does not exist would write
    an empty override over a good one.
    """
    server_dir = _installed(tmp_path)
    before = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")

    assert setup.roll_back(WOTLK, server_dir) is False
    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") == before


def test_a_second_press_does_not_overwrite_the_backup_of_the_original(
    tmp_path: Path,
) -> None:
    """Otherwise the second press backs up the channel's own configuration.

    The user presses twice — the first press already having written the
    channel on — and the rollback would then restore a file with the channel
    still in it, which is not a rollback at all.
    """
    server_dir = _installed(tmp_path)
    original = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)
    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    setup.roll_back(WOTLK, server_dir)

    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") == original


def test_a_start_failure_that_names_our_port_is_told_apart_from_one_that_does_not() -> None:
    """The predicate that decides whether a rollback is even relevant.

    Docker's message for a taken published port names the port; a message about
    anything else must not cause the channel to be silently switched off.
    """
    # Two wordings, and the SECOND one is why this test exists in this shape.
    # The first is what Docker's older message looks like and what this
    # predicate was written against. The second is what the daemon on
    # `yulon-ubuntu` actually said on 2026-09-07 when the gate held the port,
    # and it contains neither "bind for" nor "already allocated" -- so the
    # first version of this predicate answered no, no rollback happened, and
    # the gate failed on the clause it was written to prove.
    older = (
        "Error response from daemon: driver failed programming external "
        "connectivity on endpoint ac-worldserver: Bind for 127.0.0.1:7878 failed: "
        "port is already allocated"
    )
    measured = (
        "Error response from daemon: failed to set up container networking: driver "
        "failed programming external connectivity on endpoint ac-worldserver "
        "(34fb00de9718): failed to bind host port 127.0.0.1:7878/tcp: address "
        "already in use"
    )
    for taken in (older, measured):
        assert setup.blames_the_host_port(taken, 7878) is True
        assert setup.blames_the_host_port(taken, 8085) is False
    assert setup.blames_the_host_port("ac-database exited with code 1", 7878) is False
    assert setup.blames_the_host_port("could not bind: out of memory", 7878) is False
    # Compose says this on every command in an install that has not claimed the
    # port yet. It names 7878 and is not a failure at all, so matching on the
    # number alone would turn an ordinary start into a silent rollback.
    assert (
        setup.blames_the_host_port(
            'The "DOCKER_SOAP_EXTERNAL_PORT" variable is not set. ' "Defaulting to 127.0.0.1:7878.",
            7878,
        )
        is False
    )
    assert setup.blames_the_host_port("Bind for 127.0.0.1:7878 failed", 78) is False
    assert setup.blames_the_host_port("failed to bind host port 127.0.0.1:78780/tcp", 7878) is False


# -- what the review found about rolling back (2026-09-07) -------------------


def _enabled_text(server_dir: Path) -> str:
    """What the press writes, re-rendered rather than remembered."""
    from yulon.catalog import composegen

    return composegen.render(
        WOTLK,
        server_dir,
        templates_root=resources.installers_dir(),
        world_env=setup._world_env(WOTLK, WOTLK.operations.enable_env),
    ).override


def test_a_rollback_leaves_an_override_somebody_has_edited_since_the_press(
    tmp_path: Path,
) -> None:
    """The backup is written once and can be arbitrarily old.

    The override's own header says the settings surface will read and rewrite
    it. Putting a months-old copy back on top of that would throw away
    everything the user changed in between -- to undo a press they may barely
    remember making. The port is still released, because the port claim IS this
    feature's and giving it back is what makes the server start.
    """
    server_dir = _installed(tmp_path)
    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)
    override = server_dir / OVERRIDE_FILE
    edited = override.read_text(encoding="utf-8") + "\n# a person was here\n"
    override.write_text(edited, encoding="utf-8")

    assert setup.roll_back(WOTLK, server_dir, expected=_enabled_text(server_dir)) is True

    assert override.read_text(encoding="utf-8") == edited
    env = (server_dir / ".env").read_text(encoding="utf-8")
    assert f"{setup.HOST_PORT_VAR}={setup.RELEASED_HOST_PORT}" in env


def test_a_rollback_interrupted_before_its_last_step_can_simply_be_run_again(
    tmp_path: Path,
) -> None:
    """Which is why the backup is deleted last.

    Deleting the only copy first and then failing on `.env` would leave a
    restored override paired with the port it cannot have, and nothing to
    retry from.
    """
    server_dir = _installed(tmp_path)
    original = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)
    expected = _enabled_text(server_dir)

    setup.roll_back(WOTLK, server_dir, expected=expected)
    # The backup is gone, which is what makes a second call a no-op rather than
    # a second restore of something already restored.
    assert setup.roll_back(WOTLK, server_dir, expected=expected) is False
    assert (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8") == original


def test_a_bind_failure_for_another_service_does_not_roll_this_channel_back() -> None:
    """Compose prints one line per service and hands over the whole block.

    A database that could not bind, and our port named on some other line, is
    two facts about two things. Read as one they turn an unrelated failure into
    a silent rollback of a channel that was working.
    """
    unrelated = (
        "Container ac-database  Starting\n"
        "Error response from daemon: failed to bind host port 127.0.0.1:3306/tcp: "
        "address already in use\n"
        "the command channel is published on 7878\n"
    )

    assert setup.blames_the_host_port(unrelated, 7878) is False
    assert setup.blames_the_host_port(unrelated, 3306) is True


def test_the_userland_proxys_own_wording_is_matched_too() -> None:
    """`listen tcp ...` is the other sentence Docker uses for the same thing."""
    said = "Error starting userland proxy: listen tcp4 127.0.0.1:7878: bind: address already in use"

    assert setup.blames_the_host_port(said, 7878) is True


# -- 8.2c: the trees that read no environment --------------------------------


def _cmangos_installed(tmp_path: Path) -> Path:
    """A CMaNGOS server dir: the three compose files AND the conf the press patches."""
    from yulon.catalog import composegen

    plan = composegen.render(
        TBC, tmp_path, templates_root=resources.installers_dir(), db_password="pw"
    )
    composegen.write_plan(plan, tmp_path)
    assert TBC.operations is not None and TBC.operations.enable_conf is not None
    conf = tmp_path / TBC.operations.enable_conf.file
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(CONF_BEFORE, encoding="utf-8")
    return tmp_path


def _conf_of(server_dir: Path) -> Path:
    assert TBC.operations is not None and TBC.operations.enable_conf is not None
    return server_dir / TBC.operations.enable_conf.file


def test_the_press_writes_the_conf_keys_on_a_tree_with_no_environment_route(
    tmp_path: Path,
) -> None:
    """CMaNGOS reads its settings from the file and from nowhere else.

    Its config reader lowercases the key and looks it up in what it parsed
    (`src/shared/Config/Config.cpp:73`, `:91`); there is no environment fallback
    anywhere in it, so 8.2a's four environment keys cannot serve this family.
    """
    server_dir = _cmangos_installed(tmp_path)

    setup.enable(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        world_running=False,
        db_password="pw",
    )

    after = _conf_of(server_dir).read_text(encoding="utf-8")
    assert "SOAP.Enabled = 1" in after
    assert "SOAP.IP = 0.0.0.0" in after
    assert "SOAP.Port = 7878" in after
    assert "Ra.Enable = 0" in after
    # And the rest of the file is untouched: this is a patch, not a rewrite.
    assert "Console.Enable = 1" in after
    assert after.startswith("# a mangosd.conf, as the install leaves it")


def test_the_conf_is_backed_up_once_by_the_first_press(tmp_path: Path) -> None:
    """A second press must not back up the channel's own configuration.

    The same trap the override's backup has: back it up twice and a rollback
    restores a file with the channel still in it.
    """
    server_dir = _cmangos_installed(tmp_path)
    conf = _conf_of(server_dir)
    backup = conf.with_name(conf.name + setup.BACKUP_SUFFIX)

    setup.enable(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        world_running=False,
        db_password="pw",
    )
    setup.enable(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        world_running=False,
        db_password="pw",
    )

    assert backup.read_text(encoding="utf-8") == CONF_BEFORE
    assert "SOAP.Enabled = 1" in conf.read_text(encoding="utf-8")


def test_the_rollback_gives_the_conf_back_as_well_as_the_port(tmp_path: Path) -> None:
    """The port alone is not enough here: the world reads the conf on the way up."""
    server_dir = _cmangos_installed(tmp_path)
    conf = _conf_of(server_dir)
    setup.enable(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        world_running=False,
        db_password="pw",
    )
    assert "SOAP.Enabled = 1" in conf.read_text(encoding="utf-8")

    assert setup.roll_back(TBC, server_dir) is True

    assert conf.read_text(encoding="utf-8") == CONF_BEFORE
    assert not conf.with_name(conf.name + setup.BACKUP_SUFFIX).exists()


def test_the_press_refuses_when_the_conf_it_names_is_not_there(tmp_path: Path) -> None:
    """Creating the file would be worse than refusing: mangosd would read a stub.

    A conf with three SOAP keys and nothing else is not a configuration — the
    world would come up with every other setting at its compiled default,
    including the database it cannot then reach.
    """
    server_dir = _cmangos_installed(tmp_path)
    _conf_of(server_dir).unlink()

    with pytest.raises(setup.EnableRefused, match="mangosd.conf"):
        setup.enable(
            TBC,
            server_dir,
            templates_root=resources.installers_dir(),
            world_running=False,
            db_password="pw",
        )


def test_the_rollback_keeps_an_edit_made_after_the_press(tmp_path: Path) -> None:
    """Adversarial review, 2026-09-07, and the same point 8.2a's review made once.

    The backup is written by the FIRST press and lives until a rollback consumes
    it, so it can be arbitrarily old -- that argument is already written down for
    the compose override, which is why `roll_back(expected=...)` exists there.
    The conf had no equivalent and was restored wholesale: seventy kilobytes of
    somebody's settings, replaced from a copy of unknown age, to undo four keys.

    So the undo is an inverse PATCH of the keys this app owns, and every other
    line survives untouched.
    """
    server_dir = _cmangos_installed(tmp_path)
    conf = _conf_of(server_dir)
    setup.enable(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        world_running=False,
        db_password="pw",
    )
    # A person changes something else entirely, after the press.
    edited = conf.read_text(encoding="utf-8").replace(
        "Console.Enable = 1", 'Console.Enable = 0\nMotd = "a new day"'
    )
    conf.write_text(edited, encoding="utf-8")

    assert setup.roll_back(TBC, server_dir) is True

    after = conf.read_text(encoding="utf-8")
    assert "Console.Enable = 0" in after, "an unrelated edit was thrown away"
    assert 'Motd = "a new day"' in after, "a line added after the press was thrown away"
    # ...and the channel's own keys are back where the press found them.
    assert "SOAP.Enabled = 0" in after
    assert "SOAP.IP = 127.0.0.1" in after


def test_the_conf_backup_outlives_a_rollback_that_could_not_finish(tmp_path: Path) -> None:
    """The ordering argument the override already makes, applied to the conf.

    `roll_back()` restores the conf, then the override, then the `.env`. The
    conf's backup used to be deleted in the middle of that, so a failure on
    either later step left nothing to retry from -- the exact failure mode the
    override's own comment says it avoids by unlinking last.
    """
    server_dir = _cmangos_installed(tmp_path)
    conf = _conf_of(server_dir)
    backup = conf.with_name(conf.name + setup.BACKUP_SUFFIX)
    setup.enable(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        world_running=False,
        db_password="pw",
    )
    # The override's own backup is what the rest of the rollback needs; without
    # it the function returns early, which is the "could not finish" this asks
    # about.
    (server_dir / (OVERRIDE_FILE + setup.BACKUP_SUFFIX)).unlink()

    setup.roll_back(TBC, server_dir)

    assert backup.is_file(), "the conf's only copy was deleted before the job was done"
    assert "SOAP.Enabled = 0" in conf.read_text(encoding="utf-8")


# -- an enforcing SELinux host (T102) -----------------------------------------


def _on_selinux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fedora as `yulon-fedora` answered on 2026-09-24: enforcing, on xfs."""
    monkeypatch.setattr(platform, "selinux_enforcing", lambda: True)
    monkeypatch.setattr(platform, "filesystem_type", lambda _path: "xfs")


def _installed_with_label(tmp_path: Path) -> Path:
    """A server dir as the install leaves it on that host: every host bind ends `:z`."""
    from yulon.catalog import composegen

    plan = composegen.render(
        WOTLK, tmp_path, templates_root=resources.installers_dir(), bind_label=":z"
    )
    composegen.write_plan(plan, tmp_path)
    return tmp_path


def _host_binds(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("- ./")]


def test_the_press_keeps_the_selinux_label_the_install_put_on_every_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The press re-renders the override, and it used to render it unlabelled.

    Measured on `yulon-fedora` (Fedora 44, Enforcing, xfs) on 2026-09-24: the
    install wrote `- ./modules:/azerothcore/modules:z`, the press rewrote it as
    `- ./modules:/azerothcore/modules`. A bind with no `:z` is readable only
    while the folder happens to carry a shared container label already; the
    busybox stand-in on the same box was refused on an unlabelled folder and
    on a privately labelled one.
    """
    _on_selinux(monkeypatch)
    server_dir = _installed_with_label(tmp_path)
    installed = _host_binds((server_dir / OVERRIDE_FILE).read_text(encoding="utf-8"))
    assert installed, "the override has no host bind; this test proves nothing"
    assert all(line.endswith(":z") for line in installed)

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    written = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    assert 'AC_SOAP_ENABLED: "1"' in written
    assert _host_binds(written) == installed


@pytest.mark.parametrize("probe", [False, None], ids=["permissive-now", "cannot-tell-now"])
def test_the_press_takes_the_label_from_the_installed_override_not_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: bool | None
) -> None:
    """A probe that answers wrongly at press time must not strip the install's `:z`.

    `getenforce` failing (None) or a host briefly permissive (False) made the
    press render with no label, which is T102 again (final review of
    `424ac7a9`). The installed override already says which label this install
    uses, so the press reads it there.
    """
    server_dir = _installed_with_label(tmp_path)
    installed = _host_binds((server_dir / OVERRIDE_FILE).read_text(encoding="utf-8"))
    monkeypatch.setattr(platform, "selinux_enforcing", lambda: probe)

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    written = (server_dir / OVERRIDE_FILE).read_text(encoding="utf-8")
    assert 'AC_SOAP_ENABLED: "1"' in written
    assert _host_binds(written) == installed


def test_an_unlabelled_install_stays_unlabelled_on_an_enforcing_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file wins in this direction too: the press writes what the install wrote.

    That is also the text the rollback recognises, so the press cannot move an
    install onto a label its own undo would then have to guess at.
    """
    server_dir = _installed(tmp_path)
    _on_selinux(monkeypatch)

    setup.enable(WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False)

    binds = _host_binds((server_dir / OVERRIDE_FILE).read_text(encoding="utf-8"))
    assert binds and not any(line.endswith(":z") for line in binds)


def test_an_override_with_mixed_labels_is_refused_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some binds labelled and some not is no install's rendering; the press will not pick one."""
    _on_selinux(monkeypatch)
    server_dir = _installed_with_label(tmp_path)
    override = server_dir / OVERRIDE_FILE
    labelled = "      - ./modules:/azerothcore/modules:z\n"
    text = override.read_text(encoding="utf-8")
    assert labelled in text
    mixed = text.replace(labelled, labelled + "      - ./extra:/azerothcore/extra\n")
    override.write_text(mixed, encoding="utf-8")

    with pytest.raises(setup.EnableRefused, match="label"):
        setup.enable(
            WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False
        )

    assert override.read_text(encoding="utf-8") == mixed
    assert not override.with_name(override.name + setup.BACKUP_SUFFIX).exists()
    assert not (server_dir / ".env").exists()
