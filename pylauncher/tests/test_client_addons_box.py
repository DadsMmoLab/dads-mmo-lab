"""T613 PR-3: the Modules tab's add-on box, on every game, over the route PR-2 built."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from yulon.addon_archive import AddonRefusal
from yulon.apply import ApplyRefusal, ApplyReport
from yulon.catalog.catalog import load_catalog
from yulon.client_addons import Prepared
from yulon.manifest import Manifest, parse_manifest
from yulon.ui import controller_view as cv
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
GAMES = [e.id for e in CATALOG.games if e.client.addon_interface is not None]
NOTHING = "Nothing on this machine was changed."


def _manifest(name: str = "pfUI") -> Manifest:
    return parse_manifest(
        {
            "id": name.lower(),
            "name": name,
            "type": "mod",
            "game": "wow-wotlk",
            "description": "Client add-on (copied from a folder you provided).",
            "origin": {"kind": "folder", "path": "/x", "added": "2026-10-09", "addon": True},
            "build": {"rebuild": False, "restart": False},
            "client": [{"src": ".", "dest": "addons", "name": name}],
        }
    )


@dataclass
class _Applier:
    """The slice of the tab's applier the box asks: the client it really writes to, one question."""

    client_dir: Path | None
    question: str | None = None
    raises: Exception | None = None

    def replacement_question(self, manifest: Manifest) -> str | None:
        if self.raises:
            raise self.raises
        return self.question


class _Staging:
    def __init__(self, route: _Route) -> None:
        self.route = route

    def discard(self) -> None:
        self.route.discarded += 1


@dataclass
class _Route:
    """The seam `ControllerView` presses, recording what it was asked."""

    refusal: str | None = None
    question: str | None = None
    applier: _Applier = field(default_factory=lambda: _Applier(Path("/client")))
    update_errors: list[Exception] = field(default_factory=list)
    on_read: Any = None
    install_error: Exception | None = None
    rows: list[Manifest] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    discarded: int = 0

    def _prepared(self, via: str, what: object) -> Prepared:
        self.calls.append((via, what))
        if self.on_read:
            self.on_read()
        if self.refusal:
            raise AddonRefusal(self.refusal)
        return Prepared(manifest=_manifest(), staged=cast(Any, _Staging(self)))

    def from_link(self, url: str, **_: object) -> Prepared:
        return self._prepared("link", url)

    def from_folder(self, path: Path) -> Prepared:
        return self._prepared("folder", path)

    def from_zip(self, path: Path, **_: object) -> Prepared:
        return self._prepared("zip", path)

    def replacement_question(self, prepared: Prepared) -> str | None:
        return self.question

    def installed(self) -> list[Manifest]:
        return list(self.rows)

    def install(self, prepared: Prepared, **kwargs: object) -> ApplyReport:
        self.calls.append(("install", kwargs))
        if self.install_error:
            raise self.install_error
        self.rows = [m for m in self.rows if m.id != prepared.manifest.id] + [prepared.manifest]
        return ApplyReport(
            action="install", item_id=prepared.manifest.id, family="mod", done=("Installed pfUI",)
        )

    def update(self, manifest: Manifest, **kwargs: object) -> ApplyReport:
        self.calls.append(
            ("update", manifest.id) if not kwargs else ("update", manifest.id, kwargs)
        )
        if self.update_errors:
            raise self.update_errors.pop(0)
        return ApplyReport(
            action="install", item_id=manifest.id, family="mod", done=("Updated pfUI",)
        )

    def remove(self, manifest: Manifest) -> ApplyReport:
        self.calls.append(("remove", manifest.id))
        self.rows = [m for m in self.rows if m.id != manifest.id]
        return ApplyReport(
            action="remove", item_id=manifest.id, family="mod", done=("Removed pfUI",)
        )


def _answering(view: ControllerView, answer: bool, asked: list[Any]) -> None:
    """Replace the view's yes/no dialog with `answer`, noting what it was asked."""

    def confirm(title: str, question: str, parent: object = None) -> bool:
        asked.append((title, question))
        return answer

    cast(Any, view)._confirm = confirm


def _view(
    tmp_path: Path,
    route: _Route | None = None,
    *,
    game: str = "wow-wotlk",
    link: object = "https://github.com/shagu/pfUI",
    folder: object = Path("/x/pfUI"),
    zipfile: object = Path("/x/pfUI.zip"),
    client: bool = True,
    confirm: bool = True,
) -> tuple[ControllerView, _Route]:
    route = route if route is not None else _Route()
    client_dir = tmp_path / "client"
    if client:
        (client_dir / "Interface").mkdir(parents=True)
    services = ControllerServices.for_entry(
        CATALOG.get(game), tmp_path / game, client_dir if client else None
    )
    services.client_addons = cast(Any, route)
    view = ControllerView(
        CATALOG.get(game),
        services,
        status_poll_ms=0,
        job_runner=run_inline,
        addon_link_asker=lambda _parent, _title: cast("str | None", link),
        addon_folder_asker=lambda _parent, _title: cast("Path | None", folder),
        addon_zip_asker=lambda _parent, _title: cast("Path | None", zipfile),
    )
    _answering(view, confirm, [])
    view.refresh_addon_box()
    return view, route


