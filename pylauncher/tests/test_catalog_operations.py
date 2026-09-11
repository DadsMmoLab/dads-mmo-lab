"""The per-entry operations block: how a tree's command channel is switched on (8.2a).

Every value here is a per-tree fact and is asserted against the entry rather
than against a literal, so a tree that changes its mind fails rather than
inherits its neighbour's answer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import CatalogEntry, ConfEnable, Operations, load_catalog

WOTLK = load_catalog().get("wow-wotlk")


def test_wotlks_channel_is_soap_on_the_port_its_compose_file_already_publishes() -> None:
    """7878 is not a new number: the base compose has published it all along.

    `${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878` publishes a container
    port that nothing is listening on until this step turns SOAP on
    (`azerothcore.md:104-106`).
    """
    assert WOTLK.operations is not None
    assert WOTLK.operations.channel == "soap"
    assert WOTLK.operations.port == 7878


def test_the_listener_binds_every_interface_inside_the_container() -> None:
    """The load-bearing value in the whole block, and the one that was measured.

    `SOAP.IP` defaults to `127.0.0.1` — the container's OWN loopback — and the
    spike measured that such a listener does not serve a published port and does
    not refuse either: Docker accepts and relays nothing, so a connect probe
    passes on the broken configuration. The host side stays pinned to loopback
    by the compose publication, so `0.0.0.0` here is not an exposure.
    """
    assert WOTLK.operations is not None
    assert WOTLK.operations.enable_env["AC_SOAP_IP"] == "0.0.0.0"
    assert WOTLK.operations.enable_env["AC_SOAP_ENABLED"] == "1"


def test_the_same_press_closes_the_playerbot_command_server() -> None:
    """It defaults to ON, and this install reads no module conf at all.

    `AiPlayerbot.CommandServerPort` is 8888 in the dist AND in the compiled
    fallback (`PlayerbotAIConfig.cpp:468`), it accepts `<command>,<bot guid>`
    lines on its own detached thread, and 8.1a's gate measured this core
    logging "Not found modules config files" — so the compiled default is what
    is in force. Opening one remote channel while leaving another at a
    listening default is the finding this avoids.
    """
    assert WOTLK.operations is not None
    assert WOTLK.operations.enable_env["AC_AI_PLAYERBOT_COMMAND_SERVER_PORT"] == "0"
    assert 8888 in WOTLK.operations.must_not_listen


def test_the_remote_console_is_left_off_and_asserted_off() -> None:
    """`Ra.Enable` is 0 by default; the press says so anyway and the gate checks."""
    assert WOTLK.operations is not None
    assert WOTLK.operations.enable_env["AC_RA_ENABLE"] == "0"
    assert 3443 in WOTLK.operations.must_not_listen


def test_the_environment_keys_are_spelled_the_way_this_core_derives_them() -> None:
    """`"AC_" + upper_snake(key)`, the rule the catalog already relies on.

    Asserted against a key that predates this block: the install's own
    `world_env` carries `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` for
    `AiPlayerbot.MinRandomBots`, which is the proof the transform is generic
    rather than a per-key coincidence.
    """
    assert WOTLK.operations is not None
    native = WOTLK.install.native
    assert native is not None and native.azerothcore is not None
    existing = set(native.azerothcore.world_env)
    assert any(
        key.startswith("AC_AI_PLAYERBOT_") for key in existing
    ), "the proof key is gone; the spelling rule below rests on it"
    for key in WOTLK.operations.enable_env:
        assert key.startswith("AC_"), key
        assert key.upper() == key, key


def test_the_gm_level_is_the_one_soap_itself_requires() -> None:
    """`SEC_ADMINISTRATOR` (3), below which the listener answers 403."""
    assert WOTLK.operations is not None
    assert WOTLK.operations.gm_level == 3


def test_every_tree_now_states_its_channel_and_they_are_not_the_same() -> None:
    """8.2e was the last one, and the four answers are three different shapes.

    This test replaces the one that named the trees which had no block yet: as
    of 8.2e there are none, and asserting an absence that can no longer happen
    is a test that can only ever pass.
    """
    channels = {
        game: load_catalog().get(game).operations
        for game in ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise")
    }
    assert all(block is not None for block in channels.values()), channels

    assert channels["wow-wotlk"].enable_env and not channels["wow-wotlk"].enable_conf
    for game in ("wow-tbc", "wow-vanilla"):
        assert channels[game].enable_conf and not channels[game].enable_env
    # Tortoise has been round this loop twice. It said `attach` until 2026-09-08,
    # joined the SOAP trees when the fork re-added the interface (3f9a062) and
    # the pin moved onto it (3a8472e), and went back to `attach` on 2026-09-11
    # when T30 moved the whole entry off that fork onto the Penqle core -- which
    # has no SOAP at all: one comment at `src/game/World.h:799` is the only
    # occurrence of the string under its `src/`, its `mangosd.conf.dist.in`
    # ships no `SOAP.*` key and no gsoap is vendored (T30 Half 1, yulon-arch,
    # `05-conf-keys.txt`).
    assert channels["wow-tortoise"].channel == "attach"
    assert not channels["wow-tortoise"].enable_conf and not channels["wow-tortoise"].enable_env
    # THE WAY BACK IS ONE EDIT, and it is written down in the entry rather than
    # here, because that is where a hand doing the flip will be looking. The
    # owner has asked the core's maintainer for the subsystem back; this asserts
    # the recipe is still beside the block and still names every key, so it
    # cannot rot into "it used to say something about SOAP".
    notes = " ".join(channels["wow-tortoise"].notes)
    assert notes, "the Tortoise channel carries no note saying why it is `attach`"
    for key in ("channel", "namespace", "port", "gm_level", "enable_conf", "publish"):
        assert key in notes, f"the restore recipe does not name {key!r}: {notes!r}"
    for key in ("SOAP.Enabled", "SOAP.IP", "SOAP.Port"):
        assert key in notes, f"the restore recipe does not name the conf key {key!r}"
    assert "urn:MaNGOS" in notes and "7878" in notes, (
        "the restore recipe gives the namespace and the port by value; a recipe that says "
        "`put the keys back` sends the next hand to measure them again"
    )


def test_an_attach_channel_declares_nothing_it_does_not_have() -> None:
    """A core with no listener (Tortoise until 2026-09-08) has nothing for most of this block.

    There is no listener to enable, so no `enable_env` and no `enable_conf`;
    no port to publish; no envelope, so no namespace; and no account, because
    the console runs its commands at `SEC_CONSOLE` with account id 0. Declaring
    any of them would be describing a channel this core does not have.
    """
    ops = Operations(channel="attach")

    assert ops.enable_env == {} and ops.enable_conf is None
    assert ops.port is None
    assert ops.namespace is None
    assert ops.gm_level is None
    assert ops.publish is False


def test_an_attach_channel_that_claims_a_port_or_a_namespace_is_refused() -> None:
    """A field that cannot apply is worse than a missing one: it reads as measured."""
    for extra in ({"port": 7878}, {"namespace": "urn:MaNGOS"}, {"gm_level": 3}):
        with pytest.raises(ValidationError, match="attach"):
            Operations(channel="attach", **extra)


def test_a_soap_channel_still_has_to_say_all_three() -> None:
    """The other side of the same validator: nothing became optional for SOAP."""
    for missing in ("port", "namespace", "gm_level"):
        fields = {
            "channel": "soap",
            "namespace": "urn:AC",
            "port": 7878,
            "gm_level": 3,
            "enable_env": {"AC_SOAP_ENABLED": "1"},
        }
        del fields[missing]
        with pytest.raises(ValidationError):
            Operations(**fields)


def test_each_tree_states_its_own_soap_namespace() -> None:
    """Measured live on 2026-09-07, and it is not the same on both families.

    The CMaNGOS source study stopped exactly here -- "the gSOAP-generated
    envelope is in `soapC.cpp`, which is generated code I did not read... needs
    a judgement / live test" -- so it was measured against the running TBC
    server on m910q. `urn:AC` gets HTTP 500 `Method 'ns1:executeCommand' not
    implemented: method name or namespace not recognized`; `urn:MaNGOS` gets
    HTTP 200 and the server's own `CMaNGOS/0.18 ... Online players: 0`.

    There is no default, and that is the point: a default would be one tree's
    answer inherited by every other, and the failure it produces looks exactly
    like a world that has not finished loading -- the namespace is checked
    BEFORE the password, so a wrong one answers 500 even for a bad credential.
    """
    wotlk = load_catalog().get("wow-wotlk")
    tbc = load_catalog().get("wow-tbc")

    assert wotlk.operations is not None and wotlk.operations.namespace == "urn:AC"
    assert tbc.operations is not None and tbc.operations.namespace == "urn:MaNGOS"


def test_a_channel_with_no_namespace_is_refused() -> None:
    """A namespace nobody measured is a guess, and this one fails invisibly."""
    with pytest.raises(ValidationError):
        Operations(
            channel="soap",
            port=7878,
            gm_level=3,
            enable_env={"AC_SOAP_ENABLED": "1"},
        )


def test_a_soap_channel_that_never_says_how_to_switch_soap_on_is_refused() -> None:
    """The relationship has no other owner, so the model owns it."""
    with pytest.raises(ValidationError, match="turns SOAP on"):
        Operations(
            channel="soap",
            namespace="urn:AC",
            port=7878,
            gm_level=3,
            enable_env={"AC_RA_ENABLE": "0"},
        )


def test_a_tree_with_no_environment_route_says_which_conf_file_turns_it_on() -> None:
    """CMaNGOS reads no environment at all, so the env route cannot serve it.

    AzerothCore maps every ini key `X` to `AC_<UPPER_SNAKE>` generically
    (`Config.cpp:435-438`), which is why 8.2a's whole enable is four environment
    keys. The CMaNGOS lineage has no such rule anywhere in its reader — the
    keys come from the conf file and nowhere else (`src/shared/Config/
    Config.cpp:73`, `:91`) — so its channel is switched on by patching
    `mangosd.conf`, which is the mechanism the install already uses for six
    other keys.
    """
    ops = Operations(
        channel="soap",
        namespace="urn:MaNGOS",
        port=7878,
        gm_level=3,
        enable_conf=ConfEnable(
            file="etc/mangosd.conf",
            keys={"SOAP.Enabled": "1", "SOAP.IP": "0.0.0.0", "SOAP.Port": "7878"},
        ),
    )

    assert ops.enable_env == {}
    assert ops.enable_conf is not None
    assert ops.enable_conf.file == "etc/mangosd.conf"
    assert ops.enable_conf.keys["SOAP.Enabled"] == "1"


def test_a_conf_route_that_never_mentions_soap_is_refused_like_the_env_one() -> None:
    """The same clause as the environment route: a channel nobody can enable is none."""
    with pytest.raises(ValidationError, match="turns SOAP on"):
        Operations(
            channel="soap",
            namespace="urn:MaNGOS",
            port=7878,
            gm_level=3,
            enable_conf=ConfEnable(file="etc/mangosd.conf", keys={"Ra.Enable": "0"}),
        )


def test_declaring_both_routes_is_refused() -> None:
    """One install is switched on one way. Two routes is a question, not a plan."""
    with pytest.raises(ValidationError, match="switched on ONE way"):
        Operations(
            channel="soap",
            namespace="urn:AC",
            port=7878,
            gm_level=3,
            enable_env={"AC_SOAP_ENABLED": "1"},
            enable_conf=ConfEnable(file="etc/mangosd.conf", keys={"SOAP.Enabled": "1"}),
        )


def test_a_block_with_no_enable_keys_at_all_is_refused() -> None:
    with pytest.raises(ValidationError):
        Operations(channel="soap", namespace="urn:AC", port=7878, gm_level=3, enable_env={})


# -- 8.3c: where each tree keeps an account's level --------------------------


def test_the_two_cmangos_trees_keep_the_level_on_the_account_row() -> None:
    """Measured per tree, on the live servers, and not inherited from a sibling.

    TBC on 2026-09-07 and Vanilla the same afternoon: `SHOW TABLES LIKE
    'account_access'` came back EMPTY on both, `account` carries a `gmlevel`
    column, and each server's own `help account set gmlevel` reads

        Syntax: .account set gmlevel [#accountId|$accountName] #level

    with no realm argument, because there is no table with realms in it to
    name. A `table` here would make this app join something that does not
    exist; a realm argument would be an extra token the parser hands to
    `ExtractInt32` (`Level3.cpp:1041-1090`).
    """
    for game in ("wow-tbc", "wow-vanilla"):
        level = load_catalog().get(game).accounts.level
        assert level is not None, f"{game} has no measured level store"
        assert level.table is None, f"{game} must not name a table it does not have"
        assert level.level_column == "gmlevel"


def test_wotlk_still_keeps_it_in_its_own_join_table() -> None:
    """The other shape, kept beside the first so a copy-paste shows up here."""
    level = load_catalog().get("wow-wotlk").accounts.level

    assert level is not None
    assert level.table == "account_access"


def test_tortoise_keeps_the_level_in_its_own_column_and_reaches_one_higher() -> None:
    """Measured on the live Tortoise server, m910q, 2026-09-07.

    `account set gmlevel SHAPROBE 4` answered *"You change security level of
    account SHAPROBE to 4."* and the `rank` column read 4; `5` answered
    *"Incorrect values."* The fork's own check grants at the caller's own level
    rather than strictly below it, so a console can hand out 4 — and a surface
    that offered 0 to 3 here would present a lower ceiling than the tree has.

    `security` is NOT the column: it stayed NULL through every one of those
    changes while `rank` moved, and both exist on this row.
    """
    level = load_catalog().get("wow-tortoise").accounts.level

    assert level is not None
    assert level.table is None
    assert level.level_column == "rank"
    assert level.max_level == 4


def test_every_tree_says_how_high_its_levels_go() -> None:
    """A hard-coded 0-to-3 was drawn for all four games until 8.3d.

    Three of them do stop at 3 (`SEC_ADMINISTRATOR`), and Vanilla's own help
    says so in as many words: `#level may range from 0 to 3`. The fourth does
    not, and the number belongs beside the column it applies to rather than in
    the widget.
    """
    for game, ceiling in (
        ("wow-wotlk", 3),
        ("wow-tbc", 3),
        ("wow-vanilla", 3),
        ("wow-tortoise", 4),
    ):
        level = load_catalog().get(game).accounts.level
        assert level is not None, game
        assert level.max_level == ceiling, game


def test_tortoise_declares_no_channel_rank_because_its_console_has_no_account() -> None:
    """The rank that WAS measured, and why the block no longer states it.

    Measured on the fresh install on yulon-arch, 2026-09-08 19:52Z: the fork's
    SOAP block was first written with `gm_level: 3` copied from Vanilla, and the
    server answered the first `server info` with *the account exists but its GM
    level is below administrator, which SOAP requires* -- a 401 with the account
    known. This tree's `accounts.level` scale runs to 4 (`rank`, 8.3d) and its
    administrator is the top of it, not MaNGOS's 3.

    That measurement is still true of the scale and is still asserted, below, on
    `accounts.level`. What changed on 2026-09-11 is that the channel is the
    worldserver console again: console commands run at `SEC_CONSOLE` with
    account id 0 and there is no account to hold a rank, so an `attach` block
    that named one would be describing something that does not exist -- which
    the model refuses outright (`Operations._a_soap_channel_says_how_it_is_switched_on`).
    The rank comes back with the SOAP block, and the entry's notes carry it by
    value so it does not have to be measured a third time.
    """
    entry = load_catalog().get("wow-tortoise")
    assert entry.operations is not None and entry.accounts.level is not None
    assert entry.operations.channel == "attach"
    assert entry.operations.gm_level is None
    assert entry.accounts.level.max_level == 4, (
        "this fork's rank scale runs to 4 and its administrator is the top of it; the "
        "channel's rank comes back from here when SOAP does"
    )
    assert "gm_level" in " ".join(entry.operations.notes)


def test_a_channel_rank_above_the_trees_own_level_scale_is_refused() -> None:
    """`gm_level` used to be capped at 3 by the field alone -- the MaNGOS/AzerothCore
    scale, written before a five-rank tree existed. The bound is the entry's own
    `accounts.level.max_level` now, so a rank no account on that tree can hold is
    a catalog error and not a 401 on the first press."""
    entry = load_catalog().get("wow-vanilla")
    assert entry.operations is not None and entry.accounts.level is not None
    too_high = entry.operations.model_copy(update={"gm_level": entry.accounts.level.max_level + 1})
    with pytest.raises(ValidationError, match="level"):
        CatalogEntry.model_validate({**entry.model_dump(), "operations": too_high.model_dump()})
