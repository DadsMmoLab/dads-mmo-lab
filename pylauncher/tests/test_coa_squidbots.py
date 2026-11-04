"""T870: Conquest of Azeroth puts SquidBots Lite into the player's client by itself.

The add-on is a second `mod` in `manifests/wow-coa/`, built exactly like T855's
ConsolePort: one `client` step from a clone of the newest release into
`Interface/AddOns`. Unlike ConsolePort it is one of CoA's DEFAULT add-ons
(T612's rule: a fresh install, every tab open, Play), it follows CoA's
"Update the server to latest…" (T814's pairing), and it steps aside for a copy
the player brought themselves (T870 D6). The real-git tests use T814's rig: the
clone is real, only GitHub's "which release is newest" answer is stood in.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support_player_text import command_faults, text_faults
from tests.test_apply import (
    _Compare,
    _LocalOrigin,
    _origin,
    _origin_answers_as_the_manifest,
    _publish,
)
from yulon import coa_client, default_addons
from yulon.apply import Applier, clone_release
from yulon.catalog import native, upstream
from yulon.catalog.catalog import load_catalog
from yulon.catalog.native import LatestRoute
from yulon.controller_wow_coa import modules as coa_modules
from yulon.git import git_available
from yulon.manifest import Manifest, parse_manifest
from yulon.ui.controller_view import ControllerServices, manifest_store_for

COA = "wow-coa"
ADDON_ID = coa_modules.ADDON_ID
ADDON_NAME = coa_modules.ADDON_NAME
OLD_TAG = "v2.9"
NEW_TAG = "v2.9.1"

ORIGIN_FILES = {
    "SquidBotsLite/SquidBotsLite.toc": "## Interface: 30300\n## Version: {v}\nSquidBotsLite.lua\n",
    "SquidBotsLite/SquidBotsLite.lua": "-- {v}\n",
    "README.md": "readme {v}\n",
    "release/old.zip": "zip {v}\n",
}
TOC = "Interface/AddOns/SquidBotsLite/SquidBotsLite.toc"
LUA = "Interface/AddOns/SquidBotsLite/SquidBotsLite.lua"

needs_git = pytest.mark.skipif(
    not git_available(), reason="needs a host git to make a real checkout"
)


def _coa_manifest() -> Manifest:
    store = manifest_store_for(load_catalog().get(COA))
    assert store is not None
    return store.load("mod", ADDON_ID)


def _coa_services(tmp_path: Path, client_dir: Path | None) -> ControllerServices:
    entry = load_catalog().get(COA)
    server_dir = tmp_path / "coa"
    server_dir.mkdir(parents=True)
    password_file = entry.install.password.file
    if password_file:
        (server_dir / password_file).write_text("hunter2", encoding="utf-8")
    return ControllerServices.for_entry(entry, server_dir, client_dir)


def _client(folder: Path, *, interface: bool = True, exe: bool = False) -> Path:
    folder.mkdir(parents=True)
    if interface:
        (folder / "Interface" / "AddOns").mkdir(parents=True)
    if exe:
        (folder / "Ascension.exe").write_text("", encoding="utf-8")
    return folder


def _rig(tmp_path: Path, *, install: bool = False) -> dict[str, Any]:
    """The CoA add-on, its origin, and (when asked) a real install at the old release."""
    manifest = _coa_manifest()
    origin = _origin(tmp_path)
    server = tmp_path / "server"
    server.mkdir()
    client = _client(tmp_path / "Ascension", exe=True)
    released: dict[str, upstream.Release] = {}
    applier = Applier(
        server,
        git=_LocalOrigin(origin),  # type: ignore[arg-type]
        client_dir=client,
        newest_release=lambda _slug: released["now"],
    )
    _origin_answers_as_the_manifest(applier, manifest)
    applier._compare_commits = _Compare(origin)  # type: ignore[assignment]
    _publish(origin, ORIGIN_FILES, "a0")
    old = _publish(origin, ORIGIN_FILES, OLD_TAG)
    released["now"] = upstream.Release(OLD_TAG, old)
    if install:
        applier.install(manifest)
    return {
        "server": server,
        "client": client,
        "origin": origin,
        "applier": applier,
        "manifest": manifest,
        "released": released,
        "old": old,
        "addons": client / "Interface" / "AddOns",
    }


def _route(inner: Any) -> LatestRoute:
    return LatestRoute(
        confirmation=lambda: "update",
        press=inner,
        pin_confirmation=lambda: "pin",
        to_pin=inner,
        source_version=lambda: native.SourceVersion(line="", past_the_pin=False),
    )


def _follow(rig: dict[str, Any], players_own: Any = None) -> default_addons.AddonFollow:
    applier = rig["applier"]
    return default_addons.follow_server_update(
        rig["server"],
        applier,
        lambda: [rig["manifest"]],
        ADDON_ID,
        place=lambda: default_addons.client_place(rig["client"], applier.client_dir),
        players_own=(lambda: False) if players_own is None else players_own,
    )


# --------------------------------------------------------------- the manifest


def test_the_manifest_is_a_coa_client_addon() -> None:
    """F: a wrong game/type/repo/follow or a second step lands ConsolePort's shape instead."""
    manifest = _coa_manifest()
    assert manifest.game == COA
    assert manifest.type == "mod"
    assert manifest.source is not None
    assert manifest.source.repo == "Zyth45/squidbots-addon"
    assert manifest.source.follow == "releases"
    assert [(step.src, step.dest, step.name) for step in manifest.client] == [
        ("SquidBotsLite", "addons", "SquidBotsLite")
    ]
    assert manifest.build.rebuild is False and manifest.build.restart is False


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-unbound", "wow-tortoise", "wow-tbc"])
def test_no_other_game_lists_the_squidbots_item(game: str, tmp_path: Path) -> None:
    entry = load_catalog().get(game)
    if not entry.has_manifests:
        pytest.skip(f"{game} has no manifests")
    store = manifest_store_for(entry)
    assert store is not None
    assert ADDON_ID not in {m.id for m in store.load_all("mod")}


