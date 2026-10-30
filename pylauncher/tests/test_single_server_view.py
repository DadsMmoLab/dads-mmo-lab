"""Tests for the Server tab of a single-container install (`yulon.ui.single_server_view`).

Jobs run inline (`run_inline`), so a press and its answer happen inside the test; the
controller is a fake that records what it was asked.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

from yulon import gaming_mode, steam
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.play_launch import LaunchSpec
from yulon.single_controller import RUNNING, STOPPED, SingleServerError
from yulon.single_network import FirewallReport, Reach
from yulon.ui import single_server_view
from yulon.ui.single_server_view import ANOTHER_ADDRESS, SingleServerView
from yulon.ui.widgets.job import OnDone, OnError, Work, run_inline

DDON: SingleEntry = load_catalog().get_single("ddon")
DFO: SingleEntry = load_catalog().get_single("dfo")
REACHES = (
    Reach("loopback", "Only this computer", "127.0.0.1"),
    Reach("lan", "Computers on this network (192.168.0.60)", "192.168.0.60"),
)


@dataclass
class FakeController:
    server_dir: Path
    state: str = RUNNING
    address: str = "127.0.0.1"
    calls: list[str] = field(default_factory=list)
    fail_start: str = ""
    follows_with_cancel: bool = False
    running_for: str | None = None
    started_with: list[tuple[str, Path | None]] = field(default_factory=list)
    followed_with: list[threading.Event | None] = field(default_factory=list)
    entry: SingleEntry = DDON

    def status(self) -> str:
        return self.state

    def start(self, *, account: str = "", client_dir: Path | None = None) -> None:
        self.calls.append("start")
        self.started_with.append((account, client_dir))
        if self.fail_start:
            raise SingleServerError(self.fail_start)
        self.state = RUNNING

    def stop(self, **_kw: object) -> bool:
        self.calls.append("stop")
        self.state = STOPPED
        return True

    def follow_logs(self, cancel: threading.Event | None = None) -> Iterator[str]:
        self.followed_with.append(cancel)
        return iter(())

    def session_account(self) -> str | None:
        return self.running_for

    def advertised(self) -> str:
        return self.address

    def advertise(self, address: str) -> bool:
        self.calls.append(f"advertise {address}")
        self.address = address
        return False


@dataclass
class Said:
    warnings: list[tuple[str, str]] = field(default_factory=list)
    infos: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> Said:
    record = Said()
    monkeypatch.setattr(
        single_server_view, "show_warning", lambda _p, t, m: record.warnings.append((t, m))
    )
    monkeypatch.setattr(
        single_server_view, "show_information", lambda _p, t, m: record.infos.append((t, m))
    )
    return record


def _view(
    controller: FakeController,
    *,
    client_dir: Path | None = None,
    started: list[LaunchSpec] | None = None,
    firewall: list[str] | None = None,
    remembered: list[Path | None] | None = None,
    pick: Path | None = None,
    run_job: Callable[[Work, OnDone, OnError], None] = run_inline,
    entry: SingleEntry = DDON,
    account: str = "",
    accounts: list[str] | None = None,
    gaming: gaming_mode.GamingModeSeam | None = None,
    os_name: str = "windows",
) -> SingleServerView:
    def start_client(spec: LaunchSpec) -> bool:
        if started is not None:
            started.append(spec)
        return False

    def open_firewall(entry: SingleEntry, os_name: str) -> FirewallReport:
        if firewall is not None:
            firewall.append(os_name)
        return FirewallReport(added=entry.host_ports())

    view = SingleServerView(
        entry,
        controller,
        client_dir=client_dir,
        remember_client_dir=remembered.append if remembered is not None else None,
        account=account,
        remember_account=accounts.append if accounts is not None else None,
        run_job=run_job,
        poll_ms=60_000,
        reaches=lambda: REACHES,
        os_name=lambda: os_name,
        pick_dir=lambda _p, _t, _s: pick,
        start_client=start_client,
        open_firewall=open_firewall,
        gaming_mode_seam=gaming,
    )
    view.poll()
    if entry.advertise is not None:
        view.read_address()
    return view


def test_the_buttons_follow_what_the_server_is_doing(qapp: object, tmp_path: Path) -> None:
    control = FakeController(tmp_path)
    view = _view(control)
    assert view.realm_badge.status == RUNNING
    assert not view.start_button.isEnabled()
    assert view.stop_button.isEnabled() and view.play_button.isEnabled()
    control.state = STOPPED
    view.poll()
    assert view.start_button.isEnabled()
    assert not view.stop_button.isEnabled() and not view.play_button.isEnabled()
    view.shutdown()


def test_start_and_stop_ask_the_controller_and_then_look_again(
    qapp: object, tmp_path: Path
) -> None:
    control = FakeController(tmp_path, state=STOPPED)
    view = _view(control)
    view.start_server()
    assert control.calls == ["start"]
    assert view.realm_badge.status == RUNNING
    view.stop_server()
    assert control.calls == ["start", "stop"]
    assert view.realm_badge.status == STOPPED
    view.shutdown()


def test_a_failed_start_is_shown_and_reported_up(qapp: object, tmp_path: Path, said: Said) -> None:
    control = FakeController(tmp_path, state=STOPPED, fail_start="ac-worldserver is running")
    view = _view(control)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.start_server()
    assert failures == ["ac-worldserver is running"]
    assert said.warnings[0][0] == "DDON could not finish starting"
    assert view.busy_reason() is None
    view.shutdown()


def test_a_press_in_flight_holds_the_window_open(qapp: object, tmp_path: Path) -> None:
    held: list[tuple[Work, OnDone, OnError]] = []
    view = _view(FakeController(tmp_path, state=STOPPED), run_job=run_inline)
    view._run = lambda work, done, error: held.append((work, done, error))
    view.start_server()
    assert view.busy_reason() == "DDON is still starting."
    assert not view.start_button.isEnabled()
    work, done, _error = held[0]
    done(work())
    assert view.busy_reason() is None
    view.shutdown()


def test_the_address_clients_are_told_is_shown_and_selected(qapp: object, tmp_path: Path) -> None:
    view = _view(FakeController(tmp_path, address="192.168.0.60"))
    assert view.address_label.text().endswith("192.168.0.60")
    assert view.chosen_address() == "192.168.0.60"
    view.shutdown()


def test_the_lan_lookup_never_runs_on_the_gui_thread(qapp: object, tmp_path: Path) -> None:
    """`detect_lan_ip()` runs powershell.exe with an 8 s timeout: it is a job, not a build step.

    Jobs are held instead of run, so anything asked while the view is built is asked on the
    GUI thread. The LAN choice arrives with the job, before the address is matched to it.
    """
    held: list[tuple[Work, OnDone, OnError]] = []
    asked: list[str] = []

    def reaches() -> tuple[Reach, ...]:
        asked.append(threading.current_thread().name)
        return REACHES

    view = SingleServerView(
        DDON,
        FakeController(tmp_path, address="192.168.0.60"),  # type: ignore[arg-type]
        client_dir=None,
        run_job=lambda work, done, error: held.append((work, done, error)),
        poll_ms=60_000,
        reaches=reaches,
        os_name=lambda: "windows",
    )
    assert asked == [], "the LAN was looked up while the view was being built"
    assert view.reach_box.findData("192.168.0.60") == -1
    view.read_address()
    work, done, _error = held[-1]
    done(work())
    assert len(asked) == 1
    assert view.reach_box.findData("192.168.0.60") != -1
    assert view.chosen_address() == "192.168.0.60", "matched to the LAN choice, not 'another'"
    view.shutdown()


def test_an_address_not_offered_is_shown_as_another_address(qapp: object, tmp_path: Path) -> None:
    view = _view(FakeController(tmp_path, address="100.64.0.7"))
    assert view.reach_box.currentText() == ANOTHER_ADDRESS
    assert view.chosen_address() == "100.64.0.7"
    view.shutdown()


def test_choosing_this_network_advertises_it_and_opens_the_firewall(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    control = FakeController(tmp_path)
    firewall: list[str] = []
    view = _view(control, firewall=firewall)
    view.reach_box.setCurrentIndex(1)
    view.apply_network()
    assert control.calls == ["advertise 192.168.0.60"]
    assert firewall == ["windows"]
    assert said.infos and "52100" in said.infos[0][1]
    view.shutdown()


def test_only_this_computer_needs_no_firewall_rule(qapp: object, tmp_path: Path) -> None:
    control = FakeController(tmp_path, address="192.168.0.60")
    firewall: list[str] = []
    view = _view(control, firewall=firewall)
    view.reach_box.setCurrentIndex(0)
    view.apply_network()
    assert control.calls == ["advertise 127.0.0.1"]
    assert firewall == []
    view.shutdown()


def test_a_typed_address_that_is_not_ipv4_is_refused_before_anything_runs(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    control = FakeController(tmp_path)
    view = _view(control)
    view.reach_box.setCurrentIndex(view.reach_box.findData(None))
    view.other_address.setText("ddon.example.com")
    view.apply_network()
    assert control.calls == []
    assert said.warnings[0][0] == "That address cannot be used"
    view.shutdown()


def test_play_without_a_client_says_what_to_choose(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    started: list[LaunchSpec] = []
    view = _view(FakeController(tmp_path), started=started)
    view.account_edit.setText("mmodad")
    view.play()
    assert started == []
    assert "Choose your DDON client folder" in said.warnings[0][1]
    view.shutdown()


def test_play_starts_the_client_pointed_at_this_computer(qapp: object, tmp_path: Path) -> None:
    client = tmp_path / "client"
    client.mkdir()
    (client / "DDO.exe").write_bytes(b"MZ")
    started: list[LaunchSpec] = []
    view = _view(FakeController(tmp_path), client_dir=client, started=started)
    view.account_edit.setText("mmodad")
    view.play()
    (spec,) = started
    assert spec.argv[0] == str(client / "DDO.exe")
    assert "addr=127.0.0.1 port=52100 token=mmodad" in spec.argv[-1]
    view.shutdown()


def test_a_chosen_client_folder_is_remembered(qapp: object, tmp_path: Path) -> None:
    remembered: list[Path | None] = []
    view = _view(FakeController(tmp_path), remembered=remembered, pick=tmp_path / "ddon")
    view.choose_client_dir()
    assert remembered == [tmp_path / "ddon"]
    assert view.client_label.text() == f"Client: {tmp_path / 'ddon'}"
    view.shutdown()


def test_removal_asks_the_window_and_stops_before_it_forgets(qapp: object, tmp_path: Path) -> None:
    control = FakeController(tmp_path)
    view = _view(control)
    asked: list[tuple[str, object]] = []
    stopped: list[tuple[str, object, bool, str]] = []
    view.remove_requested.connect(lambda game, folder: asked.append((game, folder)))
    view.stopped_for_removal.connect(lambda *args: stopped.append(args))
    view.remove_button.click()
    assert asked == [("ddon", tmp_path)]
    view.stop_for_removal()
    assert control.calls == ["stop"]
    assert stopped == [("ddon", tmp_path, True, "")]
    view.shutdown()


# -- Add to DDON-Launcher -----------------------------------------------------------


def _client_with_launcher(tmp_path: Path) -> Path:
    client = tmp_path / "client"
    client.mkdir()
    (client / "DDO_Launcher.ini").write_bytes(b"[General]\r\nLobbyIP = dogmarising.org\r\n")
    return client


def test_add_to_launcher_lists_the_server_through_this_computer(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    """Through 127.0.0.1 even while the network is told the LAN address, as Play is."""
    client = _client_with_launcher(tmp_path)
    view = _view(FakeController(tmp_path, address="192.168.0.60"), client_dir=client)
    assert view.launcher_button.text() == "Add to DDON-Launcher"
    view.add_to_launcher()
    text = (client / "DDO_Launcher.ini").read_text(encoding="utf-8")
    assert text.startswith("[Yulon DDON]\nLobbyIP = 127.0.0.1\n")
    assert "[General]\nLobbyIP = dogmarising.org\n" in text
    ((title, message),) = said.infos
    assert title == "Added to DDON-Launcher"
    assert message.startswith("DDON-Launcher now lists Yulon DDON.")
    assert said.warnings == []
    assert view.busy_reason() is None
    view.shutdown()


def test_add_to_launcher_a_second_time_says_nothing_needed_changing(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    client = _client_with_launcher(tmp_path)
    view = _view(FakeController(tmp_path), client_dir=client)
    view.add_to_launcher()
    view.add_to_launcher()
    assert "already lists Yulon DDON" in said.infos[1][1]
    view.shutdown()


def test_add_to_launcher_without_its_file_says_how_to_get_one(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    client = tmp_path / "client"
    client.mkdir()
    failed: list[str] = []
    view = _view(FakeController(tmp_path), client_dir=client)
    view.action_failed.connect(failed.append)
    view.add_to_launcher()
    ((title, message),) = said.warnings
    assert title == "DDON was not added to DDON-Launcher"
    assert "Open DDON-Launcher from that folder once" in message
    assert failed == [message]
    assert list(client.iterdir()) == []
    view.shutdown()


def test_add_to_launcher_does_not_wait_for_the_server(qapp: object, tmp_path: Path) -> None:
    """A launcher entry is a file in the player's folder: a stopped server can be listed too."""
    view = _view(FakeController(tmp_path, state=STOPPED))
    assert not view.play_button.isEnabled()
    assert view.launcher_button.isEnabled()
    view.shutdown()


