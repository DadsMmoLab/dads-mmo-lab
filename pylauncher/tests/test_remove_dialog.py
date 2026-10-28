"""T695: one "Remove this server…" dialog, with the three removal routes behind it.

The Danger zone's three removal presses ("Stop and remove containers…",
"Remove from Yu'lon…", "Uninstall…") become one "Remove this server…" press
opening a dialog with three radios and an "Advanced" fold holding
"Stop and remove containers". The dialog is reached through the
`ControllerView._ask_removal` seam (default builds the dialog) so tests
answer it without a modal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import _Ps  # noqa: E402
from yulon import runner  # noqa: E402
from yulon.ui import controller_view as controller_view_module  # noqa: E402
from yulon.ui.controller_view import ControllerView  # noqa: E402
from yulon.ui.widgets.remove_dialog import RemovalChoice  # noqa: E402


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the view's background jobs synchronously, as test_controller_view does."""
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def test_one_remove_press_offers_three_choices_and_off_the_list_forgets(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """One press, three choices; "off this list" reaches `forget_install()`'s signal."""
    from tests.test_controller_view import _FakeUninstall, _uninstall_view

    view = _uninstall_view(ps, tmp_path, _FakeUninstall(tmp_path))
    seen: list[RemovalChoice | None] = []
    seen_choices: list[tuple[object, ...]] = []
    view._ask_removal = lambda offered: seen_choices.append(offered) or RemovalChoice.OFF_THE_LIST  # type: ignore[method-assign]
    seen_signal: list[tuple[str, object]] = []
    view.remove_requested.connect(lambda game, folder: seen_signal.append((game, folder)))

    view.remove_server()

    assert seen == []
    (offered,) = seen_choices
    assert set(offered) == {
        RemovalChoice.OFF_THE_LIST,
        RemovalChoice.KEEP_CHARACTERS,
        RemovalChoice.EVERYTHING,
    }
    assert seen_signal == [("wow-wotlk", tmp_path)]


def test_a_game_without_uninstall_offers_only_off_the_list_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Tortoise has no uninstall seam: the dialog offers only "off this list"."""
    from tests.test_controller_view import TORTOISE, _services

    view = ControllerView(TORTOISE, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.services.uninstall is None
    seen_choices: list[tuple[object, ...]] = []
    view._ask_removal = lambda offered: seen_choices.append(offered) or RemovalChoice.OFF_THE_LIST  # type: ignore[method-assign]
    seen_signal: list[tuple[str, object]] = []
    view.remove_requested.connect(lambda game, folder: seen_signal.append((game, folder)))

    view.remove_server()

    (offered,) = seen_choices
    assert list(offered) == [RemovalChoice.OFF_THE_LIST]
    assert seen_signal == [("wow-tortoise", tmp_path)]
    assert "Uninstall is not offered" in view.uninstall_absent_label.text()


def test_keep_my_characters_opens_the_plan_with_the_volume_kept(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The "keep" choice ticks the box before `show_uninstall_plan()`, so the plan keeps."""
    from tests.test_controller_view import _FakeUninstall, _uninstall_view

    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    view._ask_removal = lambda _offered: RemovalChoice.KEEP_CHARACTERS  # type: ignore[method-assign]

    view.remove_server()

    assert fake.plans == 1
    assert view.keep_characters_check.isChecked() is True


def test_everything_opens_the_plan_with_the_volume_not_kept(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The "everything" choice unticks the box, so the plan deletes the characters too."""
    from tests.test_controller_view import _FakeUninstall, _uninstall_view

    fake = _FakeUninstall(tmp_path)
    view = _uninstall_view(ps, tmp_path, fake)
    view.keep_characters_check.setChecked(True)
    view._ask_removal = lambda _offered: RemovalChoice.EVERYTHING  # type: ignore[method-assign]

    view.remove_server()

    assert fake.plans == 1
    assert view.keep_characters_check.isChecked() is False


def test_the_way_out_lights_the_remove_press_and_preselects_off_the_list(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T95's highlight moves to the new press, and the dialog opens preselected."""
    from tests.test_controller_view import TORTOISE, _services, _watch_remove

    view = ControllerView(TORTOISE, _services(ps, tmp_path, []), status_poll_ms=0)
    _watch_remove(view, result=False)

    view._remove_containers_now()
    assert view._nothing_to_remove is True
    assert bool(view.remove_server_button.property("primary")) is True

    preselected: list[RemovalChoice] = []

    def spy(self: ControllerView, offered: tuple[RemovalChoice, ...]) -> RemovalChoice | None:
        preselected.append(self._removal_preselect)
        return None

    view._ask_removal = spy.__get__(view, ControllerView)  # type: ignore[method-assign]
    view.remove_server()
    assert preselected == [RemovalChoice.OFF_THE_LIST]


def test_advanced_runs_the_remove_without_a_second_press(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Choosing Advanced in the dialog runs the removal at once: no arming path."""
    from tests.test_controller_view import WOTLK, _services, _watch_remove

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    calls = _watch_remove(view)
    view._ask_removal = lambda _offered: RemovalChoice.REMOVE_CONTAINERS  # type: ignore[method-assign]

    view.remove_server()

    assert calls == [1]


def test_dialog_cancel_forgets_nothing_and_removes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Cancel (None) leaves every route untouched."""
    from tests.test_controller_view import _FakeUninstall, _uninstall_view, _watch_remove

    view = _uninstall_view(ps, tmp_path, _FakeUninstall(tmp_path))
    calls = _watch_remove(view)
    view._ask_removal = lambda _offered: None  # type: ignore[method-assign]
    seen_signal: list[tuple[str, object]] = []
    view.remove_requested.connect(lambda game, folder: seen_signal.append((game, folder)))

    view.remove_server()

    assert calls == []
    assert seen_signal == []


def test_the_dialog_offers_three_choices_with_off_the_list_first(
    qapp: object,
) -> None:
    """The dialog itself: three radios, off-the-list selected, Advanced folded."""
    from yulon.ui.widgets.remove_dialog import RemoveDialog

    dialog = RemoveDialog(None, uninstall_available=True, preselect=RemovalChoice.OFF_THE_LIST)
    assert dialog.choice() == RemovalChoice.OFF_THE_LIST
    dialog.keep_radio.setChecked(True)
    assert dialog.choice() == RemovalChoice.KEEP_CHARACTERS
    dialog.everything_radio.setChecked(True)
    assert dialog.choice() == RemovalChoice.EVERYTHING


def test_the_dialog_without_uninstall_offers_only_off_the_list(
    qapp: object,
) -> None:
    """No uninstall seam: only off-the-list, plus the plain UNINSTALL_ABSENT line."""
    from yulon.ui.widgets.remove_dialog import RemoveDialog

    dialog = RemoveDialog(None, uninstall_available=False, preselect=RemovalChoice.OFF_THE_LIST)
    assert dialog.keep_radio.isHidden() and dialog.everything_radio.isHidden()
    assert "Uninstall is not offered" in dialog.absent_label.text()
