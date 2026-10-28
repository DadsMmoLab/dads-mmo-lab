"""T814: a finished "Update the server to latest" on Tortoise also updates the
TortoiseBots Manager client addon, through the Modules tab's own steps.

The addon clone is real git (a `file://` origin standing in for GitHub's
repository) and the addon step is the real `Applier.install/update()` behind
the press; only the server update's own press and GitHub's answer to "which
release is newest" are stood in. A fake that overrode `install()` would assert
only that the tab selected a method, and would pass while the real path reset
a checkout it should not touch.
"""

from __future__ import annotations

import shutil
import threading

import pytest

from tests.support_player_text import command_faults, text_faults
from tests.test_apply import (
    _Compare,
    _git,
    _LocalOrigin,
    _origin,
    _origin_answers_as_the_manifest,
    _publish,
    _unpinned_shipped,
)
from yulon import default_addons
from yulon.apply import Applier, ApplyError
from yulon.catalog import native, upstream
from yulon.catalog.native import LatestRoute
from yulon.controller_wow_tortoise import game, modules as tortoise_modules
from yulon.git import git_available

pytestmark = pytest.mark.skipif(
    not git_available(), reason="needs a host git to make a real checkout"
)

FILES = {
    "TortoiseBotsManager.toc": "## Interface: 11200\nCore.lua\n",
    "Core.lua": "-- TBM {v}\n",
}
LANDS = "TurtleWoW/Interface/AddOns/TortoiseBotsManager/Core.lua"
OLD_TAG = "v2026-09-24"
NEW_TAG = "v2026-09-25"


def _rig(tmp_path: Path) -> dict[str, Any]:
    """The addon installed at an old release, with a newer one published."""
    origin = _origin(tmp_path)
    _publish(origin, FILES, "a0")
    _publish(origin, FILES, "a1")
    manifest = _unpinned_shipped("tortoise-bots-manager")
    server = tmp_path / "server"
    client = tmp_path / "TurtleWoW"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    applier = Applier(
        server,
        git=_LocalOrigin(origin),  # type: ignore[arg-type]
        client_dir=client,
        newest_release=lambda _slug: released["now"],
    )
    _origin_answers_as_the_manifest(applier, manifest)
    applier._compare_commits = _Compare(origin)
    old = _publish(origin, FILES, "v1")
    _publish(origin, FILES, "tip-past-old")
    released: dict[str, upstream.Release] = {"now": upstream.Release(OLD_TAG, old)}
    applier.install(manifest)
    clone = applier.clone_dir(manifest)
    assert _git(clone, "rev-parse", "HEAD") == old
    assert (tmp_path / LANDS).read_text(encoding="utf-8").strip().endswith("v1")
    new = _publish(origin, FILES, "v2")
    released["now"] = upstream.Release(NEW_TAG, new)
    return {
        "server": server,
        "client": client,
        "applier": applier,
        "manifest": manifest,
        "origin": origin,
        "released": released,
        "clone": clone,
        "old": old,
        "new": new,
    }


def _here(rig: dict[str, Any]) -> Any:
    return default_addons.client_place(rig["client"], rig["applier"].client_dir)


def _follow(rig: dict[str, Any], place: Any = None) -> default_addons.AddonFollow:
    return default_addons.follow_server_update(
        rig["server"],
        rig["applier"],
        lambda: tortoise_modules.store().load_all("mod"),
        tortoise_modules.ADDON_ID,
        place=(lambda: _here(rig)) if place is None else place,
    )


def _route(inner: Any) -> LatestRoute:
    return LatestRoute(
        confirmation=lambda: "update",
        press=inner,
        pin_confirmation=lambda: "pin",
        to_pin=inner,
        source_version=lambda: native.SourceVersion(line="", past_the_pin=False),
    )


