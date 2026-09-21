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
from PySide6.QtCore import QObject, Qt, QUrl, SignalInstance
from PySide6.QtGui import QImage, QTextDocument, QTextFormat, qRgb
from PySide6.QtWidgets import QLabel, QPushButton, QTextBrowser

from tests.conftest import process_events
from yulon.ui.widgets import update_dialog
from yulon.ui.widgets.dadcraft_decorations import DadcraftHeader
from yulon.ui.widgets.update_bar import UpdateBar
from yulon.ui.widgets.update_dialog import (
    BODY_CUT_NOTE,
    IMAGE_REMOVED,
    MAX_SECTIONS,
    OLDER_CUT_NOTE,
    SAFE_MARKDOWN,
    TAG_CHARS,
    TAG_SIZE_ADJUSTMENT,
    UpdateChoice,
    UpdateDialog,
    _NotesView,
    _strip_resources,
)
from yulon.ui.widgets.update_progress import (
    CANCELLING,
    UpdateProgressDialog,
    as_megabytes,
)
from yulon.update import MAX_BODY_CHARS, MAX_NOTES_CHARS, ReleaseNotes, UpdateCheck

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
    notes=(ReleaseNotes("v0.8.70-Public", "### New\n- Ten.", False),),
    assets=(),
    has_checksums=False,
)


def _with_body(body: str, *, cut: bool = False) -> UpdateCheck:
    """`RESULT` with one release whose notes are `body`. The usual shape here."""
    return dataclasses.replace(RESULT, notes=(ReleaseNotes("v0.8.70-Public", body, cut),))


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
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes=()))

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
    dialog = UpdateDialog(_with_body(body))

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
    dialog = UpdateDialog(_with_body(bodies[shape]))

    notes = _notes(dialog)

    assert _red_pixels(notes) == 0, "the notes box rendered a file off the disk"
    assert _image_fragments(notes) == 0, "an image fragment is a name Qt will resolve"


def test_the_strip_runs_at_the_call_site_and_says_what_it_took(
    qapp: object, a_red_png: Path
) -> None:
    """`set_release_notes` is where the walk has to happen, and it reports the count.

    Not a test of `_strip_resources` on its own — deleting the CALL and keeping
    the function is a thing that has already passed a mutation here once
    ("reviews check functions, not call sites"). A markdown image is the one
    shape `MarkdownNoHTML` does NOT stop, so this drives the real method.
    """
    notes = _NotesView()

    removed = notes.set_release_notes(
        [ReleaseNotes("v1.0.0-Public", f"![i](file://{a_red_png})", False)]
    )

    assert removed == 1, "set_release_notes did not strip the image"
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

    removed = notes.set_release_notes([ReleaseNotes("v1.0.0-Public", "![a](x) " * 20000, False)])

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

    dialog = UpdateDialog(_with_body(body))
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

    assert notes.set_release_notes([ReleaseNotes("v1.0.0-Public", "### New\n- Ten.", False)]) == 0


def test_a_fenced_block_shows_its_angle_brackets_literally(qapp: object) -> None:
    """`MarkdownNoHTML` means no escaping pre-pass, so code is code again.

    The `<`-escaping this replaces showed the reader `&lt;config_dir>` inside a
    code span until the first review, and then needed a regex that had to agree
    with md4c about what a fence is — which is the bug the second review found.
    """
    body = "- the dir is `<config_dir>` on Linux\n\n```\n<not a tag>\nif x < 3: pass\n```\n"
    dialog = UpdateDialog(_with_body(body))

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
    dialog = UpdateDialog(_with_body(body))

    shown = _notes(dialog).toPlainText()

    assert "Ten." in shown
    assert "<script>x</script>" in shown


def test_an_autolink_survives_and_is_still_a_link(qapp: object) -> None:
    """`<https://…>` is not HTML, and NoHTML must not take it for HTML."""
    body = "see <https://github.com/DadsMmoLab/dads-mmo-lab> for more"
    dialog = UpdateDialog(_with_body(body))

    notes = _notes(dialog)

    assert "https://github.com/DadsMmoLab/dads-mmo-lab" in notes.toPlainText()
    assert "https://github.com/DadsMmoLab/dads-mmo-lab" in notes.document().toHtml()


def test_the_github_dialect_is_kept(qapp: object) -> None:
    """NoHTML is added to the GitHub dialect, not used instead of it."""
    body = "| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~\n"
    dialog = UpdateDialog(_with_body(body))

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


