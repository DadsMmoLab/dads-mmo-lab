"""Tests for asking the user to answer a subprocess prompt (roadmap 6.1.5).

The bug these guard against is the one that made installing on Linux
impossible: the scripts run `sudo`, `sudo` wants a password, no rule can answer
it, and the install stopped there with the window looking frozen.

Two things had to be true before that could work, and the first version had
neither:

* The prompt has to REACH us. `sudo` reads from /dev/tty, not stdin, precisely
  so a piped stdin cannot feed it a password — so the child needs a real
  terminal, not three pipes.
* We have to know it IS the prompt. The first version guessed from the shape of
  the line, and the guess fires on ordinary build output; `test_build_output_*`
  below is the measured list. `SUDO_PROMPT` lets the caller choose the wording
  instead, so the test is an exact match on a random marker.

Every test here carries a deadline. An `interact()` regression should fail,
not hang the suite (review, 2026-08-22).
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import (
    HANG_BOUND,
    HANG_BOUND_MS,
    process_events,
    pump_until,
    spelled_bounds,
    wait_for_panel,
)
from yulon import platform, runner
from yulon.catalog import installer
from yulon.ui.widgets.log_panel import LogPanel
from yulon.ui.widgets.prompt import InputPrompter, is_secret, tidy

MARKER = "[yulon-sudo-deadbeef] password:"


def _expiring_cancel(after: float = 5.0) -> threading.Event:
    """A cancel that trips on its own, so a broken seam fails instead of hanging."""
    event = threading.Event()
    timer = threading.Timer(after, event.set)
    timer.daemon = True
    timer.start()
    return event


def test_a_password_prompt_is_recognised_as_secret() -> None:
    """Anything that would be shoulder-surfed must be masked, not echoed."""
    assert is_secret("[sudo] password for pk:") is True
    assert is_secret("Enter passphrase for key:") is True
    assert is_secret("Please enter your PIN:") is True
    assert is_secret("Install path:") is False
    assert is_secret("Continue anyway? (y/n)") is False


def test_the_docker_group_question_is_not_a_password_box() -> None:
    """The consent answer is a yes/no, and must be typed in the clear.

    It hangs on the literal `(y/n)` surviving copy edits, so it is pinned here
    rather than left to a reviewer noticing: reword that last line and the
    question silently becomes a masked field, where a user typing `y` sees a
    dot and reasonably concludes the app wants their password.
    """
    assert is_secret(platform.DOCKER_GROUP_QUESTION.format(user="pk")) is False


def test_a_prompt_is_shown_whole() -> None:
    """The tail of a prompt is the part that says what is wanted; never truncate it."""
    raw = "  [sudo] password for pk:   "
    assert tidy(raw) == "[sudo] password for pk:"


# -- the core seam ----------------------------------------------------------


def _echo_prompt_script(prompt: str) -> list[str]:
    """A child that prints `prompt` with NO newline and then reads a line."""
    code = (
        "import sys\n"
        f"sys.stdout.write({prompt!r})\n"
        "sys.stdout.flush()\n"
        "answer = sys.stdin.readline().strip()\n"
        "print('GOT:' + answer)\n"
    )
    return [sys.executable, "-c", code]


def test_interact_asks_the_user_for_the_exact_marker() -> None:
    """The sudo case, end to end against a real child process.

    A rule table cannot contain a password, so without this seam the child sits
    on the prompt forever and the app looks hung.
    """
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "hunter2"

    lines = list(
        runner.interact(
            _echo_prompt_script(MARKER + " "),
            respond=lambda _line: None,  # no rule matches anything
            ask=ask,
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    # `ask` sees the prompt exactly as the child printed it, trailing space and
    # all; tidying is the prompter's job, at the moment of display.
    assert asked == [MARKER + " "]
    assert any("GOT:hunter2" in line for line in lines)


def test_the_user_is_never_asked_without_a_marker() -> None:
    """No marker means the seam is inert — the safe default, and deliberately so.

    `ask` used to be consulted for any quiet partial line that ended in one of
    `: ? > ]`. That is a guess, it is wrong often (see the next test), and the
    consequence was a modal dialog over a two-hour build. A caller that has not
    arranged for the child to identify its prompt gets no dialog at all.
    """
    asked: list[str] = []
    lines = list(
        runner.interact(
            _echo_prompt_script(MARKER + " "),
            respond=lambda _line: None,
            ask=lambda prompt: asked.append(prompt) or "hunter2",  # type: ignore[func-returns-value]
            quiet_seconds=0.15,
            cancel=_expiring_cancel(1.5),
        )
    )
    assert asked == [], "asked about a prompt the caller never claimed to recognise"
    assert not any("GOT:" in line for line in lines)


@pytest.mark.parametrize(
    "chunk",
    [
        "[ 43%] Building CXX object src/CMakeFiles/foo.dir/bar.cpp.o",
        "Get:12 http://archive.ubuntu.com/ubuntu jammy/main amd64 libfoo amd64 1.2 [345 kB]",
        "Downloading data.zip [====>    ]",
        "/usr/include/c++/13/bits/stl_algo.h:1234:",
        "note:",
        "Selecting previously unselected package foo:",
        "#12 sha256:abc [2/5]",
    ],
)
def test_build_output_that_looks_like_a_prompt_is_never_asked_about(chunk: str) -> None:
    """Every one of these was measured returning True from the old heuristic.

    `interact()` reads with `os.read(fd, 4096)`, so a chunk boundary lands
    mid-line constantly; over a 2-4 hour compile, one of these sitting in the
    buffer for 0.3s is routine. The old code opened an application-modal dialog
    quoting it, which blocked the Stop button and wrote whatever was typed into
    the build's stdin (review, 2026-08-22).
    """
    asked: list[str] = []
    code = (
        "import sys, time\n"
        f"sys.stdout.write({chunk!r})\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.5)\n"  # the pause that used to arm the dialog
        "print()\n"
    )
    list(
        runner.interact(
            [sys.executable, "-c", code],
            respond=lambda _line: None,
            ask=lambda prompt: asked.append(prompt) or "WRONG",  # type: ignore[func-returns-value]
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    assert asked == [], f"opened a dialog over build output: {chunk!r}"


def test_interact_prefers_a_rule_over_asking_the_user() -> None:
    """Never interrupt someone for a question the app already knows the answer to."""
    asked: list[str] = []

    lines = list(
        runner.interact(
            _echo_prompt_script("Continue anyway? "),
            respond=lambda line: "n" if "Continue anyway" in line else None,
            ask=lambda prompt: asked.append(prompt) or "SHOULD-NOT-BE-USED",  # type: ignore[func-returns-value]
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    assert asked == []
    assert any("GOT:n" in line for line in lines)


def test_interact_without_a_prompter_still_gives_up_rather_than_hanging() -> None:
    """The old behaviour, kept: unanswerable and no prompter means cancel is the way out."""
    lines = list(
        runner.interact(
            _echo_prompt_script(MARKER + " "),
            respond=lambda _line: None,
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(0.6),
        )
    )
    assert not any("GOT:" in line for line in lines), "nothing should have been answered"


def test_a_declined_dialog_answers_nothing_rather_than_an_empty_line() -> None:
    """Dismissing the dialog must not send a bare newline.

    A blank line is a real answer to a shell prompt — it usually means "accept
    the default" — so treating "the user pressed Cancel" as "" would silently
    confirm something they declined.
    """
    lines = list(
        runner.interact(
            _echo_prompt_script(MARKER + " "),
            respond=lambda _line: None,
            ask=lambda _prompt: None,  # the user pressed Cancel
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(0.8),
        )
    )
    assert not any("GOT:" in line for line in lines)


def test_a_declined_prompt_is_still_shown_in_the_log() -> None:
    """Declining left the question invisible: the install just stopped producing output.

    A partial line that nothing answers is normally held back, because a build
    pausing mid-line is not a line yet. The prompt is the exception — it is the
    last thing the user will see before the install stalls, so it has to be on
    screen (review, 2026-08-22).
    """
    lines = list(
        runner.interact(
            _echo_prompt_script(MARKER + " "),
            respond=lambda _line: None,
            ask=lambda _prompt: None,
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(0.8),
        )
    )
    assert any(MARKER in line for line in lines), f"the prompt never reached the log: {lines!r}"


# -- the terminal transport -------------------------------------------------


def _has_bash() -> bool:
    from tests.support_bash import bash_available

    try:
        return bash_available()
    except (OSError, subprocess.SubprocessError):
        return False


needs_tty = pytest.mark.skipif(
    not runner.pty_supported(), reason="no pseudo-terminal on this platform (Windows)"
)


@needs_tty
def test_a_child_reading_dev_tty_is_answered_only_with_a_terminal() -> None:
    """This is the whole reason 6.1.5 did not work, reduced to two lines of shell.

    `sudo` does not read its password from stdin — it opens /dev/tty, so that a
    piped stdin cannot supply one. Measured on the Ubuntu VM: the same child
    reading *stdin* is answered through a pipe, and reading /dev/tty is not.
    Only `terminal=True` gives it a controlling terminal for /dev/tty to
    resolve to.
    """
    if not _has_bash():
        pytest.skip("no bash that can run a script on this machine")
    script = f'printf "{MARKER} "; read pw < /dev/tty; echo "GOT:$pw"'

    def run(terminal: bool) -> list[str]:
        return list(
            runner.interact(
                ["bash", "-c", script],
                respond=lambda _line: None,
                ask=lambda _prompt: "hunter2",
                ask_marker=MARKER,
                terminal=terminal,
                quiet_seconds=0.2,
                cancel=_expiring_cancel(6.0),
            )
        )

    assert any("GOT:hunter2" in line for line in run(terminal=True))
    with_pipes = run(terminal=False)
    assert not any(
        "GOT:hunter2" in line for line in with_pipes
    ), "a pipe answered a /dev/tty read — the premise of the pty transport is wrong"


@needs_tty
def test_a_real_shell_prompt_is_answered_by_the_prompter() -> None:
    """`read -p`-style prompts are the actual shape the install scripts use."""
    if not _has_bash():
        pytest.skip("no bash that can run a script on this machine")
    script = f'printf "{MARKER} "; read word; echo "GOT:$word"'
    lines = list(
        runner.interact(
            ["bash", "-c", script],
            respond=lambda _line: None,
            ask=lambda _prompt: "please",
            ask_marker=MARKER,
            terminal=True,
            quiet_seconds=0.2,
            cancel=_expiring_cancel(),
        )
    )
    assert any("GOT:please" in line for line in lines)


# -- the thread bridge ------------------------------------------------------


def test_prompter_carries_the_answer_from_the_gui_thread_to_the_worker(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ask()` is called on a worker and must block there until the dialog answers.

    The install runs off the GUI thread so the window does not freeze during a
    two-hour build, but a dialog may only be built on the GUI thread — so the
    answer has to cross back, and the subprocess has to be kept waiting while it
    does.
    """
    from yulon.ui.widgets import prompt as prompt_module

    seen: list[tuple[str, bool]] = []

    def fake_dialog(_parent, _title, label, echo, _text):  # type: ignore[no-untyped-def]
        seen.append((label, echo == prompt_module.QLineEdit.EchoMode.Password))
        return "hunter2", True

    monkeypatch.setattr(prompt_module.QInputDialog, "getText", staticmethod(fake_dialog))

    prompter = InputPrompter()
    answer: list[str | None] = []

    worker = threading.Thread(target=lambda: answer.append(prompter.ask("[sudo] password for pk:")))
    worker.start()
    pump_until(lambda: not worker.is_alive(), "ask() returned")
    worker.join(timeout=HANG_BOUND)

    assert answer == ["hunter2"]
    assert seen == [("[sudo] password for pk:", True)], "a password must be masked"


