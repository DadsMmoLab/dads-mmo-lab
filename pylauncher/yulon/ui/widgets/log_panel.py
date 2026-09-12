"""Streaming log output widget (roadmap 4.1).

`LogPanel` shows the lines of a long-running job — an install, a rebuild,
`docker logs -f` — live, without blocking the UI thread. The job is any
`Iterator[str]` factory (e.g. `lambda: installer.run(options)` or
`lambda: runner.stream([...])`); it runs on a `QThread` inside a
`_StreamWorker` that emits `line(str)` and `finished(bool, str)` signals, and
the panel only connects to those. Call down / signal up (style-guide §5):
whoever owns the panel calls `run()`, the panel signals `run_finished` and
never reaches into the runner or into its parent.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPalette, QResizeEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yulon import runner
from yulon.log import get_logger
from yulon.ui import lines
from yulon.ui.widgets.job import in_flight

logger = get_logger(__name__)

LineSource = Callable[[], Iterator[str]]

_MAX_BLOCKS = 5000

_STICK_SLACK_PX = 4
"""How far off the bottom still counts as "at the bottom".

Qt's scrollbar maximum moves as blocks are added and trimmed, and a value one
or two pixels short of it is what a scrollbar sitting at the end reports after
a resize. Requiring exact equality would read a panel that IS following as one
the user had scrolled away from, and following would stop on its own.
"""


@dataclass(frozen=True)
class Tone:
    """How one kind of line is painted, said RELATIVE TO THE THEME rather than in ink.

    The panel has no theme of its own — it is a `QPlainTextEdit` in whatever
    palette the desktop handed the app — so a tone cannot be a hex value. Two
    ways of being relative, and each kind uses one of them:

    * `fade` is a distance from the window's own text colour towards its
      background, which is what "muted" and "dimmed" mean in both a light and a
      dark theme with no second spelling.
    * `on_light`/`on_dark` are the two hues amber and red need, picked by which
      side of the middle `QPalette.Text` falls on. Amber that reads on white is
      too dark to read on charcoal and the other way round, so the one colour a
      hue cannot be is a single one.

    `band` tints the line's background by that much of the same hue. `weight` is
    a `QFont` weight, `0` meaning "leave the panel's own".
    """

    fade: float = 0.0
    on_light: str = ""
    on_dark: str = ""
    band: float = 0.0
    weight: int = 0
    bold: bool = False


PALETTE: dict[str, Tone] = {
    "stage": Tone(bold=True),
    "marker": Tone(fade=0.45),
    "sentence": Tone(),
    "tool": Tone(fade=0.62),
    "warning": Tone(on_light="#8a5300", on_dark="#f2c14e", band=0.15),
    "failure": Tone(on_light="#a11212", on_dark="#ff8f8f", band=0.17, weight=500),
}
"""Every appearance the panel has, in one dict, keyed by `lines.parse()`'s kind.

One place rather than six branches, because the failed run's PROGRESS BAR is
painted the same red as the failed LINE above it (`_bar_style()`), and a second
spelling of that colour is a header and a log that disagree about what a
refusal looks like.
"""


def _blend(one: QColor, two: QColor, part: float) -> QColor:
    """`one` with `part` of `two` mixed into it."""
    keep = 1.0 - part
    return QColor(
        round(one.red() * keep + two.red() * part),
        round(one.green() * keep + two.green() * part),
        round(one.blue() * keep + two.blue() * part),
    )


def tone_colour(tone: Tone, palette: QPalette) -> QColor | None:
    """The foreground `tone` asks for under `palette`, or None where it wants none.

    Public so a test can ask the same question the panel asks, in whatever
    theme the box running it happens to have, instead of naming a hex value that
    would be wrong in the other one.
    """
    text = palette.color(QPalette.ColorRole.Text)
    if tone.fade:
        return _blend(text, palette.color(QPalette.ColorRole.Base), tone.fade)
    hue = tone.on_dark if text.lightness() > 127 else tone.on_light
    return QColor(hue) if hue else None


def _line_format(kind: str, palette: QPalette) -> QTextCharFormat:
    """The character format for one kind of line, built fresh on every append.

    Every kind gets one, `sentence` included, and its format is the default —
    which is how the panel says "no colour" without a second code path.
    """
    fmt = QTextCharFormat()
    tone = PALETTE.get(kind, PALETTE["sentence"])  # an unknown kind is an ordinary sentence
    colour = tone_colour(tone, palette)
    if colour is not None:
        fmt.setForeground(colour)
    if tone.band:
        hue = QColor(tone.on_dark if _is_dark(palette) else tone.on_light)
        fmt.setBackground(_blend(palette.color(QPalette.ColorRole.Base), hue, tone.band))
    if tone.bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    elif tone.weight:
        fmt.setFontWeight(tone.weight)
    return fmt


def _is_dark(palette: QPalette) -> bool:
    """True where the window's text is lighter than its background."""
    return palette.color(QPalette.ColorRole.Text).lightness() > 127


