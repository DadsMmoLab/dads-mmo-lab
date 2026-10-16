"""Where a single-container server tells its clients to connect (`catalog.AdvertisedAddress`).

The WoW cores keep that address in a realmlist row, which `networking.py` updates over
SQL. A server of the second kind keeps it in a file: DDON's login server reads
`GameServerList.csv` at start and hands each client the `IpAddress` of the game server
to dial next. The install engine copies that file out of the checkout once
(`single_install._seed_files`) and the compose file mounts the copy, so this module
rewrites one column of the copy and nothing else, and the server reads it on its next
start.

A file this module cannot read with certainty is refused rather than guessed at: a
quoted field or a header without the column is not rewritten, because a wrong column
would advertise a port as an address, and the server would hand that to every client.
"""

from __future__ import annotations

import ipaddress
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner
from yulon.catalog.catalog import SingleEntry

LOOPBACK = "127.0.0.1"
"""What a client on this computer dials: the address the shipped file advertises."""

BOM = chr(0xFEFF)
"""The UTF-8 byte order mark the shipped GameServerList.csv starts with."""

RULE_PREFIX = "Yulon"
"""The Windows Firewall rule name prefix; `platform.firewall_commands()` adds the port."""


class AddressFileError(RuntimeError):
    """The address file could not be read or written safely; the message says why."""


@dataclass(frozen=True)
class Reach:
    """One answer to "who can connect?": its mode, the words a player reads, the address."""

    mode: str
    label: str
    address: str


def _file(entry: SingleEntry, server_dir: Path) -> Path:
    if entry.advertise is None:
        raise AddressFileError(f"{entry.name} keeps no address file Yu'lon can change.")
    return server_dir / entry.advertise.dest


def _read(path: Path) -> str:
    """The file's text with its line endings as they are on disk.

    Not `read_text()`, whose universal-newline reading turns every CRLF into LF: the
    rewrite would then hand back a file whose every line ending had changed.
    """
    return path.read_bytes().decode("utf-8")


def _split(text: str) -> tuple[str, list[str]]:
    """(the byte order mark, if any), and the lines with their own line endings."""
    bom = BOM if text.startswith(BOM) else ""
    return bom, text[len(bom) :].splitlines(keepends=True)


def _column(entry: SingleEntry, header: str) -> int:
    assert entry.advertise is not None
    names = [name.strip() for name in header.lstrip("#").rstrip("\r\n").split(",")]
    try:
        return names.index(entry.advertise.column)
    except ValueError:
        raise AddressFileError(
            f"The address file's header has no {entry.advertise.column} column "
            f"({', '.join(names)}), so it was left as it is."
        ) from None


def read_address(entry: SingleEntry, server_dir: Path) -> str | None:
    """The address the first server row advertises, or None if the file has no row yet."""
    path = _file(entry, server_dir)
    try:
        _bom, lines = _split(_read(path))
    except OSError as exc:
        raise AddressFileError(f"{path} could not be read: {exc}") from exc
    if not lines:
        return None
    index = _column(entry, lines[0])
    for line in lines[1:]:
        if line.strip() and not line.startswith("#"):
            fields = line.rstrip("\r\n").split(",")
            return fields[index].strip() if index < len(fields) else None
    return None


def check_address(address: str) -> str | None:
    """Why `address` cannot be advertised, or None. An IPv4 address only.

    The game server hands the address to the client as it is written; a host name
    or an IPv6 address has never been measured to work with this client, so neither
    is offered as if it did.
    """
    try:
        ipaddress.IPv4Address(address.strip())
    except ValueError:
        return f"{address!r} is not an IPv4 address such as 192.168.1.20."
    return None


def write_address(entry: SingleEntry, server_dir: Path, address: str) -> bool:
    """Advertise `address` in every server row; True if the file changed.

    Everything else in the file — the byte order mark, the header, the other columns
    and each line's own ending — is written back exactly as it was read. Written to
    a sibling and moved into place, so a crash cannot leave half a file.
    """
    problem = check_address(address)
    if problem is not None:
        raise AddressFileError(problem)
    path = _file(entry, server_dir)
    try:
        bom, lines = _split(_read(path))
    except OSError as exc:
        raise AddressFileError(f"{path} could not be read: {exc}") from exc
    if not lines:
        raise AddressFileError(f"{path} is empty, so there is no server row to point anywhere.")
    index = _column(entry, lines[0])
    out = [lines[0]]
    for line in lines[1:]:
        if not line.strip() or line.startswith("#"):
            out.append(line)
            continue
        if '"' in line:
            raise AddressFileError(
                f"{path} has a quoted field, which Yu'lon does not rewrite; it was left as it is."
            )
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        fields = body.split(",")
        if index >= len(fields):
            raise AddressFileError(f"A row of {path} is shorter than its header; left as it is.")
        fields[index] = address.strip()
        out.append(",".join(fields) + ending)
    text = bom + "".join(out)
    if text == bom + "".join(lines):
        return False
    temporary = path.with_name(path.name + ".yulon-new")
    try:
        temporary.write_text(text, encoding="utf-8", newline="")
        temporary.replace(path)
    except OSError as exc:
        raise AddressFileError(f"{path} could not be written: {exc}") from exc
    return True


