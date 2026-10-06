"""T388: the main window fits the screen it opens on, and every main tab can be reached.

Seen on yulon-win11-gate (T243's live test, 2026-10-05): at 1280×800 the main
window opened 1296×820 and at 800×600 it opened 976×679, edges off the screen.
The first was the size it opens at, 1280×800, with a frame round it on a screen
whose working area is 1280×752 -- Windows stopped it at the screen plus its
frame. The second was its 960×640 floor, which no 800×600 screen can hold.

The rule now: the window, frame and all, is inside the screen's free area when
it opens and when it is moved to another screen; the 960×640 floor holds where
the screen has room for it, and where it has not, the window is as large as the
screen and its contents scroll inside it. A Steam Deck is the case that matters:
1280×800 in Game Mode, 1280×752-ish on the desktop with a panel.

Every case is the real window from `main.build_window()` on a real (offscreen)
screen of that size, in a child process (`tests/main_window_fit_probe.py` says
why and how a control is counted as reachable).
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

DEFAULT_SIZE = [1280, 800]
FLOOR = [960, 640]
FRAME = 2
"""The offscreen plugin's frame round a top-level window, each side."""

SCREENS = {
    "steam-deck-1280x800": (1280, 800),
    # 1280×800 less Windows' 48 px taskbar: the working area T243 measured.
    "working-area-1280x752": (1280, 752),
    "xga-1024x768": (1024, 768),
    "svga-800x600": (800, 600),
    "desktop-1920x1080": (1920, 1080),
}

_probed: dict[str, dict[str, Any]] = {}


def _screen(name: str, x: int, width: int, height: int) -> dict[str, Any]:
    return {
        "name": name,
        "x": x,
        "y": 0,
        "width": width,
        "height": height,
        "logicalDpi": 96,
        "logicalBaseDpi": 96,
        "dpr": 1,
    }


def _probe(
    key: str, screens: list[dict[str, Any]], mode: str, tmp: pytest.TempPathFactory
) -> dict[str, Any]:
    """The probe's measurements, run once per key for the whole module."""
    if key not in _probed:
        where = tmp.mktemp("screen")
        config = where / "screen.json"
        config.write_text(json.dumps({"screens": screens}), encoding="utf-8")
        data = where / "data"
        data.mkdir()
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": f"offscreen:configfile={config}",
            # The probe's own config dir: the log file and the instance lock land here.
            "XDG_DATA_HOME": str(data),
            "APPDATA": str(data),
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(PYLAUNCHER), os.environ.get("PYTHONPATH", "")) if p
            ),
        }
        done = subprocess.run(
            [sys.executable, "-m", "tests.main_window_fit_probe", mode],
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
        _probed[key] = json.loads(lines[-1])
    return _probed[key]


@pytest.fixture(params=sorted(SCREENS))
def opened(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Any:
    name = str(request.param)
    width, height = SCREENS[name]
    seen = _probe(name, [_screen(name, 0, width, height)], "open", tmp_path_factory)
    return name, seen["opened"]


def _the_walk_saw_every_tab(seen: dict[str, Any]) -> None:
    """Not a pass by default: the walk reached all three main tabs and the server's sub-tabs."""
    assert seen["tabs"] == ["Catalog", "Logs", "WotLK"], seen["tabs"]
    walked = set(seen["checked_tabs"])
    assert {"Catalog", "Logs"} <= walked, walked
    assert len([t for t in walked if t.startswith("WotLK / ")]) >= 3, walked
    assert seen["checked"] >= 40, seen["checked"]


def test_the_window_opens_inside_the_screen(opened: Any) -> None:
    name, seen = opened
    assert seen["inside"], (
        f"{name}: the window's frame {seen['frame']} is not inside the screen's free area "
        f"{seen['available']}"
    )


def test_every_main_tab_can_be_reached(opened: Any) -> None:
    name, seen = opened
    _the_walk_saw_every_tab(seen)
    assert seen["unreachable"] == [], f"{name}: off screen after scrolling to them: " + "\n".join(
        seen["unreachable"]
    )


def test_the_floor_gives_only_where_the_screen_has_no_room_for_it(opened: Any) -> None:
    """Above the screen, a floor nobody can reach; at room for it, the 960×640 T85 measured."""
    name, seen = opened
    _x, _y, width, height = seen["available"]
    room = [width - 2 * FRAME, height - 2 * FRAME]
    assert seen["minimum"] == [min(FLOOR[0], room[0]), min(FLOOR[1], room[1])], (name, seen)


def test_a_screen_with_room_opens_the_window_at_its_own_size(opened: Any) -> None:
    """Fitting is not shrinking: where 1280×800 and its frame fit, that is the size it opens at."""
    name, seen = opened
    _x, _y, width, height = seen["available"]
    if width < DEFAULT_SIZE[0] + 2 * FRAME or height < DEFAULT_SIZE[1] + 2 * FRAME:
        pytest.skip(f"{name} has no room for {DEFAULT_SIZE} and a frame")
    assert seen["client"][2:] == DEFAULT_SIZE, (name, seen)


@pytest.fixture(scope="module")
def moved(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The window opened on a 1920×1080 monitor, dragged onto an 800×600 one, and back."""
    screens = [_screen("big", 0, 1920, 1080), _screen("small", 1920, 800, 600)]
    return _probe("two-screens", screens, "move", tmp_path_factory)


def test_moved_onto_a_smaller_screen_the_window_fits_it(moved: dict[str, Any]) -> None:
    seen = moved["moved"]
    assert seen["screen"] == "small", seen
    assert seen["inside"], f"frame {seen['frame']} not inside {seen['available']}"
    _the_walk_saw_every_tab(seen)
    assert seen["unreachable"] == [], "\n".join(seen["unreachable"])


def test_moved_back_the_floor_is_960x640_again(moved: dict[str, Any]) -> None:
    assert moved["opened"]["minimum"] == FLOOR, moved["opened"]
    assert moved["moved"]["minimum"] == [800 - 2 * FRAME, 600 - 2 * FRAME], moved["moved"]
    assert moved["back"]["screen"] == "big", moved["back"]
    assert moved["back"]["minimum"] == FLOOR, moved["back"]


def test_the_contents_keep_their_floor_and_scroll_inside_a_window_below_it(opened: Any) -> None:
    """Where the window is smaller than 960×640 the contents are not squeezed: they scroll."""
    name, seen = opened
    assert seen["scrolls"], name
    assert seen["content_minimum"] == FLOOR, (name, seen)
