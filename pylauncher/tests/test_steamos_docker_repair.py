"""Putting Docker back on a Steam Deck after a SteamOS update (T160).

Nothing here touches a real package manager: `run` and `run_input` are one
scripted Deck (`_Deck`), which answers each command the way the step it stands
for would, and records every argv it was handed so the assertions are made on
the commands themselves, in order. The questions go through the real
`SudoSession` and the real `_settle_docker_group()`; only the answers are
scripted.

`set_own_password()` is tested against a real pseudo-terminal and small bash
stand-ins for `passwd` that read from /dev/tty, as the real one does.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import platform, runner
from yulon.ui.widgets.prompt import is_secret

USER = "deck"
PASSWORD = "c0rrect-horse"

_REQUIRED = "sudo: a password is required\n"


@pytest.fixture(autouse=True)
def _on_steamos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here is on a Deck unless it says otherwise: the repair re-checks (T160 review)."""
    monkeypatch.setattr(platform, "is_steamos", lambda: True)


class _Deck:
    """One Steam Deck as the repair sees it, scripted per test.

    `sudo_needs_password` is the stock-Deck answer to `sudo -n` ("a password
    is required"); `passwd_status` is the second field of `passwd -S`. Steps
    are answered through BOTH transports — `sudo -n <cmd>` via `__call__` and
    `sudo -S -p '' <cmd>` via `feed` — from the same table, so a test reads the
    same order whichever route a step took. `docker info` only answers once
    `systemctl enable --now docker` has run and `daemon_starts` is true, so a
    report that says "ready" had to have asked after the service step.
    """

    def __init__(
        self,
        *,
        sudo_needs_password: bool = False,
        passwd_status: str = "P",
        groups: str = "deck wheel docker",
        fail: dict[str, tuple[int, str]] | None = None,
        daemon_starts: bool = True,
        daemon_reachable: bool = True,
        accept: str = PASSWORD,
    ) -> None:
        self.sudo_needs_password = sudo_needs_password
        self.passwd_status = passwd_status
        self.groups = groups
        self.fail = fail or {}
        self.daemon_starts = daemon_starts
        self.daemon_reachable = daemon_reachable
        self.accept = accept
        self.calls: list[list[str]] = []
        self.feeds: list[tuple[list[str], str]] = []
        self.steps: list[str] = []
        self.started = False

    def _step(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        shown = " ".join(cmd)
        self.steps.append(shown)
        if shown in self.fail:
            rc, err = self.fail[shown]
            return subprocess.CompletedProcess(cmd, rc, "", err)
        if shown == "systemctl enable --now docker" and self.daemon_starts:
            self.started = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv == ["sudo", "-n", "true"]:
            if self.sudo_needs_password:
                return subprocess.CompletedProcess(argv, 1, "", _REQUIRED)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv == ["passwd", "-S"]:
            line = f"{USER} {self.passwd_status} 2026-09-27 0 99999 7 -1\n"
            return subprocess.CompletedProcess(argv, 0, line, "")
        if argv[:2] == ["id", "-nG"]:
            return subprocess.CompletedProcess(argv, 0, self.groups + "\n", "")
        if argv[:2] == ["docker", "info"]:
            rc = 0 if self.started and self.daemon_reachable else 1
            return subprocess.CompletedProcess(argv, rc, "", "")
        if argv == ["systemctl", "is-active", "docker"]:
            state = "active" if self.started else "inactive"
            return subprocess.CompletedProcess(argv, 0 if self.started else 3, state + "\n", "")
        if argv[:2] == ["sudo", "-n"]:
            if self.sudo_needs_password:
                return subprocess.CompletedProcess(argv, 1, "", _REQUIRED)
            return self._step(argv[2:])
        raise AssertionError(f"the Deck was asked something no step asks: {argv}")

    def feed(self, argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        self.feeds.append((argv, text))
        assert argv[:4] == ["sudo", "-S", "-p", ""], argv
        if text != self.accept + "\n":
            return subprocess.CompletedProcess(argv, 1, "", "Sorry, try again.")
        if argv[4:] == ["-v"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return self._step(argv[4:])


def _answers(*replies: str | None) -> tuple[list[str], Callable[[str], str | None]]:
    """An `ask` that hands out `replies` in order and records every question."""
    asked: list[str] = []
    queue = list(replies)

    def ask(question: str) -> str | None:
        asked.append(question)
        if not queue:
            raise AssertionError(f"asked more than was scripted: {question!r}")
        return queue.pop(0)

    return asked, ask


def _devmode(present: bool) -> Callable[[str], str | None]:
    return lambda name: (
        "/usr/bin/steamos-devmode" if present and name == "steamos-devmode" else None
    )


def _repair(
    deck: _Deck,
    ask: Callable[[str], str | None],
    *,
    devmode: bool = True,
    set_password: platform.PasswordSetter | None = None,
) -> platform.ProvisionReport:
    def never_sets(_password: str) -> platform.PasswordChange:
        raise AssertionError("the password was set on a Deck that did not need one")

    return platform.repair_docker_after_steamos_update(
        ask=ask,
        run=deck,
        which=_devmode(devmode),
        run_input=deck.feed,
        set_password=set_password if set_password is not None else never_sets,
        user=USER,
        wait_seconds=0.0,
    )


SCRIPT_ORDER = [
    "steamos-readonly disable",
    "rm -rf /etc/pacman.d/gnupg",
    "pacman-key --init",
    "pacman-key --populate archlinux holo",
    "steamos-devmode enable",
    "pacman -Sy --noconfirm archlinux-keyring",
    "pacman -Sy --noconfirm docker docker-compose docker-buildx",
    "systemctl daemon-reload",
    "systemctl enable --now docker",
]


# ------------------------------------------------------------------ the steps


def test_the_repair_runs_the_upstream_scripts_steps_in_its_order() -> None:
    """Baerthe's `fix-after-update.sh`, step for step, and the keyring reset every time.

    Owner's decisions, 2026-09-27: reset the keyring always, run
    `steamos-devmode enable` when it exists, leave the read-only lock off.
    """
    deck = _Deck()
    asked, ask = _answers("y")

    report = _repair(deck, ask)

    assert deck.steps == SCRIPT_ORDER
    assert report.docker_ready and report.ok, report
    assert report.manual_steps[0] == platform.STEAMOS_DOCKER_BACK_STEP
    assert asked == [
        platform.STEAMOS_DOCKER_REPAIR_QUESTION
    ], "a member is not asked about the group"


def test_the_read_only_lock_is_left_off_and_the_report_says_so() -> None:
    """The owner's choice for THIS repair: match the script, which never relocks.

    Said in the report every time the lock was switched off, because touching
    it has to be visible (T57's definition of done). The other SteamOS paths
    still relock: that is pinned where they are tested (`test_provision.py`).
    """
    deck = _Deck()
    report = _repair(deck, _answers("y")[1])

    assert "steamos-readonly enable" not in deck.steps
    assert platform.STEAMOS_READONLY_LEFT_OFF_STEP in report.manual_steps
    assert platform.docker_engine_commands("pacman", steamos=True)[-2] == [
        "steamos-readonly",
        "enable",
    ], "the install path's relock must not have been taken out with this one"


def test_the_success_text_names_start_and_no_folder() -> None:
    """The script's closing line was `cd ~/wow-server && docker compose up -d`."""
    report = _repair(_Deck(), _answers("y")[1])
    said = " ".join(report.manual_steps)
    assert "Press Start" in said
    assert "docker compose" not in said and "wow-server" not in said


def test_no_devmode_command_means_no_devmode_step() -> None:
    deck = _Deck()
    _repair(deck, _answers("y")[1], devmode=False)
    assert deck.steps == [s for s in SCRIPT_ORDER if s != "steamos-devmode enable"]


def test_a_failing_devmode_is_carried_past_like_the_script_does() -> None:
    deck = _Deck(fail={"steamos-devmode enable": (1, "devmode: not supported")})
    report = _repair(deck, _answers("y")[1])
    assert deck.steps == SCRIPT_ORDER
    assert report.docker_ready


def test_a_failing_archlinux_keyring_update_is_carried_past() -> None:
    deck = _Deck(fail={"pacman -Sy --noconfirm archlinux-keyring": (1, "error: failed retrieving")})
    report = _repair(deck, _answers("y")[1])
    assert deck.steps == SCRIPT_ORDER
    assert report.docker_ready


def test_a_readonly_unlock_that_fails_stops_before_the_keyring_is_deleted() -> None:
    """The keyring is deleted only on a run that can go on to rebuild it and install."""
    deck = _Deck(fail={"steamos-readonly disable": (1, "steamos-readonly: not permitted")})
    report = _repair(deck, _answers("y")[1])

    assert deck.steps == ["steamos-readonly disable"]
    assert not report.docker_ready
    assert report.manual_steps[0] == platform.STEAMOS_DOCKER_STOPPED_STEP.format(
        step="steamos-readonly disable"
    )
    assert platform.STEAMOS_READONLY_LEFT_OFF_STEP not in report.manual_steps


def test_a_docker_install_that_fails_stops_and_names_the_step_that_did() -> None:
    install = "pacman -Sy --noconfirm docker docker-compose docker-buildx"
    deck = _Deck(fail={install: (1, "error: required key missing from keyring")})

    report = _repair(deck, _answers("y")[1])

    assert deck.steps == SCRIPT_ORDER[: SCRIPT_ORDER.index(install) + 1]
    assert not any(s.startswith("systemctl") for s in deck.steps)
    assert report.manual_steps[0] == platform.STEAMOS_DOCKER_STOPPED_STEP.format(step=install)
    assert "required key missing from keyring" in report.manual_steps[1]
    assert platform.STEAMOS_READONLY_LEFT_OFF_STEP in report.manual_steps


def test_declining_the_repair_runs_nothing_at_all() -> None:
    deck = _Deck()
    report = _repair(deck, _answers("n")[1])

    assert deck.calls == [] and deck.feeds == []
    assert report.manual_steps == (platform.STEAMOS_DOCKER_REPAIR_DECLINED_STEP,)


def test_a_dismissed_question_is_a_no() -> None:
    deck = _Deck()
    report = _repair(deck, _answers(None)[1])
    assert deck.calls == []
    assert report.manual_steps == (platform.STEAMOS_DOCKER_REPAIR_DECLINED_STEP,)


def test_the_keyring_warning_is_the_first_question_and_nothing_ran_before_it() -> None:
    """Asked once, up front, with the script's warning in plain words (owner, 2026-09-27)."""
    deck = _Deck()
    seen: list[int] = []

    def ask(question: str) -> str | None:
        seen.append(len(deck.calls) + len(deck.feeds))
        return "y"

    _repair(deck, ask)

    assert seen[0] == 0, "something ran before the user agreed to the repair"
    question = platform.STEAMOS_DOCKER_REPAIR_QUESTION
    for words in (
        "/etc/pacman.d/gnupg",
        "standard Arch Linux and SteamOS keys",
        "add them again",
        "On a normal Steam Deck nothing else changes",
    ):
        assert words in question, words
    assert not is_secret(question), "the yes/no answer would be typed into a password box"


# ------------------------------------------------------------- the docker group


def test_a_deck_the_update_dropped_from_the_group_is_asked_and_rejoined() -> None:
    deck = _Deck(groups="deck wheel")
    asked, ask = _answers("y", "y")

    report = _repair(deck, ask)

    assert asked[1] == platform.DOCKER_GROUP_QUESTION.format(user=USER)
    assert deck.steps[-1] == f"usermod -aG docker {USER}"
    assert deck.steps[:-1] == SCRIPT_ORDER, "the join runs after the package made the group"
    assert report.docker_group == "granted"


def test_a_declined_group_is_not_joined() -> None:
    deck = _Deck(groups="deck wheel", daemon_reachable=False)
    report = _repair(deck, _answers("y", "n")[1])

    assert not any("usermod" in s for s in deck.steps)
    assert report.docker_group == "declined"
    assert platform.DOCKER_GROUP_DECLINED_STEP.format(user=USER) in report.manual_steps


def test_a_join_that_fails_is_reported_as_failed_not_granted() -> None:
    deck = _Deck(groups="deck wheel", fail={f"usermod -aG docker {USER}": (6, "no group")})
    report = _repair(deck, _answers("y", "y")[1])
    assert report.docker_group == "join-failed"
    assert platform.DOCKER_GROUP_JOIN_FAILED_STEP.format(user=USER) in report.manual_steps


# ---------------------------------------------------------------- verification


def test_a_daemon_that_never_started_says_restart_the_deck() -> None:
    deck = _Deck(daemon_starts=False)
    report = _repair(deck, _answers("y")[1])
    assert not report.docker_ready
    assert platform.STEAMOS_DOCKER_NOT_STARTED_STEP in report.manual_steps


def test_a_running_daemon_this_process_cannot_reach_says_restart_yulon() -> None:
    deck = _Deck(daemon_reachable=False)
    report = _repair(deck, _answers("y")[1])
    assert not report.docker_ready
    assert platform.STEAMOS_DOCKER_SESSION_STEP in report.manual_steps
    assert platform.STEAMOS_DOCKER_NOT_STARTED_STEP not in report.manual_steps


# ------------------------------------------------------------------- passwords


def test_a_deck_with_a_password_is_asked_for_it_once_and_never_offered_a_new_one() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="P")
    asked, ask = _answers("y", PASSWORD)

    report = _repair(deck, ask)

    assert asked == [platform.STEAMOS_DOCKER_REPAIR_QUESTION, platform.SUDO_PASSWORD_QUESTION]
    assert deck.steps == SCRIPT_ORDER
    assert report.docker_ready


def test_a_wrong_password_stops_with_password_advice_not_network_advice() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="P")
    report = _repair(deck, _answers("y", "nope", "nope", "nope")[1])

    assert deck.steps == []
    assert report.manual_steps[0] == platform.STEAMOS_DOCKER_NEEDS_PASSWORD_STEP.format(
        why="the sudo password was refused"
    )
    assert "online" not in report.manual_steps[0]


def test_a_locked_account_is_not_offered_a_password_it_could_not_set() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="L")
    asked, ask = _answers("y", None)
    _repair(deck, ask)
    assert platform.STEAMOS_SET_PASSWORD_QUESTION.format(user=USER) not in asked


