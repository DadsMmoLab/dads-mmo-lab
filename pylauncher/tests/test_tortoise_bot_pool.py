"""T123: after the bots module moves, the bot accounts an older module made are enrolled again.

TortoiseBots (2026-09-24, issue #265) runs only the bot accounts listed in its
own registry table, and nothing back-fills that table. An install made on the
old pin and moved onto the new module by "Update the server to latest…" keeps
its 500 bots on accounts the module ignores: measured on yulon-ubuntu with the
registry emptied, 0 of them came online and auto-create began building a second
pool beside them. The module's own remedy is `bot pool adopt preview` and then
`bot pool adopt confirm <challenge>`, and its candidates load only at world
start, so the adoption is followed by a restart.

The answers below are the module's own words at c591bbb1
(`commands/BotCommands.cpp:2763-2828`), trimmed to the lines the reader keys on
plus a few around them. Measured answers, not invented ones: the preview and
confirm texts were captured over SOAP on the box
(`.notes/gates/t123-tortoisebots-adopt/`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yulon.channel import Answer
from yulon.controller_wow_tortoise import botpool, game

ENTRY = game.entry()

CHALLENGE = "72C1FF2923F1"

PREVIEW_PENDING = "\n".join(
    (
        "Accounts matching prefix 'RNDBOT': 55",
        "  #4 RNDBOT782208 - 10 character(s) [NOT managed]",
        "  #5 RNDBOT134329 - 10 character(s) [NOT managed]",
        "  #56 RNDBOT266173 - 10 character(s) [already managed]",
        "Total: 55 account(s), 540 character(s); 51 account(s) still need adoption.",
        "Adoption registers accounts only; no character is changed or deleted.",
        f"To enroll them, run exactly: bot pool adopt confirm {CHALLENGE} (valid for 5 minutes)",
    )
)
PREVIEW_ALL_MANAGED = "\n".join(
    (
        "Accounts matching prefix 'RNDBOT': 50",
        "  #4 RNDBOT782208 - 10 character(s) [already managed]",
        "Total: 50 account(s), 500 character(s); 0 account(s) still need adoption.",
        "Adoption registers accounts only; no character is changed or deleted.",
        "Every matching account is already managed.",
    )
)
PREVIEW_NONE = "Accounts matching prefix 'RNDBOT': 0\nNothing to adopt."
CONSOLE_ONLY = "This command is only available at the server console."
CONFIRMED = (
    "Adoption complete: 51 account(s) registered, 10 already managed. No character was changed."
)
SET_CHANGED = (
    "Adoption aborted: the matching account set changed since the preview; "
    "run 'bot pool adopt preview' again"
)
CONFIRM = f"bot pool adopt confirm {CHALLENGE}"


@dataclass
class ScriptedChannel:
    """A channel that answers from a script, in order, and records what it was sent."""

    answers: list[Answer]
    sent: list[str] = field(default_factory=list)

    def send(self, command: str) -> Answer:
        self.sent.append(command)
        return self.answers.pop(0)


def yes(text: str) -> Answer:
    return Answer("yes", text)


TIMEOUT = Answer("unknown", reason="did not answer", indeterminate=True)


# ------------------------------------------------------------------ the reader


def test_a_preview_with_accounts_to_adopt_is_read_as_their_count_and_the_challenge() -> None:
    assert botpool.read_preview(PREVIEW_PENDING) == botpool.Preview(pending=51, challenge=CHALLENGE)


@pytest.mark.parametrize("text", [PREVIEW_ALL_MANAGED, PREVIEW_NONE])
def test_a_preview_with_nothing_to_adopt_is_read_as_zero(text: str) -> None:
    assert botpool.read_preview(text) == botpool.Preview(pending=0, challenge="")


@pytest.mark.parametrize("text", [CONSOLE_ONLY, "", "There is no such command."])
def test_anything_that_is_not_a_preview_is_not_read_as_one(text: str) -> None:
    """A refusal is not "nothing to adopt": reading it as zero would skip the console."""
    assert botpool.read_preview(text) is None


def test_a_preview_line_among_the_bots_own_log_lines_is_still_found() -> None:
    """The attach console hands back what was printed between two prompts, and a
    server with 500 bots prints its own lines into that window too."""
    noisy = "TortoiseBots: auto-create account 61 at character limit (10)\n" + PREVIEW_PENDING
    assert botpool.read_preview(noisy) == botpool.Preview(pending=51, challenge=CHALLENGE)


# ------------------------------------------------------------------ adopt()


def test_the_preview_and_its_confirm_are_sent_back_to_back_and_the_count_is_returned() -> None:
    channel = ScriptedChannel([yes(PREVIEW_PENDING), yes(CONFIRMED)])
    assert botpool.adopt(channel, pause=lambda _s: None) == botpool.Adopted(51)
    assert channel.sent == [botpool.PREVIEW, CONFIRM]


def test_nothing_to_adopt_sends_no_confirm() -> None:
    channel = ScriptedChannel([yes(PREVIEW_ALL_MANAGED)])
    assert botpool.adopt(channel, pause=lambda _s: None) == botpool.Adopted(0)
    assert channel.sent == [botpool.PREVIEW]


def test_a_confirm_refused_because_auto_create_added_an_account_is_asked_again() -> None:
    """Measured: auto-create adds an account every ~15 s, and the module refuses a
    confirm whose set moved since the preview. Nothing was written by that refusal,
    so a fresh preview and its confirm are safe to send."""
    channel = ScriptedChannel(
        [yes(PREVIEW_PENDING), yes(SET_CHANGED), yes(PREVIEW_PENDING), yes(CONFIRMED)]
    )
    assert botpool.adopt(channel, pause=lambda _s: None) == botpool.Adopted(51)
    assert channel.sent == [botpool.PREVIEW, CONFIRM, botpool.PREVIEW, CONFIRM]


def test_the_set_changing_every_time_ends_as_unreached_after_a_bounded_number_of_tries() -> None:
    channel = ScriptedChannel([yes(PREVIEW_PENDING), yes(SET_CHANGED)] * botpool.TRIES)
    outcome = botpool.adopt(channel, pause=lambda _s: None)
    assert isinstance(outcome, botpool.Unreached)
    assert len(channel.sent) == 2 * botpool.TRIES


def test_a_preview_that_timed_out_is_asked_again_after_a_pause() -> None:
    """The world thread is busy for tens of seconds after "up"; a preview is a
    read, so asking it again cannot do anything twice."""
    pauses: list[float] = []
    channel = ScriptedChannel([TIMEOUT, yes(PREVIEW_PENDING), yes(CONFIRMED)])
    assert botpool.adopt(channel, pause=pauses.append) == botpool.Adopted(51)
    assert channel.sent == [botpool.PREVIEW, botpool.PREVIEW, CONFIRM]
    assert pauses == [botpool.PAUSE_S]


def test_a_confirm_with_no_answer_is_never_sent_again() -> None:
    """A write that may have run is not retried (`channel.Answer.indeterminate`)."""
    channel = ScriptedChannel([yes(PREVIEW_PENDING), TIMEOUT])
    outcome = botpool.adopt(channel, pause=lambda _s: None)
    assert isinstance(outcome, botpool.Unconfirmed)
    assert channel.sent == [botpool.PREVIEW, CONFIRM]


def test_a_console_only_refusal_is_unreached_and_not_nothing_to_do() -> None:
    channel = ScriptedChannel([yes(CONSOLE_ONLY)])
    outcome = botpool.adopt(channel, pause=lambda _s: None)
    assert isinstance(outcome, botpool.Unreached)
    assert channel.sent == [botpool.PREVIEW]


def test_a_refused_credential_is_not_asked_again() -> None:
    denied = Answer("unknown", reason="bad password", denied=True)
    channel = ScriptedChannel([denied])
    assert isinstance(botpool.adopt(channel, pause=lambda _s: None), botpool.Unreached)
    assert channel.sent == [botpool.PREVIEW]


# ------------------------------------------------------------------ after_update()

OLD = "a" * 40
NEW = "b" * 40


@dataclass
class Harness:
    """Everything `after_update()` reaches, recorded."""

    heads: list[str | None]
    channels: list[ScriptedChannel]
    restarts: list[str] = field(default_factory=list)
    head_reads: list[Path] = field(default_factory=list)

    def head(self, dest: Path) -> str | None:
        self.head_reads.append(dest)
        return self.heads.pop(0)

    def restart(self) -> None:
        self.restarts.append("restart")

    def run(self, update_lines: tuple[str, ...] = ("updated",)) -> list[str]:
        def update(_cancel: object) -> Iterator[str]:
            yield from update_lines

        return list(
            botpool.after_update(
                update,
                None,
                module_dir=Path("mod"),
                head=self.head,
                channels=lambda: list(self.channels),
                restart=self.restart,
                pause=lambda _s: None,
            )
        )


def test_a_module_that_did_not_move_asks_the_server_nothing() -> None:
    soap = ScriptedChannel([])
    h = Harness(heads=[OLD, OLD], channels=[soap])
    assert h.run() == ["updated"]
    assert soap.sent == [] and h.restarts == []


def test_a_moved_module_is_adopted_and_the_world_restarted_after_the_update() -> None:
    soap = ScriptedChannel([yes(PREVIEW_PENDING), yes(CONFIRMED)])
    h = Harness(heads=[OLD, NEW], channels=[soap])
    lines = h.run()
    assert lines[0] == "updated"
    assert soap.sent == [botpool.PREVIEW, CONFIRM]
    assert h.restarts == ["restart"]
    joined = "\n".join(lines)
    assert "51" in joined and "restart" in joined.lower()


def test_a_moved_module_with_nothing_to_adopt_does_not_restart() -> None:
    soap = ScriptedChannel([yes(PREVIEW_ALL_MANAGED)])
    h = Harness(heads=[OLD, NEW], channels=[soap])
    h.run()
    assert h.restarts == []


def test_a_head_nobody_could_read_counts_as_moved_because_the_preview_is_only_a_read() -> None:
    soap = ScriptedChannel([yes(PREVIEW_ALL_MANAGED)])
    h = Harness(heads=[None, NEW], channels=[soap])
    h.run()
    assert soap.sent == [botpool.PREVIEW]


def test_soap_refusing_the_console_only_command_falls_through_to_the_console() -> None:
    soap = ScriptedChannel([yes(CONSOLE_ONLY)])
    console = ScriptedChannel([yes(PREVIEW_PENDING), yes(CONFIRMED)])
    h = Harness(heads=[OLD, NEW], channels=[soap, console])
    h.run()
    assert soap.sent == [botpool.PREVIEW]
    assert console.sent == [botpool.PREVIEW, CONFIRM]
    assert h.restarts == ["restart"]


def test_no_channel_reaching_the_command_tells_the_player_exactly_what_to_type() -> None:
    soap = ScriptedChannel([yes(CONSOLE_ONLY)])
    h = Harness(heads=[OLD, NEW], channels=[soap])
    lines = h.run()
    assert h.restarts == []
    last = lines[-1]
    assert "bot pool adopt preview" in last
    assert "bot pool adopt confirm" in last
    assert "restart" in last.lower()


def test_no_channel_at_all_tells_the_player_too() -> None:
    h = Harness(heads=[OLD, NEW], channels=[])
    lines = h.run()
    assert "bot pool adopt preview" in lines[-1]


def test_a_restart_that_fails_is_reported_and_does_not_fail_the_update() -> None:
    """The update itself succeeded; what is left is one restart the player can press."""
    soap = ScriptedChannel([yes(PREVIEW_PENDING), yes(CONFIRMED)])
    h = Harness(heads=[OLD, NEW], channels=[soap])

    def boom() -> None:
        raise RuntimeError("docker said no")

    h.restart = boom  # type: ignore[method-assign]
    lines = h.run()
    assert "docker said no" in lines[-1]
    assert "restart" in lines[-1].lower()


def test_an_update_that_failed_adopts_nothing() -> None:
    soap = ScriptedChannel([])
    h = Harness(heads=[OLD, NEW], channels=[soap])

    def update(_cancel: object) -> Iterator[str]:
        yield "fetching"
        raise RuntimeError("the build failed")

    with pytest.raises(RuntimeError):
        list(
            botpool.after_update(
                update,
                None,
                module_dir=Path("mod"),
                head=h.head,
                channels=lambda: [soap],
                restart=h.restart,
                pause=lambda _s: None,
            )
        )
    assert soap.sent == [] and h.restarts == []
    assert len(h.head_reads) == 1


# ------------------------------------------------------------------ the wiring


def test_the_module_folder_is_the_catalogs_tortoisebots_source() -> None:
    dest = botpool.module_dir(ENTRY, Path("/srv"))
    assert dest is not None
    assert dest.name == "TortoiseBots"
    assert dest.is_relative_to(Path("/srv"))


@pytest.mark.parametrize("press_name", ["press", "to_pin"])
def test_the_tortoise_tab_update_press_runs_the_adoption_after_the_update(
    press_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring, not the function: the route the Server tab holds must be the
    wrapped one, for the way forward AND the way back (both move the module).

    Driven through `ControllerServices.for_entry()` with the engine, the git head
    reader, the console and the controller replaced at their module seams, so the
    press runs the real factory's own composition. No credential exists in the
    test's config dir, so SOAP is not offered and the console is what answers.
    """
    from yulon import install_wiring
    from yulon.controller_wow_tortoise import console as tortoise_console
    from yulon.controller_wow_tortoise import controller as tortoise_controller
    from yulon.controller_wow_wotlk.console import ConsoleReply
    from yulon.ui.controller_view import ControllerServices

    class Engine:
        def update_to_latest(self, _options: object, **_kw: object) -> Iterator[str]:
            yield "engine: updated"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: Engine())
    heads = iter([OLD, NEW])
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: next(heads))
    typed: list[str] = []
    answers = iter([PREVIEW_PENDING, CONFIRMED])

    def fake_send(command: str, **_kw: object) -> ConsoleReply:
        typed.append(command)
        return ConsoleReply(command=command, lines=tuple(next(answers).splitlines()))

    monkeypatch.setattr(tortoise_console, "send", fake_send)
    lifecycle: list[str] = []
    monkeypatch.setattr(
        tortoise_controller.TortoiseController,
        "stop",
        lambda _self: lifecycle.append("stop") or True,
    )
    monkeypatch.setattr(
        tortoise_controller.TortoiseController, "start", lambda _self: lifecycle.append("start")
    )

    services = ControllerServices.for_entry(ENTRY, tmp_path)
    route = services.update_to_latest
    assert route is not None
    lines = list(getattr(route, press_name)(None))

    assert lines[0] == "engine: updated"
    assert typed == [botpool.PREVIEW, CONFIRM]
    assert lifecycle == ["stop", "start"]