def reaches(detect_lan: Callable[[], str | None] = platform.detect_lan_ip) -> tuple[Reach, ...]:
    """The answers a player picks from: this computer, and this network when it has one."""
    found: list[Reach] = [Reach("loopback", "Only this computer", LOOPBACK)]
    lan = detect_lan()
    if lan:
        found.append(Reach("lan", f"Computers on this network ({lan})", lan))
    return tuple(found)


def firewall_commands(entry: SingleEntry, os_name: str) -> list[list[str]]:
    """The Windows Firewall rules that let other computers reach this server's ports.

    Windows only, where Docker Desktop publishes the ports on the host itself. On
    Linux the ports are published by Docker's own rules, which a host firewall such
    as ufw does not see, so there is nothing of this kind to add.
    """
    if os_name != "windows":
        return []
    return platform.firewall_commands(
        "netsh", entry.host_ports(), rule_prefix=f"{RULE_PREFIX} {entry.name}"
    )


def commands_text(commands: Iterable[list[str]]) -> str:
    """The commands as lines a player can paste into an administrator terminal."""
    return "\n".join(platform.command_text(argv) for argv in commands)


@dataclass(frozen=True)
class FirewallReport:
    """What `open_firewall()` did: rules added, rules already there, and what it could not do."""

    added: tuple[int, ...] = ()
    already: tuple[int, ...] = ()
    skipped: tuple[str, ...] = ()


def _rule_name(entry: SingleEntry, port: int) -> str:
    return f"{RULE_PREFIX} {entry.name} {port}"


def open_firewall(
    entry: SingleEntry,
    os_name: str,
    *,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]] = runner.run,
) -> FirewallReport:
    """Let other computers reach this server: one Windows Firewall rule per port, ONE UAC prompt.

    A rule that is already there (by its name) is not added again: `netsh` adds a
    second rule of the same name without a word, so every Apply would grow the list.
    The adds go through `platform.elevated_batch()`, the same single administrator
    prompt and per-command verdicts the WoW servers' Networking uses (T644).
    """
    commands = firewall_commands(entry, os_name)
    if not commands:
        return FirewallReport()
    ports = entry.host_ports()
    already: list[int] = []
    wanted: list[tuple[int, list[str]]] = []
    for port, command in zip(ports, commands, strict=True):
        shown = run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={_rule_name(entry, port)}"]
        )
        if shown.returncode == 0:
            already.append(port)
        else:
            wanted.append((port, command))
    if not wanted:
        return FirewallReport(already=tuple(already))
    to_run = [command for _port, command in wanted]
    batch = platform.elevated_batch("netsh", to_run)
    if batch is None:
        # Already an administrator (or not Windows): run them as they are.
        added, skipped = [], []
        for port, command in wanted:
            proc = run(command)
            if proc.returncode == 0:
                added.append(port)
            else:
                skipped.append(f"{platform.command_text(command)}: {proc.stdout.strip()}")
        return FirewallReport(tuple(added), tuple(already), tuple(skipped))
    argv, results_path = batch
    try:
        proc = run(argv)
    except OSError as exc:
        proc = subprocess.CompletedProcess(argv, 1, "", str(exc))
    try:
        if proc.returncode == platform.UAC_DECLINED:
            return FirewallReport(already=tuple(already), skipped=(platform.UAC_DECLINED_TEXT,))
        results = platform.read_batch_results(results_path, len(to_run))
    finally:
        try:
            results_path.unlink()
        except OSError:
            pass
    if proc.returncode != 0 or results is None:
        return FirewallReport(
            already=tuple(already),
            skipped=(
                "The Windows administrator prompt did not say how the rules went. Run these "
                "in an administrator terminal:\n" + commands_text(to_run),
            ),
        )
    added = [port for (port, _c), (rc, _out) in zip(wanted, results, strict=True) if rc == 0]
    skipped = [
        f"{platform.command_text(command)}: {out}"
        for (_p, command), (rc, out) in zip(wanted, results, strict=True)
        if rc != 0
    ]
    return FirewallReport(tuple(added), tuple(already), tuple(skipped))