def test_passwordless_sudo_is_never_asked_about_passwords_at_all() -> None:
    deck = _Deck(sudo_needs_password=False, passwd_status="NP")
    asked, ask = _answers("y")
    _repair(deck, ask)
    assert ["passwd", "-S"] not in deck.calls
    assert asked == [platform.STEAMOS_DOCKER_REPAIR_QUESTION]


def test_a_deck_with_no_password_is_offered_one_and_the_repair_carries_on_with_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Owner's decision 3: set it in the app, then use it — without asking a third time."""
    caplog.set_level(logging.DEBUG)
    deck = _Deck(sudo_needs_password=True, passwd_status="NP")
    asked, ask = _answers("y", "y", PASSWORD, PASSWORD)
    set_to: list[str] = []

    def setter(password: str) -> platform.PasswordChange:
        set_to.append(password)
        return platform.PasswordChange(True, ("passwd: password updated successfully",))

    report = _repair(deck, ask, set_password=setter)

    assert set_to == [PASSWORD]
    assert asked == [
        platform.STEAMOS_DOCKER_REPAIR_QUESTION,
        platform.STEAMOS_SET_PASSWORD_QUESTION.format(user=USER),
        platform.STEAMOS_NEW_PASSWORD_QUESTION.format(user=USER),
        platform.STEAMOS_NEW_PASSWORD_AGAIN.format(user=USER),
    ], "the sudo password was asked for after the user had just chosen it"
    assert deck.steps == SCRIPT_ORDER
    assert deck.feeds[0] == (["sudo", "-S", "-p", "", "-v"], PASSWORD + "\n")
    assert all(PASSWORD not in argv for argv, _ in deck.feeds)
    assert all(PASSWORD not in " ".join(argv) for argv in deck.calls)
    assert PASSWORD not in caplog.text
    assert report.docker_ready