# ---------------------------------------------------------------- the default


def test_coa_names_the_add_on_as_a_default_only_for_a_real_client(tmp_path: Path) -> None:
    """F: without `_a_game_client` an empty folder is taken for the game and gets written."""
    exe_only = _coa_services(tmp_path / "exe", _client(tmp_path / "c1", interface=False, exe=True))
    assert exe_only.default_addons == (ADDON_ID,)

    lower = _client(tmp_path / "c2")
    (lower / "Interface").rename(lower / "interface")
    lower_case = _coa_services(tmp_path / "lower", lower)
    assert lower_case.default_addons == (ADDON_ID,)

    empty = _coa_services(tmp_path / "empty", _client(tmp_path / "c3", interface=False))
    assert empty.default_addons == ()

    none = _coa_services(tmp_path / "none", None)
    assert none.default_addons == ()
    for services in (exe_only, lower_case, empty, none):
        assert "yulon-consoleport" not in services.default_addons


def test_other_games_name_no_default_from_the_coa_tree(tmp_path: Path) -> None:
    client = _client(tmp_path / "wotlk", exe=True)
    server = tmp_path / "wotlk-server"
    server.mkdir()
    wotlk = ControllerServices.for_entry(load_catalog().get("wow-wotlk"), server, client)
    assert wotlk.default_addons == ()
    unbound = ControllerServices.for_entry(
        load_catalog().get("wow-unbound"), tmp_path / "u", client
    )
    assert unbound.default_addons == ()


# ------------------------------------------------------------ update client


def test_update_client_never_writes_the_squidbots_tree() -> None:
    """F: without the excluded tree an Update client would replace the add-on's files."""
    assert coa_client.classify("Interface/AddOns/SquidBotsLite/SquidBotsLite.toc") == "excluded"
    assert coa_client.classify("interface/addons/squidbotslite/Alts.lua") == "excluded"


# --------------------------------------------------------------- the install


