"""The control a `kind: "character"` question is answered with (T637).

One of three states, shown in one place so the dialog never has to know which:

* **reading**: the roster is being read on a worker; the line says so;
* **a list**: a combo box (one character) or a ticked list (several), over this
  server's characters, each as `Name — account ACCOUNT, level N, Race Class`;
* **typing**: the roster could not be read (or nobody offered to read it), so the
  number is typed as it was before this control existed, and a line says why.

`value()` is the answer text the manifest's `{key}` is rendered with: a GUID, or a
comma list of them. A saved answer is put back with `select()`, which keeps a GUID
this server does not list (shown as such) rather than quietly dropping it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yulon.character_pick import NONE_YET, Pickable, Roster, describe

READING = "Reading this server's characters…"
CHOOSE = "Choose a character…"
TYPE_INSTEAD = "Type the number instead."
_UNKNOWN = "GUID {guid} — saved earlier, but not one of this server's characters"
_UNKNOWN_ROLE = Qt.ItemDataRole.UserRole + 1


class CharacterPicker(QWidget):
    """A picker over a `Roster`, answering with GUID text."""

    changed = Signal()

    def __init__(self, parent: QWidget | None, *, multi: bool) -> None:
        super().__init__(parent)
        self._multi = multi
        self._by_guid: dict[int, Pickable] = {}
        self._typing = False
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox(self)
        self.listing = QListWidget(self)
        self.typed = QLineEdit(self)
        self.typed.setPlaceholderText("a whole number" if not multi else "numbers, with commas")
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        for widget in (self._status, self.combo, self.listing, self.typed):
            box.addWidget(widget)
        self.listing.setMaximumHeight(140)
        self.combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.listing.itemChanged.connect(lambda _item: self.changed.emit())
        self.typed.textChanged.connect(lambda _t: self.changed.emit())
        self._show_only(None)
        self.show_reading()

    # -- what the dialog says to it -----------------------------------------

    def show_reading(self) -> None:
        self._typing = False
        self._status.setText(READING)
        self._status.setVisible(True)
        self._show_only(self.listing if self._multi else self.combo)

    def set_roster(self, roster: Roster) -> None:
        """Show a read roster, or fall back to typing if it says it could not be read."""
        if roster.problem:
            self.set_failed(roster.problem)
            return
        keep = self.value()
        self._typing = False
        self._by_guid = {c.guid: c for c in roster.characters}
        self.combo.blockSignals(True)
        self.listing.blockSignals(True)
        self.combo.clear()
        self.listing.clear()
        self.combo.addItem(CHOOSE, None)
        for character in roster.characters:
            self.combo.addItem(describe(character), character.guid)
            item = QListWidgetItem(describe(character), self.listing)
            item.setData(Qt.ItemDataRole.UserRole, character.guid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        self.combo.blockSignals(False)
        self.listing.blockSignals(False)
        notes = []
        if not roster.characters:
            notes.append(NONE_YET)
        if roster.bots_left_out:
            many = roster.bots_left_out != 1
            notes.append(
                f"{roster.bots_left_out} playerbots {'characters are' if many else 'character is'}"
                " not listed: a bot character used here can crash the server."
            )
        self._status.setText(" ".join(notes))
        self._status.setVisible(bool(notes))
        self._show_only(self.listing if self._multi else self.combo)
        self.select(keep)

    def set_failed(self, why: str) -> None:
        self._typing = True
        self._status.setText(f"Could not read this server's characters ({why}). {TYPE_INSTEAD}")
        self._status.setVisible(True)
        self._show_only(self.typed)

    def show_typing(self) -> None:
        """No one is reading the roster (no database to ask): type the number, no sentence."""
        self._typing = True
        self._status.setVisible(False)
        self._show_only(self.typed)

    # -- answers --------------------------------------------------------------

    @property
    def typing(self) -> bool:
        return self._typing

    def status(self) -> str:
        return self._status.text()

    def value(self) -> str:
        """The answer text: a GUID, or a comma list of them, or what was typed."""
        if self._typing:
            return self.typed.text()
        if self._multi:
            return ",".join(str(g) for g in self.ticked())
        guid = self.combo.currentData()
        return "" if guid is None else str(guid)

    def ticked(self) -> list[int]:
        out: list[int] = []
        for row in range(self.listing.count()):
            item = self.listing.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def account_of(self, value: str) -> int | None:
        """The account id of the character `value` names, or None where it names none here."""
        if self._typing or self._multi:
            return None
        try:
            found = self._by_guid.get(int(value))
        except ValueError:
            return None
        return None if found is None else found.account_id

    def select(self, value: str) -> None:
        """Show `value` as the answer, without announcing a change."""
        if self._typing:
            if self.typed.text() != value:
                self.typed.blockSignals(True)
                self.typed.setText(value)
                self.typed.blockSignals(False)
            return
        wanted = [v for v in value.split(",") if v.strip()]
        guids = [int(v) for v in wanted if v.strip().isdigit()]
        if self._multi:
            self.listing.blockSignals(True)
            self._drop_unknown_rows()
            for row in range(self.listing.count()):
                item = self.listing.item(row)
                on = int(item.data(Qt.ItemDataRole.UserRole)) in guids
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            for guid in guids:
                if guid not in self._by_guid:
                    item = QListWidgetItem(_UNKNOWN.format(guid=guid), self.listing)
                    item.setData(Qt.ItemDataRole.UserRole, guid)
                    item.setData(_UNKNOWN_ROLE, True)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
            self.listing.blockSignals(False)
            return
        self.combo.blockSignals(True)
        for row in reversed(range(self.combo.count())):
            if self.combo.itemData(row, _UNKNOWN_ROLE):
                self.combo.removeItem(row)
        one = guids[0] if len(guids) == 1 else None
        if one is not None and self.combo.findData(one) < 0:
            self.combo.addItem(_UNKNOWN.format(guid=one), one)
            self.combo.setItemData(self.combo.count() - 1, True, _UNKNOWN_ROLE)
        self.combo.setCurrentIndex(max(self.combo.findData(one), 0) if one is not None else 0)
        self.combo.blockSignals(False)

    def tick(self, guid: int, on: bool) -> None:
        """Tick (or untick) one character, as a click does: announces the change."""
        for row in range(self.listing.count()):
            item = self.listing.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == guid:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    # -- internals --------------------------------------------------------------

    def _drop_unknown_rows(self) -> None:
        for row in reversed(range(self.listing.count())):
            if self.listing.item(row).data(_UNKNOWN_ROLE):
                self.listing.takeItem(row)

    def _show_only(self, widget: QWidget | None) -> None:
        self.combo.setVisible(widget is self.combo)
        self.listing.setVisible(widget is self.listing)
        self.typed.setVisible(widget is self.typed)
