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
from yulon.catalog import composegen, stack_install
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


def test_forever_runs_its_extra_stages_and_ffxi_does_not() -> None:
    assert stages_of(STACK) == (
        "fetch-loader",
        "fetch-downloads",
        "write-secrets",
        "generate-compose",
        "pull-images",
        "seed-volumes",
        "extract-client",
        "certificates",
        "up",
        "ready",
    )
    ffxi = stack_of(CATALOG.get_single("ffxi"))
    for stage in ("fetch-downloads", "extract-client", "certificates"):
        assert stage not in stages_of(ffxi)


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
    assert [run for run in daemon.runs if run[0] == "extract"] == [
        ("extract", ("seed",), (f"{client}:/client:ro",))
    ]
    for name, _template in STACK_TEMPLATES:
        text = (server_dir / name).read_text(encoding="utf-8")
        assert "{{" not in text
    assert "wowforever-world" in (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert (server_dir / "sql" / HOTFIX_UPDATE).read_bytes() == SHIPPED_UPDATE.read_bytes()
    assert f"./sql/{HOTFIX_UPDATE}:/opt/forever/src/sql/custom/hotfixes/{HOTFIX_UPDATE}:ro" in base


def test_the_install_makes_yu_lons_own_certificates_before_the_first_start(
    tmp_path: Path,
) -> None:
    """T888 item 1a: the CA is constrained to the login name alone, and its key never lands
    in the player's certs folder. The engine runs Yu'lon's script in the stack's image."""
    daemon = Daemon()
    server_dir = tmp_path / "forever"
    out = _install(daemon, server_dir, _client(tmp_path))
    assert ("certificates", ("seed",), ()) in daemon.runs
    steps = [line.rsplit(": ", 1)[-1] for line in out if line.startswith("Step ")]
    assert steps.index("certificates") == steps.index("up") - 1
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert "./certs:/srv/certs" in base and "./certs-yulon.sh:/yulon-certs.sh:ro" in base
    script = (server_dir / "certs-yulon.sh").read_text(encoding="utf-8")
    assert "nameConstraints = critical,permitted;DNS:trinity.actual.battle.net" in script
    assert 'cp "$T/ca.key"' not in script, "the CA key never reaches the player's folder"
    assert (
        'cp "$T/ca.pem" "$T/server.pem" "$T/chain.pem" "$T/server.key" "$S/"' in script
    ), "the whole set is staged before it is swapped"
    assert script.index('openssl verify -CAfile "$T/ca.pem" "$T/server.pem"') < script.index(
        'mv -f "$S/ca.pem"'
    ), "nothing in certs/ changes until openssl has verified the new set"


HOTFIX_UPDATE = "2026_11_14_00_hotfixes_yulon_spell_x_spell_visual.sql"
SHIPPED_UPDATE = (
    Path(__file__).parent.parent / "catalog/installers/wow-forever/stack/files/sql" / HOTFIX_UPDATE
)


def test_the_shipped_hotfixes_update_gives_holy_strike_its_visual_rows_under_our_push() -> None:
    """The 18 SpellXSpellVisual rows the 70888 client cannot read, Holy Strike's 8 among them."""
    text = SHIPPED_UPDATE.read_text(encoding="utf-8")
    assert chr(13) not in text
    rows = [
        line for line in text.splitlines() if line.startswith("REPLACE INTO `spell_x_spell_visual`")
    ]
    data = [line for line in text.splitlines() if line.startswith("REPLACE INTO `hotfix_data`")]
    assert len(rows) == len(data) == 18
    assert all(
        line.startswith("REPLACE INTO `hotfix_data` VALUES (3100001,3100001,666345498,")
        for line in data
    )
    holy = {int(r.split(",")[12]) for r in rows if ",190305," in r}
    assert holy == {678, 679, 680, 1866, 2495, 5569, 10332, 10333}
    assert all(line.startswith(("--", "REPLACE INTO ")) for line in text.splitlines() if line)


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


def test_the_key_watch_attaches_only_to_the_pid_that_appeared_after_launch() -> None:
    """T888 item 5: an older copy of the game keeps running; its memory is not this launch's."""
    memory = FakeMemory(_table())
    opened: list[int] = []
    ticks = {"n": 0}

    def pids(_exe: Path) -> list[int]:
        ticks["n"] += 1
        return [111, 222] if ticks["n"] < 3 else []

    applied = forever_key.watch(
        Path("Wow.exe"),
        KEY,
        8,
        before=(111,),
        seams=forever_key.KeySeams(
            pids=pids,
            open_memory=lambda pid: (opened.append(pid), memory)[1],
            sleep=lambda _s: None,
        ),
    )
    assert applied and opened == [222], "the pre-existing pid was never attached to"


def test_a_second_key_watch_is_not_started_while_one_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T888 item 5: two watches over one game would rewrite the same table from two threads."""
    gate = threading.Event()
    started: list[int] = []

    def hold(*_a: object) -> None:
        started.append(1)
        gate.wait(10)

    monkeypatch.setattr(forever_key, "_logged_watch", hold)
    try:
        first = forever_key.start_watch(Path("Wow.exe"), KEY, 8)
        second = forever_key.start_watch(Path("Wow.exe"), KEY, 8)
        assert second is first and len(started) == 1
    finally:
        gate.set()
        first.join(10)


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


NARROW_CA = Path(__file__).parent / "data" / "forever-certs" / "narrow-critical.pem"
BROAD_CA = Path(__file__).parent / "data" / "forever-certs" / "broad.pem"


def _server_with_certs(tmp_path: Path) -> Path:
    """A server folder whose install already made the CA Yu'lon's own script makes."""
    server = tmp_path / "server"
    (server / "certs").mkdir(parents=True)
    (server / "certs" / "ca.pem").write_bytes(NARROW_CA.read_bytes())
    return server


def _narrow_ca(tmp_path: Path) -> Path:
    """The CA Yu'lon's own script makes: CA:TRUE, critical name constraint, one dNSName."""
    path = tmp_path / "ca.pem"
    path.write_bytes(NARROW_CA.read_bytes())
    return path


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
        forever_trust.missing_commands(
            _narrow_ca(tmp_path), "trinity.actual.battle.net", seams=seams
        )
        == []
    )
    assert not forever_trust.ensure(_narrow_ca(tmp_path), "trinity.actual.battle.net", seams=seams)
    assert all(argv[:2] == ["certutil", "-store"] for argv in ran)


def test_what_is_missing_goes_behind_one_prompt_and_no_says_so(tmp_path: Path) -> None:
    results = tmp_path / "results.json"

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        assert [c[0] for c in commands] == ["certutil", "powershell.exe", "cmd"]
        assert commands[0][:4] == ["certutil", "-addstore", "-f", "Root"]
        assert "127.0.0.1 trinity.actual.battle.net # yulon-wow-forever" in commands[1][3]
        return (["powershell.exe", "elevated"], results)

    seams, _ = _trust_seams(
        tmp_path, trusted=False, hosts_text="", batch=batch, rc=platform.UAC_DECLINED
    )
    with pytest.raises(forever_trust.TrustError, match="said No"):
        forever_trust.ensure(_narrow_ca(tmp_path), "trinity.actual.battle.net", seams=seams)


def test_a_ca_wider_than_this_server_is_refused_before_a_prompt(tmp_path: Path) -> None:
    """T888 item 1b: the old `actual.battle.net` CA also covers Blizzard's real login hosts."""
    batched: list[list[list[str]]] = []
    seams, ran = _trust_seams(
        tmp_path,
        trusted=False,
        hosts_text="",
        batch=lambda commands: batched.append(commands),  # type: ignore[arg-type,return-value]
    )
    ca = tmp_path / "ca.pem"
    ca.write_bytes(BROAD_CA.read_bytes())
    with pytest.raises(forever_trust.TrustError) as raised:
        forever_trust.ensure(ca, "trinity.actual.battle.net", seams=seams)
    assert str(raised.value) == forever_trust.UNCHECKED
    assert batched == [] and ran == [], "nothing runs, and no prompt is raised"
    with pytest.raises(forever_trust.TrustError, match="could not be checked"):
        forever_trust.verified_ca(_ca(tmp_path), "trinity.actual.battle.net")


def test_the_trust_step_installs_the_checked_copy_not_the_players_file(tmp_path: Path) -> None:
    """T888 item 1b: ca.pem is in a writable folder and can be swapped while UAC is open."""
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    installed: list[str] = []
    trusted = [False]

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        installed.extend(c[4] for c in commands if c[:2] == ["certutil", "-addstore"])
        trusted[0] = True  # what the elevated `certutil -addstore` does to the store
        results = tmp_path / "results.json"
        results.write_text('[{"rc": 0, "out": ""}, {"rc": 0, "out": ""}]')
        return (["powershell.exe", "elevated"], results)

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["certutil", "-store"]:
            return subprocess.CompletedProcess(argv, 0 if trusted[0] else 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    seams = forever_trust.TrustSeams(
        run=run, batch=batch, hosts=lambda: hosts, os_name=lambda: "windows"
    )
    ca = _narrow_ca(tmp_path)
    forever_trust.ensure(ca, "trinity.actual.battle.net", seams=seams)
    assert len(installed) == 1
    assert installed[0] != str(ca) and Path(installed[0]).name.startswith("yulon-ca-")
    assert not Path(installed[0]).exists(), "the checked copy is gone once it is trusted"


def test_the_hosts_edits_are_literal_paths_with_no_inner_double_quote() -> None:
    """T888 item 2 (Gemini #4): `list2cmdline`'s inner quotes are not escapes to `cmd`.

    A `SystemRoot` with a space, a path with `(x86)` and a hosts line all go into one
    single-quoted PowerShell literal, so the edit is one argv element with no interior
    `"` for `cmd` to misread -- and no `cmd` in the command at all. Both edits put every
    line through `Get-Content`/`Set-Content`, so a file with no final newline is not
    glued, and an otherwise-empty file is still written (T888).
    """
    hosts = Path(r"C:\Program Files (x86)\Windows\hosts")
    for command in (
        forever_trust.append_command(hosts, "trinity.actual.battle.net"),
        forever_trust.strip_command(hosts),
    ):
        assert command[:2] == ["powershell.exe", "-NoProfile"]
        assert not any('"' in element for element in command), command
        assert platform.powershell_literal(str(hosts)) in command[3]
    assert (
        "Get-Content -LiteralPath $p"
        in forever_trust.append_command(hosts, "trinity.actual.battle.net")[3]
    )
    assert "Set-Content -LiteralPath $p" in forever_trust.strip_command(hosts)[3]


def test_an_administrator_edits_a_tmp_hosts_file_in_python(tmp_path: Path) -> None:
    """T888 item 2: already elevated, Yu'lon reads, edits and replaces the file itself.

    No command line is built for the hosts half at all, so nothing a `cmd` or PowerShell
    prompt would have parsed is involved. The tagged line is added once, after a last
    line with no newline, and the file is replaced whole.
    """
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost")  # no final newline on purpose
    ran: list[list[str]] = []
    trusted = [False]

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        if argv[:2] == ["certutil", "-store"]:
            return subprocess.CompletedProcess(argv, 0 if trusted[0] else 1, "")
        if argv[:2] == ["certutil", "-addstore"]:
            trusted[0] = True
        return subprocess.CompletedProcess(argv, 0, "")

    seams = forever_trust.TrustSeams(
        run=run,  # type: ignore[arg-type]
        hosts=lambda: hosts,
        os_name=lambda: "windows",
        is_admin=lambda: True,
    )
    ca = _narrow_ca(tmp_path)
    assert forever_trust.ensure(ca, "trinity.actual.battle.net", seams=seams) is False
    text = hosts.read_text()
    assert text.splitlines() == [
        "127.0.0.1 localhost",
        "127.0.0.1 trinity.actual.battle.net # yulon-wow-forever",
    ]
    assert all(argv[:1] != ["powershell.exe"] for argv in ran), "no command line for hosts"
    assert [argv[0] for argv in ran] == [
        "certutil",
        "certutil",
        "cmd",
        "certutil",
    ], "why: one store read, the add, the flush, and the post-check's store read"


def test_removal_builds_the_ca_command_from_the_recorded_thumbprint(tmp_path: Path) -> None:
    """T888 item 2/7: after the install folder is gone, the record's thumbprint is what is left."""
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    commands = forever_trust.remove_commands(thumb="AB12CD", hosts=hosts)
    assert commands[0] == ["certutil", "-delstore", "Root", "AB12CD"]
    assert commands[1][:2] == ["powershell.exe", "-NoProfile"]
    assert "# yulon-wow-forever" in commands[1][3]
    ca = _ca(tmp_path)
    assert forever_trust.remove_commands(ca, hosts=hosts)[0] == [
        "certutil",
        "-delstore",
        "Root",
        forever_trust.thumbprint(ca),
    ]
    assert forever_trust.remove_commands(hosts=hosts) == [commands[1]], "no CA known, so only hosts"


def test_removal_asks_for_nothing_when_there_is_nothing_to_undo(tmp_path: Path) -> None:
    """T888 item 7 (Claude #6): a never-played install's removal raises no prompt at all."""
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    assert forever_trust.remove_commands(hosts=hosts) == []
    batched: list[list[list[str]]] = []

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        batched.append(commands)
        raise AssertionError("no prompt for a removal with nothing to take off")

    seams = forever_trust.TrustSeams(
        run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "", ""),
        batch=batch,
        hosts=lambda: hosts,
        os_name=lambda: "windows",
        is_admin=lambda: False,
    )
    assert forever_trust.remove(seams=seams) == ()
    assert batched == []
    linux = forever_trust.TrustSeams(
        run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "", ""),
        hosts=lambda: hosts,
        os_name=lambda: "linux",
        which=lambda _n: "/usr/bin/sudo",
    )
    assert forever_trust.remove(seams=linux) == ()


