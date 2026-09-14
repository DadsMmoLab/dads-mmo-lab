"""My Party's server-side bridge: the deploy, the preconditions, the proof (8.6).

Every fact asserted here about the WotLK tree was **measured on `yulon-ubuntu`
on 2026-09-08** and is recorded in `.notes/gates/8.6-wotlk-yulon-ubuntu-2026-09-08/`.
None of it is inherited from a sibling family: the invalid-command sentence, the
console filter on `.playerbots bot`, the module's own conf key and its compiled
default are per-tree facts, and three of the four differ from what the prior art
assumed.

The one thing this file cannot test is the bridge answering, because the Lua
engine is not compiled into the image on that box. That is the box's first
question and its answer is *no, not today* — so what is tested here is the shape
of every sentence said on the way to that answer, and the classifier that will
recognise the answer when a rebuild puts the engine in.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from yulon import dbreads, party, play, resources
from yulon.actions import Outcome
from yulon.catalog.catalog import load_catalog
from yulon.channel import Answer

# -- where the scripts live ------------------------------------------------


def test_the_bridge_scripts_ship_with_the_app() -> None:
    """The scripts are data the app deploys, so they must be in the bundle root."""
    root = resources.lua_dir()
    assert root == resources.bundle_root() / "lua"
    assert (root / "party").is_dir()
    names = {p.name for p in (root / "party").glob("*.lua")}
    assert names == set(party.BRIDGE_SCRIPTS), names


def test_the_pyinstaller_spec_carries_the_lua_tree() -> None:
    """A script that ships from a checkout and not from a build is a bridge that
    works for developers only. The spec lists `manifests/` and
    `catalog/installers/` by hand, so a third tree has to be added by hand too."""
    spec = (Path(__file__).resolve().parents[1] / "build" / "pylauncher.spec").read_text()
    assert '"lua"' in spec, spec


def test_every_bridge_script_registers_the_command_hook_and_is_console_only() -> None:
    """Two properties no test above the file can see, and both are load-bearing.

    `RegisterPlayerEvent(42, ...)` is PLAYER_EVENT_ON_COMMAND — the hook ALE
    pushes `(player, text, handler)` into (`ALE PlayerHooks.cpp:42-62`). And
    `player ~= nil` is the console/SOAP guard: `OnCommand` sets the player to
    `nullptr` only for `handler.IsConsole()`, so a script without that line is
    one a logged-in player can fire by typing it in chat.
    """
    for name in party.BRIDGE_SCRIPTS:
        text = (resources.lua_dir() / "party" / name).read_text()
        assert "RegisterPlayerEvent(42" in text, name
        assert "if player ~= nil then return end" in text, name


def test_the_dest_is_the_directory_the_container_mounts() -> None:
    assert party.dest_dir(Path("/srv/wow")) == Path("/srv/wow/env/dist/etc/modules/lua_scripts")


# -- the deploy ------------------------------------------------------------


def _root_with(tmp_path: Path, **families: dict[str, str]) -> Path:
    root = tmp_path / "lua"
    for family, files in families.items():
        (root / family).mkdir(parents=True)
        for name, body in files.items():
            (root / family / name).write_text(body)
    return root


def test_deploy_flattens_every_family_into_one_directory(tmp_path: Path) -> None:
    """ALE scans ONE directory (`ALE.ScriptPath`), so the family folders that
    organise the source do not survive the copy."""
    root = _root_with(tmp_path, party={"a.lua": "A"}, gm={"b.lua": "B"})
    dest = tmp_path / "dest"
    done = party.deploy(root, dest)
    assert done.changed is True
    assert done.names == ("a.lua", "b.lua")
    assert (dest / "a.lua").read_text() == "A"
    assert (dest / "b.lua").read_text() == "B"


def test_a_second_deploy_of_the_same_bytes_changes_nothing(tmp_path: Path) -> None:
    """`changed` is what tells the caller a world restart is owed, so a deploy
    that always says yes asks for a restart the user does not need."""
    root = _root_with(tmp_path, party={"a.lua": "A"})
    dest = tmp_path / "dest"
    assert party.deploy(root, dest).changed is True
    assert party.deploy(root, dest).changed is False


def test_a_changed_script_is_copied_over_the_old_one(tmp_path: Path) -> None:
    root = _root_with(tmp_path, party={"a.lua": "A"})
    dest = tmp_path / "dest"
    party.deploy(root, dest)
    (root / "party" / "a.lua").write_text("A2")
    assert party.deploy(root, dest).changed is True
    assert (dest / "a.lua").read_text() == "A2"


def test_deploying_nothing_raises_instead_of_reporting_success(tmp_path: Path) -> None:
    """The Rust launcher shipped this bug and named it in its own source: with
    the source root wrong it emitted `done{changed:false}` — a success envelope
    for a no-op — so "Enable My Party" appeared to work and My Party simply did
    not function (`rust-main:crates/dml-wow/src/bridge.rs:60-70`).

    Both ways of finding nothing must fail, not one.
    """
    with pytest.raises(party.NothingToDeploy):
        party.deploy(tmp_path / "absent", tmp_path / "dest")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(party.NothingToDeploy):
        party.deploy(empty, tmp_path / "dest")
    no_lua = _root_with(tmp_path, party={"readme.txt": "x"})
    with pytest.raises(party.NothingToDeploy):
        party.deploy(no_lua, tmp_path / "dest")


def test_the_shipped_scripts_deploy(tmp_path: Path) -> None:
    """The bundled tree is a real deploy source, not only a directory of files."""
    done = party.deploy(resources.lua_dir(), tmp_path / "dest")
    assert done.names == tuple(sorted(party.BRIDGE_SCRIPTS))


# -- reading the binary ----------------------------------------------------


class _Run:
    """A stand-in for one `docker exec`, recording what it was asked."""

    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.argv: list[str] = []

    def __call__(self, command: list[str], **kw: object) -> object:
        self.argv = command
        return type("P", (), {"stdout": self.stdout, "stderr": "", "returncode": self.returncode})()


def test_the_engine_is_read_out_of_the_binary_not_out_of_a_conf_file() -> None:
    """Owner instruction, 2026-09-08: "Read the binary, not a conf file."

    A conf file is written by the manifest and says nothing about what was
    compiled — on `yulon-ubuntu` `mod_ale.conf` was absent AND the binary had no
    ALE in it, and only the second of those needs a rebuild.
    """
    run = _Run("ALE 0\nCONTROL 1\n")
    read = party.read_engine_in_binary("ac-worldserver", run=run)
    assert read.engine is False
    assert party.ALE_MARKER in " ".join(run.argv)
    assert party.CONTROL_MARKER in " ".join(run.argv)


def test_an_absent_control_string_reads_as_could_not_be_read_not_as_absent() -> None:
    """A grep that finds nothing looks identical to a grep that did not run.

    This is the `guards-that-prove-declarations` lesson in one assertion: the
    probe carries a string the binary MUST have, and a zero there means the
    reading is worthless — a wrong path, a stripped binary, a busybox grep — not
    that the engine is missing. Without it a mistyped path would have reported
    "the Lua engine is not built in" about a server that had it.
    """
    read = party.read_engine_in_binary("ac-worldserver", run=_Run("ALE 0\nCONTROL 0\n"))
    assert read.engine is None
    assert "could not" in read.sentence.lower()


def test_a_binary_carrying_the_engine_reads_as_present() -> None:
    read = party.read_engine_in_binary("ac-worldserver", run=_Run("ALE 4\nCONTROL 1\n"))
    assert read.engine is True


def test_a_failed_exec_is_could_not_be_read() -> None:
    read = party.read_engine_in_binary("ac-worldserver", run=_Run("", returncode=1))
    assert read.engine is None


# -- the preconditions -----------------------------------------------------


def _facts(**over: object) -> party.Facts:
    """Everything met, so each test can break exactly one thing."""
    base = dict(
        server_installed=True,
        world_running=True,
        engine_cloned=True,
        engine_in_binary=True,
        conf_present=True,
        engine_enabled=True,
        script_path=party.ALE_SCRIPT_PATH,
        deployed=tuple(sorted(party.BRIDGE_SCRIPTS)),
        bridge_answered=True,
    )
    base.update(over)
    return party.Facts(**base)  # type: ignore[arg-type]


def test_everything_met_is_ready_and_names_no_blocker() -> None:
    assert party.ready(_facts()) is True
    assert party.blocker(_facts()) is None


@pytest.mark.parametrize(
    ("broken", "must_say"),
    [
        ({"server_installed": False}, "not installed"),
        ({"world_running": False}, "not running"),
        ({"engine_cloned": False}, "Modules"),
        ({"engine_in_binary": False}, "rebuild"),
        ({"engine_in_binary": None}, "could not"),
        ({"conf_present": False}, "mod_ale.conf"),
        ({"engine_enabled": False}, "ALE.Enabled"),
        ({"script_path": "lua_scripts"}, "relative"),
        ({"deployed": ()}, "bridge scripts"),
        ({"bridge_answered": False}, "does not answer"),
        ({"bridge_answered": None}, "could not"),
    ],
)
def test_each_unmet_precondition_draws_its_own_sentence(
    broken: dict[str, object], must_say: str
) -> None:
    """The box's words: "with each precondition drawing its own sentence when it
    is not met". One shared "My Party is not available" is what this forbids."""
    facts = _facts(**broken)
    assert party.ready(facts) is False
    said = party.blocker(facts)
    assert said is not None
    assert must_say in said, said


def test_no_two_preconditions_share_a_sentence() -> None:
    """A distinct sentence per precondition is the whole clause; two the same is
    a user told to fix the wrong thing."""
    sentences = [p.sentence for p in party.preconditions(_facts(server_installed=False))]
    assert len(sentences) == len(set(sentences))


def test_the_blocker_is_the_first_thing_to_fix_not_the_last() -> None:
    """With the server absent, everything below it is unreadable too. Naming the
    deployed scripts there would send a user to a folder that cannot exist."""
    facts = _facts(server_installed=False, engine_cloned=False, deployed=())
    assert "not installed" in (party.blocker(facts) or "")


def test_no_sentence_asserts_a_fact_another_precondition_has_already_denied() -> None:
    """FOUND BY RUNNING IT, `yulon-ubuntu` 2026-09-08 09:53Z, on the real install.

    Every precondition below the first unmet one is still printed, and two of
    them stated as fact something the reading had just contradicted:

        engine_in_binary  "the Lua engine is installed but not built into the
                           server that is running"   -- it was NOT installed
        bridge_answered   "The scripts are in place, so the world server has
                           not read them"            -- they were NOT in place

    Neither is the blocker, so neither would ever have reached a user through
    `blocker()`. That is exactly why it survived: a sentence nobody reads on the
    happy path is still a sentence, and the gate page prints all nine. A
    precondition may describe its OWN fact and may not assert somebody else's.
    """
    facts = _facts(
        engine_cloned=False,
        engine_in_binary=False,
        conf_present=False,
        engine_enabled=None,
        script_path=None,
        deployed=(),
        bridge_answered=False,
    )
    said = {p.name: p.sentence for p in party.preconditions(facts)}
    assert "is installed" not in said["engine_in_binary"], said["engine_in_binary"]
    assert "scripts are in place" not in said["bridge_answered"], said["bridge_answered"]


def test_a_partial_deploy_is_not_a_deploy() -> None:
    """One script arriving and another not is exactly how a half-copied bridge
    answers `dml_addclass` and not `dml_uninvite`."""
    some = tuple(sorted(party.BRIDGE_SCRIPTS))[:2]
    assert party.ready(_facts(deployed=some)) is False
    assert "bridge scripts" in (party.blocker(_facts(deployed=some)) or "")


# -- the proof -------------------------------------------------------------


def test_the_probe_command_is_the_one_the_shipped_script_registers() -> None:
    """A probe asking for a command no script answers is a probe that always
    says no. This binds the two together in the only place that can."""
    text = (resources.lua_dir() / "party" / "dml_bridge_ping.lua").read_text()
    assert f'"^{party.PROBE_COMMAND}' in text or f"^{party.PROBE_COMMAND}" in text
    assert party.PROBE_TOKEN in text


def test_the_uninvite_grammar_and_refusal_sentence_are_the_shipped_scripts_own() -> None:
    """Round 1's must-fix 3: the wire grammar `uninvite_command` builds for and
    the refusal sentence `dismiss()` parses each existed twice — once in this
    file, once in `dml_uninvite.lua` — with nothing binding them to the ONE
    script that actually speaks the protocol. Same shape as the probe-command
    pin above."""
    text = (resources.lua_dir() / "party" / "dml_uninvite.lua").read_text()
    assert party.UNINVITE_COMMAND_PATTERN in text
    assert party.UNINVITE_REFUSAL_FORMAT in text


def test_the_bridge_is_six_scripts_now_and_the_sixth_is_the_named_add() -> None:
    """T26: `dml_botadd.lua` joins the five, and the count is stated rather than
    counted by hand.

    The five became six because `.playerbots bot add <name>` has no other route:
    it is `SEC_PLAYER, Console::No` like `addclass`
    (`PlayerbotCommandScript.cpp:36`, measured again at `b949b50b` on
    `yulon-ubuntu2` 2026-09-10), and its master is the calling session's own
    player, so only a relay running inside that player's session can send it.
    Every place that enumerates the bridge reads `BRIDGE_SCRIPTS`; this is the
    one place the sixth name is written down.
    """
    assert "dml_botadd.lua" in party.BRIDGE_SCRIPTS
    assert len(party.BRIDGE_SCRIPTS) == 6, party.BRIDGE_SCRIPTS


def test_the_botadd_grammar_and_both_of_its_sentences_are_the_shipped_scripts_own() -> None:
    """The same pin T13 put over `dml_uninvite.lua`, for the same reason.

    Three literals speak this protocol and each exists twice -- once in
    `party.py`, once in `dml_botadd.lua`. Nothing but this test binds them to
    the one script that actually answers, and a drift in either half is a seam
    that silently stops recognising a refusal (or, worse, reads one as a
    success).
    """
    text = (resources.lua_dir() / "party" / "dml_botadd.lua").read_text()
    assert party.BOTADD_COMMAND_PATTERN in text
    assert party.BOTADD_REFUSAL_FORMAT in text
    assert party.BOTADD_ISSUED_FORMAT in text


def test_the_measured_refusal_reads_as_a_bridge_that_did_not_arrive() -> None:
    """MEASURED on `yulon-ubuntu`, 2026-09-08 09:38Z, over the app's own SOAP
    channel, with nothing deployed:

        HTTP 500, faultstring: Command 'dml_bridge_ping' does not exist

    This is the exact wire text; the 2026-08-20 note reported the same shape.
    """
    proof = party.read_probe(Answer("no", "Command 'dml_bridge_ping' does not exist\r\n"))
    assert proof.arrived is False
    assert "does not know" in proof.sentence


def test_a_result_carrying_the_scripts_own_word_is_the_arrival() -> None:
    """The proof the box asks for: the SCRIPT answering, not the deploy
    reporting success. `handler:SendSysMessage` (`ALE ChatHandlerMethods.h:29`)
    writes into the SOAP print buffer, and a command whose hook returned false
    never sets the error flag — so the reply flips from a fault to a result
    carrying the script's own token (`ACSoap.cpp:133-140`)."""
    proof = party.read_probe(Answer("yes", f"{party.PROBE_TOKEN} 5 scripts\r\n"))
    assert proof.arrived is True
    assert party.PROBE_TOKEN in proof.text


def test_a_success_without_the_token_is_not_an_arrival() -> None:
    """A deploy reporting success is what fooled 2026-08-20. So is any other
    command on the server that happens to answer to this name."""
    proof = party.read_probe(Answer("yes", "ok\r\n"))
    assert proof.arrived is False
    assert "own word" in proof.sentence


def test_a_channel_that_could_not_ask_is_neither_arrived_nor_absent() -> None:
    """Three outcomes, as everywhere else in this phase: yes, no, could not ask.
    Reporting a stopped server as "the bridge is missing" would send the user to
    reinstall a module over a server that is simply down."""
    proof = party.read_probe(Answer("unknown", "", reason="this server is not running"))
    assert proof.arrived is None
    assert "could not" in proof.sentence.lower()


# -- the manifest ----------------------------------------------------------


def _ale_manifest() -> dict:
    path = resources.manifests_dir() / "wow-wotlk" / "modules" / "mod-ale.json"
    return json.loads(path.read_text())


def test_the_manifest_names_the_conf_key_the_module_actually_has() -> None:
    """MEASURED from the module's own source, 2026-09-08:

        conf/mod_ale.conf.dist:63          ALE.Enabled = true
        src/LuaEngine/ALEConfig.cpp:20     "ALE.Enabled", default "false"

    The manifest said `ALE.EnableLuaEngine`, which appears in neither. A key
    that does not exist is not written and not read, so the engine would have
    taken its COMPILED default — and that default is false while the shipped
    conf's own comment says true.
    """
    keys = [k["key"] for k in _ale_manifest()["conf"][0]["keys"]]
    assert "ALE.Enabled" in keys
    assert "ALE.EnableLuaEngine" not in keys