# ------------------------------------------------------------------ review round 1


def test_a_soap_confirm_with_no_answer_is_not_followed_by_the_console_and_still_restarts() -> None:
    """Cold review, Important. SOAP queues the command on the world thread, so a
    confirm whose answer timed out has usually still run. A console preview after
    it then says "already managed", which read as nothing to do: no restart, bots
    offline. Once a confirm went out unanswered no channel is asked again, and the
    world is restarted because the enrolment may have happened."""
    soap = ScriptedChannel([yes(PREVIEW_PENDING), TIMEOUT])
    console = ScriptedChannel([yes(PREVIEW_ALL_MANAGED)])
    h = Harness(heads=[OLD, NEW], channels=[soap, console])
    lines = h.run()
    assert soap.sent == [botpool.PREVIEW, CONFIRM]
    assert console.sent == []
    assert h.restarts == ["restart"]
    assert "may" in "\n".join(lines[1:]).lower()


def test_a_capture_holding_a_pending_preview_and_a_no_op_line_is_unreadable_not_zero() -> None:
    """Codex, medium: interleaved console output can carry both. Zero would skip
    the adoption, so the mixed capture is refused as unreadable."""
    mixed = PREVIEW_PENDING + "\nEvery matching account is already managed."
    assert botpool.read_preview(mixed) is None
    assert botpool.read_preview("Nothing to adopt.\n" + PREVIEW_PENDING) is None


