"""T732: mod-ale compiled against a core that names the bot-session check differently.

azerothcore/mod-ale 5472013 (#408, 2026-10-08) changed `Player:IsBot()`'s body from
`player->GetSession()->IsBot()` to `player->GetSession()->IsHeadless()`
(`src/LuaEngine/methods/PlayerMethods.h:5205` at 84b85cc). The playerbots core
renamed that `WorldSession` method in f19a1879 (v0.9.15's pin); every core before it
(7f12e89e = v0.9.13/v0.9.14, 413bea61 = yulon-win11's server) declares only
`IsBot()`. Yu'lon never moves a server's core by itself, so a WotLK server made by
Yu'lon 0.9.14 or older failed every Rebuild once mod-ale was installed on it:
"no member named 'IsHeadless' in 'WorldSession'", after half an hour.

`yulon/ale_playerbots.py` bridges the name for the compile only, in either
direction, read from the core's `WorldSession.h` and mod-ale's sources; these tests
pin what it rewrites (only `GetSession()->IsHeadless(` / `GetSession()->IsBot(`),
what it leaves alone, and that the file goes back byte for byte.

The excerpts are the measured bytes: `src/server/game/Server/WorldSession.h` lines
427 and 1230-1233 at core 413bea61, lines 429 and 1234 at 2a2211cd; mod-ale's
`PlayerMethods.h` lines 5197-5211 at 84b85cc and at bd74eae (before #408).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder
from tests.test_ale_playerbots_bridge import (
    ALE_CAUGHT_UP,
    BINDING,
    HEADER,
    PRE_RENAME_HEADER,
)
from yulon import ale_playerbots, docker, resources
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

SESSION_HEADER = "src/server/game/Server/WorldSession.h"
PLAYER_METHODS = "modules/mod-ale/src/LuaEngine/methods/PlayerMethods.h"
FUNCTIONS = "modules/mod-ale/src/LuaEngine/LuaFunctions.cpp"

OLD_CORE_SESSION = (
    "class WorldSessionFilter : public PacketFilter\n"
    "{\n"
    "};\n"
    "\n"
    "class WorldSession\n"
    "{\n"
    "public:\n"
    "    void SetPacketLogging(bool state);\n"
    "\n"
    "    LockedQueue<WorldPacket*>& GetPacketQueue();\n"
    "\n"
    "    [[nodiscard]] bool IsBot() const\n"
    "    {\n"
    "        return _isBot;\n"
    "    }\n"
    "\n"
    "private:\n"
    "    bool _isBot;\n"
    "};\n"
)
"""Core 413bea61 and 7f12e89e: `IsBot()` only."""

NEW_CORE_SESSION = (
    "class WorldSessionFilter : public PacketFilter\n"
    "{\n"
    "};\n"
    "\n"
    "class WorldSession\n"
    "{\n"
    "public:\n"
    "    // before AzerothCore #27533: [[nodiscard]] bool IsBot() const\n"
    "    [[nodiscard]] bool IsHeadless() const { return _headless; }\n"
    "\n"
    "private:\n"
    "    bool const _headless;\n"
    "};\n"
)
"""Core f19a1879 and 2a2211cd: `IsHeadless()` only.

The comment is not upstream's: it proves a name that survives only in a comment does
not count as declared.
"""

_ALE_PLAYER_IS_BOT = (
    "    /**\n"
    "     * Returns `true` if the [Player] is a Playerbot/RNDBot, `false` otherwise.\n"
    "     *\n"
    "     * @return bool isBot\n"
    "     */\n"
    "    int IsBot(lua_State* L, Player* player)\n"
    "    {\n"
    "    #if defined(MOD_PLAYERBOTS)\n"
    "        ALE::Push(L, player->GetSession()->{name}());\n"
    "    #else\n"
    "        (void)player;\n"
    "        ALE::Push(L, false);\n"
    "    #endif\n"
    "        return 1;\n"
    "    }\n"
    "    int IsHeadlessSession(lua_State* L, Player* player)\n"
    "    {\n"
    "        ALE::Push(L, other->IsHeadless());\n"
    "        return 1;\n"
    "    }\n"
)
"""mod-ale's `Player:IsBot()`. Its own function is called `IsBot` too, and must stay so;
the last function is not upstream's: a call on anything but `GetSession()` is left alone."""

ALE_NEW = _ALE_PLAYER_IS_BOT.replace("{name}", "IsHeadless")
"""mod-ale 5472013 (#408) and later, 84b85cc today."""

ALE_OLD = _ALE_PLAYER_IS_BOT.replace("{name}", "IsBot")
"""mod-ale before #408 (bd74eae, cead0cb's parent's parent): what an old clone still has."""

FUNCTIONS_TEXT = '    { "IsBot", &LuaPlayer::IsBot },\n'
"""LuaFunctions.cpp:736 at 84b85cc: the Lua name, never a session call."""