def test_the_manifest_writes_the_engine_on_rather_than_naming_the_key() -> None:
    """A key with no default is surfaced, not set. The compiled default is
    false, so surfacing it leaves the engine off."""
    enabled = next(k for k in _ale_manifest()["conf"][0]["keys"] if k["key"] == "ALE.Enabled")
    assert enabled.get("default") == "1"


def test_the_lua_engine_is_pinned() -> None:
    """The box asks for a revision. `azerothcore/mod-ale` moved from `c3de7942`
    (HEAD 2026-09-06, `phase8-delta.md:18`) to `319f43ed` (HEAD 2026-09-07) in
    two days, so an unpinned manifest hands a two-hour rebuild whatever that
    morning's HEAD is."""
    rev = _ale_manifest()["source"].get("rev")
    assert rev == party.MOD_ALE_REV
    assert len(rev) == 40


def test_the_script_path_the_manifest_writes_is_the_absolute_container_path() -> None:
    """The silent-bridge bug (`rust-main:bridge.rs:189-195`, live on Ubuntu
    2026-08-20): ALE ships `ALE.ScriptPath = "lua_scripts"`, relative, resolved
    against the worldserver's cwd where nothing is — so every bridge command
    answers "does not exist" while the deploy reports success."""
    manifest = _ale_manifest()
    path = next(k for k in manifest["conf"][0]["keys"] if k["key"] == "ALE.ScriptPath")
    assert party.ALE_SCRIPT_PATH in path["default"]
    assert path["default"].startswith('"/')


# -- adding a bot, dismissing it (8.6 part 2) ------------------------------
#
# Every command string below is the shape `rust-main` fires, cited at its line,
# and every SQL shape is that tree's too. What is NOT inherited is the class
# list: `dk` is a class THIS tree's `addclass` accepts (`PlayerbotMgr.cpp:1089`,
# read on `yulon-ubuntu` 2026-09-08), and the bash launcher's `_valid_bot_class`
# excluded it. A sibling's list would have refused a class this server supports.

WOTLK = load_catalog().get("wow-wotlk")
RNDBOT = dbreads.Marker(prefix="rndbot", source="default")


def test_the_add_command_is_the_bridge_command_and_not_the_playerbot_one() -> None:
    """`.playerbots bot addclass` is what the SERVER runs; the app can only ask
    the bridge (`rust-main:crates/dml-wow/src/party.rs:163`). Sending the
    playerbot spelling over the channel is the 2026-08-20 mistake by hand."""
    assert party.add_command("Pakka", "mage") == "dml_addclass Pakka mage"
    assert party.add_command("Pakka", "mage", gender="female") == "dml_addclass Pakka mage female"


def test_deathknight_is_a_class_this_tree_accepts() -> None:
    assert party.add_command("Pakka", "dk") == "dml_addclass Pakka dk"


@pytest.mark.parametrize("bad", ["sorcerer", "", "mage; .server shutdown 1", "MAGE!"])
def test_a_class_this_tree_does_not_have_is_refused_before_anything_is_sent(bad: str) -> None:
    with pytest.raises(party.BadRequest):
        party.add_command("Pakka", bad)


@pytest.mark.parametrize("bad", ["", "Pak ka", "Pakka;", "a" * 13])
def test_a_name_that_is_not_a_character_name_never_reaches_the_channel(bad: str) -> None:
    with pytest.raises(party.BadRequest):
        party.add_command(bad, "mage")


def test_the_dismiss_commands_are_the_uninvite_and_the_logout_whisper() -> None:
    """Two commands, and the second is best-effort — `rust-main`'s kick fires
    `dml_uninvite` then `dml_whisper <master> <bot> logout`
    (`crates/dml-wow/src/party.rs:184,192`).

    T13: `uninvite_command` now carries the master's own name too, wider than
    `rust-main:.../party.rs:184`'s `dml_uninvite <bot>` — the Lua checks it at
    the moment it acts (see `dml_uninvite.lua`), which is the only place that
    can still see the group when the whisper lands."""
    assert party.uninvite_command("Pakka", "Bottom") == "dml_uninvite Pakka Bottom"
    assert party.logout_command("Pakka", "Bottom") == "dml_whisper Pakka Bottom logout"


@pytest.mark.parametrize("bad", ["", "Pak ka", "Pakka;", "a" * 13])
def test_uninvite_refuses_a_bad_master_name_before_anything_is_sent(bad: str) -> None:
    with pytest.raises(party.BadRequest):
        party.uninvite_command(bad, "Bottom")


def test_the_finishing_whispers_are_gear_and_talents() -> None:
    """ "geared and specced" is not what `addclass` alone does: the bash and Rust
    launchers both whisper the bot afterwards
    (`rust-main:crates/dml-wow/src/party.rs:246,249`)."""
    assert party.autogear_command("Pakka", "Bottom") == "dml_whisper Pakka Bottom autogear"
    assert party.talents_command("Pakka", "Bottom") == "dml_whisper Pakka Bottom talents autopick"


def test_the_group_read_is_anchored_on_the_masters_own_group() -> None:
    """`WHERE gm.guid = (SELECT guid FROM group_member WHERE memberGuid=<master>)`
    — the master's GROUP id, not his guid (`group_member.guid` is the group on
    this tree; `describe group_member`, `yulon-ubuntu2` 2026-09-08). Without the
    sub-select this reads every group on the server."""
    sql = party.group_rows_sql(WOTLK, RNDBOT, master_guid=1001)
    assert "group_member" in sql
    assert "memberGuid=1001" in sql.replace(" ", "")
    assert "RNDBOT%" in sql.upper()
    assert "ORDER BY" in sql.upper()


def test_the_group_read_only_returns_rows_the_bot_marker_recognises() -> None:
    """The box's words: "one row appears in the group table on an account the
    bot marker recognises". The master is in his own `group_member` row too, and
    a read that returned him would report a party of one before any bot joined."""
    sql = party.group_rows_sql(WOTLK, RNDBOT, master_guid=1001)
    assert dbreads.bot_clause(WOTLK, RNDBOT) in sql


class _Chan:
    """A channel that records what it was asked and answers from a script."""

    def __init__(self, answers: dict[str, Answer] | None = None) -> None:
        self.sent: list[str] = []
        self.answers = answers or {}

    def send(self, command: str) -> Answer:
        self.sent.append(command)
        return self.answers.get(command.split()[0], Answer("yes", "ok"))


def test_a_party_that_is_not_ready_sends_nothing_and_says_which_step_failed() -> None:
    """The box's words: "with the bridge absent the group says which precondition
    failed and never reads as ready". Never-ready is the whole point: the
    2026-08-20 failure was a control that pressed happily into a dead bridge."""
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(bridge_answered=False),
        player="Pakka",
        klass="mage",
        send=chan.send,
        members=lambda: (),
    )
    assert chan.sent == []
    assert result.added is False
    assert result.blocker == party.blocker(_facts(bridge_answered=False))
    assert "does not answer" in result.sentence


def test_the_ground_is_read_before_the_press_and_the_new_row_is_the_difference() -> None:
    """A poll that asserts "there is a bot in the party" proves nothing on a
    server that already had one. The new member is the guid that was not there
    before (`rust-main:crates/dml-wow/src/party.rs:315`)."""
    already = party.Member(name="Oldbot", guid=500, klass=8, level=10)
    fresh = party.Member(name="Newbot", guid=777, klass=8, level=1)
    reads = iter([(already,), (already,), (already, fresh)])
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        send=chan.send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert result.bot == "Newbot"
    assert result.joined is True
    assert chan.sent[0] == "dml_addclass Pakka mage"
    assert "dml_whisper Pakka Newbot autogear" in chan.sent
    assert "dml_whisper Pakka Newbot talents autopick" in chan.sent
    assert result.geared is True
    assert result.specced is True


def test_a_bot_that_never_joins_is_reported_as_added_but_not_joined() -> None:
    """Not as a success and not as a failure: the command was accepted and the
    row never appeared, which is a third thing and the one a person can act on."""
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        send=chan.send,
        members=lambda: (),
        tries=3,
        sleep=lambda _s: None,
    )
    assert result.added is True
    assert result.joined is False
    assert result.bot is None
    assert chan.sent == ["dml_addclass Pakka mage"]
    assert "no bot joined" in result.sentence


def test_a_refused_add_never_polls_and_quotes_the_server() -> None:
    chan = _Chan({"dml_addclass": Answer("no", "Command 'dml_addclass' does not exist")})
    polls = 0

    def members() -> tuple[party.Member, ...]:
        nonlocal polls
        polls += 1
        return ()

    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        send=chan.send,
        members=members,
        sleep=lambda _s: None,
    )
    assert result.added is False
    assert "does not exist" in result.sentence
    assert polls <= 1


def test_dismissing_uninvites_then_whispers_logout_and_confirms_the_row_is_gone() -> None:
    reads = iter([(party.Member("Newbot", 777, 8, 1),), ()])
    chan = _Chan()
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert chan.sent[0] == "dml_uninvite Pakka Newbot"
    assert "dml_whisper Pakka Newbot logout" in chan.sent
    assert result.removed is True


def test_a_bot_that_moved_to_another_party_is_recognised_and_nothing_more_is_sent() -> None:
    """T13 (T5's round-3 Codex review): the confirm-to-whisper window. `remove_all`
    re-reads the group table and refuses unless the fresh set is exactly what was
    confirmed, but between that read and this whisper landing the bot manager can
    still move a bot into a DIFFERENT master's party on its own timer — and
    `dml_uninvite.lua` now checks the master at the moment it acts rather than
    trusting the name it was sent, answering in words this parses rather than
    treating as a plain "yes" (the bridge's hook never sets the core's error flag
    either way, so `answer.outcome` alone cannot tell the two apart).

    The mutation this catches: a parse that treats that reply as an ordinary
    "yes" goes on to whisper `logout` and poll the group table — sending a
    command for a party this master was never in, and (with the bot durably
    parked in someone else's party) eventually reporting the same `removed`
    result for the wrong reason. `chan.sent` and `logged_out` are what tell
    them apart: the right code sends nothing more once the bridge has already
    said no.
    """
    chan = _Chan(
        {
            "dml_uninvite": Answer(
                "yes", "Newbot is not in Pakka's party now (Newbot is grouped with someone else)"
            )
        }
    )
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: (party.Member("Newbot", 777, 8, 1),),
        sleep=lambda _s: None,
    )
    assert result.removed is False
    assert result.logged_out is False
    assert "Newbot" in result.sentence
    assert chan.sent == ["dml_uninvite Pakka Newbot"], (
        "a refused uninvite must not whisper logout or poll the table of a party "
        "this master was never confirmed against"
    )


def test_the_refusal_sentence_relays_the_server_and_invents_nothing() -> None:
    """Round 1's must-fix 1: the sentence is bounded to what the bridge
    actually said, not a Python-invented mechanism (the earlier "the group
    table moved it into a different party" wording asserted something no
    reply ever reported)."""
    chan = _Chan(
        {
            "dml_uninvite": Answer(
                "yes", "Newbot is not in Pakka's party now (Newbot is grouped with someone else)"
            )
        }
    )
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: (),
        sleep=lambda _s: None,
    )
    assert result.sentence == (
        "Newbot was not removed: the server says Newbot is not in Pakka's party now "
        "(Newbot is grouped with someone else)"
    )


@pytest.mark.parametrize(
    "reason",
    [
        "Pakka not found or offline",
        "Newbot not found or offline",
        "Newbot is not grouped with anyone",
        "Newbot is grouped with someone else",
    ],
)
def test_every_non_removing_branch_is_recognised_and_sends_no_logout_whisper(reason: str) -> None:
    """Round 1's must-fix 2: `dml_uninvite.lua` answers ALL FOUR non-removing
    branches (player not found, bot not found, bot ungrouped, bot grouped with
    someone else) with the one shared marker plus that branch's own reason —
    "the one marker with the reason" of the lead's two offered options — and
    `dismiss()` must recognise every one of them the same way the single
    "moved to another party" case above is recognised: no logout whisper, no
    poll of a party this master was never confirmed against."""
    chan = _Chan({"dml_uninvite": Answer("yes", f"Newbot is not in Pakka's party now ({reason})")})
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: (party.Member("Newbot", 777, 8, 1),),
        sleep=lambda _s: None,
    )
    assert result.removed is False
    assert result.logged_out is False
    assert chan.sent == ["dml_uninvite Pakka Newbot"], (
        "a refused uninvite must not whisper logout or poll the table of a party "
        "this master was never confirmed against"
    )


def test_a_logout_whisper_that_fails_does_not_fail_the_dismiss() -> None:
    """Best-effort at every call site in the prior art, whose own comment says
    the failure "never aborts the caller" (`party.rs:189-192`)."""
    chan = _Chan({"dml_whisper": Answer("no", "player not found")})
    reads = iter([(party.Member("Newbot", 777, 8, 1),), ()])
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert result.removed is True


class _Sql:
    """The reader seam, answering each statement from a script in order."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append(statement)
        return self.answers.pop(0) if self.answers else ""


def _ready_install(tmp_path: Path) -> Path:
    """A server folder with every disk-side precondition already met."""
    server = tmp_path / "wowserver"
    (server / "modules" / "mod-ale").mkdir(parents=True)
    (server / "env" / "dist" / "etc" / "modules").mkdir(parents=True)
    (server / party.ALE_CONF).write_text(
        f'ALE.Enabled = 1\nALE.ScriptPath = "{party.ALE_SCRIPT_PATH}"\n'
    )
    dest = party.dest_dir(server)
    dest.mkdir(parents=True)
    for name in party.BRIDGE_SCRIPTS:
        (dest / name).write_text("-- x\n")
    return server


def _install(
    server: Path,
    sql: _Sql,
    chan: object | None,
    running: bool = True,
    level_setter: party.LevelSetter | None = None,
    link_writer: object | None = None,
    altbots: party.AltbotMemory | None = None,
) -> party.InstallParty:
    return party.InstallParty(
        WOTLK,
        server,
        sql=sql,
        channel_for_saved=lambda: chan,
        container="ac-worldserver",
        world_running=lambda: running,
        engine=lambda: party.BinaryRead(True, "in"),
        level_setter=level_setter,
        link_writer=link_writer,  # type: ignore[arg-type]
        altbots=altbots,
    )


def test_the_seam_refuses_a_master_who_is_not_logged_in(tmp_path: Path) -> None:
    """The bridge resolves its master with `GetPlayerByName` and prints
    "player not found/offline" when there is none (`dml_addclass.lua`), and
    `.playerbots bot addclass` needs a live session in the first place. A press
    that goes out anyway gets a silent no-op and a bot that never arrives."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("")  # the online lookup finds nobody
    result = _install(_ready_install(tmp_path), sql, chan).add("Pakka", "mage")
    assert result.added is False
    assert "logged in" in result.sentence
    assert chan.sent == ["dml_bridge_ping"]


def test_the_seam_never_asks_the_server_while_the_world_is_down(tmp_path: Path) -> None:
    """`world_running` is passed in rather than guessed at, and a stopped world
    is the second precondition — so no channel is opened and no SQL is run."""
    sql = _Sql()
    chan = _Chan()
    state = _install(_ready_install(tmp_path), sql, chan, running=False).state("Pakka")
    assert state.ready is False
    assert "not running" in state.blocker
    assert sql.asked == []
    assert chan.sent == []


