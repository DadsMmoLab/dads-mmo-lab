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

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from yulon.apply import check_answer
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


RUN_AGAIN_CHOICE_NOTE = (
    "This is already installed, and running it again applies the answer below as "
    "the new setting. Yu'lon does not remember what you picked last time, so what "
    "is selected now is the default: pick the one you want now."
)
"""Shown when an installed module's choice is asked again: an Update, or Install over it (T100).

The dialog pre-selects the manifest's DEFAULT, and no earlier answer is kept
anywhere, so clicking OK on an Update of Hearthstone Tweaks set back to
30 minutes a player who had picked 5 -- without a word. Remembering the answer
is a general feature and the owner's call (T104); until then the dialog says
what OK will do. Only for a `choice`: that is the kind a player picks once and
expects to stay picked.
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
        when = "its steps run again" if again else "it can be installed"
        self._notes: list[str] = [f"{manifest.name} ({manifest.id}) asks for this before {when}."]
        if again and any(prompt.kind == "choice" for prompt in self._prompts):
            self._notes.append(RUN_AGAIN_CHOICE_NOTE)
        for text in self._notes:
            note = QLabel(text, self)
            note.setWordWrap(True)
            box.addWidget(note)
        form = QFormLayout()
        for prompt in self._prompts:
            label = QLabel(prompt.question, self)
            label.setWordWrap(True)
            self._questions.append(prompt.question)
            control = self._control_for(prompt)
            self._controls[prompt.key] = control
            form.addRow(label, control)
        box.addLayout(form)

        self._problem_label = QLabel("", self)
        self._problem_label.setWordWrap(True)
        box.addWidget(self._problem_label)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        box.addWidget(self._buttons)

        for prompt in self._prompts:
            if prompt.default is not None:
                self.set_answer(prompt.key, prompt.default)
        self._recheck()

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

    def _control_for(self, prompt: Prompt) -> QWidget:
        if prompt.kind == "choice":
            combo = QComboBox(self)
            for choice in prompt.choices:
                combo.addItem(choice, choice)
            combo.currentIndexChanged.connect(
                lambda _index, key=prompt.key, box=combo: self._combo_changed(key, box)
            )
            self._answers[prompt.key] = str(combo.currentData() or "")
            return combo
        if prompt.kind == "bool":
            combo = QComboBox(self)
            for text, value in _BOOL_CHOICES:
                combo.addItem(text, value)
            combo.currentIndexChanged.connect(
                lambda _index, key=prompt.key, box=combo: self._combo_changed(key, box)
            )
            self._answers[prompt.key] = str(combo.currentData() or "")
            return combo
        edit = QLineEdit(self)
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
) -> Mapping[str, str] | None:
    """Put the manifest's questions to the user. `None` means they cancelled.

    `None` and `{}` are different answers and the caller acts on the difference:
    cancelling must change nothing on disk, whereas an empty mapping is what a
    manifest with nothing to ask produces.
    """
    dialog = ManifestPromptDialog(parent, manifest, prompts, again=again)
    if dialog.exec() != int(QDialog.DialogCode.Accepted):
        logger.info(f"{manifest.id}: the user cancelled the questions; nothing was applied")
        return None
    return dialog.answers()
