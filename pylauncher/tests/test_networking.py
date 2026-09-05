"""Tests for networking auto-setup (`yulon.networking` + `platform` helpers, roadmap 3.4).

`plan()` is pure once its detection seams are pinned, so the LAN and internet
plans are asserted exactly; `apply()` runs against a recording runner and SQL
fake; the `docker.published_bindings()` parser and the firewall/portproxy
command builders are checked against the guide's literal commands.
"""

from __future__ import annotations

import dataclasses
import os
import ssl
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from yulon import docker, networking, platform, runner
from yulon.apply import ApplyError
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")

# What the fresh Windows 11 box actually raised; test_download.py keeps the
# verbatim message, this only needs the exception type.
_CERT_ERROR = ssl.SSLCertVerificationError("unable to get local issuer certificate")


def test_firewall_commands_match_the_guide() -> None:
    assert platform.firewall_commands("ufw", (3724, 8085), rule_prefix="X") == [
        ["ufw", "allow", "3724/tcp"],
        ["ufw", "allow", "8085/tcp"],
        ["ufw", "--force", "enable"],
    ]
    steam = platform.firewall_commands("ufw", (3724,), rule_prefix="X", steamos=True)
    assert steam[0] == ["steamos-readonly", "disable"] and steam[-1] == [
        "steamos-readonly",
        "enable",
    ]
    fwd = platform.firewall_commands("firewalld", (3724, 8085), rule_prefix="X")
    assert fwd[1] == ["firewall-cmd", "--permanent", "--add-port=3724/tcp"]
    assert fwd[-1] == ["firewall-cmd", "--reload"]
    win = platform.firewall_commands("netsh", (3724,), rule_prefix="AzerothCore")
    assert win == [
        [
            "netsh",
            "advfirewall",
            "firewall",
            "add",
            "rule",
            "name=AzerothCore 3724",
            "protocol=TCP",
            "dir=in",
            "localport=3724",
            "action=allow",
        ]
    ]
    assert platform.firewall_commands("none", (3724,), rule_prefix="X") == []


def test_portproxy_commands_forward_lan_ip_to_loopback() -> None:
    assert platform.portproxy_commands("192.168.1.25", (3724,)) == [
        [
            "netsh",
            "interface",
            "portproxy",
            "add",
            "v4tov4",
            "listenaddress=192.168.1.25",
            "listenport=3724",
            "connectaddress=127.0.0.1",
            "connectport=3724",
        ]
    ]


def test_detect_firewall_prefers_netsh_on_windows_then_ufw_then_firewalld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    assert (
        platform.detect_firewall(which=lambda n: "/usr/sbin/ufw" if n == "ufw" else None) == "ufw"
    )
    assert platform.detect_firewall(which=lambda n: "/x" if n == "firewall-cmd" else None) == (
        "firewalld"
    )
    assert platform.detect_firewall(which=lambda n: None) == "none"
    monkeypatch.setattr(platform.sys, "platform", "win32")
    assert platform.detect_firewall(which=lambda n: None) == "netsh"


def test_detect_public_ip_validates_and_falls_back() -> None:
    answers = {"https://a": "<html>oops</html>", "https://b": "98.24.105.7\n"}
    found = platform.detect_public_ip(lambda u: answers[u], services=("https://a", "https://b"))
    assert found == platform.PublicIpResult("98.24.105.7", False)

    def offline(url: str) -> str:
        raise OSError("no network")

    assert platform.detect_public_ip(offline, services=("https://a",)) == platform.PublicIpResult(
        None, False
    )


@pytest.mark.parametrize(
    "raised",
    [urllib.error.URLError(_CERT_ERROR), _CERT_ERROR],
    ids=["as urlopen raises it", "as a non-urllib seam might"],
)
def test_detect_public_ip_separates_a_bad_certificate_from_being_offline(
    raised: OSError,
) -> None:
    """The failure that used to be invisible: reached the service, refused to trust it.

    Both give address=None, and before this flag existed the networking report
    told a machine with an incomplete root store that it was offline.

    The first case is the one that matters: `urlopen` never lets an
    `ssl.SSLCertVerificationError` out, it re-raises it inside a
    `urllib.error.URLError`, and this test asserted only the bare shape — so it
    stayed green while the production path could not reach the flag at all
    (review finding, 2026-08-23). The shape is still hand-built here, because
    what this file tests is `plan()`'s reaction to the flag; test_download.py
    runs the same predicate against a real self-signed server so that no file
    has to take the shape on trust.
    """

    def cannot_verify(url: str) -> str:
        raise raised

    probe = platform.detect_public_ip(cannot_verify, services=("https://a", "https://b"))
    assert probe == platform.PublicIpResult(None, True)


@pytest.mark.parametrize(
    ("ip", "cgnat"), [("100.64.3.4", True), ("192.168.1.2", True), ("98.24.105.7", False)]
)
def test_is_cgnat(ip: str, cgnat: bool) -> None:
    assert platform.is_cgnat(ip) is cgnat


def test_published_bindings_parses_docker_ps_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    out = "0.0.0.0:3724->3724/tcp, [::]:3724->3724/tcp\n127.0.0.1:8085->8085/tcp\n3306/tcp\n"
    monkeypatch.setattr(
        runner,
        "run",
        lambda cmd, cwd=None, timeout=None: subprocess.CompletedProcess(cmd, 0, out, ""),
    )
    assert docker.published_bindings() == {3724: "0.0.0.0", 8085: "127.0.0.1"}


def test_realmlist_sql_per_core() -> None:
    assert networking.realmlist_sql(WOTLK, "98.24.105.7", "192.168.1.25") == (
        "UPDATE acore_auth.realmlist SET address='98.24.105.7', localAddress='192.168.1.25' "
        "WHERE id=1;"
    )
    # Tortoise's realmlist has no localAddress column (catalog data says so).
    assert networking.realmlist_sql(TORTOISE, "192.168.1.25", "192.168.1.25") == (
        "UPDATE tw_logon.realmlist SET address='192.168.1.25' WHERE id=1;"
    )
    with pytest.raises(ValueError, match="not an address"):
        networking.realmlist_sql(WOTLK, "1.2.3.4'; DROP TABLE x; --", None)


def test_lan_plan_is_fully_automatable() -> None:
    """Everything this plan proposes, `apply()` can do — and it proposes no enable.

    `warnings` carries exactly one sentence since bug-checklist §39: the
    disclosure that `ufw enable` was withheld. That is a statement about a
    command NOT run, not work handed to the user, so `manual_steps` is still
    empty and every command in the plan is still one the app executes itself.
    """
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        bindings={3724: "0.0.0.0", 8085: "0.0.0.0"},
    )
    assert p.ready and p.manual_steps == ()
    assert p.warnings == (networking.UFW_ENABLE_WITHHELD,)
    assert p.realmlist_sql is not None and "192.168.1.25" in p.realmlist_sql
    assert p.client_realmlist == "192.168.1.25"
    assert p.firewall_commands[0] == ("ufw", "allow", "3724/tcp")
    assert p.portproxy_commands == ()


def test_internet_plan_prompts_for_router_steps_and_flags_cgnat() -> None:
    p = networking.plan(
        WOTLK,
        "internet",
        lan_ip="192.168.1.25",
        public_ip="100.64.3.4",
        firewall="firewalld",
        steamos=False,
        wsl=False,
        # Named even though this test asserts nothing about firewall commands:
        # a firewalld plan reads the daemon's state, and a suite that lets that
        # reach the real `firewall-cmd` answers differently on the developer's
        # Fedora box than on CI.
        detect_firewalld=lambda: "unknown",
    )
    assert p.ready
    assert any("DHCP reservation" in s for s in p.manual_steps)
    assert any("forward TCP (not UDP) ports 3724 → 192.168.1.25:3724" in s for s in p.manual_steps)
    assert any("duckdns" in s for s in p.manual_steps)
    assert any("carrier-grade NAT" in w for w in p.warnings)
    assert p.realmlist_sql == (
        "UPDATE acore_auth.realmlist SET address='100.64.3.4', localAddress='192.168.1.25' "
        "WHERE id=1;"
    )


def test_loopback_bindings_warn_and_add_portproxy_on_windows_or_wsl() -> None:
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="netsh",
        steamos=False,
        wsl=False,
        bindings={3724: "127.0.0.1", 8085: "0.0.0.0"},
    )
    assert any("127.0.0.1" in w for w in p.warnings)
    assert p.portproxy_commands[0][:5] == ("netsh", "interface", "portproxy", "add", "v4tov4")
    assert any("Private" in s for s in p.manual_steps)
    linux = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        bindings={3724: "127.0.0.1"},
    )
    assert linux.portproxy_commands == () and any("127.0.0.1" in w for w in linux.warnings)


def test_missing_ips_make_the_plan_not_ready_with_clear_warnings() -> None:
    no_lan = networking.plan(
        WOTLK, "lan", firewall="none", steamos=False, wsl=False, detect_lan=lambda: None
    )
    assert no_lan.ready is False and no_lan.realmlist_sql is None
    assert any("LAN IP" in w for w in no_lan.warnings)
    assert any("allow inbound TCP 3724, 8085 by hand" in s for s in no_lan.manual_steps)
    no_pub = networking.plan(
        WOTLK,
        "internet",
        lan_ip="192.168.1.25",
        firewall="none",
        steamos=False,
        wsl=False,
        detect_public=lambda: platform.PublicIpResult(None),
    )
    assert no_pub.ready is False and no_pub.realmlist_sql is None
    assert any("public IP" in w and "offline?" in w for w in no_pub.warnings)


def test_an_unverifiable_lookup_is_not_reported_as_being_offline() -> None:
    """The user-facing half of the defect: the report used to misdiagnose the machine.

    A public-IP probe that failed its certificate check and one that found no
    route both end with public_ip=None; only the first one is fixed by Windows
    Update, and the report has to say which one happened or it sends the user to
    the router for a root-store problem.
    """
    p = networking.plan(
        WOTLK,
        "internet",
        lan_ip="192.168.1.25",
        firewall="none",
        steamos=False,
        wsl=False,
        detect_public=lambda: platform.PublicIpResult(None, verification_failed=True),
    )
    assert p.ready is False and p.realmlist_sql is None
    cert = [w for w in p.warnings if "certificate" in w]
    assert len(cert) == 1
    assert "not the same as being offline" in cert[0]
    assert platform.CERT_VERIFY_FIX in cert[0]
    assert not any("offline?" in w for w in p.warnings)


class _RecordingSql:
    def __init__(self) -> None:
        self.statements: list[tuple[str, str]] = []

    def run_file(self, db: str, path: Path) -> None:  # pragma: no cover - unused here
        raise AssertionError

    def run_statement(self, db: str, statement: str) -> None:
        self.statements.append((db, statement))


def test_apply_runs_firewall_under_sudo_n_updates_realmlist_and_reports_skips() -> None:
    """The enable path, on the box where enabling is safe: nothing listens for SSH.

    Asked for explicitly since bug-checklist §39, because a plan no longer
    proposes `ufw --force enable` on its own — and this test is about what
    `apply()` does with a command that needs a password sudo -n cannot give,
    which needs a command that can fail that way.
    """
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        enable_firewall=True,
        detect_ssh=networking.SshRoute,
    )
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        # Pretend `ufw enable` needs a password sudo -n cannot give.
        rc = 1 if argv[-2:] == ["--force", "enable"] else 0
        return subprocess.CompletedProcess(argv, rc, "", "sudo: a password is required")

    sql = _RecordingSql()
    report = networking.apply(p, sql=sql, run=run)
    assert seen[0] == ["sudo", "-n", "ufw", "allow", "3724/tcp"]
    assert report.done[:2] == ("ufw allow 3724/tcp", "ufw allow 8085/tcp")
    assert any(
        s.startswith("ufw --force enable: exit 1") and "with sudo" in s for s in report.skipped
    )
    assert sql.statements == [("auth", p.realmlist_sql)]
    assert report.done[-1] == "realmlist → 192.168.1.25"
    assert report.restart_required is True

    # Without DB access the UPDATE is reported, not silently dropped.
    no_db = networking.apply(p, sql=None, run=run)
    assert any("realmlist not updated" in s for s in no_db.skipped)
    assert no_db.restart_required is False


def test_write_client_realmlist_retail_and_repack_layouts(tmp_path: Path) -> None:
    retail = tmp_path / "retail"
    f = retail / "Data" / "enUS" / "realmlist.wtf"
    f.parent.mkdir(parents=True)
    f.write_text("set realmlist logon.example.com\nset patchlist x\n", encoding="utf-8")
    out = networking.write_client_realmlist(retail, "192.168.1.25")
    assert out == f
    assert f.read_text(encoding="utf-8") == "set realmlist 192.168.1.25\nset patchlist x\n"

    repack = tmp_path / "repack"
    repack.mkdir()
    (repack / "realmlist.wtf").write_text("SET REALMLIST 127.0.0.1\n", encoding="utf-8")
    out2 = networking.write_client_realmlist(repack, "10.0.0.5")
    assert out2 == repack / "realmlist.wtf"
    assert out2.read_text(encoding="utf-8") == "set realmlist 10.0.0.5\n"

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    out3 = networking.write_client_realmlist(fresh, "1.2.3.4")
    assert out3 == fresh / "Data" / "enUS" / "realmlist.wtf"


# --------------------------------------------------------- the macOS firewall
# Every one of these runs the real parsing against the strings `socketfilterfw`
# is documented to print. None of it has been run on a Mac — the checks that
# would settle each string are listed in `pyplan/checklist.md`, and until
# somebody runs them these tests pin our READING of the documentation, not the
# documentation's agreement with macOS.