def test_the_seam_reads_the_ground_and_shows_the_party(tmp_path: Path) -> None:
    """The group read is anchored on the master's ONLINE guid, and both reads
    happen: the lookup, then the group."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("1001\n", "Bottom\t777\t8\t1\n")
    state = _install(_ready_install(tmp_path), sql, chan).state("Pakka")
    assert state.ready is True
    assert state.blocker == ""
    assert state.members == (party.Member("Bottom", 777, 8, 1),)
    assert "online = 1" in sql.asked[0]
    assert "memberGuid=1001" in sql.asked[1].replace(" ", "")


def test_a_bridge_that_does_not_answer_leaves_the_group_unready(tmp_path: Path) -> None:
    """The live half of "never reads as ready": the disk says everything is in
    place and the SERVER says it has never heard of the bridge."""
    chan = _Chan({"dml_bridge_ping": Answer("no", "Command 'dml_bridge_ping' does not exist")})
    sql = _Sql()
    state = _install(_ready_install(tmp_path), sql, chan).state("Pakka")
    assert state.ready is False
    assert "does not answer" in state.blocker
    assert sql.asked == []


def test_the_group_rows_come_back_as_members_and_a_bad_row_is_said_so() -> None:
    """Four fields per row, and a row that is not four fields is reported
    rather than skipped: a party silently one bot short is the same lie as a
    party that never filled."""
    assert party.read_members("Bottom\t777\t8\t1\n") == (party.Member("Bottom", 777, 8, 1),)
    assert isinstance(party.read_members("Bottom\t777\n"), str)


def test_a_script_the_module_brought_is_not_a_missing_bridge_script(tmp_path: Path) -> None:
    """MEASURED on `yulon-ubuntu2` 2026-09-09, by the rebuild lane: `mod-ale`
    installs its own example `LootPet.lua` into the very directory the bridge
    is deployed to. The deployed precondition asked for set EQUALITY, so on the
    only install where the engine has ever worked My Party would have reported
    "some of the bridge scripts are missing" and named none of them — while all
    five were there. What is required is that ours are all present."""
    facts = _facts(deployed=(*party.BRIDGE_SCRIPTS, "LootPet.lua"))
    assert party.ready(facts) is True
    assert party.blocker(facts) is None


def test_the_conf_is_read_for_both_keys_or_for_neither(tmp_path: Path) -> None:
    """`ALE.Enabled` and `ALE.ScriptPath` are read from column 0 only, the same
    rule `conf.patch()` writes by: the shipped `mod_ale.conf.dist` is full of
    commented prose, and a looser pattern reads its own comment saying `true`
    as the setting — which is the exact lie the compiled default `false` makes
    expensive (`ALEConfig.cpp:20`)."""
    conf = tmp_path / "mod_ale.conf"
    conf.write_text(
        "# ALE.Enabled = 1\n"
        '#   ALE.ScriptPath = "/somewhere/else"\n'
        "ALE.Enabled = 1\n"
        'ALE.ScriptPath = "/azerothcore/env/dist/etc/modules/lua_scripts"\n'
    )
    read = party.read_conf(conf)
    assert read.present is True
    assert read.enabled is True
    assert read.script_path == party.ALE_SCRIPT_PATH


def test_a_conf_that_is_not_there_reads_as_absent_and_not_as_off(tmp_path: Path) -> None:
    """`enabled=None` and not `False`: a file nobody wrote has said nothing
    about the engine, and "switched off" sends a person to edit a key that is
    not there. The precondition above it is what names the real fix."""
    read = party.read_conf(tmp_path / "nothing.conf")
    assert read.present is False
    assert read.enabled is None
    assert read.script_path is None


def test_the_facts_are_gathered_from_the_disk_the_binary_and_the_wire(tmp_path: Path) -> None:
    """`read_facts` is what the tab's seam calls. It asks each source once and
    it asks the SERVER whether the bridge is there — the deploy's own success is
    not evidence, which is the whole finding of 2026-08-20."""
    server = tmp_path / "wowserver"
    (server / "modules" / "mod-ale").mkdir(parents=True)
    (server / "env" / "dist" / "etc" / "modules").mkdir(parents=True)
    (server / "env" / "dist" / "etc" / "modules" / "mod_ale.conf").write_text(
        f'ALE.Enabled = 1\nALE.ScriptPath = "{party.ALE_SCRIPT_PATH}"\n'
    )
    dest = party.dest_dir(server)
    dest.mkdir(parents=True)
    for name in party.BRIDGE_SCRIPTS:
        (dest / name).write_text("-- x\n")
    facts = party.read_facts(
        server,
        world_running=True,
        engine=party.BinaryRead(True, "in"),
        probe=party.Probe(True, "answered", "DML-BRIDGE-READY"),
    )
    assert party.ready(facts) is True
    assert facts.deployed == tuple(sorted(party.BRIDGE_SCRIPTS))
    assert facts.engine_cloned is True


def test_the_facts_carry_a_server_that_was_never_installed(tmp_path: Path) -> None:
    facts = party.read_facts(
        tmp_path / "nowhere",
        world_running=False,
        engine=party.BinaryRead(None, "unread"),
        probe=party.Probe(None, "unasked", ""),
    )
    assert facts.server_installed is False
    assert party.blocker(facts) is not None
    assert "not installed" in party.blocker(facts)  # type: ignore[operator]


def test_a_row_that_is_still_there_is_not_reported_as_dismissed() -> None:
    chan = _Chan()
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: (party.Member("Newbot", 777, 8, 1),),
        tries=2,
        sleep=lambda _s: None,
    )
    assert result.removed is False
    assert "still" in result.sentence


def test_a_group_read_that_keeps_failing_is_reported_as_unknown_not_removed() -> None:
    """T21 (T13 round 2 review, note 5). `_rows_only` folds a FAILED read into
    `()`, which is right for `add_bot`'s poll — watching for a guid to APPEAR,
    where a read that could not be done adds nothing either way — and wrong
    for this one, which is watching for a guid to VANISH: `()` reads as
    "gone". Round 2 of T13 removed the one route that made a failed read
    look like silence (the Lua's empty SOAP reply); what remains is a genuine
    uninvite whose follow-up read errors, and it must not become "removed" on
    a read that never happened."""
    chan = _Chan()
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=chan.send,
        members=lambda: "could not read this character's party: connection refused",
        tries=2,
        sleep=lambda _s: None,
    )
    assert result.removed is False
    assert result.logged_out is True
    assert "Newbot" in result.sentence
    assert "could not read this character's party: connection refused" in result.sentence


def test_a_group_read_that_fails_once_does_not_fail_the_dismiss() -> None:
    """A transient failure between two polls is neither "still there" nor
    "already gone" — it is nothing yet, and the poll tries again rather than
    giving up on the first bad read."""
    reads = iter(["could not read this character's party: timeout", ()])
    result = party.dismiss(
        player="Pakka",
        bot="Newbot",
        send=_Chan().send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert result.removed is True


# -- 8.6, T5: the chosen spec ----------------------------------------------
#
# Every fact in this section was measured on `yulon-ubuntu2` on 2026-09-09 by
# READING this tree's own `mod-playerbots` checkout and its shipped
# `playerbots.conf.dist`. None of it is the bash launcher's list: that list is a
# static mirror of a 2026-07-19 reading of a DIFFERENT tree, and two of its
# claims are false here (it has no `prot pve` for paladin, and it drops class 6
# entirely -- this conf has seven death-knight specs and this tree's `addclass`
# takes `dk`).

SPEC_CONF = """
# a comment
AiPlayerbot.PremadeSpecName.1.0 = arms pve
AiPlayerbot.PremadeSpecName.1.1 = fury pve
AiPlayerbot.PremadeSpecName.8.0 = arcane pve
AiPlayerbot.PremadeSpecName.8.1 = fire pve
AiPlayerbot.PremadeSpecLink.8.1.80 = 0-53-18
#AiPlayerbot.PremadeSpecName.8.2 = frost pve
AiPlayerbot.PremadeSpecName.6.0 = blood pve
"""
"""A conf shaped like the real one, with the three things that decide the answer:
a commented key (which is not a setting), a `PremadeSpecLink` sibling (which is
not a name), and a class the prior art's list dropped."""


def test_the_premade_specs_are_read_from_this_installs_own_conf() -> None:
    """The accepted names are the conf's, per class, and never a list in here.

    Measured (`mod-playerbots/src/PlayerbotAIConfig.cpp:487-493`, read on
    `yulon-ubuntu2` 2026-09-09): the module loads
    `AiPlayerbot.PremadeSpecName.<class>.<specno>` and `SpecPick` compares the
    whispered text to those values with `==`
    (`src/Ai/Base/Actions/ChangeTalentsAction.cpp:144`). So the conf IS the
    accepted list, and a hand-kept list here could only ever drift from it.
    """
    names = party.read_spec_names(SPEC_CONF)
    assert names[1] == ("arms pve", "fury pve")
    assert names[8] == ("arcane pve", "fire pve")


def test_a_death_knight_spec_is_read_here_although_the_prior_arts_list_drops_them() -> None:
    """Class 6 is a per-tree fact, and this tree has it both ways: `BOT_CLASSES`
    takes `dk` and the conf lists its specs. `_party_spec_names` skips class 6
    (`rust-main:cli/src/50-party.sh:157`), which here would offer a death knight
    no spec at all."""
    assert party.read_spec_names(SPEC_CONF)[6] == ("blood pve",)


def test_a_spec_number_the_conf_skips_hides_every_spec_after_it() -> None:
    """MEASURED, and not a guess about tidiness: `SpecPick` walks specno upwards
    and **breaks** at the first empty name
    (`ChangeTalentsAction.cpp:138-142`, `yulon-ubuntu2` 2026-09-09). So a gap at
    8.2 makes 8.3 unreachable no matter what the conf says about it -- offering
    it would be offering a name the module answers "not found" to, in the game
    window where nothing this app can read will see it."""
    conf = SPEC_CONF + "AiPlayerbot.PremadeSpecName.8.3 = frostfire pve\n"
    assert party.read_spec_names(conf)[8] == ("arcane pve", "fire pve")


def test_a_class_the_conf_says_nothing_about_has_no_specs_rather_than_a_default() -> None:
    assert party.read_spec_names(SPEC_CONF).get(11, ()) == ()


def test_the_class_ids_are_the_modules_own_and_the_class_list_is_derived_from_them() -> None:
    """One table, so the picker's class and the conf's class cannot disagree.

    `PlayerbotMgr.cpp:1093-1134` (`yulon-ubuntu2` 2026-09-09) is where `addclass`
    turns the word into the id: shaman is 7, druid is 11, dk is 6, and there is
    no 10. `BOT_CLASSES` is derived from this mapping rather than written beside
    it, because a second list is a list that will one day be missing a class the
    specs are keyed by."""
    assert party.BOT_CLASS_IDS["shaman"] == 7
    assert party.BOT_CLASS_IDS["druid"] == 11
    assert party.BOT_CLASS_IDS["dk"] == 6
    assert tuple(party.BOT_CLASS_IDS) == party.BOT_CLASSES


def test_the_spec_whisper_is_the_prior_arts_command() -> None:
    """`dml_whisper <master> <bot> talents spec <name>`
    (`rust-main:crates/dml-wow/src/party.rs:253`, `cli/src/90-main.sh:3976`).
    The same Lua is on the other side of the wire, so a second spelling would be
    a second protocol."""
    assert (
        party.spec_command("Pakka", "Bottom", "frost pve")
        == "dml_whisper Pakka Bottom talents spec frost pve"
    )


@pytest.mark.parametrize(
    "bad",
    ["", " arms pve", "arms pve; .server shutdown 1", 'arms "pve"', "arms\npve", "arms\\pve"],
)
def test_a_spec_that_could_break_out_of_the_whisper_never_reaches_the_channel(bad: str) -> None:
    """The tail of a command the world server parses, so the check is a boundary.

    The charset is the prior art's (`50-party.sh:208-215`) and it is WIDER than
    the shipped names' lowercase-and-spaces on purpose: `playerbots.conf` is
    hand-editable and the picker offers whatever it says, so refusing
    `Arms PvE` here would refuse a name the module accepts. What it must never
    admit is a quote, a backslash, a newline or a shell separator."""
    with pytest.raises(party.BadRequest):
        party.spec_command("Pakka", "Bottom", bad)


def test_a_spec_this_server_does_not_list_is_refused_before_anything_is_sent() -> None:
    """The whole reason the app validates at all: a name the module does not have
    is answered `Spec <x> not found` IN THE GAME WINDOW
    (`ChangeTalentsAction.cpp:157`), by `TellMasterNoFacing`. Nothing comes back
    over the channel, so a wrong spec sent is a bot that is silently not specced
    and an app that reported success."""
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        spec="warglaive pvp",
        specs=("arcane pve", "fire pve"),
        send=chan.send,
        members=lambda: (),
    )
    assert chan.sent == []
    assert result.added is False
    assert result.blocker == result.sentence
    assert "warglaive pvp" in result.sentence
    assert "arcane pve" in result.sentence


def test_a_spec_asked_for_where_this_install_lists_none_says_so_rather_than_an_empty_list() -> None:
    """A conf that was never deployed lists nothing, and "choose one of: " with
    nothing after it is a refusal that tells a person to do the impossible."""
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        spec="fire pve",
        specs=(),
        send=chan.send,
        members=lambda: (),
    )
    assert chan.sent == []
    assert "playerbots.conf" in result.sentence


def test_the_chosen_spec_is_whispered_after_the_join_and_the_gear_follows_it() -> None:
    """Three orderings, and each is a measured reason rather than a preference.

    * The spec is whispered AFTER the join, because it is addressed to the bot by
      name and there is no name until the group table has one.
    * `autogear` follows the spec, because gear must match the new talents
      (`90-main.sh:3975-3977`, whose own comment says so).
    * `talents autopick` is NOT sent, because it is the thing the chosen spec
      replaces -- `InitTalentsTree(true)` after `InitTalentsBySpecNo` would pick
      the talents again and the chosen spec would be gone
      (`ChangeTalentsAction.cpp:57-59` and `:146`).
    """
    fresh = party.Member(name="Newbot", guid=777, klass=8, level=1)
    reads = iter([(), (fresh,), (fresh,)])
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        spec="fire pve",
        specs=("arcane pve", "fire pve"),
        send=chan.send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert result.spec == "fire pve"
    assert result.specced is True
    assert "dml_whisper Pakka Newbot talents autopick" not in chan.sent
    assert chan.sent.index("dml_whisper Pakka Newbot talents spec fire pve") < chan.sent.index(
        "dml_whisper Pakka Newbot autogear"
    )
    assert "fire pve" in result.sentence


def test_the_sentence_never_claims_the_talents_took() -> None:
    """The module answers a spec only in the game window, so `spec_sent` is the
    whisper being accepted and nothing more. A sentence saying the bot IS fire
    pve would be this app asserting something it cannot read -- the readback is
    the bot's talent table, and that is the live half's job."""
    fresh = party.Member(name="Newbot", guid=777, klass=8, level=1)
    reads = iter([(), (fresh,), (fresh,)])
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        spec="fire pve",
        specs=("fire pve",),
        send=_Chan().send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert "in the game window" in result.sentence


def test_no_spec_leaves_the_finishing_pair_exactly_as_it_was() -> None:
    """The prior art's rule for its own spec branch: "No --spec keeps this branch
    byte-identical to before" (`90-main.sh:3973`). This tree's gear-then-autopick
    pair was proved live on 2026-09-09 and a reorder nobody measured is a change
    to a working path."""
    fresh = party.Member(name="Newbot", guid=777, klass=8, level=1)
    reads = iter([(), (fresh,)])
    chan = _Chan()
    party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        send=chan.send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert chan.sent == [
        "dml_addclass Pakka mage",
        "dml_whisper Pakka Newbot autogear",
        "dml_whisper Pakka Newbot talents autopick",
    ]


# -- 8.6, T5: the chosen level ----------------------------------------------
#
# The level is 8.4a's seam and a bot is a character, so this sends
# `play.InstallPlay.set_level` and never a second level path. What is measured
# per install is the BOUND: `MaxPlayerLevel = 80` in
# `env/dist/etc/worldserver.conf` on `yulon-ubuntu2`, 2026-09-09. Eighty is not
# written into this app anywhere -- it is a conf value on one install of one
# tree, and the fork that ships 255 or 60 would be silently mis-bounded by a
# constant.


class _Level:
    """A stand-in for the Characters tab's `set_level`, recorded into one log.

    One shared log with the channel, because the ORDER between them is the thing
    two of these tests are about and two separate lists cannot show it.
    """

    def __init__(
        self, log: list[str], outcome: Outcome | None = None, *, then: Outcome | None = None
    ) -> None:
        self.log = log
        self.outcome = outcome or Outcome(True, "level changed")
        self.then = then
        """What the SECOND send answers, where a test is about the resend (T16
        round 2). One stand-in that answers the same thing twice cannot tell a
        first send that failed apart from a second one that did, and a test that
        cannot tell them apart passes against code that confuses them — which is
        what happened to the first version of the resend-refused test."""
        self.asked: list[tuple[str, int]] = []

    def __call__(self, character: str, level: int) -> Outcome:
        self.asked.append((character, level))
        self.log.append(f"set_level {character} {level}")
        if self.then is not None and len(self.asked) > 1:
            return self.then
        return self.outcome


class _Rows:
    """The group read, scripted, with the LAST reading repeating for ever (T16).

    `lambda: next(iter([...]))` was enough while the level step read the row
    once. It polls now — the row of an ONLINE character is written by its next
    save and not by the level command (`cs_character.cpp:252-281`) — and a
    finite script ends such a test in `StopIteration` rather than in the answer
    the test is about. Repeating the last reading is what a group table that has
    stopped changing does, and `taken` lets a test say how many reads its own
    claim cost.
    """

    def __init__(self, *readings: tuple[party.Member, ...]) -> None:
        self.readings = list(readings)
        self.taken = 0

    def __call__(self) -> tuple[party.Member, ...]:
        self.taken += 1
        return self.readings[min(self.taken, len(self.readings)) - 1]


