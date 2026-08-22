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
from pathlib import Path

import pytest

from tests.conftest import process_events
from yulon import runner
from yulon.catalog.installer import Installer
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
    from yulon.catalog.installer import bash_available

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
    for _ in range(50):
        process_events(20)
        if not worker.is_alive():
            break
    worker.join(timeout=5)

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
    worker.join(timeout=5)

    assert not worker.is_alive(), "ask() never returned after cancel"
    assert answer == [None]


def test_installer_run_forwards_the_prompter_the_marker_and_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three have to reach `runner.interact`, and the marker has to match SUDO_PROMPT.

    Forwarding `ask` alone is what the first version did, and it delivered
    nothing: without the marker the seam never fires, and without the terminal
    sudo's prompt never arrives to fire it.
    """
    from yulon.catalog import installer as installer_module

    seen: dict[str, object] = {}

    def fake_interact(command, cwd=None, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        yield "done"

    entry = _first_installable_entry()
    inst = installer_module.Installer(entry, interact=fake_interact)
    monkeypatch.setattr(inst, "preflight", lambda *a, **k: None)

    def ask(_prompt: str) -> str:
        return "x"

    list(inst.run(installer_module.InstallOptions(), ask=ask))
    assert seen.get("ask") is ask
    assert seen.get("terminal") is True
    assert seen.get("ask_marker") == inst.sudo_marker
    # The marker is only useful because sudo is told to print exactly it.
    assert inst.script_env()["SUDO_PROMPT"] == inst.sudo_marker


def test_each_install_gets_its_own_unguessable_sudo_marker() -> None:
    """A fixed string could be printed by a script; a per-install random one cannot."""
    from yulon.catalog import installer as installer_module

    entry = _first_installable_entry()
    first = installer_module.Installer(entry).sudo_marker
    second = installer_module.Installer(entry).sudo_marker
    assert first != second
    assert len(first) > 20


def _first_installable_entry():  # type: ignore[no-untyped-def]
    from yulon.catalog.catalog import load_catalog

    return load_catalog().get("wow-wotlk")


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
    for _ in range(50):
        process_events(20)
        if not worker.is_alive():
            break
    worker.join(timeout=5)

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
    from yulon.ui.widgets.log_panel import LogPanel

    catalog = load_catalog()
    view = CatalogView(
        catalog,
        lambda e: _NoopInstaller(e),
        LogPanel(),
        platform_id=lambda: "linux",
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
    )
    view.start_install(catalog.get("wow-wotlk"))
    first = view._prompter
    view._log.wait(2000)
    process_events(50)
    view.start_install(catalog.get("wow-wotlk"))
    assert view._prompter is first, "a second install built a second prompter"
    assert len(view.findChildren(InputPrompter)) == 1


class _NoopInstaller(Installer):
    """An installer whose run() yields nothing, so start_install() returns at once."""

    def __init__(self, entry: object) -> None:
        super().__init__(entry, docker_check=lambda: True)  # type: ignore[arg-type]

    def preflight(self, options: object, cancel: object = None) -> None:  # type: ignore[override]
        return None

    def run(self, options: object = None, *, cancel: object = None, ask: object = None):  # type: ignore[override]
        yield "done"