def test_the_new_password_questions_are_masked_and_the_offer_is_not() -> None:
    assert not is_secret(platform.STEAMOS_SET_PASSWORD_QUESTION.format(user=USER))
    assert is_secret(platform.STEAMOS_NEW_PASSWORD_QUESTION.format(user=USER))
    assert is_secret(platform.STEAMOS_NEW_PASSWORD_AGAIN.format(user=USER))
    assert is_secret(
        platform.STEAMOS_PASSWORDS_DIFFER + platform.STEAMOS_NEW_PASSWORD_QUESTION.format(user=USER)
    )


def test_two_different_passwords_are_asked_again_and_only_a_match_is_set() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="NP")
    asked, ask = _answers("y", "y", "first", "second", PASSWORD, PASSWORD)
    set_to: list[str] = []

    def setter(password: str) -> platform.PasswordChange:
        set_to.append(password)
        return platform.PasswordChange(True)

    _repair(deck, ask, set_password=setter)

    assert set_to == [PASSWORD]
    assert asked[
        4
    ] == platform.STEAMOS_PASSWORDS_DIFFER + platform.STEAMOS_NEW_PASSWORD_QUESTION.format(
        user=USER
    )


def test_declining_to_set_a_password_changes_nothing_and_points_at_konsole() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="NP")
    report = _repair(deck, _answers("y", "n")[1])

    assert deck.steps == [] and deck.feeds == []
    assert report.manual_steps == (platform.STEAMOS_SET_PASSWORD_BY_HAND_STEP.format(user=USER),)
    assert "Konsole" in report.manual_steps[0] and "passwd" in report.manual_steps[0]


