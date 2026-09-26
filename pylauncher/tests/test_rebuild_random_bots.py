"""T144: "Rebuild random bots…" on the Tortoise Bots tab, and the offer after a TortoiseBots update.

TortoiseBots' developer (Discord, 2026-09-26): most of the module's changes to
random bots -- seeding, gear, professions, skills -- only reach bots created
afterwards, and the module's own way to get them is to rebuild the pool. That is
`AiPlayerbot.RandomBotPoolReset = once:<token>` in `aiplayerbot.conf`
(`ai/playerbot/PlayerbotAIConfig.cpp:619` at f858f9c9, read through the bot
config the module opens beside `mangosd.conf`), acted on at the next world start
and only when the token differs from the last generation it completed
(`runtime/PoolResetPolicy.h`). The log lines matched below are the module's own
format strings from `runtime/RandomBotPoolReset.cpp` and
`runtime/RandomBotService.cpp` at f858f9c9 (the reset file is byte-identical at
632e1b63), filled in the way `sLog` fills them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.conftest import pump_until
from yulon import docker, tuning
from yulon.catalog.catalog import load_catalog
from yulon.channel import Answer
from yulon.controller_wow_tortoise import botpool, poolreset
from yulon.ui import controller_view as controller_view_module
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
TORTOISE = CATALOG.get("wow-tortoise")
CONF = "etc/aiplayerbot.conf"

T0 = datetime(2026, 9, 26, 10, 15, 0, tzinfo=UTC)
TOKEN = "yulon-20260926T101500Z"

CONF_TEXT = (
    "[worldserver]\r\n"
    "AiPlayerbot.Enabled = 1\r\n"
    "# Managed random-bot pool reset.\r\n"
    "AiPlayerbot.RandomBotPoolReset = off\r\n"
    "\r\n"
    "AiPlayerbot.RandomBotAutoCreate = 1\r\n"
    "AiPlayerbot.MinRandomBots = 500\r\n"
    "AiPlayerbot.MaxRandomBots = 500\r\n"
)


def _conf(server_dir: Path, text: str = CONF_TEXT) -> Path:
    path = server_dir / CONF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


# -- the module's own lines, as sLog prints them --------------------------------------------


def scheduled(token: str = TOKEN, characters: int = 500, accounts: int = 50) -> str:
    return (
        f"TortoiseBots: random pool generation '{token}'; reset scheduled for {characters} "
        f"characters on {accounts} managed accounts"
    )


def progress(done: int, total: int = 500) -> str:
    return f"TortoiseBots: random pool reset progress: {done}/{total} characters deleted"


def verified(accounts: int = 50) -> str:
    return (
        "TortoiseBots: random pool reset verified: 0 characters remain on "
        f"{accounts} managed accounts"
    )


def applied(token: str = TOKEN) -> str:
    return f"TortoiseBots: random pool generation '{token}' applied; pool rebuild starts now"


GUILD_REASON = (
    "guild 'Bot Friends' is led by pool character 812 but holds a member outside the managed "
    "pool (character 9001 'Perzi'); reset aborted"
)
SESSION_REASON = (
    "pool character Aldren (812) is being played by a network session; this feature never logs "
    "out or deletes human sessions"
)

REFUSALS = {
    "guild": f"TortoiseBots: random pool reset aborted before any deletion: {GUILD_REASON}",
    "session": f"TortoiseBots: random pool reset aborted before any deletion: {SESSION_REASON}",
    "failed": (
        "TortoiseBots: random pool reset failed: character Aldren (812) stayed busy for "
        "120 seconds"
    ),
    "skipped": "TortoiseBots: random pool reset skipped: pool state query failed",
    "disabled": (
        "TortoiseBots: random pool reset disabled for this start: "
        "the managed-account registry is not validated"
    ),
    "summary": "TortoiseBots: random pool summary unavailable: character count query failed",
    "invalid": (
        "TortoiseBots: AiPlayerbot.RandomBotPoolReset is invalid (expected 'off', 'always' or "
        "'once:<token>'); no reset was scheduled"
    ),
    "autocreate": (
        f"TortoiseBots: random pool generation '{TOKEN}' needs AiPlayerbot.RandomBotAutoCreate=1 "
        "to refill the pool; no reset was scheduled (set it and restart)"
    ),
    "service": (
        "TortoiseBots: AiPlayerbot.RandomBotPoolReset requests a pool rebuild, but the random-bot "
        "service is disabled (RandomBotAutologin=0 and RandomBotAutoCreate=0); no reset was "
        "scheduled"
    ),
}


OFF_LINE = "TortoiseBots: random pool reset off; 50 managed accounts, 500 characters"
SESSION_MID = (
    "TortoiseBots: random pool reset failed: pool character Aldren (812) gained a network "
    "session during the reset; human sessions are never deleted"
)
NOT_PART_WAY = sorted(set(REFUSALS) - {"failed"})
"""Every refusal the module logs before a deletion: each one sets the key back to off."""


def stamped(*lines: str) -> str:
    """What `docker logs` hands back: the core's own time prefix in front of each line."""
    return "".join(f"2026-09-26 10:16:{i:02d} {line}\n" for i, line in enumerate(lines))


