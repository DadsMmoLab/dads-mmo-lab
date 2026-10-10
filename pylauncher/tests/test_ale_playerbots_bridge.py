"""T645: mod-ale's Playerbots support compiled against a mod-playerbots that renamed its config.

mod-playerbots ed54b459 (#2854, 2026-10-06) renamed every public `PlayerbotAIConfig`
member to UpperCamelCase; azerothcore/mod-ale's Playerbots bindings (03b106c #397
onwards, cead0cb today) still read two of them by the old names, so every build
with both failed at `PlayerBotAIMethods.h:515` ("no member named 'sightDistance' in
'PlayerbotAIConfig'; did you mean 'SightDistance'?").

`families/ale_playerbots.py` bridges the names for the compile only: these tests
pin what it rewrites (only `sPlayerbotAIConfig.<name>` where the header lost the name
and has exactly one twin that differs in case), what it leaves alone (every pin
today, and a mod-ale that has caught up), that it puts every file back byte for byte,
and how it recovers from a compile it never saw finish.

The header and binding excerpts are the measured bytes (line 113 of
`src/PlayerbotAIConfig.h` at mod-playerbots 037c0141 and 79bd4281; lines 515 and
528 of `src/LuaEngine/methods/Playerbots/PlayerBotAIMethods.h` at mod-ale cead0cb).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder
from yulon import docker, install_wiring, resources, server_build_presses
from yulon.catalog import native
from yulon.catalog.families import ale_playerbots
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.module_moves import BuildErrorScanner

HEADER = "modules/mod-playerbots/src/PlayerbotAIConfig.h"
BINDING = "modules/mod-ale/src/LuaEngine/methods/Playerbots/PlayerBotAIMethods.h"
UNRELATED = "modules/mod-ale/src/LuaEngine/methods/PlayerMethods.h"

PRE_RENAME_HEADER = (
    "class PlayerbotAIConfig\n"
    "{\n"
    "public:\n"
    "    static PlayerbotAIConfig& instance()\n"
    "    {\n"
    "        static PlayerbotAIConfig instance;\n"
    "        return instance;\n"
    "    }\n"
    "    bool dynamicReactDelay;\n"
    "    float sightDistance, spellDistance, reactDistance, grindDistance, lootDistance, "
    "shootDistance, fleeDistance,\n"
    "        tooCloseDistance, meleeDistance, followDistance, whisperDistance, contactDistance;\n"
    "};\n"
    "\n"
    "#define sPlayerbotAIConfig PlayerbotAIConfig::instance()\n"
)
"""mod-playerbots 037c0141 (WotLK's pin, Unbound's on #439): the old names."""

POST_RENAME_HEADER = (
    "class PlayerbotAIConfig\n"
    "{\n"
    "public:\n"
    "    static PlayerbotAIConfig& Instance()\n"
    "    {\n"
    "        static PlayerbotAIConfig instance;\n"
    "        return instance;\n"
    "    }\n"
    "    bool DynamicReactDelay;\n"
    "    // was sightDistance before #2854\n"
    "    float SightDistance, SpellDistance, ReactDistance, GrindDistance, LootDistance, "
    "ShootDistance, FleeDistance,\n"
    "        TooCloseDistance, MeleeDistance, FollowDistance, WhisperDistance, ContactDistance;\n"
    "};\n"
    "\n"
    "#define sPlayerbotAIConfig PlayerbotAIConfig::Instance()\n"
)
"""mod-playerbots ed54b459 and later (master 79bd4281): the new names.

The comment is not upstream's: it is here so a test proves a name that survives only
in a comment does not count as declared.
"""

ALE_OLD = (
    "    int GetNearGroupMemberCount(lua_State* L, PlayerbotAI* botAI)\r\n"
    "    {\r\n"
    "        float distance = ALE::CHECKVAL<float>(L, 2, sPlayerbotAIConfig.sightDistance);\r\n"
    "        ALE::Push(L, botAI->GetNearGroupMemberCount(distance));\r\n"
    "        return 1;\r\n"
    "    }\r\n"
    "    // sight distance, read as sightDistance\r\n"
    "    int HasPlayerNearby(lua_State* L, PlayerbotAI* botAI)\r\n"
    "    {\r\n"
    "        float range = ALE::CHECKVAL<float>(L, 2, sPlayerbotAIConfig.reactDistance);\r\n"
    "        ALE::Push(L, botAI->HasPlayerNearby(range));\r\n"
    "        return 1;\r\n"
    "    }\r\n"
)
"""mod-ale cead0cb's two uses (CRLF here, so a test proves line endings are kept)."""

ALE_BRIDGED = ALE_OLD.replace(
    "sPlayerbotAIConfig.sightDistance", "sPlayerbotAIConfig.SightDistance"
).replace("sPlayerbotAIConfig.reactDistance", "sPlayerbotAIConfig.ReactDistance")
"""What the compile must see against the new header: the two uses, and nothing else."""

ALE_CAUGHT_UP = ALE_BRIDGED
"""A mod-ale that renamed its own uses (the upstream fix): the new names already."""


def lay(server_dir: Path, header: str | None, binding: str | None) -> None:
    """A server folder with the two module checkouts as given; None leaves one out."""
    if header is not None:
        path = server_dir / HEADER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header.encode("utf-8"))
    if binding is not None:
        path = server_dir / BINDING
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(binding.encode("utf-8"))
        other = server_dir / UNRELATED
        other.write_bytes(b"ALE::Push(L, player->GetSession()->IsHeadless());\n")


