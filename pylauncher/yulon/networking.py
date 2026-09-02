"""Networking auto-setup: LAN and internet play, as a plan the app executes (README §13).

Shared orchestration over `platform.py`'s firewall/IP helpers and the
catalog's port table (roadmap 3.4). `plan()` is pure — it computes exactly
what would be done for a mode, including the router steps the app CANNOT do
(DHCP reservation, TCP port forwarding) and the warnings it detected (ports
bound to 127.0.0.1, CGNAT, a dynamic public IP) — and `apply()` executes the
automatable part: firewall rules (via `sudo -n` on Linux, never a hanging
password prompt), the WSL2 portproxy, and the realmlist UPDATE in the auth
DB through the server's DB container. Every step that could not be done is
reported by name, with the exact commands to paste, instead of failing
silently (style-guide §3: this module holds behavior, `catalog.json` holds
the numbers).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import platform, runner
from yulon.apply import ApplyError, SqlRunner
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

Mode = Literal["lan", "internet"]

_DUCKDNS = "https://www.duckdns.org/"


@dataclass(frozen=True)
class NetworkPlan:
    """Everything a networking setup run would do, decided before anything runs."""

    mode: Mode
    game_id: str
    lan_ip: str | None
    public_ip: str | None
    ports: tuple[int, ...]
    firewall: platform.FirewallBackend
    firewall_commands: tuple[tuple[str, ...], ...]
    portproxy_commands: tuple[tuple[str, ...], ...]
    realmlist_sql: str | None
    client_realmlist: str | None
    manual_steps: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    firewall_state: platform.AlfState | None = None
    """Probed only for the `alf` backend; None everywhere else.

    macOS has no port rules to plan, so what a Mac gets instead of a command
    list is the firewall's actual state — see `platform.AlfState`. Defaulted,
    so every existing construction and test is untouched.
    """

    @property
    def ready(self) -> bool:
        """False if something essential (the LAN IP, or the public IP for internet) is missing."""
        if self.lan_ip is None:
            return False
        return self.mode == "lan" or self.public_ip is not None


@dataclass(frozen=True)
class NetworkReport:
    """What `apply()` did, skipped (with the commands to run by hand), and still needs."""

    plan: NetworkPlan
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    restart_required: bool = False

    @property
    def manual_steps(self) -> tuple[str, ...]:
        return self.plan.manual_steps


def realmlist_sql(entry: CatalogEntry, address: str, local_address: str | None) -> str:
    """The UPDATE that advertises `address` (and `local_address`, if the core has that column)."""
    rl = entry.realmlist
    sets = [f"{rl.address_column}='{_sql_literal(address)}'"]
    if rl.local_address_column and local_address:
        sets.append(f"{rl.local_address_column}='{_sql_literal(local_address)}'")
    return (
        f"UPDATE {entry.databases.auth}.{rl.table} SET {', '.join(sets)} "
        f"WHERE id={rl.realm_id};"
    )


def _sql_literal(ip: str) -> str:
    # IPs/hostnames only: refuse anything that is not a plain address token.
    if not all(ch.isalnum() or ch in ".-:" for ch in ip):
        raise ValueError(f"not an address: {ip!r}")
    return ip


LOOPBACK = frozenset({"::1", "0.0.0.0", "localhost"})
"""Addresses that name the machine ASKING, in every spelling but `127.*`.