class _LoggedChan(_Chan):
    """`_Chan` writing into a shared log as well as its own list."""

    def __init__(self, log: list[str], answers: dict[str, Answer] | None = None) -> None:
        super().__init__(answers)
        self.log = log

    def send(self, command: str) -> Answer:
        self.log.append(command)
        return super().send(command)


def test_the_maximum_level_is_read_from_this_servers_own_worldserver_conf(tmp_path: Path) -> None:
    """Measured on `yulon-ubuntu2` 2026-09-09: `MaxPlayerLevel = 80`, at
    `env/dist/etc/worldserver.conf:2128`. Read rather than assumed, and read with
    the same column-0 rule `read_conf` uses -- the shipped `.dist` carries
    commented keys, and a pattern that matched them would read the file's prose
    as its settings."""
    server = tmp_path / "wowserver"
    (server / "env" / "dist" / "etc").mkdir(parents=True)
    (server / party.WORLD_CONF).write_text(
        "#MaxPlayerLevel = 255\nStartPlayerLevel = 1\nMaxPlayerLevel = 80\n"
    )
    assert party.max_player_level(server) == 80


def test_a_maximum_level_that_could_not_be_read_is_none_and_not_a_guess(tmp_path: Path) -> None:
    """A conf that is not there has said nothing about the level cap. `None` is a
    third answer here for the same reason it is one in `ConfRead`: a default of
    80 would be this app inventing a fact about somebody's fork."""
    assert party.max_player_level(tmp_path / "nowhere") is None


def test_a_level_above_this_servers_own_maximum_is_refused_before_anything_is_sent() -> None:
    """The bound is the server's, so the refusal names the server's number."""
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=90,
        max_level=80,
        set_level=_Level([]),
        send=chan.send,
        members=lambda: (),
    )
    assert chan.sent == []
    assert result.added is False
    assert result.blocker == result.sentence
    assert "80" in result.sentence


def test_a_level_asked_for_where_the_maximum_could_not_be_read_is_refused() -> None:
    """Not bounded by a guess and not sent unbounded: the app says it could not
    read the cap, which is the thing a person can fix."""
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=None,
        set_level=_Level([]),
        send=chan.send,
        members=lambda: (),
    )
    assert chan.sent == []
    assert "MaxPlayerLevel" in result.sentence


def test_a_level_below_one_is_refused_before_anything_is_sent() -> None:
    chan = _Chan()
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=0,
        max_level=80,
        set_level=_Level([]),
        send=chan.send,
        members=lambda: (),
    )
    assert chan.sent == []
    assert result.blocker == result.sentence


def test_the_level_is_set_on_the_bot_and_read_back_out_of_the_group_table() -> None:
    """`characters.level` after the press, which is what the design asks for
    (`.notes/phase8-designs/b-users-surface.md:422`) -- not the number the button
    sent, and not the level command's own `yes`. The group read already carries
    it (`Member.level` IS `characters.level`), so the readback costs one more
    read of a query this press already makes."""
    log: list[str] = []
    joined_low = party.Member("Newbot", 777, 8, 1)
    joined_high = party.Member("Newbot", 777, 8, 60)
    reads = iter([(), (joined_low,), (joined_high,)])
    level = _Level(log)
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60)]
    assert (result.level, result.level_before, result.level_after) == (60, 1, 60)
    assert "characters.level read 1 before the press and 60 after" in result.sentence


def test_a_bot_already_at_the_chosen_level_is_told_so_rather_than_counted_as_a_change() -> None:
    """The gate rule in the app's own voice: a step whose assertion was already
    true before its action has proved nothing. A bot the server made at 60, asked
    for 60, reads back 60 -- and reporting that as "now at level 60" is the
    sentence that would let the live gate certify a level it never changed."""
    log: list[str] = []
    already = party.Member("Newbot", 777, 8, 60)
    reads = iter([(), (already,), (already,)])
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=_Level(log),
        send=_LoggedChan(log).send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert (result.level_before, result.level_after) == (60, 60)
    assert "already" in result.sentence


def test_a_level_the_written_row_still_disagrees_with_is_not_reported_as_set() -> None:
    """The command accepted and `characters.level` unchanged is a third state, and
    it is the one a console that answered `yes` to nothing looks like.

    **T16 rewrote what this test pins, and the change is the point.** Until
    2026-09-10 it pinned a sentence saying the row "has been seen to stay
    unwritten for as long as this panel watched it" — a reading taken from
    `8.6-spec-level-dismiss-…`'s two photographs, where the row read 1 seconds
    after a level of 42 and of 55. `8.6-level-holds-yulon-ubuntu2-2026-09-10`
    put the world's own `.pinfo` beside that row and found the level had landed
    within a second every time; the row had simply not been written. So this arm
    is now reachable only AFTER the step has asked the server to write the rows
    and has sent the level a second time, and the sentence says so.
    """
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    rows = _Rows((), (low,), (low,))
    level = _Level(log)
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=rows,
        level_tries=2,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert (result.level_before, result.level_after) == (1, 1)
    assert level.asked == [("Newbot", 60), ("Newbot", 60)], "sent again, and exactly once again"
    assert log.count(party.SAVE_COMMAND) == 2, "each reading was taken off a row that was written"
    assert result.level_resent is True
    assert "still reads 1, not 60" in result.sentence
    assert "after the row was written and the level was sent a second time" in result.sentence
    assert "Take the level as not set." in result.sentence


def test_a_refused_level_does_not_stop_the_spec_or_the_gear() -> None:
    """One refusal must not hide the others, and it must not cancel them either:
    the level is the Characters tab's console command and the spec is a whisper
    through the bridge, so a tree with no `character level` still gets its spec."""
    log: list[str] = []
    joined = party.Member("Newbot", 777, 8, 1)
    reads = iter([(), (joined,), (joined,)])
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        spec="fire pve",
        specs=("fire pve",),
        level=60,
        max_level=80,
        set_level=_Level(log, Outcome(False, problem="There is no such subcommand")),
        send=_LoggedChan(log).send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert "There is no such subcommand" in result.sentence
    assert "dml_whisper Pakka Newbot talents spec fire pve" in log
    assert "dml_whisper Pakka Newbot autogear" in log
    assert result.specced is True


def test_the_level_is_set_before_the_spec_is_whispered() -> None:
    """MEASURED, and the reason the order is not a preference: `SpecPick` applies
    the premade build through `PlayerbotFactory factory(bot, bot->GetLevel())`
    (`ChangeTalentsAction.cpp:146-149`, `yulon-ubuntu2` 2026-09-09). A spec
    applied at level 1 and then levelled to 60 is a bot with a level-1 build."""
    log: list[str] = []
    joined = party.Member("Newbot", 777, 8, 1)
    party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        spec="fire pve",
        specs=("fire pve",),
        level=60,
        max_level=80,
        set_level=_Level(log),
        send=_LoggedChan(log).send,
        members=_Rows((), (joined,), (party.Member("Newbot", 777, 8, 60),)),
        sleep=lambda _s: None,
    )
    assert log.index("set_level Newbot 60") < log.index(
        "dml_whisper Pakka Newbot talents spec fire pve"
    )


# -- 8.6, T16: the chosen level holds, and the row is written before it is read
#
# MEASURED on `yulon-ubuntu2` 2026-09-10,
# `.notes/gates/8.6-level-holds-yulon-ubuntu2-2026-09-10/`. On a bot freshly
# joined to a master's party the level sent at +0, +5, +10, +20 and +30 seconds
# after the group row appeared landed every time and STAYED landed: the world's
# own `.pinfo` read the chosen level within a second and still read it a minute
# later. There is no window in which the module takes it back. What T5
# photographed is `characters.level` not having been written --
# `.character level` on an ONLINE character calls `GiveLevel` and writes no row
# (AzerothCore `src/server/scripts/Commands/cs_character.cpp:252-281`), and a
# character writes its own row at `PlayerSaveInterval`, 900 000 ms here. So the
# fix is not a wait and not a blind resend: it is asking the server to write the
# rows, which is what `party.SAVE_COMMAND` is.


def test_the_level_readback_is_the_row_the_server_wrote_and_not_the_commands_own_yes() -> None:
    """The readback is `characters.level` AFTER a save, and never the reply.

    `set_level` here answers exactly what the box answered -- `You changed level
    of Newbot to 60.` -- while the row still reads 1, which is the state this
    whole ticket exists about. A step that believed the reply would report a
    level it had not read; a step that read the row without writing it first
    would report the last save. This one sends `SAVE_COMMAND` between the two.
    """
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    high = party.Member("Newbot", 777, 8, 60)
    level = _Level(log, Outcome(True, "You changed level of Newbot to 60."))
    rows = _Rows((), (low,), (low,), (low,), (high,))
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=rows,
        level_tries=6,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60)], "one send: the row agreed once it was written"
    assert log.count(party.SAVE_COMMAND) == 1
    assert log.index("set_level Newbot 60") < log.index(party.SAVE_COMMAND)
    assert (result.level_before, result.level_after) == (1, 60)
    assert result.level_resent is False
    assert "characters.level read 1 before the press and 60 after" in result.sentence


def test_a_level_the_written_row_agrees_with_is_never_sent_a_second_time() -> None:
    """The resend fires on a disagreement and on nothing else.

    The row here disagrees at the first reading -- it always does, because
    nothing has written it -- and agrees at the first reading after the save.
    That is the ordinary press, and the ordinary press must send the level once.
    """
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    high = party.Member("Newbot", 777, 8, 60)
    level = _Level(log)
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=_Rows((), (low,), (low,), (high,)),
        level_tries=4,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60)]
    assert result.level_resent is False
    assert "had to be sent a second time" not in result.sentence


def test_a_level_that_needed_a_second_send_says_so_in_the_panels_own_sentence() -> None:
    """A press that only worked the second time is not the same press as one that
    worked, and a sentence that hid the difference would make the two
    indistinguishable in a gate folder."""
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    high = party.Member("Newbot", 777, 8, 60)
    level = _Level(log)
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=_Rows((), (low,), (low,), (low,), (low,), (high,)),
        level_tries=2,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60), ("Newbot", 60)]
    assert log.count(party.SAVE_COMMAND) == 2
    assert result.level_resent is True
    assert (result.level_before, result.level_after) == (1, 60)
    assert "had to be sent a second time to get there" in result.sentence


def test_a_saveall_the_server_refused_is_its_own_sentence_and_not_a_verdict_on_the_level() -> None:
    """T16 round 2's must-fix 1. The save is a SEND, and its answer is read.

    Thrown away — which is what the first version of the level step did — a
    `saveall` the server refused bought thirty seconds of polling a row nobody
    had written, then a resend, then a second ignored save, and then a sentence
    saying the row HAD been written and the level had been sent twice. All four
    are wrong and the last one is a lie in the panel's own words.

    So a refused save ends the step: no poll, no resend, and a sentence that says
    what happened instead of ruling on the level.
    """
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    level = _Level(log)
    chan = _LoggedChan(log, {party.SAVE_COMMAND: Answer("no", text="Server is shutting down")})
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=chan.send,
        members=_Rows((), (low,), (low,)),
        level_tries=30,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60)], "no resend: nothing was read that could disagree"
    assert log.count(party.SAVE_COMMAND) == 1, "and no second save either"
    assert result.level_resent is False
    assert f"the server did not run {party.SAVE_COMMAND}" in result.sentence
    assert "Server is shutting down" in result.sentence
    assert "nothing here says whether the level took" in result.sentence
    assert "still reads 1, not 60" not in result.sentence, "not a verdict on the level"
    assert "Take the level as not set." not in result.sentence


def test_a_saveall_that_never_came_back_is_not_reported_as_one_the_server_refused() -> None:
    """`indeterminate` is its own arm: a SOAP timeout means the command was sent
    and the answer never came (`channel.Answer`), so the rows may be written and
    may not. "The server did not run it" is a claim nothing here can make, and
    `add_bot` has no business retrying a write it cannot see."""
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    level = _Level(log)
    chan = _LoggedChan(
        log,
        {party.SAVE_COMMAND: Answer("unknown", reason="the reply never came", indeterminate=True)},
    )
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=chan.send,
        members=_Rows((), (low,), (low,)),
        level_tries=30,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60)]
    assert log.count(party.SAVE_COMMAND) == 1
    assert "did not come back" in result.sentence
    assert "may have run and may not" in result.sentence
    assert f"the server did not run {party.SAVE_COMMAND}" not in result.sentence


def test_a_bot_already_at_the_chosen_level_whose_written_row_disagrees_is_told_the_row() -> None:
    """Round 2's reviewer, on the order of the arms. "Nothing about the level
    changed" was tested BEFORE the readback, so a bot that was already at 60 and
    whose WRITTEN row came back at 1 was told nothing had changed — off the one
    reading that says something did."""
    log: list[str] = []
    already = party.Member("Newbot", 777, 8, 60)
    moved = party.Member("Newbot", 777, 8, 1)
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=_Level(log),
        send=_LoggedChan(log).send,
        members=_Rows((), (already,), (moved,)),
        level_tries=2,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert (result.level_before, result.level_after) == (60, 1)
    assert "still reads 1, not 60" in result.sentence
    assert "already read" not in result.sentence


def test_a_refused_second_send_says_the_first_one_was_taken() -> None:
    """Round 2's reviewer: the resend-failed path used to open "The level was not
    set", which is false — the first send was accepted and the row was written.
    What failed is the second send, and the sentence has to say which.

    `then=` is what makes this test able to fail: the first send succeeds and the
    SECOND is refused. Written with one outcome for both, the first arm fires and
    the test passes against code that opens either sentence."""
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    level = _Level(log, then=Outcome(False, problem="There is no such subcommand"))
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=_Rows((), (low,), (low,)),
        level_tries=2,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60), ("Newbot", 60)], "the second send is the one refused"
    assert result.level_resent is True
    assert "The server took the level and characters.level read 1, not 60" in result.sentence
    assert "The second send was refused: There is no such subcommand" in result.sentence
    assert "The level was not set" not in result.sentence


def test_a_bot_that_left_the_group_before_the_row_was_read_is_not_sent_the_level_again() -> None:
    """A row that is not there is not a level, and it must not earn a
    resend or a save. The bot manager logs bots in and out on its own timer, and
    a level sent again at a bot that has gone is a command sent about a party
    nobody is in."""
    log: list[str] = []
    low = party.Member("Newbot", 777, 8, 1)
    level = _Level(log)
    result = party.add_bot(
        facts=_facts(),
        player="Pakka",
        klass="mage",
        level=60,
        max_level=80,
        set_level=level,
        send=_LoggedChan(log).send,
        members=_Rows((), (low,), ()),
        level_tries=2,
        level_pause=0.0,
        sleep=lambda _s: None,
    )
    assert level.asked == [("Newbot", 60)]
    assert log.count(party.SAVE_COMMAND) == 0
    assert result.level_after is None
    assert result.level_resent is False
    assert "no longer in the group table" in result.sentence


def test_a_level_with_no_route_to_set_it_raises_rather_than_reporting_a_send() -> None:
    """A caller asking for a level with no setter is a mistake in this app and not
    a refusal to show a user, so it is raised the way a bad class name is."""
    with pytest.raises(party.BadRequest):
        party.add_bot(
            facts=_facts(),
            player="Pakka",
            klass="mage",
            level=60,
            max_level=80,
            send=_Chan().send,
            members=lambda: (),
        )


# -- 8.6, T5: dismiss all ---------------------------------------------------


def test_dismiss_all_sends_every_bot_away_and_names_each_one() -> None:
    """Each dismissal reported by name. A count alone ("3 bots dismissed") is a
    sentence nobody can check against the party frame in front of them."""
    chan = _Chan()
    result = party.dismiss_all(
        player="Pakka",
        bots=("Anmi", "Jilsur"),
        send=chan.send,
        members=lambda: (),
        sleep=lambda _s: None,
    )
    assert result.attempted == 2
    assert [d.bot for d in result.dismissals] == ["Anmi", "Jilsur"]
    assert all(d.removed for d in result.dismissals)
    assert "Anmi" in result.sentence
    assert "Jilsur" in result.sentence
    assert chan.sent == [
        "dml_uninvite Pakka Anmi",
        "dml_whisper Pakka Anmi logout",
        "dml_uninvite Pakka Jilsur",
        "dml_whisper Pakka Jilsur logout",
    ]


