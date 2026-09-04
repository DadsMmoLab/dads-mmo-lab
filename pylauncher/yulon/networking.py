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

One command in that set is not like the others and is treated as such:
`ufw enable` brings up a default-DENY-incoming policy, so on the headless
server this feature exists for it ends the operator's SSH session and every
one after it. Nothing here turns a firewall on unless asked to
(`plan(enable_firewall=True)`), and when asked it either keeps SSH reachable
on the port the RUNNING system says sshd is using or refuses and says so —
on `NetworkPlan.refusals`, in `warnings`, and in `NetworkReport.skipped`,
because the gate that found this read two of those and both were empty
(bug-checklist §39).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import platform, runner
from yulon.apply import ApplyError, SqlRunner
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

Mode = Literal["lan", "internet"]

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]

_DUCKDNS = "https://www.duckdns.org/"


@dataclass(frozen=True)
class SshRoute:
    """What the RUNNING system says about SSH being a way into this machine.

    Three fields, because the question has three answers and collapsing any two
    of them is how bug-checklist §39 happened:

    * `connected` — this process's session arrived over SSH. True is proof of a
      remote operator. False is NOT proof of a local one: `sudo` resets the
      environment, a systemd unit never had it, and a `tmux` session started at
      the console keeps none of it while its owner attaches over SSH from a
      beach. So False is used to allow nothing on its own.
    * `ports` — every port that has to keep admitting SSH.
    * `listeners_readable` — whether the listener probe could actually see. An
      empty `ports` from a probe that read the socket table and an empty
      `ports` from a probe that was not allowed to read it are the same tuple
      and opposite facts, and only the first one means "there is no SSH here to
      lock out". Keeping them apart is the difference between a decision and a
      guess.
    """

    connected: bool = False
    ports: tuple[int, ...] = ()
    listeners_readable: bool = True


_SS_ARGV = ("ss", "--no-header", "--listening", "--tcp", "--numeric", "--processes")
"""Ask the kernel which TCP sockets are listening, and who owns them.

Deliberately NOT `sshd_config`. That file is not ours to parse — one sshd can
carry several `Port` lines, `Match` blocks and `Include`s — and, worse, it
records what sshd was ASKED to do rather than what it is doing: an admin who
edited it without reloading, a distro that overrides the port in a systemd
socket unit, or a second sshd on another port all make the file and the machine
disagree. The socket table is the machine's own answer and cannot disagree with
itself. `--processes` is what turns "something listens on 2022" into "sshd
listens on 2022"; it is also the part that needs privilege, which is why the
probe reports whether it could see anything at all.
"""

_SS_TIMEOUT_SECONDS = 5.0
"""`ss` answers in milliseconds; a bound only exists so a wedged probe cannot
hang a plan the user is watching."""


def detect_ssh_route(
    environ: Mapping[str, str] | None = None, run: Runner | None = None
) -> SshRoute:
    """Which ports must keep admitting SSH, and whether this session came in over it.

    Two sources, unioned rather than ranked, because they answer slightly
    different questions:

    * `SSH_CONNECTION` (and `SSH_CLIENT`, which some setups still export) names
      the port THIS session arrived on — field four is the server-side port
      sshd accepted us on. That is the one port provably admitting the operator
      standing here, and it is free: no subprocess, no privilege.
    * `ss` names every port sshd is listening on, which covers the operator who
      is not here yet — a firewall that cuts tomorrow's login is the same bug
      one day later — and the case where the environment was stripped.

    Neither is sufficient alone: the environment is gone under `sudo`, in a
    systemd unit and inside a `tmux` session that predates the login, and the
    socket table is unreadable to a probe that is not root.
    """
    env = os.environ if environ is None else environ
    do = run if run is not None else (lambda argv: runner.run(argv, timeout=_SS_TIMEOUT_SECONDS))
    # `SSH_CONNECTION` = "client-ip client-port server-ip server-port";
    # `SSH_CLIENT` = "client-ip client-port server-port". Both end with the
    # port on THIS machine, which is the one that has to stay open.
    ports: set[int] = set()
    connected = False
    for name, field in (("SSH_CONNECTION", 3), ("SSH_CLIENT", 2)):
        said = (env.get(name) or "").split()
        connected = connected or bool(said)
        if len(said) > field:
            with suppress(ValueError):
                ports.add(int(said[field]))
    listening, readable = _sshd_listening_ports(do)
    return SshRoute(
        connected=connected,
        ports=tuple(sorted(ports | listening)),
        listeners_readable=readable,
    )