def test_the_shared_hosts_line_stays_while_another_install_uses_it(tmp_path: Path) -> None:
    """T888 item 7 (Codex #7): one tagged line serves every WoW Forever install on the box."""
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    assert [
        c for c in forever_trust.remove_commands(thumb="AB12CD", hosts=hosts) if c[0] != "certutil"
    ], "the last install takes the line out"
    assert forever_trust.remove_commands(thumb="AB12CD", hosts=hosts, strip_hosts=False) == [
        ["certutil", "-delstore", "Root", "AB12CD"]
    ], "another install still needs it, so only the CA goes"
    batched: list[list[list[str]]] = []
    results = tmp_path / "results.json"

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        batched.append(commands)
        results.write_text('[{"rc": 0, "out": ""}, {"rc": 0, "out": ""}]')
        return (["powershell.exe", "elevated"], results)

    seams = forever_trust.TrustSeams(
        run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "", ""),
        batch=batch,
        hosts=lambda: hosts,
        os_name=lambda: "windows",
        is_admin=lambda: False,
    )
    assert forever_trust.remove(thumb="AB12CD", strip_hosts=False, seams=seams) == ()
    assert [c[0] for c in batched[0]] == ["certutil", "cmd"]
    assert "# yulon-wow-forever" in hosts.read_text(), "the other install still resolves"

    linux = forever_trust.TrustSeams(
        run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "", ""),
        hosts=lambda: hosts,
        os_name=lambda: "linux",
        which=lambda _n: "/usr/bin/sudo",
    )
    assert forever_trust.remove(strip_hosts=False, seams=linux) == (), "no sudo, no prompt"
    assert forever_trust.tagged(hosts)