def test_one_bot_that_will_not_leave_does_not_hide_the_others() -> None:
    """The prior art's own review finding, in the other direction: "one
    unreachable bot must not strand the rest of the party"
    (`90-main.sh:4076`). And the bot that stayed is named with the reason it
    stayed, rather than subtracted from a count."""

    class _Half(_Chan):
        def send(self, command: str) -> Answer:
            self.sent.append(command)
            if command == "dml_uninvite Pakka Anmi":
                return Answer("no", "Command 'dml_uninvite' does not exist")
            return Answer("yes", "ok")

    chan = _Half()
    result = party.dismiss_all(
        player="Pakka",
        bots=("Anmi", "Jilsur"),
        send=chan.send,
        # Neither row is in this reading, so the only thing that decides Anmi is
        # the refusal to its uninvite.
        members=lambda: (),
        sleep=lambda _s: None,
    )
    assert [d.removed for d in result.dismissals] == [False, True]
    assert "Anmi" in result.sentence
    assert "does not exist" in result.sentence
    assert "Jilsur" in result.sentence
    assert "dml_uninvite Pakka Jilsur" in chan.sent, "the refusal must not stop the next bot"


def test_dismiss_all_with_no_bots_sends_nothing_and_says_so() -> None:
    """Nothing to dismiss is a refusal with a `blocker`, which is this module's
    word for "the channel was never touched"."""
    chan = _Chan()
    result = party.dismiss_all(
        player="Pakka", bots=(), send=chan.send, members=lambda: (), sleep=lambda _s: None
    )
    assert chan.sent == []
    assert result.attempted == 0
    assert result.blocker == result.sentence
    assert result.dismissals == ()


def test_the_seam_reads_the_party_before_dismissing_all_of_it(tmp_path: Path) -> None:
    """The list comes from the group table at the moment of the press, not from
    whatever the panel last drew: the bot manager logs bots in and out on a timer
    and a list read minutes ago is 8.4d's finding all over again.

    The caller supplies what it is ALLOWED to dismiss, and the table supplies who
    that is — the guid confirmed is matched against the fresh read, and the name
    that goes into `dml_uninvite` comes from the row, never from the caller."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("1001\n", "Anmi\t949\t8\t1\n", "1001\n", "")
    result = _install(_ready_install(tmp_path), sql, chan).remove_all("Pakka", (949,))
    assert result.attempted == 1
    assert [d.bot for d in result.dismissals] == ["Anmi"]
    assert "dml_uninvite Pakka Anmi" in chan.sent


def test_the_seam_dismisses_nothing_where_the_party_could_not_be_read(tmp_path: Path) -> None:
    """A read that failed is not an empty party: reporting "no bots to dismiss"
    for a group table nobody could read is the same lie `_rows_only` exists to
    keep out of the poll."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("")  # the online lookup finds nobody
    result = _install(_ready_install(tmp_path), sql, chan).remove_all("Pakka", (949,))
    assert result.attempted == 0
    assert "logged in" in result.sentence
    assert chan.sent == []


class _AlwaysFailsAfterOnline:
    """`online_guid` always resolves; every group-table read raises.

    An outage that starts the moment the press goes out: `dml_uninvite` and
    the logout whisper still land (this is not a channel that is down), but
    the poll's own read errors on every attempt."""

    def __init__(self, guid: str = "1001\n") -> None:
        self._guid = guid

    def query(self, db: str, statement: str) -> str:
        if "group_member" in statement:
            raise RuntimeError("connection refused")
        return self._guid


class _SucceedsOnceThenFails:
    """The group table reads once (the seam's own pre-dismiss confirmation)
    and raises on every read after -- an outage starting after the batch is
    already confirmed and the presses are already going out."""

    def __init__(self, first_group_row: str, guid: str = "1001\n") -> None:
        self._guid = guid
        self._first_group_row = first_group_row
        self._group_reads = 0

    def query(self, db: str, statement: str) -> str:
        if "group_member" in statement:
            self._group_reads += 1
            if self._group_reads == 1:
                return self._first_group_row
            raise RuntimeError("connection refused")
        return self._guid


def test_the_seam_does_not_report_removed_when_every_poll_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T21, round 2's must-fix 1 (Codex adversarial review, round 1): `dismiss`'s
    own unit tests call it directly with an already-unfolded `members`, so a
    regression that put `_rows_only` back between `InstallParty.remove` and the
    poll would sail through them unnoticed. This goes through the seam instead.

    `InstallParty.remove` exposes no `tries`/`pause`/`sleep` of its own, so the
    poll's real wait is silenced the same way `dismiss`'s own tests silence it
    -- by reaching past the seam to the one place that wait is injectable,
    `dismiss.__kwdefaults__["sleep"]` -- rather than actually waiting out
    `POLL_TRIES * POLL_SLEEP` (6 seconds) for a database that is never coming
    back in this test.
    """
    monkeypatch.setitem(party.dismiss.__kwdefaults__, "sleep", lambda _s: None)
    chan = _Chan()
    result = _install(_ready_install(tmp_path), _AlwaysFailsAfterOnline(), chan).remove(
        "Pakka", "Newbot"
    )
    assert result.removed is False
    assert result.unreadable is True
    assert "connection refused" in result.sentence


def test_the_batch_seam_does_not_report_removed_when_every_poll_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same finding on the batch route (Codex adversarial review, round 1,
    must-fix 1): round 1 of this ticket fixed `InstallParty.remove` and left
    `InstallParty.remove_all` handing `dismiss_all` the `_rows_only`-folded
    read, so a database outage mid-batch still reported every bot in it
    `removed=True`. `remove_all`'s own confirmation read succeeds once (that is
    what lets the batch start); the poll behind it never gets another good
    read."""
    monkeypatch.setitem(party.dismiss_all.__kwdefaults__, "sleep", lambda _s: None)
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _SucceedsOnceThenFails("Anmi\t949\t8\t1\n")
    result = _install(_ready_install(tmp_path), sql, chan).remove_all("Pakka", (949,))
    assert result.attempted == 1
    assert not any(d.removed for d in result.dismissals)
    assert all(d.unreadable for d in result.dismissals)
    assert "connection refused" in result.sentence


# -- 8.6, T5: three buckets for a mass dismissal (T21, round 2) -------------


def test_a_mass_dismissal_where_every_bot_is_unreadable_says_so_not_stayed() -> None:
    """T21, round 2's must-fix 2 (Codex adversarial review, round 1): "Still
    here" asserts a row somebody actually saw. A read that never happened
    cannot say that, so it gets its own words rather than being folded into
    the bucket for bots the poll actually watched stay."""
    result = party.dismiss_all(
        player="Pakka",
        bots=("Anmi", "Jilsur"),
        send=_Chan().send,
        members=lambda: "could not read this character's party: connection refused",
        tries=1,
        sleep=lambda _s: None,
    )
    assert not any(d.removed for d in result.dismissals)
    assert "None of 2 bots were confirmed to have left the party" in result.sentence
    assert (
        "None of 2 bots left the party" not in result.sentence
    ), "two polls that never read the table cannot say nobody left; all two may have"
    assert "Still here" not in result.sentence
    assert "Could not be confirmed" in result.sentence
    assert "connection refused" in result.sentence


def test_a_mixed_mass_dismissal_names_each_bucket_in_its_own_words() -> None:
    """One bot that went, one the bridge refused outright (a row somebody
    read), and one whose table could not be read at all: three different
    findings, and the sentence must not collapse the last two together."""

    class _Mixed(_Chan):
        def send(self, command: str) -> Answer:
            self.sent.append(command)
            if command == "dml_uninvite Pakka Jilsur":
                return Answer(
                    "yes",
                    "Jilsur is not in Pakka's party now (Jilsur is grouped with someone else)",
                )
            return Answer("yes", "ok")

    reads = iter([(), "could not read this character's party: timeout"])
    result = party.dismiss_all(
        player="Pakka",
        bots=("Anmi", "Jilsur", "Newbot"),
        send=_Mixed().send,
        members=lambda: next(reads),
        tries=1,
        sleep=lambda _s: None,
    )
    assert [d.removed for d in result.dismissals] == [True, False, False]
    sentence = result.sentence
    assert (
        "1 of 3 bots confirmed left the party: Anmi." in sentence
    ), "one read never happened, so the count is of what was confirmed"
    still_here = sentence.split("Still here — ")[1].split(" Could not be confirmed")[0]
    unreadable = sentence.split("Could not be confirmed — ")[1]
    assert "Jilsur" in still_here and "Newbot" not in still_here
    assert "Newbot" in unreadable and "Jilsur" not in unreadable


# -- 8.6, T5: what the seam offers the panel --------------------------------


def test_the_seam_offers_the_specs_this_install_lists_for_the_chosen_class(tmp_path: Path) -> None:
    """Class name in, this install's names out. The mapping is the module's own
    (`BOT_CLASS_IDS`) so the picker cannot ask for a class the conf is not keyed
    by."""
    server = _ready_install(tmp_path)
    (server / party.PLAYERBOTS_CONF).write_text(SPEC_CONF)
    seam = _install(server, _Sql(), None)
    assert seam.specs("mage") == ("arcane pve", "fire pve")
    assert seam.specs("dk") == ("blood pve",)
    assert seam.specs("druid") == ()


def test_a_shipped_dist_conf_is_not_a_deployed_one_and_offers_no_specs(tmp_path: Path) -> None:
    """The inverse of what this file asserted until the live gate ran it.

    It used to read the `.dist` when no conf was deployed, on the prior art's
    example and on a claim written here that nobody had checked -- that the
    server falls back to it too. **Measured on `yulon-ubuntu2`, 2026-09-09**: that
    box has a `.dist` and no `playerbots.conf`, and the module answered
    `talents spec list` with `Total 0 specs found`. Every one of the 63 names
    this app had offered came back `Spec <name> not found`, in the game window,
    where nothing here can hear it.

    So an install with only a `.dist` has NO premade specs, the picker offers
    only the server's own pick, and asking for one is refused by the app in its
    own words."""
    server = _ready_install(tmp_path)
    (server / (party.PLAYERBOTS_CONF + ".dist")).write_text(SPEC_CONF)
    assert _install(server, _Sql(), None).specs("mage") == ()


def test_a_deployed_conf_beside_a_dist_is_the_one_that_is_read(tmp_path: Path) -> None:
    """The control: the deployed file wins, and its contents are what is offered.

    Written with the two files DISAGREEING, because agreeing files cannot tell a
    reader that prefers the wrong one from a reader that prefers the right one.
    """
    server = _ready_install(tmp_path)
    (server / (party.PLAYERBOTS_CONF + ".dist")).write_text(SPEC_CONF)
    (server / party.PLAYERBOTS_CONF).write_text("AiPlayerbot.PremadeSpecName.8.0 = deployed only\n")
    assert _install(server, _Sql(), None).specs("mage") == ("deployed only",)


def test_the_seam_reads_this_installs_maximum_level(tmp_path: Path) -> None:
    server = _ready_install(tmp_path)
    (server / party.WORLD_CONF).write_text("MaxPlayerLevel = 60\n")
    assert _install(server, _Sql(), None).max_level() == 60


def test_the_seam_sets_a_level_through_the_characters_tab_and_not_a_second_path(
    tmp_path: Path,
) -> None:
    """8.4a's seam, reused. Two level paths would be two spellings of
    `character level` and two places to fix the day a fork renames it -- and the
    Characters tab's is the one that already refuses the trees with no such
    command, in the entry's own measured words."""
    seam = _install(_ready_install(tmp_path), _Sql(), None)
    assert seam.level_setter.__self__.__class__ is play.InstallPlay  # type: ignore[attr-defined]
    assert seam.level_setter.__name__ == "set_level"  # type: ignore[attr-defined]


def test_the_seam_hands_the_specs_the_bound_and_the_level_setter_to_the_press(
    tmp_path: Path,
) -> None:
    """Round 1's third finding: the three hand-offs had no test of their own.

    Every spec and level test above calls `add_bot` directly and supplies
    `specs=`, `max_level=` and `set_level=` itself, so the three lines in
    `InstallParty.add` that fill them in were covered by nothing -- blanking any
    of them (`specs=()`, `max_level=None`, `set_level=None`) left every test
    green while the panel's own press refused every spec, refused every level,
    or raised. This is the one press that goes the whole way: two conf files on
    disk, the group table read three times -- the ground, the poll, the level
    readback -- and the level setter injected so the console is not.
    """
    server = _ready_install(tmp_path)
    (server / party.PLAYERBOTS_CONF).write_text(SPEC_CONF)
    (server / party.WORLD_CONF).write_text("MaxPlayerLevel = 80\n")
    log: list[str] = []
    level = _Level(log)
    chan = _LoggedChan(log, {"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql(
        "1001\n",  # online_guid for the press
        "1001\n",  # the ground reading: online_guid…
        "",  # …and an empty group
        "1001\n",  # the poll: online_guid…
        "Newbot\t777\t8\t1\n",  # …and the bot, at the level the server made it
        "1001\n",  # the level readback: online_guid…
        "Newbot\t777\t8\t60\n",  # …and characters.level after the press
    )

    result = _install(server, sql, chan, level_setter=level).add(
        "Pakka", "mage", spec="fire pve", level=60
    )

    assert result.blocker == "", result.sentence
    assert level.asked == [("Newbot", 60)], "the level went through the seam it was handed"
    assert "dml_whisper Pakka Newbot talents spec fire pve" in chan.sent
    assert "dml_whisper Pakka Newbot talents autopick" not in chan.sent
    assert (result.spec, result.level_before, result.level_after) == ("fire pve", 1, 60)


def test_the_seam_refuses_a_party_that_is_not_the_one_that_was_confirmed(tmp_path: Path) -> None:
    """Round 2's must-fix, at the end that can actually enforce it.

    A panel comparing its own rows guards the wrong side of the door: between
    the two presses the group table can gain a bot nobody agreed to send away,
    and the panel would see nothing change. So the confirmed identities travel
    with the press and the SEAM re-reads: the fresh set must match exactly, or
    nothing is sent and the sentence says the party is not the one confirmed.
    Guids rather than names because a guid is what the group table has -- two
    bots can be renamed, and `characters.name` is not what `group_member` keys.
    """
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("1001\n", "Anmi\t949\t8\t1\nKobo\t950\t8\t1\n")

    result = _install(_ready_install(tmp_path), sql, chan).remove_all("Pakka", (949,))

    assert result.attempted == 0
    assert result.dismissals == ()
    assert result.blocker == result.sentence
    assert "confirmed" in result.sentence
    assert chan.sent == [], "not one uninvite may go out"


def test_the_seam_refuses_a_party_that_lost_a_bot_since_it_was_confirmed(tmp_path: Path) -> None:
    """Both directions, because a set that shrank is not the set agreed to either
    -- and a subset that "obviously" still works is how a confirmation quietly
    becomes a suggestion."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("1001\n", "Anmi\t949\t8\t1\n")

    result = _install(_ready_install(tmp_path), sql, chan).remove_all("Pakka", (949, 950))

    assert result.attempted == 0
    assert chan.sent == []


# -- T26: adding a NAMED character as a bot ---------------------------------


def _issued(master: str = "Pakka", name: str = "Nore") -> Answer:
    """The bridge's own "I ran it" reply, built from the pinned format."""
    return Answer("yes", party.BOTADD_ISSUED_FORMAT % (master, name))


def test_the_named_add_asks_the_bridge_and_reports_the_bot_the_group_table_gained() -> None:
    """The success path: the relay says it ran the command, the poll sees the row.

    `joined` is the GROUP TABLE, never the reply -- the module answers
    `.playerbots bot add` only in the master's own game window
    (`PlayerbotMgr.cpp:889-892`, measured 2026-09-10), so the wire cannot carry
    a success any more than it can carry a refusal.
    """
    chan = _Chan({"dml_botadd": _issued()})
    reads = iter([(), (party.Member("Nore", 42, 8, 60),)])
    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=lambda: next(reads),
        sleep=lambda _s: None,
    )
    assert chan.sent == ["dml_botadd Pakka Nore"]
    assert result.added is True
    assert result.joined is True
    assert result.sentence == "Nore joined the party."


@pytest.mark.parametrize(
    "reason",
    ["Pakka is not in the world", "Nore is logged in"],
)
def test_every_refusing_branch_of_the_bridge_is_recognised_and_nothing_is_polled(
    reason: str,
) -> None:
    """Both branches `dml_botadd.lua` refuses on answer with the one marker.

    Recognised by the marker and not by `answer.outcome`, for `dismiss()`'s
    reason: the bridge's hook returns false either way, so the core's error flag
    never trips and a refusal arrives as a perfectly good "yes".
    """
    said = (party.BOTADD_REFUSAL_FORMAT % ("Nore", "Pakka")) + f" ({reason})"
    chan = _Chan({"dml_botadd": Answer("yes", said)})
    polls = 0

    def members() -> tuple[party.Member, ...]:
        nonlocal polls
        polls += 1
        return ()

    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=members,
        sleep=lambda _s: None,
    )
    assert result.added is False
    assert result.joined is False
    assert polls == 1, "the ground read only -- a refused add must not poll the group table"
    # The server's own words and nothing around them (T13's rule for the same
    # shape): a refusal that fell through to the "what answered was not this
    # bridge" branch would still report a failure -- for a cause the bridge
    # never gave, over a sentence it plainly did.
    assert result.sentence == f"Nore was not added: the server says {said}"