@pytest.mark.parametrize("game", GAMES)
def test_every_game_has_the_box_with_its_three_presses(
    game: str, qapp: object, tmp_path: Path
) -> None:
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / game, None)
    view = ControllerView(CATALOG.get(game), services, status_poll_ms=0)
    box = view.addon_box
    assert not box.isHidden()
    assert box.title() == "Game add-ons you bring"
    assert [b.text() for b in (box.link_button, box.folder_button, box.zip_button)] == [
        "Add-on from link…",
        "Add-on from folder…",
        "Add-on from zip…",
    ]
    assert all(b.isEnabled() for b in (box.link_button, box.folder_button, box.zip_button))


def test_a_hand_built_services_object_without_the_route_has_no_box(
    qapp: object, tmp_path: Path
) -> None:
    services = ControllerServices.for_entry(CATALOG.get("wow-wotlk"), tmp_path / "s", None)
    services.client_addons = None
    view = ControllerView(CATALOG.get("wow-wotlk"), services, status_poll_ms=0)
    assert view.addon_box.isHidden()


def test_the_link_press_reads_installs_and_lists_the_add_on(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path)
    view.addon_box.link_button.click()

    assert route.calls[0] == ("link", "https://github.com/shagu/pfUI")
    assert route.calls[1][0] == "install"
    assert route.calls[1][1] == {"replacing": False, "replace_existing": False}
    assert "Installed pfUI" in view.module_report.toPlainText()
    assert [view.addon_box.choice.itemData(i) for i in range(view.addon_box.choice.count())] == [
        "pfui"
    ]
    assert not view.addon_box.list_row.isHidden()


def test_the_folder_and_zip_presses_hand_the_chosen_path_on(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path)
    view.addon_box.folder_button.click()
    view.addon_box.zip_button.click()
    assert [c for c in route.calls if c[0] in ("folder", "zip")] == [
        ("folder", Path("/x/pfUI")),
        ("zip", Path("/x/pfUI.zip")),
    ]


def test_a_same_source_add_again_is_a_plain_update_and_asks_nothing(
    qapp: object, tmp_path: Path
) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]))
    asked: list[Any] = []
    _answering(view, True, asked)
    view.addon_box.link_button.click()
    assert asked == []
    assert route.calls[1][1]["replacing"] is False


REPO_QUESTION = "modules/pfui is a checkout of shagu/pfUI, not of fork/pfUI.\n\nReplace it?"


def test_a_checkout_of_another_repository_is_asked_and_the_yes_is_passed_on(
    qapp: object, tmp_path: Path
) -> None:
    route = _Route(rows=[_manifest()], applier=_Applier(Path("/c"), question=REPO_QUESTION))
    view, route = _view(tmp_path, route)
    asked: list[Any] = []
    _answering(view, True, asked)
    view.addon_box.link_button.click()
    assert asked == [("Replace pfui?", REPO_QUESTION)]
    assert route.calls[1][1]["replacing"] is True


def test_a_no_to_the_repository_question_installs_nothing_and_lets_the_staging_go(
    qapp: object, tmp_path: Path
) -> None:
    route = _Route(rows=[_manifest()], applier=_Applier(Path("/c"), question=REPO_QUESTION))
    view, route = _view(tmp_path, route, confirm=False)
    view.addon_box.link_button.click()
    assert [c[0] for c in route.calls] == ["link"]
    assert route.discarded == 1
    assert "cancelled — nothing on this machine was changed" in view.module_report.toPlainText()


@pytest.mark.parametrize("press", ["link_button", "folder_button", "zip_button"])
def test_cancelling_a_dialog_changes_nothing(press: str, qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path, link=None, folder=None, zipfile=None)
    getattr(view.addon_box, press).click()
    assert route.calls == []
    assert view.module_report.toPlainText().endswith(
        "cancelled — nothing on this machine was changed."
    )
    assert view.module_report.toPlainText().startswith("add-on from ")


def test_a_refused_source_says_the_sentence_alone_and_installs_nothing(
    qapp: object, tmp_path: Path
) -> None:
    sentence = f"pfUI is made for Classic (Interface 11500). {NOTHING}"
    view, route = _view(tmp_path, _Route(refusal=sentence))
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.addon_box.link_button.click()

    assert view.module_report.toPlainText() == sentence
    assert failures == [sentence]
    assert [c[0] for c in route.calls] == ["link"]
    assert not view._module_job_running()
    assert view.addon_box.link_button.isEnabled()