def _mixed_document(png: Path, order: str) -> QTextDocument:
    """Images and a textured run in a chosen order, with the images NON-adjacent.

    Adjacent identical images merge into one fragment of length 2 — the same
    length as the marker that replaces them — which hides the position shift
    this test exists for (the fourth cold review's trap). The `p` between each
    pair is what keeps them separate.
    """
    from PySide6.QtGui import QBrush, QPixmap, QTextCharFormat, QTextCursor

    document = QTextDocument()
    cursor = QTextCursor(document)
    textured = QTextCharFormat()
    textured.setBackground(QBrush(QPixmap(str(png))))

    def images() -> None:
        for _ in range(3):
            cursor.insertImage(str(png))
            cursor.insertText("p")

    if order == "images-then-brush":
        images()
        cursor.insertText("RED", textured)
    elif order == "brush-then-images":
        cursor.insertText("RED", textured)
        images()
    else:  # image inside the textured span
        cursor.insertText("RE", textured)
        cursor.insertImage(str(png))
        cursor.insertText("D", textured)
    bold = QTextCharFormat()
    bold.setFontWeight(700)
    cursor.insertText("tail", bold)
    return document


@pytest.mark.parametrize("order", ["images-then-brush", "brush-then-images", "image-inside-brush"])
def test_an_image_before_a_textured_span_does_not_leave_it_textured(
    qapp: object, a_red_png: Path, order: str
) -> None:
    """The fallback door was open in exactly the case it exists for.

    Measured (fourth cold review, 2026-09-21): images and brushes were two
    passes over positions collected before either ran, and `IMAGE_REMOVED` is
    two UTF-16 units where an image is one — so every brush range after an
    image was off by one per image. `img p img p img p TEXTURED tail` came out
    with the textured run STILL textured and the plain format stamped over
    `🖼p`.
    """
    document = _mixed_document(a_red_png, order)
    assert _resources_in(document), "the premise: this document names a file"

    _strip_resources(document)

    assert _resources_in(document) == []
    assert document.toPlainText().endswith("tail"), "the tail was overwritten"


def test_the_nest_survives_an_image_in_front_of_it(qapp: object, a_red_png: Path) -> None:
    """The same shift, in the shape the third review built: images added before the nest."""
    from PySide6.QtGui import QTextCursor

    document = _a_nest_of_textured_formats(a_red_png)
    cursor = QTextCursor(document)
    cursor.setPosition(0)
    for _ in range(3):
        cursor.insertImage(str(a_red_png))
        cursor.insertText("p")

    _strip_resources(document)

    assert _resources_in(document) == []


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
    dialog = UpdateDialog(_with_body(body))

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
    dialog = UpdateDialog(_with_body(body))

    shown = _notes(dialog).toPlainText()
    assert "Ten." in shown, "the bullet after the HTML was dropped"
    assert "<script>" in shown, "the tag must be shown as text, not parsed away"


