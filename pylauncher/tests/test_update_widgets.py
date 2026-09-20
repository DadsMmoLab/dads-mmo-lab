"""Tests for the update bar and the what's-new dialog (T90).

Offscreen, through the suite's session `QApplication`. Nothing here talks to
GitHub: every `UpdateCheck` is built by hand, which is the whole point of the
check being a plain frozen dataclass.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument, qRgb
from PySide6.QtWidgets import QLabel, QPushButton, QTextBrowser

from tests.conftest import process_events
from yulon.ui.widgets import update_dialog
from yulon.ui.widgets.dadcraft_decorations import DadcraftHeader
from yulon.ui.widgets.update_bar import UpdateBar
from yulon.ui.widgets.update_dialog import (
    IMAGE_REMOVED,
    UpdateChoice,
    UpdateDialog,
    _NotesView,
    as_shown_markdown,
)
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
    assert _tooltip_reads(bar) == bar.text()


def _tooltip_reads(bar: UpdateBar) -> str:
    """What the tooltip actually shows, put through the same sniff Qt puts it through."""
    document = QTextDocument()
    document.setHtml(bar.label.toolTip())
    return document.toPlainText()


def test_an_error_message_with_a_tag_in_it_is_shown_and_not_rendered(qapp: object) -> None:
    """Measured on 6.11: `mightBeRichText("HTTP Error 403: <img src=x>")` is True.

    Unwrapped, that tooltip read `HTTP Error 403: ￼` — the tag became an image
    placeholder, which is a name Qt resolves. The check really can be handed
    this sentence, because a failure is shown to the user verbatim.
    """
    from PySide6.QtGui import Qt as GuiQt

    message = "HTTP Error 403: <img src=x>"
    assert GuiQt.mightBeRichText(message) is True, "the premise of this test"

    bar = UpdateBar()
    bar.show_message(message)

    assert _tooltip_reads(bar) == message
    assert bar.text() == message


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


def _notes(dialog: UpdateDialog) -> QTextBrowser:
    notes = dialog.findChild(QTextBrowser, "update-notes")
    assert notes is not None
    return notes


def _red_pixels(widget: QTextBrowser) -> int:
    """How much of `widget`, rendered, is the red of the probe image."""
    widget.resize(400, 300)
    shot = widget.grab().toImage()
    return sum(
        1
        for y in range(shot.height())
        for x in range(shot.width())
        if (c := shot.pixelColor(x, y)).red() > 180 and c.green() < 80 and c.blue() < 80
    )


def _image_fragments(widget: QTextBrowser) -> int:
    """Image fragments left in the widget's document, whatever syntax made them."""
    return _image_fragments_in(widget.document())


def _image_fragments_in(document: QTextDocument) -> int:
    found = 0
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                found += 1
            iterator += 1
        block = block.next()
    return found


@pytest.fixture
def a_red_png(tmp_path: Path) -> Path:
    """A file the notes box must never render, in a colour a screenshot can count."""
    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(qRgb(255, 0, 0))
    path = tmp_path / "red.png"
    assert image.save(str(path)), "the probe image was not written"
    return path


@pytest.mark.parametrize(
    "shape",
    ["inline-file-url", "bare-absolute-path", "reference-style", "unc-path", "html-img"],
)
def test_no_image_in_a_release_body_is_ever_rendered(
    qapp: object, a_red_png: Path, shape: str
) -> None:
    """Measured on 6.11, and the reason `loadResource` alone was not enough.

    With only the `loadResource` override, the first three of these shapes each
    put 1600 red pixels in a `grab()` of the notes box: Qt's own image handling
    opens the path when the resource comes back null. The fourth is the one
    that matters on Windows — a UNC path is an SMB connection, i.e. an NTLM
    handshake with a host the release body chose, with no click at all.
    """
    bodies = {
        "inline-file-url": f"![i](file://{a_red_png})",
        "bare-absolute-path": f"![i]({a_red_png})",
        "reference-style": f"![i][ref]\n\n[ref]: file://{a_red_png}",
        "unc-path": "![i](//host/share/x.png)",
        "html-img": f"<img src='file://{a_red_png}'>",
    }
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=bodies[shape]))

    notes = _notes(dialog)

    assert _red_pixels(notes) == 0, "the notes box rendered a file off the disk"
    assert _image_fragments(notes) == 0, "an image fragment is a name Qt will resolve"