def test_windows_removal_is_one_elevated_batch_with_the_recorded_thumbprint(
    tmp_path: Path,
) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    batched: list[list[list[str]]] = []
    results = tmp_path / "results.json"

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        batched.append(commands)
        results.write_text('[{"rc": 0, "out": ""}, {"rc": 0, "out": ""}]')
        return (["powershell.exe", "elevated"], results)

    seams = forever_trust.TrustSeams(
        run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "", ""),
        batch=batch,
        hosts=lambda: hosts,
        os_name=lambda: "windows",
    )
    assert forever_trust.remove(thumb="AB12CD", seams=seams) == ()
    assert [c[0] for c in batched[0]] == ["certutil", "cmd"]
    assert batched[0][0] == ["certutil", "-delstore", "Root", "AB12CD"]
    assert batched[0][1] == ["cmd", "/c", "ipconfig", "/flushdns"]
    assert not results.exists(), "the results file is removed once read"


def test_steamos_removal_takes_the_hosts_line_with_sudo_n_or_says_the_command(
    tmp_path: Path,
) -> None:
    """T888 item 2: SteamOS has no machine store this app touched, so only the line goes."""
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "127.0.0.1 localhost\n127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n"
    )
    ran: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        hosts.write_text("127.0.0.1 localhost\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    seams = forever_trust.TrustSeams(
        run=run, hosts=lambda: hosts, os_name=lambda: "linux", which=lambda _n: "/usr/bin/sudo"
    )
    assert forever_trust.remove(seams=seams) == () and ran[0][:2] == ["sudo", "-n"]
    assert ran[0][-1] == str(hosts)
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    no_sudo = forever_trust.TrustSeams(
        run=run, hosts=lambda: hosts, os_name=lambda: "linux", which=lambda _n: None
    )
    left = forever_trust.remove(seams=no_sudo)
    assert left == (f"sudo sed -i '/# yulon-wow-forever/d' {hosts}",)
    assert forever_trust.remove(seams=no_sudo) != (), "the line is still there"
    hosts.write_text("127.0.0.1 localhost\n")
    assert forever_trust.remove(seams=no_sudo) == (), "nothing tagged, nothing to run"