def test_the_notes_box_has_no_load_resource_override_because_it_never_helped(
    qapp: object, a_red_png: Path
) -> None:
    """Deleting it was invisible to every test — so it was measured instead.

    A document holding an image, set on the widget without going through
    `set_release_notes` (so the strip never runs), renders **60,000 red pixels**
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
    dialog = UpdateDialog(_with_body(body))

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


# ------------------------------------ one document per release, sealed from the rest


def _document_blocks(document: QTextDocument) -> list:
    out = []
    block = document.begin()
    while block.isValid():
        out.append(block)
        block = block.next()
    return out


def _sections(hostile: str, position: int, count: int = 3) -> tuple[ReleaseNotes, ...]:
    """`count` releases, with `hostile` in slot `position` and harmless notes elsewhere."""
    bodies = [f"### Fixed\n- release {n}." for n in range(count)]
    bodies[position] = hostile
    return tuple(
        ReleaseNotes(f"v1.{count - n}.0-Public", body, False) for n, body in enumerate(bodies)
    )


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize(
    "shape", ["inline-file-url", "unc-path", "html-img", "td-background", "qrc-image"]
)
def test_a_resource_in_any_section_still_resolves_to_nothing(
    qapp: object, a_red_png: Path, position: int, shape: str
) -> None:
    """The strip runs per section, and `insertFragment` must not undo it.

    Copying a fragment between documents is Qt's code rather than this file's,
    and a format carried across would be a name Qt resolves at layout — a file
    off the disk on screen. Measured in the first, middle and last section,
    because a defence that only holds in slot 0 is not a defence.
    """
    hostile = _bodies_that_must_load_nothing(a_red_png)[shape]
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes=_sections(hostile, position)))

    notes = _notes(dialog)

    assert _red_pixels(notes) == 0, f"slot {position}: a file off the disk was painted"
    assert _image_fragments(notes) == 0, f"slot {position}: an image fragment survived"
    assert _resources_in(notes.document()) == [], f"slot {position}: a format still names a file"


def test_every_section_gets_its_tag_as_a_heading_of_its_own(qapp: object) -> None:
    """The tag is inserted as a block, not parsed, so a release cannot reach past it."""
    dialog = UpdateDialog(
        dataclasses.replace(
            RESULT,
            notes=(
                ReleaseNotes("v1.2.0-Public", "```\nunclosed", False),
                ReleaseNotes("v1.1.0-Public", "### Fixed\n- older.", False),
            ),
        )
    )

    levels = {
        block.text().strip(): block.blockFormat().headingLevel()
        for block in _document_blocks(_notes(dialog).document())
        if "-Public" in block.text()
    }

    assert levels == {"v1.2.0-Public": 2, "v1.1.0-Public": 2}


def test_a_tag_that_is_markdown_is_shown_as_text(qapp: object) -> None:
    """A tag is remote text. Rendered as markdown it could open a fence of its own."""
    dialog = UpdateDialog(
        dataclasses.replace(
            RESULT,
            notes=(
                ReleaseNotes("v1.0.0-Public\n```", "### Fixed\n- one.", False),
                ReleaseNotes("v0.9.0-Public", "### Fixed\n- older.", False),
            ),
        )
    )

    blocks = _document_blocks(_notes(dialog).document())
    older = [b for b in blocks if b.text().strip() == "v0.9.0-Public"]

    assert older and older[0].blockFormat().headingLevel() == 2, "the hostile tag reached the next"
    assert "```" in _notes(dialog).toPlainText(), "the backticks were interpreted, not shown"


def test_a_cut_release_says_so_once_and_as_a_paragraph(qapp: object) -> None:
    dialog = UpdateDialog(_with_body("### Fixed\n- one.", cut=True))

    shown = _notes(dialog).toPlainText()
    blocks = [b for b in _document_blocks(_notes(dialog).document()) if BODY_CUT_NOTE in b.text()]

    assert shown.count(BODY_CUT_NOTE) == 1
    assert blocks[0].blockFormat().headingLevel() == 0
    assert not blocks[0].charFormat().fontFixedPitch()


def test_dropped_releases_say_so_once_and_as_a_paragraph(qapp: object) -> None:
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes_cut=True))

    shown = _notes(dialog).toPlainText()
    blocks = [b for b in _document_blocks(_notes(dialog).document()) if OLDER_CUT_NOTE in b.text()]

    assert shown.count(OLDER_CUT_NOTE) == 1
    assert blocks[0].blockFormat().headingLevel() == 0
    assert not blocks[0].charFormat().fontFixedPitch()


def test_neither_notice_appears_when_nothing_was_left_out(qapp: object) -> None:
    """The glued assembly said "the rest is on the release page" TWICE in 62 of 200 feeds."""
    shown = _notes(UpdateDialog(RESULT)).toPlainText()

    assert BODY_CUT_NOTE not in shown and OLDER_CUT_NOTE not in shown


def test_the_dialog_caps_what_it_is_handed_rather_than_trusting_it(qapp: object) -> None:
    """`notes` is an ordinary field and this widget is public.

    An `UpdateCheck` built by hand — a test, or a plan-3 code path — would
    otherwise hand Qt bodies that take minutes to lay out on the GUI thread.
    """
    handed = tuple(ReleaseNotes(f"v1.{n}.0-Public", "x" * 100000, False) for n in range(400, 0, -1))

    shown = _notes(UpdateDialog(dataclasses.replace(RESULT, notes=handed))).toPlainText()

    assert len(shown) <= MAX_NOTES_CHARS + MAX_BODY_CHARS
    assert OLDER_CUT_NOTE in shown, "it dropped releases and said nothing"
    assert BODY_CUT_NOTE in shown, "it cut a body and said nothing"


def test_more_sections_than_it_will_lay_out_are_not_laid_out(qapp: object) -> None:
    """`MAX_NOTES_CHARS` bounds characters, not documents: 700 one-line releases fit."""
    handed = tuple(ReleaseNotes(f"v1.{n}.0-Public", "- one line", False) for n in range(700, 0, -1))

    shown = _notes(UpdateDialog(dataclasses.replace(RESULT, notes=handed))).toPlainText()

    assert shown.count("-Public") == MAX_SECTIONS
    assert OLDER_CUT_NOTE in shown


def test_no_notes_at_all_still_says_something(qapp: object) -> None:
    shown = _notes(UpdateDialog(dataclasses.replace(RESULT, notes=()))).toPlainText()

    assert "No release notes" in shown
    assert BODY_CUT_NOTE not in shown and OLDER_CUT_NOTE not in shown


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("images", [1, 3])
def test_a_resource_is_cleared_exactly_once(
    qapp: object, a_red_png: Path, position: int, images: int
) -> None:
    """The COUNT is the pin, and the previous version of this test was not one.

    It stripped a document the production assembled pass had already stripped,
    so it read 0 whether or not `insertFragment` had resurrected anything
    (seventh cold review, 2026-09-21). `set_release_notes` adds up both passes,
    so a resource carried across by the fragment copy is counted TWICE: one
    image must report exactly one cleared resource, three must report three.

    What this does NOT pin, said plainly: **neither strip is pinnable on its
    own.** Removing either one leaves this count right, and leaves every test
    here green, because the other one does the clearing. Measured (red pixels
    in a `grab()` / image fragments left, one 300x200 red PNG):

        case             no strip   section only   assembled only   both
        markdown image    60000/1            0/0              0/0    0/0
        td background         0/0            0/0              0/0    0/0
        UNC path              0/1            0/0              0/0    0/0

    Both are kept for the different questions they answer — the section pass
    guarantees nothing unstripped ever enters the target, the assembled pass
    is what would catch Qt's fragment copy carrying a format across — and the
    fact that either alone suffices is written down rather than dressed up as
    a test that cannot fail.
    """
    hostile = " ".join(f"![i](file://{a_red_png})" for _ in range(images))
    view = _NotesView()
    try:
        cleared = view.set_release_notes(_sections(hostile, position))

        assert cleared == images, (
            f"slot {position}: {cleared} cleared for {images} image(s) — "
            "more means the fragment copy brought one back and both passes counted it"
        )
        assert _strip_resources(view.document()) == 0
    finally:
        view.deleteLater()


# ------------------------------ a section's blocks, compared with the same body alone

BODY_OPENINGS = {
    "atx h1": "# Release one\n\nafter",
    "atx h2": "## What's new\n\n- item one\n- item two",
    "atx h3": "### Fixed\n\nafter",
    "setext heading": "Underlined\n==========\n\nafter",
    "fenced code": "```sh\nyulon --update\n```\n\nafter",
    "indented code": "    indented code\n\nafter",
    "block quote": "> quoted first\n\nafter",
    "bullet list": "- first item\n- second",
    "ordered list": "1. one\n2. two",
    "task list": "- [x] done\n- [ ] not done",
    "table": "| a | b |\n| - | - |\n| 1 | 2 |\n\nafter",
    "rule": "---\n\nafter",
    "paragraph": "just a paragraph\n\nand another",
}
"""Every block type a release body can begin with. The real ones begin with a heading."""


def _shape(block: object) -> tuple:
    """Everything about a block that `insertFragment` was measured to drop."""
    block_format = block.blockFormat()  # type: ignore[attr-defined]
    iterator = block.begin()  # type: ignore[attr-defined]
    char = (
        iterator.fragment().charFormat()
        if not iterator.atEnd()
        else block.charFormat()  # type: ignore[attr-defined]
    )
    return (
        block.text(),  # type: ignore[attr-defined]
        block_format.headingLevel(),
        block.textList() is not None,  # type: ignore[attr-defined]
        block_format.intProperty(QTextFormat.Property.BlockQuoteLevel),
        block_format.hasProperty(QTextFormat.Property.BlockCodeFence),
        block_format.hasProperty(QTextFormat.Property.BlockCodeLanguage),
        block_format.hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth),
        block_format.topMargin(),
        block_format.bottomMargin(),
        block_format.indent(),
        char.fontFixedPitch(),
        char.fontWeight(),
        char.property(QTextFormat.Property.FontSizeAdjustment),
    )


def _section_shapes(document: QTextDocument, tag: str) -> list[tuple]:
    """Every block after `tag`'s heading, described the same way."""
    blocks = _document_blocks(document)
    start = next(n for n, b in enumerate(blocks) if b.text().strip() == tag)
    return [_shape(b) for b in blocks[start + 1 :]]