def _bar_style(palette: QPalette) -> str:
    """The failed run's progress bar, in `PALETTE["failure"]`'s own red.

    A stylesheet rather than a palette role: `QProgressBar`'s chunk takes its
    colour from the style, not from `QPalette.Highlight`, on every platform
    style this app has been run under.
    """
    red = tone_colour(PALETTE["failure"], palette)
    if red is None:
        return ""
    return f"QProgressBar::chunk {{ background-color: {red.name()}; }}"


_BAR_WIDTH_PX = 120
_STEP_WIDTH_PX = 200
_PROGRESS_WIDTH_PX = 230
"""How much of the header row the strip may ever ask for.

Every one of the three is bounded, and for the measured reason the status label
is wrapped: whatever the header row demands becomes this panel's minimum width,
`main.py`'s splitter has to honour it, and the pane beside it is starved (T32,
and the 2026-09-02 measurement on `setWordWrap` above). Git's own progress text
is what makes this real -- `Receiving objects:  42% (420/1000), 12.53 MiB |
3.21 MiB/s` -- and it arrives several times a second.

Measured on this box while the T35 tests were written, offscreen platform,
default font: unbounded labels took the panel's minimum width from 166px to
406px on one long progress line, which is the bug T32 closed reopening under a
new widget.
"""


class _StripLabel(QLabel):
    """One field of the stage strip: it shows what fits and DEMANDS NOTHING.

    `Ignored` horizontally is the whole trick, and it is what keeps T32 closed.
    A `QLabel`'s minimum size hint is as wide as its text, `setMaximumWidth()`
    does not bring that hint down, and the header row's minimum width IS this
    panel's minimum width — so a strip built from ordinary labels took the
    panel's minimum from 154px to 683px even with both fields capped (measured
    offscreen on m910q while T35's tests were written; 255px as it stands).
    `Ignored` says "give me what is left over and never widen anything for me",
    which is exactly a strip's claim on a header.

    Text is elided rather than wrapped, because a wrapping label in a one-line
    header grows the row to three or four lines on a long git progress line, and
    that row holds the Stop button. What it was told is kept whole in `said()`
    and in the tooltip, so nothing a reader might want is lost to the elision.

    Its own class rather than a helper called from the panel, because the width
    to elide against only exists once the LAYOUT has run: a helper called at
    append time elided against the label's previous width and left the strip
    blank until the next resize of the panel. A widget is told its own size, so
    this is the one place that can know.
    """

    def __init__(self, parent: QWidget, cap: int) -> None:
        super().__init__("", parent)
        self.setMaximumWidth(cap)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._said = ""

    def said(self) -> str:
        """The whole of what this field was told, elision and all."""
        return self._said

    def say(self, text: str) -> None:
        """Put `text` in this field, showing as much of it as there is room for."""
        self._said = text
        self.setToolTip(text)
        self._show_what_fits()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._show_what_fits()

    def _show_what_fits(self) -> None:
        fits = self.fontMetrics().elidedText(self._said, Qt.TextElideMode.ElideRight, self.width())
        # Guarded, because this runs from `resizeEvent` and `setText()` on a
        # label whose text has not changed still asks the layout to look again.
        if fits != self.text():
            self.setText(fits)