def test_every_hosts_command_passes_the_elevated_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T888 item 4's exact forms are the ones item 2's removal builds: crossed, not assumed."""
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    hosts = Path(r"C:\Windows\System32\drivers\etc\hosts")
    commands = [
        forever_trust.append_command(hosts, "trinity.actual.battle.net"),
        *forever_trust.remove_commands(thumb="AB12CD", hosts=hosts),
        ["cmd", "/c", "ipconfig", "/flushdns"],
    ]
    argv, results = platform.elevated_admin_batch(commands, is_admin=False)
    results.unlink(missing_ok=True)
    assert argv[:2] == ["powershell.exe", "-NoProfile"]


def test_only_named_programs_run_elevated_and_cmd_only_in_its_own_forms() -> None:
    """T888 item 4: `cmd` left the list, so a bare `cmd /c ...` cannot reach the prompt."""
    assert platform.ADMIN_BATCH_PROGRAMS == ("icacls", "certutil", "powershell.exe")
    with pytest.raises(ValueError):
        platform.elevated_admin_batch([["powershell", "-c", "x"]], is_admin=False)
    with pytest.raises(ValueError, match="exact forms"):
        platform.elevated_admin_batch([["cmd", "/c", "whoami"]], is_admin=False)


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


class TrustBook:
    """The install record's trust half, in memory: what Play persists and what it reports."""

    def __init__(self) -> None:
        self.stale: list[str] = []
        self.trusted: list[str] = []
        self.writes = 0

    def record(self) -> forever_play.TrustRecord:
        """The `TrustRecord` Play is handed, over this book's three lists."""

        def remember_stale(thumb: str) -> None:
            self.writes += 1
            if thumb not in self.stale:
                self.stale.append(thumb)

        def forget_stale(thumb: str) -> None:
            self.writes += 1
            self.stale = [t for t in self.stale if t != thumb]

        def trusted(thumb: str) -> None:
            self.writes += 1
            self.trusted = [thumb]

        return forever_play.TrustRecord(
            stale=lambda: tuple(self.stale),
            remember_stale=remember_stale,
            forget_stale=forget_stale,
            trusted=trusted,
        )