@needs_git
def test_a_fresh_install_clones_the_newest_release_and_copies_only_the_add_on(
    tmp_path: Path,
) -> None:
    """F: copying the repository root would land README/release/ beside the add-on; and a
    missing `_NAMES` row makes the Play line name the id instead of "SquidBots Lite"."""
    rig = _rig(tmp_path)
    # The release's tag and the files it carries are two facts: v2.9.1 is the newest
    # release, and its commit is the one whose SquidBotsLite.toc says v2.9.
    rig["released"]["now"] = upstream.Release(NEW_TAG, rig["old"])
    out = default_addons.put_in(
        rig["server"], rig["applier"], [rig["manifest"]], coa_modules.DEFAULT_ADDONS, clone=True
    )

    assert out.installed == (ADDON_ID,)
    assert out.notes() == ("Put SquidBots Lite into your game client.",)
    assert (
        (rig["client"] / TOC)
        .read_text(encoding="utf-8")
        .startswith("## Interface: 30300\n## Version: v2.9\n")
    )
    assert [p.name for p in rig["addons"].iterdir()] == ["SquidBotsLite"]
    assert not (rig["addons"] / "README.md").exists()
    assert not (rig["addons"] / "release").exists()
    assert not any((p / ".git").exists() for p in rig["addons"].iterdir())
    assert clone_release(rig["applier"].clone_dir(rig["manifest"]), item_id=ADDON_ID) == NEW_TAG


@needs_git
def test_a_remove_is_remembered_and_leaves_the_add_on_folder(tmp_path: Path) -> None:
    """F: without the note the next tab open puts back what the player removed."""
    rig = _rig(tmp_path, install=True)
    report = rig["applier"].remove(rig["manifest"])
    default_addons.remember(rig["server"], report, coa_modules.DEFAULT_ADDONS)

    out = default_addons.put_in(
        rig["server"], rig["applier"], [rig["manifest"]], coa_modules.DEFAULT_ADDONS, clone=True
    )

    assert out.installed == ()
    assert not rig["applier"].clone_dir(rig["manifest"]).exists()
    assert (rig["client"] / TOC).is_file()
    assert any("SquidBotsLite" in line for line in report.left_behind)

    default_addons.undecline(rig["server"], ADDON_ID)
    again = default_addons.put_in(
        rig["server"], rig["applier"], [rig["manifest"]], coa_modules.DEFAULT_ADDONS, clone=True
    )
    assert again.installed == (ADDON_ID,)


@needs_git
def test_play_does_not_clone_and_puts_missing_files_back(tmp_path: Path) -> None:
    """F: a Play that clones would put a stalled network in front of the game."""
    rig = _rig(tmp_path)
    first = default_addons.put_in(
        rig["server"], rig["applier"], [rig["manifest"]], coa_modules.DEFAULT_ADDONS, clone=False
    )
    assert first.installed == () and first.restored == ()
    assert not (rig["addons"] / "SquidBotsLite").exists()

    default_addons.put_in(
        rig["server"], rig["applier"], [rig["manifest"]], coa_modules.DEFAULT_ADDONS, clone=True
    )
    shutil.rmtree(rig["addons"] / "SquidBotsLite")

    out = default_addons.put_in(
        rig["server"], rig["applier"], [rig["manifest"]], coa_modules.DEFAULT_ADDONS, clone=False
    )

    assert out.restored == (ADDON_ID,)
    assert (
        (rig["client"] / TOC)
        .read_text(encoding="utf-8")
        .endswith("## Version: v2.9\nSquidBotsLite.lua\n")
    )


# ------------------------------------------------- a copy the player brought


def _outside(name: str) -> Manifest:
    return parse_manifest(
        {
            "id": "player-add-on" if name != "SquidBotsLite" else ADDON_ID,
            "name": name,
            "type": "mod",
            "game": COA,
            "origin": {"kind": "folder", "path": "/x", "added": "2026-11-04", "addon": True},
            "client": [{"src": ".", "dest": "addons", "name": name}],
        }
    )


def test_a_copy_the_player_brought_keeps_the_default_out(tmp_path: Path) -> None:
    """F: without `.casefold()` a lower-case outside folder does not match the default's."""
    squid = _coa_manifest()
    outside = _outside("squidbotslite")
    assert squid.client[0].name == "SquidBotsLite"

    assert default_addons.players_own(ADDON_ID, [squid], [outside]) is True
    assert default_addons.ids_to_put_in(coa_modules.DEFAULT_ADDONS, [squid], [outside]) == ()

    other = _outside("OtherAddon")
    assert default_addons.players_own(ADDON_ID, [squid], [other]) is False
    assert default_addons.ids_to_put_in(coa_modules.DEFAULT_ADDONS, [squid], [other]) == (ADDON_ID,)

    itself = parse_manifest({**squid.model_dump(), "origin": outside.origin.model_dump()})
    assert default_addons.players_own(ADDON_ID, [squid], [itself]) is False