def _wrapped(rig: dict[str, Any], inner: Any) -> LatestRoute:
    wrapped = tortoise_modules.follow_with_addon(
        _route(inner),
        lambda: default_addons.follow_server_update(
            rig["server"],
            rig["applier"],
            lambda: tortoise_modules.store().load_all("mod"),
            tortoise_modules.ADDON_ID,
            place=lambda: _here(rig),
        ),
    )
    assert wrapped is not None
    return wrapped


def test_a_finished_server_update_updates_the_addon_in_the_client(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    addon = tmp_path / LANDS
    addon.write_text("-- the player's old file\n", encoding="utf-8")

    def press(_cancel: threading.Event | None = None) -> Iterator[str]:
        yield "the server is on the newest code"

    lines = list(_wrapped(rig, press).press(None))

    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["new"]
    assert addon.read_text(encoding="utf-8").strip().endswith("v2")
    assert lines[-1] == default_addons.FOLLOW_UPDATED.format(
        name="TortoiseBots Manager", tag=NEW_TAG
    )


def test_an_addon_already_on_the_newest_release_is_not_fetched(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig["applier"].update(rig["manifest"])
    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["new"]
    addon = tmp_path / LANDS
    addon.unlink()
    outcome = _follow(rig)

    assert outcome.kind is default_addons.Followed.CURRENT
    assert outcome.sentence == default_addons.FOLLOW_CURRENT.format(
        name="TortoiseBots Manager", tag=NEW_TAG
    )
    assert addon.read_text(encoding="utf-8").strip().endswith("v2")


def test_no_client_folder_says_so_and_writes_nothing(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    outcome = _follow(rig, place=lambda: default_addons.client_place(None, None))

    assert outcome.kind is default_addons.Followed.UNKNOWN
    assert outcome.sentence == default_addons.FOLLOW_UNKNOWN.format(name="TortoiseBots Manager")
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == before
    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["old"]


def test_a_client_folder_not_on_this_computer_says_so(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    outcome = _follow(
        rig,
        place=lambda: default_addons.client_place(
            tmp_path / "gone" / "WoW", rig["applier"].client_dir
        ),
    )

    assert outcome.kind is default_addons.Followed.ELSEWHERE
    assert outcome.sentence == default_addons.FOLLOW_ELSEWHERE.format(name="TortoiseBots Manager")
    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["old"]


def test_a_server_on_a_box_says_so(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    outcome = default_addons.follow_server_update(
        rig["server"],
        rig["applier"],
        lambda: tortoise_modules.store().load_all("mod"),
        tortoise_modules.ADDON_ID,
        place=lambda: default_addons.client_place(
            rig["client"], rig["applier"].client_dir, on_box=True
        ),
    )

    assert outcome.kind is default_addons.Followed.ELSEWHERE
    assert outcome.sentence == default_addons.FOLLOW_ELSEWHERE.format(name="TortoiseBots Manager")
    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["old"]


def test_a_removed_addon_is_not_put_back(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    shutil.rmtree(rig["clone"])
    default_addons.decline(rig["server"], "tortoise-bots-manager")
    outcome = _follow(rig)

    assert outcome.kind is default_addons.Followed.NOT_INSTALLED
    assert outcome.sentence == default_addons.FOLLOW_NOT_INSTALLED.format(
        name="TortoiseBots Manager"
    )
    assert not rig["clone"].exists()


def test_an_unreadable_memory_note_installs_nothing(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    shutil.rmtree(rig["clone"])
    (rig["server"] / default_addons.DECLINED_FILE).write_text("{not json", encoding="utf-8")
    outcome = _follow(rig)

    assert outcome.kind is default_addons.Followed.NOT_INSTALLED
    assert not rig["clone"].exists()


def test_a_never_installed_addon_is_installed_with_the_server_update(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    _publish(origin, FILES, "a0")
    _publish(origin, FILES, "a1")
    manifest = _unpinned_shipped("tortoise-bots-manager")
    server = tmp_path / "server"
    client = tmp_path / "TurtleWoW"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    new = _publish(origin, FILES, "v2")
    applier = Applier(
        server,
        git=_LocalOrigin(origin),  # type: ignore[arg-type]
        client_dir=client,
        newest_release=lambda _slug: upstream.Release(NEW_TAG, new),
    )
    _origin_answers_as_the_manifest(applier, manifest)

    outcome = default_addons.follow_server_update(
        server,
        applier,
        lambda: tortoise_modules.store().load_all("mod"),
        tortoise_modules.ADDON_ID,
        place=lambda: default_addons.client_place(client, applier.client_dir),
    )

    assert outcome.kind is default_addons.Followed.INSTALLED
    assert outcome.sentence == default_addons.FOLLOW_INSTALLED.format(
        name="TortoiseBots Manager", tag=NEW_TAG
    )
    assert (tmp_path / LANDS).read_text(encoding="utf-8").strip().endswith("v2")
    assert default_addons._read(server) == (frozenset(), {})


@pytest.mark.parametrize("failing", ["refusal", "direction", "boom"])
def test_an_addon_failure_never_fails_the_server_update(tmp_path: Path, failing: str) -> None:
    rig = _rig(tmp_path)
    applier = rig["applier"]
    if failing == "refusal":
        applier.no_local_commits = lambda _d, _b: False  # type: ignore[method-assign]
    elif failing == "direction":
        applier._compare_commits = lambda _s, _b, _r: None  # type: ignore[assignment]
    else:

        def boom(manifest: Any, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("the disk is gone")

        applier.update = boom  # type: ignore[method-assign]

    def press(_cancel: threading.Event | None = None) -> Iterator[str]:
        yield "the server is on the newest code"

    lines = list(_wrapped(rig, press).press(None))

    assert lines[0] == "the server is on the newest code"
    assert lines[-2] == "Checking TortoiseBots Manager in your game client…"
    assert lines[-1].startswith("The server update is done, but TortoiseBots Manager")
    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["old"]


def test_a_follow_that_raises_itself_never_fails_the_server_update() -> None:
    def broken() -> default_addons.AddonFollow:
        raise RuntimeError("the note is gone")

    def press(_cancel: threading.Event | None = None) -> Iterator[str]:
        yield "the server is on the newest code"

    wrapped = tortoise_modules.follow_with_addon(_route(press), broken)
    assert wrapped is not None

    assert list(wrapped.press(None)) == [
        "the server is on the newest code",
        "Checking TortoiseBots Manager in your game client…",
        default_addons.FOLLOW_FAILED.format(name="TortoiseBots Manager", why="the note is gone"),
    ]


def test_a_failed_server_update_does_not_touch_the_addon(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    seen: list[str] = []

    def fail(_cancel: threading.Event | None = None) -> Iterator[str]:
        yield "half way"
        raise ApplyError("the build broke")

    def follow() -> default_addons.AddonFollow:
        seen.append("follow")
        raise AssertionError("must not run")

    wrapped = tortoise_modules.follow_with_addon(_route(fail), follow)
    assert wrapped is not None

    with pytest.raises(ApplyError):
        list(wrapped.press(None))

    assert seen == []
    assert _git(rig["clone"], "rev-parse", "HEAD") == rig["old"]


def test_a_stopped_update_does_not_touch_the_addon(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    seen: list[str] = []

    def follow() -> default_addons.AddonFollow:
        seen.append("follow")
        raise AssertionError("must not run")

    cancel = threading.Event()
    cancel.set()
    wrapped = tortoise_modules.follow_with_addon(
        _route(lambda _c: iter(["the server is on the newest code"])), follow
    )
    assert wrapped is not None

    lines = list(wrapped.press(cancel))

    assert seen == []
    assert not any("TortoiseBots Manager" in line for line in lines)


def test_return_to_the_tested_pin_does_not_move_the_addon() -> None:
    inner = LatestRoute(
        confirmation=lambda: "update",
        press=lambda _c: iter(["server"]),
        pin_confirmation=lambda: "pin",
        to_pin=lambda _c: iter(["pin"]),
        source_version=lambda: native.SourceVersion(line="", past_the_pin=False),
    )

    def follow() -> default_addons.AddonFollow:
        raise AssertionError("the way back leaves the addon alone")

    wrapped = tortoise_modules.follow_with_addon(inner, follow)

    assert wrapped is not None
    assert wrapped.to_pin is inner.to_pin


def test_the_tortoise_tab_wires_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import install_wiring
    from yulon.ui.controller_view import ControllerServices

    class Engine:
        def update_to_latest(self, _options: object, **_kw: object) -> Iterator[str]:
            yield "engine: updated"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: Engine())
    entry = game.entry()
    services = ControllerServices.for_entry(entry, tmp_path)
    route = services.update_to_latest
    assert route is not None

    lines = list(route.press(None))

    assert lines[0] == "engine: updated"
    assert lines[-2] == "Checking TortoiseBots Manager in your game client…"
    assert lines[-1] == default_addons.FOLLOW_UNKNOWN.format(name="TortoiseBots Manager")


def test_a_finished_update_drops_the_addons_stale_count(
    tmp_path: Path, qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.controller_view import ControllerView, ControllerServices
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(
        controller_view_module, "threaded_job_runner", lambda _parent: run_inline
    )
    entry = game.entry()
    server = tmp_path / "server"
    server.mkdir()
    services = ControllerServices.for_entry(entry, server)
    view = ControllerView(entry, services, status_poll_ms=0)
    view._behind[("mod", tortoise_modules.ADDON_ID)] = 3
    view._rebuild_moves_sources = True

    view._rebuild_finished(True, "done")

    assert ("mod", tortoise_modules.ADDON_ID) not in view._behind


def test_a_failed_update_keeps_the_addons_stale_count(
    tmp_path: Path, qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.controller_view import ControllerView, ControllerServices
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(
        controller_view_module, "threaded_job_runner", lambda _parent: run_inline
    )
    entry = game.entry()
    server = tmp_path / "server"
    server.mkdir()
    services = ControllerServices.for_entry(entry, server)
    view = ControllerView(entry, services, status_poll_ms=0)
    reloads: list[int] = []
    monkeypatch.setattr(view, "reload_modules", lambda: reloads.append(1))
    view._behind[("mod", tortoise_modules.ADDON_ID)] = 3
    view._rebuild_moves_sources = True

    view._rebuild_finished(False, "upstream could not be reached")

    assert reloads
    assert view._behind.get(("mod", tortoise_modules.ADDON_ID)) == 3

def pump_until(done: Any) -> None:
    from tests.test_controller_view import pump_until as _pump

    _pump(done, "the update finished")


def _answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from yulon.ui import controller_view as controller_view_module

    qmb = controller_view_module.QMessageBox
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "exec",
        lambda self: qmb.StandardButton.Save,
    )


def test_every_follow_sentence_is_one_plain_sentence() -> None:
    name = "TortoiseBots Manager"
    sentences = [
        default_addons.FOLLOW_UPDATED.format(name=name, tag=NEW_TAG),
        default_addons.FOLLOW_INSTALLED.format(name=name, tag=NEW_TAG),
        default_addons.FOLLOW_CURRENT.format(name=name, tag=NEW_TAG),
        default_addons.FOLLOW_UNKNOWN.format(name=name),
        default_addons.FOLLOW_ELSEWHERE.format(name=name),
        default_addons.FOLLOW_NOT_INSTALLED.format(name=name),
        default_addons.FOLLOW_FAILED.format(name=name, why="git stayed silent"),
    ]

    assert len(sentences) == 7
    for sentence in sentences:
        assert "`" not in sentence
        assert command_faults(sentence) == [], sentence
        assert text_faults(sentence) == [], sentence
        assert sentence.count(".") == 1 and sentence.endswith(".")