def _clock(now: float) -> str:
    """Wall-clock `HH:MM:SS` for a line, local time.

    Seconds and no date: the panel is read while something is happening, and
    what a reader wants from it is "how long has this step been going", which
    they get by subtracting two of these. A date on every line would push the
    line's own text past the width of the pane.
    """
    return time.strftime("%H:%M:%S", time.localtime(now))


def _elapsed(seconds: float) -> str:
    """`H:MM:SS` since the run started, hours never dropped.

    An install has stages measured in seconds (`conf`) and stages measured in
    hours (`build`, `mmaps`), so the same field has to carry both without
    changing shape. `0:00:04` and `4:31:07` line up in a monospace panel, which
    a `MM:SS` that grew an hours field partway through a run would not.
    """
    whole = max(0, int(seconds))
    return f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


class _StreamWorker(QObject):
    """Runs a `LineSource` to exhaustion on its thread, emitting each line."""

    line = Signal(str)
    finished = Signal(bool, str)  # ok, message

    def __init__(self, source: LineSource) -> None:
        super().__init__()
        self._source = source
        self._stop = False
        self._ident: int | None = None

    @Slot()
    def run(self) -> None:
        ok = True
        message = "done"
        # Published BEFORE the source is touched, because that is the only
        # moment at which this thread's ident is knowable to `request_stop()`
        # while the source can still be reached: everything after this line may
        # block for hours. See `request_stop()` for what it is for.
        self._ident = threading.get_ident()
        try:
            # The flag read that costs nothing and closes the narrowest window
            # there is: `thread.start()` returns before the OS schedules this
            # slot, so a Stop pressed in between used to arrive at a worker that
            # had not begun, do nothing, and be followed by a source that then
            # blocked with nobody left to ask.
            if self._stop:
                self.finished.emit(True, "stopped")
                self._quit_own_thread()
                return
            for text in self._source():
                if self._stop:
                    break
                self.line.emit(text)
        except Exception as exc:  # boundary: anything the job raises becomes a UI message
            ok = False
            message = f"{type(exc).__name__}: {exc}"
            if self._stop:
                # A SOURCE THAT RAISES AFTER A STOP IS THE STOP TAKING EFFECT.
                # `request_stop()` ends the job's children, and a terminated
                # child exits non-zero, so `runner.stream()` raises
                # `CalledProcessError` on the way out — exit 143 on the live box
                # (the last line of the 7.10 probe's own log). Reporting that as
                # `ok=False` would put a refusal on screen for a button the user
                # pressed, which is the twin of the "finished: stopped" bug
                # `_on_finished` exists to fix. The text is kept in the log, at
                # debug, so a genuine failure that happened to land in the same
                # millisecond is not lost.
                logger.debug(f"log panel job ended after a stop was asked for: {message}")
                ok, message = True, "stopped"
            else:
                logger.warning(f"log panel job failed: {message}")
        if self._stop:
            # Said HERE and not only in the loop above, so all three ways out
            # agree. The break reports a stop; a source that returned on its own
            # cancel (`runner.interact()`) reached the end of its iterator and
            # would otherwise have reported "done"; and a source killed with it
            # came through the `except`.
            message = "stopped"
        self.finished.emit(ok, message)
        self._quit_own_thread()

    def _quit_own_thread(self) -> None:
        """End our own thread's event loop from inside it, after `finished` was emitted.

        `finished` is also connected to `thread.quit`, but the QThread OBJECT
        lives in the main thread, so that connection is queued — and the one
        caller that most needs the join is `main._stop_background_threads()`,
        which runs after `app.exec()` has returned and then blocks in
        `wait()`. Nothing pumps the main thread's queue there, so `quit()` was
        never delivered, `wait(5000)` timed out (measured: `wait(3000)` ->
        False with the worker long finished) and Qt was torn down with the
        QThread still running — the 0xC0000409 abort that function's own
        docstring says it prevents. Called here it is direct, and the queued
        copy stays as it was (review, 2026-08-23).

        Its own method because `run()` now has two exits — the ordinary one and
        the "stopped before it started" one — and an exit that emitted
        `finished` without this call would leave the thread running for exactly
        that reason.

        **OUR thread, never the GUI one.** `run()` is called directly, on the
        calling thread, by anything that drives a worker without moving it —
        which is exactly how the "stopped before it started" ordering is proved,
        because `QThread::started` is delivered on the new thread and the GUI
        thread cannot hold it back. Without the guard below that call quits the
        MAIN event loop, and nothing looks wrong until the next nested loop:
        `QInputDialog.getText()` then returns `ok=False` having shown no dialog
        at all, so a question the installer is blocked on reads as "the user
        dismissed it". Measured 2026-09-09 — one unit test in this file left the
        main loop quit and `tests/test_prompt.py`'s real-dialog test read `None`
        where a `y` had been typed; green apart, red together, on the GitHub
        runner and again outside pytest on yulon-fedora.
        """
        thread = self.thread()
        app = QCoreApplication.instance()
        if thread is not None and (app is None or thread is not app.thread()):
            thread.quit()

    def request_stop(self) -> None:
        """Ask this job to stop, and END WHAT IT STARTED so a blocked read can return.

        The flag alone cannot do it. `run()` reads it between lines, and a source
        blocked in `readline()` on a child that has gone quiet produces no next
        line — measured through the panel's real Stop button on `yulon-ubuntu2`
        2026-09-08: `worker._stop=True` for 120 seconds with the panel still
        running, against 0.02 s for a synthetic source that kept yielding
        (`pyplan/gates/7.10-rerun-ubuntu2-2026-09-08/log-panel-stop-probe.txt`).

        So the flag is set AND every `runner.stream()` child this thread started
        is ended. Keyed on the thread rather than on a cancel token because the
        panel does not build its source's subprocesses and cannot reach them:
        the Console tab's source is `docker.follow_logs()` behind a zero-argument
        lambda, and it takes no cancel to hand one. `runner.end_streams_started_on()`
        carries the rest of the reasoning, including what it deliberately cannot
        reach.

        Called on the GUI thread while this object's own thread is blocked; it
        touches only `_stop` (a bool the worker re-reads) and `_ident` (written
        once, before the source was entered), and it returns without waiting for
        any child to die.
        """
        self._stop = True
        if self._ident is not None:
            runner.end_streams_started_on(self._ident)