Sibling of the `127.` prefix test in `advertisable()`, kept as data because
`0.0.0.0` is a bind address rather than a loopback and belongs in the same
refusal for a different reason: a realm advertising it hands the client a
string no client can dial.
"""


def advertisable(address: str | None) -> str | None:
    """`address`, if a realm may advertise it to other machines; else None.

    The one predicate for "is this worth writing into the realmlist row", so
    that nothing downstream has to ask half of it. Three refusals, and each is
    an answer some real detector gives:

    * **nothing at all** — `platform.detect_lan_ip()` answers None for a
      machine whose routing table it could not read, and its WSL branch answers
      None for a PowerShell that printed an empty line.
    * **the loopback**, `127.*` or a member of `LOOPBACK`. This is the whole of
      bug-checklist §35: a realm advertising `127.0.0.1` tells every client that
      the world server is on the CLIENT's own machine, so the client hangs at
      "Connecting" and says nothing useful. `detect_lan_ip()` filters `127.` on
      its local branch and its WSL branch does NOT — it takes whatever
      `Get-NetIPConfiguration` printed — so the filter has to exist somewhere
      both branches pass through, and this is it.
    * **anything `_sql_literal()` would refuse.** Asked HERE, by calling it,
      rather than restated: a caller that has been handed an address by this
      function can then build the UPDATE without a `ValueError` reaching it, and
      the two rules cannot drift apart because there is only one of them.

    Surrounding whitespace is stripped rather than refused — a detector that
    reads a command's stdout is the normal source of a trailing newline, and
    `_sql_literal()` would call that "not an address".
    """
    if not address:
        return None
    candidate = address.strip()
    if not candidate or candidate.startswith("127.") or candidate in LOOPBACK:
        return None
    try:
        _sql_literal(candidate)
    except ValueError:
        return None
    return candidate


def realmlist_columns(entry: CatalogEntry) -> tuple[str, ...]:
    """The address columns `realmlist_sql()` writes, in the order it writes them.

    Spelled once so that a reader of the row and a writer of the row cannot
    disagree about which columns the answer is about. `local_address_column`
    is optional per core, and `realmlist_sql()` includes it exactly when it is
    set and a local address was given — this function is the "and a local
    address was given" case, which is every caller that advertises one address
    to everybody (`plan()`'s `lan` mode, and the installer's closing step).
    """
    rl = entry.realmlist
    if rl.local_address_column:
        return (rl.address_column, rl.local_address_column)
    return (rl.address_column,)


def realmlist_address_query(entry: CatalogEntry) -> str:
    """The SELECT that says whether `realmlist_sql()` would change anything.

    EVERY column the UPDATE sets, not just `address`, and that is the point of
    it. An install whose `address` is already the LAN IP while its
    `localAddress` is still `127.0.0.1` is the same outage with a smaller blast
    radius: AzerothCore hands `localAddress` to a client it decides is on the
    realm's own subnet, so the players most likely to be on the LAN are exactly
    the ones sent to their own machine. A caller comparing one column would
    call that row unchanged and leave it.

    Fully qualified with the auth schema, like `realmlist_sql()`, so the reader
    and the writer address the same table without either needing a connection
    already pointed at a database.
    """
    rl = entry.realmlist
    return (
        f"SELECT {', '.join(realmlist_columns(entry))} "
        f"FROM {entry.databases.auth}.{rl.table} WHERE id={rl.realm_id};"
    )


def _alf_notes(state: platform.AlfState) -> tuple[list[str], list[str]]:
    """(warnings, manual steps) for a macOS firewall state. Never a command to run.

    The split follows what the user can act on: a warning is something wrong
    with the machine's configuration that stops players connecting, a manual
    step is a thing only they can do. Nothing here is automatable — every
    mutation needs root, and this path does not ask for passwords.

    An off firewall and a working one produce neither: the status line on the
    plan is the whole truth, and inventing advice for a machine that is fine is
    how a networking screen teaches people to ignore it.
    """
    warnings: list[str] = []
    manual: list[str] = []
    if state.enabled is None:
        manual.append(
            "Yu'lon could not read the macOS firewall state, so it is reported as unchecked "
            "rather than OK. Check it yourself in System Settings -> Network -> Firewall. If it "
            'is on and players cannot connect, set "Docker" to "Allow incoming connections" '
            "under Options."
        )
        return warnings, manual
    if not state.enabled:
        return warnings, manual
    if state.block_all:
        warnings.append(
            'the firewall is set to "Block all incoming connections": the allow list is ignored, '
            "so players cannot reach the server no matter what is allowed. To host, turn that "
            "off yourself in System Settings -> Network -> Firewall -> Options - it is a "
            "security choice Yu'lon will not make for you."
        )
        return warnings, manual
    if state.docker_backend == "blocked":
        command = " ".join(["sudo", *platform.alf_unblock_commands()[0]])
        manual.append(
            "The macOS firewall is blocking Docker Desktop, which is the program that actually "
            "receives player connections - the server listens inside Docker's VM, so allowing "
            "Yu'lon itself would change nothing. Yu'lon never asks for your password, so run "
            f"this yourself and then plan again: {command}"
        )
    elif state.docker_backend == "unlisted":
        manual.append(
            'macOS may ask about "Docker" the first time the server listens with the firewall '
            "on - click Allow. Signed apps are normally allowed automatically; if players still "
            "cannot connect, check System Settings -> Network -> Firewall -> Options."
        )
    elif state.docker_backend is None:
        warnings.append(
            "the firewall is on but whether Docker Desktop is allowed could not be read "
            '(unchecked). If players cannot connect, set "Docker" to "Allow incoming '
            'connections" in System Settings -> Network -> Firewall -> Options.'
        )
    return warnings, manual


def plan(
    entry: CatalogEntry,
    mode: Mode,
    *,
    lan_ip: str | None = None,
    public_ip: str | None = None,
    bindings: Mapping[int, str] | None = None,
    firewall: platform.FirewallBackend | None = None,
    steamos: bool | None = None,
    wsl: bool | None = None,
    rule_prefix: str = "Yulon",
    detect_lan: Callable[[], str | None] = platform.detect_lan_ip,
    detect_public: Callable[[], platform.PublicIpResult] = platform.detect_public_ip,
    detect_alf: Callable[[], platform.AlfState] = platform.detect_alf_state,
) -> NetworkPlan:
    """Compute the plan for `mode`. Detection seams default to the real platform probes."""
    ports = (entry.ports.auth, entry.ports.world)
    backend = firewall if firewall is not None else platform.detect_firewall()
    on_steamos = steamos if steamos is not None else platform.is_steamos()
    in_wsl = wsl if wsl is not None else platform.in_wsl()
    lan = lan_ip if lan_ip is not None else detect_lan()
    public = public_ip
    probe: platform.PublicIpResult | None = None
    if mode == "internet" and public is None:
        probe = detect_public()
        public = probe.address

    warnings: list[str] = []
    manual: list[str] = []

    fw_cmds = platform.firewall_commands(
        backend, ports, rule_prefix=rule_prefix, steamos=on_steamos
    )
    alf_state: platform.AlfState | None = None
    if backend == "alf":
        # macOS gets a state, not a command list: its firewall is
        # per-application and has no port vocabulary at all.
        alf_state = detect_alf()
        alf_warnings, alf_manual = _alf_notes(alf_state)
        warnings.extend(alf_warnings)
        manual.extend(alf_manual)
    elif backend == "none":
        manual.append(
            "No supported firewall tool (ufw/firewalld) was found; if a firewall is active, "
            f"allow inbound TCP {', '.join(map(str, ports))} by hand."
        )

    proxy_cmds: list[list[str]] = []
    if bindings:
        loopback = [p for p in ports if bindings.get(p, "").startswith("127.")]
        if loopback:
            warnings.append(
                f"ports {loopback} are published on 127.0.0.1, not 0.0.0.0 — other machines "
                "cannot reach them. Fix the compose port bindings"
                + (
                    " (a WSL2 portproxy is added as a stopgap)."
                    if in_wsl or backend == "netsh"
                    else (
                        " On Docker Desktop for Mac a loopback binding you did not ask for "
                        "usually means its port-binding setting is local-only; change it back "
                        "in Docker Desktop's settings and recreate the stack. No firewall "
                        "change can fix a loopback binding."
                        if backend == "alf"
                        else "."
                    )
                )
            )
            if (in_wsl or backend == "netsh") and lan:
                proxy_cmds = platform.portproxy_commands(lan, loopback)

    sql: str | None = None
    client_realmlist: str | None = None
    if lan is None:
        warnings.append("could not determine this machine's LAN IP — is it on a network?")
    elif mode == "lan":
        sql = realmlist_sql(entry, lan, lan)
        client_realmlist = lan
    elif public is None and probe is not None and probe.verification_failed:
        # Not "offline?": the lookup reached a server and refused to trust it, so
        # nothing was learned about this connection. Saying "offline" here sends
        # the user to the router for a problem that lives in the root store.
        warnings.append(
            "could not determine the public IP — the lookup service's certificate could not be "
            "verified, which is not the same as being offline, so internet play is not "
            f"configured and the connection itself is untested. {platform.CERT_VERIFY_FIX}"
        )
    elif public is None:
        warnings.append(
            "could not determine the public IP (offline?) — internet play not configured"
        )
    else:
        sql = realmlist_sql(entry, public, lan)
        client_realmlist = public
        if platform.is_cgnat(public):
            warnings.append(
                f"public address {public} is carrier-grade NAT / private: this ISP connection "
                "cannot accept port forwards. Ask the ISP for a public IP or use a VPN/tunnel."
            )
        manual.append(
            f"Router: give this machine ({lan}) a DHCP reservation so its LAN IP stays fixed."
        )
        manual.append(
            "Router: forward TCP (not UDP) ports "
            + " and ".join(f"{p} → {lan}:{p}" for p in ports)
            + ". Look for 'Port Forwarding', 'Virtual Server' or 'NAT'."
        )
        manual.append(
            f"Public IPs change; if friends suddenly cannot connect, re-run this. For a stable "
            f"name use a free dynamic DNS such as {_DUCKDNS}."
        )
        manual.append(
            f"LAN players still use {lan}; only remote players use {public} "
            "(most home routers have no hairpin NAT)."
        )

    if backend == "netsh":
        manual.append(
            "Windows: set the network profile to Private (Settings → Network & Internet)."
        )

    return NetworkPlan(
        mode=mode,
        game_id=entry.id,
        lan_ip=lan,
        public_ip=public,
        ports=ports,
        firewall=backend,
        firewall_commands=tuple(tuple(c) for c in fw_cmds),
        portproxy_commands=tuple(tuple(c) for c in proxy_cmds),
        realmlist_sql=sql,
        client_realmlist=client_realmlist,
        manual_steps=tuple(manual),
        warnings=tuple(warnings),
        firewall_state=alf_state,
    )


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def apply(
    network_plan: NetworkPlan,
    *,
    sql: SqlRunner | None,
    run: Runner | None = None,
    elevate: bool = True,
) -> NetworkReport:
    """Execute the automatable part of `network_plan`; report the rest by name.

    Linux firewall commands run under `sudo -n` (non-interactive): if sudo
    needs a password the step is SKIPPED with the exact command to paste, never
    blocked on an invisible prompt. The realmlist UPDATE needs a `SqlRunner`
    for the server's auth DB; without one it is skipped and reported.
    """
    do = run if run is not None else (lambda argv: runner.run(argv))
    done: list[str] = []
    skipped: list[str] = []
    # Per-backend rather than a linux/not-linux boolean: the else-branch of that
    # boolean told everyone who was not on ufw or firewalld to retry "in an
    # Administrator PowerShell", which a Mac user would have been handed the
    # moment `alf` existed.
    policy = platform.elevation_policy(network_plan.firewall)

    for cmd in network_plan.firewall_commands + network_plan.portproxy_commands:
        argv = list(cmd)
        if policy.prefix and elevate:
            argv = [*policy.prefix, *argv]
        try:
            proc = do(argv)
        except OSError as exc:
            skipped.append(f"{' '.join(cmd)}: {exc}")
            continue
        if proc.returncode == 0:
            done.append(" ".join(cmd))
        else:
            skipped.append(
                f"{' '.join(cmd)}: exit {proc.returncode} {proc.stderr.strip()} — run it by hand"
                + policy.retry_hint
            )

    restart = False
    if network_plan.realmlist_sql is not None:
        if sql is None:
            skipped.append(f"realmlist not updated (no DB access): {network_plan.realmlist_sql}")
        else:
            try:
                sql.run_statement("auth", network_plan.realmlist_sql)
            except ApplyError as exc:
                # Reported, not raised. Every firewall failure above lands in
                # `skipped` and the report survives; this one used to leave
                # through the top of the function and take with it the record of
                # the rules that HAD been applied — so a user whose realmlist
                # update failed was also not told which ports were now open, nor
                # which command to retry by hand (Discord report, 2026-08-26).
                skipped.append(f"realmlist not updated: {exc}")
            else:
                done.append(f"realmlist → {network_plan.client_realmlist}")
                restart = True

    logger.info(
        f"networking {network_plan.mode} for {network_plan.game_id}: "
        f"{len(done)} done, {len(skipped)} skipped, {len(network_plan.manual_steps)} manual"
    )
    return NetworkReport(
        plan=network_plan, done=tuple(done), skipped=tuple(skipped), restart_required=restart
    )


def write_client_realmlist(
    client_dir: Path, address: str, realmlist_file: str = "realmlist.wtf"
) -> Path:
    """Set `set realmlist <address>` in the user's own client (README §13 LAN step 3).

    Finds the file under `Data/<locale>/` (retail layout) or at the top level
    (repack layout); writes the first one found, creating `Data/enUS/` if none.
    """
    candidates = sorted((client_dir / "Data").glob(f"*/{realmlist_file}")) + [
        client_dir / realmlist_file
    ]
    target = next(
        (c for c in candidates if c.is_file()), client_dir / "Data" / "enUS" / realmlist_file
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        target.read_text(encoding="utf-8", errors="replace").splitlines()
        if target.is_file()
        else []
    )
    kept = [ln for ln in lines if not ln.strip().lower().startswith("set realmlist")]
    target.write_text(
        "\n".join([f"set realmlist {address}", *kept]).rstrip("\n") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target