@pytest.mark.parametrize("opening", list(BODY_OPENINGS))
def test_a_body_renders_in_the_dialog_as_it_renders_alone(qapp: object, opening: str) -> None:
    """Block for block, format for format. The splice may change nothing.

    `insertFragment` merges the fragment's first block into the block the
    cursor is in and keeps the TARGET's block format, so before the seventh
    cold review a body's opening block arrived stripped: `## What's new` at
    heading level 0, a ` ```sh ` block with no code-fence property, `> quoted`
    with no quote level and no margins. Every release body this project cuts
    starts with a heading, so every real release hit it — and nothing in the
    suite compared the two renderings, which is the only way to see it.
    """
    body = BODY_OPENINGS[opening]
    alone = QTextDocument()
    alone.setMarkdown(body, SAFE_MARKDOWN)
    dialog = UpdateDialog(_with_body(body))

    spliced = _section_shapes(_notes(dialog).document(), "v0.8.70-Public")

    assert spliced == [
        _shape(b) for b in _document_blocks(alone)
    ], f"{opening}: the body renders differently inside the dialog"


@pytest.mark.parametrize("opening", list(BODY_OPENINGS))
def test_nothing_of_the_dialogs_own_sits_between_a_tag_and_its_notes(
    qapp: object, opening: str
) -> None:
    """A list or a table does not merge, and the block prepared for it stayed behind.

    The gap under the tag then depended on what the body began with, which is
    a difference the reader sees and nothing else explains. Asserted against
    the body's OWN first block rather than "is it empty": a `---` rule and a
    table both begin with a block that holds no text, and both are the body's.
    """
    body = BODY_OPENINGS[opening]
    alone = QTextDocument()
    alone.setMarkdown(body, SAFE_MARKDOWN)
    dialog = UpdateDialog(_with_body(body))

    blocks = _document_blocks(_notes(dialog).document())
    after_tag = blocks[[b.text().strip() for b in blocks].index("v0.8.70-Public") + 1]

    assert _shape(after_tag) == _shape(
        alone.firstBlock()
    ), f"{opening}: what sits under the tag is not the body's own first block"