def _sshd_listening_ports(run: Runner) -> tuple[set[int], bool]:
    """(ports sshd is listening on, whether the socket table could be read at all).

    The second half is the load-bearing one. `ss --processes` can only name the
    owner of a socket it is allowed to read, and sshd runs as root — so an
    unprivileged probe on a perfectly ordinary server lists the socket with no
    owner and finds no sshd, which is indistinguishable from a box that has no
    sshd unless somebody says so. "No line carried an owner" is that somebody.
    """
    try:
        proc = run(list(_SS_ARGV))
    except OSError:
        # No `ss` on this box (or no permission to execute it): not an answer.
        return set(), False
    if proc.returncode != 0:
        return set(), False
    ports: set[int] = set()
    attributed = False
    for line in proc.stdout.splitlines():
        socket, _, owners = line.partition("users:(")
        if not owners:
            continue
        attributed = True
        # `"sshd` rather than `sshd`: it matches OpenSSH 9.8's split listener
        # (`sshd-session`) and does not match a process merely called `xsshd`.
        if '"sshd' not in owners:
            continue
        fields = socket.split()
        if len(fields) < 4:
            continue
        # State Recv-Q Send-Q Local:Port Peer:Port — and `[::]:22` for v6, so
        # the port is what follows the LAST colon.
        with suppress(ValueError):
            ports.add(int(fields[3].rsplit(":", 1)[-1]))
    return ports, attributed


def _turns_ufw_on(command: Iterable[str]) -> bool:
    """True for a command that puts ufw's default-deny policy into effect.

    Both spellings, because withholding one and running the other only moves
    the lockout: `ufw enable` does it now, `systemctl enable ufw` does it at the
    next boot — which is worse, since by then nobody connects the outage to
    this button. `systemctl enable --now firewalld` is deliberately NOT matched;
    see `_guard_the_way_back_in()` for why firewalld is not this bug.
    """
    argv = list(command)
    if not argv:
        return False
    rest = argv[1:]
    if argv[0] == "ufw" and "enable" in rest:
        return True
    # `ufw.service` as well as `ufw`: systemd accepts both names for the same
    # unit, and a predicate that recognises only the spelling in use today is
    # one edit away from waving the lockout through.
    names_ufw = any(argument.split(".")[0] == "ufw" for argument in rest)
    return argv[0] == "systemctl" and "enable" in rest and names_ufw


UFW_ENABLE_WITHHELD = (
    "Yu'lon opened the game ports in ufw's rule list but did NOT run `ufw enable`: turning a "
    "firewall on can only take reachability away, it is no part of making a server reachable, "
    "and on a machine you reach over SSH it takes away your own way in — which is exactly what "
    "it did to the box that found this (bug-checklist §39). ufw is left as you had it. To turn "
    "it on yourself, allow your SSH port FIRST: `sudo ufw allow <your ssh port>/tcp`, then "
    "`sudo ufw enable`."
)
"""Said on every ufw plan, because a command in the guide's block was not run.

A withheld enable is not a failure and is not the user's homework, so it is a
warning and a refusal rather than a `manual_steps` entry — `manual_steps` is
the list of things that MUST happen for players to connect, and this is not one
of them. It is on `warnings` as well as `refusals` because `warnings` is what
the controller view renders today, and a refusal nobody can see is the shape of
the original defect: `report.skipped` and `report.manual_steps` were both empty
while the machine was being locked.
"""