# ------------------------------------------------------------------ the token


def test_the_token_is_yulon_and_the_utc_second() -> None:
    assert poolreset.new_token(T0) == TOKEN
    oslo = T0.astimezone(timezone(timedelta(hours=2)))
    assert poolreset.new_token(oslo) == TOKEN, "the stamp is UTC whatever zone the clock is in"


@pytest.mark.parametrize(
    ("token", "valid"),
    [
        (TOKEN, True),
        ("x", True),
        ("a" * 128, True),
        ("a" * 129, False),
        ("", False),
        ("has space", False),
        ("tab\there", False),
        ("café", False),
        ("del\x7f", False),
    ],
)
def test_the_token_grammar_is_the_modules(token: str, valid: bool) -> None:
    """`IsValidPoolResetToken`: 1-128 characters, each printable non-space ASCII (0x21-0x7e)."""
    assert poolreset.is_valid_token(token) is valid


# ------------------------------------------------------------------ the write


def test_the_key_replaces_the_active_line_in_place_and_keeps_every_other_byte(
    tmp_path: Path,
) -> None:
    path = _conf(tmp_path)
    written = poolreset.write_key(TORTOISE, tmp_path, TOKEN)
    after = path.read_bytes()
    assert after == CONF_TEXT.replace(
        "AiPlayerbot.RandomBotPoolReset = off", f"AiPlayerbot.RandomBotPoolReset = once:{TOKEN}"
    ).encode("utf-8")
    assert written.backup.read_bytes() == CONF_TEXT.encode("utf-8")
    assert tuning.backups_of(path) == (written.backup,), "the Tuning card's Revert finds it"


def test_a_conf_without_the_key_gets_it_appended(tmp_path: Path) -> None:
    text = "AiPlayerbot.Enabled = 1\nAiPlayerbot.MaxRandomBots = 500"
    path = _conf(tmp_path, text)
    poolreset.write_key(TORTOISE, tmp_path, TOKEN)
    assert path.read_text() == text + f"\nAiPlayerbot.RandomBotPoolReset = once:{TOKEN}\n"


def test_a_token_outside_the_grammar_is_never_written(tmp_path: Path) -> None:
    path = _conf(tmp_path)
    with pytest.raises(poolreset.PoolResetError):
        poolreset.write_key(TORTOISE, tmp_path, "bad token")
    assert path.read_bytes() == CONF_TEXT.encode("utf-8")
    assert tuning.backups_of(path) == ()


def test_a_missing_conf_is_a_refusal_not_a_new_file(tmp_path: Path) -> None:
    with pytest.raises(poolreset.PoolResetError):
        poolreset.write_key(TORTOISE, tmp_path, TOKEN)
    assert not (tmp_path / CONF).exists()


# ------------------------------------------------------------------ reading the log


def test_the_modules_lines_read_as_progress_then_done() -> None:
    going = poolreset.read_log(stamped(scheduled(), progress(25), progress(500)), TOKEN)
    assert going.final is None
    assert len(going.seen) == 3
    assert "500" in going.seen[0] and "50" in going.seen[0]
    assert "25/500" in going.seen[1]

    done = poolreset.read_log(stamped(scheduled(), progress(500), verified(), applied()), TOKEN)
    assert done.final == "applied"
    assert "0 left" in done.seen[-1] or "checked" in done.seen[-1]
    assert "made again" in done.said


def test_another_tokens_lines_are_not_this_rebuild() -> None:
    other = "yulon-20250101T000000Z"
    watch = poolreset.read_log(stamped(scheduled(other), applied(other)), TOKEN)
    assert watch.final is None and watch.seen == ()


@pytest.mark.parametrize("why", NOT_PART_WAY)
def test_every_refusal_the_module_logs_ends_the_watch_in_plain_words(why: str) -> None:
    watch = poolreset.read_log(stamped(REFUSALS[why]), TOKEN)
    assert watch.final == "refused", why
    assert "TortoiseBots:" not in watch.said, "said in plain words, not the raw line"


def test_a_failure_after_the_reset_started_is_part_way_not_a_refusal() -> None:
    watch = poolreset.read_log(stamped(scheduled(), progress(25), REFUSALS["failed"]), TOKEN)
    assert watch.final == "part-way"
    assert "25 of 500" in watch.said
    assert "Nothing was deleted" not in watch.said


@pytest.mark.parametrize("before", [(), (progress(25),)], ids=["no-progress", "progress"])
def test_a_session_that_logged_in_mid_reset_is_part_way_never_nothing_deleted(
    before: tuple[str, ...],
) -> None:
    """RandomBotPoolReset.cpp:445 and :609 log it as `reset failed: ... gained a network
    session during the reset`, and by then the module may have deleted characters."""
    watch = poolreset.read_log(stamped(scheduled(), *before, SESSION_MID), TOKEN)
    assert watch.final == "part-way"
    assert "Nothing was deleted" not in watch.said
    assert "Aldren" in watch.said
    assert "next start" in watch.said
    if before:
        assert "25 of 500" in watch.said