def test_the_document_does_not_open_on_a_blank_line(qapp: object) -> None:
    """A `QTextDocument` is born holding one empty block; the first tag goes IN it."""
    blocks = _document_blocks(_notes(UpdateDialog(RESULT)).document())

    assert blocks[0].text().strip() == "v0.8.70-Public"


def test_a_tag_outranks_every_heading_a_body_can_hold(qapp: object) -> None:
    """md4c gives `#` adjustment 3; a tag set to 1 rendered smaller than a body's `##`."""
    dialog = UpdateDialog(_with_body("# Body heading\n\n## Smaller\n\ntext"))

    blocks = _document_blocks(_notes(dialog).document())
    sizes = {
        b.text()
        .strip(): b.begin()
        .fragment()
        .charFormat()
        .property(QTextFormat.Property.FontSizeAdjustment)
        for b in blocks
        if b.text().strip() in {"v0.8.70-Public", "Body heading", "Smaller"}
    }

    assert sizes["v0.8.70-Public"] > sizes["Body heading"] > sizes["Smaller"]
    assert sizes["v0.8.70-Public"] >= TAG_SIZE_ADJUSTMENT


# ------------------------- what this file writes is never what a body left behind

BODY_ENDINGS = [
    "### Fixed\n- one.",
    "```\nan unclosed fence",
    "```sh\ncode\n```",
    "- a bullet",
    "- outer\n  - nested\n    - deeper",
    "- [x] a task",
    "1. numbered",
    "> a quote",
    "> - a quote holding a list",
    "| a | b |\n| - | - |\n| 1 | 2 |",
    "    indented code",
    "# a heading",
    "---",
    "a paragraph",
    "",
]
"""Every shape a release body can END with, which is what `_say` inherits from."""


