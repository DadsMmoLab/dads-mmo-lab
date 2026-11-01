"""The tray's Settings (T540): keep running in the tray, and start in it at sign-in.

Opened from the flyout's gear and from the window header's "Settings…". Each
switch acts the moment it is clicked; the dialog only closes. Where the desktop
has no tray (GNOME without AppIndicator, a Steam Deck in Game Mode) both are
greyed and the dialog says once why closing Yu'lon quits it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon import autostart, ui_settings
from yulon.log import get_logger
from yulon.ui.gamepad import BACK_CLOSES
from yulon.ui.icons import dadcraft_icon
from yulon.ui.theme import (
    OPEN_THEMES_FOLDER_BUTTON,
    THEME_COMBO,
    apply_dadcraft_theme,
    available_theme_choices,
    get_theme,
    normalize_theme_name,
    resolve_widget_theme,
    sync_user_themes,
)

if TYPE_CHECKING:
    from yulon.ui.tray import YulonTray

logger = get_logger(__name__)

KEEP = "Keep running when Yu'lon is closed"
SIGN_IN = "Start the tray when I sign in"
THEME_LABEL = "Color theme:"
"""The owner's words on design C (T551), in the panel and the Settings dialog alike."""
NO_TRAY = (
    "This desktop has no system tray, so closing Yu'lon quits it, as it always has. "
    "Your servers keep running either way."
)
NEEDS_KEEP = "Turn on keeping Yu'lon in the tray first."