class _Alf:
    """A fake `socketfilterfw` answering each getter with a canned line."""

    def __init__(self, state: str = "State = 1", block: str = "disabled", app: str = "") -> None:
        self.answers = {"--getglobalstate": state, "--getblockall": block, "--getappblocked": app}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        for flag, said in self.answers.items():
            if flag in argv:
                rc = 1 if said is None else 0
                return subprocess.CompletedProcess(argv, rc, said or "", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


@pytest.fixture
def _on_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine where `socketfilterfw` exists. Any real file will do as the stand-in.

    The probe gates on the tool being present before it spawns anything, so the
    constant is pointed at a file that is there rather than the Path object
    being patched — instance attributes on `Path` are read-only.
    """
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_SOCKETFILTERFW", Path(platform.__file__))


def test_macos_gets_its_own_backend_and_no_port_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Mac used to fall into "none" and be told to install ufw.

    And `alf` emits no commands, which is the correct output rather than a gap:
    the macOS Application Firewall is per-application and has no port
    vocabulary, so "open TCP 3724" cannot be expressed in it at all.
    """
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    assert platform.detect_firewall() == "alf"
    assert platform.firewall_commands("alf", (3724, 8085), rule_prefix="X") == []


@pytest.mark.parametrize(
    ("state", "block", "app", "expect"),
    [
        ("State = 0", "disabled", "", "off — nothing is being blocked"),
        (
            "State = 1",
            "disabled",
            "com.docker.backend is not blocked",
            "is allowed to receive incoming",
        ),
        ("State = 2", "disabled", "not part of the firewall", "not in the allow list yet"),
        ("State = 1", "disabled", "com.docker.backend is blocked", "is BLOCKED from receiving"),
        ("State = 1", "block all non-essential", "", "block ALL incoming"),
    ],
)
def test_the_firewall_state_reads_back_as_the_line_a_person_sees(
    _on_a_mac: None, state: str, block: str, app: str, expect: str
) -> None:
    """Each documented output maps to one status line, and "not blocked" is not "blocked".

    That last one is the trap: `"blocked" in "not blocked"` is True, so the
    negative readings have to be tested first or every allowed app reads as
    blocked — which would tell a working machine it is broken.
    """
    said = platform.detect_alf_state(run=_Alf(state, block, app)).describe()
    assert expect in said, said


def test_an_unreadable_firewall_is_unchecked_and_says_so(_on_a_mac: None) -> None:
    """Three independent reads, and `None` is never rounded to either neighbour."""
    blind = _Alf()
    blind.answers["--getglobalstate"] = None  # type: ignore[assignment]
    state = platform.detect_alf_state(run=blind)
    assert state.enabled is None
    said = state.describe()
    assert "unchecked" in said and "not a pass" in said


def test_off_macos_the_probe_spawns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `socketfilterfw`, no subprocess — which is also what makes this safe to unit-test."""
    monkeypatch.setattr(platform, "_SOCKETFILTERFW", Path("/definitely/not/here/socketfilterfw"))

    def _never(_argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError("the macOS firewall probe ran something off macOS")

    assert platform.detect_alf_state(run=_never) == platform.AlfState()


def test_the_unblock_command_is_produced_to_show_never_to_run() -> None:
    """It needs root, and this path does not ask for passwords — so the user runs it.

    The app named is Docker Desktop's backend, not ours: the server listens
    inside Docker's VM and `com.docker.backend` is what holds the host socket,
    so allow-listing Yu'lon would be theatre.
    """
    (argv,) = platform.alf_unblock_commands()
    assert argv[0].endswith("socketfilterfw")
    assert argv[1] == "--unblockapp"
    assert argv[2].endswith("com.docker.backend")

    plan = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.5",
        firewall="alf",
        detect_alf=lambda: platform.AlfState(True, False, "blocked"),
    )
    assert plan.firewall_commands == ()  # nothing to run, ever
    assert any("run this yourself" in m and "--unblockapp" in m for m in plan.manual_steps)


def test_block_all_says_the_allow_list_cannot_help(_on_a_mac: None) -> None:
    """A per-app allowance is a dead letter under "block all", so the copy must not offer one."""
    plan = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.5",
        firewall="alf",
        detect_alf=lambda: platform.AlfState(True, True, "allowed"),
    )
    assert any("no matter what is allowed" in w for w in plan.warnings)
    assert not [m for m in plan.manual_steps if "--unblockapp" in m]


def test_a_working_mac_firewall_produces_no_advice_at_all(_on_a_mac: None) -> None:
    """Inventing a step for a machine that is fine is how a screen teaches people to ignore it."""
    for state in (platform.AlfState(False, None, None), platform.AlfState(True, False, "allowed")):
        plan = networking.plan(
            WOTLK, "lan", lan_ip="192.168.1.5", firewall="alf", detect_alf=lambda s=state: s
        )
        assert plan.warnings == ()
        assert not [m for m in plan.manual_steps if "firewall" in m.lower()]
        assert plan.firewall_state is not None


def test_a_mac_is_never_told_to_open_an_administrator_powershell() -> None:
    """The retry hint used to be a two-branch boolean whose else-branch was Windows advice.

    `none` sits with `netsh`, not with `alf`, and the distinction is not
    cosmetic: `none` is what a WSL2 distro with no ufw or firewall-cmd detects
    as, and the loopback path still queues `netsh` portproxy commands for it.
    Grouping it with `alf` dropped the privilege hint from exactly those — a
    regression the old boolean got right by accident, caught in review of the
    commit that introduced it.
    """
    assert platform.elevation_policy("ufw") == platform.ElevationPolicy(
        ("sudo", "-n"), " with sudo"
    )
    for windows_ish in ("netsh", "none"):
        assert (
            platform.elevation_policy(windows_ish).retry_hint == " in an Administrator PowerShell"
        )
    assert platform.elevation_policy("alf") == platform.ElevationPolicy()


@pytest.mark.parametrize("flag", ["--getglobalstate", "--getblockall"])
def test_a_getter_that_answers_something_unrecognised_is_unchecked(
    _on_a_mac: None, flag: str
) -> None:
    """ "The command succeeded" is not "the output was recognised", and merging them lies.

    `enabled` was `"state = 1" in said or "state = 2" in said`, so ANY wording
    the parser did not expect — a future macOS phrasing, an unanticipated
    locale — answered False, and `describe()` then said "off, nothing is being
    blocked, no rule is needed" about a machine that may be blocking every
    player. Worst outcome the design has, and it contradicted `AlfState`'s own
    docstring. Nothing tested it, because every case fed to the parser sat
    clearly on one side or the other (review, 2026-08-24).
    """
    alf = _Alf(state="State = 1", block="disabled", app="not blocked")
    alf.answers[flag] = "Firewall is in some state macOS has not documented"
    state = platform.detect_alf_state(run=alf)

    field = state.enabled if flag == "--getglobalstate" else state.block_all
    assert field is None, (flag, state)
    if flag == "--getglobalstate":
        # And it must not read as a machine that is fine.
        assert "unchecked" in state.describe() and "not a pass" in state.describe()


def test_a_mac_is_told_what_a_loopback_binding_actually_means(_on_a_mac: None) -> None:
    """No firewall change fixes a 127.0.0.1 binding, so the macOS copy must not imply one.

    The branch was wired and reachable and nothing exercised it: every other
    `alf` test passes no bindings at all (review, 2026-08-24).
    """
    plan = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.5",
        firewall="alf",
        bindings={WOTLK.ports.auth: "127.0.0.1", WOTLK.ports.world: "0.0.0.0"},
        detect_alf=lambda: platform.AlfState(True, False, "allowed"),
    )
    loopback = [w for w in plan.warnings if "127.0.0.1" in w]
    assert loopback, plan.warnings
    assert "No firewall change can fix a loopback binding" in loopback[0]
    assert "portproxy" not in loopback[0]  # that is the WSL remedy, not a Mac one
    assert plan.portproxy_commands == ()


class _FailingSql(_RecordingSql):
    """A `SqlRunner` whose realmlist UPDATE fails the way a wrong schema name does."""

    def run_statement(self, db: str, statement: str) -> None:
        super().run_statement(db, statement)
        raise ApplyError("SQL failed (inline -> tw_logon): ERROR 1054 Unknown column")


def test_a_failed_realmlist_update_does_not_throw_away_the_firewall_report() -> None:
    """The rules already applied are the half the user must not lose.

    Every other failure in `apply()` lands in `skipped`; the realmlist UPDATE
    raised straight out of the function, so a report of what DID run — ports
    opened, portproxy added, some of it needing a manual retry — went with it.
    """
    p = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="ufw", steamos=False, wsl=False
    )
    sql = _FailingSql()
    report = networking.apply(
        p, sql=sql, run=lambda argv: subprocess.CompletedProcess(argv, 0, "", "")
    )
    assert report.done[:2] == ("ufw allow 3724/tcp", "ufw allow 8085/tcp")
    assert sql.statements == [("auth", p.realmlist_sql)], "it must still have tried"
    assert any("realmlist not updated" in s and "Unknown column" in s for s in report.skipped)
    assert report.restart_required is False
    assert not any("realmlist →" in d for d in report.done)


def test_the_cmangos_cores_get_no_local_address_column_either() -> None:
    """CMaNGOS's realmlist has the MaNGOS column set, which has no `localAddress`.

    Tortoise already said so; TBC and Vanilla inherited AzerothCore's default and
    would have written a column their realmd schema does not have, failing with
    `ERROR 1054 Unknown column 'localAddress'` the moment the schema-name fix
    let the statement reach the server at all.
    """
    catalog = load_catalog()
    for game_id in ("wow-tbc", "wow-vanilla"):
        entry = catalog.get(game_id)
        assert entry.realmlist.local_address_column is None, game_id
        assert networking.realmlist_sql(entry, "98.24.105.7", "192.168.1.25") == (
            "UPDATE realmd.realmlist SET address='98.24.105.7' WHERE id=1;"
        ), game_id


# -- what may be advertised, and how to tell whether it already is ----------


@pytest.mark.parametrize(
    "given",
    [
        None,
        "",
        "   ",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "0.0.0.0",
        "localhost",
        "1.2.3.4'; DROP TABLE x; --",
        "192.168.1.25 8085",
    ],
)
def test_no_realm_may_advertise_an_address_that_names_the_machine_asking(given: str) -> None:
    """Every input `advertisable()` must answer None to, one per real source.

    The list is not decoration: `detect_lan_ip()` answers None for an unreadable
    route, its WSL branch answers "" for a PowerShell that printed nothing, and
    that same branch takes whatever Windows said WITHOUT the `127.` filter the
    local branch applies — so a loopback really can arrive here. The last two
    are `_sql_literal()`'s job, asked through this function so that a caller
    which has consulted it can build the UPDATE without a `ValueError`.

    Breaks on the mutation that matters: dropping the `127.` prefix test, or
    the `LOOPBACK` membership test, or the `_sql_literal()` call, each leaves at
    least one of these returning the input. A weaker version of this test that
    only passed `None` would survive all three.
    """
    assert networking.advertisable(given) is None


def test_an_address_other_machines_can_dial_survives_and_loses_its_whitespace() -> None:
    """The other half: the function must not refuse everything.

    Without this, `return None` is a passing implementation of `advertisable()`
    — and a passing implementation that leaves every realm on 127.0.0.1, which
    is the bug. The trailing newline is what a detector reading a command's
    stdout hands over, and `_sql_literal()` would call that "not an address".
    """
    assert networking.advertisable("100.78.24.50") == "100.78.24.50"
    assert networking.advertisable(" 192.168.1.25\n") == "192.168.1.25"
    assert networking.advertisable("wow.example.com") == "wow.example.com"


def test_the_row_is_read_on_every_column_the_update_writes() -> None:
    """The reader and the writer address the same columns, per core.

    A comparison over `address` alone would call a row unchanged while
    `localAddress` still said 127.0.0.1 — and AzerothCore hands `localAddress`
    to exactly the clients it decides are on the realm's own subnet, i.e. the
    LAN players this whole step exists for. So the query names both columns for
    WotLK and one for a core whose realmlist has no local column, which is what
    `realmlist_sql()` writes in each case.

    Mutating `realmlist_columns()` to return only `address_column` fails the
    first assertion; dropping the `local_address_column` guard fails the second
    with a column CMaNGOS's realmd does not have.
    """
    assert networking.realmlist_columns(WOTLK) == ("address", "localAddress")
    assert networking.realmlist_address_query(WOTLK) == (
        "SELECT address, localAddress FROM acore_auth.realmlist WHERE id=1;"
    )
    assert networking.realmlist_columns(TORTOISE) == ("address",)
    assert networking.realmlist_address_query(TORTOISE) == (
        "SELECT address FROM tw_logon.realmlist WHERE id=1;"
    )
    # The join that matters: every column the query reads is a column the
    # UPDATE writes, for every shipped core, so "the answer equals the address
    # in every field" really is "there is nothing to change".
    for entry in load_catalog().games:
        written = networking.realmlist_sql(entry, "10.0.0.9", "10.0.0.9")
        for column in networking.realmlist_columns(entry):
            assert f"{column}='10.0.0.9'" in written, (entry.id, column)


# ------------------------------------------ the firewall and the way back in
# bug-checklist §39, found by 7.1's own gate on 2026-09-04: `plan()` emitted
# `ufw allow 3724/tcp`, `ufw allow 8085/tcp`, `ufw --force enable` on every ufw
# box, and `ufw enable` brings up a default-DENY-incoming policy. On the
# headless server this feature exists for, that is the operator's SSH gone —
# with `report.skipped` and `report.manual_steps` both EMPTY, so nothing said
# so. Recovery took the hypervisor's synthetic keyboard.
#
# Every test here drives the SSH seam explicitly, because the whole defect was
# a plan that never asked.

_SS_LISTENING = (
    'LISTEN 0 4096   0.0.0.0:3724  0.0.0.0:*  users:(("docker-proxy",pid=9,fd=4))\n'
    'LISTEN 0 128    0.0.0.0:2022  0.0.0.0:*  users:(("sshd",pid=830,fd=3))\n'
    'LISTEN 0 128       [::]:2022     [::]:*  users:(("sshd",pid=830,fd=4))\n'
)
"""What `ss -H --listening --tcp --numeric --processes` prints on a box whose
sshd was moved off 22 — the case `ufw allow 22/tcp` would have got wrong.

docker-proxy beside it is PLACED since round 4: a named owner that is neither
an SSH daemon nor an init is not a hole, so this table settles as `({2022},
True)`. Round 3 read it as unsettled, and on the real boxes that reading
refused every reload — see `test_a_placed_stranger_is_not_a_hole`.
"""

_SS_LISTENING_ONLY_SSHD = (
    'LISTEN 0 128    0.0.0.0:2022  0.0.0.0:*  users:(("sshd",pid=830,fd=3))\n'
    'LISTEN 0 128       [::]:2022     [::]:*  users:(("sshd",pid=830,fd=4))\n'
)
"""`_SS_LISTENING` with its docker-proxy line removed: sshd and nothing else.

Under round 3's rule this was the only shape of table that settled in sshd's
favour; under round 4's it is one of many, and `_SS_LISTENING` settles the same
way. Kept because the guarded-path tests were written against it and the
verdict has not changed — only the reason it is not special has.
"""

_M910Q_UNPRIVILEGED = (
    "LISTEN 0      128                        0.0.0.0:22    0.0.0.0:*\n"
    "LISTEN 0      128                      127.0.0.1:5939  0.0.0.0:*\n"
    "LISTEN 0      4096                       0.0.0.0:3724  0.0.0.0:*\n"
    "LISTEN 0      4096                 127.0.0.53%lo:53    0.0.0.0:*\n"
    "LISTEN 0      4096                  100.78.24.50:45057 0.0.0.0:*\n"
    "LISTEN 0      4096                     127.0.0.1:3306  0.0.0.0:*\n"
    "LISTEN 0      4096                       0.0.0.0:8085  0.0.0.0:*\n"
    "LISTEN 0      128                      127.0.0.1:631   0.0.0.0:*\n"
    "LISTEN 0      128                           [::]:22       [::]:*\n"
    "LISTEN 0      10                               *:3389        *:*"
    ' users:(("gnome-remote-de",pid=1067,fd=8))\n'
    "LISTEN 0      4096                          [::]:3724     [::]:*\n"
    "LISTEN 0      128                          [::1]:631      [::]:*\n"
    "LISTEN 0      4096   [fd7a:115c:a1e0::dd3a:1833]:49179    [::]:*\n"
    "LISTEN 0      4096                          [::]:8085     [::]:*\n"
)
"""m910q, 2026-09-04, `ss --no-header --listening --tcp --numeric --processes` as `pk`.

Copied out of the terminal; only ss's right-hand column padding was trimmed so
the lines fit. This is the ordinary shape of the table on a real desktop and it
is the one that broke the first fix: sshd's two lines (`0.0.0.0:22`, `[::]:22`)
carry NO owner because sshd is root's and this probe is not, while one line —
GNOME's RDP listener, uid 1000, i.e. a socket this user owns — carries one. A
probe that concluded "I read the table" from any owner at all read that one
line and reported no sshd on a box whose sshd is running.
"""

_NAMESPACED_ROOT = _M910Q_UNPRIVILEGED.replace(' users:(("gnome-remote-de",pid=1067,fd=8))', "")
"""The same m910q table read by ROOT in a different PID namespace: no owners at all.

m910q, 2026-09-04, `sudo unshare --pid --fork --mount-proc .venv/bin/python`
with no container involved — euid 0, pid 1, pid ns 4026532875, and net ns
4026531840, which is the host's. `ss` therefore listed all 14 of the host's
listeners and `--processes` could name none of them, because `/proc` in that
namespace holds no host process. Derived from `_M910Q_UNPRIVILEGED` by dropping
its one owner rather than pasted separately, so the two fixtures cannot drift
into disagreeing about which sockets that machine has: what changes between them
is the reading, not the box.
"""

_NAMESPACED_ROOT_WITH_ITS_OWN_SSHD = (
    "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
    "LISTEN 0 5 0.0.0.0:8765 0.0.0.0:*\n"
    "LISTEN 0 128 127.0.0.1:5939 0.0.0.0:*\n"
    "LISTEN 0 4096 0.0.0.0:3724 0.0.0.0:*\n"
    "LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
    "LISTEN 0 4096 100.78.24.50:45057 0.0.0.0:*\n"
    "LISTEN 0 4096 127.0.0.1:3306 0.0.0.0:*\n"
    'LISTEN 0 128 127.0.0.1:2222 0.0.0.0:* users:(("sshd",pid=6,fd=4))\n'
    "LISTEN 0 4096 0.0.0.0:8085 0.0.0.0:*\n"
    "LISTEN 0 128 127.0.0.1:631 0.0.0.0:*\n"
    "LISTEN 0 128 [::]:22 [::]:*\n"
    "LISTEN 0 10 *:3389 *:*\n"
    "LISTEN 0 4096 [::]:3724 [::]:*\n"
    "LISTEN 0 128 [::1]:631 [::]:*\n"
    "LISTEN 0 4096 [fd7a:115c:a1e0::dd3a:1833]:49179 [::]:*\n"
    "LISTEN 0 4096 [::]:8085 [::]:*\n"
)
"""m910q, 2026-09-04, root in a PID namespace that has an sshd of ITS OWN — the third §39.

`sudo unshare --pid --fork --mount-proc`, then inside it a transient
`/usr/sbin/sshd -f` on 127.0.0.1:2222 (pid 6 of the namespace; it died with
the namespace), then the shipped `ss` argv — euid 0, pid 1, pid ns
4026532747, net ns 4026531840 == the host's. Copied out of the terminal with
only ss's column padding collapsed to single spaces (the parser splits on
whitespace). What this table has that `_NAMESPACED_ROOT` does not is one NAMED
line — the namespace's own sshd, whose `/proc` entry this probe can read — next
to fifteen it could not name, two of which are the host's real sshd on 22. It
is a container, an LXC guest, or a systemd-nspawn unit with host networking,
running its own sshd: not exotic.

Against it the second repair answered `_sshd_listening_ports -> ({2222}, True)`
and `plan(enable_firewall=True)` emitted `ufw allow 3724, 8085, 2222 ; ufw
--force enable` with `refusals 0`, under the warning "SSH (port 2222) is allowed
through it so this machine stays reachable". Port 22, where the operator was,
got no allow. The `unnamed` count that was supposed to refuse this was
discarded by `if ports: return ports, not unplaced`, which never called the
helper that read it.
"""

_SOCKET_ACTIVATED_SSHD = (
    "LISTEN 0      4096                             *:22          *:*"
    ' users:(("systemd",pid=1,fd=96))\n'
)
"""A listening socket held by systemd itself rather than by the daemon behind it.

Captured on yulon-ubuntu (2026-09-04) from a transient `.socket` unit, which
came back verbatim as `LISTEN 0 4096 *:2299 *:* users:(("systemd",pid=1,
fd=96))` — only the port is different here. It had to be captured that way
because that box runs ssh.socket AND ssh.service, so its own port-22 line names
both owners (`users:(("sshd",pid=17501,fd=3),("systemd",pid=1,fd=138))`). Where
only ssh.socket listens — Debian 13's default, Ubuntu 23.04's default — the
word `sshd` appears nowhere in the table, and sshd is nonetheless one connection
away from being started.
"""


_M910Q_ROOT = (
    'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=626,fd=3))\n'
    'LISTEN 0 5 0.0.0.0:8765 0.0.0.0:* users:(("python3",pid=1665627,fd=3))\n'
    'LISTEN 0 128 127.0.0.1:5939 0.0.0.0:* users:(("teamviewerd",pid=2754,fd=17))\n'
    'LISTEN 0 4096 0.0.0.0:3724 0.0.0.0:* users:(("docker-proxy",pid=1259133,fd=8))\n'
    'LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=410,fd=14))\n'
    'LISTEN 0 4096 100.78.24.50:45057 0.0.0.0:* users:(("tailscaled",pid=7526,fd=27))\n'
    'LISTEN 0 4096 127.0.0.1:3306 0.0.0.0:* users:(("docker-proxy",pid=1241546,fd=8))\n'
    'LISTEN 0 4096 0.0.0.0:8085 0.0.0.0:* users:(("docker-proxy",pid=1259161,fd=8))\n'
    'LISTEN 0 128 127.0.0.1:631 0.0.0.0:* users:(("cupsd",pid=843052,fd=7))\n'
    'LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=626,fd=4))\n'
    'LISTEN 0 10 *:3389 *:* users:(("gnome-remote-de",pid=1067,fd=8))\n'
    'LISTEN 0 4096 [::]:3724 [::]:* users:(("docker-proxy",pid=1259140,fd=8))\n'
    'LISTEN 0 128 [::1]:631 [::]:* users:(("cupsd",pid=843052,fd=6))\n'
    'LISTEN 0 4096 [fd7a:115c:a1e0::dd3a:1833]:49179 [::]:* users:(("tailscaled",pid=7526,fd=28))\n'
    'LISTEN 0 4096 [::]:8085 [::]:* users:(("docker-proxy",pid=1259168,fd=8))\n'
)
"""m910q, 2026-09-04 (round 4), `sudo ss --no-header --listening --tcp --numeric --processes`.