def test_a_named_add_whose_ground_read_failed_sends_nothing() -> None:
    """T21's rule on the other side of the wire: nothing is added on a read that
    did not happen.

    The ground read is what "joined" is measured against. With it unreadable, a
    bot that was already in the party reads as one this press brought, so the
    press is refused before the channel is touched.
    """
    chan = _Chan()
    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=lambda: "could not read this character's party: docker is not running",
        sleep=lambda _s: None,
    )
    assert chan.sent == []
    assert result.added is False
    assert result.blocker
    assert "docker is not running" in result.sentence


def test_a_character_already_in_the_party_is_refused_before_anything_is_sent() -> None:
    """A step whose assertion holds before its action proves nothing (the gate
    rule, in the app's voice). A name already in the group table cannot be shown
    to have joined by joining."""
    chan = _Chan()
    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=lambda: (party.Member("Nore", 42, 8, 60),),
        sleep=lambda _s: None,
    )
    assert chan.sent == []
    assert result.blocker
    assert "already" in result.sentence


def test_an_answer_without_the_bridges_own_words_is_not_taken_as_an_add() -> None:
    """`read_probe`'s rule for this command: a "yes" that does not carry the
    script's own sentence is something else answering to that name."""
    chan = _Chan({"dml_botadd": Answer("yes", "")})
    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=lambda: (),
        sleep=lambda _s: None,
    )
    assert result.added is False
    assert result.joined is False
    assert "dml_botadd" in result.sentence


def test_a_bot_that_never_joins_says_where_the_servers_refusal_went() -> None:
    """The honest end of the poll: the module's own refusal is invisible here.

    "Failure: You are not allowed to control bot <Name>" (`:115`), the cap at
    `:134` and "player already logged in" (`:686`) all go to the master's chat
    window, so a timeout cannot be reported as "the server said no" -- and must
    not be reported as a bot that arrived either.
    """
    chan = _Chan({"dml_botadd": _issued()})
    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=lambda: (),
        tries=3,
        pause=0.5,
        sleep=lambda _s: None,
    )
    assert result.added is True
    assert result.joined is False
    assert "1.5 seconds" in result.sentence
    assert "game window" in result.sentence


def test_a_poll_that_could_never_be_read_is_not_a_bot_that_did_not_arrive() -> None:
    """T21 again, at the other end: `()` may not mean both "no row yet" and "the
    table could not be read". The last read decides what the expiry reports."""
    chan = _Chan({"dml_botadd": _issued()})
    reads = iter([(), "could not read this character's party: docker is not running"])
    result = party.add_named(
        master="Pakka",
        name="Nore",
        send=chan.send,
        members=lambda: next(reads),
        tries=1,
        sleep=lambda _s: None,
    )
    assert result.joined is False
    assert result.unreadable is True
    assert "could not be read" in result.sentence


# -- T26: the picker -------------------------------------------------------
#
# The five rules are one `if` in `PlayerbotHolder::AddPlayerBot`
# (`mod-playerbots PlayerbotMgr.cpp:101-116`, read on `yulon-ubuntu2`
# 2026-09-10 at `b949b50b`), with the online short-circuit at `:685-686` before
# any of them and the cap at `:132-135` after. Each test below owns ONE of
# them, and the mutation each catches is that rule deleted from `candidates()`.


def _char(
    name: str,
    *,
    guid: int = 100,
    account: int = 1,
    online: bool = False,
    guild_id: int = 0,
    guild: str = "",
    level: int = 60,
    klass: int = 8,
) -> party.CharacterRow:
    return party.CharacterRow(
        guid=guid,
        name=name,
        level=level,
        klass=klass,
        account=account,
        online=online,
        guild_id=guild_id,
        guild=guild,
    )


def _picked(
    *rows: party.CharacterRow,
    master: str = "Pakka",
    accounts: dict[int, str] | None = None,
    pool: frozenset[int] = frozenset(),
    linked: frozenset[int] = frozenset(),
    flags: party.AllowFlags | None = None,
) -> tuple[party.Candidate, ...]:
    """The fold, with the three allow-flags ON unless a test says otherwise.

    `all_on()` is what `playerbots.conf.dist` ships (`:155`, `:158`, `:161`) and
    it is spelled here rather than defaulted inside the app: the tests about the
    four rules are about the rules, and the tests about the flags say so."""
    out = party.candidates(
        rows,
        master=master,
        accounts={1: "OWNER", 2: "OTHER", 3: "FRIEND"} if accounts is None else accounts,
        pool=pool,
        linked=linked,
        flags=flags or party.AllowFlags.all_on(),
    )
    assert not isinstance(out, str), out
    return out


def _row_for(rows: tuple[party.Candidate, ...], name: str) -> party.Candidate:
    return next(row for row in rows if row.name == name)


def test_an_online_character_is_refused_before_any_rule_is_asked() -> None:
    """`PlayerbotMgr.cpp:685-686` short-circuits with "player already logged in"
    BEFORE the permission `if`, so a character on the master's own account is
    refused too while it is in the world.

    Mutation: drop the online arm. The row then reads as allowed on rule 1, and
    the panel offers a press the server answers only in the game window.
    """
    rows = _picked(_char("Pakka", guid=1), _char("Nore", guid=2, online=True))
    assert _row_for(rows, "Nore").allowed is False
    assert _row_for(rows, "Nore").refused_because == party.REFUSED_ONLINE


def test_the_master_is_not_in_its_own_picker() -> None:
    """`PlayerbotMgr.cpp:1314` skips the master himself, and a list offering a
    person their own character is a press that cannot mean anything.

    Mutation: keep the master's row. The list then holds a name whose press the
    module refuses where nothing here can hear it.
    """
    rows = _picked(_char("Pakka", guid=1), _char("Nore", guid=2))
    assert [row.name for row in rows] == ["Nore"]


def test_a_character_on_the_masters_own_account_is_allowed_by_rule_one() -> None:
    """Rule 1, `sameAccount` -- `AiPlayerbot.AllowAccountBots`, shipped `1`.
    This is the owner's own alts, the case T26 was filed for.

    Mutation: drop the same-account arm. The row falls through to the refusal
    and the alts the owner asked for are greyed out.
    """
    rows = _picked(_char("Pakka", guid=1, account=1), _char("Nore", guid=2, account=1))
    assert _row_for(rows, "Nore").allowed_by == party.ALLOWED_SAME_ACCOUNT


def test_a_guild_mate_on_another_account_is_allowed_by_rule_two() -> None:
    """Rule 2, `sameGuild` -- the guild the master is in right now
    (`guild->GetMember(playerGuid)`), `AllowGuildBots`, shipped `1`. It is the
    guild ID that is compared and not the guild's name.

    Mutation: drop the guild arm. A guild mate reads as refused, which is one of
    the two routes the owner's "friends/family chars" answer goes through.
    """
    rows = _picked(
        _char("Pakka", guid=1, account=1, guild_id=12, guild="Violet Dawn"),
        _char("Jordanik", guid=2, account=3, guild_id=12, guild="Violet Dawn"),
    )
    assert _row_for(rows, "Jordanik").allowed_by == party.ALLOWED_GUILD
    assert "Violet Dawn" in _row_for(rows, "Jordanik").account_or_guild


def test_a_character_in_the_addclass_pool_is_allowed_by_rule_three() -> None:
    """Rule 3, `addClassBot` -- membership of the module's addclass pool, which
    has no allow flag at all: those characters may be added by anybody. The pool
    is the offline characters of the accounts `playerbots_account_type` marks
    type 2 (`RandomPlayerbotMgr.cpp:1719-1751`), which is what the picker's
    third read asks for. 500 of them on the measured box.

    Mutation: drop the pool arm. On a stock install the picker empties out --
    that bucket is the only one the measurement found rows in.
    """
    rows = _picked(
        _char("Pakka", guid=1, account=1),
        _char("Rinmu", guid=2, account=51),
        pool=frozenset({51}),
    )
    assert _row_for(rows, "Rinmu").allowed_by == party.ALLOWED_POOL


def test_a_character_on_a_linked_account_is_allowed_by_rule_four() -> None:
    """Rule 4, `linkedAccount` -- a row in
    `acore_playerbots.playerbots_account_links`, `AllowTrustedAccountBots`,
    shipped `1`. This is the friends-and-family route, and the one the app can
    open itself (`link_account`).

    Mutation: drop the linked arm. "Link an account" then writes a row that
    changes nothing the picker shows.
    """
    rows = _picked(
        _char("Pakka", guid=1, account=1),
        _char("Sibling", guid=2, account=2),
        linked=frozenset({2}),
    )
    assert _row_for(rows, "Sibling").allowed_by == party.ALLOWED_LINK


def test_a_character_no_rule_reaches_is_refused_and_the_row_says_why() -> None:
    """The `else` of the same `if` (`:113-116`): the module answers "Failure: You
    are not allowed to control bot <Name>" -- into the master's game window,
    where nothing this app can read would ever see it. So the row is greyed here
    instead, naming all four rules it failed.

    Mutation: allow the fall-through. Every character on the server becomes a
    row a person may press, and the press times out with no reason anywhere.
    """
    rows = _picked(_char("Pakka", guid=1, account=1), _char("Stranger", guid=2, account=9))
    assert _row_for(rows, "Stranger").allowed is False
    assert _row_for(rows, "Stranger").refused_because == party.REFUSED_NO_RULE


@pytest.mark.parametrize(
    ("flags", "key", "rule"),
    [
        (party.AllowFlags(False, True, True), party.ALLOW_ACCOUNT_KEY, party.ALLOWED_SAME_ACCOUNT),
        (party.AllowFlags(True, False, True), party.ALLOW_GUILD_KEY, party.ALLOWED_GUILD),
        (party.AllowFlags(True, True, False), party.ALLOW_LINKED_KEY, party.ALLOWED_LINK),
    ],
)
def test_a_rule_whose_conf_key_is_zero_does_not_let_a_row_in(
    flags: party.AllowFlags, key: str, rule: str
) -> None:
    """Round 1's first finding. The module's arms are
    `allow<X>Bots && <the membership test>` (`PlayerbotMgr.cpp:101-116`), so an
    install that sets one to 0 refuses a character this picker used to offer --
    in the master's client alone, after this app has said the command went out.

    Mutation, one per arm: drop the gate (`if flags.account` -> `if True`). The
    row is offered again and the press ends in a refusal nothing can hear.
    """
    rows = _picked(
        _char("Pakka", guid=1, account=1, guild_id=12, guild="Violet Dawn"),
        _char("Alt", guid=2, account=1),
        _char("Mate", guid=3, account=3, guild_id=12, guild="Violet Dawn"),
        _char("Friend", guid=4, account=2),
        linked=frozenset({2}),
        flags=flags,
    )
    shut = next(row for row in rows if row.refused_because.startswith(rule))
    assert shut.allowed is False
    assert shut.refused_because == party.FLAG_OFF % (rule, key)


@pytest.mark.parametrize(
    ("flags", "key", "rule"),
    [
        (party.AllowFlags(None, True, True), party.ALLOW_ACCOUNT_KEY, party.ALLOWED_SAME_ACCOUNT),
        (party.AllowFlags(True, None, True), party.ALLOW_GUILD_KEY, party.ALLOWED_GUILD),
        (party.AllowFlags(True, True, None), party.ALLOW_LINKED_KEY, party.ALLOWED_LINK),
    ],
)
def test_a_rule_whose_conf_key_could_not_be_read_is_greyed_and_never_offered(
    flags: party.AllowFlags, key: str, rule: str
) -> None:
    """Indeterminate, which is a third answer and not a quiet yes.

    A `.dist`-only install -- which is what `yulon-ubuntu2` is -- has said
    nothing about these keys, and the compiled defaults behind them were never
    measured on this fork. Offering the row on the shipped template's value
    would be this app asserting a fact about somebody's server: the mistake
    `ALE.Enabled`'s own comment makes about ITS compiled default.

    Mutation: read `None` as on (`if flags.account is not False`). The row is
    offered on a key nobody read.
    """
    rows = _picked(
        _char("Pakka", guid=1, account=1, guild_id=12, guild="Violet Dawn"),
        _char("Alt", guid=2, account=1),
        _char("Mate", guid=3, account=3, guild_id=12, guild="Violet Dawn"),
        _char("Friend", guid=4, account=2),
        linked=frozenset({2}),
        flags=flags,
    )
    unknown = next(row for row in rows if row.refused_because.startswith(rule))
    assert unknown.allowed is False
    assert unknown.refused_because == party.FLAG_UNREADABLE % (rule, key)


def test_a_flag_that_is_off_ends_its_own_arm_and_not_the_row() -> None:
    """The four arms are OR'd (`:113`), so a gate shuts an ARM. A character on
    the master's own account with `AllowAccountBots = 0` is still addable if it
    is in the addclass pool, exactly as the module has it.

    Mutation: refuse the row at the first gated arm instead of falling through.
    Every addclass bot on an install with one flag off disappears from a list
    the server would have accepted.
    """
    rows = _picked(
        _char("Pakka", guid=1, account=1),
        _char("Alt", guid=2, account=1),
        pool=frozenset({1}),
        flags=party.AllowFlags(False, True, True),
    )
    assert _row_for(rows, "Alt").allowed_by == party.ALLOWED_POOL


def test_an_unreadable_key_beats_one_that_is_merely_off() -> None:
    """A row with one arm switched off and another whose key could not be read
    is a row nobody can say anything certain about, and the sentence names the
    key a person can actually settle."""
    rows = _picked(
        _char("Pakka", guid=1, account=1),
        _char("Alt", guid=2, account=1),
        linked=frozenset({1}),
        flags=party.AllowFlags(False, True, None),
    )
    assert party.ALLOW_LINKED_KEY in _row_for(rows, "Alt").refused_because
    assert "could not be read" in _row_for(rows, "Alt").refused_because


def test_the_three_flags_are_read_off_the_deployed_conf_and_nothing_else(
    tmp_path: Path,
) -> None:
    """The same reader `max_added_bots()` uses, on the same deployed file --
    column 0 only, the template never. A key that is absent, or spelled anything
    but 0 or 1, reads as `None`."""
    server = tmp_path / "wowserver"
    (server / "env" / "dist" / "etc" / "modules").mkdir(parents=True)
    (server / party.PLAYERBOTS_CONF).write_text(
        f"{party.ALLOW_ACCOUNT_KEY} = 1\n"
        f"{party.ALLOW_GUILD_KEY} = 0\n"
        f"# {party.ALLOW_LINKED_KEY} = 1\n"
    )
    read = party.allow_flags(server)
    assert (read.account, read.guild, read.linked) == (True, False, None)
    # A third spelling is unread, not on. The shipped file's own comments say
    # "true"/"false" beside these keys, and how `sConfigMgr.GetOption<bool>`
    # takes that word was never measured on this fork -- guessing it is the
    # difference between a row greyed and a row offered into a silent refusal.
    # The core's own grammar (`StringConvert.h:110-121` on the pinned tree): the
    # words on either side, case-insensitively, and nothing else. Round 2 read
    # `true` as unread and greyed rows the module allows (Codex, round 2).
    for spelling in ("true", "TRUE", "yes", "y", "on", "1"):
        (server / party.PLAYERBOTS_CONF).write_text(f"{party.ALLOW_ACCOUNT_KEY} = {spelling}\n")
        assert party.allow_flags(server).account is True, spelling
    for spelling in ("false", "False", "no", "n", "off", "0"):
        (server / party.PLAYERBOTS_CONF).write_text(f"{party.ALLOW_ACCOUNT_KEY} = {spelling}\n")
        assert party.allow_flags(server).account is False, spelling
    for spelling in ("maybe", "2", "", "enabled"):
        (server / party.PLAYERBOTS_CONF).write_text(f"{party.ALLOW_ACCOUNT_KEY} = {spelling}\n")
        assert party.allow_flags(server).account is None, spelling
    bare = tmp_path / "dist-only"
    (bare / "env" / "dist" / "etc" / "modules").mkdir(parents=True)
    (bare / "env" / "dist" / "etc" / "modules" / "playerbots.conf.dist").write_text(
        f"{party.ALLOW_ACCOUNT_KEY} = 1\n{party.ALLOW_GUILD_KEY} = 1\n"
    )
    assert party.allow_flags(bare) == party.AllowFlags(None, None, None)


