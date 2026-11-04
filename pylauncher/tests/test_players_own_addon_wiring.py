"""T870 review round 1: the wiring, not the units, of "removed stays removed" and "mine wins".

Three gaps the cold review found, each asserted through the tab the player presses:

* B1: CoA names SquidBots Lite as a default whatever its client folder is, so a Remove
  in a folder that is not (yet) a game client is still the player's Remove and lands in
  `.yulon-declined-addons.json`. The write paths ask whether the folder holds the game,
  and with the folder back they put nothing in.
* B2: Tortoise's "Update the server to latest…" leaves a player's own TortoiseBots
  Manager alone -- the rule CoA's follow already carried (D6).
* B3: the CoA tab's own tab-open/Play path and its wired update route both honour D6.

The CoA factories are the real `ControllerServices.for_entry()`, so the wiring is what is
under test: the add-on route's outside records are the real ones, written where the route
reads them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from yulon import default_addons, install_wiring, module_source
from yulon.apply import ApplyReport
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_coa import modules as coa_modules
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest, parse_manifest
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

COA = "wow-coa"
TORTOISE = "wow-tortoise"
ADDON_ID = coa_modules.ADDON_ID
ADDON_NAME = coa_modules.ADDON_NAME


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the view's background jobs synchronously, as test_controller_view does."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _game_client(folder: Path) -> Path:
    """A CoA client: its `Interface/` and its exe, which is what `_a_game_client` asks."""
    (folder / "Interface" / "AddOns").mkdir(parents=True)
    (folder / "Ascension.exe").write_text("", encoding="utf-8")
    return folder


def _services(game: str, server: Path, client: Path | None) -> ControllerServices:
    """`ControllerServices.for_entry()` as the tab builds it, without Docker."""
    server.mkdir(parents=True, exist_ok=True)
    entry = load_catalog().get(game)
    password_file = entry.install.password.file
    if password_file:
        (server / password_file).write_text("hunter2", encoding="utf-8")
    return ControllerServices.for_entry(entry, server, client)


def _player_brought(game: str, item_id: str, folder: str) -> Manifest:
    """The record of an add-on the player brought: an add-on origin and one `addons` step."""
    return parse_manifest(
        {
            "id": item_id,
            "name": folder,
            "type": "mod",
            "game": game,
            "origin": {"kind": "folder", "path": "/x", "added": "2026-11-04", "addon": True},
            "client": [{"src": ".", "dest": "addons", "name": folder}],
        }
    )


def _record_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Manifest) -> None:
    """Keep the record where the add-on route reads it, and nowhere near the real home."""
    monkeypatch.setattr(wotlk_modules, "user_manifests_dir", lambda *a, **k: tmp_path / "user")
    module_source.persist(tmp_path / "user", manifest, shipped_ids=())


class _Writes:
    """Stands in for the applier's two client writes: records the id, copies nothing.

    `client_files_missing` always says the clone's files are not in the client, so a
    Play that reaches the restore branch is visible as a `put_back` even with
    `clone=False` (a Play never installs an add-on that has no clone).
    """

    def __init__(self) -> None:
        self.installed: list[str] = []
        self.put_back: list[str] = []

    def install(self, manifest: Any, *args: Any, **kwargs: Any) -> ApplyReport:
        self.installed.append(str(manifest.id))
        return ApplyReport("install", str(manifest.id), family=manifest.type, done=("clone",))

    def client_files_missing(self, _manifest: Any) -> tuple[Path, ...]:
        return (Path("x"),)

    def put_back_client_files(self, manifest: Any) -> tuple[Path, ...]:
        self.put_back.append(str(manifest.id))
        return ()


def _watched(services: ControllerServices) -> _Writes:
    """Point the tab's own applier's client writes at a recorder; everything else is real."""
    writes = _Writes()
    assert services.applier is not None
    services.applier.install = writes.install  # type: ignore[method-assign]
    services.applier.client_files_missing = writes.client_files_missing  # type: ignore[method-assign]
    services.applier.put_back_client_files = writes.put_back_client_files  # type: ignore[method-assign]
    return writes


def _deliver_remove(view: ControllerView, item_id: str) -> None:
    """A Remove of a shipped `mod` row, the way its press delivers the report."""
    manifest = view._manifests.get(("mod", item_id))
    assert manifest is not None, f"{item_id} is not listed on this tab"
    view._acting_on = manifest
    view._module_done(ApplyReport("remove", item_id, family="mod"))


# ------------------------------------------------------------------------ B1


def test_a_folder_that_is_not_the_game_gets_no_default_addon_written(
    qapp: object, tmp_path: Path
) -> None:
    """F: with the name unconditional, a write path that does not ask `_a_game_client`
    clones SquidBots Lite -- or puts its files back -- into a folder that is not a CoA
    client (D3)."""
    server = tmp_path / "coa"
    folder = tmp_path / "not-the-game"
    folder.mkdir()
    services = _services(COA, server, folder)
    assert services.default_addons == (ADDON_ID,)
    writes = _watched(services)
    # A clone on disk, so Play's restore branch is reachable too (clone=False never installs).
    (server / "sql_scripts" / "clones" / ADDON_ID).mkdir(parents=True)
    view = ControllerView(load_catalog().get(COA), services, status_poll_ms=0)

    view.put_default_addons_in()  # the tab opened at start-up
    assert (writes.installed, writes.put_back) == ([], [])
    assert view._default_addons_work(lambda _line: None, clone=False) == ()  # Play
    assert (writes.installed, writes.put_back) == ([], [])


def test_a_remove_in_a_folder_that_is_not_a_game_client_is_still_remembered(
    qapp: object, tmp_path: Path
) -> None:
    """F: `default_ids=()` for a non-client folder leaves `remember()` blind, so the next
    tab open puts back the SquidBots Lite the player removed."""
    server = tmp_path / "coa"
    folder = tmp_path / "not-the-game"
    folder.mkdir()
    (folder / "Data").mkdir()  # a folder, just not one with `Interface/` or the exe
    services = _services(COA, server, folder)
    assert services.default_addons == (ADDON_ID,)
    writes = _watched(services)
    view = ControllerView(load_catalog().get(COA), services, status_poll_ms=0)

    _deliver_remove(view, ADDON_ID)

    assert default_addons.declined(server) == {ADDON_ID}
    # The folder is the game's now: neither a tab open nor Play puts it back.
    _game_client(folder)
    view.put_default_addons_in()
    assert (writes.installed, writes.put_back) == ([], [])
    assert view._default_addons_work(lambda _line: None, clone=False) == ()
    assert (writes.installed, writes.put_back) == ([], [])
    assert not (server / "sql_scripts" / "clones" / ADDON_ID).exists()


# ------------------------------------------------------------------------ B3


def test_a_tab_open_and_play_leave_the_default_out_for_a_copy_the_player_brought(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F: without `ids_to_put_in()` in `_default_addons_work` the tab open clones the
    shipped SquidBots Lite over the copy the player brought themselves."""
    server = tmp_path / "coa"
    client = _game_client(tmp_path / "Ascension")
    _record_it(tmp_path, monkeypatch, _player_brought(COA, "players-squidbots", "SquidBotsLite"))
    services = _services(COA, server, client)
    assert services.default_addons == (ADDON_ID,)
    writes = _watched(services)
    view = ControllerView(load_catalog().get(COA), services, status_poll_ms=0)

    view.put_default_addons_in()  # the tab opened at start-up
    assert (writes.installed, writes.put_back) == ([], [])

    assert view._default_addons_work(lambda _line: None, clone=False) == ()  # Play
    assert (writes.installed, writes.put_back) == ([], [])