def test_a_cancel_before_the_adoption_sends_nothing_and_prints_the_console_steps() -> None:
    import threading

    cancel = threading.Event()
    soap = ScriptedChannel([])
    h = Harness(heads=[OLD, NEW], channels=[soap])

    def update(_c: object) -> Iterator[str]:
        yield "updated"
        cancel.set()

    lines = list(
        botpool.after_update(
            update,
            cancel,
            module_dir=Path("mod"),
            head=h.head,
            channels=lambda: [soap],
            restart=h.restart,
            pause=lambda _s: None,
        )
    )
    assert soap.sent == [] and h.restarts == []
    assert "bot pool adopt preview" in lines[-1]


def test_a_cancel_after_the_confirm_does_not_start_the_restart() -> None:
    import threading

    cancel = threading.Event()

    class CancelOnConfirm(ScriptedChannel):
        def send(self, command: str) -> Answer:
            answer = super().send(command)
            if command.startswith("bot pool adopt confirm"):
                cancel.set()
            return answer

    soap = CancelOnConfirm([yes(PREVIEW_PENDING), yes(CONFIRMED)])
    h = Harness(heads=[OLD, NEW], channels=[soap])
    lines = list(
        botpool.after_update(
            lambda _c: iter(["updated"]),
            cancel,
            module_dir=Path("mod"),
            head=h.head,
            channels=lambda: [soap],
            restart=h.restart,
            pause=lambda _s: None,
        )
    )
    assert h.restarts == []
    assert "restart" in lines[-1].lower()


def test_a_restart_that_fails_says_the_next_start_loads_the_bots() -> None:
    """Codex, high (reduced): the registry rows are written before the restart, so a
    restart that never happens -- a failure, or Yu'lon dying -- is repaired by any
    later start of the world. The line says so rather than implying lost work."""
    soap = ScriptedChannel([yes(PREVIEW_PENDING), yes(CONFIRMED)])
    h = Harness(heads=[OLD, NEW], channels=[soap])

    def boom() -> None:
        raise RuntimeError("docker said no")

    h.restart = boom  # type: ignore[method-assign]
    assert "next start" in h.run()[-1].lower()