Copied out of the terminal with ss's column padding collapsed to single spaces
(the parser splits on whitespace). Fifteen
lines, fifteen named, sshd on 22 twice, and thirteen owners that are not sshd:
docker-proxy (the WoW stack's 3724/8085/3306), systemd-resolve's stub,
tailscaled, teamviewerd, cupsd, GNOME's RDP listener, and another lane's
`python3 -m http.server 8765` that happened to be up. This is what the server
this feature exists for looks like as root, and round 3's rule — settled only
when every named owner was sshd's — answered `({22}, False)` to it: the default
firewalld plan REFUSED its reload and dropped port 22 with it. Same on
yulon-ubuntu. A guard that refuses on the box it was built for is not a guard.
"""

_YULON_UBUNTU_ROOT = (
    'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=17501,fd=3),("systemd",pid=1,fd=138))\n'
    'LISTEN 0 4096 127.0.0.1:7878 0.0.0.0:* users:(("docker-proxy",pid=40550,fd=7))\n'
    'LISTEN 0 4096 0.0.0.0:3724 0.0.0.0:* users:(("docker-proxy",pid=40588,fd=7))\n'
    'LISTEN 0 4096 127.0.0.1:3306 0.0.0.0:* users:(("docker-proxy",pid=37646,fd=7))\n'
    'LISTEN 0 4096 127.0.0.1:33739 0.0.0.0:* users:(("containerd",pid=18852,fd=14))\n'
    'LISTEN 0 4096 127.0.0.1:631 0.0.0.0:* users:(("cupsd",pid=1216,fd=7))\n'
    'LISTEN 0 4096 127.0.0.54:53 0.0.0.0:* users:(("systemd-resolve",pid=805,fd=17))\n'
    'LISTEN 0 4096 0.0.0.0:8085 0.0.0.0:* users:(("docker-proxy",pid=40565,fd=7))\n'
    'LISTEN 0 4096 100.71.125.58:40030 0.0.0.0:* users:(("tailscaled",pid=21160,fd=27))\n'
    'LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=805,fd=15))\n'
    'LISTEN 0 4096 [::]:22 [::]:* users:(("sshd",pid=17501,fd=4),("systemd",pid=1,fd=139))\n'
    'LISTEN 0 4096 [::]:3724 [::]:* users:(("docker-proxy",pid=40596,fd=7))\n'
    'LISTEN 0 4096 [::]:8085 [::]:* users:(("docker-proxy",pid=40572,fd=7))\n'
    'LISTEN 0 4096 [::1]:631 [::]:* users:(("cupsd",pid=1216,fd=6))\n'
    "LISTEN 0 4096 [fd7a:115c:a1e0::312c:7d3b]:34825 [::]:* "
    'users:(("tailscaled",pid=21160,fd=31))\n'
)
"""yulon-ubuntu, 2026-09-04 (round 4), the same command as root, padding collapsed the same way;
ssh.socket AND ssh.service both active.

The port-22 lines carry TWO owners — `("sshd",pid=17501,...),("systemd",pid=1,...)`
— because the socket unit and the service both hold the listener. sshd is
named, so the line is placed and 22 is its port; the `systemd` beside it is
not a hole, because the hole a socket front stands for is "what will answer
here is unknown", and here it is known. `_SOCKET_ACTIVATED_SSHD` is the case
where systemd is ALONE on the line, and that one stays a refusal.
"""

_ONLY_GAME_SERVERS = (
    'LISTEN 0 5 0.0.0.0:8085 0.0.0.0:* users:(("python3",pid=13,fd=3))\n'
    'LISTEN 0 5 0.0.0.0:3724 0.0.0.0:* users:(("python3",pid=7,fd=3))\n'
)
"""A box with named listeners and no SSH daemon at all: fedora:41 on m910q, 2026-09-04.

The `fw41ssh` image with sshd NOT started and `python3 -m http.server` on 3724
and 8085 as the WoW stack's stand-in, read by root with the shipped `ss` argv
(iproute2 6.10.0). Every line named, none an SSH daemon's, none an init's.
This — and not an empty table — is what "there is no SSH here to lock out"
looks like, and it is the one shape on which the guide's commands run with
nothing said.
"""

_LONE_SYSTEMD_ON_22 = 'LISTEN 0 4096 *:22 *:* users:(("systemd",pid=1,fd=138))\n'
"""`_SOCKET_ACTIVATED_SSHD`'s shape on port 22 with yulon-ubuntu's pid-1 fd: the round-1 line.

What a Debian 13 / Ubuntu 23.04 default looks like before the first login of
the boot — `sshd` appears nowhere, and the next connection would have worked.
"""

_ACTIVE_ZONES_INTERNAL = "internal\n  interfaces: eth0\npublic (default)\n"
"""`firewall-cmd --get-active-zones`, firewalld 2.2.3 (fedora:41 on m910q, 2026-09-04),
eth0 bound to `internal` — the layout under which round 3's "resolved and
written" reload ended the ssh session."""

_ACTIVE_ZONES_DEFAULT_ONLY = "public (default)\n"
"""The same command with nothing bound: the default zone is listed on its own."""

_ACTIVE_ZONES_THREE = "trusted\n  sources: 10.9.9.0/24\nwork\n  interfaces: lo\npublic (default)\n"
"""The same command with a source-bound zone and an interface-bound one, eth0 unbound."""

_OFFLINE_LIST_ALL_ZONES = """FedoraServer
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: cockpit dhcpv6-client ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

FedoraWorkstation
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: dhcpv6-client samba-client ssh
  ports: 1025-65535/udp 1025-65535/tcp
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

block
  target: %%REJECT%%
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services:
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

dmz
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

drop
  target: DROP
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services:
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

external
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: ssh
  ports:
  protocols:
  forward: yes
  masquerade: yes
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

home
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: dhcpv6-client mdns samba-client ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

internal
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces: eth0
  sources:
  services: dhcpv6-client mdns samba-client ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

public (default)
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: dhcpv6-client mdns ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

trusted
  target: ACCEPT
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources: 10.9.9.0/24
  services:
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

work
  target: default
  ingress-priority: 0
  egress-priority: 0
  icmp-block-inversion: no
  interfaces:
  sources:
  services: dhcpv6-client mdns ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:

"""
"""`firewall-offline-cmd --list-all-zones`, verbatim, firewalld 2.2.3 with the daemon KILLED.

fedora:41 on m910q, 2026-09-04: eth0 bound permanently to `internal`, a
permanent source on `trusted`, and `firewall-cmd --state` answering "not
running" (rc 252) at the time; the trailing space ss leaves after an empty
`interfaces:` is dropped here. Eleven zones; three of them will be active when
the daemon starts — the two with a binding and the default — and the parser has
to find those three among the `ports:` and `services:` lines without mistaking
`FedoraWorkstation`'s `ports: 1025-65535/udp ...` for a binding.
"""


def _here() -> bool:
    """The namespace answer for every table this file stages: "yes, this machine".

    `_sshd_listening_ports()` settles a table only if it came from the network
    namespace whose sockets the firewall configuration governs
    (`in_host_network_namespace()`), and a staged table has no namespace of its
    own — the suite's own process does. Pinned here so that these fixtures
    assert what they are about (the SHAPE of the table) and the namespace rule
    is asserted where it is about the namespace: see
    `test_a_table_read_in_another_network_namespace_is_no_evidence`.
    """
    return True


def _route(**kwargs: object) -> networking.SshRoute:
    """`detect_ssh_route()` with the namespace answer pinned to `_here`."""
    kwargs.setdefault("in_host_namespace", _here)
    return networking.detect_ssh_route(**kwargs)  # type: ignore[arg-type]


def _ports(run: object, prefix: tuple[str, ...] = ()) -> tuple[set[int], bool]:
    """`_sshd_listening_ports()` with the namespace answer pinned to `_here`.

    Two values, not the three the function returns: these fixtures are about
    the SHAPE of the table, and the caller's verdict on a table is `settled and
    the reading was this machine's` — which is what `detect_ssh_route()`
    computes and what this returns. The third value is asserted on its own in
    the tests that are about which machine the reading came from.
    """
    found, settled, machine = networking._sshd_listening_ports(  # type: ignore[arg-type]
        run, prefix, in_host_namespace=_here
    )
    return found, settled and machine is True


def _no_ss(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """A machine with no `ss` at all: the probe cannot answer, and must say so."""
    raise FileNotFoundError(2, "No such file or directory", argv[0])


def _unprivileged_ss(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """`ss --processes` as a normal user: root's sockets are listed, their owners are not.

    The whole m910q table, not the one line it used to be. A fixture of a single
    ownerless line encoded half the condition — it was dml-arch, a WSL2 Arch box
    with no user-owned listener at all — and the half it left out is the half
    that broke the fix: a real desktop has at least one socket of the user's
    own, and one of those was enough to make the probe claim it had read the
    table. Anything asserted through this fixture is asserted against a machine
    somebody logs into.
    """
    return subprocess.CompletedProcess(argv, 0, _M910Q_UNPRIVILEGED, "")


def _ok(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, "", "")


def _ufw_plan(
    *,
    enable_firewall: bool = False,
    route: networking.SshRoute | None = None,
    steamos: bool = False,
) -> networking.NetworkPlan:
    """A LAN plan on a ufw box with only the SSH decision left to the caller."""
    answer = route if route is not None else networking.SshRoute()
    return networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=steamos,
        wsl=False,
        enable_firewall=enable_firewall,
        detect_ssh=lambda: answer,
    )


def _firewalld_plan(
    *,
    daemon: networking.FirewalldDaemon,
    enable_firewall: bool = False,
    route: networking.SshRoute | None = None,
    zones: tuple[str, ...] | None = ("public",),
    default_zone: str | None = None,
    zoning: networking.FirewalldZoning | None = None,
) -> networking.NetworkPlan:
    """A LAN plan on a firewalld box, with the daemon's state and zones named rather than probed.

    Named in every firewalld test: the state decides which tool writes the
    ports and the zones decide where, so a test that let either seam default
    would assert whatever the machine running the suite happens to have
    installed — and spawn `firewall-cmd` from a unit test to find out.

    The default `route` is `SshRoute()` — no port, not connected, table
    readable — which is what `detect_ssh_route()` returns for a table whose
    every listener was named and placed as not-SSH (`_ONLY_GAME_SERVERS`), and
    NOT for an empty table, which has been unsettled since round 4. It stands in
    for "nothing to preserve" here, and a test about the reload guard names its
    route rather than relying on it. The default `zones` is the layout of a box
    where nothing was bound: `public (default)` alone.

    `zones` names a box whose runtime and permanent zone bindings AGREE, which
    is every box in this file that is not about the disagreement; `zoning`
    takes the whole `FirewalldZoning` for the tests that are.
    """
    answer = route if route is not None else networking.SshRoute()
    read = zoning
    if read is None and zones is not None:
        # Both default-zone readings are the first zone: an ordinary box, where
        # the daemon's live default and `DefaultZone` in firewalld.conf are the
        # same name. A running daemon whose CONFIGURED default could not be read
        # refuses the reload (round 6), and that refusal is asserted by the
        # tests that are about it, not implied by every fixture in this file.
        named = default_zone or ("public" if "public" in zones else zones[0])
        read = networking.FirewalldZoning(
            write=zones,
            permanent=zones,
            runtime=zones,
            default_zone=named,
            configured_default_zone=named,
        )
    return networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="firewalld",
        steamos=False,
        wsl=False,
        enable_firewall=enable_firewall,
        detect_ssh=lambda: answer,
        detect_firewalld=lambda: daemon,
        detect_zones=lambda daemon: read,
    )


def test_the_lan_step_does_not_turn_the_firewall_on_by_itself() -> None:
    """The command that caused the lockout is not in the default plan at all.

    Opening the ports is what the user asked for; `ufw --force enable` is the
    one command in that block that can only SUBTRACT reachability, and it is
    not needed for the request either way — `ufw allow` applies immediately on
    an already-active ufw and is staged on an inactive one. So it is withheld,
    and the withholding is SAID, on both objects: the gate that found this read
    `report.skipped` and `report.manual_steps` and both were empty.
    """
    p = _ufw_plan()
    assert list(p.firewall_commands) == [
        ("ufw", "allow", "3724/tcp"),
        ("ufw", "allow", "8085/tcp"),
    ]
    assert p.refusals and any("enable" in r for r in p.refusals)
    assert any("enable" in w for w in p.warnings), "the GUI renders warnings, not refusals"
    report = networking.apply(p, sql=None, run=_ok)
    assert report.refusals == p.refusals
    assert all(r in report.skipped for r in p.refusals)


def test_the_default_plan_never_asks_the_machine_about_ssh() -> None:
    """No enable, no probe: nothing to guard, so no subprocess and no seam call."""

    def never() -> networking.SshRoute:
        raise AssertionError("probed the listener table for a plan that enables nothing")

    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        detect_ssh=never,
    )
    assert p.firewall_commands and p.ssh_ports == ()


def test_enabling_over_ssh_allows_the_port_that_session_arrived_on() -> None:
    """The other reachable path: enable, but never without the way back in.

    2222, not 22 — the port comes from the running system, so a moved sshd is
    covered and `sshd_config` (a file we do not own, with Match blocks and
    includes) never has to be parsed.
    """
    p = _ufw_plan(enable_firewall=True, route=networking.SshRoute(connected=True, ports=(2222,)))
    cmds = list(p.firewall_commands)
    assert ("ufw", "allow", "2222/tcp") in cmds
    assert cmds.index(("ufw", "allow", "2222/tcp")) < cmds.index(("ufw", "--force", "enable"))
    assert p.ssh_ports == (2222,)
    assert p.refusals == ()
    assert any("2222" in w for w in p.warnings), "a port opened for you is a thing to be told"


@pytest.mark.parametrize(
    ("route", "because"),
    [
        (networking.SshRoute(connected=True, ports=()), "SSH_CONNECTION"),
        (networking.SshRoute(listeners_readable=False), "listening"),
    ],
    ids=["remote session, port unknown", "listener table unreadable"],
)
def test_an_ssh_port_that_cannot_be_established_refuses_the_enable(
    route: networking.SshRoute, because: str
) -> None:
    """A port that cannot be established is a refusal, never a shrug-and-enable.

    The second case is the one an artifact would lie about: an `ss` that could
    not be read answers with no sshd ports, which looks exactly like a box that
    has no sshd — so the probe reports WHETHER it could see, and an unreadable
    table is not an empty one.
    """
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.refusals and because in " ".join(p.refusals)
    assert all(r in p.warnings for r in p.refusals)
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    report = networking.apply(p, sql=None, run=run)
    assert all("enable" not in a for argv in seen for a in argv)
    assert all(r in report.skipped for r in p.refusals)


def test_the_guides_three_commands_need_a_table_that_ruled_sshd_out() -> None:
    """The enable branch, reached from a READING rather than from the default.

    This replaces `test_a_box_with_no_ssh_at_all_enables_exactly_what_the_guide
    _says`, whose argument has been withdrawn rather than edited. That test
    passed `SshRoute()` — the default, every field's fallback — and asserted
    that it means "a box with no ssh at all", so it asserted that the plan
    SHOULD emit the §39 command list for it. On m910q the probe returned exactly
    that value from a box running sshd, and the test would have called the
    lockout correct.

    So the route here is read out of a socket table, and the table is the one
    shape that means "there is no way in to preserve": listeners, every one of
    them named, none of them an SSH daemon's or an init's — `_ONLY_GAME_SERVERS`,
    a real fedora:41 root table with the game-port stand-ins and no sshd. For
    such a machine the guide's three commands run, with no sentence printed,
    because inventing advice for a machine that is fine is how a networking
    screen teaches people to ignore it.

    Until round 4 this test used an EMPTY table for that reading, on the
    argument that `ss` lists every socket in the namespace whatever the uid.
    It does — and the namespace was the wrong thing to be sure about; see
    `test_an_empty_socket_table_is_a_private_namespace_not_a_box_without_ssh`.
    """
    read_it_all = _route(
        environ={}, run=lambda argv: subprocess.CompletedProcess(argv, 0, _ONLY_GAME_SERVERS, "")
    )
    assert read_it_all == networking.SshRoute(connected=False, ports=(), listeners_readable=True)
    p = _ufw_plan(enable_firewall=True, route=read_it_all)
    assert list(p.firewall_commands) == [
        ("ufw", "allow", "3724/tcp"),
        ("ufw", "allow", "8085/tcp"),
        ("ufw", "--force", "enable"),
    ]
    assert p.refusals == () and p.warnings == () and p.manual_steps == ()
    # A table with listeners in it that could not be named is NOT that answer —
    # which is what the withdrawn test could not tell apart. One line, no owner:
    # something is listening and this probe has demonstrated nothing about what
    # it may read.
    one_unnamed = "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
    could_not_see = _route(
        environ={},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, one_unnamed, ""),
    )
    assert could_not_see.listeners_readable is False
    assert _ufw_plan(enable_firewall=True, route=could_not_see).refusals


def test_the_boot_time_enable_is_withheld_with_the_live_one() -> None:
    """SteamOS's `systemctl enable ufw` arms the same lockout one reboot later."""
    flat = [" ".join(c) for c in _ufw_plan(steamos=True).firewall_commands]
    assert "steamos-readonly disable" in flat and "steamos-readonly enable" in flat
    assert "ufw allow 3724/tcp" in flat
    assert "ufw --force enable" not in flat
    assert "systemctl enable ufw" not in flat


@pytest.mark.parametrize(
    ("command", "turns_it_on"),
    [
        (["ufw", "--force", "enable"], True),
        (["ufw", "enable"], True),
        (["systemctl", "enable", "ufw"], True),
        (["systemctl", "enable", "ufw.service"], True),
        (["systemctl", "enable", "--now", "ufw"], True),
        (["systemctl", "enable", "--now", "firewalld"], False),
        (["ufw", "allow", "3724/tcp"], False),
        (["steamos-readonly", "enable"], False),
        (["pacman", "-Sy", "--noconfirm", "ufw"], False),
        ([], False),
    ],
)
def test_what_counts_as_turning_the_firewall_on(command: list[str], turns_it_on: bool) -> None:
    """Enumerated rather than read off the one command that caused the outage.

    Three of the False rows are the ones a looser predicate gets wrong and each
    breaks something real: matching bare "enable" would swallow
    `steamos-readonly enable` and leave a SteamOS box read-only forever;
    matching any argv containing "ufw" would drop the pacman line that installs
    it; and matching `systemctl enable` without reading the unit would call
    firewalld's start a ufw enable, and then say "ufw" to a user who has no ufw.
    firewalld IS withheld — by `_starts_firewalld()`, which owns that argument
    and that vocabulary; this predicate stays about ufw.
    """
    assert networking._turns_ufw_on(command) is turns_it_on


def test_firewalld_is_the_same_bug_on_another_backend() -> None:
    """Withdrawn: "firewalld's default zone admits ssh" was true of port 22 only.

    This replaces `test_firewalld_is_not_the_same_bug_and_is_left_alone`, which
    asserted that `systemctl enable --now firewalld` runs unguarded because the
    `public` zone ships the `ssh` service allowed. That service is
    `/usr/lib/firewalld/services/ssh.xml`, and it is `22/tcp` and nothing else —
    so on a Fedora/Rocky/RHEL box whose admin moved sshd to 2222, which is
    routine hardening and exactly the case `SshRoute` exists for, starting
    firewalld admits 22 and drops 2222. A default zone of `drop` or `block` is
    the same outage with no allowance at all. The old test pinned that.

    So firewalld's start is withheld by default like ufw's enable, in firewalld's
    own words, and the port rules stay: they are what the user asked for. This
    is the `unknown` reading — the daemon answered on the bus but would not say
    what it was doing (exit 253, an unprivileged `--state` against a running
    daemon, which is what the shipped app gets on Fedora) — and there the
    commands are the ones the guide has always used.
    """
    p = _firewalld_plan(daemon="unknown")
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
        ("firewall-cmd", "--reload"),
    ]
    assert p.refusals == (networking.firewalld_start_withheld("unknown"),)
    assert p.refusals[0] in p.warnings, "the GUI renders warnings, not refusals"
    assert "firewall-offline-cmd" in p.refusals[0], "a state we could not read owes both commands"
    assert p.ssh_ports == () and p.firewalld_daemon == "unknown"
    report = networking.apply(p, sql=None, run=_ok)
    assert all(r in report.skipped for r in p.refusals)


def test_withholding_firewalld_s_start_must_not_withhold_the_ports() -> None:
    """The regression this whole backend was re-measured for: zero ports opened.

    Reproduced on m910q, 2026-09-04, against real firewalld 2.2.3-2.fc41 in a
    `fedora:41` container with the daemon down, driving the shipped `plan()` and
    `apply()` with the DEFAULT `enable_firewall=False` — the only thing the app
    can produce today:

        firewall-cmd --permanent --add-port=3724/tcp -> DBUS_ERROR, rc 36
        firewall-cmd --permanent --add-port=8085/tcp -> DBUS_ERROR, rc 36
        firewall-cmd --reload                        -> DBUS_ERROR, rc 36
        report.done = ()   ports open in the container = none

    …where the code one commit earlier ran `systemctl enable --now firewalld`
    first and opened both. The repair had brought firewalld under ufw's opt-in
    argument on the assumption that `--permanent` stages with the daemon down,
    the way `ufw allow` does with ufw inactive. It does not: firewalld's
    permanent configuration is edited THROUGH the daemon, over D-Bus.

    `firewall-offline-cmd` is the same package's tool for that state — measured
    on the same container: rc 0, and `--list-ports` then showed the port — so a
    stopped daemon gets the ports without anything being started, which is the
    plan the ufw analogy could not see.
    """
    p = _firewalld_plan(daemon="stopped")
    assert list(p.firewall_commands) == [
        ("firewall-offline-cmd", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-offline-cmd", "--zone=public", "--add-port=8085/tcp"),
    ], "the ports the user asked for, in the only vocabulary that works here"
    assert not any("systemctl" in c for cmd in p.firewall_commands for c in cmd)
    assert p.firewalld_daemon == "stopped"
    # `--reload` is dropped rather than translated: `firewall-offline-cmd` has
    # no such option, and a stopped firewalld loads the file when it starts.
    assert not any("--reload" in c for cmd in p.firewall_commands for c in cmd)
    withheld = p.refusals[0]
    assert "firewall-offline-cmd --zone=<zone> --add-port=<your ssh port>/tcp" in withheld
    assert "not running" in withheld, "the daemon being down is the fact that explains all of it"
    report = networking.apply(p, sql=None, run=_ok)
    assert report.done == (
        "firewall-offline-cmd --zone=public --add-port=3724/tcp",
        "firewall-offline-cmd --zone=public --add-port=8085/tcp",
    ), "an empty `done` was the regression"


def test_a_firewalld_that_is_already_running_is_told_nothing() -> None:
    """No refusal and no warning on a machine whose firewall is already on.

    The first repair printed "firewalld is left as you had it. To turn it on
    yourself…" on every firewalld plan, including the normal one where the
    daemon has been running since boot. There the sentence is not a refusal a
    person can act on — the policy the withheld command would bring up is
    already in effect — it is advice to do what has already been done, on the
    same screen as the real warnings. The start is still not run: the one thing
    it would change is boot persistence, which is a posture decision this module
    does not make for anybody.

    "Told nothing" is earned on the real thing: the route is read out of
    m910q's root table (`_M910Q_ROOT`), the SSH port it names goes in
    permanently, in the one active zone, BEFORE the reload, and then the
    machine is fine and is told so by silence. Round 3's version of this test
    read the route from an EMPTY table, which is now a refusal (round 4), and
    round 3's rule read this table as unsettled, which is now an allow.
    """
    the_server = _route(
        environ={}, run=lambda argv: subprocess.CompletedProcess(argv, 0, _M910Q_ROOT, "")
    )
    assert the_server == networking.SshRoute(connected=False, ports=(22,), listeners_readable=True)
    p = _firewalld_plan(daemon="running", route=the_server)
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=22/tcp"),
        ("firewall-cmd", "--reload"),
    ]
    assert p.refusals == () and p.warnings == () and p.manual_steps == ()
    assert p.ssh_ports == (22,) and p.firewalld_zones == ("public",)


def _sshd_on_2022() -> networking.SshRoute:
    """A route READ out of a table that names sshd on 2022 and nothing else.

    A shape of table that resolves in sshd's favour — the only one under round
    3's rule, one of many under round 4's — and the one every guarded-path test
    wants: a written-down `SshRoute(ports=(2022,))` would assert the guard
    against a value the probe may never produce.
    """
    route = _route(
        environ={},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, _SS_LISTENING_ONLY_SSHD, ""),
    )
    assert route == networking.SshRoute(ports=(2022,), listeners_readable=True)
    return route