def test_a_log_not_scoped_to_this_run_ends_only_on_this_tokens_own_lines() -> None:
    """No start time -> the whole history: an older run's token-less lines prove nothing."""
    old = stamped(REFUSALS["guild"], progress(3), OFF_LINE, REFUSALS["failed"])
    watch = poolreset.read_log(old, TOKEN, this_run_only=False)
    assert watch.final is None and watch.seen == ()
    done = poolreset.read_log(old + stamped(scheduled(), applied()), TOKEN, this_run_only=False)
    assert done.final == "applied"
    refused = poolreset.read_log(old + stamped(REFUSALS["autocreate"]), TOKEN, this_run_only=False)
    assert refused.final == "refused", "the AutoCreate refusal names this token"


def test_the_two_refusals_the_dialog_names_say_who() -> None:
    guild = poolreset.read_log(stamped(REFUSALS["guild"]), TOKEN).said
    assert "Bot Friends" in guild and "Perzi" in guild and "Nothing was deleted" in guild
    session = poolreset.read_log(stamped(REFUSALS["session"]), TOKEN).said
    assert "Aldren" in session and "Nothing was deleted" in session


def test_a_reset_that_reads_off_says_the_server_did_not_see_the_request() -> None:
    watch = poolreset.read_log(stamped(OFF_LINE), TOKEN)
    assert watch.final == "not-read"


def test_our_token_already_applied_counts_as_done() -> None:
    line = f"TortoiseBots: random pool generation '{TOKEN}' already applied; reset skipped"
    assert poolreset.read_log(stamped(line), TOKEN).final == "applied"


# ------------------------------------------------------------------ the flow


PREVIEW_PENDING = "\n".join(
    (
        "Total: 55 account(s), 540 character(s); 51 account(s) still need adoption.",
        "To enroll them, run exactly: bot pool adopt confirm 72C1FF2923F1 (valid for 5 minutes)",
    )
)
CONFIRMED = "Adoption complete: 51 account(s) registered, 10 already managed."
CONSOLE_ONLY = "This command is only available at the server console."


@dataclass
class World:
    """Everything the flow reaches, recording one ordered list of what happened."""

    tmp: Path
    running: bool | None = True
    answers: list[str] = field(default_factory=lambda: [PREVIEW_PENDING, CONFIRMED])
    log_after_restart: list[str] = field(default_factory=lambda: [applied()])
    events: list[str] = field(default_factory=list)
    restarts: int = 0
    now: datetime = T0
    backup_fails: bool = False
    scoped: bool = True
    restart_fails: bool = False

    def channel(self) -> object:
        world = self

        class _Channel:
            def send(self, command: str) -> Answer:
                world.events.append(command.split()[3] if command.startswith("bot pool") else "?")
                return Answer("yes", world.answers.pop(0))

        return _Channel()

    def channels(self) -> Sequence[object]:
        return [self.channel()]

    def key(self) -> str:
        conf = (self.tmp / CONF).read_text()
        return conf.split("RandomBotPoolReset = ")[1].split()[0]

    def restart(self) -> None:
        if self.restart_fails:
            raise RuntimeError("docker said no")
        self.events.append("restart:" + self.key())
        self.restarts += 1

    def world_log(self) -> docker.RunLog:
        text = stamped(*self.log_after_restart) if self.restarts else "old run\n"
        return docker.RunLog(text, this_run_only=self.scoped)

    def clock(self) -> datetime:
        return self.now

    def backup(self) -> object:
        self.events.append("backup")
        if self.backup_fails:
            raise RuntimeError("mysqldump said no")
        from yulon.controller_wow_wotlk.maintenance import BackupReport

        return BackupReport(directory=Path("backups"), dumps=())

    def rebuild(self, **kw: object) -> poolreset.PoolRebuild:
        return poolreset.PoolRebuild(
            entry=TORTOISE,
            server_dir=self.tmp,
            world_running=lambda: self.running,
            channels=self.channels,  # type: ignore[arg-type]
            restart=self.restart,
            world_log=self.world_log,
            clock=self.clock,
            pause=lambda _s, _c=None: None,
            **kw,  # type: ignore[arg-type]
        )

    def run(self, **kw: object) -> list[str]:
        backup = kw.pop("backup", None)
        cancel = kw.pop("cancel", None)
        owed = bool(kw.pop("restart_owed", False))
        job = self.rebuild(**kw)
        return list(
            job.rebuild(backup=backup, cancel=cancel, restart_owed=owed)  # type: ignore[arg-type]
        )


def test_adopt_then_write_then_one_restart_then_the_log_says_done(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path)
    lines = world.run()
    assert world.events == ["preview", "confirm", f"restart:once:{TOKEN}"]
    assert world.restarts == 1
    assert "made again" in lines[-1]


def test_back_up_first_backs_up_before_anything_else(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path)
    world.run(backup=world.backup)
    assert world.events[0] == "backup"
    assert world.events[1:] == ["preview", "confirm", f"restart:once:{TOKEN}"]