class LogPanel(QWidget):
    """A read-only, auto-scrolling text panel fed by a background job."""

    run_started = Signal()
    run_finished = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(_MAX_BLOCKS)
        self._status = QLabel("idle", self)
        # WRAPPED, and the app is unusable without it. This label is handed the
        # whole of a refusal -- `("finished: " if ok else "FAILED: ") + message`
        # below -- and an unwrapped QLabel's size hint is as wide as its text.
        # That hint becomes this panel's minimum width, and the splitter in
        # `main.py` has to honour it, so the catalog pane next to it is squeezed
        # to nothing.
        #
        # Measured 2026-09-02 on yulon-ubuntu, in a 986px window, with the real
        # home-folder refusal (196 characters): the catalog pane went from 684px
        # to 88px and this panel demanded 1478px -- wider than the window. Game
        # tiles were clipped mid-word and their Install buttons unreachable, so
        # the only way out was to resize or restart. Found by the owner during
        # the 7.2 gate, on the first refusal a real user would ever see.
        self._status.setWordWrap(True)
        # SELECTABLE, so a refusal can be copied instead of screenshotted (T32:
        # a macOS report arrived as a photograph of this label because a QLabel
        # selects nothing by default). Keyboard selection is asked for beside
        # mouse selection, not in place of it: `TextSelectableByKeyboard` is
        # what gives the label a text cursor for Ctrl+A/Ctrl+C at all.
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        # Elapsed lives HERE, beside Stop, not on every line (owner,
        # 2026-09-03). One field that ticks answers "how long has this been
        # going" better than the same number repeated down the panel, and it
        # keeps answering during the long silences: an hour of `build` emits
        # lines in bursts, so a per-line elapsed stops moving exactly when the
        # reader most wants to know the install has not died. This is also the
        # only place the number can go on updating with nothing to print.
        self._elapsed_label = QLabel("", self)
        self._elapsed_label.setToolTip("Time since this job started")
        self._stop_button = QPushButton("Stop", self)
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self.stop)
        # THE STAGE STRIP (T35), between the status and the elapsed clock. Three
        # widgets for three different facts: which stage of how many is running
        # (the engine's own `Step N of M` line, parsed), how far the thing
        # inside that stage has got, and what it is doing right now.
        #
        # Both labels wrap, for the measured reason `self._status` does: git's
        # progress text runs to `Receiving objects:  42% (420/1000), 12.53 MiB |
        # 3.21 MiB/s`, and an unwrapped label's size hint is as wide as its text.
        self._step_label = _StripLabel(self, _STEP_WIDTH_PX)
        self._bar = QProgressBar(self)
        self._bar.setMaximumWidth(_BAR_WIDTH_PX)
        self._bar.setTextVisible(True)
        self._bar.setVisible(False)
        self._progress_label = _StripLabel(self, _PROGRESS_WIDTH_PX)

        header = QHBoxLayout()
        # Stretch, not size hints. The strip's two fields demand no width of
        # their own (`_StripLabel`), so a share of the row is the only way they
        # get any — and a share is what should shrink first when the splitter
        # narrows, since the status label carries the refusals.
        header.addWidget(self._status, 3)
        header.addWidget(self._step_label, 2)
        header.addWidget(self._bar)
        header.addWidget(self._progress_label, 2)
        header.addWidget(self._elapsed_label)
        header.addWidget(self._stop_button)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self._text, 1)

        self._cancel: threading.Event | None = None
        self._started_at: float | None = None
        # One second, because the field it drives has a seconds place; a faster
        # tick repaints a label that cannot have changed.
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._show_elapsed)
        self._thread: QThread | None = None
        self._worker: _StreamWorker | None = None
        self._stop_requested = False

    # -- public ---------------------------------------------------------

    @property
    def running(self) -> bool:
        """True while a job is streaming into the panel."""
        return self._thread is not None and self._thread.isRunning()

    @property
    def cancelled(self) -> bool:
        """True if `stop()` was asked for the job now running, or the last one.

        Nothing downstream can work this out for itself. A cancelled source
        does not raise — `runner.interact()` returns when its cancel event is
        set — so the worker reports `ok=True` and a message ("done"/"stopped")
        that a completed job could also produce. The panel is the only thing
        that knows the Stop button was pressed, so it is the thing that says
        so. Reset by `run()` and only ever set for a job that was running, so it
        always describes the current job — see `stop()`.
        """
        return self._stop_requested

    def text(self) -> str:
        """Everything currently shown."""
        return self._text.toPlainText()

    def status_text(self) -> str:
        """What the header says about the job (tests / accessibility)."""
        return self._status.text()

    def clear(self) -> None:
        """Empty the panel."""
        self._text.clear()

    def append(self, line: str) -> None:
        """Append one line, without its terminal colour codes.

        Stripped HERE rather than in each source, because every source has the
        problem and none of them had the fix. `runner.interact()` yields the
        install script's lines raw and says so, and `docker.follow_logs()`
        streams the worldserver's own colour — confirmed in the real stream:
        `\\x1b[36m` on every `[mod-city-bots]` line, plus bracketed-paste
        `\\x1b[?2004h` around the console prompt. A QPlainTextEdit renders none
        of that, so the Console tab showed the escape sequences themselves on
        every coloured line, and the install panel would have too. The parser in
        `console.py` strips separately and must keep doing so — it reads the
        prompt out of the raw stream, long before anything is displayed
        (review, 2026-08-23).

        The stray-ESC removal is the second half: `strip_ansi()` only matches
        CSI sequences, so an `ESC(B`-style charset switch leaves the ESC byte
        behind, and that renders as a box glyph.

        **Each line is stamped, and the panel keeps following the bottom.**
        Both were asked for by the owner on 2026-09-03 while watching a real
        install: an hour of build output with no clock on it answers neither
        "when did this step start" nor "how long has it been going", and a
        panel that stops following makes the newest line the one you cannot
        see. The stamp is `HH:MM:SS +H:MM:SS` -- wall clock, then time since
        this run began -- so the cost of a stage is a subtraction the reader can
        do in their head, and a stalled install is visible as a clock that has
        stopped moving.

        **Following the bottom is conditional, and that is the whole design.**
        A panel that always jumps to the end cannot be read while it is
        running: scrolling up to look at the error that just went past yanks
        the reader back on the next line. So the position is measured BEFORE
        the append and restored only if it was already at the end -- scroll up
        and the panel holds still; scroll back down and it resumes on its own,
        with no button and nothing to remember.

        The stamp is applied here, at the one place every source arrives,
        rather than at the yield sites: the install engine's lines are also the
        gate transcripts and the fixtures dozens of tests compare literally, and
        a timestamp in those would make every one of them a clock-dependent
        test. `text()` returns what is displayed, stamps and all.

        **Every line is classified, and one kind of line is not a line at all.**
        `lines.parse()` answers what arrived, and T35's three answers about
        appearance follow from it: the engine's own stage lines are bold, a
        relayed subprocess's are dimmed, and a warning or a failure is painted.
        A `progress` line is NOT appended — it is a reading that replaces the
        last one, so it goes to the header strip and nothing else. The T30
        install on yulon-arch wrote 97 minutes of `[Map 230] Building tile
        [32,32] (08 / 12)` into this panel at the weight of a sentence; that
        torrent is now one moving bar.

        `text()` therefore returns the DISPLAY text, prefixes off, which is why
        every test in this file that reads it passed unchanged through T35.

        Thread-safe only from the UI thread; the worker reaches it by signal.
        """
        clean = runner.strip_ansi(line).replace("\x1b", "")
        parsed = lines.parse(clean)
        if parsed.kind == "progress":
            self._show_progress(parsed)
            return
        if parsed.kind == "stage":
            self._show_step(parsed.text)
        scrollbar = self._text.verticalScrollBar()
        following = scrollbar.value() >= scrollbar.maximum() - _STICK_SLACK_PX
        self._write(f"[{_clock(time.time())}] {parsed.text}", parsed.kind)
        if following:
            scrollbar.setValue(scrollbar.maximum())

    def _write(self, stamped: str, kind: str) -> None:
        """Put one finished line in the panel, painted for its kind.

        **Appended first, then formatted — never the other way round.** Two
        reasons, and the second was measured. `appendPlainText()` is the one
        call whose handling of `maximumBlockCount`, the undo stack and the
        scrollbar is Qt's own, so the text goes in through it. And a format set
        on the EDIT'S insertion cursor (`setCurrentCharFormat()`) is carried
        forward into everything appended after it: measured offscreen on m910q,
        a red failure line followed by an ordinary sentence left the sentence
        red too. Formatting the text that has already landed cannot do that —
        the same measurement, the same day, showed the next line's fragment
        back at `NoBrush`.
        """
        self._text.appendPlainText(stamped)
        block = self._text.document().lastBlock()
        cursor = QTextCursor(block)
        cursor.setPosition(block.position())
        cursor.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(_line_format(kind, self._text.palette()))

    def _show_step(self, line: str) -> None:
        """Put this stage line's own three fields on the strip.

        A line that does not parse leaves the label alone rather than blanking
        it: the format is the engine's and `lines.parse()` has already said this
        is one of its stage lines, so a `None` here means the format moved — and
        the last stage that DID parse is still the truer answer to "where is
        this run" than nothing at all.
        """
        step = lines.parse_step(line)
        if step is None:
            return
        self._step_label.say(f"Step {step.number} of {step.total} · {step.name}")

    def _show_progress(self, parsed: lines.Parsed) -> None:
        """Move the bar to this reading, and say what is being done beside it.

        A missing percent is Qt's busy bar (`setRange(0, 0)`) and never a zero:
        `Enumerating objects` and `Resolving deltas` report no number, and a bar
        sitting at 0% for them says nothing has happened yet, which is the
        opposite of true.
        """
        if parsed.percent is None:
            self._bar.setRange(0, 0)
        else:
            self._bar.setRange(0, 100)
            self._bar.setValue(max(0, min(100, parsed.percent)))
        self._bar.setVisible(True)
        self._progress_label.say(parsed.text)

    def _clear_strip(self) -> None:
        """Take the last run's strip down. Called by `run()`, for the elapsed field's reason."""
        self._step_label.say("")
        self._progress_label.say("")
        self._bar.setStyleSheet("")
        self._bar.setRange(0, 100)
        self._bar.reset()
        self._bar.setVisible(False)

    def step_text(self) -> str:
        """Which stage the strip was told is going (tests / accessibility).

        What the strip was TOLD, not what its label shows: the label elides to
        fit the header, and the elision is a property of the font on the box.
        The same string is the label's tooltip, so it is reachable on screen.
        """
        return self._step_label.said()

    def progress_text(self) -> str:
        """What the strip was told the current stage is doing (tests / accessibility)."""
        return self._progress_label.said()

    def run(
        self,
        source: LineSource,
        *,
        title: str = "running",
        cancel: threading.Event | None = None,
    ) -> bool:
        """Start streaming `source()` into the panel. Returns False if a job is already running.

        `cancel`, when given, is set by `stop()` so a source that supports it
        (e.g. an engine's `run(cancel=...)`) can be interrupted even while blocked
        between lines (review finding, 2026-08-21).
        """
        if self.running:
            logger.debug("log panel busy; run() ignored")
            return False
        self._dispose_last_job()
        self._cancel = cancel
        self._stop_requested = False
        # The zero the elapsed clock counts from. Set on the RUN, not on the
        # panel or the first line: the same panel is reused for the next
        # install and for the console, and an elapsed field that kept counting
        # from whenever the window opened would say four hours into a job that
        # started a minute ago.
        self._started_at = time.time()
        self._show_elapsed()
        self._clear_strip()
        self._ticker.start()
        self._status.setText(title)
        self._stop_button.setEnabled(True)
        # No parent, and held by `in_flight()` until finished: a panel dropped
        # between `start()` and the OS scheduling the thread must not take the
        # worker down with it - see `job.InFlight`.
        thread = QThread()
        worker = _StreamWorker(source)
        worker.moveToThread(thread)
        in_flight().hold(thread, worker)
        thread.started.connect(worker.run)
        worker.line.connect(self.append)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        # Deliberately NOT `thread.finished.connect(worker.deleteLater)`, the
        # textbook pattern - see `_dispose_last_job()` for why it is the segfault.
        self._thread, self._worker = thread, worker
        thread.start()
        self.run_started.emit()
        return True

    def _dispose_last_job(self) -> None:
        """Delete the previous job's worker HERE, on the GUI thread, once its thread is gone.

        This used to be `thread.finished.connect(worker.deleteLater)` - the
        textbook Qt pattern, and it is the segfault. `deleteLater` posts the
        delete to the object's OWN thread, and Qt runs it inside that thread's
        final cleanup (`QThreadPrivate::finish`) - on the worker thread.
        `_StreamWorker` is a Python subclass, so tearing it down needs the GIL.
        If the GUI thread holds the GIL at that instant and is inside Qt's
        connection mutex - delivering a signal into a Python slot, which is
        most of what a GUI thread does - each side holds what the other needs.

        gdb on yulon-ubuntu, 2026-08-28: the main thread in
        `cleanOrphanedConnectionsImpl` -> `QBasicMutex::lockInternal`, reached
        from a slider `rangeChanged` into a Python slot; a thread named
        "QThread" in `~QObject()` -> `Shiboken::GilState::acquire`. That is the
        deadlock. The same race that does not deadlock corrupts memory instead:
        9 of 20 GUI-only runs died with SIGSEGV or SIGBUS, always at the
        boundary where one test module's last worker finished while the next
        module's fixture was building a window. Removing `processEvents()` from
        that fixture's teardown changed nothing, which is how the suspect moved
        from the fixture to here.

        And the panel already held `self._worker`, so the deferred delete was
        destroying the C++ side of an object Python still pointed at.

        Deleting from the GUI thread is safe once the worker's thread has
        finished: an object whose thread is gone has no affinity, and Qt permits
        its destruction from any thread. `running` is false to reach here, and
        `wait()` turns "not running" into "fully exited" rather than "about to".
        The QThread object itself lives on the GUI thread - it was created here -
        so ITS deferred delete is a GUI-thread event and is fine.
        """
        thread, worker = self._thread, self._worker
        self._thread = self._worker = None
        if thread is None:
            return
        thread.wait(5000)
        thread.deleteLater()
        del worker  # the last Python reference; the C++ object goes with it, on this thread

    @Slot()
    def stop(self) -> None:
        """Ask the running job to stop after its current line (and cancel a blocked one).

        A no-op when nothing is running, which is not tidiness: `cancelled`
        promises to describe the job, and `main._stop_background_threads()`
        calls `stop()` on EVERY registered panel at exit with no running check.
        Without the guard a panel that had finished its job cleanly ended the
        session reporting `cancelled is True` beside a header reading "finished:
        done" (review, 2026-08-23).

        **Three things, because two of them are not enough on their own.** The
        panel's `cancel` event stops a source that polls one (`installer.run()`,
        the rebuild engine) and is None for the source that does not — the
        Console tab's log follow. The worker's flag stops a source between
        lines, and a quiet `docker logs -f` has no next line. What closes the
        gap is `_StreamWorker.request_stop()`, which also ends the stream
        children that job started; the measurement that made it necessary is
        recorded there. None of the three waits: this method returns at once
        and the panel finds out through `run_finished`.
        """
        if not self.running:
            return
        self._stop_requested = True
        if self._cancel is not None:
            self._cancel.set()
        if self._worker is not None:
            self._worker.request_stop()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until the job's thread exits (tests / shutdown). True if it did."""
        if self._thread is None:
            return True
        return bool(self._thread.wait(timeout_ms))

    # -- slots ----------------------------------------------------------

    @Slot(bool, str)
    def _on_finished(self, ok: bool, message: str) -> None:
        # Cancellation is asked about FIRST, because a stopped job arrives here
        # with ok=True and message "done" — indistinguishable, from here, from
        # one that ran to the end. Measured on a real install driven through
        # the Catalog's own button: Stop pressed during the source clone left
        # the panel reading "finished: stopped" (install gate, 2026-08-23).
        # "finished" is a claim about the work, and a stopped job did not
        # finish it. What was left behind is the caller's story to tell — the
        # panel does not know whether it was following a log or building a
        # server.
        if self._stop_requested:
            self._status.setText("cancelled")
        else:
            self._status.setText(("finished: " if ok else "FAILED: ") + message)
        # Stopped, then shown ONE more time. The ticker is what makes the field
        # live, and a job that has ended must not go on counting; but the last
        # value is the run's total, which is the number somebody wants after a
        # four-hour install, so it is written once more and left standing until
        # the next `run()` resets it.
        self._ticker.stop()
        self._show_elapsed()
        # The strip is LEFT STANDING, and the bar goes red on a refusal. Which
        # of the nine stages an install died in is the first thing anybody asks
        # of a failed run, and it is already on screen — clearing it would
        # throw away the one field that answers before the log is scrolled.
        if not ok:
            self._bar.setStyleSheet(_bar_style(self._text.palette()))
        self._stop_button.setEnabled(False)
        self.run_finished.emit(ok, message)

    def _show_elapsed(self) -> None:
        """Put the run's elapsed time in the header field."""
        if self._started_at is None:
            self._elapsed_label.setText("")
            return
        self._elapsed_label.setText(_elapsed(time.time() - self._started_at))

    def elapsed_text(self) -> str:
        """What the header's elapsed field says (tests / accessibility)."""
        return self._elapsed_label.text()
