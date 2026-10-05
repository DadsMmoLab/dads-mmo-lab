"""T243: the long questions fit the screen, keep their buttons, and can be read to the end.

The Rebuild question grew taller than a 1920×1080 screen once T223, T224 and
T217 had each added what a player needs to know (sitting B, yulon-win11,
2026-10-05), so its Yes and No could be below the bottom of the screen. The
rule now, for every long question this app asks: the dialog fits the screen it
opens on -- a 960×640 handheld, a Steam Deck's 1280×800, a 1920×1080 desktop --
the buttons are always on screen, and the words that do not fit scroll inside
the dialog, where the mouse, the Tab key and the pad can all reach them.

Every case is a real dialog on a real (offscreen) screen of that size, in a
child process (`tests/dialog_fit_probe.py` says why), asked through the
function the app asks it with and answered by a click on its own decline
button. The texts are the real composed questions at their longest: Tortoise,
with a kept build (T224), the Docker Hub note (T223) and the database copy
(T217), and all three together.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.slow

PYLAUNCHER = Path(__file__).resolve().parents[1]

SCREENS = {
    "handheld-960x640": (960, 640),
    "steam-deck-1280x800": (1280, 800),
    "desktop-1920x1080": (1920, 1080),
    # A 1366×768 laptop at Windows' 125 %: the one size here where `QMessageBox`'s
    # own width limit (screen - 480 above 1024) is narrower than the question's column.
    "laptop-1366x768-at-125": (1093, 614),
}

DIALOGS = (
    "rebuild",
    "update-to-latest",
    "return-to-pin",
    "repair",
    "longest",
    "longest-three-way",
)

DECLINED = {
    "rebuild": "False",
    "return-to-pin": "False",
    "repair": "False",
    "longest": "False",
    "update-to-latest": "'cancel'",
    "longest-three-way": "'cancel'",
}
"""What each function answers when its decline button is clicked."""

_probed: dict[str, dict[str, Any]] = {}


def _probe(screen: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The probe's measurements on `screen`, run once per screen for the whole module."""
    if screen not in _probed:
        width, height = SCREENS[screen]
        config = tmp_path_factory.mktemp("screen") / "screen.json"
        config.write_text(
            json.dumps(
                {
                    "screens": [
                        {
                            "name": screen,
                            "x": 0,
                            "y": 0,
                            "width": width,
                            "height": height,
                            "logicalDpi": 96,
                            "logicalBaseDpi": 96,
                            "dpr": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": f"offscreen:configfile={config}",
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(PYLAUNCHER), os.environ.get("PYTHONPATH", "")) if p
            ),
        }
        done = subprocess.run(
            [sys.executable, "-m", "tests.dialog_fit_probe"],
            cwd=PYLAUNCHER,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert done.returncode == 0, f"the probe failed:\n{done.stdout}\n{done.stderr}"
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        assert lines, f"the probe printed no result:\n{done.stdout}\n{done.stderr}"
        _probed[screen] = json.loads(lines[-1])
    return _probed[screen]


@pytest.fixture(params=sorted(SCREENS))
def screen(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(params=DIALOGS)
def seen(
    request: pytest.FixtureRequest, screen: str, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    result = _probe(screen, tmp_path_factory)[request.param]
    assert "error" not in result, result["error"]
    assert result.get("screen") == [
        0,
        0,
        *SCREENS[screen],
    ], f"the probe ran on {result.get('screen')}, not the {screen} screen it was given"
    return dict(result, name=request.param)


def test_the_dialog_fits_the_screen(seen: dict[str, Any]) -> None:
    assert seen[
        "frame_inside_screen"
    ], f"the dialog {seen['frame']} reaches past the screen {seen['screen']}"


def test_every_button_is_inside_the_dialog_and_on_screen(seen: dict[str, Any]) -> None:
    assert len(seen["buttons"]) >= 2, seen["buttons"]
    off = {
        label: where
        for label, where in seen["buttons"].items()
        if not (where["inside_box"] and where["fully_on_screen"])
    }
    assert off == {}, f"buttons cut off or outside the dialog: {off}"


def test_the_whole_question_is_in_the_dialog(seen: dict[str, Any]) -> None:
    """Every word of the real composed text, shown in the dialog's labels, and nothing lost."""
    assert seen["question"] == seen["text"], "the dialog's question is not the text it was given"
    assert seen["words"] == seen["text"], "the words the dialog shows are not the whole question"


def test_every_part_of_the_question_can_be_scrolled_into_view(seen: dict[str, Any]) -> None:
    assert seen["labels_reachable_by_scrolling"], "no words found in the dialog"
    assert all(seen["labels_reachable_by_scrolling"]), seen["labels_reachable_by_scrolling"]
    assert seen["last_label_shown_scrolled_to_the_end"], "the end of the question never shows"


def test_the_decline_button_has_the_focus_when_it_opens(seen: dict[str, Any]) -> None:
    """Enter declines, as it did before (T157, `rebuild_server()`'s rule)."""
    assert seen["first_focus"] == f"button:{seen['decline']}"


def test_the_tab_key_reaches_every_button(seen: dict[str, Any]) -> None:
    assert seen["tab_reaches"] == sorted(seen["buttons"])


def test_the_pad_reaches_every_button_and_stays_in_the_dialog(seen: dict[str, Any]) -> None:
    assert not seen["pad_left_the_dialog"]
    assert seen["pad_reaches"] == sorted(seen["buttons"])
    assert seen["pad_down_ends_on"].startswith(
        "button:"
    ), f"Down to the end left the focus on {seen['pad_down_ends_on']}, not a button"


def test_the_pad_can_read_every_part_the_screen_does_not_show_at_once(
    seen: dict[str, Any],
) -> None:
    """The pad cannot scroll; it stops on what it can focus. A part that does not show when
    the dialog opens has to be a stop the D-pad reaches, scrolled into view as it lands."""
    unread = [
        i
        for i, (shown, read) in enumerate(
            zip(seen["labels_shown_at_open"], seen["pad_read_labels"], strict=True)
        )
        if not shown and not read
    ]
    assert unread == [], f"parts of the question the pad never reaches: {unread}"


def test_the_decline_button_answers_no(seen: dict[str, Any]) -> None:
    assert seen["answer"] == DECLINED[seen["name"]]


def test_the_longest_question_is_longer_than_the_desktop_screen_shows_at_once(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The cases above are not vacuous: on every screen, even 1920×1080, the longest text
    has parts that do not show when the dialog opens, so scrolling is what the tests read."""
    for screen in SCREENS:
        longest = _probe(screen, tmp_path_factory)["longest"]
        assert "error" not in longest, longest["error"]
        assert not all(longest["labels_shown_at_open"]), screen