def _is_plain(block: object, what: str) -> None:
    """A block this file wrote: no list, no fence, no quote, no indent, no texture."""
    block_format = block.blockFormat()  # type: ignore[attr-defined]
    char = block.charFormat()  # type: ignore[attr-defined]
    assert block.textList() is None, f"{what} became a list item"  # type: ignore[attr-defined]
    assert not block_format.hasProperty(
        QTextFormat.Property.BlockCodeFence
    ), f"{what} became a code block"
    assert not block_format.hasProperty(
        QTextFormat.Property.BlockCodeLanguage
    ), f"{what} became a code block"
    assert (
        block_format.intProperty(QTextFormat.Property.BlockQuoteLevel) == 0
    ), f"{what} became a quotation"
    assert block_format.indent() == 0, f"{what} was indented by the block before it"
    assert block_format.background().style() == Qt.BrushStyle.NoBrush, f"{what} took a background"
    assert not char.fontFixedPitch(), f"{what} came out monospaced"


@pytest.mark.parametrize("cut", [False, True])
@pytest.mark.parametrize("ending", BODY_ENDINGS)
def test_what_this_file_writes_never_inherits_the_body_above_it(
    qapp: object, ending: str, cut: bool
) -> None:
    """The notes, the tags and the separator are this file's blocks, not the body's.

    A bare `insertBlock()` inherits the block before it, and the block before
    these is the last block of a release body. Measured on the endings above:
    a body ending inside a fence made "the rest of this release…" a code
    block, and one ending in a list made it another bullet (seventh cold
    review, 2026-09-21). All 216 tests were green while that was true.

    **Both with and without the cut note**, because that note resets the
    formats itself: with `cut=True` it sits between the body and the
    separator, so the separator inherits from a block this file already wrote
    and an inheriting separator looks correct. The first spelling of this test
    ran only the cut case and stayed green on exactly that mutation.
    """
    dialog = UpdateDialog(
        dataclasses.replace(
            RESULT,
            notes=(
                ReleaseNotes("v1.2.0-Public", ending, cut),
                ReleaseNotes("v1.1.0-Public", ending, cut),
            ),
            notes_cut=True,
        )
    )

    blocks = _document_blocks(_notes(dialog).document())
    for block in blocks:
        text = block.text().strip()
        if text == BODY_CUT_NOTE:
            _is_plain(block, f"{ending!r}: the cut note")
            assert block.blockFormat().headingLevel() == 0
        elif text == OLDER_CUT_NOTE:
            _is_plain(block, f"{ending!r}: the older-releases note")
            assert block.blockFormat().headingLevel() == 0
        elif text in {"v1.2.0-Public", "v1.1.0-Public"}:
            _is_plain(block, f"{ending!r}: the tag heading")
            assert block.blockFormat().headingLevel() == 2
        elif not text:
            _is_plain(block, f"{ending!r}: the separator")


# --------------------------------------------- a tag is remote text like any other


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2.0-Public\nv9.9.9-Public",
        "v1.2.0-Public\r\nsecond line",
        "v1.2.0-Public second",
        "v1.2.0-Public second",
        "v1.2.0-Public\x00hidden",
        "v1.2.0-Public‮cilbuP-0.0.9v",
        "v1.2.0-Public" + "\n" * 5000,
        "v" + "9" * 5000,
    ],
)
def test_a_tag_is_one_heading_however_it_is_spelled(qapp: object, tag: str) -> None:
    """A newline in a tag does not make a long heading, it makes SEVERAL headings.

    Which is the one thing the per-release design promises cannot happen, so
    the count is what this asserts.
    """
    notes = (
        ReleaseNotes(tag, "### Fixed\n- one.", False),
        ReleaseNotes("v1.1.0-Public", "b", False),
    )
    dialog = UpdateDialog(dataclasses.replace(RESULT, notes=notes))

    headings = [
        b
        for b in _document_blocks(_notes(dialog).document())
        if b.blockFormat().headingLevel() == 2
    ]

    assert len(headings) == len(notes), f"{tag!r} made {len(headings)} headings"
    assert all(len(b.text()) <= TAG_CHARS for b in headings)


# ---------------------------------------------------------- the progress dialog


