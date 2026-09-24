"""Ask a manifest's own questions before its item is applied (Lane A, 2026-09-07).

Two of the 41 shipped `wow-wotlk` manifests carry a prompt with no default —
`mod-ah-bot` and `mod-ah-bot-plus`, both of which want the GUID of the auction
house bot's character — and until this existed the Modules tab called
`Applier.install()` with no `values` argument at all. `apply._values()` then
filled in only the prompts that HAD a default, `_render()` raised on the one
that did not, and what the owner's screenshot read was:

    install mod-ah-bot-plus FAILED: conf AuctionHouseBot.GUIDs: no value for {bot_guid}

after the module had already been cloned onto his disk. So the two modules he
could actually test were the two the GUI could not install.

`ManifestPromptDialog` is deliberately more than a `QInputDialog.getText()`:
the manifest declares a *kind* per prompt (`int`, `float`, `bool`, `choice`,
`string`), and a text box that accepts anything would hand the applier an
answer it can only refuse later. The kind decides the control, and
`apply.check_answer()` — the SAME function the applier's own pre-flight uses —
decides whether OK can be pressed at all. One rule, one place, two callers.

The dialog holds the answers in a dict rather than reading them back off the
widgets. That is what lets `set_answer()` state a value a control cannot
display (a `bool` answered `"maybe"`, a `choice` answered `""`) and have the
dialog say so, rather than silently rounding it to whatever the combo box
happened to be showing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yulon.apply import check_answer, reapplies_on_top
from yulon.log import get_logger
from yulon.manifest import Manifest, Prompt

logger = get_logger(__name__)

_BOOL_CHOICES = (("Yes", "1"), ("No", "0"))
"""What a `bool` prompt offers, and what each option answers with.

`"1"`/`"0"` rather than `"true"`/`"false"` because that is what the conf files
these values are written into already hold — `AuctionHouseBot.EnableSeller = 1`
in `mod_ahbot.conf` — and `check_answer()` accepts either spelling anyway, so
the choice here is about what a worldserver reads, not about what passes.
"""


_ROWS_MIN_HEIGHT = 120
"""The shortest the question area may get: about four rows, so a squeezed dialog shows some."""

_MIN_WIDTH = 480
"""Wide enough that a question label wraps to two lines, not six."""


RERUN_SETS_NOTE = (
    "This is already installed, and running it again applies the answers below as the new "
    "setting."
)
"""The lead-in to `NO_RECORD_NOTE`, for a module whose re-run REPLACES the setting.

Not said of a module whose re-run compounds (`COMPOUNDS_NOTE` says what happens
there instead): "as the new setting" would be false for the mob multipliers,
measured in the fix-wave live check on m910q, where both sentences appeared.
"""


NO_RECORD_NOTE = (
    "Yu'lon has no record of your earlier answer to: {questions}. What is filled in there is "
    "the default (or nothing, where there is none), so change it if you set it differently."
)
"""Shown when an installed module is asked again and some answer is not in its record.

