"""Tests for networking auto-setup (`yulon.networking` + `platform` helpers, roadmap 3.4).

`plan()` is pure once its detection seams are pinned, so the LAN and internet
plans are asserted exactly; `apply()` runs against a recording runner and SQL
fake; the `docker.published_bindings()` parser and the firewall/portproxy
command builders are checked against the guide's literal commands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import docker, networking, platform, runner
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")


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
    assert platform.detect_public_ip(lambda u: answers[u], services=("https://a", "https://b")) == (
        "98.24.105.7"
    )

    def offline(url: str) -> str:
        raise OSError("no network")

    assert platform.detect_public_ip(offline, services=("https://a",)) is None


@pytest.mark.parametrize(
    ("ip", "cgnat"), [("100.64.3.4", True), ("192.168.1.2", True), ("98.24.105.7", False)]
)
def test_is_cgnat(ip: str, cgnat: bool) -> None:
    assert platform.is_cgnat(ip) is cgnat


def test_published_bindings_parses_docker_ps_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    out = "0.0.0.0:3724->3724/tcp, [::]:3724->3724/tcp\n" "127.0.0.1:8085->8085/tcp\n" "3306/tcp\n"
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None: subprocess.CompletedProcess(cmd, 0, out, "")
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
        detect_public=lambda: None,
    )
    assert no_pub.ready is False and no_pub.realmlist_sql is None
    assert any("public IP" in w for w in no_pub.warnings)


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
