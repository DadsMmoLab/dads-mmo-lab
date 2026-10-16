"""Tests for the host-program controller (`yulon.process_controller`).

The process table, the spawn, the ports and taskkill are `ProcessControlSeams` fakes,
so nothing starts: what these prove is what Start writes and refuses, what the record
says, how status reads it, that Stop ends only the tree it started, and the console.
"""

from __future__ import annotations

import json
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import resources, runner
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.process_controller import (
    HOLDER_SCRIPT,
    RUN_RECORD,
    SERVER_WRAP,
    ProcessController,
    ProcessControlSeams,
    RunRecord,
)
from yulon.single_controller import RUNNING, STARTING, STOPPED, UNKNOWN, SingleServerError
from yulon.ui import lines

DFO: SingleEntry = load_catalog().get_single("dfo")
READY = "Server listening on 127.0.0.1:7001 and 127.0.0.2:51234\n"


@dataclass
class Host:
    """A fake process table and network: who is alive, what runs as what, which ports answer."""

    alive_pids: set[int] = field(default_factory=set)
    programs: dict[int, Path] = field(default_factory=dict)
    unreadable: set[int] = field(default_factory=set)
    open_ports: set[int] = field(default_factory=set)
    spawned: list[dict[str, Any]] = field(default_factory=list)
    ended: list[int] = field(default_factory=list)
    survives_end: bool = False
    next_pid: int = 4242
    now: float = 0.0

    def spawn(self, argv: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> int:
        self.spawned.append({"argv": argv, "cwd": cwd, "env": env, "log": log})
        log.write_bytes(b"")
        pid = self.next_pid
        self.alive_pids.add(pid)
        self.programs[pid] = Path(argv[0])
        return pid

    def end_tree(self, pid: int) -> None:
        self.ended.append(pid)
        if not self.survives_end:
            self.alive_pids.discard(pid)

    def alive(self, pid: int) -> bool | None:
        if pid in self.unreadable:
            return None
        return pid in self.alive_pids

    def is_program(self, pid: int, program: Path) -> bool | None:
        return self.programs.get(pid) == program

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def seams(self) -> ProcessControlSeams:
        return ProcessControlSeams(
            spawn=self.spawn,
            end_tree=self.end_tree,
            alive=self.alive,
            is_program=self.is_program,
            port_open=lambda port: port in self.open_ports,
            sleep=self.sleep,
            clock=lambda: self.now,
            # The Windows path: the private network is Linux's, with tests of its own.
            platform_id=lambda: "windows",
        )


@pytest.fixture
def host() -> Host:
    return Host()


@pytest.fixture
def client(tmp_path: Path) -> Path:
    folder = tmp_path / "DFO client"
    folder.mkdir()
    (folder / "DFO.exe").write_bytes(b"MZ")
    return folder


def _controller(tmp_path: Path, host: Host) -> ProcessController:
    server = tmp_path / "yulon-dfo"
    (server / "src").mkdir(parents=True, exist_ok=True)
    return ProcessController(
        DFO, server, installers_root=resources.installers_dir(), seams=host.seams()
    )


def test_a_folder_that_never_started_is_stopped(tmp_path: Path, host: Host) -> None:
    control = _controller(tmp_path, host)
    assert control.status() == STOPPED
    assert control.session_account() is None
    assert control.stop() is False


def test_start_writes_what_the_server_reads_and_runs_its_launcher_detached(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)

    server = control.server_dir.resolve().as_posix()
    launcher = json.loads((control.server_dir / "src/server/launcher.local.json").read_text())
    assert launcher == {
        "channel_identity": False,
        "client_dir": client.resolve().as_posix(),
        "server_binary": "work/dfo-lan/bin/wireprobe-pvf.exe",
    }
    storage_file = control.server_dir / "src/server/work/dfo-lan/runtime/storage/local.json"
    storage = json.loads(storage_file.read_text())
    assert (
        storage["sqlite_path"] == f"{server}/src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3"
    )
    assert storage["driver"] == "sqlite"

    (spawn,) = host.spawned
    assert spawn["argv"] == [
        f"{server}/src/server/work/dfo-lan/bin/dfolauncher.exe",
        "launch",
        "--server-only",
        "--root",
        f"{server}/src",
        "--account",
        "mmodad",
    ]
    assert spawn["cwd"] == control.server_dir / "src"
    assert spawn["env"]["DFO_SHOP_OPEN_ALL"] == "1"
    assert spawn["env"]["DFO_QUEST_NPC_DISTANCE_MULTIPLIER"] == "5"
    assert spawn["log"] == control.server_dir / "yulon-server.log"
    record = json.loads((control.server_dir / RUN_RECORD).read_text())
    assert record["pid"] == 4242 and record["account"] == "mmodad"
    assert control.session_account() == "mmodad"


@pytest.mark.parametrize(
    ("account", "has_client", "has_exe", "port", "match"),
    [
        ("a b", True, True, None, "3 to 20"),
        ("mmodad", False, True, None, "Choose your DFO client folder"),
        ("mmodad", True, False, None, "no DFO.exe"),
        ("mmodad", True, True, 7001, "port 7001"),
    ],
)
def test_start_refuses_and_starts_nothing(
    tmp_path: Path,
    host: Host,
    client: Path,
    account: str,
    has_client: bool,
    has_exe: bool,
    port: int | None,
    match: str,
) -> None:
    if not has_exe:
        (client / "DFO.exe").unlink()
    if port is not None:
        host.open_ports.add(port)
    control = _controller(tmp_path, host)
    with pytest.raises(SingleServerError, match=match):
        control.start(account=account, client_dir=client if has_client else None)
    assert host.spawned == []
    assert not (control.server_dir / RUN_RECORD).exists()


def test_a_lease_a_killed_server_left_is_cleared_once_its_holder_is_gone(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    lease = (
        control.server_dir / "src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3.admin-guard"
    )
    lease.parent.mkdir(parents=True)
    lease.write_text("13248")
    control.start(account="mmodad", client_dir=client)
    assert not lease.exists()
    assert len(host.spawned) == 1


def test_a_lease_whose_holder_still_runs_refuses_the_start(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    lease = (
        control.server_dir / "src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3.admin-guard"
    )
    lease.parent.mkdir(parents=True)
    lease.write_text("13248")
    host.alive_pids.add(13248)
    with pytest.raises(SingleServerError, match="Process 13248 holds"):
        control.start(account="mmodad", client_dir=client)
    assert lease.exists() and host.spawned == []


def test_status_reads_the_record_the_process_and_this_runs_log(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    assert control.status() == STARTING
    control.log_path.write_text("Using inner PVF\n" + READY)
    assert control.status() == RUNNING
    host.unreadable.add(4242)
    assert control.status() == UNKNOWN
    host.unreadable.clear()
    host.programs[4242] = Path("C:/Windows/notepad.exe")  # the pid was reused
    assert control.status() == STOPPED
    assert control.session_account() is None


def test_a_second_start_while_it_runs_starts_nothing(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    control.start(account="mmodad", client_dir=client)
    assert len(host.spawned) == 1


def test_stop_ends_only_the_tree_it_started_and_forgets_it(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    assert control.stop() is True
    assert host.ended == [4242]
    assert not (control.server_dir / RUN_RECORD).exists()
    assert control.status() == STOPPED


def test_stop_of_a_server_that_already_exited_forgets_it_and_ends_nothing(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    host.alive_pids.clear()
    assert control.stop() is False
    assert host.ended == []
    assert not (control.server_dir / RUN_RECORD).exists()


def test_a_tree_that_outlives_its_end_is_reported_not_forgotten(
    tmp_path: Path, host: Host, client: Path
) -> None:
    host.survives_end = True
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    with pytest.raises(SingleServerError, match="still running"):
        control.stop()
    assert (control.server_dir / RUN_RECORD).exists()


def test_the_files_start_writes_are_rewritten_only_when_their_text_changes(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    launcher = control.server_dir / "src/server/launcher.local.json"
    before = launcher.stat().st_mtime_ns
    control.stop()
    control.start(account="mmodad", client_dir=client)
    assert launcher.stat().st_mtime_ns == before


def test_the_console_relays_this_runs_log_and_ends_when_the_server_does(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    control.log_path.write_text("first\r\n" + READY + "tail without newline")
    host.alive_pids.clear()
    said = [lines.parse(line).text for line in control.follow_logs()]
    assert said == ["first", READY.strip(), "tail without newline"]


def test_the_console_ends_on_its_cancel_while_the_server_runs(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    control.log_path.write_text(READY)
    cancel = threading.Event()
    cancel.set()
    said = [lines.parse(line).text for line in control.follow_logs(cancel)]
    assert said == [READY.strip()]
    assert control.status() == RUNNING


def test_there_is_no_address_to_advertise(tmp_path: Path, host: Host) -> None:
    control = _controller(tmp_path, host)
    assert control.advertised() is None
    with pytest.raises(SingleServerError, match="this computer only"):
        control.advertise("192.168.0.60")


def test_a_container_entry_is_refused() -> None:
    with pytest.raises(ValueError, match="host programs"):
        ProcessController(load_catalog().get_single("ddon"), Path("x"))


def test_the_record_round_trips(tmp_path: Path, host: Host, client: Path) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    record = control._record()
    assert isinstance(record, RunRecord)
    assert record.program.endswith("dfolauncher.exe")


def test_the_console_follows_the_gateway_log_the_launcher_names(
    tmp_path: Path, host: Host, client: Path
) -> None:
    """`Logs: <folder>` names the per-run folder; its gateway.err is followed from line one."""
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    session = control.server_dir / "src/server/work/dfo-lan/runtime/roles_x_next37"
    session.mkdir(parents=True)
    (session / "gateway.err").write_text("loaded 17 professions\nchannel refresh ready\n")
    control.log_path.write_text(READY + f"Logs: {session}\n")
    host.alive_pids.clear()
    said = [lines.parse(line).text for line in control.follow_logs()]
    assert said == [
        READY.strip(),
        f"Logs: {session}",
        "loaded 17 professions",
        "channel refresh ready",
    ]


def test_a_named_log_folder_outside_the_server_is_not_followed(
    tmp_path: Path, host: Host, client: Path
) -> None:
    control = _controller(tmp_path, host)
    control.start(account="mmodad", client_dir=client)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "gateway.err").write_text("not ours\n")
    control.log_path.write_text(f"Logs: {elsewhere}\n")
    host.alive_pids.clear()
    said = [lines.parse(line).text for line in control.follow_logs()]
    assert said == [f"Logs: {elsewhere}"]


def test_a_start_whose_record_cannot_be_written_ends_the_program_it_started(
    tmp_path: Path, host: Host, client: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _controller(tmp_path, host)

    def refuse(_record: RunRecord) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(control, "_write_record", refuse)
    with pytest.raises(SingleServerError, match="could not be written"):
        control.start(account="mmodad", client_dir=client)
    assert host.ended == [4242]


# -- Linux: the private network ------------------------------------------------------------

OURS = "net:[4026531840]"
PRIVATE = "net:[4026532999]"


@dataclass
class LinuxHost:
    """A fake Linux process table: names, network namespaces, process groups and signals.

    `unshare -rn` makes the holder and writes its pid where the script would; `nsenter`
    starts the server inside the holder's network, with its gateway beside it.
    """

    alive_pids: set[int] = field(default_factory=set)
    comms: dict[int, str] = field(default_factory=dict)
    nets: dict[int, str] = field(default_factory=dict)
    groups: dict[int, int] = field(default_factory=dict)
    spawned: list[dict[str, Any]] = field(default_factory=list)
    signals: list[tuple[str, int, int]] = field(default_factory=list)
    holder_comes_up: bool = True
    nsenter_refused: bool = False
    gateway_own_group: bool = True
    deaf_to_sigint: bool = False
    next_pid: int = 5000
    now: float = 0.0

    def add(self, comm: str, net: str, group: int | None = None) -> int:
        pid = self.next_pid
        self.next_pid += 1
        self.alive_pids.add(pid)
        self.comms[pid] = comm
        self.nets[pid] = net
        self.groups[pid] = group if group is not None else pid
        return pid

    def spawn(self, argv: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> int:
        self.spawned.append({"argv": argv, "cwd": cwd, "env": env, "log": log})
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes(b"")
        if argv[0] == "unshare":
            pid = self.add("sleep", PRIVATE)
            if self.holder_comes_up:
                Path(argv[-1]).write_text(f"{pid}\n", encoding="utf-8")
            return pid
        if self.nsenter_refused:
            pid = self.next_pid
            self.next_pid += 1
            return pid  # exited at once: never alive, no server.pid
        server = self.add("dfolauncher.exe", PRIVATE)
        Path(argv[argv.index(SERVER_WRAP) + 2]).write_text(f"{server}\n", encoding="utf-8")
        self.add("wireprobe-pvf.e", PRIVATE, None if self.gateway_own_group else server)
        return server

    def kill(self, pid: int) -> None:
        self.alive_pids.discard(pid)

    def signal_group(self, group: int, sig: int) -> None:
        self.signals.append(("group", group, sig))
        if sig == signal.SIGINT and self.deaf_to_sigint:
            return
        for pid in [p for p in self.alive_pids if self.groups.get(p) == group]:
            self.kill(pid)

    def signal(self, pid: int, sig: int) -> None:
        self.signals.append(("pid", pid, sig))
        self.kill(pid)

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def seams(self) -> ProcessControlSeams:
        alive = self.alive_pids
        return ProcessControlSeams(
            spawn=self.spawn,
            end_tree=lambda pid: pytest.fail("Linux never ends a tree with taskkill"),
            alive=lambda pid: pid in alive,
            is_program=lambda pid, program: pytest.fail("Linux reads the name, not the exe"),
            port_open=lambda port: True,  # every host port "taken": Linux must not look
            sleep=self.sleep,
            clock=lambda: self.now,
            platform_id=lambda: "linux",
            comm=lambda pid: self.comms.get(pid) if pid in alive else None,
            netns=lambda pid: (
                OURS if pid == "self" else (self.nets.get(int(pid)) if int(pid) in alive else None)
            ),
            pids=lambda: sorted(alive),
            signal_group=self.signal_group,
            signal=self.signal,
            ids=lambda: (1000, 1000),
            pgid=lambda pid: self.groups.get(pid) if pid in alive else None,
        )


@pytest.fixture
def deck() -> LinuxHost:
    return LinuxHost()


def _deck_controller(tmp_path: Path, deck: LinuxHost) -> ProcessController:
    server = tmp_path / "yulon-dfo"
    (server / "src").mkdir(parents=True, exist_ok=True)
    return ProcessController(
        DFO, server, installers_root=resources.installers_dir(), seams=deck.seams()
    )


def test_on_linux_start_makes_the_private_network_then_runs_the_server_inside_it(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)

    run = control.server_dir / "run"
    holder_spawn, server_spawn = deck.spawned
    assert holder_spawn["argv"] == [
        "unshare",
        "-rn",
        "sh",
        "-c",
        HOLDER_SCRIPT,
        "sh",
        str(run / "holder.pid"),
    ]
    assert holder_spawn["cwd"] == Path("/"), "never a folder inside an AppImage's mount"
    holder = int((run / "holder.pid").read_text())
    serve = server_spawn["argv"]
    assert serve[:12] == [
        "nsenter",
        "-t",
        str(holder),
        "-U",
        "-n",
        "--preserve-credentials",
        "--",
        "unshare",
        "--user",
        "--map-user=1000",
        "--map-group=1000",
        "--",
    ]
    assert serve[12:17] == ["sh", "-c", SERVER_WRAP, "sh", str(run / "server.pid")]
    assert serve[17].endswith("/src/server/work/dfo-lan/bin/dfolauncher.exe")
    assert serve[18:21] == ["launch", "--server-only", "--root"]
    assert server_spawn["cwd"] == control.server_dir / "src"
    assert server_spawn["log"] == control.log_path
    assert (run / "server.logpath").read_text().strip() == str(control.log_path)
    record = json.loads((control.server_dir / RUN_RECORD).read_text())
    assert record["pid"] == int((run / "server.pid").read_text())
    assert control.status() == STARTING
    control.log_path.write_text(READY, encoding="utf-8")
    assert control.status() == RUNNING
    assert control.session_account() == "mmodad"


def test_on_linux_every_child_gets_the_cleaned_environment(
    tmp_path: Path, deck: LinuxHost, client: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AppImage's LD_LIBRARY_PATH kills SteamOS's /bin/sh (measured on Deck #2)."""
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_Yulon/_internal")
    monkeypatch.setattr(
        runner,
        "child_env",
        lambda env=None: {k: v for k, v in (env or {}).items() if k != "LD_LIBRARY_PATH"},
    )
    _deck_controller(tmp_path, deck).start(account="mmodad", client_dir=client)
    for spawn in deck.spawned:
        assert "LD_LIBRARY_PATH" not in spawn["env"], spawn["argv"][0]
    assert deck.spawned[1]["env"]["DFO_SHOP_OPEN_ALL"] == "1"


def test_on_linux_a_network_that_never_comes_up_starts_no_server(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    deck.holder_comes_up = False
    control = _deck_controller(tmp_path, deck)
    with pytest.raises(SingleServerError, match="user namespaces"):
        control.start(account="mmodad", client_dir=client)
    assert [s["argv"][0] for s in deck.spawned] == ["unshare"]
    assert not (control.server_dir / RUN_RECORD).exists()


def test_on_linux_a_server_running_with_no_record_is_never_orphaned(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    run = control.server_dir / "run"
    run.mkdir()
    holder = deck.add("sleep", PRIVATE)
    (run / "holder.pid").write_text(f"{holder}\n", encoding="utf-8")
    deck.add("dfolauncher.exe", PRIVATE)
    with pytest.raises(SingleServerError, match="started outside Yu'lon"):
        control.start(account="mmodad", client_dir=client)
    assert deck.spawned == [] and deck.signals == []


def test_on_linux_stop_refuses_while_the_game_is_open(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)
    deck.add("CrBrowserMain", PRIVATE)  # the game, as its Chromium renames it
    with pytest.raises(SingleServerError, match="still being played"):
        control.stop()
    assert deck.signals == []


def test_on_linux_stop_ends_the_server_its_gateway_and_the_network_and_nothing_else(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)
    run = control.server_dir / "run"
    server = int((run / "server.pid").read_text())
    holder = int((run / "holder.pid").read_text())
    gateway = server + 1
    stranger = deck.add("wireprobe-pvf.e", OURS)  # same name, another network: not ours
    lease = (
        control.server_dir / "src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3.admin-guard"
    )
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text(str(gateway), encoding="utf-8")

    assert control.stop() is True

    assert deck.signals[0] == ("group", server, signal.SIGINT)
    assert ("pid", gateway, signal.SIGTERM) in deck.signals, "the gateway is in its own group"
    assert deck.signals[-1] == ("pid", holder, signal.SIGTERM), "the network goes last"
    assert stranger in deck.alive_pids and all(s[1] != stranger for s in deck.signals)
    assert not lease.exists(), "the lock goes once its holder is gone"
    assert not (run / "holder.pid").exists() and not (run / "server.pid").exists()
    assert not (control.server_dir / RUN_RECORD).exists()
    assert control.status() == STOPPED


def test_on_linux_a_server_deaf_to_sigint_gets_sigterm_after_its_grace(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    deck.deaf_to_sigint = True
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)
    server = int((control.server_dir / "run" / "server.pid").read_text())
    assert control.stop() is True
    assert ("group", server, signal.SIGTERM) in deck.signals
    assert deck.now >= 15.0


def test_on_linux_a_reused_pid_with_another_name_is_not_our_server(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)
    server = int((control.server_dir / "run" / "server.pid").read_text())
    deck.comms[server] = "bash"
    assert control.status() == STOPPED


def test_on_linux_an_nsenter_that_is_refused_is_a_failed_start_not_a_started_one(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    deck.nsenter_refused = True
    control = _deck_controller(tmp_path, deck)
    with pytest.raises(SingleServerError, match="inside its private network"):
        control.start(account="mmodad", client_dir=client)
    holder = deck.spawned[0]
    assert holder["argv"][0] == "unshare"
    assert not (control.server_dir / RUN_RECORD).exists()
    assert not (control.server_dir / "run" / "holder.pid").exists()
    assert all(pid not in deck.alive_pids for pid, comm in deck.comms.items() if comm == "sleep")


def _crash_leaving_the_gateway(deck: LinuxHost, control: ProcessController) -> tuple[int, int]:
    run = control.server_dir / "run"
    server = int((run / "server.pid").read_text())
    gateway = server + 1
    deck.kill(server)  # the server crashed; its gateway (own session) did not
    lease = (
        control.server_dir / "src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3.admin-guard"
    )
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text(str(gateway), encoding="utf-8")
    return server, gateway


def test_on_linux_stop_after_a_crash_ends_the_gateway_it_left_and_its_lock(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)
    _server, gateway = _crash_leaving_the_gateway(deck, control)
    assert control.status() == STOPPED
    assert control.stop() is False
    assert ("pid", gateway, signal.SIGTERM) in deck.signals
    assert gateway not in deck.alive_pids
    assert not any(comm == "sleep" and pid in deck.alive_pids for pid, comm in deck.comms.items())
    lease = (
        control.server_dir / "src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3.admin-guard"
    )
    assert not lease.exists()


def test_on_linux_start_after_a_crash_clears_what_it_left_and_starts(
    tmp_path: Path, deck: LinuxHost, client: Path
) -> None:
    control = _deck_controller(tmp_path, deck)
    control.start(account="mmodad", client_dir=client)
    _server, gateway = _crash_leaving_the_gateway(deck, control)
    control.start(account="mmodad", client_dir=client)
    assert gateway not in deck.alive_pids
    assert len([s for s in deck.spawned if s["argv"][0] == "nsenter"]) == 2
    assert control.status() == STARTING