def test_a_password_passwd_refused_falls_back_to_konsole_with_passwds_reason() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="NP")

    def refuses(_password: str) -> platform.PasswordChange:
        return platform.PasswordChange(False, ("BAD PASSWORD: it is too short",))

    report = _repair(deck, _answers("y", "y", "short", "short")[1], set_password=refuses)

    assert deck.steps == [] and deck.feeds == []
    assert "BAD PASSWORD: it is too short" in report.manual_steps[0]
    assert report.manual_steps[-1] == platform.STEAMOS_SET_PASSWORD_BY_HAND_STEP.format(user=USER)


def test_a_set_password_sudo_will_not_take_runs_nothing() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="NP", accept="something else")

    def sets(_password: str) -> platform.PasswordChange:
        return platform.PasswordChange(True)

    report = _repair(deck, _answers("y", "y", PASSWORD, PASSWORD)[1], set_password=sets)

    assert deck.steps == []
    assert [argv for argv, _ in deck.feeds] == [["sudo", "-S", "-p", "", "-v"]]
    assert "sudo would not accept it" in report.manual_steps[0]


def test_adopt_proves_the_password_without_opening_a_dialog() -> None:
    deck = _Deck()

    def never(_q: str) -> str | None:
        raise AssertionError("adopt() opened the password dialog")

    session = platform.SudoSession(never, deck.feed)
    assert session.adopt(PASSWORD) is True
    assert session.asked == 0 and session.outcome == "verified"
    assert session.verify() is True, "a later step must reuse it, not ask"
    assert PASSWORD not in repr(session) and PASSWORD not in repr(vars(session))