def test_no_row_is_decided_on_a_cap_this_app_cannot_check() -> None:
    """Round 1's second finding: the module counts
    `mgr->GetPlayerbotsCount() + loadingForMaster` (`PlayerbotMgr.cpp:125-135`),
    which is state inside the world process. The group table is not that number
    in either direction -- a bot uninvited but still controlled counts for the
    module and not for the table, and another player's bots in a shared group
    count for the table and not for the module.

    So the fold cannot be handed a count at all: there is no argument for one.
    """
    taken = inspect.signature(party.candidates).parameters
    assert "added" not in taken and "max_added" not in taken


def test_the_cap_line_says_which_of_the_two_numbers_is_missing() -> None:
    """Two different absences, two different sentences -- and neither of them
    claims a row is allowed on the cap's account."""
    unread = party._cap_note("Pakka", None)
    assert party.MAX_ADDED_BOTS_KEY in unread and party.PLAYERBOTS_CONF in unread
    checked = party._cap_note("Pakka", 40)
    assert "(40) could not be checked from here" in checked
    assert "Pakka's game window" in checked


def test_a_master_that_is_not_on_this_server_is_a_sentence_and_not_an_empty_list() -> None:
    """An empty picker and a name that is not a character read identically on
    screen, which is the pair this whole feature exists to tell apart."""
    out = party.candidates(
        (_char("Nore", guid=2),),
        master="Nobody",
        accounts={},
        pool=frozenset(),
        linked=frozenset(),
        flags=party.AllowFlags.all_on(),
    )
    assert isinstance(out, str)
    assert "Nobody" in out


def test_the_pickers_three_reads_name_the_three_databases_the_measurement_found() -> None:
    """One read per database, because `DockerSql` asks one database at a time
    and the columns live in three: `characters`/`guild_member`/`guild` in
    `acore_characters`, `account` in `acore_auth`, `playerbots_account_type` and
    `playerbots_account_links` in `acore_playerbots`
    (`8.6-altbot-measure-…/README.md` §6, the column table).
    """
    chars, auth, bots = party.candidates_sql(WOTLK, "Pakka")
    assert "acore_characters.characters" in chars
    assert "acore_characters.guild_member" in chars
    assert "acore_characters.guild" in chars
    assert "acore_auth.account" in auth
    assert "acore_playerbots.playerbots_account_type" in bots
    assert "acore_playerbots.playerbots_account_links" in bots
    assert "'Pakka'" in bots, "the links read is filtered to THIS master's account"


def test_a_character_row_that_does_not_parse_is_reported_and_not_skipped() -> None:
    """`read_members`' rule: a picker silently one row short reads exactly like a
    picker that had nothing to offer."""
    out = party.read_characters("Nore\tnonsense\n")
    assert isinstance(out, str)
    assert "nonsense" in out


# -- T26: linking two accounts ---------------------------------------------
#
# The friends-and-family route the owner asked for. `.playerbots account link`
# is the in-game half (`PlayerbotMgr.cpp:1840-1885`): it SHA-256s a key set with
# `.playerbots account setKey`, and on a match writes BOTH directions into
# `acore_playerbots.playerbots_account_links` with `INSERT IGNORE`. The check
# that reads it back is one `SELECT 1` (`IsAccountLinked`, `:191-196`) --
# nothing in it cares which end wrote the row, which is why the launcher can
# write it itself (the measurement's §2, and its §10.4).


BOTH_ROWS = f"{party.WROTE_TAG}\t2\n{party.ROW_TAG}\t1\t2\n{party.ROW_TAG}\t2\t1\n"
"""What the link transaction says when this press wrote both rows: the row
count, then the two directional rows the readback found."""


class _WriteSql(_Sql):
    """The read seam of `_Sql` with the ONE write this feature makes recorded.

    A recorder and not a database: what these tests are about is which script
    would go out and what the app does with what comes back, and a test that
    needed docker would be a test of docker (the suite's own conftest guard
    refuses one anyway).

    The write is told apart from the reads by its own text rather than by a
    second method, because since round 2 it goes out through the returning
    runner: it is one script carrying `START TRANSACTION`, the `INSERT IGNORE`,
    `ROW_COUNT()` and the readback, and `wrote` is what that script answers.
    """

    def __init__(self, *answers: str, wrote: str | Exception = BOTH_ROWS) -> None:
        super().__init__(*answers)
        self.written: list[tuple[str, str]] = []
        self.wrote = wrote

    def query(self, db: str, statement: str) -> str:
        if "INSERT IGNORE" not in statement:
            return super().query(db, statement)
        self.written.append((db, statement))
        if isinstance(self.wrote, Exception):
            raise self.wrote
        return self.wrote


def _linkable(
    tmp_path: Path, *answers: str, wrote: str | Exception = BOTH_ROWS
) -> tuple[party.InstallParty, _WriteSql]:
    """An install whose world is DOWN, with the link reads scripted in order and
    the transaction's own answer scripted after them."""
    sql = _WriteSql(*answers, wrote=wrote)
    return _install(_ready_install(tmp_path), sql, None, link_writer=sql), sql


def test_linking_refuses_an_account_this_server_does_not_have(tmp_path: Path) -> None:
    """Refused on the read, by name, and nothing is written.

    The alternative is an `INSERT ... SELECT` whose `WHERE` matches nothing:
    that writes no row and reports success, which is the shape of every false
    success this feature has been fixed out of.
    """
    seam, sql = _linkable(tmp_path, "1\tOWNER", "")
    result = seam.link_account("Pakka", "NOBODY")
    assert result.linked is False
    assert sql.written == []
    assert "NOBODY" in result.sentence


def test_linking_refuses_the_masters_own_account(tmp_path: Path) -> None:
    """Rule 1 already covers it (`sameAccount`, `AllowAccountBots`), and a row
    linking an account to itself is a row that changes nothing."""
    seam, sql = _linkable(tmp_path, "1\tOWNER", "1\tOWNER")
    result = seam.link_account("Pakka", "OWNER")
    assert result.linked is False
    assert sql.written == []
    assert "own account" in result.sentence


def test_linking_refuses_a_pair_that_is_already_linked(tmp_path: Path) -> None:
    """`INSERT IGNORE` would report success over a row that was already there,
    and "linked" would then be said about a press that did nothing."""
    seam, sql = _linkable(tmp_path, "1\tOWNER", "2\tFRIEND", "1\t2\n2\t1\n")
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is False
    assert sql.written == []
    assert "already" in result.sentence


def test_the_link_write_inserts_both_directions_and_names_both_accounts(tmp_path: Path) -> None:
    """Both directions, because the module's own command writes both
    (`PlayerbotMgr.cpp:1840-1885`) and `IsAccountLinked` is asked with the two
    ids in whichever order the add happens to put them."""
    seam, sql = _linkable(tmp_path, "1\tOWNER", "2\tFRIEND", "")
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is True
    written = sql.written[0][1]
    assert "playerbots_account_links" in written
    assert "(1, 2)" in written and "(2, 1)" in written
    assert "OWNER" in result.sentence and "FRIEND" in result.sentence


def test_a_link_asked_for_over_a_running_world_is_still_written_and_says_so(
    tmp_path: Path,
) -> None:
    """The one row in this feature that is a WRITE, and it is not guarded on the
    world being down.

    The reason is measured rather than assumed: the module writes this same
    table from inside the running world through `.playerbots account link`, and
    reads it back with a fresh `SELECT 1` on every add -- there is no cached
    copy for a row written beside it to fall out of step with. The ledger row
    carries the argument in full.
    """
    sql = _WriteSql("1\tOWNER", "2\tFRIEND", "")
    seam = _install(_ready_install(tmp_path), sql, None, running=True, link_writer=sql)
    assert seam.link_account("Pakka", "FRIEND").linked is True
    assert len(sql.written) == 1


def test_an_install_with_no_write_route_says_so_rather_than_failing_quietly(
    tmp_path: Path,
) -> None:
    """`link_writer` is a seam of its own and not the read seam widened:
    `dbreads.SqlReader` deliberately cannot reach `run_statement`, and this
    feature's one write arrives through a type that says exactly what it is."""
    sql = _WriteSql("1\tOWNER", "2\tFRIEND", "")
    plain = _install(_ready_install(tmp_path), sql, None)
    result = plain.link_account("Pakka", "FRIEND")
    assert result.linked is False
    assert result.blocker
    assert sql.asked == [], "an install with no write route reads nothing either"


def test_the_confirmation_names_both_accounts_and_what_it_would_mean(tmp_path: Path) -> None:
    """The first press asks, the second writes. What it asks has to name both
    accounts and the consequence, because the consequence is the point: from
    then on the module treats EVERY character of that account as one this master
    may add."""
    seam, sql = _linkable(tmp_path, "1\tOWNER", "2\tFRIEND", "")
    plan = seam.link_plan("Pakka", "FRIEND")
    assert plan.linked is False
    assert plan.blocker == ""
    assert "OWNER" in plan.sentence and "FRIEND" in plan.sentence
    assert "every character" in plan.sentence
    assert sql.written == [], "a confirmation writes nothing"


def test_a_link_name_that_is_not_an_account_name_is_refused_before_any_read(
    tmp_path: Path,
) -> None:
    """It goes into a quoted SQL literal, so the check is a boundary and not a
    courtesy -- `_check_name`'s reason, on the field a person types into."""
    seam, sql = _linkable(tmp_path)
    result = seam.link_account("Pakka", "O'BRIEN; DROP")
    assert result.linked is False
    assert sql.asked == []
    assert sql.written == []


def test_the_seam_folds_its_four_reads_into_the_rows_the_picker_shows(tmp_path: Path) -> None:
    """The picker through the object the panel actually holds, not the fold alone.

    Four reads in this order: the master's online guid, his party (which is also
    the added-bot count), then the picker's three. T5's round-2 finding is why
    this exists at all -- every rule above is tested against `candidates()`
    directly, and a seam that handed it the wrong reads would pass every one of
    them.
    """
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql(
        "1001\n",  # Pakka is online, guid 1001
        "Bottom\t777\t8\t1\n",  # one bot in the party already
        "1001\tPakka\t6\t2\t102\t1\t0\t\n2\tNore\t60\t8\t102\t0\t0\t\n",
        "102\tOWNER\n",
        "",
    )
    server = _with_playerbots_conf(_ready_install(tmp_path), account=1, guild=1, linked=1)
    picked = _install(server, sql, chan).candidates("Pakka")
    assert picked.problem == ""
    assert picked.added == 1
    assert [row.name for row in picked.rows] == ["Nore"]
    assert picked.rows[0].allowed_by == party.ALLOWED_SAME_ACCOUNT
    assert picked.rows[0].account_or_guild == "OWNER"
    assert picked.note, "the cap is reported beside the rows, never applied to them"


def _with_playerbots_conf(
    server: Path, *, account: int, guild: int, linked: int, cap: int | None = None
) -> Path:
    """A DEPLOYED `playerbots.conf` with the three allow-flags in it.

    `_ready_install` deliberately has none -- which is what `yulon-ubuntu2` is,
    a box with only the shipped template -- so every seam test that wants the
    rules to decide has to say what this install's conf holds."""
    text = (
        f"{party.ALLOW_ACCOUNT_KEY} = {account}\n"
        f"{party.ALLOW_GUILD_KEY} = {guild}\n"
        f"{party.ALLOW_LINKED_KEY} = {linked}\n"
    )
    if cap is not None:
        text += f"{party.MAX_ADDED_BOTS_KEY} = {cap}\n"
    (server / party.PLAYERBOTS_CONF).write_text(text)
    return server


def test_an_install_with_no_deployed_conf_offers_nothing_on_the_three_gated_rules(
    tmp_path: Path,
) -> None:
    """The measured box's own shape: `playerbots.conf.dist` and no
    `playerbots.conf`. Nothing has said whether the three rules are on, so the
    rows they would have offered are greyed with the key that could not be read
    -- never offered, which is the lead's rejection in one assertion.

    Mutation: default an unread flag to on. Every alt on that box is offered
    again on a value nobody read.
    """
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql(
        "1001\n",
        "",
        "1001\tPakka\t6\t2\t102\t1\t0\t\n2\tNore\t60\t8\t102\t0\t0\t\n",
        "102\tOWNER\n",
        "",
    )
    picked = _install(_ready_install(tmp_path), sql, chan).candidates("Pakka")
    assert [row.allowed for row in picked.rows] == [False]
    assert party.ALLOW_ACCOUNT_KEY in picked.rows[0].refused_because
    assert "could not be read" in picked.rows[0].refused_because


def test_a_party_already_holding_the_caps_worth_of_bots_still_offers_its_rows(
    tmp_path: Path,
) -> None:
    """Round 1's second finding, at the seam that used to do the arithmetic.

    Two bots in the group table and `MaxAddedBots = 2`: the old code refused
    every row on that comparison. The module's count is not this number -- it
    counts the bots it controls, plus the ones loading for that account
    (`PlayerbotMgr.cpp:125-135`) -- so the rows stay offered and the note says
    the cap could not be checked from here.

    Mutation: put the group-table count back as the decision (`offered = () if
    cap is not None and len(rows) >= cap else …`). The picker empties itself on
    a number that means something else.
    """
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql(
        "1001\n",
        "Bottom\t777\t8\t1\nOther\t778\t8\t1\n",  # two bots in the party
        "1001\tPakka\t6\t2\t102\t1\t0\t\n2\tNore\t60\t8\t102\t0\t0\t\n",
        "102\tOWNER\n",
        "",
    )
    server = _with_playerbots_conf(_ready_install(tmp_path), account=1, guild=1, linked=1, cap=2)
    picked = _install(server, sql, chan).candidates("Pakka")
    assert picked.added == 2
    assert picked.max_added == 2
    assert [row.name for row in picked.rows if row.allowed] == ["Nore"]
    assert "(2) could not be checked from here" in picked.note
    assert "Pakka's game window" in picked.note


def test_the_seam_reports_a_picker_read_that_failed_instead_of_a_short_list(
    tmp_path: Path,
) -> None:
    """A picker missing the rows a failed read would have held offers less than
    the server does, with nothing on screen saying so."""

    class _Broken(_Sql):
        def query(self, db: str, statement: str) -> str:
            if "guild_member" in statement:
                raise RuntimeError("docker is not running")
            return super().query(db, statement)

    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    picked = _install(
        _ready_install(tmp_path), _Broken("1001\n", "Bottom\t777\t8\t1\n"), chan
    ).candidates("Pakka")
    assert picked.rows == ()
    assert "docker is not running" in picked.problem


def test_the_seam_sends_the_named_add_and_polls_the_group_table(tmp_path: Path) -> None:
    """The whole press through the seam: preconditions, the master's guid, the
    whisper, and the group table read back."""
    chan = _Chan(
        {
            "dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping"),
            "dml_botadd": _issued("Pakka", "Nore"),
        }
    )
    sql = _Sql(
        "1001\n",  # the precondition read: Pakka is online
        "1001\n",  # the ground read's own guid lookup
        "",  # the party is empty before the press
        "1001\n",
        "Nore\t2\t8\t60\n",  # and holds Nore after it
    )
    result = _install(_ready_install(tmp_path), sql, chan).add_named("Pakka", "Nore")
    assert "dml_botadd Pakka Nore" in chan.sent
    assert result.joined is True
    assert result.sentence == "Nore joined the party."