class _Engine:
    """The server update itself: one line, then done."""

    def update_to_latest(self, _options: object, **_kw: object) -> Iterator[str]:
        yield "engine: updated"


def _route_of(
    game: str, server: Path, client: Path | None, monkeypatch: pytest.MonkeyPatch
) -> ControllerServices:
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: _Engine())
    services = _services(game, server, client)
    assert services.update_to_latest is not None
    return services


def test_the_coa_update_route_leaves_a_copy_the_player_brought(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F: without `players_own=` on the CoA follow, a finished update clones the shipped
    SquidBots Lite over the copy the player brought."""
    server = tmp_path / "coa"
    client = _game_client(tmp_path / "Ascension")
    _record_it(tmp_path, monkeypatch, _player_brought(COA, "players-squidbots", "SquidBotsLite"))
    services = _route_of(COA, server, client, monkeypatch)
    writes = _watched(services)

    lines = list(services.update_to_latest.press(None))  # type: ignore[union-attr]

    assert lines[0] == "engine: updated"
    assert lines[-2] == f"Checking {ADDON_NAME} in your game client…"
    assert lines[-1] == default_addons.FOLLOW_PLAYERS_OWN.format(name=ADDON_NAME)
    assert writes.installed == []


# ------------------------------------------------------------------------ B2


def test_the_tortoise_update_route_leaves_a_copy_the_player_brought(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F: without `players_own=` on the Tortoise follow, "Update the server to latest…"
    clones the shipped TortoiseBots Manager over the player's own copy (review round 1).

    The follow the factory hands the route is called directly: the two bot-pool and
    dashboard wrappers around the press are T144/T123's own, and neither decides this.
    """
    server = tmp_path / "tortoise"
    client = _game_client(tmp_path / "Turtle")
    _record_it(
        tmp_path,
        monkeypatch,
        _player_brought(TORTOISE, "players-bots-manager", "TortoiseBotsManager"),
    )
    follows: list[Callable[[], default_addons.AddonFollow]] = []
    real = tortoise_modules.follow_with_addon

    def spy(route: Any, follow: Callable[[], default_addons.AddonFollow]) -> Any:
        follows.append(follow)
        return real(route, follow)

    monkeypatch.setattr(tortoise_modules, "follow_with_addon", spy)
    services = _services(TORTOISE, server, client)
    assert len(follows) == 1, "the Tortoise tab did not wire the follow"
    writes = _watched(services)

    outcome = follows[0]()

    assert outcome.kind is default_addons.Followed.NOT_INSTALLED
    assert outcome.sentence == default_addons.FOLLOW_PLAYERS_OWN.format(name="TortoiseBots Manager")
    assert writes.installed == []
    assert not (server / "sql_scripts" / "clones" / tortoise_modules.ADDON_ID).exists()