def lay(server_dir: Path, session: str | None, methods: str | None) -> None:
    """A server folder with the core header and mod-ale's PlayerMethods.h as given."""
    if session is not None:
        path = server_dir / SESSION_HEADER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(session.encode("utf-8"))
    if methods is not None:
        path = server_dir / PLAYER_METHODS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(methods.encode("utf-8"))
        (server_dir / FUNCTIONS).write_bytes(FUNCTIONS_TEXT.encode("utf-8"))


def methods_bytes(server_dir: Path) -> bytes:
    return (server_dir / PLAYER_METHODS).read_bytes()


def files(server_dir: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in server_dir.rglob("*") if p.is_file()}


# -- what is renamed ---------------------------------------------------------


def test_a_new_mod_ale_on_an_old_core_calls_the_cores_is_bot() -> None:
    assert ale_playerbots.session_renames_for(OLD_CORE_SESSION, ALE_NEW) == {"IsHeadless": "IsBot"}


def test_an_old_mod_ale_on_a_new_core_calls_the_cores_is_headless() -> None:
    assert ale_playerbots.session_renames_for(NEW_CORE_SESSION, ALE_OLD) == {"IsBot": "IsHeadless"}


@pytest.mark.parametrize(
    ("session", "source"),
    [
        (NEW_CORE_SESSION, ALE_NEW),
        (OLD_CORE_SESSION, ALE_OLD),
        (OLD_CORE_SESSION + "bool IsHeadless() const;\n", ALE_NEW),
    ],
    ids=["v0916-pins", "old-core-old-ale", "core-declares-both"],
)
def test_nothing_is_renamed_where_the_core_declares_what_mod_ale_calls(
    session: str, source: str
) -> None:
    assert ale_playerbots.session_renames_for(session, source) == {}


def test_a_core_with_neither_name_is_left_for_the_compiler() -> None:
    """Plain AzerothCore before #27533 has no bot session at all: nothing to bridge to."""
    plain = OLD_CORE_SESSION.replace("bool IsBot() const", "bool IsSomethingElse() const")
    assert ale_playerbots.session_renames_for(plain, ALE_NEW) == {}


def test_a_name_only_a_comment_carries_is_not_declared() -> None:
    """`// ... bool IsBot() const` in the new header must not make `IsBot` look declared."""
    assert ale_playerbots.session_renames_for(NEW_CORE_SESSION, ALE_OLD) == {"IsBot": "IsHeadless"}


def test_a_header_that_is_not_the_session_class_renames_nothing() -> None:
    other = OLD_CORE_SESSION.replace("class WorldSession\n", "class SomethingElse\n")
    assert ale_playerbots.session_renames_for(other, ALE_NEW) == {}


# -- the bridge on disk ------------------------------------------------------


def test_the_bridge_rewrites_only_the_session_call_and_says_so(tmp_path: Path) -> None:
    lay(tmp_path, OLD_CORE_SESSION, ALE_NEW)
    functions = (tmp_path / FUNCTIONS).read_bytes()

    said = ale_playerbots.bridge(tmp_path)

    expected = ALE_NEW.replace("GetSession()->IsHeadless()", "GetSession()->IsBot()")
    assert methods_bytes(tmp_path) == expected.encode("utf-8")
    assert "int IsBot(lua_State* L" in expected and "other->IsHeadless()" in expected
    assert (tmp_path / FUNCTIONS).read_bytes() == functions
    joined = " ".join(said)
    assert "IsHeadless -> IsBot" in joined and "PlayerMethods.h" in joined, said
    record = json.loads((tmp_path / ale_playerbots.RECORD).read_text(encoding="utf-8"))
    assert record["files"][PLAYER_METHODS]["original"] == ALE_NEW


def test_putting_back_restores_mod_ales_bytes_and_removes_the_record(tmp_path: Path) -> None:
    lay(tmp_path, OLD_CORE_SESSION, ALE_NEW)
    ale_playerbots.bridge(tmp_path)

    said = ale_playerbots.put_back(tmp_path)

    assert methods_bytes(tmp_path) == ALE_NEW.encode("utf-8")
    assert not (tmp_path / ale_playerbots.RECORD).exists()
    assert any("PlayerMethods.h" in line for line in said), said


def test_the_reverse_bridge_round_trips_too(tmp_path: Path) -> None:
    lay(tmp_path, NEW_CORE_SESSION, ALE_OLD)

    ale_playerbots.bridge(tmp_path)
    assert methods_bytes(tmp_path) == ALE_NEW.encode("utf-8")
    ale_playerbots.put_back(tmp_path)

    assert methods_bytes(tmp_path) == ALE_OLD.encode("utf-8")
    assert not (tmp_path / ale_playerbots.RECORD).exists()


@pytest.mark.parametrize(
    ("session", "methods"),
    [(NEW_CORE_SESSION, ALE_NEW), (OLD_CORE_SESSION, ALE_OLD), (None, ALE_NEW)],
    ids=["v0916-pins", "old-core-old-ale", "no-core-header"],
)
def test_the_bridge_is_a_no_op_where_core_and_mod_ale_agree(
    tmp_path: Path, session: str | None, methods: str | None
) -> None:
    lay(tmp_path, session, methods)
    before = files(tmp_path)

    assert ale_playerbots.bridge(tmp_path) == []
    assert ale_playerbots.put_back(tmp_path) == []
    assert files(tmp_path) == before