# ----------------------------------------------------------------- the follow


@needs_git
def test_a_finished_server_update_brings_the_add_on_to_the_newest_release(
    tmp_path: Path,
) -> None:
    """F: a follow that lifts the check after `install()` puts the add-on in anyway."""
    rig = _rig(tmp_path, install=True)
    addon = rig["client"] / LUA
    addon.write_text("-- the player's old file\n", encoding="utf-8")
    new = _publish(rig["origin"], ORIGIN_FILES, NEW_TAG)
    rig["released"]["now"] = upstream.Release(NEW_TAG, new)

    def press(_cancel: Any = None) -> Iterator[str]:
        yield "the server is on the newest code"

    wrapped = coa_modules.follow_with_addon(_route(press), lambda: _follow(rig))
    assert wrapped is not None
    lines = list(wrapped.press(None))

    assert addon.read_text(encoding="utf-8").strip() == f"-- {NEW_TAG}"
    assert lines[-2] == "Checking SquidBots Lite in your game client…"
    assert lines[-1] == default_addons.FOLLOW_UPDATED.format(name=ADDON_NAME, tag=NEW_TAG)


@needs_git
def test_the_follow_leaves_the_players_own_copy_alone(tmp_path: Path) -> None:
    """F: asking after `install()` would clone over the copy the player brought themselves."""
    rig = _rig(tmp_path, install=True)
    shutil.rmtree(rig["applier"].clone_dir(rig["manifest"]))
    new = _publish(rig["origin"], ORIGIN_FILES, NEW_TAG)
    rig["released"]["now"] = upstream.Release(NEW_TAG, new)

    outcome = _follow(rig, players_own=lambda: True)

    assert outcome.kind is default_addons.Followed.NOT_INSTALLED
    assert outcome.sentence == default_addons.FOLLOW_PLAYERS_OWN.format(name=ADDON_NAME)
    assert not rig["applier"].clone_dir(rig["manifest"]).exists()
    assert (rig["client"] / LUA).read_text(encoding="utf-8").strip() == f"-- {OLD_TAG}"


def test_the_coa_tab_wires_the_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F: without the wrap the finished server update never mentions SquidBots Lite."""
    from yulon import install_wiring

    class Engine:
        def update_to_latest(self, _options: object, **_kw: object) -> Iterator[str]:
            yield "engine: updated"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: Engine())
    coa_server = tmp_path / "coa"
    coa_server.mkdir()
    services = ControllerServices.for_entry(load_catalog().get(COA), coa_server)
    route = services.update_to_latest
    assert route is not None

    lines = list(route.press(None))

    assert lines[0] == "engine: updated"
    assert lines[-2] == "Checking SquidBots Lite in your game client…"
    assert lines[-1] == default_addons.FOLLOW_UNKNOWN.format(name=ADDON_NAME)
    assert not any("Checking" in line for line in route.to_pin(None))

    wotlk_server = tmp_path / "w"
    wotlk_server.mkdir()
    other = ControllerServices.for_entry(load_catalog().get("wow-wotlk"), wotlk_server)
    wotlk_route = other.update_to_latest
    assert wotlk_route is not None
    assert not any("Checking" in line for line in wotlk_route.press(None))


# ----------------------------------------------------------------- the words


def test_every_filled_sentence_is_player_text() -> None:
    """F: a backtick, a ticket number or the word "manifest" in a sentence the player sees."""
    checking = "Checking SquidBots Lite in your game client…"
    lines = [
        default_addons.FOLLOW_PLAYERS_OWN.format(name=ADDON_NAME),
        checking,
        *_coa_manifest().description.splitlines(),
        *default_addons.Outcome(installed=(ADDON_ID,)).notes(),
        *default_addons.Outcome(restored=(ADDON_ID,)).notes(),
        *default_addons.Outcome(failed={ADDON_ID: "GitHub could not be reached"}).notes(),
    ]
    for line in lines:
        assert text_faults(line) == [], line
        assert command_faults(line) == [], line
        assert "`" not in line, line