def _guard_the_way_back_in(
    commands: list[list[str]], *, enable_firewall: bool, route: SshRoute | None
) -> tuple[list[list[str]], tuple[int, ...], list[str], list[str]]:
    """Make `commands` safe to run on a box whose only route in is SSH.

    Returns `(commands, ssh_ports, refusals, warnings)`, where `ssh_ports` are
    the ports opened solely to keep SSH reachable — `apply()` needs them by
    number so it can check the rules ARRIVED before it enables anything.

    Two decisions live here, and the second is the reason the first is cheap:

    **The enable is opt-in.** `ufw allow` takes effect immediately on an active
    ufw and is staged on an inactive one, so the rules the user asked for land
    either way and `ufw enable` is never needed for the request. What it does
    do is change the machine's security posture — and, on the headless server
    this feature exists for, end the operator's session. A step should not run
    a command that cannot advance its goal and can destroy access. So the
    default withholds it and says so, and `enable_firewall=True` is the path
    for a caller that really is asking to turn the firewall on.

    **When it IS asked for, SSH is preserved or the enable is refused.** Never
    a bare `ufw allow 22/tcp`: sshd may be anywhere, so the port comes from
    `SshRoute` — the running system. If no port can be established the enable
    is dropped, because refusing costs the user nothing they had a second ago
    and enabling wrongly costs them the machine.

    firewalld is not routed through here at all, and that is a decision rather
    than an oversight: its `public` zone ships the `ssh` service allowed, so
    `systemctl enable --now firewalld` does not cut SSH, and `firewall-cmd
    --reload` needs the daemon running — withholding that start would break a
    path that works to prevent a lockout this backend does not cause.
    """
    if not enable_firewall:
        return (
            [c for c in commands if not _turns_ufw_on(c)],
            (),
            [UFW_ENABLE_WITHHELD],
            [UFW_ENABLE_WITHHELD],
        )
    asked = route if route is not None else SshRoute(listeners_readable=False)
    if not asked.ports:
        if not asked.connected and asked.listeners_readable:
            # The socket table was read and nothing is listening for SSH: there
            # is no way in to preserve, so the guide's three commands run
            # exactly as they always did — and a machine that is fine is told
            # nothing at all.
            return commands, (), [], []
        why = (
            "this session arrived over SSH (SSH_CONNECTION is set) and the port sshd is "
            "listening on could not be established"
            if asked.connected
            else "this machine's listening sockets could not be read, so Yu'lon cannot tell "
            "whether enabling ufw would cut an SSH login"
        )
        refusal = (
            f"REFUSED to enable ufw: {why}. The game ports are in ufw's rule list and nothing "
            "was enabled. Allow your SSH port and enable it yourself: `sudo ufw allow <your "
            "ssh port>/tcp`, then `sudo ufw enable`."
        )
        return [c for c in commands if not _turns_ufw_on(c)], (), [refusal], [refusal]
    wanted = [["ufw", "allow", f"{port}/tcp"] for port in asked.ports]
    new = [c for c in wanted if c not in commands]
    first_enable = next(i for i, c in enumerate(commands) if _turns_ufw_on(c))
    guarded = commands[:first_enable] + new + commands[first_enable:]
    said = ", ".join(str(port) for port in asked.ports)
    return (
        guarded,
        asked.ports,
        [],
        [
            f"ufw is being turned ON, and SSH (port {said}) is allowed through it so this "
            "machine stays reachable. That port was read from the running system, not from "
            "sshd_config. Every other inbound connection will be blocked."
        ],
    )


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
    refusals: tuple[str, ...] = ()
    """What this plan DECLINED to do, and why, in a sentence a person can act on.

    Its own field rather than a flavour of `warnings` because a refusal is a
    fact about the plan — a command from the backend's block is missing from
    `firewall_commands` — and a caller that wants to know whether anything was
    held back should not have to grep prose. Every entry is also copied into
    `warnings` (which the controller view renders) and into
    `NetworkReport.skipped`, since an invisible refusal is what bug-checklist
    §39 actually was.
    """
    ssh_ports: tuple[int, ...] = ()
    """Ports in `firewall_commands` that are there only to keep SSH reachable.

    By number, so `apply()` can check the rule ARRIVED before it runs anything
    that turns the firewall on. Empty unless an enable was both requested and
    guarded.
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
    refusals: tuple[str, ...] = ()
    """The plan's refusals, plus anything `apply()` refused once it saw the machine.

    Also present in `skipped`, deliberately: this field is for a caller that
    wants the refusals apart from the failures, and `skipped` is for the caller
    that only ever reads one list.
    """

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
    enable_firewall: bool = False,
    detect_ssh: Callable[[], SshRoute] = detect_ssh_route,
    detect_lan: Callable[[], str | None] = platform.detect_lan_ip,
    detect_public: Callable[[], platform.PublicIpResult] = platform.detect_public_ip,
    detect_alf: Callable[[], platform.AlfState] = platform.detect_alf_state,
) -> NetworkPlan:
    """Compute the plan for `mode`. Detection seams default to the real platform probes.

    `enable_firewall` is the one knob that can turn a firewall ON, and it is off
    by default — see `_guard_the_way_back_in()` for the argument. A caller that
    passes True gets the enable only if SSH survives it, and `detect_ssh` is the
    seam that decides: it is consulted ONLY when an enable is on the table, so
    an ordinary plan spawns no probe and asks the environment nothing.
    """
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
    refusals: list[str] = []
    ssh_ports: tuple[int, ...] = ()

    fw_cmds = platform.firewall_commands(
        backend, ports, rule_prefix=rule_prefix, steamos=on_steamos
    )
    if any(_turns_ufw_on(c) for c in fw_cmds):
        # The machine is asked about SSH only when there is an enable to guard;
        # a plan that turns nothing on has nothing to lock anybody out of.
        fw_cmds, ssh_ports, ufw_refusals, ufw_warnings = _guard_the_way_back_in(
            fw_cmds,
            enable_firewall=enable_firewall,
            route=detect_ssh() if enable_firewall else None,
        )
        refusals.extend(ufw_refusals)
        warnings.extend(ufw_warnings)
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
        refusals=tuple(refusals),
        ssh_ports=ssh_ports,
    )


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

    Anything the plan REFUSED to do arrives here already decided, and is copied
    into `skipped` as well as `refusals`: a reader that only knows about
    `skipped` — which is what the gate that found bug-checklist §39 read — must
    not see an empty list while a command from the block went unrun.
    """
    do = run if run is not None else (lambda argv: runner.run(argv))
    done: list[str] = []
    refusals: list[str] = list(network_plan.refusals)
    skipped: list[str] = list(network_plan.refusals)
    # Per-backend rather than a linux/not-linux boolean: the else-branch of that
    # boolean told everyone who was not on ufw or firewalld to retry "in an
    # Administrator PowerShell", which a Mac user would have been handed the
    # moment `alf` existed.
    policy = platform.elevation_policy(network_plan.firewall)

    for cmd in network_plan.firewall_commands + network_plan.portproxy_commands:
        missing = _ssh_rules_still_missing(network_plan, done) if _turns_ufw_on(cmd) else ()
        if missing:
            # The plan can only DECLARE that SSH stays reachable; whether the
            # rule arrived is a fact about this machine, and `sudo -n ufw allow
            # 2222/tcp` can fail on its own while the enable behind it would
            # have succeeded. A guard that trusts the declaration is the same
            # lockout with a paper trail.
            refusal = (
                f"REFUSED to run `{' '.join(cmd)}`: the rule that keeps SSH reachable "
                f"(ufw allow {', '.join(f'{p}/tcp' for p in missing)}) did not apply, so "
                "enabling ufw would have cut the way back into this machine."
            )
            refusals.append(refusal)
            skipped.append(refusal)
            continue
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
        f"networking {network_plan.mode} for {network_plan.game_id}: {len(done)} done, "
        f"{len(skipped)} skipped ({len(refusals)} refused), "
        f"{len(network_plan.manual_steps)} manual"
    )
    return NetworkReport(
        plan=network_plan,
        done=tuple(done),
        skipped=tuple(skipped),
        restart_required=restart,
        refusals=tuple(refusals),
    )


def _ssh_rules_still_missing(network_plan: NetworkPlan, done: list[str]) -> tuple[int, ...]:
    """The SSH ports this plan promised to open that have not actually been opened yet."""
    return tuple(port for port in network_plan.ssh_ports if f"ufw allow {port}/tcp" not in done)


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