def test_a_failed_backup_stops_before_anything_is_asked_written_or_restarted(
    tmp_path: Path,
) -> None:
    path = _conf(tmp_path)
    world = World(tmp_path, backup_fails=True)
    with pytest.raises(poolreset.PoolResetError, match="mysqldump said no"):
        world.run(backup=world.backup)
    assert world.events == ["backup"]
    assert path.read_bytes() == CONF_TEXT.encode("utf-8")
    assert tuning.backups_of(path) == ()


def test_a_stopped_server_is_not_asked_to_enrol_and_is_started_once(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path, running=False)
    lines = world.run()
    assert world.events == [f"restart:once:{TOKEN}"]
    assert any("not running" in line for line in lines)


def test_an_enrolment_nobody_could_run_is_said_and_the_rebuild_still_runs(tmp_path: Path) -> None:
    """Adoption only registers accounts; without it the reset touches fewer, never more."""
    _conf(tmp_path)
    world = World(tmp_path, answers=[CONSOLE_ONLY])
    lines = world.run()
    assert world.events == ["preview", f"restart:once:{TOKEN}"]
    assert any("bot pool adopt preview" in line for line in lines)


def test_two_presses_write_two_different_tokens(tmp_path: Path) -> None:
    path = _conf(tmp_path)
    world = World(tmp_path, answers=[PREVIEW_PENDING, CONFIRMED] * 2)
    world.run()
    world.now = T0 + timedelta(minutes=7)
    second = poolreset.new_token(world.now)
    world.log_after_restart = [applied(second)]
    world.run()
    assert second != TOKEN
    assert f"RandomBotPoolReset = once:{second}" in path.read_text()
    assert world.events.count(f"restart:once:{TOKEN}") == 1
    assert world.events.count(f"restart:once:{second}") == 1
    assert len(tuning.backups_of(path)) == 2


TAKEN_BACK = "nothing will happen at the next restart"


@pytest.mark.parametrize("why", [*NOT_PART_WAY, "off"])
def test_every_refusal_sets_the_key_back_to_off_so_no_later_restart_rebuilds(
    tmp_path: Path, why: str
) -> None:
    """Owner, 2026-09-26: a later ordinary restart must never rebuild the bots by surprise."""
    path = _conf(tmp_path)
    world = World(tmp_path, log_after_restart=[OFF_LINE if why == "off" else REFUSALS[why]])
    with pytest.raises(poolreset.PoolResetError) as caught:
        world.run()
    assert "TortoiseBots:" not in str(caught.value)
    assert TAKEN_BACK in str(caught.value) and "Rebuild random bots" in str(caught.value)
    assert world.restarts == 1
    assert world.key() == "off"
    assert path.read_bytes() == CONF_TEXT.encode("utf-8"), "only the value moved, and back"
    assert len(tuning.backups_of(path)) == 2, "the take-back is backed up like the write"


def test_a_part_way_failure_keeps_the_request_so_the_module_finishes_it(tmp_path: Path) -> None:
    """Off would stop the resume (`PlanAtStartup` returns on Off) and strand half a pool."""
    _conf(tmp_path)
    world = World(tmp_path, log_after_restart=[scheduled(), progress(25), REFUSALS["failed"]])
    with pytest.raises(poolreset.PoolResetError) as caught:
        world.run()
    assert world.key() == f"once:{TOKEN}"
    assert "next start" in str(caught.value) and TAKEN_BACK not in str(caught.value)


def test_a_restart_that_fails_takes_the_request_back(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path, restart_fails=True)
    with pytest.raises(poolreset.PoolResetError) as caught:
        world.run()
    assert "docker said no" in str(caught.value) and TAKEN_BACK in str(caught.value)
    assert world.key() == "off"


def test_a_watch_that_runs_out_says_where_to_look_and_is_not_a_failure(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path, log_after_restart=[scheduled(), progress(25)])
    ticks = iter(range(0, 100_000, 10))
    lines = world.run(monotonic=lambda: float(next(ticks)), timeout_s=60.0)
    assert any("25/500" in line for line in lines)
    assert "Console tab" in lines[-1] and "not seen" in lines[-1].lower()
    assert "may still be running" in lines[-1]
    assert sum("25/500" in line for line in lines) == 1, "a progress line is said once"
    assert world.key() == f"once:{TOKEN}", "a rebuild that may be running is not taken back"


def test_a_cancel_before_the_write_writes_and_restarts_nothing(tmp_path: Path) -> None:
    path = _conf(tmp_path)
    cancel = threading.Event()
    world = World(tmp_path)

    class CancelOnConfirm:
        def send(self, command: str) -> Answer:
            world.events.append(command.split()[3])
            if command.startswith("bot pool adopt confirm"):
                cancel.set()
            return Answer("yes", world.answers.pop(0))

    world.channels = lambda: [CancelOnConfirm()]  # type: ignore[method-assign]
    world.run(cancel=cancel)
    assert world.restarts == 0
    assert path.read_bytes() == CONF_TEXT.encode("utf-8")