def binding_bytes(server_dir: Path) -> bytes:
    return (server_dir / BINDING).read_bytes()


# -- what is renamed ---------------------------------------------------------


def test_the_two_old_names_get_the_new_header_names() -> None:
    assert ale_playerbots.renames_for(POST_RENAME_HEADER, ALE_OLD) == {
        "sightDistance": "SightDistance",
        "reactDistance": "ReactDistance",
    }


def test_nothing_is_renamed_against_the_tested_pins_header() -> None:
    assert ale_playerbots.renames_for(PRE_RENAME_HEADER, ALE_OLD) == {}


def test_nothing_is_renamed_once_mod_ale_has_caught_up() -> None:
    assert ale_playerbots.renames_for(POST_RENAME_HEADER, ALE_CAUGHT_UP) == {}


def test_a_caught_up_mod_ale_is_bridged_back_to_a_pinned_old_header() -> None:
    """The other direction: upstream mod-ale renames first, WotLK's pin still has old names."""
    assert ale_playerbots.renames_for(PRE_RENAME_HEADER, ALE_CAUGHT_UP) == {
        "SightDistance": "sightDistance",
        "ReactDistance": "reactDistance",
    }


def test_a_name_with_no_twin_or_two_twins_is_left_for_the_compiler() -> None:
    header = POST_RENAME_HEADER.replace("ContactDistance;", "ContactDistance, CONTACTdistance;")
    used = "sPlayerbotAIConfig.contactDistance; sPlayerbotAIConfig.botCheatMask;"
    assert ale_playerbots.renames_for(header, used) == {}


def test_a_name_only_a_comment_still_carries_is_not_declared() -> None:
    """`// was sightDistance` in the new header must not make the old name look declared."""
    assert "sightDistance" in ale_playerbots.renames_for(POST_RENAME_HEADER, ALE_OLD)


def test_a_header_that_is_not_the_config_class_renames_nothing() -> None:
    header = POST_RENAME_HEADER.replace("class PlayerbotAIConfig", "class SomethingElse")
    assert ale_playerbots.renames_for(header, ALE_OLD) == {}


# -- the bridge on disk ------------------------------------------------------


def test_the_bridge_rewrites_only_the_two_uses_and_says_so(tmp_path: Path) -> None:
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    unrelated = (tmp_path / UNRELATED).read_bytes()

    said = list(ale_playerbots.bridge(tmp_path))

    assert binding_bytes(tmp_path) == ALE_BRIDGED.encode("utf-8")
    assert (tmp_path / UNRELATED).read_bytes() == unrelated
    joined = " ".join(said)
    assert "sightDistance -> SightDistance" in joined and "reactDistance -> ReactDistance" in joined
    assert "PlayerBotAIMethods.h" in joined, said
    assert (tmp_path / ale_playerbots.RECORD).is_file()


def test_putting_back_restores_every_byte_and_removes_the_record(tmp_path: Path) -> None:
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    os.chmod(tmp_path / BINDING, 0o640)
    list(ale_playerbots.bridge(tmp_path))

    said = ale_playerbots.put_back(tmp_path)

    assert binding_bytes(tmp_path) == ALE_OLD.encode("utf-8")
    assert (tmp_path / BINDING).stat().st_mode & 0o777 == 0o640
    assert not (tmp_path / ale_playerbots.RECORD).exists()
    assert any("PlayerBotAIMethods.h" in line for line in said), said