def _play_seams(
    calls: list[str], *, world: str = "running", book: TrustBook | None = None
) -> forever_play.PlaySeams:
    def trust(
        ca: Path,
        portal: str,
        *,
        record: Callable[[str], None] | None = None,
        stale: Sequence[str] = (),
        forget: Callable[[str], None] | None = None,
    ) -> bool:
        calls.append(f"trust {ca.name} {portal}" + (f" after {' '.join(stale)}" if stale else ""))
        if record is not None:
            record("AB12CD34")
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
        trust_record=(book or TrustBook()).record(),
        account=account,
        start_client=start,
        watch=lambda exe, key, group, **_kw: calls.append(f"watch {exe.name} {group}"),
        game_pids=lambda _client: (),
        find_game=lambda _client: None,
        restart_service=lambda _where, service, **_kw: calls.append(f"restart {service}"),
        container_project=lambda _c: "project",
        compose_project=lambda _e, _d: "project",
    )


def test_play_trusts_makes_the_account_writes_the_config_starts_and_watches(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    client = _client(tmp_path)
    played = forever_play.play(
        FOREVER, _server_with_certs(tmp_path), client, os_name="windows", seams=_play_seams(calls)
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


def test_windows_play_records_the_thumbprint_of_the_ca_it_trusted(tmp_path: Path) -> None:
    """T888 item 2/3: the install record keeps it, so removal works once certs/ is gone."""
    calls: list[str] = []
    book = TrustBook()
    seams = forever_play.PlaySeams(
        **{**_play_seams(calls, book=book).__dict__},
    )
    forever_play.play(
        FOREVER, _server_with_certs(tmp_path), _client(tmp_path), os_name="windows", seams=seams
    )
    assert book.trusted == ["AB12CD34"]
    assert calls[0] == "trust ca.pem trinity.actual.battle.net"


def test_the_new_thumbprint_is_recorded_only_after_the_batch_and_the_check(
    tmp_path: Path,
) -> None:
    """T888 item 3 (Codex #2, Claude #2): a failed batch loses nothing and records nothing."""
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    book = TrustBook()
    book.stale = ["STALET"]
    results = tmp_path / "results.json"
    trusted = [False]

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["certutil", "-store"]:
            if argv[3] == "STALET":
                return subprocess.CompletedProcess(argv, 0, "", "")  # still in the store
            return subprocess.CompletedProcess(argv, 0 if trusted[0] else 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        assert ["certutil", "-delstore", "Root", "STALET"] in commands
        second = 0 if trusted[0] else 1
        results.write_text(f'[{{"rc": 0, "out": ""}}, {{"rc": {second}, "out": ""}}]')
        return (["powershell.exe", "elevated"], results)

    seams = forever_trust.TrustSeams(
        run=run,  # type: ignore[arg-type]
        batch=batch,
        hosts=lambda: hosts,
        os_name=lambda: "windows",
        is_admin=lambda: False,
    )
    with pytest.raises(forever_trust.TrustError):
        forever_trust.ensure(
            _narrow_ca(tmp_path),
            "trinity.actual.battle.net",
            seams=seams,
            record=book.record().trusted,
            stale=book.stale,
            forget=book.record().forget_stale,
        )
    assert book.trusted == [] and book.stale == ["STALET"], "nothing recorded, nothing dropped"

    trusted[0] = True
    assert forever_trust.ensure(
        _narrow_ca(tmp_path),
        "trinity.actual.battle.net",
        seams=seams,
        record=book.record().trusted,
        stale=book.stale,
        forget=book.record().forget_stale,
    )
    assert book.trusted == [forever_trust.thumbprint(_narrow_ca(tmp_path))]


def test_play_remembers_the_ca_it_replaces_before_anything_deletes_it(tmp_path: Path) -> None:
    """T888 item 3: a remake writes the old thumbprint before the file it names is replaced.

    The prompt is declined, so Play refuses and records no new thumbprint; the old one is
    in the record for the next Play to delete, and the batch it was going to run named it.
    """
    server = tmp_path / "server"
    (server / "certs").mkdir(parents=True)
    (server / "certs" / "ca.pem").write_bytes(BROAD_CA.read_bytes())
    old = forever_trust.thumbprint(server / "certs" / "ca.pem")
    book = TrustBook()
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    results = tmp_path / "results.json"
    batched: list[list[list[str]]] = []

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        batched.append(commands)
        return (["powershell.exe", "elevated"], results)

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["certutil", "-store"]:
            return subprocess.CompletedProcess(argv, 0, "", "")  # the old root is installed
        return subprocess.CompletedProcess(argv, platform.UAC_DECLINED, "", "")

    def remake(_entry: SingleEntry, where: Path) -> None:
        (where / "certs" / "ca.pem").write_bytes(NARROW_CA.read_bytes())

    trust_seams = forever_trust.TrustSeams(
        run=run,  # type: ignore[arg-type]
        batch=batch,
        hosts=lambda: hosts,
        os_name=lambda: "windows",
        is_admin=lambda: False,
    )
    seams = forever_play.PlaySeams(
        **{
            **_play_seams([], book=book).__dict__,
            "remake_certificates": remake,
            "restart_service": lambda *_a, **_k: None,
            "trust": lambda ca, portal, **kw: forever_trust.ensure(
                ca, portal, seams=trust_seams, **kw
            ),
        }
    )
    client = _client(tmp_path)
    with pytest.raises(PlayRefusal) as raised:
        forever_play.play(FOREVER, server, client, os_name="windows", seams=seams)
    assert str(raised.value) == forever_trust.DECLINED
    assert book.stale == [old], "the replaced CA is still on the record"
    assert book.trusted == [], "a declined prompt records nothing"
    assert ["certutil", "-delstore", "Root", old] in batched[0]

    calls: list[str] = []
    retried: list[Sequence[str]] = []

    def later_trust(
        ca: Path,
        portal: str,
        *,
        record: Callable[[str], None] | None = None,
        stale: Sequence[str] = (),
        forget: Callable[[str], None] | None = None,
    ) -> bool:
        retried.append(tuple(stale))
        if forget is not None:
            for thumb in stale:
                forget(thumb)
        if record is not None:
            record("NEWTHUMB")
        return True

    later = forever_play.PlaySeams(
        **{**_play_seams(calls, book=book).__dict__, "trust": later_trust}
    )
    forever_play.play(FOREVER, server, client, os_name="windows", seams=later)
    assert retried == [(old,)], "a later Play still asks for the old root's delete"
    assert book.stale == [] and book.trusted == ["NEWTHUMB"]


def test_removing_a_forever_server_takes_its_trust_with_it(tmp_path: Path) -> None:
    """T888 item 2: the removal path asks forever_trust with the record's thumbprint."""
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 trinity.actual.battle.net # yulon-wow-forever\n")
    batched: list[list[list[str]]] = []

    def batch(commands: list[list[str]]) -> tuple[list[str], Path]:
        batched.append(commands)
        return None  # type: ignore[return-value]  # run as they are, like an administrator

    seams = forever_trust.TrustSeams(
        run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, "", ""),
        batch=batch,
        hosts=lambda: hosts,
        os_name=lambda: "windows",
    )
    assert (
        forever_play.remove_trust(FOREVER, tmp_path / "server", thumbprint="AB12CD34", seams=seams)
        == ()
    )
    assert batched[0][0] == ["certutil", "-delstore", "Root", "AB12CD34"]


def test_play_remakes_the_ca_in_the_container_and_restarts_only_the_login(
    tmp_path: Path,
) -> None:
    """T888 item 4 (Claude #3, Gemini #1): the container swaps the set; Play restarts bnet.

    The image's `certs.sh` constrained its CA to all of `actual.battle.net` and left the CA
    key in the install folder. Play hands the remake to the stack's own script, which makes
    and verifies the new set in a staging folder inside the container and swaps it in; Play
    itself deletes nothing in `certs/` (the folder may be root's), restarts only the login
    server, and hands the old thumbprint to the trust step so the old root leaves the store
    in the same prompt.
    """
    server = tmp_path / "server"
    (server / "certs").mkdir(parents=True)
    (server / "certs" / "ca.key").write_bytes(b"the image's key")
    (server / "certs" / "ca.pem").write_bytes(BROAD_CA.read_bytes())
    old = forever_trust.thumbprint(server / "certs" / "ca.pem")
    calls: list[str] = []
    made: list[tuple[SingleEntry, Path]] = []

    def remake(entry: SingleEntry, where: Path) -> None:
        made.append((entry, where))
        (where / "certs" / "ca.pem").write_bytes(NARROW_CA.read_bytes())

    seams = forever_play.PlaySeams(
        **{
            **_play_seams(calls).__dict__,
            "remake_certificates": remake,
            "container_project": lambda _c: "same-project",
            "compose_project": lambda _e, _d: "same-project",
            "restart_service": lambda _where, service, **_kw: calls.append(f"restart {service}"),
        }
    )
    forever_play.play(FOREVER, server, _client(tmp_path), os_name="windows", seams=seams)
    assert made == [(FOREVER, server)], "the installer's own render plus the script, once"
    assert (server / "certs" / "ca.key").exists(), "Python deletes nothing in certs/"
    assert (server / "certs" / "ca.pem").read_bytes() == NARROW_CA.read_bytes()
    assert calls[:2] == [
        "restart bnet",
        f"trust ca.pem trinity.actual.battle.net after {old}",
    ]
    assert "restart world" not in calls, "the world server does not read the certificate"


def test_a_login_server_of_another_compose_project_is_not_restarted(tmp_path: Path) -> None:
    """T888 item 4 (Codex #1, Gemini #6): a fixed container name is not proof of ownership."""
    server = tmp_path / "server"
    (server / "certs").mkdir(parents=True)
    (server / "certs" / "ca.pem").write_bytes(BROAD_CA.read_bytes())
    calls: list[str] = []

    def remake(_entry: SingleEntry, where: Path) -> None:
        (where / "certs" / "ca.pem").write_bytes(NARROW_CA.read_bytes())

    seams = forever_play.PlaySeams(
        **{
            **_play_seams(calls).__dict__,
            "remake_certificates": remake,
            "container_project": lambda _c: "somebody-elses-project",
            "compose_project": lambda _e, _d: "this-install",
            "restart_service": lambda _where, service, **_kw: calls.append(f"restart {service}"),
        }
    )
    forever_play.play(FOREVER, server, _client(tmp_path), os_name="windows", seams=seams)
    assert [c for c in calls if c.startswith("restart")] == []


def test_play_remakes_a_broad_ca_but_restarts_nothing_that_was_stopped(
    tmp_path: Path,
) -> None:
    """Only a service that was running is restarted: a stopped stack stays stopped."""
    server = tmp_path / "server"
    (server / "certs").mkdir(parents=True)
    (server / "certs" / "ca.pem").write_bytes(BROAD_CA.read_bytes())
    calls: list[str] = []

    def remake(_entry: SingleEntry, where: Path) -> None:
        (where / "certs" / "ca.pem").write_bytes(NARROW_CA.read_bytes())

    def state_of(container: str) -> docker.ContainerState:
        running = container.endswith("-world")
        return docker.ContainerState(status="running" if running else "exited")

    seams = forever_play.PlaySeams(
        **{
            **_play_seams(calls).__dict__,
            "state_of": state_of,
            "remake_certificates": remake,
            "restart_service": lambda _where, service, **_kw: calls.append(f"restart {service}"),
        }
    )
    forever_play.play(FOREVER, server, _client(tmp_path), os_name="windows", seams=seams)
    assert [c for c in calls if c.startswith("restart")] == []


def test_a_failed_remake_leaves_the_old_certificates_and_play_refuses(tmp_path: Path) -> None:
    """T888 item 4 (Gemini #1): a failed one-shot changes nothing, and Play says why.

    The script builds the new set in a staging folder inside the container and only swaps
    it in after `openssl verify`, so a run that fails leaves the old files exactly where
    they were; Play itself never unlinks one. The sentence is the one the install uses.
    """
    server = tmp_path / "server"
    (server / "certs").mkdir(parents=True)
    (server / "certs" / "ca.key").write_bytes(b"the image's key")
    (server / "certs" / "ca.pem").write_bytes(BROAD_CA.read_bytes())
    before = {p.name: p.read_bytes() for p in (server / "certs").iterdir()}

    def remake(_entry: SingleEntry, _where: Path) -> None:
        raise stack_install.CertificatesError(
            "The server's certificate could not be made (exit 1). openssl verify failed"
        )

    seams = forever_play.PlaySeams(**{**_play_seams([]).__dict__, "remake_certificates": remake})
    with pytest.raises(PlayRefusal) as raised:
        forever_play.play(FOREVER, server, _client(tmp_path), os_name="windows", seams=seams)
    assert "The server's certificate could not be made" in str(raised.value)
    assert {p.name: p.read_bytes() for p in (server / "certs").iterdir()} == before


def test_a_ca_the_key_it_left_beside_it_is_remade_even_when_it_passes_the_check(
    tmp_path: Path,
) -> None:
    """T888 item 1c: `certs/ca.key` alone is reason enough -- a CA key anyone can read.

    The certificate itself is the narrow one Yu'lon would make, so nothing but the key's
    presence can be the reason; removing that rule leaves this file's CA trusted as it is.
    """
    server = _server_with_certs(tmp_path)
    (server / "certs" / "ca.key").write_bytes(b"the image's key")
    calls: list[str] = []
    made: list[Path] = []

    def remake(_entry: SingleEntry, where: Path) -> None:
        made.append(where)

    seams = forever_play.PlaySeams(**{**_play_seams(calls).__dict__, "remake_certificates": remake})
    forever_play.play(FOREVER, server, _client(tmp_path), os_name="windows", seams=seams)
    assert made == [server]


def test_play_refuses_while_this_clients_game_already_runs(tmp_path: Path) -> None:
    """T888 item 5: a second press would write this server's key into the running copy."""
    calls: list[str] = []
    seams = forever_play.PlaySeams(
        **{**_play_seams(calls).__dict__, "game_pids": lambda _client: (4242,)}
    )
    with pytest.raises(PlayRefusal) as raised:
        forever_play.play(
            FOREVER,
            _server_with_certs(tmp_path),
            _client(tmp_path),
            os_name="windows",
            seams=seams,
        )
    assert str(raised.value) == "WoW Forever is already running. Close it, then press Play again."
    assert calls == [], "nothing is trusted, made or started"


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


def test_a_certificate_whose_body_is_not_base64_raises_rather_than_guessing(
    tmp_path: Path,
) -> None:
    """T888 item 6 (Gemini #2): a PEM with junk in it is not a certificate, and says so.

    `base64.b64decode` without `validate` silently drops every character outside its
    alphabet, so `!!!` became a thumbprint of the empty string and Play then asked the
    store to delete -- or add -- a root no certificate ever had.
    """
    ca = tmp_path / "ca.pem"
    for body in ("!!! not base64 !!!", "AAAA?", "AAAA===="):
        ca.write_text(
            f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n", encoding="ascii"
        )
        with pytest.raises(forever_trust.TrustError, match="no readable certificate"):
            forever_trust.thumbprint(ca)


def test_a_thumbprint_of_a_sound_pem_is_the_sha1_of_its_der(tmp_path: Path) -> None:
    ca = _ca(tmp_path)
    assert (
        forever_trust.thumbprint(ca) == hashlib.sha1(b"pretend certificate").hexdigest().upper()
    )  # noqa: S324