def test_a_cancel_after_the_write_takes_the_request_back_and_does_not_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conf(tmp_path)
    cancel = threading.Event()
    world = World(tmp_path)
    real = poolreset.write_key

    def write_then_cancel(*args: object) -> poolreset.KeyWritten:
        written = real(*args)  # type: ignore[arg-type]
        cancel.set()
        return written

    monkeypatch.setattr(poolreset, "write_key", write_then_cancel)
    lines = world.run(cancel=cancel)
    assert world.restarts == 0
    assert world.key() == "off"
    assert TAKEN_BACK in lines[-1]


def test_an_owed_restart_still_runs_when_the_backup_fails(tmp_path: Path) -> None:
    """After an update T123 left the enrolment's restart to this job; a failure keeps it."""
    _conf(tmp_path)
    world = World(tmp_path, backup_fails=True)
    with pytest.raises(poolreset.PoolResetError):
        world.run(backup=world.backup, restart_owed=True)
    assert world.events == ["backup", "restart:off"]


def test_an_owed_restart_is_the_rebuilds_own_one_restart(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path)
    world.run(restart_owed=True)
    assert world.events == ["preview", "confirm", f"restart:once:{TOKEN}"]


def test_the_owed_restart_on_its_own_is_one_restart(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path)
    lines = list(world.rebuild().restart_owed_now())
    assert world.events == ["restart:off"]
    assert lines


def test_an_unscoped_log_with_an_old_refusal_does_not_end_the_watch(tmp_path: Path) -> None:
    _conf(tmp_path)
    world = World(tmp_path, scoped=False, log_after_restart=[REFUSALS["guild"], applied()])
    lines = world.run()
    assert "made again" in lines[-1]
    assert world.key() == f"once:{TOKEN}"


# ------------------------------------------------------------------ this run's log


def test_the_run_log_is_scoped_by_the_containers_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    argvs: list[list[str]] = []

    def fake(argv: list[str], *a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "line\n", "")

    monkeypatch.setattr(docker, "_docker", fake)
    monkeypatch.setattr(docker, "started_at", lambda _c, **_kw: "2026-09-26T10:00:00.5Z")
    log = docker.current_run_log("tortoise-world", wsl_distro="d")
    assert log == docker.RunLog("line\n", this_run_only=True)
    assert argvs[-1] == ["logs", "--since", "2026-09-26T10:00:00.5Z", "tortoise-world"]

    monkeypatch.setattr(docker, "started_at", lambda _c, **_kw: "")
    log = docker.current_run_log("tortoise-world", wsl_distro="d")
    assert log.this_run_only is False
    assert argvs[-1] == ["logs", "tortoise-world"]


# ------------------------------------------------------------------ the update's flag


OLD = "a" * 40
NEW = "b" * 40


PREVIEW_ALL_MANAGED = "Every matching account is already managed."


def _after(
    heads: list[str | None],
    moved: botpool.ModuleMoved,
    fail: bool = False,
    answers: tuple[str, ...] = (),
) -> list[str]:
    """T123's `after_update` with the flag; returns what it restarted."""
    restarts: list[str] = []
    replies = list(answers)

    class _Channel:
        def send(self, command: str) -> Answer:
            return Answer("yes", replies.pop(0))

    def update(_cancel: object) -> Iterator[str]:
        yield "updated"
        if fail:
            raise RuntimeError("the build failed")

    run = botpool.after_update(
        update,
        None,
        module_dir=Path("mod"),
        head=lambda _d: heads.pop(0),
        channels=lambda: [_Channel()] if answers else [],
        restart=lambda: restarts.append("restart"),
        pause=lambda _s: None,
        moved=moved,
    )
    if fail:
        with pytest.raises(RuntimeError):
            list(run)
    else:
        list(run)
    return restarts


def test_an_update_that_moved_the_module_leaves_the_flag_for_one_reader() -> None:
    moved = botpool.ModuleMoved()
    _after([OLD, NEW], moved)
    assert moved.take() == botpool.Move(restart_owed=False)
    assert moved.take() is None, "taken once"


def test_an_enrolment_after_a_move_owes_the_restart_instead_of_making_it() -> None:
    """Approved design point 3: the view asks first, so the update path restarts once."""
    moved = botpool.ModuleMoved()
    restarts = _after([OLD, NEW], moved, answers=(PREVIEW_PENDING, CONFIRMED))
    assert restarts == []
    assert moved.take() == botpool.Move(restart_owed=True)


def test_nothing_enrolled_owes_no_restart() -> None:
    moved = botpool.ModuleMoved()
    restarts = _after([OLD, NEW], moved, answers=(PREVIEW_ALL_MANAGED,))
    assert restarts == [] and moved.take() == botpool.Move(restart_owed=False)


def test_an_update_that_did_not_move_the_module_or_failed_leaves_no_flag() -> None:
    moved = botpool.ModuleMoved()
    _after([OLD, OLD], moved)
    assert moved.take() is None
    _after([OLD, NEW], moved, fail=True)
    assert moved.take() is None


# ------------------------------------------------------------------ the wiring