def test_the_second_door_takes_an_image_the_first_one_missed(
    qapp: object, a_red_png: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strip, at the call site that has to do it, with the first door taken off.

    The two doors are belt and braces, and a brace nothing exercises is not a
    brace: with `as_shown_markdown` in front of it no image syntax survives to
    reach the document, so the strip has nothing to do — deleting the CALL left
    39 of 39 tests green (measured twice, the second time with a test that
    called `_strip_images` itself, which is the "reviews check functions, not
    call sites" trap). Here the rewrite is a pass-through, which is what any
    shape md4c turns into an image that the rewrite did not anticipate looks
    like from `set_release_body`'s side.
    """
    monkeypatch.setattr(update_dialog, "as_shown_markdown", lambda body: body)
    notes = _NotesView()

    removed = notes.set_release_body(f"![i](file://{a_red_png})")

    assert removed == 1, "set_release_body did not strip the image"
    assert _image_fragments(notes) == 0
    assert _red_pixels(notes) == 0
    assert IMAGE_REMOVED in notes.toPlainText()


def test_strip_images_reports_nothing_to_do_on_an_ordinary_body(qapp: object) -> None:
    """The count is the wiring's own evidence: normally the first door left it none."""
    notes = _NotesView()

    assert notes.set_release_body("### New\n- Ten.\n") == 0


def test_an_image_leaves_a_mark_rather_than_disappearing(qapp: object, a_red_png: Path) -> None:
    """The reader is told something was there; they are not shown it."""
    body = f"### New\n\n![the shiny new tab]({a_red_png})\n\n- Ten."
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    shown = _notes(dialog).toPlainText()
    assert "Ten." in shown, "the text after the image survived"
    assert IMAGE_REMOVED in shown or "the shiny new tab" in shown


def test_a_link_in_the_notes_is_never_navigated_to_by_the_widget(qapp: object) -> None:
    """`setOpenLinks(True)` would let the box browse; `setOpenExternalLinks` would shell out."""
    notes = _notes(UpdateDialog(RESULT))

    assert notes.openLinks() is False
    assert notes.openExternalLinks() is False


@pytest.mark.parametrize(
    ("href", "opened"),
    [
        ("https://example.invalid/notes", True),
        ("http://example.invalid/notes", True),
        ("file:///etc/passwd", False),
        ("file://host/share/x.png", False),
        ("smb://host/share", False),
        ("javascript:alert(1)", False),
        ("//host/share/x.exe", False),
        ("", False),
    ],
)
def test_only_an_http_link_is_handed_to_the_desktop(qapp: object, href: str, opened: bool) -> None:
    """A click is a click; what it may start is not the release body's decision."""
    notes = _notes(UpdateDialog(RESULT))
    handed: list[str] = []
    notes.open_url = handed.append

    notes.anchorClicked.emit(QUrl(href))

    assert bool(handed) is opened
    assert handed == ([href] if opened else [])


def test_a_file_link_never_puts_a_local_file_in_front_of_the_reader(
    qapp: object, tmp_path: Path
) -> None:
    """Measured on 6.11: the old widget NAVIGATED to a `file:` link and showed the file.

    Clicking `[run](file:///etc/hostname)` left `source()` at that path and the
    box reading the machine's hostname off the disk. Not a launch — a read, and
    a release body naming the path.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("the contents of a local file", encoding="utf-8")
    notes = _notes(UpdateDialog(RESULT))
    before = notes.toPlainText()

    notes.anchorClicked.emit(QUrl.fromLocalFile(str(secret)))

    assert notes.source().toString() == "", "the widget navigated somewhere"
    assert notes.toPlainText() == before
    assert "the contents of a local file" not in notes.toPlainText()


def test_raw_html_in_a_release_body_is_shown_and_eats_nothing_after_it(qapp: object) -> None:
    """Measured on 6.11: a raw HTML block swallowed the rest of the body silently.

    `setMarkdown` passed the tags through to the document and md4c took
    everything up to the next blank line with them, so the bullet below the
    `<img>` was simply not in the dialog.
    """
    body = "## v0.8.70-Public\n\n<img src='http://example.invalid/x.png'><script>x</script>\n- Ten."
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    shown = _notes(dialog).toPlainText()
    assert "Ten." in shown, "the bullet after the HTML was dropped"
    assert "<script>" in shown, "the tag must be shown as text, not parsed away"


def test_the_notes_box_refuses_every_resource_it_is_asked_for(qapp: object) -> None:
    """The last of the three doors, and the one that stops a fetch for any OTHER reason.

    It is NOT what stops an image: that was measured and it does not (see
    `test_no_image_in_a_release_body_is_ever_rendered`).
    """
    notes = _notes(UpdateDialog(RESULT))

    assert notes.loadResource(2, QUrl("http://example.invalid/x.png")) is None


def test_escaping_leaves_ordinary_markdown_alone() -> None:
    """Only `<` is neutralised: `&` would turn every deliberate entity into source."""
    assert as_shown_markdown("### New\n- Ten & more.") == "### New\n- Ten & more."
    assert as_shown_markdown("<b>x</b>") == "&lt;b>x&lt;/b>"


@pytest.mark.parametrize(
    "body",
    [
        "the dir is `<config_dir>` on Linux",
        "```\n<not a tag>\n```",
        "~~~sh\nif [ $x -lt 3 ]; then echo '<'; fi\n~~~",
        "``a span with a ` in it and <b>``",
    ],
)
def test_code_is_left_exactly_as_the_author_wrote_it(body: str) -> None:
    """`<` means `<` inside code. Escaping it showed the reader `&lt;config_dir>`."""
    assert as_shown_markdown(body) == body


def test_code_spans_survive_into_the_rendered_notes(qapp: object) -> None:
    """The measurement that matters is what the reader sees, not the intermediate text."""
    body = "- it lives at `<config_dir>/update.json`"
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    assert "<config_dir>/update.json" in _notes(dialog).toPlainText()


def test_an_autolink_stays_a_link(qapp: object) -> None:
    """`<https://…>` is how a bare URL is made clickable; escaping it made it prose."""
    body = "see <https://example.invalid/x> for more"

    assert as_shown_markdown(body) == body

    document = QTextDocument()
    document.setMarkdown(as_shown_markdown(body))
    assert "https://example.invalid/x" in document.toHtml()


def test_an_image_is_turned_into_a_link_not_deleted_from_the_source() -> None:
    """`![` → `[`: inert until clicked, and then `_NotesView` decides."""
    assert as_shown_markdown("![alt](file:///x.png)") == "[alt](file:///x.png)"
    assert as_shown_markdown("a `![keep](x)` span") == "a `![keep](x)` span"


def test_the_header_takes_an_action_left_of_the_badge(qapp: object) -> None:
    header = DadcraftHeader()
    button = QPushButton("Check for updates")

    header.add_action(button)

    layout = header.layout()
    assert layout.indexOf(button) == layout.indexOf(header._badge) - 1
    assert button.parent() is header