def test_a_reload_on_a_running_daemon_writes_the_ssh_port_permanently_first() -> None:
    """BLOCKER B, closed: the reload is the same lockout, on the DEFAULT path.

    Measured with the shipped code on m910q, 2026-09-04, firewalld 2.2.3 in a
    fedora:41 container, from the host across the docker bridge. A listener on
    9999 kept reachable by a runtime-only `firewall-cmd --add-port=9999/tcp`
    (how an admin keeps a moved sshd alive while deciding) answered curl 200.
    With `route=SshRoute(connected=True, ports=(9999,), listeners_readable=True)`:

        daemon=running enable_firewall=False: --permanent 3724 ; --permanent 8085 ; --reload
           ssh_ports=() refusals=0 warnings=0
        apply(): done = (3724, 8085, --reload)
        runtime ports after: 2222 3724 4444 5555 6666 8085   <- 9999 gone
        curl after apply -> 000

    …while the `unknown`-daemon plan for the same route wrote `--permanent
    9999` first. The running-daemon early return in `_guard_the_way_back_in()`
    left before the route was consulted. Now the SSH port goes in permanently
    BEFORE the reload, `ssh_ports` carries it so `apply()` can prove it landed,
    and — because the machine is fine — nothing is said: no refusal, no warning,
    exactly as "already running" was promised, but earned.
    """
    p = _firewalld_plan(daemon="running", route=_sshd_on_2022())
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=2022/tcp"),
        ("firewall-cmd", "--reload"),
    ], "the SSH port, permanently, before the reload that drops the runtime allow"
    assert p.ssh_ports == (2022,)
    assert p.refusals == () and p.warnings == (), "resolved and written: told nothing"
    # `unknown` with the default `enable_firewall=False`: the start is withheld
    # with its sentence, and the reload is guarded exactly the same way.
    q = _firewalld_plan(daemon="unknown", route=_sshd_on_2022())
    assert list(q.firewall_commands) == list(p.firewall_commands)
    assert q.ssh_ports == (2022,)
    assert q.refusals == (networking.firewalld_start_withheld("unknown"),)
    # And `apply()` proves the rule ARRIVED before it reloads: a `--permanent`
    # SSH rule that failed and a reload that then runs is the runtime-only
    # allow gone with nothing to replace it.
    seen: list[list[str]] = []

    def rule_fails(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        rc = 1 if "--add-port=2022/tcp" in argv else 0
        return subprocess.CompletedProcess(argv, rc, "", "Error: INVALID_PORT")

    report = networking.apply(p, sql=None, run=rule_fails, elevate=False)
    assert ["firewall-cmd", "--reload"] not in seen, "the reload ran without the SSH rule"
    assert any("--reload" in r and "2022" in r and "REFUSED" in r for r in report.refusals)
    assert all(r in report.skipped for r in report.refusals)


def test_a_reload_with_an_unresolved_route_is_refused_and_says_why() -> None:
    """The other half of B: no port to write, so the reload does not run, and the user is told.

    The route here is the ordinary one on a Fedora desktop — the app's own uid,
    `ss` listing sshd's sockets with no owner on them — and it does not resolve.
    A reload on that reading could be the end of the operator's session, so the
    ports are written permanently and NOT reloaded, and the refusal says what a
    reload costs, what to run first, and that the ports are not in effect yet.
    Nothing here is gated on `enable_firewall`: this IS the production path.
    """
    unresolved = _route(environ={}, run=_unprivileged_ss)
    assert unresolved.listeners_readable is False
    p = _firewalld_plan(daemon="running", route=unresolved)
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
    ], "the request minus the one command that can cut a session"
    assert p.ssh_ports == ()
    assert len(p.refusals) == 1 and p.refusals[0] in p.warnings
    refusal = p.refusals[0]
    assert "REFUSED to run `firewall-cmd --reload`" in refusal
    assert "NOT in effect until a reload" in refusal
    assert "`sudo firewall-cmd --permanent --zone=<zone> --add-port=<your ssh port>/tcp`" in refusal
    assert "systemctl" not in refusal, "no start was asked for, so none is handed back"
    seen: list[list[str]] = []
    report = networking.apply(p, sql=None, run=lambda argv: (seen.append(argv), _ok(argv))[1])
    assert not any("--reload" in a for argv in seen for a in argv)
    assert refusal in report.skipped and refusal in report.refusals
    # With an enable asked for as well (`unknown` daemon, guide order), the two
    # dropped commands share ONE sentence, and the hand names both.
    both = _firewalld_plan(daemon="unknown", enable_firewall=True, route=unresolved)
    assert not any(networking._can_lock_out(c) for c in both.firewall_commands)
    assert len(both.refusals) == 1
    assert "REFUSED to enable firewalld and to run `firewall-cmd --reload`" in both.refusals[0]
    assert "systemctl enable --now firewalld && sudo firewall-cmd --reload" in both.refusals[0]


def test_the_default_firewalld_plan_asks_the_machine_about_ssh() -> None:
    """`detect_ssh` is consulted whenever a reload is in the list, `enable_firewall` or not.

    The ufw default asks nothing (`test_the_default_plan_never_asks_the_machine_
    about_ssh`), because a withheld enable is dropped whatever the route says.
    The firewalld default against a running or unreadable daemon keeps a
    reload, and a reload is decided on the route — so it asks, once. Against a
    stopped daemon the reload is dropped with the daemon's other commands, and
    there is nothing to ask about.
    """
    asked: list[str] = []

    def probe(when: str) -> networking.SshRoute:
        asked.append(when)
        return _sshd_on_2022()

    def planned(daemon: networking.FirewalldDaemon) -> networking.NetworkPlan:
        return networking.plan(
            WOTLK,
            "lan",
            lan_ip="192.168.1.25",
            firewall="firewalld",
            steamos=False,
            wsl=False,
            detect_ssh=lambda: probe(daemon),
            detect_firewalld=lambda: daemon,
            detect_zones=lambda daemon: networking.FirewalldZoning(
                write=("public",),
                permanent=("public",),
                runtime=("public",),
                default_zone="public",
                configured_default_zone="public",
            ),
        )

    planned("running")
    planned("unknown")
    assert asked == ["running", "unknown"]

    def never() -> networking.SshRoute:
        raise AssertionError("probed the listener table for a plan that reloads nothing")

    stopped = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="firewalld",
        steamos=False,
        wsl=False,
        detect_ssh=never,
        detect_firewalld=lambda: "stopped",
        detect_zones=lambda daemon: networking.FirewalldZoning(
            write=("public",),
            permanent=("public",),
            runtime=("public",),
            default_zone="public",
            configured_default_zone="public",
        ),
    )
    assert not any(networking._can_lock_out(c) for c in stopped.firewall_commands)


@pytest.mark.parametrize(
    ("command", "reloads"),
    [
        (["firewall-cmd", "--reload"], True),
        (["sudo", "-n", "firewall-cmd", "--reload"], True),
        (["firewall-cmd", "--permanent", "--add-port=3724/tcp"], False),
        (["firewall-offline-cmd", "--reload"], False),
        (["systemctl", "reload", "firewalld"], False),
        (["ufw", "reload"], False),
        ([], False),
    ],
)
def test_what_counts_as_a_firewalld_reload(command: list[str], reloads: bool) -> None:
    """The one command that drops runtime-only rules, and only that one.

    `firewall-offline-cmd --reload` does not exist (an argparse usage error —
    see `_offline_firewalld()`), `systemctl reload firewalld` is a different
    thing this module never emits, and `ufw reload` is another backend's word.
    A `--permanent` write is the request, not a reload. The elevated spelling
    is what `apply()` builds, and the predicate must see through it like the
    enable predicates do.
    """
    assert networking._reloads_firewalld(command) is reloads
    turns_on = networking._turns_a_firewall_on(command)
    assert networking._can_lock_out(command) is (reloads or turns_on)


def test_firewalld_asked_for_keeps_the_ssh_port_in_its_own_vocabulary() -> None:
    """The guard is per-backend in spelling and identical in argument.

    firewalld has no `ufw allow`, so the rule that keeps SSH reachable is
    `firewall-cmd --permanent --add-port=2222/tcp`, it goes in before the start,
    and `apply()` proves it ARRIVED before it lets the start run — the same
    proof as ufw's, asked in the language of the backend it is proving.

    The route is read out of a socket table rather than written down, because
    `SshRoute(connected=True, ports=(2222,))` alone no longer reaches this
    branch: a port with an unread table is refused (see
    `test_a_supplied_ssh_port_is_not_a_reading_of_the_socket_table`).
    """
    p = _firewalld_plan(daemon="unknown", enable_firewall=True, route=_sshd_on_2022())
    cmds = list(p.firewall_commands)
    guard = ("firewall-cmd", "--permanent", "--zone=public", "--add-port=2022/tcp")
    assert guard in cmds
    assert cmds.index(guard) < cmds.index(("systemctl", "enable", "--now", "firewalld"))
    assert cmds.index(guard) < cmds.index(("firewall-cmd", "--reload"))
    assert p.ssh_ports == (2022,) and p.refusals == ()

    seen: list[list[str]] = []

    def rule_fails(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        rc = 1 if "--add-port=2022/tcp" in argv else 0
        return subprocess.CompletedProcess(argv, rc, "", "Error: NOT_RUNNING")

    report = networking.apply(p, sql=None, run=rule_fails)
    assert not any(a == "systemctl" for argv in seen for a in argv), "the daemon was started"
    assert not any(a == "--reload" for argv in seen for a in argv), "the reload ran unproven"
    assert any("2022" in r and "REFUSED" in r for r in report.refusals)


def test_the_ssh_rule_is_spelled_for_the_daemon_the_plan_actually_read() -> None:
    """One spelling, chosen once, and `apply()` looks for the one that was chosen.

    The guard puts the SSH rule into the plan and `apply()` decides whether the
    start may run by finding that exact string in what SUCCEEDED. With the
    daemon down the rule is `firewall-offline-cmd`, so an `apply()` that
    re-derived it as `firewall-cmd --permanent` would look for a command that
    never ran and refuse a start it had every reason to allow — a guard that
    fires on its own bookkeeping teaches people to ignore it. The state travels
    on the plan for exactly that reason.

    The ORDER is asserted whole here, because getting the spelling right and the
    order wrong is a plan that opens nothing: the start moves to the END, which
    inverts the guide. `firewall-offline-cmd` writes the zone file, and a
    running firewalld holds its own copy and does not reread it — measured on
    the fedora:41 container, where an offline `--add-port=5555/tcp` against a
    live daemon returned 0 while `firewall-cmd --permanent --list-ports` never
    showed it. So every offline write has to land before anything starts. The
    first version of this repair left the start where the guide put it, first,
    and would have started the daemon and then written three files it ignores.
    """
    p = _firewalld_plan(daemon="stopped", enable_firewall=True, route=_sshd_on_2022())
    assert list(p.firewall_commands) == [
        ("firewall-offline-cmd", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-offline-cmd", "--zone=public", "--add-port=8085/tcp"),
        ("firewall-offline-cmd", "--zone=public", "--add-port=2022/tcp"),
        ("systemctl", "enable", "--now", "firewalld"),
    ], "every offline write, THEN the start"
    assert p.ssh_ports == (2022,) and p.refusals == ()
    ran: list[list[str]] = []
    report = networking.apply(
        p, sql=None, run=lambda argv: (ran.append(argv), _ok(argv))[1], elevate=False
    )
    assert ran[-1] == ["systemctl", "enable", "--now", "firewalld"], "rule arrived; let it start"
    assert report.refusals == ()


def test_a_firewall_cmd_that_cannot_reach_the_daemon_is_told_what_does_work() -> None:
    """ "Run it by hand with sudo" is the wrong advice for DBUS_ERROR.

    The exit statuses are measured, not guessed: firewalld 2.2.3-2.fc41 in a
    `fedora:41` container on m910q, 2026-09-04, with the daemon stopped —
    `firewall-cmd --permanent --add-port=2222/tcp` returned 252 with a system
    bus present and 36 with none, at uid 0 and at uid 1000 alike. Sudo is not
    what is missing in either case, so the same line run again by hand fails the
    same way; the sentence names `firewall-offline-cmd` instead.

    Reachable when the daemon stops between `plan()` and `apply()`, which is the
    only way a plan spelled in `firewall-cmd` meets a machine without one.
    """
    p = _firewalld_plan(daemon="running")

    def no_daemon(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 36, "", "Error: DBUS_ERROR: ...")

    report = networking.apply(p, sql=None, run=no_daemon)
    add = next(s for s in report.skipped if "--add-port=3724/tcp" in s)
    assert "sudo firewall-offline-cmd --zone=public --add-port=3724/tcp" in add
    reload_line = next(s for s in report.skipped if "--reload" in s)
    assert "nothing to reload" in reload_line, "there is no offline reload to send them to"
    assert all("firewall-offline-cmd --reload" not in s for s in report.skipped)


@pytest.mark.parametrize(
    ("returncode", "daemon"),
    [
        (0, "running"),
        (252, "stopped"),
        (36, "stopped"),
        (253, "unknown"),
        (1, "unknown"),
    ],
    ids=["running", "not running", "no system bus", "not authorized", "anything else"],
)
def test_the_firewalld_daemon_state_is_read_from_firewall_cmds_own_exit_status(
    returncode: int, daemon: networking.FirewalldDaemon
) -> None:
    """Every row measured on firewalld 2.2.3-2.fc41 in a fedora:41 container, 2026-09-04.

        daemon   system bus   uid     `firewall-cmd --state`
        running  up           0       "running"      rc 0
        running  up           1000    auth failure   rc 253
        stopped  up           0       "not running"  rc 252
        stopped  up           1000    "not running"  rc 252
        stopped  absent       0       DBUS_ERROR     rc 36
        stopped  absent       1000    DBUS_ERROR     rc 36

    253 is `unknown` rather than `running` on purpose, and the asymmetry in that
    table is why an unprivileged probe can be trusted at all: the only state it
    ever reads POSITIVELY is `stopped`, which is the only state whose commands
    change. 253 can only come back from a daemon that answered on the bus, but
    that is an inference; `unknown` keeps the daemon's own commands, which fail
    loudly and name the offline one if the inference was wrong.
    """
    seen: list[list[str]] = []

    def state(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, returncode, "", "")

    assert networking.detect_firewalld_daemon(run=state) is daemon
    assert seen == [["firewall-cmd", "--state"]]


@pytest.mark.parametrize(
    "boom",
    [
        FileNotFoundError(2, "No such file or directory", "firewall-cmd"),
        subprocess.TimeoutExpired("firewall-cmd", 5.0),
    ],
    ids=["no firewall-cmd", "wedged"],
)
def test_a_state_probe_that_cannot_run_is_unknown_not_stopped(boom: Exception) -> None:
    """A probe that never answered is not a reading of "the daemon is down".

    Which matters because `stopped` is the one answer that changes the tool:
    calling `firewall-offline-cmd` while the daemon is up returns 0 and writes a
    file the daemon does not see until a reload, so a guess in that direction is
    a success that is not one. `unknown` keeps `firewall-cmd`, which fails where
    it cannot work and says so.
    """

    def explode(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise boom

    assert networking.detect_firewalld_daemon(run=explode) == "unknown"


@pytest.mark.parametrize(
    ("command", "starts_it"),
    [
        (["systemctl", "enable", "--now", "firewalld"], True),
        (["systemctl", "start", "firewalld"], True),
        (["systemctl", "enable", "firewalld.service"], True),
        (["sudo", "-n", "systemctl", "enable", "--now", "firewalld"], True),
        (["systemctl", "enable", "ufw"], False),
        (["firewall-cmd", "--reload"], False),
        (["firewall-cmd", "--permanent", "--add-port=3724/tcp"], False),
        (["dnf", "install", "-y", "firewalld"], False),
        ([], False),
    ],
)
def test_what_counts_as_starting_firewalld(command: list[str], starts_it: bool) -> None:
    """Enumerated like ufw's, and the False rows are the ones that must keep running.

    `firewall-cmd --reload` and the `--add-port` lines are the request itself —
    withholding those would leave the user without the ports they asked for,
    which is the opposite failure — and the package-manager line is how the tool
    arrives in the first place. `enable` without `--now` is True for the same
    reason SteamOS's `systemctl enable ufw` is: a policy that lands at the next
    reboot is the same outage with nobody left to connect it to this button.
    """
    assert networking._starts_firewalld(command) is starts_it


@pytest.mark.parametrize(
    "command",
    [
        ["sudo", "ufw", "enable"],
        ["sudo", "-n", "ufw", "--force", "enable"],
        ["sudo", "-u", "root", "ufw", "enable"],
        ["doas", "ufw", "enable"],
        ["pkexec", "systemctl", "enable", "ufw"],
        ["sudo", "--", "ufw", "enable"],
    ],
    ids=["sudo", "sudo -n", "sudo -u root", "doas", "pkexec", "sudo --"],
)
def test_an_already_elevated_command_is_still_the_command(command: list[str]) -> None:
    """The predicates are anchored on argv[0], so the wrapper is stripped first.

    `['sudo', '-n', 'ufw', 'enable']` is what `apply()` builds one line after the
    guard reads the command, and what any caller assembling argv by hand would
    write. Anchored on argv[0] alone it is a program called "sudo", and it walks
    past both the plan-time strip and the apply-time guard. Nothing shipped is
    spelled this way today; the point is that the guard cannot be got round by
    spelling it differently tomorrow.
    """
    assert networking._turns_a_firewall_on(command) is True
    assert networking._unelevated(["sudo", "-u", "root", "ufw", "enable"]) == ["ufw", "enable"]


def test_the_guard_answers_a_command_list_that_enables_nothing() -> None:
    """Called directly, with no enable in the list: an answer, not a StopIteration.

    `next(i for i, c in ... if turns it on)` was total only because `plan()`
    checks the same predicate before calling — an invariant kept in a different
    function, which is one refactor from being an unhandled exception on the
    path whose whole job is not to break.

    The answer changed with the reload guard, and the new one is the right one:
    a list with nothing in it that can cut a session has nothing to guard, so
    no SSH allow is added for a guard that is not there and nothing is said.
    The old answer appended `ufw allow 2222/tcp` and warned that ufw "is being
    turned ON" for a list that turned nothing on.
    """
    guarded, ssh_ports, refusals, warnings = networking._guard_the_way_back_in(
        [["ufw", "allow", "3724/tcp"]],
        backend="ufw",
        enable_firewall=True,
        route=networking.SshRoute(connected=True, ports=(2222,)),
    )
    assert guarded == [["ufw", "allow", "3724/tcp"]]
    assert ssh_ports == () and refusals == [] and warnings == []


def test_a_named_sshd_next_to_unnamed_listeners_is_not_a_complete_answer() -> None:
    """BLOCKER A, closed at the CALL SITE, on the verbatim table that broke the third fix.

    This replaces two tests — `test_the_privilege_question_is_asked_of_the_
    process_not_of_the_output` and `test_a_listener_nobody_could_name_is_not_a_
    reading_whatever_the_euid` — that asserted the privilege helper
    `_reads_root_sockets()`: that it was given the pids and the holes, and that
    it refused on any hole. Both were true, and both were beside the point,
    because the helper was only called on the empty-`ports` branch and the
    table below took the other one. "Reviews check functions, not call sites":
    the fix was asserted where it lived and not where the plan was made.

    m910q, 2026-09-04, `sudo unshare --pid --fork --mount-proc` with a
    transient sshd of the namespace's own on 127.0.0.1:2222, shipped `ss`,
    real code (see `_NAMESPACED_ROOT_WITH_ITS_OWN_SSHD`):

        ss: 16 lines, 1 named, 15 UNNAMED
        _sshd_listening_ports -> ({2222}, True)
        detect_ssh_route      -> SshRoute(connected=False, ports=(2222,), listeners_readable=True)
        plan(enable_firewall=True) -> ufw allow 3724/tcp ; ufw allow 8085/tcp ;
                                      ufw allow 2222/tcp ; ufw --force enable
           ssh_ports (2222,) refusals 0 warnings 1
        W: ufw is being turned ON, and SSH (port 2222) is allowed through it so
           this machine stays reachable.

    0.0.0.0:22 and [::]:22 — the host's real sshd, where the operator was — are
    two of the fifteen, and got no allow. The rule is now one line at the
    probe's return, for the ports it found and for an empty answer alike: a
    table with ANY listener it could not name or place has not settled what it
    is complete about. The ports still come back — 2222 IS sshd's, and the
    refusal names it so the sentence helps — but not claiming to be all of it.
    """
    table = _NAMESPACED_ROOT_WITH_ITS_OWN_SSHD
    assert table.count("users:(") == 1 and table.count("\n") == 16
    assert "0.0.0.0:22 " in table and "[::]:22 " in table, "the host's sshd is on this table"
    run = lambda argv: subprocess.CompletedProcess(argv, 0, table, "")  # noqa: E731
    assert _ports(run) == ({2222}, False)
    route = _route(environ={}, run=run)
    assert route == networking.SshRoute(connected=False, ports=(2222,), listeners_readable=False)
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.ssh_ports == ()
    assert p.refusals and all(r in p.warnings for r in p.refusals)
    assert "2222" in p.refusals[0] and "not provably the only one" in p.refusals[0]
    # The same reading is the same verdict on the other backend's lockout
    # command — the reload — on the production path, with nothing asked for.
    q = _firewalld_plan(daemon="running", route=route)
    assert ("firewall-cmd", "--reload") not in q.firewall_commands
    assert q.refusals and "2222" in q.refusals[0]


@pytest.mark.parametrize(
    ("table", "ports", "settled"),
    [
        ("", set(), False),
        (_SS_LISTENING_ONLY_SSHD, {2022}, True),
        (_SS_LISTENING, {2022}, True),
        (_M910Q_ROOT, {22}, True),
        (_YULON_UBUNTU_ROOT, {22}, True),
        (_ONLY_GAME_SERVERS, set(), True),
        (_M910Q_UNPRIVILEGED, set(), False),
        (_NAMESPACED_ROOT, set(), False),
        (_NAMESPACED_ROOT_WITH_ITS_OWN_SSHD, {2222}, False),
        (_SOCKET_ACTIVATED_SSHD, set(), False),
        (_LONE_SYSTEMD_ON_22, set(), False),
        ("LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n", set(), False),
        ('LISTEN 0 128 users:(("sshd",pid=830,fd=3))\n', set(), False),
    ],
    ids=[
        "empty table",
        "sshd and nothing else",
        "sshd next to docker-proxy",
        "m910q as root",
        "yulon-ubuntu as root",
        "game servers, no sshd",
        "m910q as uid 1000",
        "m910q as namespaced root",
        "namespaced root with its own sshd",
        "socket-activated sshd",
        "systemd alone on 22",
        "one unnamed line",
        "sshd whose port will not parse",
    ],
)
def test_the_one_rule_that_settles_a_socket_table(
    table: str, ports: set[int], settled: bool
) -> None:
    """Every fixture this file holds, through the one gate, with what it decides.

    The rule: settled when the table has at least one line, every line was
    named, every SSH daemon's port parsed, and no owner is an init that could
    be fronting one. Enumerated rather than argued so the next fix cannot move
    the gate back into a helper one branch skips — a gate in one place answers
    the same way for a table with ports and a table without, and this is every
    shape of table this module has been measured against.

    Two rows flipped in round 4 and both flips were measured. "sshd next to
    docker-proxy" was False and is True: the real root tables of m910q and
    yulon-ubuntu have that shape and round 3's rule refused them both. "empty
    table" was True and is False: `sudo unshare --net` on m910q produced it
    from a host with fifteen listeners, and a reload issued from there reached
    the host's daemon.
    """
    run = lambda argv: subprocess.CompletedProcess(argv, 0, table, "")  # noqa: E731
    assert _ports(run) == (ports, settled)
    route = _route(environ={}, run=run)
    assert route.ports == tuple(sorted(ports)) and route.listeners_readable is settled
    p = _ufw_plan(enable_firewall=True, route=route)
    enabled = any("enable" in c for cmd in p.firewall_commands for c in cmd)
    if settled:
        assert enabled and p.refusals == () and p.ssh_ports == tuple(sorted(ports))
    else:
        assert not enabled and p.refusals and p.ssh_ports == ()


def test_root_in_a_pid_namespace_cannot_read_the_hosts_socket_owners() -> None:
    """The §39 route euid 0 waved through, driven through the whole path.

    The same m910q table, staged as a fixture so the verdict does not depend on
    the suite itself running as root inside a namespace. Every line is a real
    listener, one of them is sshd's, and not one carries an owner — which is
    exactly what `ss` prints when it can read the network namespace and not the
    `/proc` of the processes living in it. No privilege seam exists any more
    (see `detect_ssh_route()`), so the verdict rests on the fourteen unnamed
    lines alone and is the same whether or not the suite runs under sudo.
    """
    route = _route(
        environ={},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, _NAMESPACED_ROOT, ""),
    )
    assert route.ports == ()
    assert route.listeners_readable is False, "0.0.0.0:22 is listening and nobody could name it"
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.refusals and all(r in p.warnings for r in p.refusals)


def test_an_sshd_whose_port_will_not_parse_is_an_unknown_too() -> None:
    """sshd is HERE and its port is unreadable: the one line that must not be skipped.

    The parser used to `continue` past a malformed sshd line, which turns "sshd
    is listening on something I could not read" into "no sshd" — the same silent
    enable by a different route, on the one line that says outright that there
    is something to lose.
    """
    truncated = 'LISTEN 0 128 users:(("sshd",pid=830,fd=3))\n'
    route = _route(
        environ={},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, truncated, ""),
    )
    assert route.ports == () and route.listeners_readable is False


def test_the_ssh_port_comes_from_the_running_system_not_from_a_config_file() -> None:
    """`ss`, not `sshd_config`: the answer is what is LISTENING right now."""
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _SS_LISTENING_ONLY_SSHD, "")

    route = _route(environ={}, run=run)
    assert route == networking.SshRoute(connected=False, ports=(2022,), listeners_readable=True)
    assert calls and calls[0][0] == "ss"