def test_prompter_stops_waiting_when_the_job_is_cancelled(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled install must not leave a worker parked on a question forever."""
    from yulon.ui.widgets import prompt as prompt_module

    monkeypatch.setattr(
        prompt_module.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("", False)),
    )
    cancel = threading.Event()
    prompter = InputPrompter()
    prompter.bind_cancel(cancel)
    answer: list[str | None] = []

    worker = threading.Thread(target=lambda: answer.append(prompter.ask("[sudo] password:")))
    worker.start()
    cancel.set()  # the user hit Cancel on the install itself
    worker.join(timeout=HANG_BOUND)

    assert not worker.is_alive(), "ask() never returned after cancel"
    assert answer == [None]


# -- hygiene ----------------------------------------------------------------


@pytest.mark.parametrize(
    "localised",
    [
        "[sudo] adgangskode for pk:",
        "[sudo] Passwort für pk:",
        "[sudo] Mot de passe de pk :",
        "[sudo] contraseña para pk:",
        "[sudo] wachtwoord voor pk:",
    ],
)
def test_a_sudo_prompt_in_any_language_is_masked(localised: str) -> None:
    """Masking is the default now, because the two failure directions differ.

    The old rule was an allowlist of English words, and a miss meant
    EchoMode.Normal — so every non-English box echoed the password onto the
    screen. Masking a folder path is an annoyance; echoing a password is not
    undoable (review, 2026-08-22).
    """
    assert is_secret(localised) is True


def test_the_prompter_does_not_keep_the_answer_after_handing_it_over(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module docstring promises the answer is never kept. It was.

    The prompter outlives the install — it is a child of the catalog view — so
    a retained `_answer` left a plaintext password on a live widget's child for
    the rest of the session.
    """
    from yulon.ui.widgets import prompt as prompt_module

    monkeypatch.setattr(
        prompt_module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("hunter2", True))
    )
    prompter = InputPrompter()
    answer: list[str | None] = []

    worker = threading.Thread(target=lambda: answer.append(prompter.ask("[sudo] password:")))
    worker.start()
    pump_until(lambda: not worker.is_alive(), "ask() returned")
    worker.join(timeout=HANG_BOUND)

    assert answer == ["hunter2"]
    assert prompter._answer is None, "the password is still on the prompter"


