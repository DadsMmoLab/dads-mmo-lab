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
three signatures genuinely differ: TBC takes neither `realm_host` nor
`realm_port` because that entry has no auth marker to spell, Vanilla defaults
`realm_host`, and Tortoise requires both. A single spelling here would have had
to invent arguments for two of the three, so the dispatch is explicit.

THE RESTORE IS A ROUND TRIP, deliberately. It backs up the CURRENT databases and
restores THAT dump back over them, so the server ends holding exactly what it
held before. There is no separate "known good" state to lose.

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
from pathlib import Path

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


def wait_ready_for_game(game: str, auth_port: int) -> bool:
    """That game's own `wait_server_ready()`, with the arguments it actually takes.

    Not one spelling for all three: TBC's takes no realm arguments at all and
    raises `TypeError` on anything but `timeout`/`interval`, so passing a host
    to be uniform would fail on the one entry that is already ticked.
    """
    module = importlib.import_module(f"yulon.controller_{game.replace('-', '_')}.docker_ctl")
    if game == "wow-tbc":
        return bool(module.wait_server_ready())
    if game == "wow-vanilla":
        return bool(module.wait_server_ready())
    return bool(module.wait_server_ready("127.0.0.1", auth_port))


def main() -> int:
    game, server_dir = sys.argv[1], Path(sys.argv[2])
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
    if not status.all_running:
        print("not all up; starting first so the console and backup have a server", flush=True)
        services.controller.start()
        wait_ready_for_game(game, auth_port)
        print("running now:", running([world, auth, db]), flush=True)

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
        if after == before:
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
        ok(
            f"backup() -> {len(report.dumps)} dump(s) ({', '.join(report.databases)}) in "
            f"{report.directory} in {TIMES['backup_seconds']:.1f}s, "
            f"server_was_running={report.server_was_running}"
        )
        if report.missing_core:
            fail(f"the server was missing core databases: {report.missing_core}")
    except Exception as exc:  # noqa: BLE001
        fail("backup()", exc)

    # -------------------------------------------------------- 3. verify_dump
    section("verify_dump() on every dump")
    try:
        if report is None:
            raise RuntimeError("no report from backup()")
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
        plan = services.plan_restore(pick)
        print("refusals:", plan.refusals, flush=True)
        if plan.refusals:
            ok(f"plan_restore() refused while running: {plan.refusals}")
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
    section("restore round trip: db up, world+auth down")
    try:
        if pick is None:
            raise RuntimeError("no dump to restore")
        subprocess.run(["docker", "stop", world, auth], check=True)
        time.sleep(3)
        plan = services.plan_restore(pick)
        print("refusals now:", plan.refusals, flush=True)
        print(f"databases: {plan.databases}  size_bytes: {plan.size_bytes}", flush=True)
        print(f"interrupted: {plan.interrupted}", flush=True)
        if plan.refusals:
            fail(f"plan_restore() still refused with world+auth down: {plan.refusals}")
        else:
            t0 = time.monotonic()
            restored = services.restore(plan)
            TIMES["restore_seconds"] = time.monotonic() - t0
            ok(
                f"restore() put back {restored.databases} from {restored.backup.name} in "
                f"{TIMES['restore_seconds']:.1f}s (safety backup: "
                f"{[p.name for p in restored.safety_backup]})"
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