def test_an_install_the_applier_refuses_says_it_did_not_finish(
    qapp: object, tmp_path: Path
) -> None:
    sentence = f"An add-on named pfUI is already in this client. {NOTHING}"
    view, route = _view(tmp_path, _Route(install_error=ApplyRefusal(sentence)))
    view.addon_box.link_button.click()
    said = view.module_report.toPlainText()
    assert said.startswith("Add-on from link pfui did not finish:")
    assert sentence in said
    assert not view._module_job_running()


def test_a_replace_question_yes_installs_over_the_players_folder(
    qapp: object, tmp_path: Path
) -> None:
    question = "An add-on named pfUI is already in this client. Replace it?"
    view, route = _view(tmp_path, _Route(question=question), confirm=True)
    asked: list[tuple[str, str]] = []
    _answering(view, True, asked)
    view.addon_box.link_button.click()
    assert asked == [("Replace pfui?", question)]
    assert route.calls[1][1]["replace_existing"] is True


def test_a_replace_question_no_installs_nothing_and_lets_the_staging_go(
    qapp: object, tmp_path: Path
) -> None:
    route = _Route(question="An add-on named pfUI is already in this client. Replace it?")
    view, route = _view(tmp_path, route, confirm=False)
    view.addon_box.link_button.click()
    assert [c[0] for c in route.calls] == ["link"]
    assert "cancelled — nothing on this machine was changed" in view.module_report.toPlainText()
    assert route.discarded == 1


def test_a_press_the_client_check_stops_installs_nothing_and_lets_the_staging_go(
    qapp: object, tmp_path: Path
) -> None:
    view, route = _view(tmp_path, client=False)
    stopped: list[str] = []

    def stop(what: str, manifest: Manifest) -> bool:
        stopped.append(what)
        return True

    cast(Any, view)._stopped_for_the_client = stop
    view.addon_box.link_button.click()
    assert stopped == ["add-on from link"]
    assert [c[0] for c in route.calls] == ["link"]
    assert route.discarded == 1


def test_an_update_the_client_check_stops_updates_nothing(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]))
    stopped: list[str] = []

    def stop(what: str, manifest: Manifest) -> bool:
        stopped.append(what)
        return True

    cast(Any, view)._stopped_for_the_client = stop
    view.addon_box.update_button.click()
    assert stopped == ["update pfui"]
    assert route.calls == []


def test_a_remove_while_a_module_job_runs_is_refused_in_words(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]))
    asked: list[Any] = []
    _answering(view, True, asked)
    view._module_pending = "update mod-x"
    view.remove_addon("pfui")
    assert route.calls == [] and asked == []
    assert "update mod-x" in view.module_report.toPlainText()


def test_an_update_while_a_module_job_runs_is_refused_in_words(
    qapp: object, tmp_path: Path
) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]))
    view._module_pending = "update mod-x"
    view.update_addon("pfui")
    assert route.calls == []
    assert "update mod-x" in view.module_report.toPlainText()


def test_a_rebuild_started_during_the_read_stops_the_install_and_lets_the_staging_go(
    qapp: object, tmp_path: Path
) -> None:
    route = _Route()
    view, route = _view(tmp_path, route)
    route.on_read = lambda: setattr(view, "_busy", True)
    view.addon_box.link_button.click()
    assert [c[0] for c in route.calls] == ["link"]
    assert route.discarded == 1
    assert "Wait" in view.module_report.toPlainText()


def test_a_raise_after_the_read_leaks_no_staging_and_says_so(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path)

    def boom(what: str, manifest: Manifest) -> bool:
        raise RuntimeError("the disk went away")

    cast(Any, view)._stopped_for_the_client = boom
    view.addon_box.link_button.click()
    said = view.module_report.toPlainText()
    assert "the disk went away" in said and NOTHING in said
    assert route.discarded == 1
    assert [c[0] for c in route.calls] == ["link"]
    assert not view._module_job_running()


def test_a_list_that_cannot_be_read_does_not_kill_the_remove_press(
    qapp: object, tmp_path: Path
) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]))

    def broken() -> list[Manifest]:
        raise OSError("unreadable")

    route.installed = broken  # type: ignore[method-assign]
    view.remove_addon("pfui")
    assert route.calls == []
    assert "unreadable" in view.module_report.toPlainText()