def test_no_dialog_opens_for_a_job_that_was_already_cancelled(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_show` is a QUEUED slot, so a cancel can land between emit and dequeue.

    Without this check the user gets a modal dialog belonging to an install that
    no longer exists, with nothing waiting for the answer (review, 2026-08-22).
    """
    from yulon.ui.widgets import prompt as prompt_module

    opened: list[str] = []
    monkeypatch.setattr(
        prompt_module.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: (opened.append(a[2] if len(a) > 2 else ""), ("", False))[1]),
    )
    cancel = threading.Event()
    cancel.set()  # already cancelled before anything is asked
    prompter = InputPrompter()
    prompter.bind_cancel(cancel)

    prompter.requested.emit("[sudo] password:", True)
    process_events(50)

    assert opened == [], "opened a dialog for a cancelled job"


def test_the_view_reuses_one_prompter_instead_of_leaving_one_per_install(
    qapp: object, tmp_path: Path
) -> None:
    """Each install used to build a new prompter parented to the view, and keep it."""
    from yulon.catalog.catalog import load_catalog
    from yulon.ui.catalog_view import CatalogView

    catalog = load_catalog()
    panel = LogPanel()
    view = CatalogView(
        catalog,
        lambda e: _NoopInstaller(e),
        panel,
        platform_id=lambda: "linux",
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
    )
    try:
        view.start_install(catalog.get("wow-wotlk"))
        first = view._prompter
        wait_for_panel(panel)
        view.start_install(catalog.get("wow-wotlk"))
        wait_for_panel(panel)
        assert view._prompter is first, "a second install built a second prompter"
        assert len(view.findChildren(InputPrompter)) == 1
    finally:
        # Both jobs joined before the panel goes out of scope. A LogPanel left
        # holding a live QThread does not fail this test — it aborts the whole
        # interpreter at exit with "QThread: Destroyed while thread is still
        # running", so every other test passes and the run still exits 134.
        # That is what it did on CI (2026-08-23).
        panel.stop()
        assert panel.wait(HANG_BOUND_MS), "the panel's job never joined after stop()"
        process_events(50)


class _NoopInstaller:
    """An engine whose run() yields nothing, so start_install() returns at once.

    A plain class since 7.2 rather than an `installer.Installer` subclass: the
    view only ever needed the `InstallEngine` protocol, and the class it used to
    inherit from is gone.
    """

    def __init__(self, entry: object) -> None:
        self.entry = entry

    def preflight(self, options: object, cancel: object = None, *, ask: object = None) -> None:
        return None

    def run(
        self, options: object = None, *, cancel: object = None, ask: object = None
    ) -> Iterator[str]:
        yield "done"


# -- round-2 review fixes ---------------------------------------------------


def test_ask_receives_only_the_prompt_not_the_output_stuck_in_front_of_it() -> None:
    """The dialog's label — and its masking decision — must come from the prompt alone.

    `ask()` used to receive the whole pending buffer. On a terminal that buffer
    is routinely non-empty (progress ends in `\\r`; only `\\n` splits a line), so
    the question shown was `Checking directory /opt/azerothcore ... [marker]
    password:` — and `is_secret()` read the word "directory" and turned masking
    OFF, so the root password went into an echoed field and the log
    (review, 2026-08-22).
    """
    asked: list[str] = []
    code = (
        "import sys\n"
        "sys.stdout.write('Checking directory /opt/azerothcore ... ')\n"
        "sys.stdout.flush()\n"
        f"sys.stdout.write({MARKER + ' '!r})\n"
        "sys.stdout.flush()\n"
        "answer = sys.stdin.readline().strip()\n"
        "print('GOT:' + answer)\n"
    )
    lines = list(
        runner.interact(
            [sys.executable, "-c", code],
            respond=lambda _line: None,
            ask=lambda prompt: (asked.append(prompt), "hunter2")[1],
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    assert any("GOT:hunter2" in line for line in lines)
    assert asked == [MARKER + " "], f"ask() saw more than the prompt: {asked!r}"
    assert is_secret(asked[0]) is True, "the very leak this guards against"


def test_a_sudo_marker_shaped_label_is_masked() -> None:
    """A sudo-shaped label must not TRIP the not-secret allowlist. Nothing recognises it.

    An earlier version of this docstring said `install_wiring._terminal_prompter()`
    "and this predicate still recognise the spelling". Half of that was false and
    it was the half this file could check. `is_secret()` recognises nothing: it
    is `not _NOT_SECRET.search(prompt)` over an allowlist of harmless spellings
    (`path`, `folder`, `directory`, `(y/n)`, `press enter`), so it answers True
    for this marker, for gibberish, and for the empty string alike — measured
    2026-09-02, with the prefix and without it. The assertion passed because a
    neighbouring rule did the work, which makes it a test of the union of every
    rule here rather than of one of them.

    The rule it can honestly pin is the other direction, and it is the one that
    can break: this label must not match the allowlist. The two lines below say
    so by showing the default and then flipping the answer with a single word —
    reword the marker to mention a folder or a path, the words most likely to
    reach an install prompt, and a root password is typed into an echoed field.

    That `_terminal_prompter()` recognises the prefix at all is asserted where
    that function runs:
    `test_install_wiring.py::test_the_terminal_prompter_hides_a_password_and_shows_a_consent_question`.
    """
    marker = f"{installer.SUDO_PROMPT_PREFIX}0123456789abcdef] password:"
    assert "sudo" in marker and "password" in marker
    assert is_secret(marker) is True
    # Masked by default: this is what carried the assertion above, not the label.
    assert is_secret("") is True
    # And the allowlist is what decides — one of its words flips the same label.
    assert is_secret(marker.replace("password:", "install folder:")) is False


def test_a_canned_rule_cannot_answer_the_sudo_prompt_from_neighbouring_output() -> None:
    """The other half of the slicing fix, and the one that was missed.

    `respond()` runs before `ask()` and short-circuits it, and its rules are
    unanchored `search`es — including a bare `(y/n)`. Given the whole buffer it
    matched output printed BEFORE the prompt and typed "y" into sudo's password
    read, so `ask()` was never called and the install died on three failed
    attempts. Exactly the pre-6.1.5 symptom, by a new route.

    The child below reproduces the measured shape: a real prompt the rules DO
    answer, then a carriage return (which never splits a line here), then the
    marker (review, 2026-08-23).

    The rule is spelled inline since 7.2: it is the catch-all `PROMPT_RULES`
    ended with, and the point survives its table — ANY unanchored responder
    must not be shown the buffer in front of the marker.
    """

    def yes_to_yes_no(line: str) -> str | None:
        """The old `PROMPT_RULES` catch-all: an unanchored search for `(y/n)`."""
        return "y" if "(y/n)" in line else None

    asked: list[str] = []
    code = (
        "import sys\n"
        "sys.stdout.write('Reset the keyring? (y/n) ')\n"
        f"sys.stdout.write({chr(13)!r})\n"
        f"sys.stdout.write({MARKER + ' '!r})\n"
        "sys.stdout.flush()\n"
        "answer = sys.stdin.readline().strip()\n"
        "print('GOT:' + answer)\n"
    )
    lines = list(
        runner.interact(
            [sys.executable, "-c", code],
            respond=yes_to_yes_no,
            ask=lambda prompt: (asked.append(prompt), "hunter2")[1],
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    assert asked == [MARKER + " "], f"the rules answered sudo instead of asking: {asked!r}"
    assert any("GOT:hunter2" in line for line in lines)
    assert not any("GOT:y" in line for line in lines), "typed 'y' as the password"


def test_ask_is_never_consulted_for_a_complete_line() -> None:
    """A marker on a finished line means something ECHOED it, not that anyone is waiting.

    Nothing in the shipped scripts prints the environment, but `SUDO_PROMPT` is
    exported to every descendant, so `set -x` or `env` in a future script would
    put the marker on a complete line. Answering it writes the password into a
    build's stdin (review, 2026-08-22/23).
    """
    asked: list[str] = []
    code = "import sys\n" f"print({'SUDO_PROMPT=' + MARKER!r})\n" "sys.stdout.flush()\n"
    list(
        runner.interact(
            [sys.executable, "-c", code],
            respond=lambda _line: None,
            ask=lambda prompt: asked.append(prompt) or "WRONG",  # type: ignore[func-returns-value]
            ask_marker=MARKER,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    assert asked == [], "answered a prompt nothing was waiting on"


def test_the_last_line_survives_a_child_that_exits_without_a_newline() -> None:
    """Those bytes were what the bash engine's `run()` built its failure message
    from, and the shortcut they exercise is still every engine's.

    The "child is gone, stop waiting for EOF" shortcut required an EMPTY buffer,
    so a script whose last words had no trailing newline never took it — and
    when the wait was then cancelled, the text was dropped (review, 2026-08-23).
    """
    code = (
        "import sys\n"
        "sys.stdout.write('FATAL: could not reach the database')\n"
        "sys.stdout.flush()\n"
    )
    lines = list(
        runner.interact(
            [sys.executable, "-c", code],
            respond=lambda _line: None,
            quiet_seconds=0.15,
            cancel=_expiring_cancel(),
        )
    )
    assert any("FATAL: could not reach the database" in line for line in lines), lines


def test_the_docker_group_question_reaches_a_real_dialog_unmasked(qapp: object) -> None:
    """Drive the consent question the way a person meets it: as an actual dialog.

    Everything else about the docker-group gate is proven at the seam — the argv
    invariant, the eight mutations, and two container runs on yulon-ubuntu. None
    of that shows a dialog on a screen, and the failure this closes is specific
    and silent: `QInputDialog` decides the echo mode from `is_secret()`, so a
    question that stops matching `(y/n)` becomes a password box, and the user
    typing `y` sees a dot and reasonably concludes the launcher wants their
    password.

    So this runs the real `InputPrompter` against the real question, finds the
    modal dialog Qt actually opened, reads the text off it, and answers it —
    offscreen, but through the same widgets. `ask()` blocks on a worker thread,
    exactly as it does during an install, so the watchdog has to drive the GUI
    thread while it waits.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

    from yulon import platform as plat
    from yulon.ui.widgets.prompt import InputPrompter

    prompter = InputPrompter()
    question = plat.DOCKER_GROUP_QUESTION.format(user="pk")
    seen: dict[str, object] = {}
    answer: list[str | None] = []

    def in_the_worker() -> None:
        answer.append(prompter.ask(question))

    worker = threading.Thread(target=in_the_worker, daemon=True)
    worker.start()

    def drive() -> None:
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QDialog):
            return
        field = dialog.findChild(QLineEdit)
        assert field is not None
        seen["echo"] = field.echoMode()
        seen["shown"] = _dialog_text(dialog)
        field.setText("y")
        dialog.accept()
        watchdog.stop()

    watchdog = QTimer()
    watchdog.setInterval(20)
    watchdog.timeout.connect(drive)
    watchdog.start()

    pump_until(lambda: not worker.is_alive(), "the consent dialog was driven to an answer")
    worker.join(timeout=HANG_BOUND)
    watchdog.stop()

    assert not worker.is_alive(), "the consent dialog never appeared"
    assert answer == ["y"], answer
    # The field must be typed in the clear.
    assert seen["echo"] == QLineEdit.EchoMode.Normal, seen["echo"]
    # And the dialog must actually say the two things a user cannot infer.
    shown = str(seen["shown"])
    assert "'pk'" in shown
    assert "never creates passwordless sudo rules" in shown
    # Both halves of the warning, because they do different work and a rewrite
    # can drop one without touching the other: the claim ("full root access")
    # and the concrete thing it lets someone do, which is what makes it real to
    # a reader who does not already know what the docker group is.
    assert "full root access" in shown
    assert "mount your entire disk" in shown
    # The consequence of saying YES specifically. "log out and back in" is the
    # wrong anchor for it — that phrase is in the no-branch too, so dropping it
    # from the yes-branch left this assertion passing (caught by mutation).
    assert "then click Install again" in shown
    # And what saying NO costs, which is the half a user weighing the question
    # actually needs: the engine is still installed, but the launcher cannot
    # drive it until they join the group themselves.
    assert "Yu'lon still installs Docker Engine" in shown
    assert "sudo usermod -aG docker pk" in shown


def _dialog_text(dialog: object) -> str:
    """Every piece of text Qt is showing in `dialog`, joined."""
    from PySide6.QtWidgets import QLabel

    return " ".join(label.text() for label in dialog.findChildren(QLabel))


def test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number() -> None:
    """Every bound here must be spelled as one of the named ones, and nothing else.

    The same audit `test_log_panel.py` runs on itself, for the same reason.
    Until 2026-09-04 this file kept its own `_drain` with `timeout: float =
    5.0` and a `panel.wait(2000)` whose result was thrown away, three
    `worker.join(timeout=5)`s and a `worker.join(timeout=2.0)`, two loops
    bounded by a turn count (`for _ in range(50): process_events(20)`) that no
    clock audit can see, and one deadline built by hand (`time.monotonic() +
    10.0`). Every wait goes through `pump_until` now, and every join is
    `HANG_BOUND`.
    """
    assert spelled_bounds(__file__) == {"HANG_BOUND", "HANG_BOUND_MS"}