def test_a_placed_stranger_is_not_a_hole() -> None:
    """ROUTE A, closed: the verbatim root tables of the two real boxes ALLOW, with 22 preserved.

    This replaces `test_a_port_we_found_is_not_all_the_ports_there_are`, whose
    argument has been withdrawn: it held that sshd on 2022 next to one
    docker-proxy line is "a machine that may also be reachable on a port about
    to be blocked", and pinned `({2022}, False)` for `_SS_LISTENING`. Measured
    through the real code as root on m910q and on yulon-ubuntu, 2026-09-04
    (`_M910Q_ROOT`, `_YULON_UBUNTU_ROOT`):

        15 lines, 15 named, sshd on 22
        _sshd_listening_ports -> ({22}, False)
        firewalld daemon=running, enable_firewall=False (THE DEFAULT PATH):
            firewall-cmd --permanent --add-port=3724/tcp
            firewall-cmd --permanent --add-port=8085/tcp
            ssh_ports=() refusals=1
            R: REFUSED to run `firewall-cmd --reload`: SSH is known to arrive on
               port 22, but this machine's listening sockets could not all be
               accounted for ...

    docker-proxy on 3724/8085/3306, systemd-resolve's stub, cupsd, tailscaled,
    teamviewerd: each one made the table "unplaced". The server this feature
    exists for always has docker-proxy or a worldserver on the game ports, and
    every Ubuntu has resolved's stub, so the rule refused on every box it could
    run on — root and uid 1000 alike — and a guard that never allows is a
    broken feature.

    What `unplaced` was FOR is round 1's finding: a socket-activated sshd whose
    listener is held by systemd pid 1 and reads as "no sshd here". That is the
    only hole a named owner can be: an init that could be fronting SSH, or an
    SSH daemon whose port did not parse. docker-proxy cannot front sshd. So a
    named owner that is neither is PLACED as not-SSH, and the two tables
    resolve to `({22}, True)`; yulon-ubuntu's port-22 lines name sshd AND
    systemd together, and sshd being named is what places them.
    """
    for box, table in (("m910q", _M910Q_ROOT), ("yulon-ubuntu", _YULON_UBUNTU_ROOT)):
        lines = [line for line in table.splitlines() if line.strip()]
        assert len(lines) == 15 and all("users:(" in line for line in lines), box
        run = lambda argv, t=table: subprocess.CompletedProcess(argv, 0, t, "")  # noqa: E731
        assert _ports(run) == ({22}, True), box
        route = _route(environ={}, run=run)
        assert route == networking.SshRoute(connected=False, ports=(22,), listeners_readable=True)
        # The production path: firewalld, running, nothing asked for. 22 is
        # written permanently, in the active zone, BEFORE the reload.
        p = _firewalld_plan(daemon="running", route=route)
        assert list(p.firewall_commands) == [
            ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
            ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
            ("firewall-cmd", "--permanent", "--zone=public", "--add-port=22/tcp"),
            ("firewall-cmd", "--reload"),
        ], box
        assert p.ssh_ports == (22,) and p.refusals == () and p.warnings == (), box
        # And the enable, when asked for: ufw allows 22 before it turns on.
        q = _ufw_plan(enable_firewall=True, route=route)
        cmds = list(q.firewall_commands)
        assert cmds.index(("ufw", "allow", "22/tcp")) < cmds.index(("ufw", "--force", "enable"))
        assert q.ssh_ports == (22,) and q.refusals == (), box
    # The line `unplaced` was built for still refuses: systemd ALONE on 22.
    alone = _route(
        environ={}, run=lambda argv: subprocess.CompletedProcess(argv, 0, _LONE_SYSTEMD_ON_22, "")
    )
    assert alone == networking.SshRoute(connected=False, ports=(), listeners_readable=False)
    assert ("firewall-cmd", "--reload") not in _firewalld_plan(
        daemon="running", route=alone
    ).firewall_commands


@pytest.mark.parametrize(
    ("line", "ports", "settled"),
    [
        ('LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=7,fd=3))\n', {22}, True),
        ('LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd-session",pid=7,fd=3))\n', {22}, True),
        ('LISTEN 0 128 0.0.0.0:2200 0.0.0.0:* users:(("dropbear",pid=7,fd=3))\n', {2200}, True),
        ('LISTEN 0 128 0.0.0.0:20000 0.0.0.0:* users:(("autossh",pid=7,fd=3))\n', {20000}, True),
        (
            "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* "
            'users:(("sshd",pid=17501,fd=3),("systemd",pid=1,fd=138))\n',
            {22},
            True,
        ),
        ('LISTEN 0 4096 *:22 *:* users:(("systemd",pid=1,fd=138))\n', set(), False),
        ('LISTEN 0 4096 *:22 *:* users:(("init",pid=1,fd=5))\n', set(), False),
        ('LISTEN 0 4096 *:22 *:* users:(("xinetd",pid=412,fd=5))\n', set(), False),
        ('LISTEN 0 4096 *:22 *:* users:(("inetd",pid=412,fd=5))\n', set(), False),
        ('LISTEN 0 4096 *:22 *:* users:(("s6-svscan",pid=1,fd=5))\n', set(), False),
        ('LISTEN 0 4096 0.0.0.0:3724 0.0.0.0:* users:(("docker-proxy",pid=9,fd=4))\n', set(), True),
        (
            'LISTEN 0 4096 0.0.0.0:53 0.0.0.0:* users:(("systemd-resolve",pid=410,fd=14))\n',
            set(),
            True,
        ),
        ('LISTEN 0 128 users:(("dropbear",pid=7,fd=3))\n', set(), False),
        ("LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(\n", set(), False),
    ],
    ids=[
        "sshd",
        "sshd-session (OpenSSH 9.8)",
        "dropbear",
        "autossh: over-allowed on purpose",
        "sshd and systemd on one line (yulon-ubuntu)",
        "systemd alone",
        "init alone",
        "xinetd",
        "inetd",
        "an init by another name, by pid",
        "docker-proxy: placed",
        "systemd-resolve is not systemd",
        "an SSH daemon whose port will not parse",
        "an owner column nothing could be read from",
    ],
)
def test_which_owners_are_placed_and_which_are_holes(
    line: str, ports: set[int], settled: bool
) -> None:
    """The placed/hole rule, one owner at a time, so a change to it is a visible diff here.

    Three rows carry the argument. `systemd-resolve` is not `systemd`: the
    front test is on the whole name, and it is resolved's stub on every Ubuntu
    that made round 3 refuse. `autossh` is over-matched by `ssh` and gets an
    allow it did not need — the mistake in the cheap direction. And an init by
    a name the list does not know is caught by pid 1, which costs a refusal
    rather than a lockout. The `users:(` column with nothing readable in it is
    a hole like an unnamed line: it says a listener is there and nothing else.
    """
    run = lambda argv: subprocess.CompletedProcess(argv, 0, line, "")  # noqa: E731
    assert _ports(run) == (ports, settled)


def test_an_empty_socket_table_is_a_private_namespace_not_a_box_without_ssh() -> None:
    """ROUTE B, closed: an empty table is UNRESOLVED, and the refusal names both commands.

    Rounds 1–3 held the empty table to be the one empty answer that settles
    anything: "`ss` lists every listening socket in the namespace whatever the
    probe's uid, so nothing is listening". Measured on m910q, 2026-09-04, from
    `sudo unshare --net`:

        ss rc=0, 0 lines  ->  _sshd_listening_ports -> (set(), True)
        ufw plan(enable_firewall=True): ufw allow 3724, 8085 ; ufw --force enable
           refusals=0
        firewalld default plan (daemon=running): ... ; firewall-cmd --reload
           refusals=0
        from the same namespace: systemctl is-active ssh -> active
        from the same namespace: firewall-cmd --reload -> success, and the
           host's runtime allow was gone afterwards

    The namespace's socket table is empty; the host's D-Bus, `/etc/ufw` and
    `/etc/firewalld` are all still reachable from it, so an enable written
    there is the host's next-boot policy. The machine with genuinely no
    listener loses a refusal that names the two commands; the other loses its
    way in.
    """
    empty = _route(environ={}, run=lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    assert empty == networking.SshRoute(connected=False, ports=(), listeners_readable=False)
    p = _firewalld_plan(daemon="running", route=empty)
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
    ], "the request minus the reload"
    assert len(p.refusals) == 1 and p.refusals[0] in p.warnings
    assert "REFUSED to run `firewall-cmd --reload`" in p.refusals[0]
    assert "listed NOTHING at all" in p.refusals[0] and "unshare --net" in p.refusals[0]
    q = _ufw_plan(enable_firewall=True, route=empty)
    assert all("enable" not in c for cmd in q.firewall_commands for c in cmd)
    assert q.refusals and "REFUSED to enable ufw" in q.refusals[0]
    assert "`sudo ufw allow <your ssh port>/tcp`, then `sudo ufw enable`" in q.refusals[0]
    # And the two together, when both are on the table: one sentence, both named.
    both = _firewalld_plan(daemon="unknown", enable_firewall=True, route=empty)
    assert not any(networking._can_lock_out(c) for c in both.firewall_commands)
    assert "REFUSED to enable firewalld and to run `firewall-cmd --reload`" in both.refusals[0]


def test_a_supplied_ssh_port_is_not_a_reading_of_the_socket_table() -> None:
    """`listeners_readable` is read on BOTH sides of the port test, not just the empty one.

    `if not asked.ports:` was the only branch that consulted it, so a port from
    `SSH_CONNECTION` discarded the flag built to mean "the table did not settle
    this". Measured on m910q as an ordinary desktop uid, 2026-09-04:

        euid=1000  SSH_CONNECTION="100.72.215.6 63739 100.78.24.50 22"
        detect_ssh_route() -> SshRoute(True, (22,), listeners_readable=False)
        plan(enable_firewall=True) -> ufw allow 3724/tcp, 8085/tcp, 22/tcp,
                                      ufw --force enable ; refusals ()

    `ss` contributed nothing there: sshd's lines are ownerless to uid 1000. So
    on a box also running sshd on 2222 — a port being migrated, a key-only admin
    port — the plan allows 22, enables ufw, and 2222 dies, and the probe that
    would have found it is the one that could not read the table. A supplied
    port is proof of ONE way in and no evidence at all that it is the only one.
    """
    route = _route(
        environ={"SSH_CONNECTION": "100.72.215.6 63739 100.78.24.50 22"}, run=_unprivileged_ss
    )
    assert route == networking.SshRoute(connected=True, ports=(22,), listeners_readable=False)
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.ssh_ports == ()
    assert p.refusals and all(r in p.warnings for r in p.refusals)
    assert "22" in p.refusals[0] and "not provably the only one" in p.refusals[0]


def test_a_wedged_ss_is_a_refusal_and_not_a_crash() -> None:
    """`subprocess.TimeoutExpired` is a SubprocessError and NOT an OSError.

    Measured on m910q, 2026-09-04: `issubclass(subprocess.TimeoutExpired,
    OSError)` is False, and `detect_ssh_route(environ={}, run=<raises
    TimeoutExpired>)` left through the top of the function as an unhandled
    exception rather than returning a refusal. `runner.run()` converts its own
    timeout into rc 124, which is why nothing caught it: the default seam never
    raises, and an injected runner or a direct `subprocess.run` does. A probe
    that never came back is the same fact as a probe that could not run.
    """
    assert not issubclass(subprocess.TimeoutExpired, OSError)

    def wedged(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, networking._SS_TIMEOUT_SECONDS)

    route = _route(environ={}, run=wedged)
    assert route == networking.SshRoute(connected=False, ports=(), listeners_readable=False)
    assert _ufw_plan(enable_firewall=True, route=route).refusals


def test_ssh_connection_names_the_port_this_session_actually_arrived_on() -> None:
    """Field four of `SSH_CONNECTION` is the server-side port sshd accepted us on.

    Which is the one port that provably admits the operator standing here, so
    it joins whatever the listener table said rather than replacing it.
    """
    route = _route(
        environ={"SSH_CONNECTION": "10.0.0.5 51234 10.0.0.9 2200"},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, _SS_LISTENING, ""),
    )
    assert route.connected is True
    assert route.ports == (2022, 2200)


def test_a_session_over_ssh_still_names_its_port_when_ss_is_missing() -> None:
    """The environment answers even when the probe cannot, and that is enough to proceed."""
    route = _route(environ={"SSH_CONNECTION": "10.0.0.5 51234 10.0.0.9 2200"}, run=_no_ss)
    assert route.ports == (2200,) and route.connected is True
    assert route.listeners_readable is False


@pytest.mark.parametrize(
    "run", [_no_ss, _unprivileged_ss], ids=["no ss on the box", "ss ran unprivileged"]
)
def test_a_listener_table_we_could_not_read_is_not_an_empty_one(run: networking.Runner) -> None:
    """Both of these used to look like "no sshd here", which is how a guess becomes a lockout.

    The second is the ordinary case, not an exotic one: `ss --processes` can
    only name the owner of a socket it is allowed to read, and sshd runs as
    root — so an unprivileged probe sees the socket and no name at all.
    """
    route = _route(environ={}, run=run)
    assert route.ports == () and route.listeners_readable is False


def test_a_socket_this_user_owns_is_not_a_reading_of_root_s_sockets() -> None:
    """The real m910q table: one owned line, and it is not sshd's.

    Reproduced on the box on 2026-09-04. `attributed = True` on the first line
    that carried ANY owner made GNOME's RDP listener — uid 1000, a socket this
    probe owns because it is this user's — stand for "the table was read", and
    sshd's two ownerless lines then read as "there is no sshd here". The route
    that came out was byte-identical to `SshRoute()`, the default, and the plan
    it produced was `ufw allow 3724/tcp`, `ufw allow 8085/tcp`, `ufw --force
    enable` with nothing on `refusals` — §39 verbatim, out of §39's own fix.

    So the question is not "did any line carry an owner" but "could this probe
    have named the owner of a socket ROOT holds", and one user-owned socket is
    no evidence of that either way.
    """

    def m910q(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, _M910Q_UNPRIVILEGED, "")

    route = _route(environ={}, run=m910q)
    assert route.ports == ()
    assert route.listeners_readable is False, "one socket this user owns is not the table"
    # The verdict rests on the table alone — thirteen unnamed lines and one
    # owner that is not sshd — and not on which uid happens to own pid 1067 on
    # the machine running the test: no privilege question is asked any more.
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.refusals and all(r in p.warnings for r in p.refusals)


def test_a_listener_owned_by_systemd_is_an_unknown_sshd_not_an_absent_one() -> None:
    """Socket activation puts pid 1 on sshd's port, and `"sshd` is nowhere in the line.

    Debian 13 and Ubuntu 23.04 ship ssh.socket enabled by default, and
    yulon-ubuntu has it enabled and active today. The listener is systemd's
    until the first connection arrives, so a root probe — which CAN read every
    socket — still finds no `sshd` token, and a rule that treats "no sshd token"
    as "no sshd" enables a default-deny firewall on a machine whose next SSH
    login was going to work. An owner this module cannot place is an unknown,
    and an unknown is a refusal.
    """
    route = _route(
        environ={},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, _SOCKET_ACTIVATED_SSHD, ""),
    )
    assert route.ports == ()
    assert route.listeners_readable is False, "systemd on port 22 is not a box without sshd"
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.refusals and all(r in p.warnings for r in p.refusals)


@pytest.mark.parametrize(
    ("value", "connected"),
    [
        ("", False),
        ("garbage", True),
        ("10.0.0.5 51234 10.0.0.9", True),
        ("10.0.0.5 51234 10.0.0.9 not-a-port", True),
    ],
)
def test_a_malformed_ssh_connection_is_still_a_session_over_ssh(
    value: str, connected: bool
) -> None:
    """No port from it, but the remoteness it declares is not thrown away.

    That split is the whole safety property: connected-with-no-port is exactly
    the case that must REFUSE, so a parser that raised, or that answered "not
    remote" on a shape it did not recognise, would enable ufw on the one box
    with the most to lose.
    """
    route = _route(environ={"SSH_CONNECTION": value}, run=_no_ss)
    assert route.connected is connected
    assert route.ports == ()


def test_apply_does_not_enable_ufw_when_the_ssh_rule_did_not_land() -> None:
    """The guard has to prove the rule ARRIVED, not that the plan declared it.

    `sudo -n ufw allow 2222/tcp` can fail on its own — a bad port, a ufw that
    refuses the rule — while the enable that follows it succeeds, and the plan
    would have been right about everything except the outcome.
    """
    p = _ufw_plan(enable_firewall=True, route=networking.SshRoute(connected=True, ports=(2222,)))
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        rc = 1 if "2222/tcp" in argv else 0
        return subprocess.CompletedProcess(argv, rc, "", "ERROR: Bad port")

    report = networking.apply(p, sql=None, run=run)
    assert ["sudo", "-n", "ufw", "--force", "enable"] not in seen
    assert any("2222" in s for s in report.skipped)
    assert any("SSH" in r and "enable" in r for r in report.refusals)


# ------------------------------------------------------------ firewalld zones (round 4, route C)
# Measured on firewalld 2.2.3 in a fedora:41 container on m910q, 2026-09-04,
# from a real ssh session across the docker bridge. eth0 bound to `internal`,
# sshd on 2222 kept alive by a runtime-only allow in that zone, and the route
# resolved on 2222 from the container's own `ss`:
#
#     DEFAULT plan: --permanent --add-port=3724/tcp ; ... 8085 ; ... 2222 ; --reload
#        ssh_ports=(2222,) refusals=0 warnings=0
#     ssh BEFORE apply: rc=0 ssh-alive
#     apply(): done = all four
#     internal ports after: runtime=[] permanent=[]
#     public   ports after: runtime=[2222/tcp 3724/tcp 8085/tcp] permanent=[same]
#     ssh AFTER apply: rc=255  ssh: connect to host 172.17.0.2 port 2222: No route to host
#     curl 3724 AFTER apply: 000
#
# `firewall-cmd --get-active-zones` printed exactly what was needed and the plan
# never asked it. Every test here drives the zone seam explicitly.


