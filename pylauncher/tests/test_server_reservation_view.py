"""T568: the Server tab's two answers to another Yu'lon's reservation.

Stop asks once (who, which press, since when), then stops anyway: the holder's reservation
is removed by the id it was read with, and the Stop runs as always. A leftover reservation
of this user's own is cleared by [Clear it], only when this Yu'lon holds the single-instance
lock; another user's leftover shows its command only. Fixtures are `test_controller_view`'s.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_controller_view import WOTLK, _Ps, _services, ps  # noqa: F401
from yulon import docker, forgetting
from yulon.ui import controller_view as controller_view_module
from yulon.ui import single_instance
from yulon.ui.controller_view import ControllerView

HOLDER = docker.ServerHolder(
    "yulon-busy-abc",
    "container-id-1",
    press="Update the server to latest…",
    who="pk@THEIR-PC (Windows)",
    created="2026-10-09T12:00:00Z",
)


def _refused(holder: docker.ServerHolder) -> docker.ServerReserved:
    return docker.ServerReserved("Another Yu'lon is working on WoW. Nothing was changed.", holder)


class _Asked:
    """What the dialog was shown, and the answer it gives."""

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.texts: list[str] = []

    def __call__(
        self, parent: object, title: str, text: str, yes: str, save: str | None = None
    ) -> str | None:
        self.texts.append(text)
        assert yes == controller_view_module.STOP_OVER_ANOTHER_BUTTON
        return self.answer


@pytest.fixture
def view(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> ControllerView:
    made = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    # Jobs run in line: the worker half is what is under test, not the thread.
    def run_in_line(fn: Any, done: Any, failed: Any) -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - what the runner hands `failed`
            failed(exc)
        else:
            done(result)

    monkeypatch.setattr(made, "_run", run_in_line)
    return made


def _stopping(view: ControllerView, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(
        docker, "end_reservation", lambda holder: events.append(f"end:{holder.container}") or True
    )
    monkeypatch.setattr(view.services.controller, "stop", lambda: events.append("stop") or True)
    return events


def test_stop_asks_once_naming_who_what_and_since_when(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _Asked("yes")
    monkeypatch.setattr(controller_view_module, "_ask_with", asked)
    events = _stopping(view, monkeypatch)

    view._stop_failed(_refused(HOLDER))

    (text,) = asked.texts
    assert "Update the server to latest…" in text and "pk@THEIR-PC (Windows)" in text
    assert "Stopping now ends that too" in text and "started" in text
    assert events == ["end:container-id-1", "stop"], "removed before stopping, by the shown id"


def test_stop_anyway_not_chosen_leaves_the_refusal_and_removes_nothing(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(controller_view_module, "_ask_with", _Asked(None))
    events = _stopping(view, monkeypatch)

    view._stop_failed(_refused(HOLDER))

    assert events == []
    assert "Another Yu'lon is working on WoW" in view.problem_label.text()


def test_stop_asks_only_once_per_press(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second refusal in the same press (a newer holder) is shown, not asked again."""
    asked = _Asked("yes")
    monkeypatch.setattr(controller_view_module, "_ask_with", asked)
    _stopping(view, monkeypatch)
    monkeypatch.setattr(
        view.services.controller, "stop", lambda: (_ for _ in ()).throw(_refused(HOLDER))
    )

    view._stop_failed(_refused(HOLDER))

    assert len(asked.texts) == 1
    assert "Another Yu'lon is working on WoW" in view.problem_label.text()


@pytest.mark.parametrize(
    "holder",
    [
        docker.ServerHolder("yulon-busy-abc", "id", here=True),
        docker.ServerHolder("yulon-busy-abc", "", known=False),
    ],
    ids=["this process's own", "docker would not name it"],
)
def test_stop_does_not_ask_about_a_holder_it_cannot_remove_by_id(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch, holder: docker.ServerHolder
) -> None:
    asked = _Asked("yes")
    monkeypatch.setattr(controller_view_module, "_ask_with", asked)
    events = _stopping(view, monkeypatch)

    view._stop_failed(_refused(holder))

    assert asked.texts == [] and events == []


