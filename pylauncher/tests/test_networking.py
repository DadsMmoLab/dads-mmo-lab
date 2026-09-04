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
sshd was moved off 22 — the case `ufw allow 22/tcp` would have got wrong."""


def _no_ss(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """A machine with no `ss` at all: the probe cannot answer, and must say so."""
    raise FileNotFoundError(2, "No such file or directory", argv[0])


def _unprivileged_ss(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """`ss --processes` as a normal user: the socket is listed, its owner is not."""
    return subprocess.CompletedProcess(argv, 0, "LISTEN 0 128 0.0.0.0:22 *:*\n", "")


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


def test_a_box_with_no_ssh_at_all_enables_exactly_what_the_guide_says() -> None:
    """A box that looks local is unchanged: the three commands, no advice.

    Nothing listens for SSH and nothing arrived over it, so there is no route
    to preserve and no sentence worth printing — inventing one for a machine
    that is fine is how a networking screen teaches people to ignore it.
    """
    p = _ufw_plan(enable_firewall=True, route=networking.SshRoute())
    assert list(p.firewall_commands) == [
        ("ufw", "allow", "3724/tcp"),
        ("ufw", "allow", "8085/tcp"),
        ("ufw", "--force", "enable"),
    ]
    assert p.refusals == () and p.warnings == () and p.manual_steps == ()


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
    it; and matching `systemctl enable` without reading the unit would withhold
    firewalld's start, after which `firewall-cmd --reload` has no daemon to
    talk to.
    """
    assert networking._turns_ufw_on(command) is turns_it_on


def test_firewalld_is_not_the_same_bug_and_is_left_alone() -> None:
    """Scope, argued rather than assumed: firewalld's default zone admits ssh.

    `systemctl enable --now firewalld` starts a firewall whose `public` zone
    ships the `ssh` service allowed, and `firewall-cmd --reload` needs the
    daemon running — so withholding the start here would break a working path
    to prevent a lockout this backend does not cause.
    """
    p = networking.plan(
        WOTLK, "lan", lan_ip="192.168.1.25", firewall="firewalld", steamos=False, wsl=False
    )
    assert p.firewall_commands[0] == ("systemctl", "enable", "--now", "firewalld")
    assert p.refusals == () and p.ssh_ports == ()


def test_the_ssh_port_comes_from_the_running_system_not_from_a_config_file() -> None:
    """`ss`, not `sshd_config`: the answer is what is LISTENING right now."""
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _SS_LISTENING, "")

    route = networking.detect_ssh_route(environ={}, run=run)
    assert route == networking.SshRoute(connected=False, ports=(2022,), listeners_readable=True)
    assert calls and calls[0][0] == "ss"


def test_ssh_connection_names_the_port_this_session_actually_arrived_on() -> None:
    """Field four of `SSH_CONNECTION` is the server-side port sshd accepted us on.

    Which is the one port that provably admits the operator standing here, so
    it joins whatever the listener table said rather than replacing it.
    """
    route = networking.detect_ssh_route(
        environ={"SSH_CONNECTION": "10.0.0.5 51234 10.0.0.9 2200"},
        run=lambda argv: subprocess.CompletedProcess(argv, 0, _SS_LISTENING, ""),
    )
    assert route.connected is True
    assert route.ports == (2022, 2200)


def test_a_session_over_ssh_still_names_its_port_when_ss_is_missing() -> None:
    """The environment answers even when the probe cannot, and that is enough to proceed."""
    route = networking.detect_ssh_route(
        environ={"SSH_CONNECTION": "10.0.0.5 51234 10.0.0.9 2200"}, run=_no_ss
    )
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
    route = networking.detect_ssh_route(environ={}, run=run)
    assert route.ports == () and route.listeners_readable is False


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
    route = networking.detect_ssh_route(environ={"SSH_CONNECTION": value}, run=_no_ss)
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