def test_the_progress_dialog_starts_busy_and_says_nothing_it_does_not_know(
    qapp: object,
) -> None:
    """A bar at 0% before the first byte reads as a hang; Qt's busy form does not."""
    dialog = UpdateProgressDialog("v0.8.70-Public")
    try:
        assert "v0.8.70-Public" in dialog.windowTitle()
        assert (dialog.bar.minimum(), dialog.bar.maximum()) == (0, 0)
        assert dialog.detail_label.text() == ""
        assert not dialog.close_button.isVisibleTo(dialog)
    finally:
        dialog.deleteLater()


def test_progress_fills_the_bar_and_a_total_of_zero_leaves_it_busy(qapp: object) -> None:
    dialog = UpdateProgressDialog("v1")
    try:
        dialog.set_progress(25, 100)
        assert (dialog.bar.minimum(), dialog.bar.maximum(), dialog.bar.value()) == (0, 100, 25)
        dialog.set_progress(7, 0)
        assert (dialog.bar.minimum(), dialog.bar.maximum()) == (0, 0)
    finally:
        dialog.deleteLater()


def test_progress_past_the_total_does_not_overfill_the_bar(qapp: object) -> None:
    """The total is the size the RELEASE declares; the bytes are what arrived."""
    dialog = UpdateProgressDialog("v1")
    try:
        dialog.set_progress(150, 100)
        assert dialog.bar.value() == 100
    finally:
        dialog.deleteLater()


def test_the_worker_talks_to_the_dialog_only_through_the_relays_signals(qapp: object) -> None:
    """`job.py`'s `LineRelay` rule: a plain callable would be delivered on the worker.

    Driven through `emit_progress` / `emit_stage` — the two methods the worker
    is handed — rather than through the slots, so what is pinned is the whole
    wire and not the slot at the end of it.
    """
    dialog = UpdateProgressDialog("v1")
    try:
        dialog.relay.emit_stage("Unpacking…")
        dialog.relay.emit_progress(5_000_000, 10_000_000)
        process_events()
        assert dialog.stage_label.text() == "Unpacking…"
        assert dialog.bar.value() == 5_000_000
        assert dialog.detail_label.text() == "5.0 MB of 10.0 MB"
    finally:
        dialog.deleteLater()


def test_the_relay_is_a_qobject_whose_news_travels_as_signals(qapp: object) -> None:
    """The rule is about the MECHANISM, so the mechanism is what is asserted.

    A relay whose `emit_*` were plain calls into the widget would pass every
    other test in this section and be the bug `job.py` records: PySide6 6.11.2
    delivers a plain callable on the WORKER thread, with an explicit
    QueuedConnection. A `Signal` on a class becomes a `SignalInstance` when it
    is read off an instance, which is the thing to look for.
    """
    dialog = UpdateProgressDialog("v1")
    try:
        assert isinstance(dialog.relay, QObject)
        assert isinstance(dialog.relay.progressed, SignalInstance)
        assert isinstance(dialog.relay.staged, SignalInstance)
    finally:
        dialog.deleteLater()


def test_cancel_sets_the_event_the_worker_reads_and_disables_itself(qapp: object) -> None:
    dialog = UpdateProgressDialog("v1")
    try:
        assert not dialog.cancel_event.is_set(), "the precondition: nothing has been cancelled"
        dialog.cancel_button.click()
        process_events()
        assert dialog.cancel_event.is_set()
        assert not dialog.cancel_button.isEnabled()
        assert dialog.cancel_button.text() == CANCELLING
    finally:
        dialog.deleteLater()


def test_a_refusal_replaces_the_bar_with_the_reason_and_one_way_out(qapp: object) -> None:
    dialog = UpdateProgressDialog("v1")
    try:
        dialog.finish_error("The download's checksum does not match. Nothing was installed.")
        process_events()
        assert "checksum" in dialog.stage_label.text()
        assert not dialog.bar.isVisibleTo(dialog)
        assert not dialog.cancel_button.isVisibleTo(dialog)
        assert dialog.close_button.isVisibleTo(dialog)
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize(
    ("done", "total", "said"),
    [(0, 0, "0.0 MB"), (1_500_000, 3_000_000, "1.5 MB of 3.0 MB"), (500, 0, "0.0 MB")],
)
def test_the_byte_counts_are_said_in_megabytes(done: int, total: int, said: str) -> None:
    """A player watching a 78 MB download wants megabytes, not 81788928."""
    assert as_megabytes(done, total) == said