def test_adopt_of_a_password_sudo_refuses_is_refused() -> None:
    session = platform.SudoSession(lambda _q: None, _Deck(accept="other").feed)
    assert session.adopt(PASSWORD) is False
    assert session.outcome == "refused"


# ------------------------------------------------------------------- detection


@pytest.mark.parametrize(
    ("steamos", "docker", "offered"),
    [(True, None, True), (True, "/usr/bin/docker", False), (False, None, False)],
)
def test_the_offer_is_for_a_steamos_deck_whose_docker_command_is_gone(
    monkeypatch: pytest.MonkeyPatch, steamos: bool, docker: str | None, offered: bool
) -> None:
    monkeypatch.setattr(platform, "is_steamos", lambda: steamos)
    which = lambda name: docker if name == "docker" else None  # noqa: E731
    assert platform.steamos_docker_removed(which) is offered


# --------------------------------------------------------- setting the password

needs_tty = pytest.mark.skipif(
    not runner.pty_supported(), reason="no pseudo-terminal on this platform (Windows)"
)


def _passwd(tmp_path: Path, body: str) -> list[str]:
    """A bash stand-in for `passwd` that reads its answers from /dev/tty, as the real one does."""
    script = tmp_path / "passwd"
    script.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return ["bash", str(script)]


