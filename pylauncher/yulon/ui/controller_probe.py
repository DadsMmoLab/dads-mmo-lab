"""Temporary controller-probe panel for the macOS "no pickup" investigation.

Gated behind `YULON_CONTROLLER_PROBE` in `main.py`, so it never ships, never
runs in the test suite, and never spawns a thread. It snapshots, in one read,
everything SDL can tell us about the gamepad path the real `GamepadSource`
walks, and prints it to a read-only panel for the owner to paste back.

The crux it exists to expose: SDL has two device layers and they do NOT agree.
`pygame._sdl2.controller.get_count()` counts only *mapped* controllers — devices
present in SDL's `gamecontrollerdb.txt` — while `pygame.joystick.get_count()`
counts *raw* HID devices. A Bluetooth pad (8BitDo, DualSense, Switch Pro, …)
that the database does not know shows up as `joystick count = 1` but
`controller count = 0`. `GamepadSource.start()` reads only the controller layer,
so it sees nothing and declares the "no controller present" no-op. This panel
prints both counts, each device's GUID/name/axes/buttons/hats, and whether SDL
maps it (`is_controller(i)`). A `controller count == 0` with a non-zero
`joystick count`, plus the GUID string, is the smoking gun: that GUID simply
needs adding to SDL's database (or `set_mapping()`).

It reads SDL on the GUI thread, once per "Rescan" click, inside a short
`init()`/`quit()` cycle — never concurrently with the worker thread, which is
the one thing `_GamepadWorker` is careful about.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon.ui.gamepad import (
    AXIS_LEFT_X,
    AXIS_LEFT_Y,
    AXIS_RIGHT_X,
    AXIS_RIGHT_Y,
    AXIS_TRIGGER_LEFT,
    AXIS_TRIGGER_RIGHT,
    BTN_A,
    BTN_B,
    BTN_BACK,
    BTN_DPAD_DOWN,
    BTN_DPAD_LEFT,
    BTN_DPAD_RIGHT,
    BTN_DPAD_UP,
    BTN_GUIDE,
    BTN_LB,
    BTN_LEFT_STICK,
    BTN_RB,
    BTN_RIGHT_STICK,
    BTN_START,
    BTN_X,
    BTN_Y,
)

# Labeled controller-face buttons and axes, so the snapshot reads as "A: down",
# not "button 0: True". `controller.get_button()` takes the SDL semantic enum.
_BUTTON_LABELS: dict[int, str] = {
    BTN_A: "A (confirm)",
    BTN_B: "B (back)",
    BTN_X: "X",
    BTN_Y: "Y",
    BTN_BACK: "Back",
    BTN_GUIDE: "Guide",
    BTN_START: "Start",
    BTN_LEFT_STICK: "Left stick",
    BTN_RIGHT_STICK: "Right stick",
    BTN_LB: "LB (cycle prev)",
    BTN_RB: "RB (cycle next)",
    BTN_DPAD_UP: "D-pad up",
    BTN_DPAD_DOWN: "D-pad down",
    BTN_DPAD_LEFT: "D-pad left",
    BTN_DPAD_RIGHT: "D-pad right",
}

_AXIS_LABELS: dict[int, str] = {
    AXIS_LEFT_X: "Left X",
    AXIS_LEFT_Y: "Left Y",
    AXIS_RIGHT_X: "Right X",
    AXIS_RIGHT_Y: "Right Y",
    AXIS_TRIGGER_LEFT: "Left trigger",
    AXIS_TRIGGER_RIGHT: "Right trigger",
}


def snapshot() -> list[str]:
    """Read everything SDL knows about the gamepad path, as display-ready lines.

    A self-contained `init`/`quit` around the whole read so the GUI thread never
    holds SDL open between rescan clicks. Returns one string per line; the panel
    renders the list as-is. No Qt, no thread, pure SDL facts.
    """
    import pygame  # typed (py.typed); imported lazily like gamepad.py does
    from pygame._sdl2 import controller as _sdl2ctl

    lines: list[str] = [f"pygame {pygame.version.ver}  |  SDL {pygame.version.SDL}"]
    lines.append("")

    # Raw layer first: this is what macOS HID sees, mapped or not.
    _ = pygame.joystick.get_init()
    try:
        pygame.joystick.init()
        raw = pygame.joystick.get_count()
        raw_init = pygame.joystick.get_init()
    except pygame.error as exc:
        lines.append(f"joystick subsystem error: {exc}")
        return lines
    lines.append(f"raw joystick layer: init={raw_init}  count={raw}")

    # Mapped layer: what GamepadSource actually reads.
    _sdl2ctl.init()
    mapped_init = _sdl2ctl.get_init()
    mapped = _sdl2ctl.get_count()
    lines.append(f"mapped controller layer: init={mapped_init}  count={mapped}")
    lines.append("")
    if raw == 0 and mapped == 0:
        lines.append("no devices at either layer — nothing is connected that SDL can see.")
        lines.append("  · on macOS, pair the pad in System Settings → Bluetooth first,")
        lines.append("  · then check System Settings → Game Controllers shows it.")
    if raw > 0 and mapped == 0:
        lines.append(
            "SMOKING GUN: a raw device is present but SDL has NO mapping for it "
            "(controller layer sees 0)."
        )
        lines.append("  → GamepadSource.start() therefore no-ops: this is the failure.")
        lines.append("  → copy the GUID(s) below and add them to SDL's gamecontrollerdb,")
        lines.append("    or call Controller.set_mapping() from the GUID + a template.")

    if raw > 0:
        lines.append("--- raw devices (pygame.joystick) ---")
        for i in range(raw):
            try:
                joy = pygame.joystick.Joystick(i)
                joy.init()
                guid = joy.get_guid()
                name = joy.get_name()
                n_axes = joy.get_numaxes()
                n_btn = joy.get_numbuttons()
                n_hats = joy.get_numhats()
                mapped_i = _sdl2ctl.is_controller(i)
                lines.append(
                    f"[{i}] name={name!r} guid={guid} axes={n_axes} buttons={n_btn} "
                    f"hats={n_hats} mapped={mapped_i}"
                )
                joy.quit()
            except pygame.error as exc:
                lines.append(f"[{i}] error reading raw device: {exc}")

    if mapped > 0:
        lines.append("--- mapped controllers (pygame._sdl2.controller) ---")
        for j in range(mapped):
            try:
                ctl = _sdl2ctl.Controller(j)
                lines.append(f"[{j}] name={ctl.name!r} id={ctl.id}")
                ctl.quit()
            except pygame.error as exc:
                lines.append(f"[{j}] error reading controller: {exc}")
        # Live button/axis state of EVERY controller. Two things make this
        # misleading if read naively, and both matter here:
        #  - SDL's polled state is only refreshed on `SDL_GameControllerUpdate`,
        #    which the event pump calls. Reading without a pump returns the LAST
        #    pumped frame — all zeros here. `_sdl2ctl.update()` refreshes first.
        #  - with more than one pad, `Controller(0)` may be a DIFFERENT device
        #    than the one the owner is pressing. Report each index separately so
        #    "which pad lights up" is visible, not assumed.
        _sdl2ctl.update()
        lines.append("")
        lines.append("--- live state (hold a button, then Rescan) ---")
        for j in range(mapped):
            try:
                live = _sdl2ctl.Controller(j)
                held = [lbl for b, lbl in _BUTTON_LABELS.items() if live.get_button(b)]
                lines.append(
                    f"[{j}] {live.name!r} — buttons: "
                    + (", ".join(held) if held else "(none)")
                )
                for ax, lbl in _AXIS_LABELS.items():
                    lines.append(f"     {lbl} = {live.get_axis(ax)}")
                live.quit()
            except pygame.error as exc:
                lines.append(f"[{j}] live state error: {exc}")
    else:
        lines.append("(no mapped controller, so no live state to show)")

    _sdl2ctl.quit()
    pygame.joystick.quit()
    return lines


class ControllerProbePanel(QWidget):
    """The temporary read-only panel + Rescan button, dropped into a tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)

        header = QLabel("Controller probe (diagnostic, YULON_CONTROLLER_PROBE)")
        header.setObjectName("probe-header")
        outer.addWidget(header)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        outer.addWidget(self._text, 1)

        row = QHBoxLayout()
        self._status = QLabel("")
        row.addWidget(self._status, 1)
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan)
        row.addWidget(rescan)
        outer.addLayout(row)

        self.rescan()

    def rescan(self) -> None:
        """Re-read SDL and repaint the panel (a plain synchronous snapshot)."""
        self._status.setText("reading SDL…")
        try:
            lines = snapshot()
            self._text.setPlainText("\n".join(lines))
            self._status.setText(f"scanned at {__import__('time').strftime('%H:%M:%S')}")
        except Exception as exc:  # a probe must never take the app down
            self._text.setPlainText(f"probe failed: {exc!r}")
            self._status.setText("probe error")