# -- a host-program server (DFO): this computer only, started for an account ---------


def _dfo_client(tmp_path: Path) -> Path:
    client = tmp_path / "client"
    client.mkdir()
    (client / "DFO.exe").write_bytes(b"MZ")
    return client


def test_a_server_with_no_address_to_advertise_says_this_computer_only(
    qapp: object, tmp_path: Path
) -> None:
    """No network choice, no Apply, and nothing asked about an address."""
    control = FakeController(tmp_path, entry=DFO, follows_with_cancel=True)
    view = _view(control, entry=DFO)
    assert view.apply_network_button is None
    assert not hasattr(view, "address_label")
    shown = [label.text() for label in view.findChildren(single_server_view.QLabel)]
    assert single_server_view.THIS_COMPUTER_ONLY in shown
    assert not any(call.startswith("advertise") for call in control.calls)
    view.shutdown()


def test_start_hands_a_host_program_server_the_account_and_the_client(
    qapp: object, tmp_path: Path
) -> None:
    client = _dfo_client(tmp_path)
    accounts: list[str] = []
    control = FakeController(tmp_path, state=STOPPED, entry=DFO, follows_with_cancel=True)
    view = _view(control, entry=DFO, client_dir=client, account="mmodad", accounts=accounts)
    view.start_server()
    assert control.started_with == [("mmodad", client)]
    assert accounts == ["mmodad"]
    view.shutdown()


