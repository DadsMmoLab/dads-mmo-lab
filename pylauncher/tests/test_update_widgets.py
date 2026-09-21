"""Tests for the update bar and the what's-new dialog (T90).

Offscreen, through the suite's session `QApplication`. Nothing here talks to
GitHub: every `UpdateCheck` is built by hand, which is the whole point of the
check being a plain frozen dataclass.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt, QUrl
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
    _strip_resources,
)
from yulon.update import UpdateCheck

STRIP_BOUND = 10.0
"""A deadlock/quadratic breaker for the strip, not a claim about how fast Qt is.

Measured on the dev box: 0.12 s for the 20,000-image body this bounds, and
22.8 s before the fix. Eighty times the measurement and a fifth of the
defect, so a loaded CI runner cannot flake it and a return to quadratic
behaviour cannot hide under it.
"""

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


def test_the_bar_text_can_be_selected_with_the_mouse_and_is_still_plain(qapp: object) -> None:
    """A message can carry a URL the player has to get out of the app by hand.

    On a box with no browser and no `xdg-open` (yulon-arch, gate of
    2026-09-21) that URL is the only route to the release, so it has to be
    selectable — and still `PlainText`, because it comes off the network.
    """
    from PySide6.QtCore import Qt

    bar = UpdateBar()
    bar.show_message("Could not open a browser. The download page is: https://example.invalid/r")

    assert bar.label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert bar.label.textFormat() == Qt.TextFormat.PlainText


def test_the_dialog_hands_its_notes_the_opener_it_was_given(qapp: object) -> None:
    """One opener for both routes out of this dialog, so one place decides the fallback."""
    handed: list[str] = []

    def opener(url: str) -> bool:
        handed.append(url)
        return True

    dialog = UpdateDialog(RESULT, open_url=opener)

    dialog.notes.anchorClicked.emit(QUrl("https://example.invalid/notes"))

    assert handed == ["https://example.invalid/notes"]


def test_the_notes_opener_answers_whether_it_opened(qapp: object) -> None:
    """`Callable[[str], bool]`: the widget passes the answer back to its caller."""
    dialog = UpdateDialog(RESULT, open_url=lambda url: False)

    assert dialog.notes.open_link(QUrl("https://example.invalid/x")) is False

    dialog_ok = UpdateDialog(RESULT, open_url=lambda url: True)
    assert dialog_ok.notes.open_link(QUrl("https://example.invalid/x")) is True
    assert dialog_ok.notes.open_link(QUrl("file:///etc/passwd")) is False, "refused, not opened"


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


def _resources_in(document: QTextDocument) -> list[str]:
    """Every format in `document` that still names something Qt would go and load.

    Not just image char-formats: `<table background=…>` and `<td background=…>`
    put the file on the format's BRUSH, which `isImageFormat()` never sees and
    `loadResource` does not stop — the second cold review's finding, and the
    reason this helper walks formats rather than counting images.
    """
    from PySide6.QtGui import QTextTable

    found: list[str] = []

    def textured(brush: Any) -> bool:
        return bool(brush.style() == Qt.BrushStyle.TexturePattern) or not (
            brush.textureImage().isNull()
        )

    frames = [document.rootFrame()]
    while frames:
        frame = frames.pop()
        frames.extend(frame.childFrames())
        if textured(frame.frameFormat().background()):
            found.append(f"frame background {frame.frameFormat().background()}")
        if isinstance(frame, QTextTable):
            for row in range(frame.rows()):
                for column in range(frame.columns()):
                    cell = frame.cellAt(row, column)
                    if cell.isValid() and textured(cell.format().background()):
                        found.append(f"table cell background at {row},{column}")

    block = document.begin()
    while block.isValid():
        if textured(block.blockFormat().background()):
            found.append(f"block background at {block.position()}")
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                char_format = fragment.charFormat()
                if char_format.isImageFormat():
                    found.append(f"image {char_format.toImageFormat().name()!r}")
                if textured(char_format.background()):
                    found.append("char background")
            iterator += 1
        block = block.next()

    if document.baseUrl().toString():
        found.append(f"baseUrl {document.baseUrl().toString()!r}")
    document_url = document.metaInformation(QTextDocument.MetaInformation.DocumentUrl)
    if document_url:
        found.append(f"DocumentUrl {document_url!r}")
    return found


@pytest.fixture
def a_red_png(tmp_path: Path) -> Path:
    """A file the notes box must never render, in a colour a screenshot can count."""
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(qRgb(255, 0, 0))
    path = tmp_path / "red.png"
    assert image.save(str(path)), "the probe image was not written"
    return path


def _bodies_that_must_load_nothing(png: Path) -> dict[str, str]:
    """Every shape the two cold reviews found, in one place.

    The first five were door 1's and door 2's; the rest are the second review's
    — where a regex and md4c disagreed about what a fence is, so raw block HTML
    reached `setMarkdown` and `<table background=…>` painted the file as a
    BRUSH, which no image walk and no `loadResource` could see.
    """
    table = f"<table background='{png}' width=300 height=200><tr><td>x</td></tr></table>"
    return {
        "inline-file-url": f"![i](file://{png})",
        "bare-absolute-path": f"![i]({png})",
        "reference-style": f"![i][ref]\n\n[ref]: file://{png}",
        "unc-path": "![i](//host/share/x.png)",
        "html-img": f"<img src='file://{png}' width=300 height=200>",
        # The regex read line 1 as a fence opener; md4c read it as a paragraph,
        # because a backtick in an info string is not a fence.
        "backtick-in-info-string": f"``` a`b\n\n{table}\n\n```\n",
        # md4c closes a fence indented up to three spaces; the regex did not.
        "closing-fence-indented": f"```\ncode\n   ```\n\n{table}\n\n```\n",
        "td-background": (
            f"<table><tr><td background='{png}' width=300 height=200>x</td></tr></table>"
        ),
        "body-background": f"<body background='{png}'><p>x</p></body>",
        "double-bang-image": f"!![i]({png})",
        "image-in-unbalanced-backticks": f"`` ![i]({png}) `",
        "never-closed-fence-then-html": f"```\n\n{table}\n",
        "indented-code-block-html": f"    {table}\n",
        "qrc-image": "![i](qrc:/x/red.png)",
        "unc-file-url": "![i](file://host/share/x.png)",
        "data-uri": (
            "![i](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==)"
        ),
    }


@pytest.mark.parametrize("shape", list(_bodies_that_must_load_nothing(Path("/x/red.png"))))
def test_nothing_in_a_release_body_makes_the_dialog_load_a_file(
    qapp: object, a_red_png: Path, shape: str
) -> None:
    """Rendered and counted, not reasoned about. 0 red pixels and no resource left.

    Measured before the fix, through the real dialog: the two fence shapes put
    **59,978 red pixels** on screen the moment the dialog opened.
    """
    body = _bodies_that_must_load_nothing(a_red_png)[shape]
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    notes = _notes(dialog)

    assert _red_pixels(notes) == 0, "the notes box painted a file off the disk"
    assert _resources_in(notes.document()) == []


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


def test_the_strip_runs_at_the_call_site_and_says_what_it_took(
    qapp: object, a_red_png: Path
) -> None:
    """`set_release_body` is where the walk has to happen, and it reports the count.

    Not a test of `_strip_resources` on its own — deleting the CALL and keeping
    the function is a thing that has already passed a mutation here once
    ("reviews check functions, not call sites"). A markdown image is the one
    shape `MarkdownNoHTML` does NOT stop, so this drives the real method.
    """
    notes = _NotesView()

    removed = notes.set_release_body(f"![i](file://{a_red_png})")

    assert removed == 1, "set_release_body did not strip the image"
    assert _resources_in(notes.document()) == []
    assert _red_pixels(notes) == 0
    assert IMAGE_REMOVED in notes.toPlainText()


def test_twenty_thousand_images_do_not_hold_the_gui_thread(qapp: object) -> None:
    """Measured before the fix: `"![a](x) " * 20000` spent **22.8 s** in the strip.

    One `insertText` per image, each re-laying the document out and pushing an
    undo step — quadratic on a 160 KB body that fits in one GitHub release
    (third cold review, 2026-09-21). Collected first and applied inside one
    `beginEditBlock` with undo off, the same body is ~0.12 s here.

    The bound is deliberately loose (10 s against a measured 0.12 s) so a
    loaded CI runner cannot flake it: what it catches is the return of
    quadratic behaviour, which was 200x over this bound, not a slow box.
    """
    body = "![a](x) " * 20000
    document = QTextDocument()
    document.setMarkdown(body, update_dialog.SAFE_MARKDOWN)

    started = time.monotonic()
    removed = _strip_resources(document)
    elapsed = time.monotonic() - started

    assert removed >= 20000, f"the premise: this document holds 20,000 images, not {removed}"
    assert elapsed < STRIP_BOUND, f"the strip took {elapsed:.1f}s for {removed} images"


def test_the_cap_keeps_that_body_away_from_the_dialog_in_the_first_place(
    qapp: object,
) -> None:
    """The algorithm is linear now AND the call site is capped — both, not either.

    The cap alone hid the quadratic behaviour from the timing test above:
    64k characters is ~8,000 images, which the slow version got through inside
    the bound. That is why the test above builds the document itself.
    """
    notes = _NotesView()

    removed = notes.set_release_body("![a](x) " * 20000)

    assert 0 < removed < 20000, f"the cap let {removed} images through"


def _anchors_in(document: QTextDocument) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            char_format = iterator.fragment().charFormat()
            if char_format.isAnchor():
                found.append((iterator.fragment().text(), char_format.anchorHref()))
            iterator += 1
        block = block.next()
    return found


def test_qt_itself_drops_a_badge_links_href_before_this_app_sees_it(qapp: object) -> None:
    """The third review said the replacement killed badge links. It does not — Qt does.

    Measured on 6.11: for `[![badge](img)](https://…)` the href is **nowhere in
    the document** after `setMarkdown`, with or without `MarkdownNoHTML`, and
    the one fragment is a bare image with `isAnchor()` False. An ordinary
    `[text](url)` link keeps its anchor perfectly. So there is no anchor here
    for the replacement to drop, and this app cannot put back what the parser
    never produced — pinned so the claim is not made again from either side.

    Not worked around with another markdown rewrite: racing md4c with a regex
    is the defect the previous review spent itself on. The release page stays
    one button away on the dialog's own action.
    """
    body = "[![badge](https://img.example/b.svg)](https://github.com/DadsMmoLab/dads-mmo-lab)"
    raw = QTextDocument()
    raw.setMarkdown(body, update_dialog.SAFE_MARKDOWN)

    assert _anchors_in(raw) == [], "Qt kept an anchor after all — then this app must too"
    assert "github.com/DadsMmoLab" not in raw.toHtml()

    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))
    assert _resources_in(_notes(dialog).document()) == []
    assert IMAGE_REMOVED in _notes(dialog).toPlainText()


def test_an_image_inside_a_link_keeps_the_link_when_there_is_one(
    qapp: object, a_red_png: Path
) -> None:
    """`_mark_format`'s reason, driven directly because markdown cannot reach it.

    If an image fragment DOES carry an anchor — a document built by hand, or a
    future Qt that keeps it — the href is the author's text and survives the
    replacement; only the resource goes.
    """
    from PySide6.QtGui import QTextCharFormat, QTextCursor

    document = QTextDocument()
    cursor = QTextCursor(document)
    image = QTextCharFormat()
    image.setAnchor(True)
    image.setAnchorHref("https://github.com/DadsMmoLab/dads-mmo-lab")
    cursor.insertImage(str(a_red_png))
    cursor.setPosition(0)
    cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    merged = QTextCharFormat(cursor.charFormat())
    merged.setAnchor(True)
    merged.setAnchorHref("https://github.com/DadsMmoLab/dads-mmo-lab")
    cursor.setCharFormat(merged)
    assert _anchors_in(document), "the premise: an image fragment carrying an anchor"

    _strip_resources(document)

    assert _anchors_in(document) == [(IMAGE_REMOVED, "https://github.com/DadsMmoLab/dads-mmo-lab")]
    assert _resources_in(document) == []


def test_a_textured_run_of_text_keeps_its_text(qapp: object, a_red_png: Path) -> None:
    """A background is the brush's fault, not the text's: clear one, keep the other."""
    from PySide6.QtGui import QBrush, QPixmap, QTextCharFormat, QTextCursor

    document = QTextDocument()
    cursor = QTextCursor(document)
    char_format = QTextCharFormat()
    char_format.setBackground(QBrush(QPixmap(str(a_red_png))))
    cursor.insertText("the author's own words", char_format)

    _strip_resources(document)

    assert document.toPlainText() == "the author's own words"
    assert _resources_in(document) == []


def test_the_strip_reports_nothing_to_do_on_an_ordinary_body(qapp: object) -> None:
    notes = _NotesView()

    assert notes.set_release_body("### New\n- Ten.\n") == 0


def test_a_fenced_block_shows_its_angle_brackets_literally(qapp: object) -> None:
    """`MarkdownNoHTML` means no escaping pre-pass, so code is code again.

    The `<`-escaping this replaces showed the reader `&lt;config_dir>` inside a
    code span until the first review, and then needed a regex that had to agree
    with md4c about what a fence is — which is the bug the second review found.
    """
    body = "- the dir is `<config_dir>` on Linux\n\n```\n<not a tag>\nif x < 3: pass\n```\n"
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    shown = _notes(dialog).toPlainText()

    assert "<config_dir>" in shown
    assert "<not a tag>" in shown
    assert "if x < 3: pass" in shown
    assert "&lt;" not in shown


def test_raw_html_is_shown_as_text_and_swallows_nothing(qapp: object) -> None:
    """The md4c "the rest of the body disappears" finding, fixed at its cause.

    With `MarkdownNoHTML` md4c never treats the tags as HTML at all, so there
    is no HTML block to swallow the bullet under it.
    """
    body = "## v0.8.70-Public\n\n<img src='http://example.invalid/x.png'><script>x</script>\n- Ten."
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    shown = _notes(dialog).toPlainText()

    assert "Ten." in shown
    assert "<script>x</script>" in shown


def test_an_autolink_survives_and_is_still_a_link(qapp: object) -> None:
    """`<https://…>` is not HTML, and NoHTML must not take it for HTML."""
    body = "see <https://github.com/DadsMmoLab/dads-mmo-lab> for more"
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    notes = _notes(dialog)

    assert "https://github.com/DadsMmoLab/dads-mmo-lab" in notes.toPlainText()
    assert "https://github.com/DadsMmoLab/dads-mmo-lab" in notes.document().toHtml()


def test_the_github_dialect_is_kept(qapp: object) -> None:
    """NoHTML is added to the GitHub dialect, not used instead of it."""
    body = "| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~\n"
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    shown = _notes(dialog).toPlainText()

    assert "1" in shown and "2" in shown, "the table was not parsed"
    assert "gone" in shown


def _a_nest_of_textured_formats(png: Path) -> QTextDocument:
    """A 3x3 table with merged cells, a nested 2x2, a frame, and a textured run.

    Markdown cannot produce this — with `MarkdownNoHTML` nothing can — so it is
    built by hand. It exists because each of these mutations left all 63 tests
    green (third cold review, 2026-09-21): dropping `childFrames()` from the
    walk, turning the cell condition off, and dropping the character-background
    half. A walk nothing nested is ever fed is a walk nobody is testing.
    """
    from PySide6.QtGui import (
        QBrush,
        QPixmap,
        QTextCharFormat,
        QTextCursor,
        QTextFrameFormat,
        QTextTableFormat,
    )

    def textured() -> QBrush:
        return QBrush(QPixmap(str(png)))

    document = QTextDocument()
    cursor = QTextCursor(document)
    table_format = QTextTableFormat()
    table_format.setBackground(textured())
    outer = cursor.insertTable(3, 3, table_format)
    for row in range(3):
        for column in range(3):
            cell = outer.cellAt(row, column)
            cell_format = cell.format()
            cell_format.setBackground(textured())
            cell.setFormat(cell_format)
    outer.mergeCells(0, 0, 2, 2)

    inner = outer.cellAt(2, 2).firstCursorPosition().insertTable(2, 2, table_format)
    for row in range(2):
        for column in range(2):
            cell = inner.cellAt(row, column)
            cell_format = cell.format()
            cell_format.setBackground(textured())
            cell.setFormat(cell_format)

    frame_format = QTextFrameFormat()
    frame_format.setBackground(textured())
    frame = inner.cellAt(1, 1).firstCursorPosition().insertFrame(frame_format)
    char_format = QTextCharFormat()
    char_format.setBackground(textured())
    frame.firstCursorPosition().insertText("deep", char_format)
    return document


def test_the_walk_reaches_a_nested_table_a_merged_cell_and_a_textured_run(
    qapp: object, a_red_png: Path
) -> None:
    """Every level of the nest, because three ways of not walking it passed 63/63."""
    document = _a_nest_of_textured_formats(a_red_png)
    assert _resources_in(document), "the premise: this document names a file, repeatedly"

    cleared = _strip_resources(document)

    assert cleared >= 14, f"only {cleared} formats were cleared"
    assert _resources_in(document) == []
    assert "deep" in document.toPlainText(), "the text under the brush was taken with it"


def test_the_walk_clears_a_textured_brush_no_markdown_can_currently_make(
    qapp: object, a_red_png: Path
) -> None:
    """The belt-and-braces half of door 2, driven directly because nothing else reaches it.

    With `MarkdownNoHTML` no release body can put a texture on a format any
    more — that is what the flag is for — so this builds the document by hand
    and hands it to the walk. Without this the widened walk would be code that
    nothing exercises.
    """
    from PySide6.QtGui import QBrush, QPixmap, QTextCursor

    document = QTextDocument()
    cursor = QTextCursor(document)
    cursor.insertText("x")
    block_format = cursor.blockFormat()
    block_format.setBackground(QBrush(QPixmap(str(a_red_png))))
    cursor.setBlockFormat(block_format)
    frame_format = document.rootFrame().frameFormat()
    frame_format.setBackground(QBrush(QPixmap(str(a_red_png))))
    document.rootFrame().setFrameFormat(frame_format)
    document.setBaseUrl(QUrl.fromLocalFile(str(a_red_png.parent)))
    document.setMetaInformation(
        QTextDocument.MetaInformation.DocumentUrl, QUrl.fromLocalFile(str(a_red_png)).toString()
    )
    assert _resources_in(document), "the premise: this document names a file"

    cleared = _strip_resources(document)

    assert cleared >= 2
    assert _resources_in(document) == []
    # Both bases, because a relative name in a future body would be resolved
    # against either. Neither can be set from markdown today, which is why this
    # test sets them by hand — see `_strip_resources`.
    assert document.baseUrl().toString() == ""
    assert document.metaInformation(QTextDocument.MetaInformation.DocumentUrl) == ""


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


def test_the_notes_box_has_no_load_resource_override_because_it_never_helped(
    qapp: object, a_red_png: Path
) -> None:
    """Deleting it was invisible to every test — so it was measured instead.

    A document holding an image, set on the widget without going through
    `set_release_body` (so the strip never runs), renders **60,000 red pixels**
    with the override and 60,000 without it: Qt's own image handling opens the
    path when the resource comes back null. The defence was dead, and dead
    defence reads like a guard.
    """
    view = _NotesView()
    document = QTextDocument(view)
    document.setMarkdown(f"![i]({a_red_png})", update_dialog.SAFE_MARKDOWN)
    view.setDocument(document)

    assert _red_pixels(view) > 0, "if this is 0, Qt changed and the override may be worth having"
    assert "loadResource" not in dir(_NotesView) or "_NotesView" not in str(
        _NotesView.loadResource
    ), "the override is back without a measurement saying it works"


@pytest.mark.parametrize(
    "body",
    [
        "the dir is `<config_dir>` on Linux",
        "```\n<not a tag>\n```",
        "~~~sh\nif [ $x -lt 3 ]; then echo '<'; fi\n~~~",
        "``a span with a ` in it and <b>``",
    ],
)
def test_code_reaches_the_reader_as_the_author_wrote_it(qapp: object, body: str) -> None:
    """Measured through the rendered document, not through an intermediate string.

    The `<`-escaping this replaces had to know where a fence began to leave it
    alone, which is precisely what it got wrong. `MarkdownNoHTML` means nothing
    has to know.
    """
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_markdown=body))

    shown = _notes(dialog).toPlainText()

    assert "&lt;" not in shown
    assert "<" in shown


def test_the_header_takes_an_action_left_of_the_badge(qapp: object) -> None:
    header = DadcraftHeader()
    button = QPushButton("Check for updates")

    header.add_action(button)

    layout = header.layout()
    assert layout.indexOf(button) == layout.indexOf(header._badge) - 1
    assert button.parent() is header
