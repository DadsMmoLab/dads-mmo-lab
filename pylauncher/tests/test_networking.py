"""Tests for networking auto-setup (`yulon.networking` + `platform` helpers, roadmap 3.4).

`plan()` is pure once its detection seams are pinned, so the LAN and internet
plans are asserted exactly; `apply()` runs against a recording runner and SQL
fake; the `docker.published_bindings()` parser and the firewall/portproxy
command builders are checked against the guide's literal commands.
"""

from __future__ import annotations

import ssl
import subprocess
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
    out = "0.0.0.0:3724->3724/tcp, [::]:3724->3724/tcp\n" "127.0.0.1:8085->8085/tcp\n" "3306/tcp\n"
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
    p = networking.plan(
        WOTLK,
        "lan",
        lan_ip="192.168.1.25",
        firewall="ufw",
        steamos=False,
        wsl=False,
        bindings={3724: "0.0.0.0", 8085: "0.0.0.0"},
    )
    assert p.ready and p.warnings == () and p.manual_steps == ()
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
    p = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="ufw", steamos=False, wsl=False
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