def test_start_hands_a_container_server_the_same_and_it_reads_neither(
    qapp: object, tmp_path: Path
) -> None:
    """The container controller takes both arguments and ignores them (`SingleServer`)."""
    accounts: list[str] = []
    control = FakeController(tmp_path, state=STOPPED)
    view = _view(control, account="mmodad", accounts=accounts)
    view.start_server()
    assert control.started_with == [("mmodad", None)]
    assert accounts == []  # remembered at Play for DDON, as before
    view.shutdown()


def test_play_as_another_account_than_the_server_runs_for_is_refused(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    client = _dfo_client(tmp_path)
    started: list[LaunchSpec] = []
    control = FakeController(tmp_path, entry=DFO, running_for="mmodad")
    view = _view(control, entry=DFO, client_dir=client, started=started, account="someone")
    view.play()
    assert started == []
    (title, message), *_ = said.warnings
    assert "running for mmodad" in message and "someone" in message
    view.shutdown()


def test_play_starts_dfo_with_the_payload_its_launcher_builds(qapp: object, tmp_path: Path) -> None:
    """`3?<host>?7001?<account>?<32 zeros>?0?0?30?0?0?0`: upstream's `probePayload`, 7001 form."""
    client = _dfo_client(tmp_path)
    started: list[LaunchSpec] = []
    control = FakeController(tmp_path, entry=DFO, running_for="mmodad")
    view = _view(control, entry=DFO, client_dir=client, started=started, account="mmodad")
    view.play()
    (spec,) = started
    assert spec.argv[0] == str(client / "DFO.exe")
    assert spec.argv[-1] == "3?127.0.0.1?7001?mmodad?" + "0" * 32 + "?0?0?30?0?0?0"
    view.shutdown()


def test_play_on_linux_starts_dfo_through_its_client_script(qapp: object, tmp_path: Path) -> None:
    """On Linux DFO's game must join the server's private network: its script does that."""
    client = _dfo_client(tmp_path)
    started: list[LaunchSpec] = []
    control = FakeController(tmp_path, entry=DFO, running_for="mmodad")
    view = _view(
        control, entry=DFO, client_dir=client, started=started, account="mmodad", os_name="linux"
    )
    view.play()
    (spec,) = started
    assert spec.argv[:2] == ("/bin/bash", "-c") and spec.argv[3] == "dfo-client.sh"
    assert "exec nsenter -t" in spec.argv[2]
    assert spec.env["YULON_ACCOUNT"] == "mmodad"
    assert spec.env["YULON_CLIENT_DIR"] == str(client)
    view.shutdown()


def test_the_console_of_a_file_follow_is_given_an_event_that_ends_it(
    qapp: object, tmp_path: Path
) -> None:
    control = FakeController(tmp_path, entry=DFO, follows_with_cancel=True)
    view = _view(control, entry=DFO)
    view.console.wait(5000)
    (event,) = control.followed_with
    assert isinstance(event, threading.Event)
    view.shutdown()


def test_the_console_of_a_container_follow_is_given_no_event(qapp: object, tmp_path: Path) -> None:
    control = FakeController(tmp_path)
    view = _view(control)
    view.console.wait(5000)
    assert control.followed_with == [None]
    view.shutdown()


# -- Add to Steam Deck (Gaming Mode)… -----------------------------------------------------------


def test_no_gaming_mode_seam_draws_no_button(qapp: object, tmp_path: Path) -> None:
    """Off Linux `main.py` hands no seam, and the tab draws nothing for it."""
    view = _view(FakeController(tmp_path))
    assert view.findChild(QPushButton, "gaming-mode-ddon") is None
    view.shutdown()


def test_gaming_mode_hands_the_seam_the_client_and_account_and_remembers_it(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    asked: list[tuple[Path | None, str]] = []
    remembered: list[str] = []

    def seam(client_dir: Path | None, account: str) -> gaming_mode.GamingModeReport:
        asked.append((client_dir, account))
        scripts = tmp_path / "gaming-mode" / "ddon"
        return gaming_mode.GamingModeReport(
            entries=(
                "Dragon's Dogma Online Server",
                "Dragon's Dogma Online",
                "Dragon's Dogma Online Server Stop",
            ),
            scripts=(
                scripts / "ddo-launcher.sh",
                scripts / "ddo-client.sh",
                scripts / "ddo-stop.sh",
            ),
            lib="~/dml-launcher-lib.sh was installed (1.3.0).",
            path=tmp_path / "shortcuts.vdf",
            backup=None,
            replaced=False,
        )

    client = tmp_path / "client"
    view = _view(FakeController(tmp_path), client_dir=client, gaming=seam, accounts=remembered)
    assert view.gaming_mode_button.text() == gaming_mode.BUTTON
    view.account_edit.setText("mmodad")
    view.add_to_gaming_mode()
    assert asked == [(client, "mmodad")]
    assert remembered == ["mmodad"]
    ((title, message),) = said.infos
    assert title == "Added to Steam Deck"
    assert "Dragon's Dogma Online Server: starts the server" in message
    assert view.busy_reason() is None
    view.shutdown()


def test_a_gaming_mode_refusal_is_shown_and_reported_up(
    qapp: object, tmp_path: Path, said: Said
) -> None:
    def seam(client_dir: Path | None, account: str) -> gaming_mode.GamingModeReport:
        raise steam.SteamRefusal("Steam is running. Quit Steam completely.")

    failed: list[str] = []
    view = _view(FakeController(tmp_path), gaming=seam)
    view.action_failed.connect(failed.append)
    view.add_to_gaming_mode()
    ((title, message),) = said.warnings
    assert title == "DDON was not added to Steam"
    assert failed == [message] and "Steam is running" in message
    view.shutdown()