@pytest.mark.parametrize(
    ("header", "binding"),
    [
        (PRE_RENAME_HEADER, ALE_OLD),
        (POST_RENAME_HEADER, ALE_CAUGHT_UP),
        (None, ALE_OLD),
        (POST_RENAME_HEADER, None),
    ],
    ids=["tested-pins", "mod-ale-caught-up", "no-mod-playerbots", "no-mod-ale"],
)
def test_the_bridge_is_a_no_op_where_the_names_agree_or_a_module_is_absent(
    tmp_path: Path, header: str | None, binding: str | None
) -> None:
    lay(tmp_path, header, binding)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert list(ale_playerbots.bridge(tmp_path)) == []
    assert ale_playerbots.put_back(tmp_path) == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_a_linked_source_file_is_never_written_through(tmp_path: Path) -> None:
    lay(tmp_path, POST_RENAME_HEADER, None)
    outside = tmp_path / "elsewhere.h"
    outside.write_bytes(ALE_OLD.encode("utf-8"))
    link = tmp_path / BINDING
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    assert list(ale_playerbots.bridge(tmp_path)) == []
    assert outside.read_bytes() == ALE_OLD.encode("utf-8")
    assert link.is_symlink()


def test_a_compile_that_never_finished_is_put_back_before_bridging_again(tmp_path: Path) -> None:
    """Yu'lon died inside the window: the next compile starts from upstream's bytes."""
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    list(ale_playerbots.bridge(tmp_path))  # ... and no put_back: the process died

    list(ale_playerbots.bridge(tmp_path))
    assert binding_bytes(tmp_path) == ALE_BRIDGED.encode("utf-8")
    ale_playerbots.put_back(tmp_path)

    assert binding_bytes(tmp_path) == ALE_OLD.encode("utf-8")
    assert not (tmp_path / ale_playerbots.RECORD).exists()


def test_a_left_over_record_restores_a_file_once_the_names_agree_again(tmp_path: Path) -> None:
    """Crash, then the player returned to the tested pin: the bridge has nothing to do, the
    leftover rewrite must still go."""
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    list(ale_playerbots.bridge(tmp_path))
    (tmp_path / HEADER).write_text(PRE_RENAME_HEADER, encoding="utf-8")

    list(ale_playerbots.bridge(tmp_path))
    ale_playerbots.put_back(tmp_path)

    assert binding_bytes(tmp_path) == ALE_OLD.encode("utf-8")
    assert not (tmp_path / ale_playerbots.RECORD).exists()


def test_a_file_changed_since_the_bridge_wrote_it_is_never_overwritten(tmp_path: Path) -> None:
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    list(ale_playerbots.bridge(tmp_path))
    theirs = b"// the player's own edit\n"
    (tmp_path / BINDING).write_bytes(theirs)

    said = ale_playerbots.put_back(tmp_path)

    assert binding_bytes(tmp_path) == theirs
    assert not (tmp_path / ale_playerbots.RECORD).exists()
    assert any("changed" in line for line in said), said


def test_a_record_yulon_cannot_read_refuses_before_the_compile(tmp_path: Path) -> None:
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    (tmp_path / ale_playerbots.RECORD).write_text("{not json", encoding="utf-8")

    with pytest.raises(InstallerError, match="Nothing was built"):
        list(ale_playerbots.bridge(tmp_path))
    assert binding_bytes(tmp_path) == ALE_OLD.encode("utf-8")


def test_the_record_holds_the_original_text_and_the_bridged_digest(tmp_path: Path) -> None:
    lay(tmp_path, POST_RENAME_HEADER, ALE_OLD)
    list(ale_playerbots.bridge(tmp_path))

    record = json.loads((tmp_path / ale_playerbots.RECORD).read_text(encoding="utf-8"))
    assert list(record["files"]) == [BINDING]
    assert record["files"][BINDING]["original"] == ALE_OLD


# -- the build error that names the cause -------------------------------------


REPORTED = (
    "#12 1260.1 /azerothcore/modules/mod-ale/src/LuaEngine/methods/Playerbots/"
    "PlayerBotAIMethods.h:515:72: fatal error: no member named 'sightDistance' in "
    "'PlayerbotAIConfig'; did you mean 'SightDistance'?"
)
"""The player's screenshot, as BuildKit prefixes the line."""


def test_the_scanner_tells_a_playerbots_binding_error_from_any_other_mod_ale_error() -> None:
    scanner = BuildErrorScanner()
    scanner.feed(REPORTED)
    assert scanner.named == ("mod-ale",)
    assert scanner.ale_playerbots_failed

    other = BuildErrorScanner()
    other.feed(
        "/azerothcore/modules/mod-ale/src/LuaEngine/methods/PlayerMethods.h:5167:44: "
        "fatal error: no member named 'IsBot' in 'WorldSession'"
    )
    assert other.named == ("mod-ale",)
    assert not other.ale_playerbots_failed


# -- around the compile, on every press that compiles --------------------------


def _installed_wotlk(tmp_path: Path) -> tuple[Recorder, Path, AzerothCoreInstaller]:
    """WotLK installed on the doubles, then mod-ale and the renamed playerbots laid in."""
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
    lay(server_dir, POST_RENAME_HEADER, ALE_OLD)
    rec.images = True
    return rec, server_dir, made


