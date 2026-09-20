"""Tests for the update bar and the what's-new dialog (T90).

Offscreen, through the suite's session `QApplication`. Nothing here talks to
GitHub: every `UpdateCheck` is built by hand, which is the whole point of the
check being a plain frozen dataclass.
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QTextBrowser

from tests.conftest import process_events
from yulon.ui.widgets.dadcraft_decorations import DadcraftHeader
from yulon.ui.widgets.update_bar import UpdateBar
from yulon.ui.widgets.update_dialog import UpdateChoice, UpdateDialog, as_shown_markdown
from yulon.update import UpdateCheck

RESULT = UpdateCheck(
    "0.8.66-Public",
    "v0.8.70-Public",
    True,
    "https://example.invalid/r",
    notes_markdown="## v0.8.70-Public\n\n### New\n- Ten.\n",
    assets=(),
    has_checksums=False,
)


def test_the_bar_is_hidden_until_there_is_something_to_say(qapp: object) -> None:
    bar = UpdateBar()
    assert bar.isHidden()

    bar.show_update(RESULT)

    assert not bar.isHidden()
    assert "v0.8.70-Public" in bar.text() and "0.8.66-Public" in bar.text()
    assert not bar.details_button.isHidden()


def test_a_message_has_no_button_and_clear_hides(qapp: object) -> None:
    bar = UpdateBar()
    bar.show_update(RESULT)

    bar.show_message("You have the newest version.")

    assert bar.details_button.isHidden()
    assert bar.text() == "You have the newest version."

    bar.clear()
    assert bar.isHidden()


def test_the_button_asks_for_the_details(qapp: object) -> None:
    bar = UpdateBar()
    bar.show_update(RESULT)
    seen: list[int] = []
    bar.details_requested.connect(lambda: seen.append(1))

    bar.details_button.click()

    assert seen == [1]


def test_the_bar_does_not_widen_the_window(qapp: object) -> None:
    """The window's minimum is 960x640 and this row may not raise it.

    A tag comes off the network, so its length is not this app's to promise.
    The label is `Ignored` horizontally: a long one elides inside the row.
    """
    bar = UpdateBar()
    bar.show_update(dataclasses.replace(RESULT, latest="v" + "9" * 200 + ".0.0-Public"))

    assert bar.minimumSizeHint().width() <= 480


def test_a_sentence_too_long_for_the_row_is_elided_and_whole_in_the_tooltip(
    qapp: object,
) -> None:
    """Cut text with nothing to say it was cut is how T85's minimum window was paid for."""
    bar = UpdateBar()
    bar.show_update(dataclasses.replace(RESULT, latest="v" + "9" * 200 + ".0.0-Public"))
    bar.resize(320, 30)
    process_events()

    assert bar.label.text() != bar.text()
    assert bar.label.text().endswith("…")
    assert bar.label.toolTip() == bar.text()


def test_the_tag_is_shown_as_text_and_never_as_markup(qapp: object) -> None:
    """The tag comes from the network and used to be interpolated into HTML."""
    from PySide6.QtCore import Qt

    bar = UpdateBar()
    bar.show_update(dataclasses.replace(RESULT, latest="<b>v9.9.9-Public</b>"))

    assert bar.label.textFormat() == Qt.TextFormat.PlainText
    assert "<b>" in bar.text()


def test_the_dialog_shows_both_versions_and_the_notes(qapp: object) -> None:
    dialog = UpdateDialog(RESULT)

    notes = dialog.findChild(QTextBrowser, "update-notes")
    assert notes is not None
    assert "Ten." in notes.toPlainText()
    assert "v0.8.70-Public" in dialog.windowTitle()
    current = dialog.findChild(QLabel, "update-current")
    assert current is not None and "0.8.66-Public" in current.text()


def test_no_notes_says_so_instead_of_an_empty_box(qapp: object) -> None:
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=""))

    notes = dialog.findChild(QTextBrowser, "update-notes")
    assert notes is not None and "No release notes" in notes.toPlainText()


@pytest.mark.parametrize(
    ("name", "choice"),
    [
        ("update-action", UpdateChoice.UPDATE),
        ("update-later", UpdateChoice.LATER),
        ("update-skip", UpdateChoice.SKIP),
    ],
)
def test_each_button_is_its_choice_and_closes(
    qapp: object, name: str, choice: UpdateChoice
) -> None:
    dialog = UpdateDialog(RESULT)
    dialog.show()

    button = dialog.findChild(QPushButton, name)
    assert button is not None
    button.click()

    assert dialog.choice is choice
    assert not dialog.isVisible()


def test_closing_the_window_is_later(qapp: object) -> None:
    """Esc and the window's close button both reach `reject()`; neither is a skip."""
    dialog = UpdateDialog(RESULT)
    dialog.show()

    dialog.reject()

    assert dialog.choice is UpdateChoice.LATER


def test_the_action_label_is_the_callers(qapp: object) -> None:
    """Plan 3 relabels it per install kind; this plan opens the download page."""
    dialog = UpdateDialog(RESULT, action_label="Update now")

    button = dialog.findChild(QPushButton, "update-action")
    assert button is not None and button.text() == "Update now"


def test_notes_links_open_outside_and_the_page_never_navigates(qapp: object) -> None:
    dialog = UpdateDialog(RESULT)

    notes = dialog.findChild(QTextBrowser, "update-notes")
    assert notes is not None and notes.openExternalLinks() is True


def test_raw_html_in_a_release_body_is_shown_and_eats_nothing_after_it(qapp: object) -> None:
    """Measured on 6.11: a raw HTML block swallowed the rest of the body silently.

    `setMarkdown` passed the tags through to the document and md4c took
    everything up to the next blank line with them, so the bullet below the
    `<img>` was simply not in the dialog.
    """
    body = "## v0.8.70-Public\n\n<img src='http://example.invalid/x.png'><script>x</script>\n- Ten."
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    notes = dialog.findChild(QTextBrowser, "update-notes")
    assert notes is not None
    shown = notes.toPlainText()
    assert "Ten." in shown, "the bullet after the HTML was dropped"
    assert "<script>" in shown, "the tag must be shown as text, not parsed away"


def test_the_notes_box_fetches_nothing(qapp: object) -> None:
    """An image in the body would otherwise be GET-ed when the dialog opens."""
    from PySide6.QtCore import QUrl

    dialog = UpdateDialog(RESULT)

    notes = dialog.findChild(QTextBrowser, "update-notes")
    assert notes is not None
    assert notes.loadResource(2, QUrl("http://example.invalid/x.png")) is None


def test_escaping_leaves_ordinary_markdown_alone() -> None:
    """Only `<` is neutralised: `&` would turn every deliberate entity into source."""
    assert as_shown_markdown("### New\n- Ten & more.") == "### New\n- Ten & more."
    assert as_shown_markdown("<b>x</b>") == "&lt;b>x&lt;/b>"


def test_the_header_takes_an_action_left_of_the_badge(qapp: object) -> None:
    header = DadcraftHeader()
    button = QPushButton("Check for updates")

    header.add_action(button)

    layout = header.layout()
    assert layout.indexOf(button) == layout.indexOf(header._badge) - 1
    assert button.parent() is header