def test_the_ports_are_written_to_every_active_zone_before_the_reload() -> None:
    """ROUTE C, closed: one write per zone, SSH's among them, and `apply()` proves each landed.

    The zones are the measured layout — `internal` on eth0, `public` the
    default — and the plan spells the game ports and the SSH port once per
    zone, in the order the daemon listed them, with the reload last. The
    apply-time proof is per rule: an `internal` write that fails while the
    `public` one lands is a port still cut where the traffic is, so the reload
    does not run and the refusal names the rule that did not apply.
    """
    zones = networking.zones_from_listing(_ACTIVE_ZONES_INTERNAL, bound_only=False)
    assert zones == ("internal", "public")
    p = _firewalld_plan(daemon="running", route=_sshd_on_2022(), zones=zones)
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--zone=internal", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--zone=internal", "--add-port=8085/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=8085/tcp"),
        ("firewall-cmd", "--permanent", "--zone=internal", "--add-port=2022/tcp"),
        ("firewall-cmd", "--permanent", "--zone=public", "--add-port=2022/tcp"),
        ("firewall-cmd", "--reload"),
    ]
    assert p.ssh_ports == (2022,) and p.firewalld_zones == zones
    assert p.refusals == (), "resolved, zoned and written: nothing is refused"
    # Round 6: "told nothing" was the wrong half of that. Two zones means the
    # ports went somewhere the user did not name, so the plan names both.
    said = next(w for w in p.warnings if "game ports" in w)
    assert "`internal`" in said and "`public`" in said and "3724, 8085" in said
    assert "2022" in said, "and why the SSH port is in every one of them"

    seen: list[list[str]] = []

    def internal_fails(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        rc = 1 if argv[2:] == ["--zone=internal", "--add-port=2022/tcp"] else 0
        return subprocess.CompletedProcess(argv, rc, "", "Error: INVALID_PORT")

    report = networking.apply(p, sql=None, run=internal_fails, elevate=False)
    assert ["firewall-cmd", "--reload"] not in seen, "the reload ran with one zone unwritten"
    assert "firewall-cmd --permanent --zone=public --add-port=2022/tcp" in report.done
    refusal = next(r for r in report.refusals if "--reload" in r)
    assert "firewall-cmd --permanent --zone=internal --add-port=2022/tcp" in refusal
    assert (
        "--zone=public --add-port=2022/tcp" not in refusal
    ), "name the rule that failed, not the one that landed"
    # With every rule landed the reload runs, once, last.
    ran: list[list[str]] = []
    ok = networking.apply(
        p, sql=None, run=lambda argv: (ran.append(argv), _ok(argv))[1], elevate=False
    )
    assert ran[-1] == ["firewall-cmd", "--reload"] and ok.refusals == ()


def test_a_reload_is_refused_when_the_active_zones_cannot_be_read() -> None:
    """A known SSH port and an unknown zone layout is the coin-toss with a paper trail.

    The route is resolved — that is the point: round 3 would have written the
    port and reloaded — and the zones are None, which is what uid 1000 read in
    the container (rc 253, no polkit agent, and no passwordless sudo to elevate
    past it), what a stopped daemon answers `firewall-cmd` (rc 252/36), and what
    a probe that could not run answers. Since round 5 the reading that decides
    is the PERMANENT one, so that is the command the refusal names: sending a
    user to `--get-active-zones` sends them to the list a reload throws away.
    The reload is dropped, the refusal says which command to read the zones
    with and how to write each one, the game ports are still written (to the
    default zone, the daemon's own resolution of "no --zone") and a warning says
    that is where they went.
    """
    p = _firewalld_plan(daemon="running", route=_sshd_on_2022(), zones=None)
    assert list(p.firewall_commands) == [
        ("firewall-cmd", "--permanent", "--add-port=3724/tcp"),
        ("firewall-cmd", "--permanent", "--add-port=8085/tcp"),
    ], "the request, unzoned, minus the reload"
    assert p.ssh_ports == () and p.firewalld_zones is None
    assert len(p.refusals) == 1
    refusal = p.refusals[0]
    assert "REFUSED to run `firewall-cmd --reload`" in refusal
    assert "SSH arrives on port 2022" in refusal
    assert "permanent zone bindings could not be read" in refusal
    assert "`firewall-cmd --permanent --list-all-zones`" in refusal
    assert "`sudo firewall-cmd --permanent --zone=<zone> --add-port=<port>/tcp`" in refusal
    assert "No route to host" in refusal, "say what was measured, not what might happen"
    assert refusal in p.warnings
    assert any("written to the DEFAULT zone" in w for w in p.warnings if w != refusal)
    seen: list[list[str]] = []
    report = networking.apply(p, sql=None, run=lambda argv: (seen.append(argv), _ok(argv))[1])
    assert not any("--reload" in a for argv in seen for a in argv)
    assert refusal in report.skipped
    # A table that settled as "no SSH here" has nothing the zones could cut:
    # the reload runs, and only the default-zone warning is said.
    nothing_to_cut = _route(
        environ={}, run=lambda argv: subprocess.CompletedProcess(argv, 0, _ONLY_GAME_SERVERS, "")
    )
    q = _firewalld_plan(daemon="running", route=nothing_to_cut, zones=None)
    assert ("firewall-cmd", "--reload") in q.firewall_commands and q.refusals == ()
    assert len(q.warnings) == 1 and "DEFAULT zone" in q.warnings[0]


@pytest.mark.parametrize(
    ("text", "bound_only", "zones"),
    [
        (_ACTIVE_ZONES_DEFAULT_ONLY, False, ("public",)),
        (_ACTIVE_ZONES_INTERNAL, False, ("internal", "public")),
        (_ACTIVE_ZONES_THREE, False, ("trusted", "work", "public")),
        (_OFFLINE_LIST_ALL_ZONES, True, ("internal", "public", "trusted")),
        ("", False, ()),
    ],
    ids=[
        "nothing bound",
        "eth0 in internal",
        "source, interface, default",
        "offline files",
        "empty",
    ],
)
def test_zones_are_read_from_firewalld_s_own_listing(
    text: str, bound_only: bool, zones: tuple[str, ...]
) -> None:
    """Every listing shape that was measured, through the one parser, in the daemon's order.

    The offline row is the load-bearing one: eleven zones, three of which will
    be active at start — the two with a binding and the default — found among
    `ports:` and `services:` lines that must not read as bindings
    (`FedoraWorkstation` ships `ports: 1025-65535/udp ...` bound to nothing).
    """
    assert networking.zones_from_listing(text, bound_only=bound_only) == zones


_PERMANENT_LIST_ALL_ZONES = """internal
  target: default
  interfaces: eth0
  sources:
  services: dhcpv6-client mdns samba-client ssh
  ports:
public (default)
  target: default
  interfaces:
  sources:
  services: dhcpv6-client ssh
  ports:
work
  target: default
  interfaces:
  sources:
  services: dhcpv6-client ssh
  ports:
"""
"""`firewall-cmd --permanent --list-all-zones`, firewalld 2.2.3 in the fw5 container on m910q.

2026-09-05, eth0 bound PERMANENTLY to `internal`. The real answer lists all
eleven shipped zones with every field; three of them are kept here because the
parser reads only the headers and the `interfaces:`/`sources:` lines, and
because those three are the ones the runtime listing disagrees with. rc 0 as
root and behind `sudo -n`; rc 253 at uid 1000.
"""

_RUNTIME_ZONES_WORK = "work\n  interfaces: eth0\npublic (default)\n"
"""`firewall-cmd --get-active-zones` on the SAME box a moment later.

After `firewall-cmd --zone=work --change-interface=eth0` — a runtime-only move,
which is what an admin does to keep a moved sshd alive while deciding. This is
the list round 4 wrote its `--permanent` rules into, and the reload put eth0
back in `internal`.
"""

_FIREWALLD_CONF_FLUSH_YES = (
    "# comment\nDefaultZone=public\nFlushAllOnReload=yes\nIPv6_rpfilter=yes\n"
)
"""`/etc/firewalld/firewalld.conf` as shipped: line 73 is `FlushAllOnReload=yes`.

Trimmed to four lines; the file is root-only (`cat` at uid 1000 is "Permission
denied", rc 1, measured in the fw5 container), which is why it is read through
the same prefix as every other probe.
"""

_FIREWALLD_CONF_FLUSH_NO = _FIREWALLD_CONF_FLUSH_YES.replace("Reload=yes", "Reload=no")
"""The same file with the one setting that makes a runtime binding survive a reload."""

_PERMANENT_ARGV = ("firewall-cmd", "--permanent", "--list-all-zones")
_RUNTIME_ARGV = ("firewall-cmd", "--get-active-zones")
_OFFLINE_ARGV = ("firewall-offline-cmd", "--list-all-zones")
_CONF_ARGV = ("cat", "/etc/firewalld/firewalld.conf")


def _zone_probes(
    answers: dict[tuple[str, ...], tuple[int, str]],
) -> tuple[networking.Runner, list[list[str]]]:
    """A runner answering each zone probe by argv, and the list of what it was asked.

    An argv it was not told about is rc 1 with no output — the shape of a probe
    that failed — so a test that forgets one reads as unread rather than
    silently reusing another probe's answer, which is what a
    one-response-for-every-argv fake did before the reading was split in two.
    """
    seen: list[list[str]] = []

    def probe(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        code, out = answers.get(tuple(command), (1, ""))
        return subprocess.CompletedProcess(command, code, out, "")

    return probe, seen


@pytest.mark.parametrize(
    ("daemon", "answers", "write", "permanent", "runtime"),
    [
        (
            "running",
            {
                _PERMANENT_ARGV: (0, _PERMANENT_LIST_ALL_ZONES),
                _RUNTIME_ARGV: (0, _ACTIVE_ZONES_INTERNAL),
                _CONF_ARGV: (0, _FIREWALLD_CONF_FLUSH_YES),
            },
            ("internal", "public"),
            ("internal", "public"),
            ("internal", "public"),
        ),
        (
            "unknown",
            {
                _PERMANENT_ARGV: (0, _PERMANENT_LIST_ALL_ZONES),
                _RUNTIME_ARGV: (0, _RUNTIME_ZONES_WORK),
                _CONF_ARGV: (0, _FIREWALLD_CONF_FLUSH_YES),
            },
            ("internal", "public", "work"),
            ("internal", "public"),
            ("work", "public"),
        ),
        (
            "stopped",
            {
                _OFFLINE_ARGV: (0, _OFFLINE_LIST_ALL_ZONES),
                _CONF_ARGV: (0, _FIREWALLD_CONF_FLUSH_YES),
            },
            ("internal", "public", "trusted"),
            ("internal", "public", "trusted"),
            None,
        ),
        (
            "running",
            {_RUNTIME_ARGV: (0, _ACTIVE_ZONES_INTERNAL), _PERMANENT_ARGV: (253, "")},
            None,
            None,
            None,
        ),
        ("unknown", {_PERMANENT_ARGV: (252, "")}, None, None, None),
        ("unknown", {_PERMANENT_ARGV: (36, "")}, None, None, None),
        ("stopped", {_OFFLINE_ARGV: (255, "")}, None, None, None),
        ("running", {_PERMANENT_ARGV: (0, "")}, None, None, None),
        (
            "running",
            {
                _PERMANENT_ARGV: (0, _PERMANENT_LIST_ALL_ZONES),
                _RUNTIME_ARGV: (253, ""),
                _CONF_ARGV: (0, _FIREWALLD_CONF_FLUSH_YES),
            },
            ("internal", "public"),
            ("internal", "public"),
            None,
        ),
    ],
    ids=[
        "running, the two readings agree",
        "unknown, an interface moved at runtime",
        "stopped reads the files",
        "permanent not authorized (uid 1000, no polkit agent)",
        "not running",
        "no system bus",
        "offline needs root",
        "rc 0 with nothing listed",
        "runtime unread, permanent is enough",
    ],
)
def test_the_zone_probe_reads_the_bindings_a_reload_restores(
    daemon: networking.FirewalldDaemon,
    answers: dict[tuple[str, ...], tuple[int, str]],
    write: tuple[str, ...] | None,
    permanent: tuple[str, ...] | None,
    runtime: tuple[str, ...] | None,
) -> None:
    """The PERMANENT bindings decide, the runtime ones are added, and neither is guessed.

    Round 4 asked `--get-active-zones` and wrote `--permanent` rules into what
    it answered. Measured on firewalld 2.2.3 (fedora:41 on m910q, 2026-09-05)
    with eth0 permanently in `internal` and moved to `work` at runtime: the six
    port writes all succeeded, `--reload` ran, eth0 went back to `internal`,
    `internal` listed no ports, and ssh, curl and both game ports were dead —
    with `refusals=0` and `warnings=0`. So the permanent listing is the one
    that must be read, and it is the one whose failure returns None. Every exit
    status here is measured: 253 at uid 1000 without a polkit agent and without
    passwordless sudo to elevate past it, 252 with the daemon stopped and the
    bus up, 36 with no bus, 255 from the offline tool as uid 1000.

    A runtime listing that fails while the permanent one succeeds is NOT None:
    the zones the reload restores are known, which is what the writes need, and
    the only thing lost is the disagreement warning.
    """
    probe, seen = _zone_probes(answers)
    read = networking.detect_firewalld_zones(daemon, run=probe)
    if write is None:
        assert read is None
        return
    assert read is not None
    assert (read.write, read.permanent, read.runtime) == (write, permanent, runtime)
    first = tuple(seen[0])
    assert first == (
        _OFFLINE_ARGV if daemon == "stopped" else _PERMANENT_ARGV
    ), "the permanent bindings are read first and their failure is what returns None"


def test_the_zone_probe_carries_the_same_authority_as_the_writes() -> None:
    """Every zone probe goes out behind the prefix, because at uid 1000 none of them answer.

    Measured in the fw5 container on m910q, 2026-09-05, daemon running:
    `firewall-cmd --permanent --list-all-zones`, `--get-active-zones` and
    `--state` are each rc 253 `NotAuthorizedException: ...FirewallD1.info` at
    uid 1000, and each is rc 0 behind `sudo -n`.
    `/etc/firewalld/firewalld.conf` is root-only ("Permission denied", rc 1).
    """
    probe, seen = _zone_probes(
        {
            ("sudo", "-n", *_PERMANENT_ARGV): (0, _PERMANENT_LIST_ALL_ZONES),
            ("sudo", "-n", *_RUNTIME_ARGV): (0, _RUNTIME_ZONES_WORK),
            ("sudo", "-n", *_CONF_ARGV): (0, _FIREWALLD_CONF_FLUSH_YES),
        }
    )
    read = networking.detect_firewalld_zones("running", run=probe, prefix=("sudo", "-n"))
    assert read is not None and read.write == ("internal", "public", "work")
    assert seen and all(command[:2] == ["sudo", "-n"] for command in seen), seen


@pytest.mark.parametrize(
    ("conf", "flush"),
    [
        (_FIREWALLD_CONF_FLUSH_YES, True),
        (_FIREWALLD_CONF_FLUSH_NO, False),
        ("DefaultZone=public\n", None),
    ],
    ids=["yes (shipped)", "no", "not in the file"],
)
def test_whether_a_reload_keeps_the_runtime_bindings_is_read_not_assumed(
    conf: str, flush: bool | None
) -> None:
    """`FlushAllOnReload` decides which of the two readings survives a reload.

    Measured on firewalld 2.2.3 (fw5 on m910q, 2026-09-05), eth0 permanently in
    `internal` and moved to `work` at runtime:

        FlushAllOnReload  runtime eth0 after `firewall-cmd --reload`
        yes (shipped)     internal   — the permanent binding is restored
        no                work       — the runtime move survives

    A runtime-only PORT was dropped under both settings; it is the interface
    binding this setting governs. The ports are written to both sets of zones
    either way, so this changes what the user is TOLD, not where the rules go —
    which is why an unreadable file is None and not a guess.
    """
    probe, _ = _zone_probes(
        {
            _PERMANENT_ARGV: (0, _PERMANENT_LIST_ALL_ZONES),
            _RUNTIME_ARGV: (0, _RUNTIME_ZONES_WORK),
            _CONF_ARGV: (0, conf),
        }
    )
    read = networking.detect_firewalld_zones("running", run=probe)
    assert read is not None and read.flush_all_on_reload is flush


@pytest.mark.parametrize(
    "boom",
    [
        FileNotFoundError(2, "No such file or directory", "firewall-cmd"),
        subprocess.TimeoutExpired("firewall-cmd", 5.0),
    ],
    ids=["no firewall-cmd", "wedged"],
)
def test_a_zone_probe_that_cannot_run_is_unread_not_empty(boom: Exception) -> None:
    def explode(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise boom

    assert networking.detect_firewalld_zones("running", run=explode) is None


def test_a_stopped_daemon_writes_its_ssh_rule_to_every_zone_it_will_bring_up() -> None:
    """The enable path on a stopped daemon, with the zones read from the permanent files.

    `firewall-offline-cmd --get-active-zones` does not exist (rc 2, measured);
    what a stopped firewalld will apply is in `--list-all-zones` — the zones
    with an interface or a source under them, plus the default. The offline
    writes go to each of those, the start goes last, and when the files could
    not be read (uid 1000: "You need to be root", rc 255) the start is refused
    with the offline spelling of the hand.
    """
    zones = networking.zones_from_listing(_OFFLINE_LIST_ALL_ZONES, bound_only=True)
    p = _firewalld_plan(daemon="stopped", enable_firewall=True, route=_sshd_on_2022(), zones=zones)
    assert list(p.firewall_commands) == [
        ("firewall-offline-cmd", "--zone=internal", "--add-port=3724/tcp"),
        ("firewall-offline-cmd", "--zone=public", "--add-port=3724/tcp"),
        ("firewall-offline-cmd", "--zone=trusted", "--add-port=3724/tcp"),
        ("firewall-offline-cmd", "--zone=internal", "--add-port=8085/tcp"),
        ("firewall-offline-cmd", "--zone=public", "--add-port=8085/tcp"),
        ("firewall-offline-cmd", "--zone=trusted", "--add-port=8085/tcp"),
        ("firewall-offline-cmd", "--zone=internal", "--add-port=2022/tcp"),
        ("firewall-offline-cmd", "--zone=public", "--add-port=2022/tcp"),
        ("firewall-offline-cmd", "--zone=trusted", "--add-port=2022/tcp"),
        ("systemctl", "enable", "--now", "firewalld"),
    ]
    assert p.ssh_ports == (2022,) and p.refusals == ()
    unread = _firewalld_plan(
        daemon="stopped", enable_firewall=True, route=_sshd_on_2022(), zones=None
    )
    assert not any(networking._starts_firewalld(c) for c in unread.firewall_commands)
    assert "REFUSED to enable firewalld" in unread.refusals[0]
    assert "`sudo firewall-offline-cmd --list-all-zones`" in unread.refusals[0]
    assert "`sudo firewall-offline-cmd --zone=<zone> --add-port=<port>/tcp`" in unread.refusals[0]
    # The default path on a stopped daemon has no lockout command, so unread
    # zones cost a warning and nothing else: the ports are written, unzoned.
    quiet = _firewalld_plan(daemon="stopped", zones=None)
    assert list(quiet.firewall_commands) == [
        ("firewall-offline-cmd", "--add-port=3724/tcp"),
        ("firewall-offline-cmd", "--add-port=8085/tcp"),
    ]
    assert any("DEFAULT zone" in w for w in quiet.warnings)


@pytest.mark.parametrize(
    ("command", "zones", "expected"),
    [
        (
            ["firewall-cmd", "--permanent", "--add-port=3724/tcp"],
            ("internal", "public"),
            [
                ["firewall-cmd", "--permanent", "--zone=internal", "--add-port=3724/tcp"],
                ["firewall-cmd", "--permanent", "--zone=public", "--add-port=3724/tcp"],
            ],
        ),
        (
            ["firewall-offline-cmd", "--add-port=3724/tcp"],
            ("public",),
            [["firewall-offline-cmd", "--zone=public", "--add-port=3724/tcp"]],
        ),
        (
            ["firewall-cmd", "--permanent", "--add-port=3724/tcp"],
            None,
            [["firewall-cmd", "--permanent", "--add-port=3724/tcp"]],
        ),
        (["firewall-cmd", "--reload"], ("internal", "public"), [["firewall-cmd", "--reload"]]),
        (
            ["systemctl", "enable", "--now", "firewalld"],
            ("internal",),
            [["systemctl", "enable", "--now", "firewalld"]],
        ),
        (
            ["firewall-cmd", "--permanent", "--zone=work", "--add-port=1/tcp"],
            ("internal",),
            [["firewall-cmd", "--permanent", "--zone=work", "--add-port=1/tcp"]],
        ),
        (["ufw", "allow", "3724/tcp"], ("internal",), [["ufw", "allow", "3724/tcp"]]),
    ],
    ids=[
        "two zones",
        "offline",
        "zones unread",
        "reload untouched",
        "start untouched",
        "already zoned",
        "not firewalld",
    ],
)
def test_only_port_writes_are_repeated_per_zone(
    command: list[str], zones: tuple[str, ...] | None, expected: list[list[str]]
) -> None:
    """`_zoned()` touches a port write and nothing else, and never a second time."""
    assert networking._zoned(command, zones) == expected


# --- round 5, 2026-09-05 -----------------------------------------------------
# Three refutations of round 4, each measured on a real box before it was
# fixed. Blocker 1: the probe asked with less authority than `apply()` acts
# with, so the guard refused on every box. Blocker 2: the zone list the ports
# were written to is the one `firewall-cmd --reload` throws away. Blocker 3:
# "at least one line and no hole" is satisfiable by a single placed listener in
# a network namespace that is not this machine's.


def _sudo_ok(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """`sudo -n <anything>` works here; anything else was not asked for."""
    assert argv[:2] == ["sudo", "-n"], argv
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_the_probe_prefix_is_the_one_apply_puts_in_front_of_the_writes() -> None:
    """`probe_prefix()` reads `platform.elevation_policy()`, not a second spelling of it.

    Two callers have to agree about how this machine acts with authority — the
    probes here and the writes in `apply()` — and round 4 had only one of them.
    Measured as uid 1000 on 2026-09-04 with round-4's code: `ss --processes`
    named 2 of m910q's 15 listeners and 0 of yulon-ubuntu's 7, sshd was among
    the unnamed on both, and every enable and every reload was refused. Behind
    `sudo -n` the same command named 15/15 and 7/7 and both boxes resolved on
    port 22.
    """
    for backend in ("ufw", "firewalld"):
        assert networking.probe_prefix(backend, run=_sudo_ok) == ("sudo", "-n")
        assert networking.probe_prefix(backend, run=_sudo_ok) == tuple(
            platform.elevation_policy(backend).prefix
        )


@pytest.mark.parametrize(
    ("backend", "prefix"),
    [("netsh", ()), ("alf", ()), ("none", ())],
    ids=["netsh", "alf", "none"],
)
def test_a_backend_with_no_elevation_prefix_probes_as_itself(
    backend: platform.FirewallBackend, prefix: tuple[str, ...]
) -> None:
    """`netsh`, `alf` and `none` carry no `sudo`, so nothing is put in front of their probes."""

    def never(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"asked a prefix-less backend to test its prefix: {argv}")

    assert networking.probe_prefix(backend, run=never) == prefix


@pytest.mark.parametrize(
    ("answer", "prefix"),
    [
        ((0, ""), ("sudo", "-n")),
        ((1, "sudo: a password is required"), ()),
    ],
    ids=["passwordless sudo", "a password is required"],
)
def test_a_prefix_that_will_not_run_is_not_used_to_claim_a_reading(
    answer: tuple[int, str], prefix: tuple[str, ...]
) -> None:
    """The prefix is TESTED before the reading is taken with it, and the test is measured.

    In the fw5 fedora:41 container on m910q, 2026-09-05: `sudo -n true` is rc 0
    for a user in a NOPASSWD sudoers file and rc 1 with `sudo: a password is
    required` on stderr for one who is not. Under the second answer every probe
    runs unelevated, root's sshd comes back as an unnamed line, and the guard
    refuses — which is the same box's honest answer and not a lockout. Measured
    the same day: `sudo -u nosudo sudo -n ss --processes` is rc 1 with no
    output at all.
    """
    seen: list[list[str]] = []

    def probe(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, answer[0], "", answer[1])

    assert networking.probe_prefix("ufw", run=probe) == prefix
    assert seen == [["sudo", "-n", "true"]]


@pytest.mark.parametrize(
    "boom",
    [
        FileNotFoundError(2, "No such file or directory", "sudo"),
        subprocess.TimeoutExpired("sudo", 5.0),
    ],
    ids=["no sudo installed", "wedged"],
)
def test_a_prefix_that_cannot_be_run_at_all_leaves_the_probes_bare(boom: Exception) -> None:
    """Root on a box with no `sudo` must keep the reading it already had.

    Its bare probes name every socket — measured on m910q as root, 15/15 — so
    falling back to no prefix is what keeps that box working. Round 4 allowed
    with port 22 preserved there, and this is the case that must not regress.
    """

    def explode(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise boom

    assert networking.probe_prefix("ufw", run=explode) == ()


def test_elevate_false_probes_as_itself_because_its_writes_will() -> None:
    """A caller that will not elevate must not be handed a reading only elevation could take."""

    def never(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"tested a prefix for a plan that will not elevate: {argv}")

    assert networking.probe_prefix("firewalld", elevate=False, run=never) == ()


def test_the_ss_probe_goes_out_behind_the_prefix() -> None:
    """The argv `detect_ssh_route()` runs is the prefixed one, and the table is read from it.

    The m910q table as root is what `sudo -n ss --processes` returns at uid
    1000 — measured on 2026-09-05, 15 lines, 15 named, sshd on 22 — so this is
    the one command whose spelling decides whether the product can ever turn a
    firewall on.
    """
    seen: list[list[str]] = []

    def probe(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, _M910Q_ROOT, "")

    route = networking.detect_ssh_route(
        environ={}, run=probe, prefix=("sudo", "-n"), in_host_namespace=_here
    )
    assert seen == [
        ["sudo", "-n", "ss", "--no-header", "--listening", "--tcp", "--numeric", "--processes"]
    ]
    assert route.ports == (22,) and route.listeners_readable is True


def test_the_state_probe_goes_out_behind_the_prefix() -> None:
    """`firewall-cmd --state` is rc 253 at uid 1000 and rc 0 behind `sudo -n` (fw5, measured)."""
    seen: list[list[str]] = []

    def probe(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        rc = 0 if argv[:2] == ["sudo", "-n"] else 253
        return subprocess.CompletedProcess(argv, rc, "running" if rc == 0 else "", "")

    assert networking.detect_firewalld_daemon(run=probe) == "unknown"
    assert networking.detect_firewalld_daemon(run=probe, prefix=("sudo", "-n")) == "running"
    assert seen[-1] == ["sudo", "-n", "firewall-cmd", "--state"]


def _recording_probes(
    monkeypatch: pytest.MonkeyPatch, prefix: tuple[str, ...]
) -> dict[str, object]:
    """Replace `plan()`'s three default probes with recorders of the authority they were given."""
    seen: dict[str, object] = {}

    def fake_prefix(
        backend: platform.FirewallBackend,
        *,
        elevate: bool = True,
        run: networking.Runner | None = None,
    ) -> tuple[str, ...]:
        seen["asked"] = (backend, elevate)
        return prefix if elevate else ()

    def fake_ssh(
        prefix: tuple[str, ...] = (),
        backend: platform.FirewallBackend | None = None,
    ) -> networking.SshRoute:
        seen["ssh"] = prefix
        seen["ssh_backend"] = backend
        return networking.SshRoute(ports=(22,), listeners_readable=True)

    def fake_daemon(
        run: networking.Runner | None = None, *, prefix: tuple[str, ...] = ()
    ) -> networking.FirewalldDaemon:
        seen["daemon"] = prefix
        return "running"

    def fake_zones(
        daemon: networking.FirewalldDaemon,
        run: networking.Runner | None = None,
        *,
        prefix: tuple[str, ...] = (),
    ) -> networking.FirewalldZoning:
        seen["zones"] = prefix
        return networking.FirewalldZoning(
            write=("internal",),
            permanent=("internal",),
            runtime=("internal",),
            default_zone="internal",
            configured_default_zone="internal",
        )

    monkeypatch.setattr(networking, "probe_prefix", fake_prefix)
    monkeypatch.setattr(networking, "detect_ssh_route", fake_ssh)
    monkeypatch.setattr(networking, "detect_firewalld_daemon", fake_daemon)
    monkeypatch.setattr(networking, "detect_firewalld_zones", fake_zones)
    return seen


def test_every_default_probe_in_a_plan_carries_the_same_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One reading of "can I act here", handed to all three probes, on the real call path.

    Asserted through `plan()` rather than by reading the source, because the
    defect being fixed was in the wiring and not in any of the four functions:
    each of them was correct about the argv it was given, and the caller gave
    them an argv with no authority in it.
    """
    seen = _recording_probes(monkeypatch, ("sudo", "-n"))
    p = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="firewalld", steamos=False, wsl=False
    )
    assert seen["asked"] == ("firewalld", True)
    assert seen["ssh"] == ("sudo", "-n")
    assert seen["daemon"] == ("sudo", "-n")
    assert seen["zones"] == ("sudo", "-n")
    assert seen["ssh_backend"] == "firewalld", (
        "the route probe is told which backend's config it must find, or it can only ask "
        "which namespace the sockets came from (round 6)"
    )
    assert p.probed_elevated is True
    assert p.ssh_ports == (22,), "the port the elevated reading found is preserved"


def test_a_plan_that_will_not_elevate_probes_without_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`elevate=False` reaches every probe, and the plan does not claim the elevated reading."""
    seen = _recording_probes(monkeypatch, ("sudo", "-n"))
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="firewalld",
        steamos=False,
        wsl=False,
        elevate=False,
    )
    assert seen["asked"] == ("firewalld", False)
    assert seen["ssh"] == () and seen["daemon"] == () and seen["zones"] == ()
    assert p.probed_elevated is False


def test_a_plan_with_nothing_to_guard_still_tests_no_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authority probe is a subprocess too, and a ufw plan that guards nothing spawns none.

    `plan()` has had a test since round 1 saying an ordinary ufw plan asks the
    machine nothing; adding a `sudo -n true` to the top of it would have broken
    that quietly, so the reading is taken on first USE and not on entry.
    """

    def never(*args: object, **kwargs: object) -> tuple[str, ...]:
        raise AssertionError("tested an elevation prefix for a plan that guards nothing")

    monkeypatch.setattr(networking, "probe_prefix", never)
    p = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="ufw", steamos=False, wsl=False
    )
    assert p.firewall_commands and p.ssh_ports == () and p.probed_elevated is False


def test_a_reading_taken_with_authority_may_not_be_applied_without_it() -> None:
    """plan(elevate=True) + apply(elevate=False) is refused by name, not by six failures.

    The two calls are separate and the production path passes neither flag, so
    both default to True and agree. A caller that disagrees with itself gets
    the mismatch said once, before anything runs, instead of watching every
    `sudo`-less `ufw allow` fail and then reading a refusal that blames the
    rule.
    """
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        enable_firewall=True,
        detect_ssh=lambda: networking.SshRoute(ports=(22,), listeners_readable=True),
    )
    p = dataclasses.replace(p, probed_elevated=True)
    seen: list[list[str]] = []

    report = networking.apply(
        p,
        sql=None,
        run=lambda argv: (seen.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1],
        elevate=False,
    )
    assert ["ufw", "--force", "enable"] not in seen, "the enable must not have run"
    assert ["ufw", "allow", "3724/tcp"] in seen, "the ports the user asked for still go in"
    refusal = next(r for r in report.refusals if "--force enable" in r)
    assert "sudo -n" in refusal and "elevate=False" in refusal
    assert refusal in report.skipped


def test_an_elevated_apply_of_an_elevated_plan_runs_the_lockout_command() -> None:
    """The other side of the same test: agreeing calls are not refused."""
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        enable_firewall=True,
        detect_ssh=lambda: networking.SshRoute(ports=(22,), listeners_readable=True),
    )
    p = dataclasses.replace(p, probed_elevated=True)
    seen: list[list[str]] = []
    report = networking.apply(
        p,
        sql=None,
        run=lambda argv: (seen.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1],
        elevate=True,
    )
    assert ["sudo", "-n", "ufw", "allow", "22/tcp"] in seen
    assert ["sudo", "-n", "ufw", "--force", "enable"] in seen
    assert report.refusals == ()


# --- blocker 2: the zones a reload restores ---------------------------------


def test_the_ports_go_where_the_reload_will_leave_them() -> None:
    """A `--permanent` write followed by a `--reload` is judged by the PERMANENT bindings.

    Measured end to end on firewalld 2.2.3 (fedora:41 container on m910q,
    2026-09-05), from the host across the docker bridge, with round-4's code:

        eth0 permanent -> internal, then `--zone=work --change-interface=eth0`
        before   curl 9999 -> 200, tcp 2222 OPEN (runtime allows in `work`)
        plan     6 x `--permanent --zone={work,public} --add-port=...`, `--reload`
                 refusals=0  warnings=0  apply: 7 done, 0 skipped
        after    runtime eth0 = internal;  internal ports: (none)
                 curl 9999 -> 000, tcp 2222 CLOSED, ssh "No route to host",
                 curl 3724 -> 000

    Every write succeeded and the machine went dark, because `--get-active-zones`
    is the runtime binding and `FlushAllOnReload=yes` (shipped, line 73 of
    /etc/firewalld/firewalld.conf) restores the permanent one over it.
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(connected=True, ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("internal", "public", "work"),
            permanent=("internal", "public"),
            runtime=("work", "public"),
            default_zone="public",
            configured_default_zone="public",
            flush_all_on_reload=True,
        ),
    )
    written = [" ".join(c) for c in p.firewall_commands]
    for port in (3724, 8085, 2222):
        assert (
            f"firewall-cmd --permanent --zone=internal --add-port={port}/tcp" in written
        ), "the zone the reload restores gets every port"
    assert p.refusals == ()
    assert p.ssh_ports == (2222,)
    # The SSH rule lands in the surviving zone before the reload that restores it.
    assert written.index(
        "firewall-cmd --permanent --zone=internal --add-port=2222/tcp"
    ) < written.index("firewall-cmd --reload")


def test_a_runtime_zone_move_the_reload_will_undo_is_said_out_loud() -> None:
    """The disagreement is the state that killed the session, so it is named, not smoothed over.

    Not a refusal: the ports go to BOTH lists, so they are in effect whichever
    binding survives. What the user cannot see from a plan is that the reload
    it is about to run is what moves the interface back.
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("internal", "public", "work"),
            permanent=("internal", "public"),
            runtime=("work", "public"),
            default_zone="public",
            configured_default_zone="public",
            flush_all_on_reload=True,
        ),
    )
    said = next(w for w in p.warnings if "disagree" in w)
    assert "work" in said and "internal" in said
    assert "WILL undo that move" in said
    assert p.refusals == ()


def test_flush_all_on_reload_no_changes_what_the_disagreement_means() -> None:
    """With `FlushAllOnReload=no` the runtime move survives, and the sentence says so.

    Measured on the same container the same day: with `no`, eth0 moved to
    `work` at runtime was still in `work` after `firewall-cmd --reload`, while
    with `yes` it was back in `internal`. (A runtime-only PORT was dropped
    under both; the setting governs the interface binding.)
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("internal", "public", "work"),
            permanent=("internal", "public"),
            runtime=("work", "public"),
            default_zone="public",
            configured_default_zone="public",
            flush_all_on_reload=False,
        ),
    )
    said = next(w for w in p.warnings if "disagree" in w)
    assert "leave that move in place" in said


def test_two_readings_that_agree_say_nothing() -> None:
    """A box nobody has moved an interface on is told nothing about zones."""
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(22,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("internal", "public"),
            permanent=("internal", "public"),
            runtime=("internal", "public"),
            default_zone="public",
            configured_default_zone="public",
            flush_all_on_reload=True,
        ),
    )
    assert not [w for w in p.warnings if "disagree" in w]
    assert p.refusals == ()


def test_unreadable_permanent_bindings_refuse_the_reload() -> None:
    """None from the zone probe still drops every lockout command, and now says which reading.

    The remediation has to name the PERMANENT listing: sending a user to
    `--get-active-zones` is sending them to the list that was measured wrong.
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zones=None,
        zoning=None,
    )
    assert ("firewall-cmd", "--reload") not in p.firewall_commands
    refusal = next(r for r in p.refusals if "REFUSED" in r)
    assert "--permanent --list-all-zones" in refusal
    assert p.ssh_ports == () and p.firewalld_zones is None


# --- blocker 3: what makes a socket table evidence --------------------------


@pytest.mark.parametrize(
    "table",
    [
        _ONLY_GAME_SERVERS,
        'LISTEN 0 5 127.0.0.1:8099 0.0.0.0:* users:(("python3",pid=13,fd=3))\n',
        _SS_LISTENING_ONLY_SSHD,
        _M910Q_ROOT,
    ],
    ids=[
        "game servers, no sshd",
        "one placed listener, no sshd",
        "an sshd of its own",
        "a whole host's table",
    ],
)
def test_a_table_read_in_another_network_namespace_is_no_evidence(table: str) -> None:
    """Blocker 3, and the two namespace routes that a "must find an sshd" rule would have missed.

    Measured on m910q, 2026-09-04, driving round-4's whole module with
    `enable_firewall=True` from inside each namespace — every one of them
    emitted `ufw --force enable` with `refusals=0`, against the HOST's
    `/etc/ufw`, on a host whose sshd is on port 22:

        `unshare --net`, one root-owned python3 on 8099   (set(), True)
        `unshare --net`, an sshd of its own on 2222       ({2222}, True)
        `unshare --net --pid --fork --mount-proc`, ditto  ({2222}, True)

    The first is the gate blocker 3 named — `lines > 0 and holes == 0` is
    satisfiable by ONE placed listener. The other two carry an sshd and would
    have survived a rule that only demanded one. What settles all three is the
    namespace the reading came from, which is why THAT is the rule.
    """
    run = lambda argv: subprocess.CompletedProcess(argv, 0, table, "")  # noqa: E731
    ports, settled, machine = networking._sshd_listening_ports(run, in_host_namespace=lambda: False)
    assert settled is True, "the table itself is fine; it is whose it is that is not"
    assert machine is False, "a table from another namespace settles nothing"
    route = networking.detect_ssh_route(environ={}, run=run, in_host_namespace=lambda: False)
    assert route.listeners_readable is False
    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    assert p.refusals and all(r in p.warnings for r in p.refusals)
    assert p.ssh_ports == ()
    # The ports found are still reported; it is their COMPLETENESS that is denied.
    assert ports == _ports(run)[0]


def test_a_namespace_question_that_cannot_be_answered_settles_nothing() -> None:
    """None is not True. An unreadable `/proc/1/ns/net` and no prefix to elevate it is None.

    Measured on both boxes at uid 1000: `os.stat("/proc/1/ns/net")` is EACCES,
    while `/proc/self/ns/net` is not. Without a prefix there is nothing to
    compare it to, and a probe that cannot tell must not be read as a yes.
    """
    run = lambda argv: subprocess.CompletedProcess(argv, 0, _M910Q_ROOT, "")  # noqa: E731
    assert networking._sshd_listening_ports(run, in_host_namespace=lambda: None) == (
        {22},
        True,
        None,
    )


def test_the_namespace_is_asked_last_so_a_dead_table_spends_no_subprocess() -> None:
    """An already-unsettled table does not pay for the elevated `stat`.

    `in_host_network_namespace()` can cost a subprocess at uid 1000, and a
    table with a hole in it is refused whatever the answer.
    """
    asked = 0

    def counting() -> bool:
        nonlocal asked
        asked += 1
        return True

    unnamed = lambda argv: subprocess.CompletedProcess(  # noqa: E731
        argv, 0, _M910Q_UNPRIVILEGED, ""
    )
    assert networking._sshd_listening_ports(unnamed, in_host_namespace=counting) == (
        set(),
        False,
        True,
    )
    assert asked == 0, "a table with holes in it never asked"

    named = lambda argv: subprocess.CompletedProcess(argv, 0, _M910Q_ROOT, "")  # noqa: E731
    assert networking._sshd_listening_ports(named, in_host_namespace=counting) == ({22}, True, True)
    assert asked == 1


def test_a_namespace_probe_asks_pid_one_through_the_prefix_it_was_given() -> None:
    """The `/proc/1/ns/net` read is elevated like every other, and its answer is compared.

    Measured at uid 1000: `sudo -n stat -L -c %i /proc/1/ns/net` is rc 0 and
    answers 4026531840 on m910q and 4026531833 on yulon-ubuntu — the same
    numbers `/proc/self/ns/net` gives on each box, which is what makes them the
    host's namespace.
    """
    if not sys.platform.startswith("linux"):
        pytest.skip("/proc/self/ns is Linux's")
    mine = os.stat("/proc/self/ns/net").st_ino
    seen: list[list[str]] = []

    def probe(argv: list[str], answer: int) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"{answer}\n", "")

    if os.stat("/proc/self/ns/pid").st_ino != networking._INITIAL_PID_NAMESPACE_INO:
        pytest.skip("this suite is not running in the initial pid namespace")
    try:
        os.stat("/proc/1/ns/net")
    except OSError:
        pass
    else:
        pytest.skip("pid 1's namespace is readable here, so no prefix is needed")

    assert (
        networking.in_host_network_namespace(
            run=lambda argv: probe(argv, mine), prefix=("sudo", "-n")
        )
        is True
    )
    assert seen == [["sudo", "-n", "stat", "-L", "-c", "%i", "/proc/1/ns/net"]]
    assert (
        networking.in_host_network_namespace(
            run=lambda argv: probe(argv, mine + 1), prefix=("sudo", "-n")
        )
        is False
    )
    assert (
        networking.in_host_network_namespace(run=lambda argv: probe(argv, mine)) is None
    ), "with no prefix there is nothing to elevate the unreadable read with"


def test_the_initial_pid_namespace_is_a_kernel_constant_not_a_guess() -> None:
    """`PROC_PID_INIT_INO`, and the reason the NET namespace does not get one.

    Measured 2026-09-05: `/proc/self/ns/pid` is 4026531836 on m910q and on
    yulon-ubuntu, at uid 1000 and as root — 0xEFFFFFFC, a compile-time constant
    in `include/linux/proc_ns.h`. `/proc/self/ns/net` is 4026531840 on m910q
    and 4026531833 on yulon-ubuntu, so a magic number for the net namespace
    would have refused one of the two boxes this feature exists for.
    """
    assert networking._INITIAL_PID_NAMESPACE_INO == 0xEFFFFFFC == 4026531836

    # And it is load-bearing: a pid namespace that is not the initial one is
    # False before pid 1 is asked anything, because there pid 1 is the
    # namespace's own init and comparing against it compares a thing to itself.
    # `unshare --net --pid --fork --mount-proc` with an sshd of its own (m910q,
    # 2026-09-05) reads ns/pid 4026533089, ns/net 4026533090 and pid 1's ns/net
    # 4026533090 — equal, and equal about the wrong machine.
    real = os.stat

    def namespaced(path: str, *args: object, **kwargs: object) -> object:
        if path == "/proc/self/ns/pid":
            return type("St", (), {"st_ino": 4026533089})()
        if path == "/proc/self/ns/net":
            return type("St", (), {"st_ino": 4026533090})()
        raise AssertionError(f"asked about {path} after the pid namespace already answered")

    def never(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"spawned {argv} after the pid namespace already answered")

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(networking.os, "stat", namespaced)
        assert networking.in_host_network_namespace(run=never, prefix=("sudo", "-n")) is False

    if not sys.platform.startswith("linux"):
        pytest.skip("the rest reads /proc, which is Linux's")
    # The other side, against the running kernel: on a box that IS the initial
    # pid namespace the constant matches, so the rule does not refuse it. If
    # this suite is itself running in a container it is one of the environments
    # above and there is nothing here to check.
    if real("/proc/self/ns/pid").st_ino != networking._INITIAL_PID_NAMESPACE_INO:
        pytest.skip("this suite is in a pid namespace of its own (a container)")
    assert networking.in_host_network_namespace(run=None, prefix=()) in (
        True,
        None,
    ), "the initial pid namespace must not be refused by the constant"


def test_a_machine_with_no_proc_namespaces_answers_nothing_rather_than_yes() -> None:
    """Windows and macOS have no `/proc/self/ns`, and None is what that is worth.

    Neither reaches this rule in production — `netsh` and `alf` emit no command
    that can cut a session — but a probe that cannot read the question must not
    answer it, and a bool would have had to pick a side.
    """

    def no_proc(path: str, *args: object, **kwargs: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", path)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(networking.os, "stat", no_proc)
        assert networking.in_host_network_namespace() is None


# --- round 6: the door DefaultZone left open, the breadth nobody was told
# --- about, and the second namespace question -------------------------------


_DIVERGED_PERMANENT_ZONES = """public (default)
  target: default
  interfaces:
  sources:
  services: dhcpv6-client mdns ssh
  ports: 2222/tcp

work
  target: default
  interfaces:
  sources:
  services: dhcpv6-client mdns ssh
  ports:
"""
"""`firewall-cmd --permanent --list-all-zones` with the daemon and the file DISAGREEING.

firewalld 2.2.3 in a fedora:41 container on m910q, 2026-09-05, after
`firewall-offline-cmd --set-default-zone=work` — a supported call that returns
`success` against a running daemon and writes only the file. The daemon's
default is still `public`, so `public` is what carries the `(default)` tag HERE
even though `DefaultZone=work` is what the next reload will install. Trimmed to
the two zones that matter and to the fields the parser reads; the real answer
lists all twelve zones.
"""

_DIVERGED_OFFLINE_ZONES = """public
  target: default
  interfaces:
  sources:
  services: dhcpv6-client mdns ssh
  ports: 2222/tcp

work (default)
  target: default
  interfaces:
  sources:
  services: dhcpv6-client mdns ssh
  ports:
"""
"""`firewall-offline-cmd --list-all-zones` in that same state, one second later.

The tag is on `work`, because this tool reads the files. Two commands, one
machine, one moment, two different answers to "which zone is the default" —
which is the whole of the blocker, and the reason this listing is the
cross-check.
"""

_FIREWALLD_CONF_DEFAULT_WORK = (
    "# comment\nDefaultZone=work\nFlushAllOnReload=yes\nIPv6_rpfilter=yes\n"
)
"""That container's firewalld.conf in that state.

`DefaultZone` is six lines above the setting this module was already reading
out of this file, which is what makes the blocker a door left open rather than
a reading nobody could have taken.
"""


def test_the_default_zone_a_reload_installs_is_read_from_the_file_not_the_daemon() -> None:
    """BLOCKER, round 6: `(default)` on a `firewall-cmd` listing is the DAEMON's answer.

    Measured twice in the container above (firewalld 2.2.3, fedora:41 on m910q,
    2026-09-05), daemon default `public`, file `DefaultZone=work`, reached by
    editing the file and by `firewall-offline-cmd --set-default-zone=work`:

        firewall-cmd --permanent --list-all-zones  ->  public (default)
        firewall-cmd --get-active-zones            ->  public (default)
        firewall-offline-cmd --list-all-zones      ->  work (default)

    All three of round 5's readings agreed, `moved_at_runtime` was empty, and
    the plan wrote 3724, 8085 and 2222 to `public` and reloaded: apply 4/4,
    refusals 0, warnings 0. Afterwards the daemon's default was `work`, eth0
    had "no zone", `work` listed no ports, `ssh -p 2222` answered "No route to
    host" and curl on 3724 and 8085 both answered 000.
    """
    probe, _ = _zone_probes(
        {
            _PERMANENT_ARGV: (0, _DIVERGED_PERMANENT_ZONES),
            _RUNTIME_ARGV: (0, _ACTIVE_ZONES_DEFAULT_ONLY),
            _CONF_ARGV: (0, _FIREWALLD_CONF_DEFAULT_WORK),
        }
    )
    read = networking.detect_firewalld_zones("running", run=probe)
    assert read is not None
    assert read.default_zone == "public", "what the daemon says"
    assert read.configured_default_zone == "work", "what the reload will install"
    assert read.default_zone_moves is True
    assert read.write == ("public", "work"), "the surviving zone first, the new one after"
    # And the disagreement is invisible to every reading round 5 had.
    assert read.permanent == ("public",) and read.runtime == ("public",)
    assert read.moved_at_runtime == ()


def test_a_default_zone_that_moves_is_written_to_and_said_out_loud() -> None:
    """The ports land in BOTH zones before the reload, and the plan names the two of them."""
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("public", "work"),
            permanent=("public",),
            runtime=("public",),
            default_zone="public",
            configured_default_zone="work",
            flush_all_on_reload=True,
        ),
    )
    written = [" ".join(c) for c in p.firewall_commands]
    for port in (3724, 8085, 2222):
        assert f"firewall-cmd --permanent --zone=work --add-port={port}/tcp" in written
    assert written.index(
        "firewall-cmd --permanent --zone=work --add-port=2222/tcp"
    ) < written.index("firewall-cmd --reload")
    assert p.refusals == (), "written to both zones is not a refusal"
    said = next(w for w in p.warnings if "DefaultZone" in w)
    assert "`public`" in said and "`work`" in said
    assert "/etc/firewalld/firewalld.conf" in said
    assert "firewall-offline-cmd --list-all-zones" in said


def test_a_running_daemon_whose_configured_default_zone_is_unread_refuses_the_reload() -> None:
    """Unknown is not "they agree". The one state the divergence cannot be ruled out in.

    The zones ARE known here and the ports are written to all of them — that is
    what makes this its own refusal rather than `_zone_refusal()`'s. What could
    not be read is which zone every unbound interface will be in one second
    after the reload, so the reload does not run.
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("public",),
            permanent=("public",),
            runtime=("public",),
            default_zone="public",
            configured_default_zone=None,
        ),
    )
    assert ("firewall-cmd", "--reload") not in p.firewall_commands
    assert p.ssh_ports == ()
    refusal = next(r for r in p.refusals if "DefaultZone" in r)
    assert "/etc/firewalld/firewalld.conf" in refusal
    assert "firewall-offline-cmd --get-default-zone" in refusal
    assert "`public`" in refusal, "the running default is named, since it IS known"
    assert refusal in p.warnings
    # The ports the user asked for are still written; it is the reload that is held.
    assert (
        "firewall-cmd",
        "--permanent",
        "--zone=public",
        "--add-port=3724/tcp",
    ) in p.firewall_commands