def _build_reads(rec: Recorder, server_dir: Path, seen: list[bytes], result: int = 0) -> Any:
    def build(*args: object, **kwargs: object) -> docker.AttachedRun:
        seen.append(binding_bytes(server_dir))
        rec.calls.append("build")
        return docker.AttachedRun(result, ("built",) if result == 0 else ("error: boom",))

    return build


def test_a_rebuild_compiles_the_bridged_names_and_leaves_upstreams_file(tmp_path: Path) -> None:
    rec, server_dir, made = _installed_wotlk(tmp_path)
    seen: list[bytes] = []
    made._seams = rec.seams(build=_build_reads(rec, server_dir, seen))

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == [ALE_BRIDGED.encode("utf-8")], "the compile did not see the new names"
    assert binding_bytes(server_dir) == ALE_OLD.encode("utf-8")
    assert not (server_dir / ale_playerbots.RECORD).exists()
    assert any("sightDistance -> SightDistance" in line for line in said), said


def test_a_failed_compile_still_puts_upstreams_file_back(tmp_path: Path) -> None:
    rec, server_dir, made = _installed_wotlk(tmp_path)
    seen: list[bytes] = []
    made._seams = rec.seams(build=_build_reads(rec, server_dir, seen, result=1))

    with pytest.raises(InstallerError):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == [ALE_BRIDGED.encode("utf-8")]
    assert binding_bytes(server_dir) == ALE_OLD.encode("utf-8")
    assert not (server_dir / ale_playerbots.RECORD).exists()


def test_a_rebuild_on_the_tested_pins_writes_nothing_and_says_nothing(tmp_path: Path) -> None:
    rec, server_dir, made = _installed_wotlk(tmp_path)
    lay(server_dir, PRE_RENAME_HEADER, ALE_OLD)
    seen: list[bytes] = []
    made._seams = rec.seams(build=_build_reads(rec, server_dir, seen))

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == [ALE_OLD.encode("utf-8")]
    assert not any("mod-playerbots renamed" in line for line in said), said
    assert not (server_dir / ale_playerbots.RECORD).exists()


# -- the sentence a failed build ends with --------------------------------------


@pytest.mark.parametrize(
    "press",
    [
        server_build_presses.REBUILD,
        server_build_presses.UPDATE_TO_LATEST,
        server_build_presses.RETURN_TO_PIN,
    ],
)
def test_the_note_names_the_lag_and_never_promises_a_mod_ale_update(press: str) -> None:
    said = native.ale_playerbots_note(press)
    assert "mod-ale's Playerbots support" in said and "mod-playerbots" in said
    assert "renamed" in said
    assert "once its authors" in said
    assert "If mod-ale has an update" not in said


def test_the_rebuild_note_points_at_the_tested_pin() -> None:
    said = native.ale_playerbots_note(server_build_presses.REBUILD)
    assert server_build_presses.RETURN_TO_PIN in said


def test_a_failed_build_in_the_bindings_ends_with_the_lag_note_not_the_generic_one(
    tmp_path: Path,
) -> None:
    def failing() -> Iterator[str]:
        yield REPORTED
        raise InstallerError("the build failed (exit 1).")

    with pytest.raises(InstallerError) as raised:
        list(
            install_wiring.with_module_moves(
                failing(),
                tmp_path,
                put_back=None,
                kept_settles=False,
                note=partial(native.module_order_note, press=server_build_presses.UPDATE_TO_LATEST),
                lag=native.ale_playerbots_note(server_build_presses.UPDATE_TO_LATEST),
            )
        )

    said = str(raised.value)
    assert "mod-ale's Playerbots support" in said, said
    assert "If mod-ale has an update" not in said, said


def test_any_other_mod_ale_error_keeps_the_generic_note(tmp_path: Path) -> None:
    def failing() -> Iterator[str]:
        yield (
            "/azerothcore/modules/mod-ale/src/LuaEngine/methods/PlayerMethods.h:5167:44: "
            "fatal error: no member named 'IsBot' in 'WorldSession'"
        )
        raise InstallerError("the build failed (exit 1).")

    with pytest.raises(InstallerError) as raised:
        list(
            install_wiring.with_module_moves(
                failing(),
                tmp_path,
                put_back=None,
                kept_settles=False,
                note=partial(native.module_order_note, press=server_build_presses.UPDATE_TO_LATEST),
                lag=native.ale_playerbots_note(server_build_presses.UPDATE_TO_LATEST),
            )
        )

    said = str(raised.value)
    assert "If mod-ale has an update" in said, said
    assert "mod-ale's Playerbots support" not in said, said