def test_a_restart_refused_by_another_yulon_does_not_offer_to_stop(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _Asked("yes")
    monkeypatch.setattr(controller_view_module, "_ask_with", asked)
    events = _stopping(view, monkeypatch)

    view._restart_failed(_refused(HOLDER))

    assert asked.texts == [] and events == []


# ------------------------------------------------------------------ [Clear it]

OWN = docker.ServerHolder("yulon-busy-abc", "own-id", press="Rebuild", ours=True)


def _lock(monkeypatch: pytest.MonkeyPatch, held: bool) -> None:
    monkeypatch.setattr(single_instance, "holds_the_lock", lambda: held)


def test_clear_it_is_offered_for_this_users_own_leftover_when_the_lock_is_held(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lock(monkeypatch, True)
    view._start_failed(_refused(OWN))
    assert not view.clear_reservation_button.isHidden()

    cleared: list[str] = []
    monkeypatch.setattr(
        docker, "end_reservation", lambda holder: cleared.append(holder.container) or True
    )
    view.clear_the_leftover_reservation()

    assert cleared == ["own-id"]
    assert view.clear_reservation_button.isHidden()
    assert view.problem_label.text() == controller_view_module.RESERVATION_CLEARED


@pytest.mark.parametrize(
    ("holder", "lock", "why"),
    [
        (docker.ServerHolder("n", "id", ours=False), True, "another user's"),
        (docker.ServerHolder("n", "id", ours=True), False, "no single-instance lock"),
        (docker.ServerHolder("n", "id", ours=True, here=True), True, "this process's own"),
        (docker.ServerHolder("n", "", ours=True), True, "no container id"),
    ],
)
def test_clear_it_is_not_offered_otherwise(
    view: ControllerView,
    monkeypatch: pytest.MonkeyPatch,
    holder: docker.ServerHolder,
    lock: bool,
    why: str,
) -> None:
    _lock(monkeypatch, lock)
    view._start_failed(_refused(holder))
    assert view.clear_reservation_button.isHidden(), why
    removed: list[Any] = []
    monkeypatch.setattr(docker, "end_reservation", lambda h: removed.append(h) or True)
    view.clear_the_leftover_reservation()
    assert removed == [], why


# ------------------------------------------------------------------ Stop anyway always stops


def test_stop_anyway_stops_even_when_the_holders_reservation_would_not_be_removed(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex adversarial review: a removal that fails left the Stop refused again by the same
    reservation, with the question already spent. The one confirmed override stops regardless.

    Mutation this catches: `_stop_over` stopping under the ordinary reservation whatever
    `end_reservation()` answered.
    """
    from tests.test_server_reservation import ran, stop_staged

    ran.clear()
    monkeypatch.setattr(controller_view_module, "_ask_with", _Asked("yes"))
    monkeypatch.setattr(docker, "end_reservation", lambda holder: False)
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    monkeypatch.setattr(docker, "server_claim", _always_held)  # the holder is still there
    monkeypatch.setattr(
        view.services.controller, "stop", lambda: stop_staged(_SPEC, tmp_path) or True
    )

    view._stop_failed(_refused(HOLDER))

    assert ran == ["stop"], "the confirmed Stop did not stop"


def test_stop_anyway_stops_when_another_yulon_took_the_server_after_the_removal(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: the holder was removed, a newcomer reserved before the Stop, and the
    confirmed Stop (asked once) was refused. It falls back to the one unreserved Stop.

    Mutation this catches: no fallback for a `ServerReserved` after a successful removal.
    """
    from tests.test_server_reservation import ran, stop_staged

    ran.clear()
    monkeypatch.setattr(controller_view_module, "_ask_with", _Asked("yes"))
    monkeypatch.setattr(docker, "end_reservation", lambda holder: True)
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    monkeypatch.setattr(docker, "server_claim", _always_held)  # a newcomer holds it again
    monkeypatch.setattr(
        view.services.controller, "stop", lambda: stop_staged(_SPEC, tmp_path) or True
    )

    view._stop_failed(_refused(HOLDER))

    assert ran == ["stop"]


def test_a_later_stop_is_not_unreserved(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The override is for that one Stop: the next ordinary Stop meets the holder as before."""
    from tests.test_server_reservation import ran, stop_staged

    ran.clear()
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    monkeypatch.setattr(docker, "server_claim", _always_held)
    with docker.stopping_regardless():
        stop_staged(_SPEC, tmp_path)
    with pytest.raises(docker.ServerReserved):
        stop_staged(_SPEC, tmp_path)
    assert ran == ["stop"]


_SPEC = docker.ContainerSpec(db="d", auth="a", world="w", ports=(1,))


@contextmanager
def _always_held(*_a: Any, **_kw: Any) -> Iterator[None]:
    raise docker.ServerReserved("Another Yu'lon is working on WoW.", HOLDER)
    yield


@pytest.mark.parametrize(
    ("press", "said"),
    [
        (forgetting.PRESS_RESTORE, "left half-loaded"),
        (forgetting.PRESS_BACKUP, "left incomplete"),
    ],
)
def test_stop_anyway_over_a_backup_or_restore_says_what_it_would_leave(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch, press: str, said: str
) -> None:
    """Codex review: a Restore's load cannot be cancelled, so the player is told before the Yes."""
    asked = _Asked(None)
    monkeypatch.setattr(controller_view_module, "_ask_with", asked)
    holder = docker.ServerHolder("yulon-busy-abc", "id", press=press, who="pk@PC (WSL)")

    view._stop_failed(_refused(holder))

    (text,) = asked.texts
    assert said in text, text
    assert "Stopping now ends that too" in text


def test_stop_anyway_over_an_ordinary_press_adds_nothing_about_the_databases(
    view: ControllerView, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _Asked(None)
    monkeypatch.setattr(controller_view_module, "_ask_with", asked)
    view._stop_failed(_refused(HOLDER))
    (text,) = asked.texts
    assert "half-loaded" not in text and "incomplete" not in text