class TraySwitches(QWidget):
    """The tray's two switches and the one line that says what is not possible here, and why.

    Shared (T551): the Settings dialog shows them, and so does the foot of the
    tray's compact panel (design C). Each switch acts the moment it is clicked.
    """

    def __init__(
        self,
        tray: YulonTray | _WindowTrayAdapter,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tray = tray
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self.keep = QCheckBox(KEEP, self)
        self.sign_in = QCheckBox(SIGN_IN, self)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setObjectName("tray-settings-note")
        column.addWidget(self.keep)
        column.addWidget(self.sign_in)
        column.addWidget(self.note)
        self._said_problem = ""
        self.keep.toggled.connect(self._keep_toggled)
        self.sign_in.toggled.connect(self._sign_in_toggled)
        # The panel and the Settings dialog are two views of one state (Codex):
        # a change in either is read again by both.
        tray.settings_changed.connect(self.read)
        self.read()

    def read(self) -> None:
        """Show what is true now: the tray's setting and the sign-in entry on disk."""
        has_tray = self.tray.icon is not None
        for box, on in (
            (self.keep, self.tray.keep_in_tray and has_tray),
            (self.sign_in, autostart.is_enabled()),
        ):
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self._settle()

    def _settle(self) -> None:
        """Which switch may be used, and the one line that says why one may not."""
        has_tray = self.tray.icon is not None
        self.keep.setEnabled(has_tray)
        why_not = autostart.why_not()
        if not has_tray:
            sign_in_why = NO_TRAY
        elif why_not is not None:
            sign_in_why = why_not
        elif not self.keep.isChecked():
            sign_in_why = NEEDS_KEEP
        else:
            sign_in_why = ""
        # An entry that is on can always be turned off (normal review): only
        # turning it ON needs a tray and an installed Yu'lon.
        self.sign_in.setEnabled(not sign_in_why or self.sign_in.isChecked())
        self.sign_in.setToolTip(sign_in_why)
        # The reason is said on screen, not only in a greyed box's tooltip.
        text = self._said_problem or sign_in_why
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _keep_toggled(self, on: bool) -> None:
        self._said_problem = ""
        if not on and self.sign_in.isChecked():
            # "Start the tray" with no tray to start in: the entry goes with it,
            # rather than staying on behind a greyed box (normal review). Only
            # if it really went: otherwise keep stays on (T551 adversarial).
            try:
                autostart.set_enabled(False)
            except OSError as exc:
                logger.warning(f"tray: could not turn start-at-sign-in off: {exc}")
                self._said_problem = (
                    f"Could not turn starting at sign-in off ({exc}), so Yu'lon stays in "
                    "the tray."
                )
                self._set_quietly(self.keep, True)
                self._settle()
                return
            self._set_quietly(self.sign_in, False)
            self.tray.settings_changed.emit()
        if not self.tray.remember_keep_in_tray(on):
            self._said_problem = (
                "This setting could not be saved, so it lasts only until Yu'lon closes."
            )
        self._settle()

    @staticmethod
    def _set_quietly(box: QCheckBox, on: bool) -> None:
        box.blockSignals(True)
        box.setChecked(on)
        box.blockSignals(False)

    def _sign_in_toggled(self, on: bool) -> None:
        self._said_problem = ""
        try:
            autostart.set_enabled(on)
        except OSError as exc:
            logger.warning(f"tray: could not turn start-at-sign-in {'on' if on else 'off'}: {exc}")
            self._said_problem = (
                f"Could not turn starting at sign-in {'on' if on else 'off'}: {exc}"
            )
            self.sign_in.blockSignals(True)
            self.sign_in.setChecked(not on)
            self.sign_in.blockSignals(False)
        else:
            self.tray.settings_changed.emit()
        self._settle()


class TraySettingsDialog(QDialog):
    """The header's Settings…: theme selector, tray switches (`TraySwitches`) and Close."""

    def __init__(self, tray: YulonTray) -> None:
        super().__init__(None)
        from yulon.ui.tray import OWN_DIALOG

        self.tray = tray
        self.setWindowTitle("Yu'lon settings")
        self.setProperty(OWN_DIALOG, True)
        self.setProperty(BACK_CLOSES, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        column = QVBoxLayout(self)

        theme_row = QHBoxLayout()
        self.theme_label = QLabel(THEME_LABEL, self)
        self.theme_combo = QComboBox(self)
        self.theme_combo.setObjectName(THEME_COMBO)
        for theme_id, label in available_theme_choices():
            self.theme_combo.addItem(label, theme_id)
        theme_row.addWidget(self.theme_label, 0)
        theme_row.addWidget(self.theme_combo, 1)
        column.addLayout(theme_row)

        self.switches = TraySwitches(tray, self)
        self.keep = self.switches.keep
        self.sign_in = self.switches.sign_in
        self.note = self.switches.note
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        column.addWidget(self.switches)
        column.addWidget(buttons)

        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        tray.settings_changed.connect(self._read_theme)
        self._read_theme()
        apply_dadcraft_theme(self, theme_name=self.current_theme())

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's own name
        super().showEvent(event)
        current = self.current_theme()
        choices = available_theme_choices(seed_user_dir=True)
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for theme_id, label in choices:
            self.theme_combo.addItem(label, theme_id)
        idx = self.theme_combo.findData(current)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

    def current_theme(self) -> str:
        """The theme ID currently selected in the combo box."""
        data = self.theme_combo.currentData()
        raw = data if isinstance(data, str) else self.theme_combo.currentText()
        return normalize_theme_name(raw)

    def _read_theme(self) -> None:
        current = getattr(self.tray, "theme", None) or ui_settings.load_ui_settings().theme
        normalized = normalize_theme_name(current)
        index = self.theme_combo.findData(normalized)
        if index >= 0 and index != self.theme_combo.currentIndex():
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(index)
            self.theme_combo.blockSignals(False)
        apply_dadcraft_theme(self, theme_name=normalized)

    def _theme_changed(self, _index: int) -> None:
        chosen = self.current_theme()
        remember = getattr(self.tray, "remember_theme", None)
        if callable(remember):
            ok = bool(remember(chosen))
        else:
            ok = ui_settings.remember_theme(chosen)
        apply_dadcraft_theme(self, theme_name=chosen)
        if not ok:
            self.switches._said_problem = (
                "This setting could not be saved, so it lasts only until Yu'lon closes."
            )
            self.switches._settle()


class _WindowTrayAdapter(QWidget):
    """Lightweight tray stand-in for `SettingsView` before `YulonTray.install()` binds a tray."""

    settings_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        on_theme_changed: Callable[[str], bool] | None = None,
        *,
        initial_theme: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_theme_changed = on_theme_changed
        self.icon: object = None
        saved = ui_settings.load_ui_settings()
        self.keep_in_tray: bool = saved.keep_in_tray
        self.theme: str = normalize_theme_name(initial_theme or saved.theme)

    def remember_keep_in_tray(self, keep: bool) -> bool:
        self.keep_in_tray = keep
        saved = ui_settings.remember_tray(keep_in_tray=keep)
        self.settings_changed.emit()
        return saved

    def remember_theme(self, theme_name: str) -> bool:
        normalized = normalize_theme_name(theme_name)
        self.theme = normalized
        if self._on_theme_changed is not None:
            saved = self._on_theme_changed(normalized)
        else:
            saved = ui_settings.remember_theme(normalized)
        self.settings_changed.emit()
        return saved


class SettingsView(QWidget):
    """In-window Settings submenu panel opened by the top menu bar's Settings item."""

    theme_selected = Signal(str)
    logs_requested = Signal()

    def __init__(
        self,
        initial_theme: str | QWidget | None = None,
        *,
        on_theme_changed: Callable[[str], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        resolved_parent = (
            parent
            if parent is not None
            else (initial_theme if isinstance(initial_theme, QWidget) else None)
        )
        super().__init__(resolved_parent)
        self.setObjectName("settings-view")
        if isinstance(initial_theme, str) and initial_theme:
            start_theme = normalize_theme_name(initial_theme)
        elif initial_theme is not None and not isinstance(initial_theme, str):
            prop_getter = getattr(initial_theme, "property", None)
            win_theme = prop_getter("yulon_theme") if callable(prop_getter) else None
            start_theme = normalize_theme_name(
                win_theme if isinstance(win_theme, str) else ui_settings.load_ui_settings().theme
            )
        else:
            start_theme = normalize_theme_name(ui_settings.load_ui_settings().theme)
        self._current_theme_id: str = start_theme
        self._adapter = _WindowTrayAdapter(
            self,
            on_theme_changed,
            initial_theme=start_theme,
        )
        self._tray: YulonTray | _WindowTrayAdapter = self._adapter

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

        heading = QLabel("Settings", self)
        heading.setObjectName("section-title")
        outer.addWidget(heading)

        card = QFrame(self)
        card.setObjectName("panel")
        card.setMaximumWidth(960)
        self._card = card
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)

        theme_row = QHBoxLayout()
        self.theme_label = QLabel(THEME_LABEL, card)
        self.theme_combo = QComboBox(card)
        self.theme_combo.setObjectName(THEME_COMBO)
        self.open_themes_button = QPushButton("Open themes folder…", card)
        self.open_themes_button.setObjectName(OPEN_THEMES_FOLDER_BUTTON)
        self.open_themes_button.setToolTip(
            "Open the folder where theme .json files live so you can add or edit custom themes"
        )
        self.open_themes_button.clicked.connect(self.open_themes_folder)
        theme_row.addWidget(self.theme_label, 0)
        theme_row.addWidget(self.theme_combo, 1)
        theme_row.addWidget(self.open_themes_button, 0)
        card_layout.addLayout(theme_row)

        # Live color swatch strip previewing the active theme's primary tokens
        swatch_row = QHBoxLayout()
        swatch_row.setContentsMargins(0, 0, 0, 2)
        swatch_row.setSpacing(6)
        self.theme_swatches: list[QFrame] = []
        for role in ("bg_dark", "bg_panel", "accent_bright", "uncommon", "danger"):
            swatch = QFrame(card)
            swatch.setObjectName(f"theme-swatch-{role}")
            swatch.setFixedSize(22, 10)
            swatch_row.addWidget(swatch)
            self.theme_swatches.append(swatch)
        swatch_row.addStretch(1)
        card_layout.addLayout(swatch_row)

        self._switches_box = QVBoxLayout()
        self._switches_box.setContentsMargins(0, 0, 0, 0)
        self.switches = TraySwitches(self._tray, card)
        self._switches_box.addWidget(self.switches)
        card_layout.addLayout(self._switches_box)

        self._actions_row = QHBoxLayout()
        self._actions_row.setContentsMargins(0, 4, 0, 0)
        self.open_logs_button = QPushButton("Diagnostic logs…", card)
        self.open_logs_button.setObjectName("settings-open-logs")
        self.open_logs_button.setToolTip(
            "Open Yu'lon's diagnostic logs and save a support file when something goes wrong"
        )
        self.open_logs_button.clicked.connect(self.logs_requested.emit)
        self._actions_row.addWidget(self.open_logs_button)
        self._actions_row.addStretch(1)
        card_layout.addLayout(self._actions_row)

        outer.addWidget(card)
        outer.addStretch(1)

        self.keep = self.switches.keep
        self.sign_in = self.switches.sign_in
        self.note = self.switches.note

        self.refresh_themes()
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        self._tray.settings_changed.connect(self._read_theme)
        self._read_theme()
        self.apply_theme(self._current_theme_id)

    def add_action(self, widget: QWidget) -> None:
        """Place an action button (such as 'Check for updates') inside the Settings panel."""
        widget.setParent(self)
        self._actions_row.insertWidget(0, widget)

    def set_current_theme(self, theme_id: str) -> None:
        """Update the selected theme and live swatches without emitting `theme_selected`."""
        normalized = normalize_theme_name(theme_id)
        self._current_theme_id = normalized
        self._adapter.theme = normalized
        index = self.theme_combo.findData(normalized)
        if index >= 0 and index != self.theme_combo.currentIndex():
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(index)
            self.theme_combo.blockSignals(False)
        self._update_swatches_and_icons(normalized)

    def apply_theme(self, theme_name: str | None = None) -> None:
        """Apply `theme_name` to the Settings panel's swatches, icons, and selector."""
        resolved = normalize_theme_name(
            theme_name or self._current_theme_id or resolve_widget_theme(self)
        )
        self.setProperty("yulon_theme", resolved)
        self.set_current_theme(resolved)

    def _update_swatches_and_icons(self, theme_name: str) -> None:
        spec = get_theme(theme_name)
        icon_color = spec.text_primary if spec.is_light else spec.text_gold
        self.open_themes_button.setIcon(dadcraft_icon("folder", icon_color, 14))
        self.open_logs_button.setIcon(dadcraft_icon("terminal", icon_color, 14))
        tokens = (spec.bg_dark, spec.bg_panel, spec.accent_bright, spec.uncommon, spec.danger)
        for swatch, color in zip(self.theme_swatches, tokens, strict=False):
            swatch.setStyleSheet(
                f"background-color: {color}; border: 1px solid {spec.border_dark}; "
                "border-radius: 2px;"
            )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's own name
        super().showEvent(event)
        self.refresh_themes(seed_user_dir=True)

    def open_themes_folder(self) -> None:
        """Seed the user-accessible `themes/` folder and open it in the file manager."""
        folder = sync_user_themes()
        self.refresh_themes()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def refresh_themes(self, *, seed_user_dir: bool = False) -> None:
        """Reload the theme dropdown from bundled and user `.json` theme files."""
        current = (
            getattr(self._tray, "theme", None)
            or self._current_theme_id
            or ui_settings.load_ui_settings().theme
        )
        choices = available_theme_choices(seed_user_dir=seed_user_dir)
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for theme_id, label in choices:
            self.theme_combo.addItem(label, theme_id)
        normalized = normalize_theme_name(current)
        index = self.theme_combo.findData(normalized)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
        self.theme_combo.blockSignals(False)
        self._update_swatches_and_icons(normalized)

    def bind_tray(self, tray: YulonTray | None) -> None:
        """Bind a live `YulonTray` (or revert to the window adapter on uninstall)."""
        old = self._tray
        if old is not None and shiboken6.isValid(old):
            try:
                old.settings_changed.disconnect(self._read_theme)
            except (RuntimeError, TypeError):
                pass
        if shiboken6.isValid(self.switches):
            self._switches_box.removeWidget(self.switches)
            self.switches.hide()
            self.switches.deleteLater()
        self._tray = tray if tray is not None else self._adapter
        self.switches = TraySwitches(self._tray, self._card)
        self._switches_box.addWidget(self.switches)
        self.keep = self.switches.keep
        self.sign_in = self.switches.sign_in
        self.note = self.switches.note
        self._tray.settings_changed.connect(self._read_theme)
        self._read_theme()

    def current_theme(self) -> str:
        """The theme ID currently selected in the combo box."""
        data = self.theme_combo.currentData()
        raw = data if isinstance(data, str) else self.theme_combo.currentText()
        return normalize_theme_name(raw)

    def _read_theme(self) -> None:
        current = (
            getattr(self._tray, "theme", None)
            or self._current_theme_id
            or ui_settings.load_ui_settings().theme
        )
        self.set_current_theme(current)

    def _theme_changed(self, _index: int) -> None:
        chosen = self.current_theme()
        self._current_theme_id = chosen
        self._update_swatches_and_icons(chosen)
        remember = getattr(self._tray, "remember_theme", None)
        if callable(remember):
            ok = bool(remember(chosen))
        else:
            ok = ui_settings.remember_theme(chosen)
        self.theme_selected.emit(chosen)
        if not ok:
            self.switches._said_problem = (
                "This setting could not be saved, so it lasts only until Yu'lon closes."
            )
            self.switches._settle()
