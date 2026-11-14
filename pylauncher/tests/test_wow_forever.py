"""WoW Forever's installable entry: its catalog shape, the stack engine's two extra stages,
and Play's four parts (`forever_key`, `forever_trust`, `forever_account`, `forever_play`).

Every external effect is a fake: no daemon, no network, no game process, no UAC prompt.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from yulon import docker, forever_account, forever_key, forever_play, forever_trust, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import ForeverLogin, SingleEntry, SinglePlay, load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.catalog.stack_install import (
    STACK_TEMPLATES,
    StackInstaller,
    StackSeams,
    client_build,
    client_problem,
    stack_of,
    stages_of,
)
from yulon.single_play import PlayRefusal

CATALOG = load_catalog()
FOREVER: SingleEntry = CATALOG.get_single("wow-forever")
STACK = stack_of(FOREVER)
EXTRACT = STACK.client_extract
LOGIN = FOREVER.play.forever if FOREVER.play is not None else None
KEY = bytes.fromhex("02596F0D0C061A8B30745988FD72C59E29EC367FB0F341F28E0F08D037BAFC69")
TDB = b"7z pretend TDB release"


# -- the catalog entry ---------------------------------------------------------------


def test_wow_forever_is_a_forevercore_stack_with_its_tdb_its_client_extract_and_its_login() -> None:
    assert STACK.family == "forevercore"
    assert {i.role for i in STACK.images} == {"server", "database"}
    assert all("@sha256:" in i.ref for i in STACK.images)
    assert [s.service for s in STACK.services] == ["database", "prepare", "world", "bnet"]
    assert [s.service for s in STACK.services if s.once] == ["prepare"]
    assert STACK.downloads[0].file.url.startswith(
        "https://github.com/Karrz1/TrinityCore/releases/download/forever-tdb-2026-10-14/"
    )
    assert STACK.downloads[0].file.size == 146573540
    assert EXTRACT is not None and EXTRACT.exe == "_forever_/Wow.exe"
    builds = {b.build: b.exe_size for b in EXTRACT.builds}
    assert EXTRACT.builds[0].build == "70707" and builds["70707"] == 85829840
    assert builds["70888"] == 80627920 and "70938" in builds
    assert FOREVER.install.client_extract is EXTRACT
    assert LOGIN is not None and LOGIN.region_group == 8 and LOGIN.ca == "certs/ca.pem"
    assert FOREVER.play is not None and FOREVER.play.args == ("-config", LOGIN.config)
    assert FOREVER.play.account is False
    assert FOREVER.host_ports() == (1119, 8082, 8085)


def test_forever_runs_the_two_extra_stages_and_ffxi_does_not() -> None:
    assert stages_of(STACK) == (
        "fetch-loader",
        "fetch-downloads",
        "write-secrets",
        "generate-compose",
        "pull-images",
        "seed-volumes",
        "extract-client",
        "up",
        "ready",
    )
    ffxi = stack_of(CATALOG.get_single("ffxi"))
    assert "fetch-downloads" not in stages_of(ffxi) and "extract-client" not in stages_of(ffxi)


def _play(**changes: Any) -> dict[str, Any]:
    assert FOREVER.play is not None
    return {**FOREVER.play.model_dump(), **changes}


def test_a_forever_login_must_start_the_client_with_its_own_config() -> None:
    with pytest.raises(ValidationError, match="play.args must be"):
        SinglePlay.model_validate(_play(args=["-config", "Other.wtf"]))
    with pytest.raises(ValidationError, match="asks for the account at its login screen"):
        SinglePlay.model_validate(_play(account=True))


def test_a_forever_login_key_is_64_hex_and_its_region_group_one_of_twelve() -> None:
    assert LOGIN is not None
    base = LOGIN.model_dump()
    with pytest.raises(ValidationError):
        ForeverLogin.model_validate({**base, "public_key": "00"})
    with pytest.raises(ValidationError):
        ForeverLogin.model_validate({**base, "region_group": 13})
    with pytest.raises(ValidationError):
        ForeverLogin.model_validate({**base, "ca": "../outside.pem"})


# -- the client check ------------------------------------------------------------------


def _client(tmp_path: Path, *, size: int = 85829840, build: str = "1.60.1.70707") -> Path:
    root = tmp_path / "World of Warcraft"
    (root / "_forever_").mkdir(parents=True)
    with open(root / "_forever_" / "Wow.exe", "wb") as handle:
        handle.truncate(size)
    (root / ".build.info").write_text(f"Branch!STRING:0|Version!STRING:0\nus|{build}\n")
    return root


def test_the_client_check_takes_the_70707_client_and_a_battle_net_70888_copy(
    tmp_path: Path,
) -> None:
    assert EXTRACT is not None
    assert client_problem(EXTRACT, _client(tmp_path)) is None
    battle_net = _client(tmp_path / "bnet", size=80627920, build="1.60.1.70888")
    assert client_problem(EXTRACT, battle_net) is None
    assert client_build(EXTRACT, battle_net) == "70888"


def test_the_client_check_refuses_no_folder_no_exe_another_size_and_another_build(
    tmp_path: Path,
) -> None:
    assert EXTRACT is not None
    assert "Choose" in (client_problem(EXTRACT, None) or "")
    assert "There is no _forever_/Wow.exe" in (client_problem(EXTRACT, tmp_path) or "")
    other = _client(tmp_path / "a", size=1000)
    assert "not the 85,829,840" in (client_problem(EXTRACT, other) or "")
    other_build = _client(tmp_path / "b", build="1.60.1.70999")
    assert "names none of the builds" in (client_problem(EXTRACT, other_build) or "")
    half = _client(tmp_path / "c", build="1.60.1.70888")  # 70888 named, 70707's Wow.exe
    assert "not the 80,627,920 of build 70888" in (client_problem(EXTRACT, half) or "")


# -- the install engine's two stages ----------------------------------------------------


@dataclass
class Daemon:
    """A fake Docker and network for a WoW Forever install."""

    runs: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    extract_exit: int = 0
    now: float = 0.0

    def download(self, url: str, dest: Path) -> Path:
        self.downloads.append(url)
        dest.write_bytes(TDB)
        return dest

    def run_once(
        self,
        server_dir: Path,
        service: str,
        *,
        profiles: Sequence[str] = (),
        volumes: Sequence[str] = (),
        sink: Callable[[str], None],
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        self.runs.append((service, tuple(profiles), tuple(volumes)))
        sink("[yulon] map data ready")
        return docker.AttachedRun(returncode=self.extract_exit, tail=("mapextractor failed",))

    def run_log(self, container: str) -> docker.RunLog:
        service = next(s for s in STACK.services if s.container == container)
        return docker.RunLog("\n".join(service.ready), True)

    def seams(self) -> StackSeams:
        return StackSeams(
            platform_id=lambda: "windows",
            docker_ready=lambda: True,
            ensure_docker=_never_provision,
            dir_problem=lambda _p: None,
            download=self.download,
            port_conflicts=lambda _ports: [],
            container_exists=lambda _name: False,
            container_project=lambda _name: None,
            pull=lambda *_a, **_k: docker.AttachedRun(returncode=0, tail=()),
            run_once=self.run_once,
            up=lambda *_a, **_k: None,
            run_log=self.run_log,
            state_of=lambda _c: docker.ContainerState(status="running"),
            token=lambda n: "ab" * n,
            sleep=lambda s: None,
            clock=lambda: self.now,
        )


def _never_provision(**_: Any) -> platform.ProvisionReport:
    raise AssertionError("Docker answered; nothing should be provisioned")


def _pinned_to_fake_tdb() -> SingleEntry:
    item = STACK.downloads[0]
    file = item.file.model_copy(
        update={"sha256": hashlib.sha256(TDB).hexdigest(), "size": len(TDB)}
    )
    stack = STACK.model_copy(update={"downloads": (item.model_copy(update={"file": file}),)})
    install = FOREVER.install.model_copy(update={"stack": stack})
    return FOREVER.model_copy(update={"install": install})


def _install(daemon: Daemon, server_dir: Path, client: Path | None) -> list[str]:
    engine = StackInstaller(_pinned_to_fake_tdb(), seams=daemon.seams())
    return list(engine.run(InstallOptions(server_dir=server_dir, client_dir=client)))


def test_an_install_fetches_the_tdb_and_extracts_from_the_client_read_only(
    tmp_path: Path,
) -> None:
    daemon = Daemon()
    client = _client(tmp_path)
    server_dir = tmp_path / "forever"
    out = _install(daemon, server_dir, client)
    assert [line.removeprefix("--- ") for line in out if line.startswith("--- ")] == list(
        stages_of(STACK)
    )
    assert (server_dir / "downloads" / "TDB_full_forever_2026_10_14.7z").read_bytes() == TDB
    assert daemon.runs == [("extract", ("seed",), (f"{client}:/client:ro",))]
    for name, _template in STACK_TEMPLATES:
        text = (server_dir / name).read_text(encoding="utf-8")
        assert "{{" not in text
    assert "wowforever-world" in (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")


def test_an_install_without_the_client_writes_nothing(tmp_path: Path) -> None:
    server_dir = tmp_path / "forever"
    with pytest.raises(InstallerError, match="Nothing was written"):
        _install(Daemon(), server_dir, None)
    assert not server_dir.exists()


def test_a_kept_tdb_is_not_fetched_again_and_a_failed_extract_says_so(tmp_path: Path) -> None:
    client = _client(tmp_path)
    server_dir = tmp_path / "forever"
    daemon = Daemon(extract_exit=7)
    with pytest.raises(InstallerError, match="could not be made"):
        _install(daemon, server_dir, client)
    again = Daemon()
    _install(again, server_dir, client)
    assert again.downloads == []


# -- forever_key ---------------------------------------------------------------------


def _table(key_for_8: bytes = b"\x11" * 32) -> bytes:
    rows = []
    for index in range(12):
        key = key_for_8 if index == 7 else bytes([index + 1]) * 32
        rows.append(
            (index + 1).to_bytes(4, "little") + key + bytes([forever_key.FLAGS[index], 0, 0, 0])
        )
    return b"".join(rows)


def test_the_key_table_is_found_by_its_layout_and_only_on_a_four_byte_boundary() -> None:
    table = _table()
    assert forever_key.looks_like_table(table)
    assert forever_key.tables_in(b"\0" * 8 + table + b"\0" * 8, 0x1000) == [0x1008]
    assert forever_key.tables_in(b"\0" * 2 + table, 0x1000) == []
    assert forever_key.record_key(table, 8) == b"\x11" * 32
    broken = bytearray(table)
    broken[36] = 9  # record 1's flag byte
    assert not forever_key.looks_like_table(bytes(broken))


class FakeMemory:
    def __init__(self, block: bytes, at: int = 0x4000) -> None:
        self.block, self.at, self.writes = bytearray(block), at, 0

    def find_tables(self) -> list[int]:
        return [self.at] if forever_key.looks_like_table(bytes(self.block)) else []

    def read(self, address: int, size: int) -> bytes:
        start = address - self.at
        return bytes(self.block[start : start + size])

    def write(self, address: int, data: bytes) -> bool:
        start = address - self.at
        self.block[start : start + len(data)] = data
        self.writes += 1
        return True

    def close(self) -> None:
        pass


def test_the_watch_writes_the_key_and_writes_it_again_after_the_game_rebuilds_its_table() -> None:
    memory = FakeMemory(_table())
    ticks = {"n": 0}

    def pids(_exe: Path) -> list[int]:
        ticks["n"] += 1
        if ticks["n"] == 4:  # the client rebuilt its table between two checks
            memory.block[: forever_key.BLOCK_SIZE] = _table()
        return [42] if ticks["n"] < 7 else []

    said: list[str] = []
    applied = forever_key.watch(
        Path("Wow.exe"),
        KEY,
        8,
        say=said.append,
        seams=forever_key.KeySeams(
            pids=pids, open_memory=lambda _pid: memory, sleep=lambda s: None
        ),
    )
    assert applied and memory.writes == 2
    assert forever_key.record_key(bytes(memory.block), 8) == KEY
    assert any("written again" in line for line in said)


class LateTableMemory(FakeMemory):
    """A 70888 game: no key table until the first Login press builds it, here 20 minutes in."""

    def __init__(self, clock: dict[str, float]) -> None:
        super().__init__(bytes(forever_key.BLOCK_SIZE))
        self.clock = clock

    def find_tables(self) -> list[int]:
        if self.clock["t"] >= 1200 and not forever_key.looks_like_table(bytes(self.block)):
            self.block[: forever_key.BLOCK_SIZE] = _table()  # the client built its store
        return super().find_tables()


def test_a_table_built_at_a_late_first_login_still_gets_the_key() -> None:
    """70888 builds its table only during the first Login press; the watch must still be there."""
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        clock["t"] += seconds

    memory = LateTableMemory(clock)
    said: list[str] = []

    def pids(_exe: Path) -> list[int]:
        return [42] if clock["t"] < 1300 else []

    applied = forever_key.watch(
        Path("Wow.exe"),
        KEY,
        8,
        say=said.append,
        seams=forever_key.KeySeams(
            pids=pids, open_memory=lambda _pid: memory, sleep=sleep, clock=lambda: clock["t"]
        ),
    )
    assert applied
    assert forever_key.record_key(bytes(memory.block), 8) == KEY
    assert any("login key is in the game" in line for line in said)


def test_the_watch_gives_up_when_the_game_never_starts() -> None:
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        clock["t"] += seconds

    said: list[str] = []
    seams = forever_key.KeySeams(
        pids=lambda _exe: [],
        sleep=sleep,
        clock=lambda: clock["t"],
        open_memory=lambda _pid: FakeMemory(_table()),
    )
    assert not forever_key.watch(Path("Wow.exe"), KEY, 8, say=said.append, seams=seams)
    assert "did not start" in said[0]


# -- forever_trust -------------------------------------------------------------------


def _ca(tmp_path: Path, der: bytes = b"pretend certificate") -> Path:
    path = tmp_path / "ca.pem"
    body = base64.b64encode(der).decode()
    path.write_text(f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n")
    return path


def test_the_thumbprint_is_the_sha1_of_the_der(tmp_path: Path) -> None:
    assert (
        forever_trust.thumbprint(_ca(tmp_path))
        == hashlib.sha1(b"pretend certificate").hexdigest().upper()
    )  # noqa: S324


def test_hosts_counts_only_a_live_loopback_line_for_the_portal() -> None:
    portal = "trinity.actual.battle.net"
    assert forever_trust.hosts_maps(f"127.0.0.1 {portal} # yulon-wow-forever\n", portal)
    assert forever_trust.hosts_maps(f"127.0.0.1\tother {portal}\n", portal)
    assert not forever_trust.hosts_maps(f"# 127.0.0.1 {portal}\n", portal)
    assert not forever_trust.hosts_maps(f"10.0.0.1 {portal}\n", portal)
    assert not forever_trust.hosts_maps(f"127.0.0.1 {portal}.evil\n", portal)


def _trust_seams(
    tmp_path: Path, *, trusted: bool, hosts_text: str, batch: Any = None, rc: int = 0
) -> tuple[forever_trust.TrustSeams, list[list[str]]]:
    hosts = tmp_path / "hosts"
    hosts.write_text(hosts_text)
    ran: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        if argv[:2] == ["certutil", "-store"]:
            return subprocess.CompletedProcess(argv, 0 if trusted else 1, "", "")
        return subprocess.CompletedProcess(argv, rc, "", "")

    return (
        forever_trust.TrustSeams(run=run, batch=batch or (lambda c: None), hosts=lambda: hosts),
        ran,
    )


def test_nothing_is_asked_when_the_ca_is_trusted_and_hosts_maps_the_portal(
    tmp_path: Path,
) -> None:
    seams, ran = _trust_seams(
        tmp_path, trusted=True, hosts_text="127.0.0.1 trinity.actual.battle.net\n"
    )
    assert (
        forever_trust.missing_commands(_ca(tmp_path), "trinity.actual.battle.net", seams=seams)
        == []
    )
    assert not forever_trust.ensure(_ca(tmp_path), "trinity.actual.battle.net", seams=seams)
    assert all(argv[:2] == ["certutil", "-store"] for argv in ran)


def test_what_is_missing_goes_behind_one_prompt_and_no_says_so(tmp_path: Path) -> None:
    results = tmp_path / "results.json"

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        assert [c[0] for c in commands] == ["certutil", "cmd", "cmd"]
        assert commands[0][:4] == ["certutil", "-addstore", "-f", "Root"]
        assert "127.0.0.1 trinity.actual.battle.net # yulon-wow-forever" in commands[1][2]
        return (["powershell.exe", "elevated"], results)

    seams, _ = _trust_seams(
        tmp_path, trusted=False, hosts_text="", batch=batch, rc=platform.UAC_DECLINED
    )
    with pytest.raises(forever_trust.TrustError, match="said No"):
        forever_trust.ensure(_ca(tmp_path), "trinity.actual.battle.net", seams=seams)


def test_only_certutil_icacls_and_cmd_run_elevated() -> None:
    assert platform.ADMIN_BATCH_PROGRAMS == ("icacls", "cmd", "certutil")
    with pytest.raises(ValueError):
        platform.elevated_admin_batch([["powershell", "-c", "x"]], is_admin=False)


# -- forever_account -----------------------------------------------------------------


@dataclass
class Realm:
    """A fake world console and auth database."""

    accounts: set[str] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)
    kept: dict[str, str] = field(default_factory=dict)
    console_rc: int = 0

    def exec_output(self, container: str, argv: list[str], **_: Any) -> Any:
        if container == "wowforever-world":
            self.commands.append(argv[1])
            if self.console_rc == 0 and argv[1].startswith("bnetaccount create "):
                self.accounts.add(argv[1].split()[2].upper())
            return subprocess.CompletedProcess(argv, self.console_rc, "", "no such container")
        found = any(f"UPPER('{a.lower()}')" in argv[-1] for a in self.accounts)
        return subprocess.CompletedProcess(argv, 0, "1\n" if found else "0\n", "")

    def _forget(self, name: str) -> None:
        self.kept.pop(name, None)

    def seams(self) -> forever_account.AccountSeams:
        return forever_account.AccountSeams(
            exec_output=self.exec_output,
            install_id=lambda _d: "abc123",
            recall=lambda game, server, name: self.kept.get(name),
            remember=lambda game, server, name, pw: self.kept.__setitem__(name, pw),
            forget=lambda game, server, name: self._forget(name),
            sleep=lambda s: None,
        )


def _random_password_entry() -> SingleEntry:
    """WoW Forever with no fixed password in the catalog: Play makes one."""
    assert FOREVER.play is not None and LOGIN is not None
    login = LOGIN.model_copy(update={"account_password": None})
    return FOREVER.model_copy(update={"play": FOREVER.play.model_copy(update={"forever": login})})


def test_the_catalog_makes_mmodad_with_pass_typed_without_its_domain(
    tmp_path: Path,
) -> None:
    assert LOGIN is not None
    assert forever_account.typed_name(LOGIN) == "mmodad"
    realm = Realm()
    assert forever_account.ensure(FOREVER, tmp_path, seams=realm.seams()) == (
        "mmodad@local",
        "pass",
    )
    assert realm.commands == ["bnetaccount create mmodad@local pass"]
    assert realm.kept["mmodad@local"] == "pass"


def test_without_a_fixed_password_the_first_play_makes_one_and_keeps_it(tmp_path: Path) -> None:
    realm = Realm()
    entry = _random_password_entry()
    email, password = forever_account.ensure(entry, tmp_path, seams=realm.seams())
    assert email == "mmodad@local"
    assert len(password) == forever_account.PASSWORD_LENGTH and password.isalnum()
    assert realm.commands == [f"bnetaccount create {email} {password}"]
    assert realm.kept[email] == password


def test_a_later_play_reuses_the_account_and_types_nothing(tmp_path: Path) -> None:
    realm = Realm(accounts={"MMODAD@LOCAL"}, kept={"mmodad@local": "pass"})
    assert forever_account.ensure(FOREVER, tmp_path, seams=realm.seams()) == (
        "mmodad@local",
        "pass",
    )
    assert realm.commands == []


def test_an_account_with_another_password_is_set_to_the_fixed_one(tmp_path: Path) -> None:
    realm = Realm(accounts={"MMODAD@LOCAL"}, kept={"mmodad@local": "old"})
    forever_account.ensure(FOREVER, tmp_path, seams=realm.seams())
    assert realm.commands == ["bnetaccount set password mmodad@local pass pass"]
    assert realm.kept["mmodad@local"] == "pass"


def test_an_account_with_no_kept_password_gets_a_new_one_set(tmp_path: Path) -> None:
    realm = Realm(accounts={"MMODAD@LOCAL"})
    entry = _random_password_entry()
    email, password = forever_account.ensure(entry, tmp_path, seams=realm.seams())
    assert realm.commands == [f"bnetaccount set password {email} {password} {password}"]


def test_a_console_that_refuses_forgets_the_password(tmp_path: Path) -> None:
    realm = Realm(console_rc=1)
    with pytest.raises(forever_account.AccountError, match="Is the server running"):
        forever_account.ensure(FOREVER, tmp_path, seams=realm.seams())
    assert realm.kept == {}


# -- forever_play --------------------------------------------------------------------


def test_the_config_sets_the_portal_and_account_and_keeps_the_players_lines(
    tmp_path: Path,
) -> None:
    assert LOGIN is not None
    game = _client(tmp_path) / "_forever_"
    (game / "WTF").mkdir()
    (game / "WTF" / "Config.wtf").write_text('SET gxWindow "1"\nSET portal "us"\n')
    written = forever_play.write_config(game, LOGIN, "player@yulon.local")
    lines = written.read_text().splitlines()
    assert 'SET gxWindow "1"' in lines
    assert 'SET portal "trinity.actual.battle.net"' in lines and 'SET portal "us"' not in lines
    assert 'SET accountName "player@yulon.local"' in lines
    assert (game / "WTF" / "Config.wtf").read_text() == 'SET gxWindow "1"\nSET portal "us"\n'
    assert written.name == "ForeverLauncher.wtf"


def _play_seams(calls: list[str], *, world: str = "running") -> forever_play.PlaySeams:
    def trust(ca: Path, portal: str) -> bool:
        calls.append(f"trust {ca.name} {portal}")
        return True

    def account(entry: SingleEntry, server_dir: Path) -> tuple[str, str]:
        calls.append("account")
        return ("player@yulon.local", "pw")

    def start(spec: Any) -> bool:
        calls.append(f"start {' '.join(spec.argv[1:])}")
        return False

    return forever_play.PlaySeams(
        state_of=lambda _c: docker.ContainerState(status=world),
        trust=trust,
        account=account,
        start_client=start,
        watch=lambda exe, key, group: calls.append(f"watch {exe.name} {group}"),
    )


def test_play_trusts_makes_the_account_writes_the_config_starts_and_watches(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    client = _client(tmp_path)
    played = forever_play.play(
        FOREVER, tmp_path / "server", client, os_name="windows", seams=_play_seams(calls)
    )
    assert calls == [
        "trust ca.pem trinity.actual.battle.net",
        "account",
        "start -config ForeverLauncher.wtf",
        "watch Wow.exe 8",
    ]
    assert played == forever_play.Played("mmodad", "pw", True)  # the name typed at login
    assert (client / "_forever_" / "WTF" / "ForeverLauncher.wtf").is_file()
    assert "press Login again" in forever_play.login_text(FOREVER, played)


def test_play_refuses_a_stopped_server_another_os_and_another_client(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path)
    with pytest.raises(PlayRefusal, match="Start WoW Forever first"):
        forever_play.play(
            FOREVER, tmp_path, client, os_name="windows", seams=_play_seams(calls, world="exited")
        )
    with pytest.raises(PlayRefusal, match="on Windows and on Linux"):
        forever_play.play(FOREVER, tmp_path, client, os_name="macos", seams=_play_seams(calls))
    newer = _client(tmp_path / "newer", build="1.60.1.70999")
    with pytest.raises(PlayRefusal, match="names none of the builds"):
        forever_play.play(FOREVER, tmp_path, newer, os_name="windows", seams=_play_seams(calls))
    assert calls == []


def test_the_catalog_json_keeps_wow_forever_last() -> None:
    raw = json.loads(
        (Path(__file__).parent.parent / "yulon" / "catalog" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    assert [e["id"] for e in raw["single"]][-1] == "wow-forever"