def test_a_linked_core_header_is_no_evidence(tmp_path: Path) -> None:
    """The header is read only where it is: a link to some other file decides nothing."""
    outside = tmp_path / "elsewhere.h"
    outside.write_text(OLD_CORE_SESSION, encoding="utf-8")
    server_dir = tmp_path / "server"
    lay(server_dir, None, ALE_NEW)
    (server_dir / SESSION_HEADER).parent.mkdir(parents=True)
    (server_dir / SESSION_HEADER).symlink_to(outside)

    assert ale_playerbots.bridge(server_dir) == []
    assert methods_bytes(server_dir) == ALE_NEW.encode("utf-8")


def test_yulon_win11s_server_gets_both_bridges_and_both_files_back(tmp_path: Path) -> None:
    """The reported box: core 413bea61 (IsBot) + mod-playerbots b949b50 (old config names)
    + mod-ale 84b85cc (IsHeadless and the new config names)."""
    lay(tmp_path, OLD_CORE_SESSION, ALE_NEW)
    for relative, text in ((HEADER, PRE_RENAME_HEADER), (BINDING, ALE_CAUGHT_UP)):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_bytes(text.encode("utf-8"))
    before = files(tmp_path)

    said = " ".join(ale_playerbots.bridge(tmp_path))

    assert b"GetSession()->IsBot()" in methods_bytes(tmp_path)
    assert (tmp_path / BINDING).read_bytes() != ALE_CAUGHT_UP.encode("utf-8")
    assert "SightDistance -> sightDistance" in said and "IsHeadless -> IsBot" in said, said
    ale_playerbots.put_back(tmp_path)
    assert files(tmp_path) == before


def test_a_leftover_session_bridge_goes_once_the_core_has_the_new_name(tmp_path: Path) -> None:
    """Crash inside the window, then "Return to the tested pin…" moved the core to one with
    IsHeadless: nothing is bridged any more, and the leftover rewrite must still go."""
    lay(tmp_path, OLD_CORE_SESSION, ALE_NEW)
    ale_playerbots.bridge(tmp_path)  # ... and no put_back: the process died
    (tmp_path / SESSION_HEADER).write_text(NEW_CORE_SESSION, encoding="utf-8")

    assert all("IsHeadless -> IsBot" not in line for line in ale_playerbots.bridge(tmp_path))
    assert methods_bytes(tmp_path) == ALE_NEW.encode("utf-8")
    assert not (tmp_path / ale_playerbots.RECORD).exists()


# -- around the compile: the Rebuild press -------------------------------------


def _installed_old_core(tmp_path: Path) -> tuple[Recorder, Path, AzerothCoreInstaller]:
    """WotLK installed on the doubles, then an old core header and mod-ale 84b85cc laid in."""
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    made = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )
    list(made.run(InstallOptions(server_dir=server_dir)))
    lay(server_dir, OLD_CORE_SESSION, ALE_NEW)
    rec.images = True
    return rec, server_dir, made


def _build_reads(rec: Recorder, server_dir: Path, seen: list[bytes], result: int = 0) -> Any:
    def build(*args: object, **kwargs: object) -> docker.AttachedRun:
        seen.append(methods_bytes(server_dir))
        rec.calls.append("build")
        return docker.AttachedRun(result, ("built",) if result == 0 else ("error: boom",))

    return build


def test_a_rebuild_on_an_old_core_compiles_is_bot_and_leaves_mod_ales_file(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = _installed_old_core(tmp_path)
    seen: list[bytes] = []
    made._seams = rec.seams(build=_build_reads(rec, server_dir, seen))

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    bridged = ALE_NEW.replace("GetSession()->IsHeadless()", "GetSession()->IsBot()")
    assert seen == [bridged.encode("utf-8")], "the compile did not see the core's name"
    assert methods_bytes(server_dir) == ALE_NEW.encode("utf-8")
    assert not (server_dir / ale_playerbots.RECORD).exists()
    assert any("IsHeadless -> IsBot" in line for line in said), said


def test_a_failed_compile_on_an_old_core_still_puts_mod_ales_file_back(tmp_path: Path) -> None:
    rec, server_dir, made = _installed_old_core(tmp_path)
    seen: list[bytes] = []
    made._seams = rec.seams(build=_build_reads(rec, server_dir, seen, result=1))

    with pytest.raises(InstallerError):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert len(seen) == 1 and b"GetSession()->IsBot()" in seen[0]
    assert methods_bytes(server_dir) == ALE_NEW.encode("utf-8")
    assert not (server_dir / ale_playerbots.RECORD).exists()


def test_the_config_bridge_still_works_without_a_core_header(tmp_path: Path) -> None:
    """T645's half alone: the session half must not make the config half need a core."""
    for relative, text in ((HEADER, PRE_RENAME_HEADER), (BINDING, ALE_CAUGHT_UP)):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_bytes(text.encode("utf-8"))

    ale_playerbots.bridge(tmp_path)
    assert b"sPlayerbotAIConfig.sightDistance" in (tmp_path / BINDING).read_bytes()