def test_the_offline_listing_is_the_cross_check_when_the_conf_carries_no_default_zone() -> None:
    """A conf with no `DefaultZone` line falls back to the tool that reads the same files.

    `firewall-offline-cmd --list-all-zones` answered the truth in the diverged
    state where every `firewall-cmd` reading did not, so it is the fallback
    rather than a second opinion: it is asked only when the file itself said
    nothing, because it is another process.
    """
    probe, seen = _zone_probes(
        {
            _PERMANENT_ARGV: (0, _DIVERGED_PERMANENT_ZONES),
            _RUNTIME_ARGV: (0, _ACTIVE_ZONES_DEFAULT_ONLY),
            _CONF_ARGV: (0, "FlushAllOnReload=yes\n"),
            _OFFLINE_ARGV: (0, _DIVERGED_OFFLINE_ZONES),
        }
    )
    read = networking.detect_firewalld_zones("running", run=probe)
    assert read is not None
    assert read.configured_default_zone == "work" and read.default_zone_moves is True
    assert "work" in read.write
    assert [list(_OFFLINE_ARGV)] == [c for c in seen if c[0] == "firewall-offline-cmd"]

    # And it is NOT spent when the file answered.
    probe, seen = _zone_probes(
        {
            _PERMANENT_ARGV: (0, _DIVERGED_PERMANENT_ZONES),
            _RUNTIME_ARGV: (0, _ACTIVE_ZONES_DEFAULT_ONLY),
            _CONF_ARGV: (0, _FIREWALLD_CONF_DEFAULT_WORK),
        }
    )
    assert networking.detect_firewalld_zones("running", run=probe) is not None
    assert not [c for c in seen if c[0] == "firewall-offline-cmd"]