_PAM_BLANK = """\
printf 'Changing password for deck.\\n'
printf 'New password: '
IFS= read -r a < /dev/tty
printf '\\nRetype new password: '
IFS= read -r b < /dev/tty
printf '\\n'
if [ "$a" != "$b" ]; then echo 'Sorry, passwords do not match.'; exit 10; fi
printf '%s' "$a" > "{out}"
echo 'passwd: password updated successfully'
"""
"""Linux-PAM `pam_unix` for an account with an EMPTY password: no current-password prompt."""

_PAM_ASKS_CURRENT = """\
printf 'Changing password for deck.\\n'
printf 'Current password: '
IFS= read -r c < /dev/tty
if [ -n "$c" ]; then echo 'passwd: Authentication token manipulation error'; exit 10; fi
printf '\\nNew password: '
IFS= read -r a < /dev/tty
printf '\\nRetype new password: '
IFS= read -r b < /dev/tty
printf '\\n'
if [ "$a" != "$b" ]; then echo 'Sorry, passwords do not match.'; exit 10; fi
printf '%s' "$a" > "{out}"
echo 'passwd: password updated successfully'
"""
"""A stack that asks for the current password anyway; for an `NP` account that is the empty line."""

_QUALITY_REFUSES = """\
for i in 1 2 3; do
  printf 'New password: '
  IFS= read -r a < /dev/tty
  printf '\\nBAD PASSWORD: The password is shorter than 8 characters\\n'
  echo "$i" >> "{out}"
done
echo 'passwd: Have exhausted maximum number of retries for service'
exit 1
"""
"""A quality check that refuses: it asks again, and the same password must not be typed again."""


@needs_tty
def test_set_own_password_answers_new_and_retype_on_a_terminal(tmp_path: Path) -> None:
    out = tmp_path / "set-to"
    change = platform.set_own_password(
        PASSWORD, command=_passwd(tmp_path, _PAM_BLANK.format(out=out)), timeout=10.0
    )
    assert change.ok, change
    assert out.read_text(encoding="utf-8") == PASSWORD
    assert "passwd: password updated successfully" in change.said
    assert all(PASSWORD not in line for line in change.said)


@needs_tty
def test_set_own_password_answers_a_current_prompt_with_the_empty_password(tmp_path: Path) -> None:
    out = tmp_path / "set-to"
    change = platform.set_own_password(
        PASSWORD, command=_passwd(tmp_path, _PAM_ASKS_CURRENT.format(out=out)), timeout=10.0
    )
    assert change.ok, change
    assert out.read_text(encoding="utf-8") == PASSWORD


@needs_tty
def test_a_refused_password_is_typed_once_and_the_run_stops(tmp_path: Path) -> None:
    out = tmp_path / "rounds"
    started = time.monotonic()
    change = platform.set_own_password(
        PASSWORD, command=_passwd(tmp_path, _QUALITY_REFUSES.format(out=out)), timeout=10.0
    )
    assert not change.ok
    assert time.monotonic() - started < 8.0, "it waited for the watchdog instead of stopping"
    assert out.read_text(encoding="utf-8").split() == ["1"], "the refused password was typed again"
    assert any("BAD PASSWORD" in line for line in change.said), change.said