def test_only_tortoise_is_wired_and_its_update_route_carries_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import install_wiring
    from yulon.controller_wow_tortoise import console as tortoise_console
    from yulon.controller_wow_wotlk.console import ConsoleReply
    from yulon.ui.controller_view import ControllerServices

    class Engine:
        def update_to_latest(self, _options: object, **_kw: object) -> Iterator[str]:
            yield "engine: updated"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: Engine())
    heads = iter([OLD, NEW])
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: next(heads))
    monkeypatch.setattr(
        tortoise_console,
        "send",
        lambda command, **_kw: ConsoleReply(command=command, lines=(CONSOLE_ONLY,)),
    )
    services = ControllerServices.for_entry(TORTOISE, tmp_path)
    seam = services.bot_pool_rebuild
    assert isinstance(seam, poolreset.PoolRebuild)
    route = services.update_to_latest
    assert route is not None
    assert seam.take_module_moved() is None
    list(route.press(None))
    assert seam.take_module_moved() == botpool.Move(restart_owed=False)

    for other in ("wow-wotlk", "wow-tbc", "wow-vanilla"):
        other_services = ControllerServices.for_entry(CATALOG.get(other), tmp_path)
        assert other_services.bot_pool_rebuild is None, other


# ------------------------------------------------------------------ the tab


class _Seam:
    """The Bots tab's seam, recording what the view asked of it."""

    def __init__(self, release: threading.Event | None = None) -> None:
        self.calls: list[str] = []
        self.backups: list[object] = []
        self.threads: list[threading.Thread] = []
        self.release = release

    def take_module_moved(self) -> botpool.Move | None:
        return None

    def rebuild(
        self,
        *,
        backup: Callable[[], object] | None = None,
        cancel: threading.Event | None = None,
        restart_owed: bool = False,
    ) -> Iterator[str]:
        self.calls.append("rebuild")
        self.threads.append(threading.current_thread())
        self.backups.append(backup)
        if self.release is not None:
            self.release.wait(10)
        if backup is not None:
            backup()
        yield "rebuilt"

    def restart_owed_now(self, cancel: threading.Event | None = None) -> Iterator[str]:
        self.calls.append("owed-restart")
        yield "restarted"


class _NoBots:
    def page(self, *, after: object = None, name_like: str = "") -> object:
        return None


def _view(
    tmp_path: Path, seam: object | None, entry: object = TORTOISE, **extra: object
) -> controller_view_module.ControllerView:
    from tests.test_controller_view import _Ps, _services

    services = replace(
        _services(_Ps(), tmp_path, []), bots=_NoBots(), bot_pool_rebuild=seam, **extra
    )
    return controller_view_module.ControllerView(
        entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
    )


def _answer(monkeypatch: pytest.MonkeyPatch, which: object) -> list[QMessageBox]:
    boxes: list[QMessageBox] = []

    def exec_(self: QMessageBox) -> object:
        boxes.append(self)
        return which

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return boxes


def _answers(monkeypatch: pytest.MonkeyPatch, *which: object) -> None:
    """Answer the three-way dialogs in order: the update's, then the rebuild's."""
    queue = list(which)
    monkeypatch.setattr(QMessageBox, "exec", lambda _self: queue.pop(0))


def _wait(view: controller_view_module.ControllerView) -> None:
    log = view.bot_rebuild_log
    assert log is not None
    pump_until(lambda: not log.running and not view._busy, "the rebuild job finished")


def test_the_button_is_on_the_tortoise_bots_tab_only(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path, _Seam())
    assert view.bot_rebuild_button is not None
    assert view.bot_rebuild_button.text() == "Rebuild random bots…"
    assert not view.bot_rebuild_button.isHidden()
    assert view.bot_rebuild_log in view.log_panels()
    bare = _view(tmp_path, None, entry=CATALOG.get("wow-tbc"))
    assert bare.bot_rebuild_button is None
    assert bare.rebuild_random_bots() is False


@pytest.mark.parametrize("answer", ["Cancel", "NoButton", "No"])
def test_cancel_escape_and_the_close_button_do_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    _answer(monkeypatch, getattr(QMessageBox.StandardButton, answer))
    seam = _Seam()
    view = _view(tmp_path, seam)
    assert view.rebuild_random_bots() is False
    assert seam.calls == []