def test_the_seam_refuses_a_named_add_for_a_master_who_is_not_logged_in(tmp_path: Path) -> None:
    """The same refusal `add()` makes, and for the same measured reason: the
    module resolves the master from a live session and there is no other
    spelling of it."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    result = _install(_ready_install(tmp_path), _Sql(""), chan).add_named("Pakka", "Nore")
    assert result.added is False
    assert result.blocker
    assert chan.sent == ["dml_bridge_ping"]


# -- T26 round 2: the link write is transactional and read back --------------


def test_the_link_write_is_one_transaction_that_reads_itself_back(tmp_path: Path) -> None:
    """Round 2, must-fix 3, on the wire: one script, in this order.

    One script because one `DockerSql` call is one `docker exec` is one mysql
    session, and a transaction spread over two of them is not a transaction --
    which is also why `ROW_COUNT()`, session state, can only be read here.

    Mutation: send the `INSERT IGNORE` on its own again (the old
    check-then-write). The order assertions fail, and with them the only
    evidence the app has that the rows are there.
    """
    seam, sql = _linkable(tmp_path, "1\tOWNER", "2\tFRIEND", "")
    assert seam.link_account("Pakka", "FRIEND").linked is True
    script = sql.written[0][1]
    order = [
        script.index("START TRANSACTION"),
        script.index("INSERT IGNORE"),
        script.index("ROW_COUNT()"),
        script.index("SELECT 'row'"),
        script.index("COMMIT"),
    ]
    assert order == sorted(order), script
    assert sql.written[0][0] == "playerbots"


def test_a_concurrent_insert_is_not_reported_as_this_press_writing_the_rows(
    tmp_path: Path,
) -> None:
    """The pre-read saw neither row, the insert wrote neither, and both are
    there: somebody else's press landed in between.

    `INSERT IGNORE` cannot say no, so before round 2 this press said "are
    linked: this press wrote both rows" over a write that did nothing.
    `ROW_COUNT()` is what tells them apart.

    Mutation: report the row count as 2 whatever came back. The sentence claims
    a write this press did not make.
    """
    seam, sql = _linkable(
        tmp_path,
        "1\tOWNER",
        "2\tFRIEND",
        "",  # the pre-read: neither row
        wrote=f"{party.WROTE_TAG}\t0\n{party.ROW_TAG}\t1\t2\n{party.ROW_TAG}\t2\t1\n",
    )
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is True
    assert "were already linked by the time this press ran" in result.sentence
    assert "this press wrote both rows" not in result.sentence


def test_a_half_linked_pair_is_repaired_and_the_report_says_which_row_it_wrote(
    tmp_path: Path,
) -> None:
    """One row in the table is a third state, not "already linked".

    The module's own command writes both directions, so a table holding one is a
    link `IsAccountLinked` answers for one way round and not the other. Before
    round 2 the pre-read refused it as already linked and the pair stayed half
    linked for good.

    Mutation: refuse on any row found (`if forward or back`). The repair never
    happens and the refusal names a link that only half exists.
    """
    seam, sql = _linkable(
        tmp_path,
        "1\tOWNER",
        "2\tFRIEND",
        "1\t2\n",  # the pre-read: the master's direction only
        wrote=f"{party.WROTE_TAG}\t1\n{party.ROW_TAG}\t1\t2\n{party.ROW_TAG}\t2\t1\n",
    )
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is True
    assert "one way only" in result.sentence
    assert "wrote the missing row" in result.sentence
    assert len(sql.written) == 1


def test_the_confirmation_says_a_half_linked_pair_would_be_repaired(tmp_path: Path) -> None:
    """The first press has to name what the second one will do, and repairing a
    half link is not the same thing as making one."""
    seam, _ = _linkable(tmp_path, "1\tOWNER", "2\tFRIEND", "2\t1\n")
    plan = seam.link_plan("Pakka", "FRIEND")
    assert plan.blocker == ""
    assert "one way only" in plan.sentence


def test_a_write_that_could_not_be_read_back_is_never_reported_as_linked(
    tmp_path: Path,
) -> None:
    """T21's rule on the write side: nothing is claimed on a read that did not
    happen. The transaction may have committed -- the sentence says what is
    known, which is that this press could not show both rows.

    Mutation: return `linked=True` when the write raised nothing. A database
    that answered nothing at all reads as a link the module will not honour.
    """
    seam, sql = _linkable(
        tmp_path, "1\tOWNER", "2\tFRIEND", "", wrote=RuntimeError("docker is not running")
    )
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is False
    assert "NOT linked" in result.sentence
    assert "docker is not running" in result.sentence
    assert len(sql.written) == 1, "the script went out; what failed is knowing what it did"


def test_a_readback_that_finds_one_row_is_not_a_link(tmp_path: Path) -> None:
    """`linked` is both rows read back inside the transaction, never the insert
    being sent. One row is what the module honours in one direction only."""
    seam, _ = _linkable(
        tmp_path,
        "1\tOWNER",
        "2\tFRIEND",
        "",
        wrote=f"{party.WROTE_TAG}\t1\n{party.ROW_TAG}\t1\t2\n",
    )
    result = seam.link_account("Pakka", "FRIEND")
    assert result.linked is False
    assert "found 1 of the two rows" in result.sentence


def test_a_link_write_that_answers_something_else_is_reported_and_not_parsed_past(
    tmp_path: Path,
) -> None:
    """`read_members`' rule on the one output that is this write's only evidence:
    a line nobody understands is reported, never dropped."""
    read = party.read_link_write("wrote\t2\nsomething else\n")
    assert isinstance(read, str)
    assert "something else" in read
    assert isinstance(party.read_link_write("row\t1\t2\n"), str), "no row count is no answer"
    seam, _ = _linkable(tmp_path, "1\tOWNER", "2\tFRIEND", "", wrote="what?\n")
    assert seam.link_account("Pakka", "FRIEND").linked is False


# -- T26 live half: the named route reads the party WITHOUT the bot marker ----


def test_the_named_routes_party_read_does_not_filter_by_the_bot_marker() -> None:
    """Measured on `yulon-ubuntu2`, 2026-09-11 02:04:38, and it is the whole
    defect the live half found.

    `group_rows_sql` keeps only rows the BOT MARKER recognises, which is right
    for `add_bot`: `addclass` logs in a character out of the module's own pool,
    on an account whose username begins with `RandomBotAccountPrefix`. This
    route's characters are the opposite by definition -- somebody's own alt, a
    guild mate's character, a friend's -- and every one of them is on a PERSON's
    account. Pressed live, `Tsixalt` logged in and stood in the party (the
    module's own `add: Tsixalt - ok` / `Tsixalt joins the party.` in the
    master's client, and `group_member` holding group 2 with both guids), and
    this app said "no bot by that name joined the party within 6 seconds"
    because the poll could not see it. So the named route reads the party with
    the marker's clause left out, and drops the master by guid instead -- which
    is the only row the marker was keeping out on this path.
    """
    sql = party.party_rows_sql(WOTLK, master_guid=1001)
    assert dbreads.bot_clause(WOTLK, RNDBOT) not in sql
    assert "RNDBOT" not in sql.upper()
    assert "memberGuid=1001" in sql.replace(" ", "")
    assert "memberGuid<>1001" in sql.replace(" ", "")
    assert "ORDER BY" in sql.upper()


def test_the_named_routes_party_read_is_anchored_on_the_masters_own_group() -> None:
    """The same sub-select `group_rows_sql` is anchored on: `group_member.guid`
    is the GROUP and `memberGuid` is the member, so without it this reads every
    group on the server."""
    sql = party.party_rows_sql(WOTLK, master_guid=1001)
    assert "group_member" in sql
    # Three, and each of them is a different job: the JOIN onto `characters`,
    # the sub-select that turns the master's guid into his GROUP's id, and the
    # one clause this read has instead of the bot marker's.
    assert sql.count("memberGuid") == 3
    assert "LIMIT 1" in sql
    assert "c.guid = gm.memberGuid" in sql


def test_the_seam_polls_the_named_add_with_the_unfiltered_party_read(tmp_path: Path) -> None:
    """The press, end to end, with the poll's own statement read back out of the
    seam: a character on an account the bot marker does NOT recognise is the
    case this route is for, and the statement the poll sends must not carry the
    marker's clause."""
    chan = _Chan(
        {
            "dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping"),
            "dml_botadd": _issued("Pakka", "Nore"),
        }
    )
    sql = _Sql(
        "1001\n",  # the precondition read: Pakka is online
        "1001\n",  # the ground read's own guid lookup
        "",  # the party is empty before the press
        "1001\n",
        "Nore\t2\t8\t60\n",  # and holds Nore after it
    )
    result = _install(_ready_install(tmp_path), sql, chan).add_named("Pakka", "Nore")
    assert result.joined is True
    party_reads = [asked for asked in sql.asked if "group_member" in asked]
    assert party_reads, "the named add never read the group table"
    for asked in party_reads:
        assert "RNDBOT" not in asked.upper()
        assert "memberGuid <> 1001" in asked


def test_the_bot_routes_party_read_still_filters_by_the_bot_marker(tmp_path: Path) -> None:
    """The other half of the same change, so the fix cannot be "drop the clause
    everywhere": `members()` is what the panel draws and what `remove_all`
    confirms, and a party list that offered to dismiss a real person's character
    would be a worse defect than the one being fixed."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("1001\n", "Bottom\t777\t8\t1\n")
    seam = _install(_ready_install(tmp_path), sql, chan)
    assert seam.members("Pakka") == (party.Member("Bottom", 777, 8, 1),)
    assert "RNDBOT" in sql.asked[1].upper()


# -- T26 round 2: members() counts the altbots this app added ----------------
#
# Measured on `yulon-ubuntu2` 2026-09-11 03:04 (`22-discriminator.log`): the
# server records a `bot add` NOWHERE this app can read. All thirty tables in
# `acore_playerbots` hold the same row counts before and after an add,
# `playerbots_random_bots` included; the only `PlayerbotsDatabase` writes in
# `PlayerbotMgr.cpp` are the four account-link statements; `.playerbots bot
# list` is `Console::No` and answers the SOAP caller with the USAGE list of its
# three `Console::Yes` siblings both before and after; and in
# `acore_characters` the only column that moves for the added character is
# `online`, which is what a person logging in moves too. `account.online` is 1
# for `RNDBOT0` as well, so it does not separate a bot session from a real one
# either. So the app keeps its own record.


def test_the_group_read_counts_a_name_this_app_remembers_adding() -> None:
    """The union. `dbreads.bot_clause` answers for the module's own pool; this
    route's characters are on people's accounts and no marker will ever match
    one, so the names this app added through `add_named` are asked for by
    name."""
    sql = party.group_rows_sql(WOTLK, RNDBOT, master_guid=1001, also=("Tsixalt", "Tsixmate"))
    assert dbreads.bot_clause(WOTLK, RNDBOT) in sql
    assert "c.name IN ('Tsixalt', 'Tsixmate')" in sql


def test_the_group_read_never_counts_the_master_himself() -> None:
    """The master has a `group_member` row of his own, and the union arm would
    return him the moment his own name were remembered. He is dropped by guid,
    which is the one thing the marker's clause was doing on this path."""
    sql = party.group_rows_sql(WOTLK, RNDBOT, master_guid=1001, also=("Tsixmaster",))
    assert "memberGuid<>1001" in sql.replace(" ", "")


def test_the_group_read_with_nothing_remembered_is_the_marker_clause_alone() -> None:
    """A human in the party is neither a marker bot nor a name this app added,
    so nothing about the union can let one in: with nothing remembered the
    query is what it always was."""
    sql = party.group_rows_sql(WOTLK, RNDBOT, master_guid=1001)
    assert "c.name IN" not in sql
    assert dbreads.bot_clause(WOTLK, RNDBOT) in sql


def test_a_remembered_name_that_is_not_a_character_name_is_refused_not_quoted() -> None:
    """`dbreads._SAFE_PREFIX`'s rule, on the one value here that reaches a
    quoted literal from a stored file rather than from a press."""
    sql = party.group_rows_sql(WOTLK, RNDBOT, master_guid=1001, also=("Bad'; DROP --",))
    assert "DROP" not in sql
    assert "c.name IN" not in sql


def test_the_seam_remembers_a_character_it_added_and_then_counts_it(tmp_path: Path) -> None:
    """The whole of must-fix 2, end to end: after a successful `add_named` the
    party the panel draws holds the character, where round 1's panel printed
    "X joined the party." and "This character's party has no bots in it yet."
    under it."""
    chan = _Chan(
        {
            "dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping"),
            "dml_botadd": _issued("Pakka", "Nore"),
        }
    )
    sql = _Sql(
        "1001\n",  # precondition: Pakka is online
        "1001\n",  # the ground read's guid lookup
        "",  # the party is empty before the press
        "1001\n",
        "Nore\t2\t8\t60\n",  # and holds Nore after it
    )
    seam = _install(_ready_install(tmp_path), sql, chan, altbots=party.AltbotMemory(None))
    assert seam.add_named("Pakka", "Nore").joined is True
    assert seam.remembered_altbots("Pakka") == ("Nore",)
    sql.answers = ["1001\n", "Nore\t2\t8\t60\n"]
    assert seam.members("Pakka") == (party.Member("Nore", 2, 8, 60),)
    assert "'Nore'" in sql.asked[-1]


def test_a_remembered_character_that_left_the_party_is_forgotten(tmp_path: Path) -> None:
    """The record is dropped when the character leaves the party: it is pruned
    against the group table on every successful read, so a name nobody removed
    through the app does not sit in the file for ever."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    memory = party.AltbotMemory(None)
    memory.remember("Pakka", "Nore")
    seam = _install(_ready_install(tmp_path), _Sql("1001\n", ""), chan, altbots=memory)
    assert seam.members("Pakka") == ()
    assert memory.names("Pakka") == ()


def test_a_group_read_that_failed_forgets_nothing(tmp_path: Path) -> None:
    """A read that could not be done says nothing about who is in the party,
    and pruning on it would throw the record away on a database hiccup."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})

    class _BrokenGroupRead(_Sql):
        def query(self, db: str, statement: str) -> str:
            if "group_member" in statement:
                raise RuntimeError("docker is not running")
            return super().query(db, statement)

    memory = party.AltbotMemory(None)
    memory.remember("Pakka", "Nore")
    seam = _install(_ready_install(tmp_path), _BrokenGroupRead("1001\n"), chan, altbots=memory)
    assert isinstance(seam.members("Pakka"), str)
    assert memory.names("Pakka") == ("Nore",)


def test_a_party_row_that_did_not_parse_forgets_nothing_either(tmp_path: Path) -> None:
    """The other shape of a read that did not answer, and the one that reaches
    the prune's guard: `read_members` REPORTS an unparsable row rather than
    skipping it, so `members()` returns a sentence with the rows unread. Pruning
    on that would drop the record because of one malformed line."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    memory = party.AltbotMemory(None)
    memory.remember("Pakka", "Nore")
    sql = _Sql("1001\n", "Nore\tnot-a-guid\n")
    seam = _install(_ready_install(tmp_path), sql, chan, altbots=memory)
    answer = seam.members("Pakka")
    assert isinstance(answer, str)
    assert "not a group member" in answer
    assert memory.names("Pakka") == ("Nore",)


def test_the_altbot_record_survives_a_new_object_over_the_same_file(tmp_path: Path) -> None:
    """It is a per-install record on disk and not a process's memory: the panel
    is rebuilt on every tab switch and the app is restarted between sessions."""
    path = party.altbot_store_path(tmp_path)
    first = party.AltbotMemory(path, "wow-wotlk-243c46e3")
    first.remember("Pakka", "Nore")
    assert party.AltbotMemory(path, "wow-wotlk-243c46e3").names("Pakka") == ("Nore",)
    assert party.AltbotMemory(path, "another-install").names("Pakka") == ()


def test_an_unreadable_altbot_store_is_an_empty_record_and_not_a_crash(tmp_path: Path) -> None:
    """The panel must draw. A store that cannot be parsed is the same as one
    that has nothing in it."""
    path = party.altbot_store_path(tmp_path)
    path.write_text("{not json")
    memory = party.AltbotMemory(path, "wow-wotlk-243c46e3")
    assert memory.names("Pakka") == ()
    memory.remember("Pakka", "Nore")
    assert memory.names("Pakka") == ("Nore",)


# -- T26 round 2: the dismissal readback is not vacuous ----------------------


def test_the_seam_refuses_to_dismiss_a_character_that_is_not_in_the_party(
    tmp_path: Path,
) -> None:
    """Must-fix 3. `dismiss()` polls for a name to VANISH, so a name that was
    never in the read it polls comes back `removed=True` on the first read
    having had nothing to observe -- the false-success shape T21 deletes. The
    seam reads the party first and sends nothing when the character is not in
    it, exactly as `add_named` refuses a character that already is."""
    chan = _Chan({"dml_bridge_ping": Answer("yes", "DML-BRIDGE-READY dml_bridge_ping")})
    sql = _Sql("1001\n", "")  # online, and an empty party
    result = _install(_ready_install(tmp_path), sql, chan).remove("Pakka", "Nore")
    assert result.removed is False
    assert "not in Pakka's party" in result.sentence
    assert chan.sent == [], "nothing may be sent for a party nobody is in"


def test_the_seam_dismisses_a_character_the_party_read_does_hold(tmp_path: Path) -> None:
    """The other side of the same read: a character the group table holds is
    uninvited and the readback watches it go."""
    chan = _Chan()
    sql = _Sql(
        "1001\n",  # the ground read's guid lookup
        "Nore\t2\t8\t60\n",  # the party holds Nore
        "1001\n",
        "",  # and does not, after the uninvite
    )
    memory = party.AltbotMemory(None)
    memory.remember("Pakka", "Nore")
    seam = _install(_ready_install(tmp_path), sql, chan, altbots=memory)
    result = seam.remove("Pakka", "Nore")
    assert result.removed is True
    assert "dml_uninvite Pakka Nore" in chan.sent
    assert memory.names("Pakka") == (), "a dismissed character is forgotten"