def test_a_running_daemon_with_neither_source_readable_has_no_configured_default() -> None:
    """Both readings gone is None, not a guess — and the caller refuses on it."""
    probe, _ = _zone_probes(
        {
            _PERMANENT_ARGV: (0, _DIVERGED_PERMANENT_ZONES),
            _RUNTIME_ARGV: (0, _ACTIVE_ZONES_DEFAULT_ONLY),
            _CONF_ARGV: (1, ""),
            _OFFLINE_ARGV: (255, ""),
        }
    )
    read = networking.detect_firewalld_zones("running", run=probe)
    assert read is not None, "the zones themselves were readable"
    assert read.configured_default_zone is None and read.default_zone_moves is False
    assert read.write == ("public",)


def test_a_stopped_daemon_has_one_default_zone_and_cannot_diverge_from_itself() -> None:
    """No daemon, no live default: the file's answer is both readings, and nothing moves."""
    probe, _ = _zone_probes(
        {_OFFLINE_ARGV: (0, _DIVERGED_OFFLINE_ZONES), _CONF_ARGV: (0, _FIREWALLD_CONF_DEFAULT_WORK)}
    )
    read = networking.detect_firewalld_zones("stopped", run=probe)
    assert read is not None
    assert read.default_zone == "work" and read.configured_default_zone == "work"
    assert read.default_zone_moves is False
    assert "work" in read.write

    # With the file unreadable the offline listing's own tag is the answer.
    probe, _ = _zone_probes({_OFFLINE_ARGV: (0, _DIVERGED_OFFLINE_ZONES), _CONF_ARGV: (1, "")})
    read = networking.detect_firewalld_zones("stopped", run=probe)
    assert read is not None and read.configured_default_zone == "work"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (_DIVERGED_PERMANENT_ZONES, "public"),
        (_DIVERGED_OFFLINE_ZONES, "work"),
        (_ACTIVE_ZONES_INTERNAL, "public"),
        ("internal (active)\n  interfaces: eth0\n", None),
        ("public (default, active)\n  interfaces: eth0\n", "public"),
        ("", None),
    ],
    ids=[
        "the daemon's, from the permanent listing",
        "the file's, from the offline listing",
        "the active listing tags it too",
        "active is not default",
        "both tags on one line",
        "nothing at all",
    ],
)
def test_the_default_tag_is_read_apart_from_the_active_one(text: str, name: str | None) -> None:
    """`(default)` and `(active)` are different claims and only one of them answers this."""
    assert networking.default_zone_from_listing(text) == name


def test_every_zone_the_game_ports_are_written_to_is_named_in_the_plan() -> None:
    """DEFECT 2: the union is kept, and it stops being silent.

    Measured on firewalld 2.2.3 (fedora:41 on m910q, 2026-09-05) with eth1
    bound permanently to a custom `wanzone` that allowed nothing: the round-5
    plan emitted six `--permanent` writes plus the reload, `apply()` ran 7/7,
    `refusals=0`, `warnings=0`, and afterwards `wanzone` listed 2222/tcp,
    3724/tcp and 8085/tcp — the game ports AND the SSH port on the interface a
    multi-homed box faces the internet with. The string `wanzone` appeared
    nowhere a user could read.

    The breadth is kept because both narrowings break the feature on an
    ordinary box — ports written to the default zone alone were unreachable on
    a box bound to `internal` (round 3), and in `internet` mode the WAN-facing
    zone is where the clients arrive — so what changes is that the plan says
    where the ports went and how to take one back.
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zones=("public", "wanzone"),
    )
    written = [" ".join(c) for c in p.firewall_commands]
    assert "firewall-cmd --permanent --zone=wanzone --add-port=3724/tcp" in written
    said = next(w for w in p.warnings if "game ports" in w)
    assert "`wanzone`" in said and "`public`" in said
    assert "3724, 8085" in said
    assert "--remove-port=<port>/tcp" in said, "and the command that takes one back"
    assert "SSH (port 2222)" in said, "and why the SSH rule is not narrowed with them"
    assert p.refusals == ()


def test_one_zone_is_no_breadth_and_a_machine_that_is_fine_is_still_told_nothing() -> None:
    """The sentence appears when a second zone means a rule nobody asked for by name.

    A box with one zone had its ports written to the only zone there is. Saying
    so would put a paragraph on the screen of every ordinary Fedora desktop,
    next to the warnings that mean something — the failure mode
    `firewalld_start_withheld()` was written against.
    """
    p = _firewalld_plan(
        daemon="running",
        route=networking.SshRoute(ports=(22,), listeners_readable=True),
        zones=("public",),
    )
    assert p.refusals == () and p.warnings == ()


class _Ino:
    """A `stat` result with nothing on it but the inode, which is all this rule reads."""

    def __init__(self, ino: int) -> None:
        self.st_ino = ino


_HOST_PID_NS = 4026531836
_HOST_NET_NS = 4026531840
_HOST_MNT_NS = 4026531841
_CONTAINER_MNT_NS = 4026533518
"""The four inodes measured on m910q, 2026-09-05, from inside `docker run --rm
--privileged --pid=host --network=host` of a fedora:41 image on the Ubuntu
host: the pid namespace IS the initial one, the network namespace IS pid 1's,
and the mount namespace is not."""


def _namespaced(*, mnt: int, pid1_mnt: int = _HOST_MNT_NS) -> object:
    """`os.stat` for a process in the host's net namespace and `mnt`'s mount namespace."""
    answers = {
        "/proc/self/ns/pid": _HOST_PID_NS,
        "/proc/self/ns/net": _HOST_NET_NS,
        "/proc/1/ns/net": _HOST_NET_NS,
        "/proc/self/ns/mnt": mnt,
        "/proc/1/ns/mnt": pid1_mnt,
    }

    def stat(path: str, *args: object, **kwargs: object) -> object:
        if path in answers:
            return _Ino(answers[path])
        raise AssertionError(f"asked about {path}")

    return stat


def _never_asked(path: str) -> bool:
    raise AssertionError(f"asked whether {path} exists for a backend that writes no file")


def test_a_container_with_the_hosts_network_is_still_not_the_hosts_machine() -> None:
    """DEFECT 3: round 5 asked which NETWORK namespace and never which MOUNT namespace.

    Measured on m910q, 2026-09-05: `docker run --rm --privileged --pid=host
    --network=host` of a fedora:41 image on the Ubuntu host read

        /proc/self/ns/pid  4026531836   the initial pid namespace
        /proc/self/ns/net  4026531840 == /proc/1/ns/net
        /proc/self/ns/mnt  4026533518 != /proc/1/ns/mnt (4026531841)
        /etc/ufw           absent, and /etc/firewalld present (the image's)

    `in_host_network_namespace()` answered True — correctly; those really are
    the host's sockets — `ss` named the host's sshd on 22, and the round-5 plan
    emitted `ufw allow 3724/tcp`, `ufw allow 8085/tcp`, `ufw allow 22/tcp` and
    `ufw --force enable` with `refusals=0`, writing a config directory that
    does not exist for a netfilter policy that is the host's. It also refuted
    that function's own stated price, which said a container "is not in the
    initial pid namespace and gets False here".
    """
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(networking.os, "stat", _namespaced(mnt=_CONTAINER_MNT_NS))
        patched.setattr(networking.os.path, "isdir", lambda path: path == "/etc/firewalld")
        assert networking.in_host_network_namespace() is True, "round 5's answer, unchanged"
        assert networking.in_host_mount_namespace() is False
        # The config-directory question alone does not catch this one: the
        # image HAS /etc/firewalld. The mount namespace is what does.
        assert networking.reads_this_machine("firewalld") is False
        assert networking.reads_this_machine("ufw") is False
        assert networking.reads_this_machine() is False


def test_the_config_the_backend_writes_must_exist_on_this_filesystem() -> None:
    """The second half of the question, asked before the mount namespace costs a subprocess.

    A missing `/etc/ufw` is decisive on its own and free to read, which matters
    because the mount-namespace read is EACCES at uid 1000 and needs the prefix.
    """
    spawned: list[list[str]] = []

    def never(argv: list[str]) -> subprocess.CompletedProcess[str]:
        spawned.append(argv)
        raise AssertionError(f"spawned {argv} after the missing directory answered")

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(networking.os, "stat", _namespaced(mnt=_HOST_MNT_NS))
        patched.setattr(networking.os.path, "isdir", lambda path: False)
        assert networking.reads_this_machine("ufw", run=never) is False
        assert spawned == []
        # With the directory there and the mount namespace pid 1's, it is this
        # machine — which is every ordinary box, and must stay an ALLOW.
        patched.setattr(networking.os.path, "isdir", lambda path: True)
        assert networking.reads_this_machine("ufw") is True
        assert networking.reads_this_machine("firewalld") is True
        # A backend with no config file of its own is not asked about one.
        patched.setattr(networking.os.path, "isdir", _never_asked)
        assert networking.reads_this_machine("none") is True


def test_a_refusal_names_the_machine_it_could_not_place_not_a_hole_it_did_not_have() -> None:
    """The `--pid=host` table had 15 lines, every one named, sshd on 22, and no hole.

    So the sentence the guard had for an unsettled table — "a listener on the
    table could not be named, or is held by an init that could be fronting an
    SSH daemon" — would have been a false reason attached to a correct
    refusal. `SshRoute.read_elsewhere` carries the true one.
    """
    run = lambda argv: subprocess.CompletedProcess(argv, 0, _M910Q_ROOT, "")  # noqa: E731
    route = networking.detect_ssh_route(environ={}, run=run, in_host_namespace=lambda: False)
    assert route.ports == (22,), "the table itself was fine"
    assert route.listeners_readable is False
    assert route.read_elsewhere is not None and "mount namespace" in route.read_elsewhere

    p = _ufw_plan(enable_firewall=True, route=route)
    assert all("enable" not in c for cmd in p.firewall_commands for c in cmd)
    refusal = next(r for r in p.refusals if "REFUSED" in r)
    assert "mount namespace" in refusal and "/etc/ufw" in refusal
    assert "could not all be accounted for" not in refusal, "do not blame a table that was whole"

    # And "could not tell" is a different sentence from "it is somewhere else".
    unknown = networking.detect_ssh_route(environ={}, run=run, in_host_namespace=lambda: None)
    assert unknown.read_elsewhere is not None
    assert "could not be established" in unknown.read_elsewhere
    assert unknown.read_elsewhere != route.read_elsewhere


def test_a_table_that_never_settled_does_not_blame_the_machine_it_was_read_on() -> None:
    """A holed table is refused for the hole, and the namespace question is not asked at all."""
    run = lambda argv: subprocess.CompletedProcess(argv, 0, _M910Q_UNPRIVILEGED, "")  # noqa: E731

    def never() -> bool:
        raise AssertionError("asked which machine a table that had already failed came from")

    route = networking.detect_ssh_route(environ={}, run=run, in_host_namespace=never)
    assert route.listeners_readable is False
    assert route.read_elsewhere is None


def test_an_unknown_daemon_is_held_to_the_running_rule_not_the_stopped_one() -> None:
    """`unknown` reloads too, and a zone listing that answered means a daemon answered it.

    `firewall-cmd --state` failing is what makes the state `unknown`, and the
    reload stays in the list for it (`_firewalld_port_commands()`). If the
    permanent zone listing then succeeded there is a daemon behind it, with a
    live default zone that can differ from the file's — so the same refusal
    applies. A STOPPED daemon cannot reach it: its zones came from the files
    the reload installs.
    """
    unknown = _firewalld_plan(
        daemon="unknown",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("public",),
            permanent=("public",),
            runtime=("public",),
            default_zone="public",
            configured_default_zone=None,
        ),
    )
    assert ("firewall-cmd", "--reload") not in unknown.firewall_commands
    assert any("DefaultZone" in r for r in unknown.refusals)

    stopped = _firewalld_plan(
        daemon="stopped",
        route=networking.SshRoute(ports=(2222,), listeners_readable=True),
        zoning=networking.FirewalldZoning(
            write=("public",),
            permanent=("public",),
            default_zone=None,
            configured_default_zone=None,
        ),
    )
    assert not any("DefaultZone" in r for r in stopped.refusals)
    assert not any(networking._can_lock_out(c) for c in stopped.firewall_commands)
