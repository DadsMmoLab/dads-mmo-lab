"""T44 item 1's measurement: the Modules tab's reload time, before and after.

BEFORE is the implementation item 1 forbids -- a `git log -1` per installed
row INSIDE `reload_modules()`. It is produced here by making `VersionCache.
known()` read (which is mutation V1), so "before" is this tab with the naive
version line in it rather than a different program.

AFTER is what shipped: the reload hands the builder only what is cached, and
the reads happen one event-loop turn at a time afterwards.

The version reader is a REAL `git log -1` through `subprocess`, not through
`yulon.runner` -- the harness fakes `runner.run` for Docker, and a faked git
would measure nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.getcwd())

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.conftest import pump_until  # noqa: E402
from tests.test_controller_view import WOTLK, _Ps, _services  # noqa: E402
from yulon import runner  # noqa: E402
from yulon.controller_wow_wotlk import modules as wotlk_modules  # noqa: E402
from yulon.ui.controller_view import ControllerView  # noqa: E402
from yulon.ui.widgets import modules_panel as mp  # noqa: E402

REPEATS = 5


def real_head_version(path: Path) -> str | None:
    if not (path / ".git").is_dir():
        return None
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%h %cs"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    parts = proc.stdout.strip().split()
    return " · ".join(parts) if len(parts) == 2 else None


def make_clones(server_dir: Path, ids: list[str]) -> None:
    for item_id in ids:
        dest = server_dir / "modules" / item_id
        dest.mkdir(parents=True, exist_ok=True)
        for argv in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "t@example.invalid"],
            ["git", "config", "user.name", "T"],
            ["git", "commit", "-q", "--allow-empty", "-m", "one"],
        ):
            subprocess.run(argv, cwd=dest, check=True, capture_output=True)


def build(server_dir: Path, ids: list[str]) -> ControllerView:
    ps = _Ps()
    runner.run = ps  # type: ignore[assignment]
    services = _services(ps, server_dir, [])
    object.__setattr__(services, "installed_modules", lambda: {"module": frozenset(ids)})
    object.__setattr__(services, "module_version", real_head_version)
    return ControllerView(WOTLK, services, status_poll_ms=0)


def timed(fn: object) -> float:
    best = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        fn()  # type: ignore[operator]
        best.append((time.perf_counter() - start) * 1000.0)
    best.sort()
    return best[len(best) // 2]


def measure(count: int, root: Path) -> None:
    store = wotlk_modules.store()
    catalog = [m.id for m in store.load_all("module")]
    ids = (catalog * ((count // len(catalog)) + 1))[:count]
    ids = list(dict.fromkeys(ids))
    while len(ids) < count:  # the shipped catalog is 21 modules; never needed above that
        ids.append(f"mod-filler-{len(ids)}")
    server_dir = root / f"n{count}"
    server_dir.mkdir(parents=True, exist_ok=True)
    make_clones(server_dir, ids)

    # --- BEFORE: the read happens inside the reload
    original_known = mp.VersionCache.known
    mp.VersionCache.known = mp.VersionCache.fill  # type: ignore[method-assign]
    view = build(server_dir, ids)

    def before() -> None:
        view._versions.clear()
        view.reload_modules()

    before_ms = timed(before)
    mp.VersionCache.known = original_known  # type: ignore[method-assign]
    view.deleteLater()

    # --- AFTER: the reload reads nothing; the fill follows it
    view2 = build(server_dir, ids)
    pump_until(lambda: not view2._filling_versions, "fill")

    def cold() -> None:
        view2._versions.clear()
        view2._filling_versions = False
        view2.reload_modules()
        # the paint only; the fill is scheduled and not run here

    cold_ms = timed(cold)

    view2._versions.clear()
    view2._filling_versions = False
    view2.reload_modules()
    fill_start = time.perf_counter()
    pump_until(lambda: not view2._filling_versions, "fill")
    fill_ms = (time.perf_counter() - fill_start) * 1000.0

    def warm() -> None:
        view2.reload_modules()

    warm_ms = timed(warm)
    view2.deleteLater()

    raw_ms = timed(lambda: [real_head_version(server_dir / "modules" / i) for i in ids])
    print(
        f"{count:3d} installed | {count} raw `git log -1` {raw_ms:8.1f} ms"
        f" | before(inline git log) {before_ms:8.1f} ms"
        f" | after: first paint {cold_ms:8.1f} ms"
        f" | fill after the paint {fill_ms:8.1f} ms"
        f" | later reload {warm_ms:8.1f} ms"
    )


def main() -> None:
    QApplication.instance() or QApplication([])
    root = Path(sys.argv[1])
    for count in (2, 20):
        measure(count, root)


if __name__ == "__main__":
    main()