T100 said this of every re-run ("Yu'lon does not remember what you picked last
time"). Since T104 it is true only of an install made before answers were kept,
or of an answer the question no longer accepts, so it names just those.
"""


REMEMBERED_NOTE = (
    "Your answers from the last time this was installed or updated are filled in. "
    "OK applies what is shown."
)
"""Shown when at least one answer came from this install's record (T104)."""


REMOVE_NO_RECORD_NOTE = (
    "Yu'lon has no record of the value used when this was installed. Enter the one you "
    "chose: Remove undoes the install with it, so with any other value the change is not "
    "undone exactly (a multiplier stays multiplied)."
)
"""Shown when Remove has to ask what install used (cold review + Codex, T104 fix wave).

The mob multipliers undo `HealthModifier*{hp}` with `HealthModifier/{hp}`. With
no usable record -- an install made before T104, or a damaged file -- the only
other value to hand is the manifest's default, and dividing by it in silence
would leave every creature multiplied whenever the player had picked another.
So Remove asks, pre-filled with the default, and says why.
"""


COMPOUNDS_NOTE = (
    "Running this again applies the values below again, on top of what the database holds "
    "now, so they compound. To change them, Remove it and then Install it instead."
)
"""Shown when a module whose install is relative to the current values is run again.

`apply.reapplies_on_top()`: the four mob multipliers. Compounding on Update is
pre-existing and tracked as T115; until it is fixed, the dialog says what OK does.
"""


class ManifestPromptDialog(QDialog):
    """One row per prompt, the manifest's own question as the label.

    The question text is written for the player and is the only guidance there
    is: `mod-ah-bot`'s is "GUID of the AH bot character (create the AHBOT
    account + ONE character first, log it out)", which is the entire procedure
    in one line. It is shown verbatim.
    """

    def __init__(
        self,
        parent: QWidget | None,
        manifest: Manifest,
        prompts: Sequence[Prompt],
        *,
        again: bool = False,
        remembered: Mapping[str, str] | None = None,
        removing: bool = False,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._prompts = tuple(prompts)
        self._answers: dict[str, str] = {}
        self._controls: dict[str, QWidget] = {}
        self._questions: list[str] = []
        self.setWindowTitle(f"{manifest.name} needs an answer")
        self.setModal(True)

        box = QVBoxLayout(self)
        when = (
            "it can be removed"
            if removing
            else "its steps run again" if again else "it can be installed"
        )
        self._notes: list[str] = [f"{manifest.name} ({manifest.id}) asks for this before {when}."]
        # T104: what this install remembers, where the question still accepts it;
        # else the manifest's default. A saved answer the question now refuses (a
        # dropped `choice` option) is not shown, because OK would then be refused.
        prefill: dict[str, str] = {}
        from_record: list[Prompt] = []
        for prompt in self._prompts:
            saved = (remembered or {}).get(prompt.key)
            if saved is not None and check_answer(prompt, saved) == "":
                prefill[prompt.key] = saved
                from_record.append(prompt)
            elif prompt.default is not None:
                prefill[prompt.key] = prompt.default
        if from_record:
            self._notes.append(REMEMBERED_NOTE)
        missing = [p for p in self._prompts if p not in from_record]
        if removing:
            if missing:
                self._notes.append(REMOVE_NO_RECORD_NOTE)
        elif again:
            compounds = reapplies_on_top(manifest)
            if missing:
                questions = "; ".join(p.question for p in missing)
                lead = "" if compounds else RERUN_SETS_NOTE + " "
                self._notes.append(lead + NO_RECORD_NOTE.format(questions=questions))
            if compounds:
                self._notes.append(COMPOUNDS_NOTE)
        for text in self._notes:
            note = QLabel(text, self)
            note.setWordWrap(True)
            box.addWidget(note)
        # The rows scroll (T104, ruling A of the T92 merge). Since T92 an install
        # also runs configure-time steps, so accountwide's Install asks all 13 of
        # its flags; laid straight into the dialog, 13 rows made it at least
        # ~470 px tall with no way to be shorter, which does not fit the app's
        # 960x640 minimum window once the notes and buttons are added.
        rows = QWidget()
        form = QFormLayout(rows)
        form.setContentsMargins(0, 0, 0, 0)
        for prompt in self._prompts:
            label = QLabel(prompt.question, rows)
            label.setWordWrap(True)
            self._questions.append(prompt.question)
            control = self._control_for(prompt, rows)
            self._controls[prompt.key] = control
            form.addRow(label, control)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(rows)
        box.addWidget(scroll, 1)

        self._problem_label = QLabel("", self)
        self._problem_label.setWordWrap(True)
        box.addWidget(self._problem_label)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        box.addWidget(self._buttons)

        for key, value in prefill.items():
            self.set_answer(key, value)
        self._recheck()
        self._fit(scroll, rows, parent)

    def _fit(self, scroll: QScrollArea, rows: QWidget, parent: QWidget | None) -> None:
        """Open as tall as the rows need, but never taller than the window it belongs to.

        `QScrollArea`'s own size hint stops well short of 13 rows, so without this
        a dialog that has room would open with a scrollbar it does not need.
        """
        scroll.setMinimumHeight(min(rows.sizeHint().height(), _ROWS_MIN_HEIGHT))
        wanted = self.sizeHint().height() - scroll.sizeHint().height() + rows.sizeHint().height()
        window = parent.window() if parent is not None else None
        screen = self.screen().availableGeometry().height() if self.screen() else wanted
        cap = min(window.height(), screen) if window is not None else screen
        self.resize(max(self.sizeHint().width(), _MIN_WIDTH), min(wanted, cap))

    # -- what the tests and the caller ask ---------------------------------

    def questions(self) -> tuple[str, ...]:
        """The question text shown for each prompt, in the manifest's order."""
        return tuple(self._questions)

    def notes(self) -> str:
        """The sentences shown above the questions, one per line."""
        return "\n".join(self._notes)

    def answers(self) -> dict[str, str]:
        """What is currently filled in, as the `values` mapping the applier takes."""
        return dict(self._answers)

    def problem(self) -> str:
        """The first answer that cannot be used, named with its own question, or `""`.

        First rather than all of them: the dialog shows one line under the form,
        and a person fixing three empty boxes does not need to be told about
        three empty boxes.
        """
        for prompt in self._prompts:
            reason = check_answer(prompt, self._answers.get(prompt.key, ""))
            if reason:
                return f"{prompt.question} — {reason}."
        return ""

    def set_answer(self, key: str, value: str) -> None:
        """State one answer, and show it in its control if that control can show it."""
        self._answers[key] = value
        control = self._controls.get(key)
        if isinstance(control, QLineEdit):
            if control.text() != value:
                control.setText(value)
        elif isinstance(control, QComboBox):
            index = control.findData(value)
            if index >= 0 and control.currentIndex() != index:
                control.setCurrentIndex(index)
        self._recheck()

    # -- internals ---------------------------------------------------------

    def _control_for(self, prompt: Prompt, owner: QWidget) -> QWidget:
        if prompt.kind == "choice":
            combo = QComboBox(owner)
            for choice in prompt.choices:
                combo.addItem(choice, choice)
            combo.currentIndexChanged.connect(
                lambda _index, key=prompt.key, box=combo: self._combo_changed(key, box)
            )
            self._answers[prompt.key] = str(combo.currentData() or "")
            return combo
        if prompt.kind == "bool":
            combo = QComboBox(owner)
            for text, value in _BOOL_CHOICES:
                combo.addItem(text, value)
            combo.currentIndexChanged.connect(
                lambda _index, key=prompt.key, box=combo: self._combo_changed(key, box)
            )
            self._answers[prompt.key] = str(combo.currentData() or "")
            return combo
        edit = QLineEdit(owner)
        # No `QIntValidator`: a validator that silently drops keystrokes leaves
        # a person typing into a box that does nothing and says nothing. The
        # refusal is shown as a sentence instead, under the form.
        edit.setPlaceholderText({"int": "a whole number", "float": "a number"}.get(prompt.kind, ""))
        edit.textChanged.connect(lambda text, key=prompt.key: self._text_changed(key, text))
        self._answers[prompt.key] = ""
        return edit

    def _text_changed(self, key: str, text: str) -> None:
        self._answers[key] = text
        self._recheck()

    def _combo_changed(self, key: str, box: QComboBox) -> None:
        self._answers[key] = str(box.currentData() or "")
        self._recheck()

    def _recheck(self) -> None:
        problem = self.problem()
        self._problem_label.setText(problem)
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(not problem)


def ask_manifest_prompts(
    parent: QWidget | None,
    manifest: Manifest,
    prompts: Sequence[Prompt],
    *,
    again: bool = False,
    remembered: Mapping[str, str] | None = None,
    removing: bool = False,
) -> Mapping[str, str] | None:
    """Put the manifest's questions to the user. `None` means they cancelled.

    `None` and `{}` are different answers and the caller acts on the difference:
    cancelling must change nothing on disk, whereas an empty mapping is what a
    manifest with nothing to ask produces.
    """
    dialog = ManifestPromptDialog(
        parent, manifest, prompts, again=again, remembered=remembered, removing=removing
    )
    if dialog.exec() != int(QDialog.DialogCode.Accepted):
        logger.info(f"{manifest.id}: the user cancelled the questions; nothing was applied")
        return None
    return dialog.answers()
