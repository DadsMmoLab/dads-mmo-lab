"""7.9's three unmeasured criteria, driven through the seams the Server tab uses.

WHAT THIS CLOSES. `pyplan/phase7-decisions.md` sets 7.9's bar as
"start/stop/logs/accounts/backup on each installed server". Accounts and a real
client login were driven on all three CMaNGOS games on 2026-09-03; three things
were not, and this script is those three:

  1. a backup -> verify -> restore round trip,
  2. `console.send_command()` against a live CMaNGOS worldserver,
  3. TIMED `stop_staged` / `start_staged`.

WHY IT GOES THROUGH `ControllerServices`. Calling `docker.stop_staged()` here
would time a function the Server tab does not call. `Controller.stop()` is what
the Stop button calls and it is what calls `stop_staged()` (`controller.py:291`);
`Controller.start()` likewise (`controller.py:212`). So the button's own path is
timed, not a sibling of it. Same for the rest: `services.backup()`,
`services.plan_restore()`, `services.restore()` and `services.send_console()`
are the callables the view binds, taken from `ControllerServices.for_entry()`,
which dispatches on the catalog id into that game's own package.

WHAT THE CONSOLE STEP IS REALLY ASKING. Not "did a reply come back" -- that is
the easy half. `send_command()` attaches to the worldserver's tty and detaches
again, and a detach that forwards a signal kills the server. So the container's
`State.Pid`, `RestartCount` and `StartedAt` are read BEFORE and AFTER through
the docker CLI -- not through this app, because an answer sourced from the code
under test is worth less -- and the gate is that all three are unchanged. A
reply with a restarted server behind it is a failure that looks like a pass.

READINESS IS TIMED THROUGH EACH PACKAGE'S OWN `wait_server_ready()`, and the
four signatures genuinely differ: TBC takes neither `realm_host` nor
`realm_port` because that entry has no auth marker to spell, Vanilla defaults
`realm_host` and accepts no port, and Tortoise and WotLK require both. A single
spelling here would have had to invent arguments for two of the four, so the
dispatch is a table keyed by catalog id (`_READY_CALLS`) and an id that is not
in it stops the run.

AND THE READY WAIT HAPPENS WHETHER OR NOT THIS RUN DID THE STARTING. On the
2026-09-04 TBC re-run the baseline found all three containers already up -- a
person had `docker start`ed them a minute earlier -- so the harness took the
"nothing to do" path, skipped its own wait, and attached the console to a
worldserver that was still printing its load progress. It recorded
`FAIL the console prompt never appeared in the window; the reply was not
delimited`, which is a fact about this harness's timing and says nothing about
`send_command()`. That wait is now unconditional, and it is booked as
`baseline_ready_seconds` rather than `ready_seconds`: the latter names the time
from a `start_staged()` this run performed, and waiting out the tail of
somebody else's start is not that number.

THE RESTORE IS A ROUND TRIP, deliberately. It backs up the CURRENT databases and
restores THOSE dumps back over them, so the server ends holding exactly what it
held before. There is no separate "known good" state to lose.

AND IT RESTORES EVERY DUMP, which the first version of this script did not. It
restored `report.dumps[0]`, and because `backup()` writes one file per database
in sorted order, that is always the alphabetically first one -- `characters`.
The world database, by far the largest and the one anybody actually restores a
server for, was backed up and byte-verified and then never handed to `restore()`
at all. `plan_restore()` genuinely does take one file at a time, so covering
them all means a loop; that is what a person restoring a whole server would do,
and it is what this now does. Found by a review of the first run, not by the run.

RUN IT:
    python gate-79-controller-surface.py <game-id> <server-dir>

e.g. python gate-79-controller-surface.py wow-tbc /home/pk/tbc-7.4c
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from yulon.catalog.catalog import load_catalog
from yulon.log import use_utf8_streams
from yulon.ui.controller_view import ControllerServices

use_utf8_streams()

RESULTS: list[tuple[str, str]] = []
TIMES: dict[str, float] = {}


def section(name: str) -> None:
    print(f"\n{'=' * 22} {name} {'=' * 22}", flush=True)


def ok(msg: str) -> None:
    print(f"[OK] {msg}", flush=True)
    RESULTS.append(("OK", msg))


def fail(msg: str, exc: BaseException | None = None) -> None:
    print(f"[FAIL] {msg}", flush=True)
    if exc is not None:
        traceback.print_exc()
    RESULTS.append(("FAIL", msg))


def inspect(container: str) -> dict[str, object]:
    """`State.Pid`, `RestartCount` and `StartedAt` for one container, via the CLI."""
    out = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Pid}}|{{.RestartCount}}|{{.State.StartedAt}}",
            container,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    pid, restarts, started = out.split("|", 2)
    return {"pid": int(pid), "restarts": int(restarts), "started_at": started}


def running(names: list[str]) -> dict[str, bool]:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True
    ).stdout.split()
    return {name: name in out for name in names}


class UnknownGameError(Exception):
    """A catalog id this harness has no `wait_server_ready()` spelling wired for."""


# How each game's `wait_server_ready()` is called, keyed by catalog id. A table
# and not a chain of `if`s: the chain this replaced ended in an `else` whose
# comment said "Tortoise", and `wow-wotlk` -- a game that has shipped in the
# catalog the whole time -- was landing in it. That went unnoticed only because
# WotLK happens to take the same two arguments Tortoise does. The next id added
# would have been handed `("127.0.0.1", auth_port)` on the strength of an
# `else`, and whatever came back would have been printed as that game's
# readiness.
_READY_CALLS: dict[str, Callable[[ModuleType, int], bool]] = {
    # No realm arguments: this entry has no auth marker to spell, and its
    # `wait_server_ready()` raises `TypeError` on anything but timeout/interval.
    "wow-tbc": lambda module, auth_port: bool(module.wait_server_ready()),
    # `realm_host` defaults and no port is accepted -- `ready.auth` is null.
    "wow-vanilla": lambda module, auth_port: bool(module.wait_server_ready()),
    "wow-tortoise": lambda module, auth_port: bool(
        module.wait_server_ready("127.0.0.1", auth_port)
    ),
    "wow-wotlk": lambda module, auth_port: bool(module.wait_server_ready("127.0.0.1", auth_port)),
}


def unknown_game_error(game: str) -> UnknownGameError:
    """The refusal for an id with no `_READY_CALLS` entry, worded once."""
    return UnknownGameError(
        f"this harness has no wait_server_ready() spelling wired for {game!r}, so it cannot "
        f"tell whether that game's server is up and must not report on one. The ids it is "
        f"wired for are: {', '.join(sorted(_READY_CALLS))}. To add {game!r}, give it its own "
        f"_READY_CALLS entry with the arguments that game's own wait_server_ready() takes; "
        f"borrowing another game's spelling is what this replaced."
    )


def wait_ready_for_game(game: str, auth_port: int) -> bool:
    """That game's own `wait_server_ready()`, with the arguments it actually takes.

    Raises:
        UnknownGameError: `game` has no entry in `_READY_CALLS`.
    """
    call = _READY_CALLS.get(game)
    if call is None:
        raise unknown_game_error(game)
    module = importlib.import_module(f"yulon.controller_{game.replace('-', '_')}.docker_ctl")
    return call(module, auth_port)


def main() -> int:
    game, server_dir = sys.argv[1], Path(sys.argv[2])
    if game not in _READY_CALLS:
        # Checked before docker is touched. Every section below asks a server a
        # question, and a run that cannot establish that the server is up cannot
        # honestly report the answers -- half a gate reads like a whole one.
        print(f"[FAIL] {unknown_game_error(game)}", flush=True)
        return 2
    entry = load_catalog().get(game)
    services = ControllerServices.for_entry(entry, server_dir)
    spec = entry.container_spec()
    world, auth, db = spec.world, spec.auth, spec.db
    auth_port = spec.ports[0]
    print(f"game={game} server_dir={server_dir}", flush=True)
    print(f"containers: world={world} auth={auth} db={db} auth_port={auth_port}", flush=True)

    # ------------------------------------------------------------- 0. baseline
    section("baseline")
    status = services.controller.status()
    print(f"status: db={status.db} auth={status.auth} world={status.world}", flush=True)
    print("running:", running([world, auth, db]), flush=True)
    started_here = not status.all_running
    if started_here:
        print("not all up; starting first so the console and backup have a server", flush=True)
        t0 = time.monotonic()
        services.controller.start()
        TIMES["baseline_start_staged_seconds"] = time.monotonic() - t0
        print("running now:", running([world, auth, db]), flush=True)
    else:
        print("all three already up; this run did not start them", flush=True)

    # The wait is unconditional, and that is the fix for the 2026-09-04 TBC
    # re-run: containers found up were taken for containers found ready, the
    # console attached to a worldserver still loading its area levels, and the
    # gate recorded a console failure that was really a scheduling failure. A
    # server this run did not start still has to be ready before it is asked a
    # question -- `docker start` returns long before mangosd is listening.
    try:
        t0 = time.monotonic()
        baseline_ready = wait_ready_for_game(game, auth_port)
        # Not `ready_seconds`. That key belongs to section 7, which times a
        # `start_staged()` this run performed; a wait spent on the tail of
        # somebody else's start is a different measurement and gets a different
        # name, so no reader can quote one as the other.
        TIMES["baseline_ready_seconds"] = time.monotonic() - t0
        if baseline_ready:
            ok(
                f"the server reached this game's ready marker before the console step "
                f"({TIMES['baseline_ready_seconds']:.1f}s waited, "
                f"started_by_this_run={started_here})"
            )
        else:
            fail(
                f"the server never reached this game's ready marker "
                f"({TIMES['baseline_ready_seconds']:.0f}s waited, "
                f"started_by_this_run={started_here}); every step below is questioning a "
                f"server that is not up yet"
            )
    except Exception as exc:  # noqa: BLE001
        fail("wait_server_ready() at baseline", exc)

    # ------------------------------------------------- 1. console.send_command
    section("console.send_command() -- and what the attach did to the server")
    try:
        before = inspect(world)
        print(f"{world} before: {before}", flush=True)
        t0 = time.monotonic()
        reply = services.send_console("server info")
        TIMES["console_seconds"] = time.monotonic() - t0
        print(f"reply.command : {reply.command!r}", flush=True)
        print(f"reply.prompted: {reply.prompted}", flush=True)
        for line in reply.lines:
            print(f"  | {line}", flush=True)
        after = inspect(world)
        print(f"{world} after:  {after}", flush=True)
        if before["pid"] == 0:
            # `docker inspect` reports State.Pid 0 for a container that is not
            # running, and 0 stays 0 across the attach, so the "unchanged"
            # comparison below is satisfied by a worldserver that was never up
            # to be disturbed. The whole point of this step is that attaching to
            # a LIVE server does not kill it.
            fail(
                f"{world} reported State.Pid 0, so it was not running and the attach had "
                f"nothing to disturb: {before}"
            )
        elif after == before:
            ok(
                f"send_command('server info') answered in {TIMES['console_seconds']:.2f}s and left "
                f"pid/RestartCount/StartedAt unchanged (pid {before['pid']}, "
                f"RestartCount {before['restarts']})"
            )
        else:
            fail(f"the attach disturbed {world}: {before} -> {after}")
        if not reply.prompted:
            fail("the console prompt never appeared in the window; the reply was not delimited")
        elif not any(line.strip() for line in reply.lines):
            fail("the console replied with no lines; the prompt split is wrong for this core")
        else:
            ok(f"the reply carries {len(reply.lines)} line(s) cut on this core's own prompt")
    except Exception as exc:  # noqa: BLE001 -- a gate records failures, it does not raise
        fail("send_command()", exc)

    # ------------------------------------------------------------- 2. backup
    section("backup() -- live, with the server running")
    report = None
    try:
        t0 = time.monotonic()
        report = services.backup()
        TIMES["backup_seconds"] = time.monotonic() - t0
        for dump in report.dumps:
            print(f"  dump: {dump.database} {dump.path} {dump.size_bytes} bytes", flush=True)
        print(f"  missing_core: {report.missing_core}", flush=True)
        # Either/or, not both. The first draft called `ok()` unconditionally and
        # THEN `fail()` on `missing_core`, so a backup that had lost a core
        # database would have printed a pass line and a failure line for the
        # same operation -- and a reader scanning for `[OK]` would have seen the
        # pass sitting next to the failure it contradicts.
        if not report.dumps:
            # `missing_core` only names the databases the catalog entry calls
            # core, so an entry that declares none would come back with an empty
            # `missing_core` and an empty `dumps`, and the branch below would
            # have printed "backup() -> 0 dump(s) ()" as a pass. A backup that
            # wrote nothing is the failure this step exists to catch.
            fail(
                f"backup() returned no dumps at all; it reported directory {report.directory} "
                f"and missing_core={report.missing_core}"
            )
        elif report.missing_core:
            fail(
                f"backup() ran but the server was missing core databases: {report.missing_core}; "
                f"it wrote {len(report.dumps)} dump(s) ({', '.join(report.databases)})"
            )
        else:
            ok(
                f"backup() -> {len(report.dumps)} dump(s) ({', '.join(report.databases)}) in "
                f"{report.directory} in {TIMES['backup_seconds']:.1f}s, "
                f"server_was_running={report.server_was_running}"
            )
    except Exception as exc:  # noqa: BLE001
        fail("backup()", exc)

    # -------------------------------------------------------- 3. verify_dump
    section("verify_dump() on every dump")
    try:
        if report is None:
            raise RuntimeError("no report from backup()")
        # An empty loop leaves nothing to say. Without this the line below read
        # "verify_dump() passed for all 0 dump(s)" -- a pass for a function that
        # was never called.
        if not report.dumps:
            raise RuntimeError("backup() wrote no dumps, so verify_dump() was never called")
        maintenance = importlib.import_module(
            f"yulon.controller_{game.replace('-', '_')}.maintenance"
        )
        for dump in report.dumps:
            size = maintenance.verify_dump(dump.path, dump.database)
            print(f"  verify_dump({dump.path.name}) -> {size} bytes", flush=True)
        ok(f"verify_dump() passed for all {len(report.dumps)} dump(s)")
    except Exception as exc:  # noqa: BLE001
        fail("verify_dump()", exc)

    # ----------------------------------------- 4. plan_restore refuses, running
    section("plan_restore() while the server is up -- it must REFUSE")
    pick = None
    try:
        if report is None:
            raise RuntimeError("no report from backup()")
        pick = report.dumps[0].path
        state = running([world, auth, db])
        print("running when asked:", state, flush=True)
        plan = services.plan_restore(pick)
        print("refusals:", plan.refusals, flush=True)
        # `plan_restore()` collects every reason at once -- a truncated dump, a
        # missing file, a stopped db container -- and the old check passed on any
        # of them. It would have printed "refused while running" for a plan that
        # was refused because the file was unreadable, which proves nothing about
        # the live-server guard. The guard's own refusal names the containers it
        # found up (`maintenance.py`: "<names> are running. A restore has to
        # happen with the game servers stopped"), so that is what is looked for.
        blocked_on_world = [reason for reason in plan.refusals if world in reason]
        if not state[world]:
            fail(
                f"{world} was not running, so this step never put plan_restore() in front of "
                f"the situation it is named for; its refusals were {plan.refusals}"
            )
        elif blocked_on_world:
            ok(f"plan_restore() refused while running: {blocked_on_world}")
        elif plan.refusals:
            fail(
                f"plan_restore() refused, but no refusal names {world}, so something other than "
                f"the live-server guard stopped it: {plan.refusals}"
            )
        else:
            fail("plan_restore() allowed a restore over a RUNNING server")
    except Exception as exc:  # noqa: BLE001
        fail("plan_restore() refusal", exc)

    # --------------------------------------- 5. TIMED stop_staged (Stop button)
    section("Controller.stop() -- the Stop button, which calls stop_staged()")
    try:
        t0 = time.monotonic()
        stopped = services.controller.stop()
        TIMES["stop_staged_seconds"] = time.monotonic() - t0
        state = running([world, auth, db])
        print("running after stop:", state, flush=True)
        if any(state.values()):
            fail(f"stop() returned {stopped} but containers are still up: {state}")
        else:
            ok(
                f"stop_staged() took {TIMES['stop_staged_seconds']:.1f}s and returned {stopped}; "
                "db, auth and world all down"
            )
    except Exception as exc:  # noqa: BLE001
        fail("controller.stop()", exc)

    # ------------------------------------- 6. TIMED start_staged (Start button)
    section("Controller.start() -- the Start button, which calls start_staged()")
    try:
        t0 = time.monotonic()
        services.controller.start()
        TIMES["start_staged_seconds"] = time.monotonic() - t0
        state = running([world, auth, db])
        print("running after start:", state, flush=True)
        if all(state.values()):
            ok(f"start_staged() returned in {TIMES['start_staged_seconds']:.1f}s; all three up")
        else:
            fail(f"start() left containers down: {state}")
    except Exception as exc:  # noqa: BLE001
        fail("controller.start()", exc)

    # ------------------------------------ 7. time from that start to READY
    section("time from start_staged() to this game's own ready marker")
    try:
        t0 = time.monotonic()
        ready = wait_ready_for_game(game, auth_port)
        TIMES["ready_seconds"] = time.monotonic() - t0
        if ready:
            ok(f"reached ready {TIMES['ready_seconds']:.1f}s after start_staged() returned")
        else:
            fail(f"wait_server_ready() gave up after {TIMES['ready_seconds']:.0f}s")
    except Exception as exc:  # noqa: BLE001
        fail("wait_server_ready()", exc)

    # ------------------------------------- 8. the real restore round trip
    section("restore round trip: db up, world+auth down -- EVERY dump, not just the first")
    try:
        if report is None or not report.dumps:
            raise RuntimeError("no dumps to restore")
        # `docker stop`, NOT `Controller.stop()`, and NOT the database: a restore
        # needs the db container up and the two servers down, which is a state
        # the Stop button does not have. The order matters and is why this is
        # spelled out rather than left to a bare `docker stop` of all three --
        # see the note this gate produced about a worldserver that segfaults
        # when its database disappears underneath it.
        subprocess.run(["docker", "stop", world, auth], check=True)
        time.sleep(3)
        # EVERY dump. The first draft restored `report.dumps[0]` alone, which is
        # always the alphabetically-first database, so the world database -- by
        # far the largest and the one a restore exists for -- was backed up and
        # verified but its restore path was never run. `plan_restore()` really
        # does take one file at a time, so the loop is the honest way to cover
        # them: it is what a user restoring a whole server would press.
        TIMES["restore_seconds"] = 0.0
        restored_names: list[str] = []
        for dump in report.dumps:
            plan = services.plan_restore(dump.path)
            print(f"\n-- {dump.database} ({dump.size_bytes} bytes)", flush=True)
            print("   refusals:", plan.refusals, flush=True)
            print(f"   databases: {plan.databases}  size_bytes: {plan.size_bytes}", flush=True)
            print(f"   interrupted: {plan.interrupted}", flush=True)
            if plan.refusals:
                fail(f"plan_restore({dump.database}) refused with world+auth down: {plan.refusals}")
                continue
            t0 = time.monotonic()
            restored = services.restore(plan)
            took = time.monotonic() - t0
            TIMES["restore_seconds"] += took
            restored_names.extend(restored.databases)
            print(
                f"   restored {restored.databases} in {took:.1f}s "
                f"(safety: {[p.name for p in restored.safety_backup]})",
                flush=True,
            )
        # By name, not by count. `restored.databases` is a tuple per dump and
        # nothing promises it holds exactly one, so `len(restored_names) ==
        # len(report.dumps)` -- the old check -- is satisfied by one dump that
        # restored two databases next to one that restored none, and it would
        # then have named all of them in a pass line. Comparing the sorted names
        # against what the backup actually held is the claim being made.
        if sorted(restored_names) == sorted(report.databases):
            ok(
                f"restore() put back all {len(restored_names)} databases "
                f"({', '.join(restored_names)}) in {TIMES['restore_seconds']:.1f}s total"
            )
        else:
            fail(
                f"restore() put back {sorted(restored_names)}, but the backup held "
                f"{sorted(report.databases)}"
            )
    except Exception as exc:  # noqa: BLE001
        fail("restore()", exc)

    # ------------------------------------- 9. leave the box as it was found
    section("start again, and confirm the restored server comes back ready")
    try:
        services.controller.start()
        t0 = time.monotonic()
        ready = wait_ready_for_game(game, auth_port)
        TIMES["ready_after_restore_seconds"] = time.monotonic() - t0
        print("running:", running([world, auth, db]), flush=True)
        if ready:
            ok(
                f"the restored server reached ready again in "
                f"{TIMES['ready_after_restore_seconds']:.1f}s"
            )
        else:
            fail("the restored server never reached ready")
    except Exception as exc:  # noqa: BLE001
        fail("controller.start() after restore", exc)

    section("SUMMARY")
    for status_, msg in RESULTS:
        print(f"{status_} {msg}", flush=True)
    print("\nTIMES " + json.dumps(TIMES), flush=True)
    failures = [m for s, m in RESULTS if s == "FAIL"]
    print(f"\n{len(RESULTS) - len(failures)} passed, {len(failures)} failed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