def test_an_update_nobody_can_place_asks_and_a_yes_presses_it_again_approved(
    qapp: object, tmp_path: Path
) -> None:
    from yulon.apply import ReleaseDirectionUnknown

    approval = cast(Any, object())
    route = _Route(rows=[_manifest()])
    route.update_errors = [
        ReleaseDirectionUnknown("could not place it", "Update anyway?", approval)
    ]
    view, route = _view(tmp_path, route)
    asked: list[Any] = []
    _answering(view, True, asked)
    view.addon_box.update_button.click()
    assert asked == [("Update pfui without checking?", "Update anyway?")]
    assert route.calls[-1] == ("update", "pfui", {"approved": approval})
    assert "Updated pfUI" in view.module_report.toPlainText()


def test_an_update_nobody_can_place_and_a_no_changes_nothing(qapp: object, tmp_path: Path) -> None:
    from yulon.apply import ReleaseDirectionUnknown

    route = _Route(rows=[_manifest()])
    route.update_errors = [
        ReleaseDirectionUnknown("could not place it", "Update anyway?", cast(Any, 1))
    ]
    view, route = _view(tmp_path, route, confirm=False)
    view.addon_box.update_button.click()
    assert len(route.calls) == 1
    assert "cancelled — nothing on this machine was changed" in view.module_report.toPlainText()


def test_the_note_does_not_promise_what_a_replace_breaks() -> None:
    from yulon.ui.widgets.client_addons_box import ADDON_BOX_NOTE

    assert "never touched" not in ADDON_BOX_NOTE
    assert "replaced only if you say yes" in ADDON_BOX_NOTE and "puts yours back" in ADDON_BOX_NOTE


@pytest.mark.parametrize(("game", "interface"), [("wow-centurion", 30300), ("wow-tortoise", 11200)])
def test_a_client_folder_with_no_interface_folder_refuses_the_add_on_and_writes_nothing(
    game: str, interface: int, qapp: object, tmp_path: Path
) -> None:
    """The reproduced case: the install "succeeded", skipped its step, and listed the add-on."""
    client = tmp_path / "client"
    client.mkdir()
    source = tmp_path / "src" / "pfUI"
    source.mkdir(parents=True)
    (source / "pfUI.toc").write_text(f"## Interface: {interface}\n## Title: pfUI\npfUI.lua\n")
    (source / "pfUI.lua").write_text("x = 1\n")
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / game, client)
    view = ControllerView(
        CATALOG.get(game),
        services,
        status_poll_ms=0,
        job_runner=run_inline,
        addon_folder_asker=lambda _p, _t: source,
    )
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.addon_box.folder_button.click()

    said = view.module_report.toPlainText()
    assert str(client) in said and "Interface" in said and NOTHING in said
    assert failures and failures[0] == said
    assert not (client / "Interface").exists()
    assert view.addon_box.choice.count() == 0
    assert not view._module_job_running()


def test_update_and_remove_press_the_chosen_add_on(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]))
    view.addon_box.update_button.click()
    assert route.calls == [("update", "pfui")]
    assert "Updated pfUI" in view.module_report.toPlainText()
    view.addon_box.remove_button.click()
    assert route.calls[-1] == ("remove", "pfui")
    assert view.addon_box.list_row.isHidden()


def test_remove_declined_removes_nothing(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path, _Route(rows=[_manifest()]), confirm=False)
    asked: list[str] = []
    _answering(view, False, asked)
    view.addon_box.remove_button.click()
    assert route.calls == []
    assert len(route.rows) == 1
    assert "takes back" in asked[0][1] and "WTF" in asked[0][1]
    assert "cancelled" in view.module_report.toPlainText()


def test_a_press_while_a_module_job_runs_is_refused_in_words(qapp: object, tmp_path: Path) -> None:
    view, route = _view(tmp_path)
    view._module_pending = "update mod-x"
    view.addon_box.link_button.click()
    assert route.calls == []
    said = view.module_report.toPlainText()
    assert "update mod-x" in said and "Wait" in said and NOTHING in said


def test_the_busy_lock_greys_the_box_and_gives_it_back(qapp: object, tmp_path: Path) -> None:
    view, _ = _view(tmp_path, _Route(rows=[_manifest()]))
    box = view.addon_box
    pressed = (
        box.link_button,
        box.folder_button,
        box.zip_button,
        box.update_button,
        box.remove_button,
    )
    view._set_busy(True, "Start")
    assert not any(b.isEnabled() for b in pressed)
    view._set_busy(False)
    assert all(b.isEnabled() for b in pressed)


def test_the_server_tab_points_at_the_box(qapp: object, tmp_path: Path) -> None:
    view, _ = _view(tmp_path)
    assert view.addon_pointer.text() == cv.ADDON_POINTER
    assert "Modules tab" in cv.ADDON_POINTER
    assert not view.addon_pointer.isHidden() or not view.isVisible()