def test_the_dialog_says_what_is_lost_and_what_stays_and_defaults_to_cancel(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boxes = _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    view = _view(tmp_path, _Seam())
    view.bot_rebuild_button.click()  # type: ignore[union-attr]
    box = boxes[0]
    qmb = QMessageBox.StandardButton
    assert box.button(qmb.Yes).text() == "Back up first, then rebuild"
    assert box.button(qmb.Save).text() == "Rebuild without a backup"
    assert box.button(qmb.Cancel).text() == "Cancel"
    assert box.button(qmb.No) is None
    assert box.defaultButton() is box.button(qmb.Cancel)
    assert box.escapeButton() is box.button(qmb.Cancel)
    text = box.text()
    for said in (
        "level",
        "gear",
        "bags",
        "bank",
        "quests",
        "professions",
        "hired",
        "pinned",
        "guilds",
        "auctions",
        "refunded",
        "accounts",
        "never touched",
        "restarts (or starts, if it is stopped)",
        "disconnected",
        "refuses",
    ):
        assert said in text, said


@pytest.mark.parametrize(("answer", "backs_up"), [("Yes", True), ("Save", False)])
def test_the_two_rebuild_answers_run_the_job_with_and_without_the_backup(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, backs_up: bool
) -> None:
    _answer(monkeypatch, getattr(QMessageBox.StandardButton, answer))
    seam = _Seam()
    view = _view(tmp_path, seam)
    assert view.rebuild_random_bots() is True
    _wait(view)
    assert seam.calls == ["rebuild"]
    assert (seam.backups[0] is not None) is backs_up
    assert seam.threads[0] is not threading.main_thread(), "the job runs off the GUI thread"
    assert "rebuilt" in view.bot_rebuild_log.text()  # type: ignore[union-attr]


def test_the_real_flow_through_the_button_writes_the_key_and_restarts_once(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    path = _conf(tmp_path)
    world = World(tmp_path)
    view = _view(tmp_path, world.rebuild())
    view.rebuild_random_bots()
    _wait(view)
    assert world.events == ["preview", "confirm", f"restart:once:{TOKEN}"]
    assert f"once:{TOKEN}" in path.read_text()
    assert "made again" in view.bot_rebuild_log.text()  # type: ignore[union-attr]


def test_a_refusal_is_put_where_the_player_is_looking(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _conf(tmp_path)
    world = World(tmp_path, log_after_restart=[REFUSALS["guild"]])
    view = _view(tmp_path, world.rebuild())
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.rebuild_random_bots()
    _wait(view)
    assert "Bot Friends" in view.bot_rebuild_report.text()  # type: ignore[union-attr]
    assert failures and "Bot Friends" in failures[0]


@pytest.mark.parametrize("flag", ["_restore_running", "_bot_count_writing", "_reset_running"])
def test_the_press_is_refused_while_another_job_writes_this_server(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """The view's one "is anything running here" answer (`forget_refusal`), not a copy of it."""
    boxes = _answer(monkeypatch, QMessageBox.StandardButton.Save)
    seam = _Seam()
    view = _view(tmp_path, seam)
    setattr(view, flag, True)
    assert view.rebuild_random_bots() is False
    assert seam.calls == [] and boxes == [], "refused before the question"


def test_the_bot_count_and_the_tuning_reset_are_refused_while_a_rebuild_runs(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import bot_population

    path = _conf(tmp_path)
    release = threading.Event()
    seam = _Seam(release)
    view = _view(tmp_path, seam, bot_population=bot_population.bot_count_route(TORTOISE, tmp_path))
    assert view.bot_count_box.value() == 500
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    asked = _questions(monkeypatch, QMessageBox.StandardButton.Yes)
    try:
        assert view.rebuild_random_bots() is True
        pump_until(lambda: seam.calls == ["rebuild"], "the rebuild job started")
        view.bot_count_box.setValue(50)
        view.apply_bot_count()
        view.reset_to_default([CONF])
        assert asked == [], "neither asked its question while the rebuild ran"
        assert path.read_bytes() == CONF_TEXT.encode("utf-8")
    finally:
        release.set()
    _wait(view)


# -- after an update -------------------------------------------------------------------------


def _updating_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    heads: list[str | None],
    fail: bool = False,
) -> tuple[controller_view_module.ControllerView, _Seam, botpool.ModuleMoved]:
    """A Tortoise tab whose update route is T123's REAL wrapper over a scripted press."""
    from yulon.catalog import native

    def press(cancel: object = None) -> Iterator[str]:
        yield "moved"
        if fail:
            raise RuntimeError("the build failed")

    base = native.LatestRoute(
        confirmation=lambda: "Update?",
        press=press,
        pin_confirmation=lambda: "Back?",
        to_pin=press,
        source_version=lambda: native.SourceVersion(line="", past_the_pin=False),
    )
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: heads.pop(0))
    moved = botpool.ModuleMoved()
    route = botpool.wrap_route(
        base, TORTOISE, tmp_path, channels=lambda: [], restart=lambda: None, moved=moved
    )
    seam = _Seam()
    seam.take_module_moved = moved.take  # type: ignore[method-assign]
    view = _view(tmp_path, seam, update_to_latest=route)
    return view, seam, moved


def _real_updating_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: World
) -> controller_view_module.ControllerView:
    """T123's real wrapper AND the real rebuild, sharing one world: one restart counter."""
    from yulon.catalog import native

    def press(cancel: object = None) -> Iterator[str]:
        yield "moved"

    base = native.LatestRoute(
        confirmation=lambda: "Update?",
        press=press,
        pin_confirmation=lambda: "Back?",
        to_pin=press,
        source_version=lambda: native.SourceVersion(line="", past_the_pin=False),
    )
    heads = [OLD, NEW]
    monkeypatch.setattr(botpool, "head_sha", lambda _dest, **_kw: heads.pop(0))
    moved = botpool.ModuleMoved()
    route = botpool.wrap_route(
        base,
        TORTOISE,
        tmp_path,
        channels=world.channels,  # type: ignore[arg-type]
        restart=world.restart,
        moved=moved,
    )
    return _view(tmp_path, world.rebuild(module_moved=moved), update_to_latest=route)


def _questions(monkeypatch: pytest.MonkeyPatch, answer: object) -> list[str]:
    asked: list[str] = []

    def question(_parent: object, _title: str, text: str, *_a: object) -> object:
        asked.append(text)
        return answer

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def _update_without_backup(view: controller_view_module.ControllerView) -> None:
    view.update_to_latest()
    pump_until(lambda: not view.rebuild_log.running and not view._busy, "the update job finished")


OFFER = "TortoiseBots changed"


def test_an_update_that_moved_tortoisebots_offers_the_rebuild_and_yes_runs_it(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, seam, _moved = _updating_view(tmp_path, monkeypatch, heads=[OLD, NEW])
    asked = _questions(monkeypatch, QMessageBox.StandardButton.Yes)
    # The update's own dialog and then the rebuild's: both "without a backup".
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _update_without_backup(view)
    assert any(OFFER in text for text in asked)
    offer = next(text for text in asked if OFFER in text)
    assert "lose level and gear" in offer and "accounts stay" in offer
    _wait(view)
    assert seam.calls == ["rebuild"]


def test_no_to_the_offer_leaves_everything(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, seam, _moved = _updating_view(tmp_path, monkeypatch, heads=[OLD, NEW])
    asked = _questions(monkeypatch, QMessageBox.StandardButton.No)
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _update_without_backup(view)
    assert any(OFFER in text for text in asked)
    assert seam.calls == []


def test_an_update_that_did_not_move_tortoisebots_asks_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, seam, _moved = _updating_view(tmp_path, monkeypatch, heads=[OLD, OLD])
    asked = _questions(monkeypatch, QMessageBox.StandardButton.Yes)
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _update_without_backup(view)
    assert not any(OFFER in text for text in asked)
    assert seam.calls == []


def test_a_failed_update_asks_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, seam, moved = _updating_view(tmp_path, monkeypatch, heads=[OLD, NEW], fail=True)
    asked = _questions(monkeypatch, QMessageBox.StandardButton.Yes)
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _update_without_backup(view)
    assert not any(OFFER in text for text in asked)
    assert seam.calls == []


def test_a_cancelled_update_asks_nothing_and_the_flag_does_not_outlive_it(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, seam, moved = _updating_view(tmp_path, monkeypatch, heads=[OLD, NEW])
    asked = _questions(monkeypatch, QMessageBox.StandardButton.Yes)
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    monkeypatch.setattr(type(view.rebuild_log), "cancelled", property(lambda _self: True))
    _update_without_backup(view)
    assert not any(OFFER in text for text in asked)
    assert seam.calls == []
    assert moved.take() is None, "the finish consumed the flag"


def test_moved_enrolled_and_yes_is_exactly_one_restart_with_the_key_written_before_it(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conf(tmp_path)
    world = World(tmp_path, answers=[PREVIEW_PENDING, CONFIRMED, PREVIEW_ALL_MANAGED])
    view = _real_updating_view(tmp_path, monkeypatch, world)
    _questions(monkeypatch, QMessageBox.StandardButton.Yes)
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _update_without_backup(view)
    _wait(view)
    assert world.events == ["preview", "confirm", "preview", f"restart:once:{TOKEN}"]
    assert world.restarts == 1


@pytest.mark.parametrize("how", ["No to the offer", "Cancel on the rebuild dialog"])
def test_moved_enrolled_and_no_is_exactly_one_restart_the_enrolments(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    _conf(tmp_path)
    world = World(tmp_path, answers=[PREVIEW_PENDING, CONFIRMED])
    view = _real_updating_view(tmp_path, monkeypatch, world)
    yes = how != "No to the offer"
    _questions(
        monkeypatch, QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
    )
    _answers(monkeypatch, QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel)
    _update_without_backup(view)
    _wait(view)
    assert world.events == ["preview", "confirm", "restart:off"]
    assert world.restarts == 1


def test_moved_nothing_enrolled_and_no_is_no_restart(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conf(tmp_path)
    world = World(tmp_path, answers=[PREVIEW_ALL_MANAGED])
    view = _real_updating_view(tmp_path, monkeypatch, world)
    asked = _questions(monkeypatch, QMessageBox.StandardButton.No)
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _update_without_backup(view)
    _wait(view)
    assert any(OFFER in text for text in asked)
    assert world.events == ["preview"]
    assert world.restarts == 0


def test_an_owed_restart_the_view_cannot_run_is_said_not_dropped(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stop landing after T123 owed the restart: nothing is asked, and the player is told."""
    _conf(tmp_path)
    world = World(tmp_path, answers=[PREVIEW_PENDING, CONFIRMED])
    view = _real_updating_view(tmp_path, monkeypatch, world)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a: told.append(text))
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    monkeypatch.setattr(type(view.rebuild_log), "cancelled", property(lambda _self: True))
    _update_without_backup(view)
    assert world.events == ["preview", "confirm"]
    assert world.restarts == 0
    assert told and "Server tab" in told[-1]