@needs_tty
def test_a_passwd_that_fails_is_not_ok(tmp_path: Path) -> None:
    body = "printf 'New password: '; IFS= read -r a < /dev/tty; echo; echo 'passwd: nope'; exit 3\n"
    change = platform.set_own_password(PASSWORD, command=_passwd(tmp_path, body), timeout=10.0)
    assert not change.ok
    assert "passwd: nope" in change.said


def test_a_missing_passwd_is_not_ok(tmp_path: Path) -> None:
    change = platform.set_own_password(
        PASSWORD, command=[str(tmp_path / "no-such-passwd")], timeout=5.0
    )
    assert not change.ok


# ------------------------------------------------ preconditions and the lock (round 2)


def test_a_host_that_is_not_steamos_is_refused_before_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex [high]: the repair checks the machine itself, not only the button."""
    monkeypatch.setattr(platform, "is_steamos", lambda: False)
    deck = _Deck()
    asked, ask = _answers()

    report = _repair(deck, ask)

    assert asked == [] and deck.calls == [] and deck.feeds == []
    assert report.manual_steps == (platform.STEAMOS_DOCKER_REPAIR_NOT_STEAMOS,)


def test_a_deck_that_has_docker_is_refused_before_anything() -> None:
    deck = _Deck()
    asked, ask = _answers()

    def which(name: str) -> str | None:
        return "/usr/bin/docker" if name == "docker" else None

    report = platform.repair_docker_after_steamos_update(
        ask=ask, run=deck, which=which, run_input=deck.feed, user=USER, wait_seconds=0.0
    )

    assert asked == [] and deck.calls == []
    assert report.manual_steps == (platform.STEAMOS_DOCKER_REPAIR_DOCKER_PRESENT,)


def test_docker_appearing_after_the_yes_stops_the_run_before_the_keyring_is_deleted() -> None:
    """Checked again just before the destructive step, with a machine that changed in between."""
    deck = _Deck()

    def which(name: str) -> str | None:
        # Docker "installed by hand in Konsole" once the unlock has run.
        if name == "docker" and "steamos-readonly disable" in deck.steps:
            return "/usr/bin/docker"
        return None

    report = platform.repair_docker_after_steamos_update(
        ask=_answers("y")[1],
        run=deck,
        which=which,
        run_input=deck.feed,
        user=USER,
        wait_seconds=0.0,
    )

    assert deck.steps == ["steamos-readonly disable"], "the keyring was deleted anyway"
    assert report.manual_steps[0] == platform.STEAMOS_DOCKER_REPAIR_DOCKER_PRESENT
    assert platform.STEAMOS_READONLY_LEFT_OFF_STEP in report.manual_steps
    assert not report.docker_ready


def test_a_second_repair_while_one_runs_is_refused_without_a_question() -> None:
    """Reviewer: two tabs, one keyring. The second press is refused before it asks."""
    deck = _Deck()
    inner: list[platform.ProvisionReport] = []
    running: list[bool] = []

    def ask(question: str) -> str | None:
        if question == platform.STEAMOS_DOCKER_REPAIR_QUESTION and not inner:
            running.append(platform.steamos_docker_repair_running())
            second_deck = _Deck()
            inner.append(_repair(second_deck, _answers()[1]))
            assert second_deck.calls == [] and second_deck.feeds == []
        return "y"

    first = _repair(deck, ask)

    assert running == [True]
    assert inner[0].manual_steps == (platform.STEAMOS_DOCKER_REPAIR_BUSY,)
    assert first.docker_ready, "the first repair was disturbed by the refused one"
    assert not platform.steamos_docker_repair_running(), "the lock outlived the repair"


def test_the_lock_is_given_back_when_the_repair_raises() -> None:
    def boom(_question: str) -> str | None:
        raise RuntimeError("the dialog broke")

    with pytest.raises(RuntimeError):
        _repair(_Deck(), boom)
    assert not platform.steamos_docker_repair_running()


def test_a_stop_between_steps_says_the_user_stopped_it() -> None:
    deck = _Deck()
    cancel = threading.Event()

    def ask(question: str) -> str | None:
        return "y"

    original = deck._step

    def step_then_stop(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        proc = original(cmd)
        if cmd == ["pacman-key", "--init"]:
            cancel.set()
        return proc

    deck._step = step_then_stop  # type: ignore[method-assign]
    report = platform.repair_docker_after_steamos_update(
        ask=ask,
        run=deck,
        which=_devmode(True),
        run_input=deck.feed,
        user=USER,
        wait_seconds=0.0,
        cancel=cancel,
    )

    assert deck.steps == SCRIPT_ORDER[:3]
    assert report.manual_steps[0] == platform.STEAMOS_DOCKER_REPAIR_STOPPED_AT.format(
        step="pacman-key --populate archlinux holo"
    )
    assert "online" not in report.manual_steps[0]


# ------------------------------------------------------ passwd that is stopped part way


def test_an_interrupted_passwd_that_did_set_the_password_carries_on_with_it() -> None:
    """`passwd -S` decides, not the interruption: `P` means it was written (T160 review)."""
    deck = _Deck(sudo_needs_password=True, passwd_status="NP")

    def set_then_hang(_password: str) -> platform.PasswordChange:
        deck.passwd_status = "P"
        return platform.PasswordChange(False, (), interrupted=True)

    report = _repair(deck, _answers("y", "y", PASSWORD, PASSWORD)[1], set_password=set_then_hang)

    assert deck.steps == SCRIPT_ORDER
    assert report.docker_ready


def test_an_interrupted_passwd_that_set_nothing_falls_back_to_konsole() -> None:
    deck = _Deck(sudo_needs_password=True, passwd_status="NP")

    def hangs(_password: str) -> platform.PasswordChange:
        return platform.PasswordChange(False, (), interrupted=True)

    report = _repair(deck, _answers("y", "y", PASSWORD, PASSWORD)[1], set_password=hangs)

    assert deck.steps == [] and deck.feeds == []
    assert "stopped before passwd finished" in report.manual_steps[0]
    assert report.manual_steps[-1] == platform.STEAMOS_SET_PASSWORD_BY_HAND_STEP.format(user=USER)


_UNRECOGNISED = """\
printf 'Enter the magic word: '
IFS= read -r a < /dev/tty
echo "$a" > "{out}"
"""


@needs_tty
def test_a_prompt_nobody_recognises_is_left_alone_and_the_watchdog_ends_it(
    tmp_path: Path,
) -> None:
    out = tmp_path / "answered"
    started = time.monotonic()
    change = platform.set_own_password(
        PASSWORD, command=_passwd(tmp_path, _UNRECOGNISED.format(out=out)), timeout=1.0
    )
    took = time.monotonic() - started

    assert not change.ok and change.interrupted
    assert not out.exists(), "an unrecognised prompt was answered"
    assert 0.9 <= took < 6.0, took


@needs_tty
def test_a_cancel_stops_passwd_long_before_its_deadline(tmp_path: Path) -> None:
    out = tmp_path / "answered"
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    change = platform.set_own_password(
        PASSWORD,
        command=_passwd(tmp_path, _UNRECOGNISED.format(out=out)),
        timeout=30.0,
        cancel=cancel,
    )
    assert not change.ok and change.interrupted
    assert time.monotonic() - started < 6.0


@needs_tty
def test_a_clean_passwd_run_is_not_interrupted(tmp_path: Path) -> None:
    out = tmp_path / "set-to"
    change = platform.set_own_password(
        PASSWORD, command=_passwd(tmp_path, _PAM_BLANK.format(out=out)), timeout=10.0
    )
    assert change.ok and not change.interrupted


def test_the_new_password_question_warns_that_a_short_one_may_be_refused() -> None:
    question = platform.STEAMOS_NEW_PASSWORD_QUESTION.format(user=USER)
    assert "very short one may be refused" in question
    assert is_secret(question)
