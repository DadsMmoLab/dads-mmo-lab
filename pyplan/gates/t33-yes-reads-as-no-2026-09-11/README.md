# T33 — a Yes on a question dialog reads as No, reproduced on two boxes

The app's own `_qt_suggestion_asker()` (`pylauncher/yulon/ui/catalog_view.py`) was called on each box with a `QTimer` pressing the dialog's real button, through `live_probe.py` in this folder. Each capture opens with the box's `date -Is`.

| Capture | Box | Platform plugin | Python / PySide6 | Tree | Pressed Yes | Pressed No |
|---|---|---|---|---|---|---|
| `01-yulon-arch-xcb.txt` | `yulon-arch`, display `:0` | xcb (a real window) | 3.13.15 / 6.11.2 | `~/y8` at `124c9a7` | `False` | `False` |
| `02-m910q-offscreen.txt` | `m910q`, the gate venv | offscreen | 3.11.15 / 6.11.2 | `~/dads-mmo-lab` at `a13a5def` | `False` | `False` |

The fix (`hand-t33` at `9229baf3`) was not re-probed by hand on a box: the branch's own real-dialog test (`tests/test_catalog_view.py::test_a_real_yes_on_the_suggestion_dialog_reads_as_yes`, the same timer-click against a real `QMessageBox`) failed on the old code with `assert False is True` and ran green on m910q inside the `--checks` gate (4205 passed), which is the same machine and venv as capture `02`.

Both boxes: the asker returns `False` whichever button is pressed, so `start_install()` opens the folder picker after a Yes — the owner's report on Windows, 2026-09-11 22:06. The cause is the static `QMessageBox.question()` returning `int` on this PySide6 (the laptop probe `static_probe.py`: `16384`, `type=int`, `is Yes=False`, `== Yes=True`), compared with `is` at six sites; the ticket lists them.
