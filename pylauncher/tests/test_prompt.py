"""Tests for asking the user to answer a subprocess prompt (roadmap 6.1.5).

The bug these guard against is the one that made installing on Linux impossible:
the scripts run `sudo`, `sudo` prints `[sudo] password for pk:` with no trailing
newline, no rule can answer it, and the install stopped there with the window
looking frozen.
"""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from tests.conftest import process_events
from yulon import runner
from yulon.ui.widgets.prompt import InputPrompter, is_secret, tidy


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


def test_interact_asks_the_user_when_no_rule_can_answer() -> None:
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
            _echo_prompt_script("[sudo] password for pk: "),
            respond=lambda _line: None,  # no rule matches anything
            ask=ask,
            quiet_seconds=0.15,
        )
    )
    # `ask` sees the prompt exactly as the child printed it, trailing space and
    # all; tidying is the prompter's job, at the moment of display.
    assert asked == ["[sudo] password for pk: "]
    assert any("GOT:hunter2" in line for line in lines)


def test_interact_prefers_a_rule_over_asking_the_user() -> None:
    """Never interrupt someone for a question the app already knows the answer to."""
    asked: list[str] = []

    lines = list(
        runner.interact(
            _echo_prompt_script("Continue anyway? "),
            respond=lambda line: "n" if "Continue anyway" in line else None,
            ask=lambda prompt: asked.append(prompt) or "SHOULD-NOT-BE-USED",  # type: ignore[func-returns-value]
            quiet_seconds=0.15,
        )
    )
    assert asked == []
    assert any("GOT:n" in line for line in lines)


def test_interact_without_a_prompter_still_gives_up_rather_than_hanging() -> None:
    """The old behaviour, kept: unanswerable and no prompter means cancel is the way out."""
    cancel = threading.Event()

    def cancel_soon() -> None:
        cancel.wait(0.1)
        cancel.set()

    threading.Timer(0.4, cancel.set).start()
    lines = list(
        runner.interact(
            _echo_prompt_script("[sudo] password for pk: "),
            respond=lambda _line: None,
            quiet_seconds=0.15,
            cancel=cancel,
        )
    )
    assert not any("GOT:" in line for line in lines), "nothing should have been answered"


def test_a_declined_dialog_answers_nothing_rather_than_an_empty_line() -> None:
    """Dismissing the dialog must not send a bare newline.

    A blank line is a real answer to a shell prompt — it usually means "accept
    the default" — so treating "the user pressed Cancel" as "" would silently
    confirm something they declined.
    """
    written: list[str] = []

    class _Fake:
        def write(self, data: bytes) -> int:
            written.append(data.decode())
            return len(data)

        def flush(self) -> None: ...

    lines = list(
        runner.interact(
            _echo_prompt_script("[sudo] password for pk: "),
            respond=lambda _line: None,
            ask=lambda _prompt: None,  # the user pressed Cancel
            quiet_seconds=0.15,
            cancel=_expiring_cancel(0.6),
        )
    )
    assert not any("GOT:" in line for line in lines)
    assert written == []


def _expiring_cancel(after: float) -> threading.Event:
    event = threading.Event()
    threading.Timer(after, event.set).start()
    return event


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


def test_installer_run_forwards_the_prompter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam has to actually reach `runner.interact`, not stop at the installer."""
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


def _first_installable_entry():  # type: ignore[no-untyped-def]
    from yulon.catalog.catalog import load_catalog

    return load_catalog().get("wow-wotlk")


def test_a_real_shell_prompt_is_answered_by_the_prompter() -> None:
    """`read -p`-style prompts are the actual shape the install scripts use."""
    if not _has_bash():
        pytest.skip("no bash that can run a script on this machine")
    script = 'printf "Enter the magic word: "; read word; echo "GOT:$word"'
    lines = list(
        runner.interact(
            ["bash", "-c", script],
            respond=lambda _line: None,
            ask=lambda _prompt: "please",
            quiet_seconds=0.2,
        )
    )
    assert any("GOT:please" in line for line in lines)


def _has_bash() -> bool:
    from yulon.catalog.installer import bash_available

    try:
        return bash_available()
    except (OSError, subprocess.SubprocessError):
        return False
