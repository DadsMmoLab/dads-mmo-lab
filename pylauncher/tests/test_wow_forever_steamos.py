"""WoW Forever on SteamOS: GE-Proton launch, the prefix's CA, hosts, and the Linux key watch."""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from yulon import docker, forever_keywatch, forever_play, forever_proton, forever_trust
from yulon.catalog.catalog import load_catalog
from yulon.forever_keywatch import (
    KNOWN_REGION_KEYS,
    PAGE_IS_SOFT_DIRTY,
    Memory,
    WatchSeams,
    clip,
    is_game,
    is_this_game,
    take,
    world_connected,
    writable_mappings,
)
from yulon.forever_proton import PinnedFile, ProtonError, ProtonSeams
from yulon.single_play import PlayRefusal

FOREVER = load_catalog().get_single("wow-forever")
SERVER_KEY = bytes.fromhex("02596F0D0C061A8B30745988FD72C59E29EC367FB0F341F28E0F08D037BAFC69")
GROUP8 = KNOWN_REGION_KEYS[7]
PORTAL = FOREVER.play.forever.portal  # type: ignore[union-attr]


# -- the key watch ------------------------------------------------------------------------


def test_the_game_is_known_by_the_windows_path_wine_keeps_as_argv0() -> None:
    assert is_game(
        b"Z:\\home\\deck\\Games\\WoWForever-70888\\World of Warcraft\\_forever_\\Wow.exe"
    )
    assert is_game(b"C:\\Games\\World of Warcraft\\_forever_\\WowB.exe")
    assert not is_game(b"/home/deck/.local/share/Steam/compatibilitytools.d/GE/proton")
    assert not is_game(b"C:\\Program Files (x86)\\Battle.net\\Battle.net.exe")


def test_only_writable_heaps_and_wine_sections_are_searched() -> None:
    maps = b"\n".join(
        [
            b"00010000-00020000 rw-p 00000000 00:00 0",
            b"00020000-00030000 r-xp 00000000 00:00 0",
            b"00030000-00040000 rw-p 00000000 08:01 1 /usr/lib/libc.so.6",
            b"00040000-00050000 rw-p 00000000 00:00 0 [heap]",
            b"00050000-00060000 rw-s 00000000 00:05 7 /dev/dri/renderD128",
            b"00060000-00070000 rw-p 00000000 08:01 2 /home/deck/Games/_forever_/Wow.exe",
            b"00070000-00080000 rw-s 00000000 00:01 3 /memfd:wine-mapping (deleted)",
        ]
    )
    assert writable_mappings(maps) == [(0x10000, 0x20000), (0x40000, 0x50000), (0x70000, 0x80000)]


def test_page_runs_are_cut_to_the_mappings_and_a_sweep_is_taken_in_slices() -> None:
    mappings = [(0, 100), (200, 300)]
    pieces = clip([(50, 250)], mappings)
    assert pieces == [(50, 100, 0, 100), (200, 250, 200, 300)]
    sweep = list(pieces)
    part, i = take(sweep, 0, 60)
    assert part == [(50, 100, 0, 100), (200, 210, 200, 300)] and i == 1
    assert sweep[1] == (210, 250, 200, 300)


def test_the_world_connection_is_a_loopback_socket_on_the_world_port() -> None:
    head = "  sl  local_address rem_address   st tx_queue rx_queue\n"
    up = head + "   0: 0100007F:D2A4 0100007F:1F95 01 00000000:00000000\n"
    listening = head + "   0: 0100007F:1F95 00000000:0000 0A 00000000:00000000\n"
    other = head + "   0: 0100007F:D2A4 0100007F:0477 01 00000000:00000000\n"
    six = (
        head
        + "   0: 00000000000000000000000001000000:D2A4 00000000000000000000000001000000:1F95 01\n"
    )
    assert world_connected([up], 8085)
    assert world_connected(["", six], 8085)
    assert not world_connected([listening, other], 8085)


class FakeGame(Memory):
    """A game's memory as one buffer at `base`, alive for `life` checks."""

    def __init__(self, data: bytes, *, base: int = 0x10000, life: int = 3, dirty: bool = True):
        self.pid, self.base, self.buf = 42, base, bytearray(data)
        self.life, self.dirty = life, dirty
        self.cleared = 0

    def close(self) -> None:
        pass

    def alive(self) -> bool:
        self.life -= 1
        return self.life >= 0

    def maps(self) -> bytes:
        end = self.base + len(self.buf)
        return f"{self.base:08x}-{end:08x} rw-p 00000000 00:00 0".encode()

    def read(self, start: int, end: int) -> bytes:
        return bytes(self.buf[start - self.base : end - self.base])

    def write(self, at: int, data: bytes) -> bool:
        self.buf[at - self.base : at - self.base + len(data)] = data
        return True

    def page_ranges(self, mask: int, anyof: int) -> list[tuple[int, int]]:
        if not self.dirty:
            raise OSError(25, "PAGEMAP_SCAN failed")
        return [(self.base, self.base + len(self.buf))]

    def clear_dirty(self) -> None:
        self.cleared += 1


def _memory_with_keys(size: int = 3 << 20) -> tuple[bytearray, list[int]]:
    data = bytearray(size)
    spots = [100, 5000, 2_000_000]
    data[spots[0] : spots[0] + 32] = GROUP8
    data[spots[1] : spots[1] + 32] = KNOWN_REGION_KEYS[2]
    data[spots[2] : spots[2] + 32] = GROUP8
    return data, spots


def test_search_finds_every_copy_and_replace_writes_the_server_key() -> None:
    data, spots = _memory_with_keys()
    game = FakeGame(bytes(data))
    whole = [(game.base, game.base + len(data), game.base, game.base + len(data))]
    hits, read = forever_keywatch.search(game, whole, [GROUP8])
    assert hits == {game.base + spots[0], game.base + spots[2]} and read >= len(data)
    hits, _ = forever_keywatch.search(game, whole, KNOWN_REGION_KEYS)
    assert len(hits) == 3
    assert forever_keywatch.replace(game, hits, SERVER_KEY, GROUP8) == (3, 0, 2)
    for at in spots:
        assert game.buf[at : at + 32] == SERVER_KEY
    assert forever_keywatch.replace(game, hits, SERVER_KEY, GROUP8) == (0, 0, 0)


def _watch_seams(game: FakeGame | None, tcp: Sequence[str] = ()) -> WatchSeams:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return WatchSeams(
        find_game=lambda _client: game.pid if game is not None else None,
        open_memory=lambda _pid: game,  # type: ignore[arg-type,return-value]
        tcp_tables=lambda: list(tcp),
        sleep=sleep,
        clock=lambda: now[0],
    )


@pytest.mark.parametrize("dirty", [True, False], ids=["changed-pages", "older-kernel"])
def test_the_watch_replaces_the_keys_and_ends_with_the_game(dirty: bool) -> None:
    data, spots = _memory_with_keys()
    game = FakeGame(bytes(data), dirty=dirty)
    said: list[str] = []
    assert forever_keywatch.watch(SERVER_KEY, 8, 8085, say=said.append, seams=_watch_seams(game))
    assert all(game.buf[at : at + 32] == SERVER_KEY for at in spots)
    assert "The server's login key is in the game." in said
    assert (game.cleared > 0) is dirty
    assert any("sweeps" in line for line in said) is not dirty


def test_the_watch_idles_in_the_world_and_gives_up_on_a_game_that_never_starts() -> None:
    """Connected for `SETTLE` seconds: a key that turns up after that is left alone."""
    up = "h\n 0: 0100007F:D2A4 0100007F:1F95 01\n"
    game = FakeGame(bytes(1 << 20), life=200)
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        if now[0] > forever_keywatch.SETTLE + 5 and game.buf[64:96] != GROUP8:
            game.buf[64:96] = GROUP8  # a copy made long after the login

    seams = WatchSeams(
        find_game=lambda _client: game.pid,
        open_memory=lambda _pid: game,  # type: ignore[arg-type,return-value]
        tcp_tables=lambda: [up],
        sleep=sleep,
        clock=lambda: now[0],
    )
    assert not forever_keywatch.watch(SERVER_KEY, 8, 8085, seams=seams)
    assert game.buf[64:96] == GROUP8 and now[0] > forever_keywatch.SETTLE
    said: list[str] = []
    assert not forever_keywatch.watch(
        SERVER_KEY, 8, 8085, say=said.append, seams=_watch_seams(None)
    )
    assert said == ["The game did not start, so no login key was written."]


def test_only_a_game_running_from_this_client_folder_is_this_servers_game() -> None:
    """T888 item 5: another copy of the game on the Deck is another game's memory."""
    deck = Path("/home/deck/Games/WoWForever-70888/World of Warcraft")
    mine = b"Z:\\home\\deck\\Games\\WoWForever-70888\\World of Warcraft\\_forever_\\Wow.exe"
    other = b"Z:\\home\\deck\\Games\\Other\\_forever_\\Wow.exe"
    assert is_this_game(mine, deck) and is_this_game(mine, None)
    assert not is_this_game(other, deck)
    assert not is_this_game(
        mine.replace(b"_forever_", b"_forever_2"), deck
    ), "a prefix is not a parent"
    assert not is_this_game(b"C:\\Games\\_forever_\\Wow.exe", deck), "a prefix path is not ours"
    assert forever_keywatch.wine_path(mine) == Path(
        "/home/deck/Games/WoWForever-70888/World of Warcraft/_forever_/Wow.exe"
    )


def test_the_watch_ignores_a_game_that_was_already_running() -> None:
    """T888 item 5: the pre-launch pid set is skipped; the new pid is the one attached to."""
    data, spots = _memory_with_keys()
    game = FakeGame(bytes(data))
    opened: list[int] = []
    asked = {"n": 0}

    def find_game(_client: Path | None) -> int | None:
        asked["n"] += 1
        return 111 if asked["n"] == 1 else game.pid

    seams = WatchSeams(
        find_game=find_game,
        open_memory=lambda pid: (opened.append(pid), game)[1],  # type: ignore[arg-type,return-value]
        tcp_tables=lambda: [],
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    assert forever_keywatch.watch(SERVER_KEY, 8, 8085, before=(111,), seams=seams)
    assert opened == [game.pid]
    assert game.buf[spots[0] : spots[0] + 32] == SERVER_KEY


def test_linux_play_refuses_while_the_game_this_client_starts_already_runs(
    tmp_path: Path,
) -> None:
    """T888 item 5: on the Deck, `forever_keywatch`'s own finder is the refusal."""
    calls: list[str] = []
    seams = forever_play.PlaySeams(
        **{**_linux_seams(calls).__dict__, "find_game": lambda _client: 4040}
    )
    with pytest.raises(PlayRefusal, match="already running"):
        forever_play.play(
            FOREVER, _server(tmp_path), _client(tmp_path), os_name="linux", seams=seams
        )
    assert calls == []


def test_the_soft_dirty_flag_is_the_one_the_kernel_defines() -> None:
    assert PAGE_IS_SOFT_DIRTY == 1 << 7 and forever_keywatch.PAGEMAP_SCAN == 0xC0606610


# -- GE-Proton, the runtime and the prefix --------------------------------------------------


def _home(tmp_path: Path, *, slr: bool = True, proton: bool = True) -> Path:
    home = tmp_path / "home"
    steam = home / ".local/share/Steam"
    (steam / "steamapps/common").mkdir(parents=True)
    if slr:
        runtime = steam / "steamapps/common/SteamLinuxRuntime_4"
        runtime.mkdir()
        (runtime / "_v2-entry-point").write_text("#!/bin/sh\n")
    if proton:
        tool = steam / "compatibilitytools.d" / forever_proton.GE_PROTON_FOLDER
        tool.mkdir(parents=True)
        (tool / "proton").write_text("#!/usr/bin/env python3\n")
    return home


def test_paths_reach_the_prefix_through_wines_z_drive() -> None:
    assert forever_proton.winpath(Path("/home/deck/x y/ca.pem")) == "Z:\\home\\deck\\x y\\ca.pem"


def test_the_runtime_is_found_in_steam_or_on_an_sd_card(tmp_path: Path) -> None:
    home = _home(tmp_path)
    found = forever_proton.find_slr(home, media=tmp_path / "none")
    assert found is not None and found.name == "_v2-entry-point"
    bare = _home(tmp_path / "b", slr=False)
    media = tmp_path / "media"
    card = media / "deck" / "abcd-1234" / "steamapps" / "common" / "SteamLinuxRuntime_4"
    card.mkdir(parents=True)
    (card / "_v2-entry-point").write_text("")
    assert forever_proton.find_slr(bare, media=media) == card / "_v2-entry-point"
    assert forever_proton.find_slr(bare, media=tmp_path / "none") is None


def test_the_launch_command_and_environment_are_deck_ones() -> None:
    home, server, client = Path("/home/deck"), Path("/home/deck/srv"), Path("/home/deck/Games/c")
    prefix = server / forever_proton.PREFIX_NAME
    env = forever_proton.compat_env(home, prefix, client, server)
    assert env["STEAM_COMPAT_DATA_PATH"] == str(prefix)
    assert env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(home / ".local/share/Steam")
    assert env["STEAM_COMPAT_MOUNTS"].split(":") == sorted([str(client), str(server)])
    assert env["WINE_SIMULATE_WRITECOPY"] == "1"
    assert any(c.isdigit() for c in forever_proton.PREFIX_NAME)  # protonfixes needs digits
    argv = forever_proton.proton_argv(Path("/slr/_v2-entry-point"), Path("/ge"), "Wow.exe", ["-a"])
    assert argv[1:3] == ["--verb=waitforexitandrun", "--"]
    assert argv[4:] == ["waitforexitandrun", "Wow.exe", "-a"]


def _tarball(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _pinned(data: bytes, name: str) -> PinnedFile:
    return PinnedFile(
        url=f"https://example.invalid/{name}",
        name=name,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def test_ge_proton_is_downloaded_checked_and_unpacked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = forever_proton.GE_PROTON_FOLDER
    tarball = _tarball({f"{folder}/proton": b"#!/usr/bin/env python3\n", f"{folder}/version": b"x"})
    monkeypatch.setattr(forever_proton, "GE_PROTON", _pinned(tarball, "ge.tar.gz"))
    home = _home(tmp_path, proton=False)
    fetched: list[str] = []

    def download(url: str, dest: Path) -> Path:
        fetched.append(url)
        dest.write_bytes(tarball)
        return dest

    seams = ProtonSeams(home=lambda: home, download=download)
    got = forever_proton.ensure_proton(lambda _m: None, seams)
    assert (got / "proton").is_file() and got.name == folder and len(fetched) == 1
    assert forever_proton.ensure_proton(lambda _m: None, seams) == got and len(fetched) == 1


def test_a_download_that_does_not_match_is_deleted_and_refused(tmp_path: Path) -> None:
    pinned = _pinned(b"the real file", "f.bin")
    dest = tmp_path / "f.bin"

    def download(url: str, path: Path) -> Path:
        path.write_bytes(b"something else")
        return path

    with pytest.raises(ProtonError, match="did not match"):
        forever_proton.fetch(pinned, dest, lambda _m: None, ProtonSeams(download=download))
    assert not dest.exists()


def test_a_tarball_member_outside_its_folder_refuses_the_whole_tarball() -> None:
    top = forever_proton.GE_PROTON_FOLDER
    for name in ("../evil", "/etc/passwd", "other/proton"):
        with pytest.raises(ProtonError):
            forever_proton.safe_members([tarfile.TarInfo(f"{top}/ok"), tarfile.TarInfo(name)], top)


class FakeProton:
    """Records each Proton run and does what Wine would: a prefix, then the CA report."""

    def __init__(self, *, trusted: bool = True) -> None:
        self.runs: list[list[str]] = []
        self.trusted = trusted

    def run(self, argv: list[str], **kwargs: Any) -> object:
        self.runs.append(argv)
        prefix = Path(kwargs["cwd"])
        if "cmd.exe" in argv[5]:
            (prefix / "pfx" / "drive_c" / "windows").mkdir(parents=True)
        elif argv[5].endswith("python.exe"):
            said = forever_proton.REPORT_TRUSTED if self.trusted else "CA in LocalMachine\\Root: NO"
            (prefix / "import_ca-report.txt").write_text(said + "\n")
        return object()


NARROW_CA = Path(__file__).parent / "data" / "forever-certs" / "narrow-critical.pem"


def _server(tmp_path: Path) -> Path:
    server = tmp_path / "srv"
    python = server / forever_proton.PREFIX_NAME / "pfx" / "drive_c" / "python313"
    python.mkdir(parents=True)
    (python / "python.exe").write_bytes(b"MZ")
    (server / "certs").mkdir()
    (server / "certs" / "ca.pem").write_bytes(NARROW_CA.read_bytes())
    return server


def test_prepare_makes_the_prefix_once_and_trusts_the_ca_in_it_every_play(tmp_path: Path) -> None:
    home, server, proton = _home(tmp_path), _server(tmp_path), FakeProton()
    seams = ProtonSeams(home=lambda: home, run=proton.run)
    client = tmp_path / "client"
    runtime = forever_proton.prepare(
        server, client, server / "certs/ca.pem", print, portal=PORTAL, seams=seams
    )
    assert runtime.prefix == server / forever_proton.PREFIX_NAME
    assert runtime.compat["STEAM_COMPAT_INSTALL_PATH"] == str(client)
    assert [r[5].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for r in proton.runs] == [
        "cmd.exe",
        "python.exe",
    ]
    assert proton.runs[1][6:8] == ["-I", forever_proton.winpath(runtime.prefix / "import_ca.py")]
    # The CA the prefix is given is the CHECKED COPY, not the file in the player's folder:
    # that folder is writable, and the prefix trusts whatever it is handed (T888).
    imported = Path(proton.runs[1][8].removeprefix("Z:").replace("\\", "/"))
    assert imported.name.startswith("yulon-ca-") and not imported.exists()
    forever_proton.prepare(
        server, client, server / "certs/ca.pem", print, portal=PORTAL, seams=seams
    )
    assert len(proton.runs) == 3  # no second prefix; the CA is checked again


def test_prepare_says_how_to_get_the_runtime_and_refuses_an_untrusted_ca(tmp_path: Path) -> None:
    server = _server(tmp_path)
    bare, full = _home(tmp_path / "a", slr=False), _home(tmp_path / "b")
    no_runtime = ProtonSeams(home=lambda: bare, run=FakeProton().run)
    with pytest.raises(ProtonError, match="Steam Linux Runtime 4.0 is not installed"):
        forever_proton.prepare(
            server, tmp_path, server / "certs/ca.pem", print, portal=PORTAL, seams=no_runtime
        )
    refusing = ProtonSeams(home=lambda: full, run=FakeProton(trusted=False).run)
    with pytest.raises(ProtonError, match="did not take the install's certificate"):
        forever_proton.prepare(
            server, tmp_path, server / "certs/ca.pem", print, portal=PORTAL, seams=refusing
        )
    broad = tmp_path / "broad.pem"
    broad.write_bytes(Path(__file__).parent.joinpath("data/forever-certs/broad.pem").read_bytes())
    with pytest.raises(forever_trust.TrustError, match="could not be checked"):
        forever_proton.prepare(
            server, tmp_path, broad, print, portal=PORTAL, seams=ProtonSeams(home=lambda: full)
        )


def test_the_hosts_line_is_kept_added_with_sudo_n_or_reported_missing() -> None:
    portal = "trinity.actual.battle.net"
    hosts = ["127.0.0.1 localhost\n"]
    ran: list[list[str]] = []

    def sudo(argv: list[str], **_kw: Any) -> object:
        ran.append(argv)
        hosts.append(f"127.0.0.1 {portal} # yulon-wow-forever\n")
        return object()

    there = ProtonSeams(read_hosts=lambda: f"127.0.0.1 {portal}\n", run=sudo)
    assert forever_proton.hosts_ready(portal, there) and ran == []
    no_sudo = ProtonSeams(read_hosts=lambda: "".join(hosts), which=lambda _n: None)
    assert not forever_proton.hosts_ready(portal, no_sudo)
    added = ProtonSeams(
        read_hosts=lambda: "".join(hosts), run=sudo, which=lambda n: f"/usr/bin/{n}"
    )
    assert forever_proton.hosts_ready(portal, added)
    assert ran[0][:2] == ["sudo", "-n"] and "/etc/hosts" in ran[0][-1]


# -- Play on Linux ----------------------------------------------------------------------------


def _client(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    (root / "_forever_").mkdir(parents=True)
    with open(root / "_forever_" / "Wow.exe", "wb") as handle:
        handle.truncate(80627920)
    (root / ".build.info").write_text("Branch!STRING:0|Version!STRING:0\nus|1.60.1.70888\n")
    return root


def _linux_seams(calls: list[str], *, hosts: bool = True) -> forever_play.PlaySeams:
    def prepare(server: Path, client: Path, ca: Path, say: Any, **_: Any) -> object:
        calls.append(f"prepare {ca.name}")
        return "runtime"

    return forever_play.PlaySeams(
        state_of=lambda _c: docker.ContainerState(status="running"),
        trust=lambda *_a, **_k: calls.append("windows trust") or True,
        account=lambda *_a: (calls.append("account") or ("mmodad@local", "pass")),
        hosts_ready=lambda portal: calls.append(f"hosts {portal}") is None and hosts,
        prepare_proton=prepare,  # type: ignore[arg-type]
        start_game=lambda runtime, exe, args: calls.append(f"start {exe.name} {' '.join(args)}"),
        keywatch=lambda key, group, port, **_kw: calls.append(f"keywatch {group} {port}"),
    )


def test_linux_play_checks_hosts_makes_the_account_and_starts_under_proton(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path)
    server = _server(tmp_path)
    played = forever_play.play(FOREVER, server, client, os_name="linux", seams=_linux_seams(calls))
    assert calls == [
        "hosts trinity.actual.battle.net",
        "account",
        "prepare ca.pem",
        "start Wow.exe -config ForeverLauncher.wtf",
        "keywatch 8 8085",
    ]
    assert (played.account, played.password, played.asked_admin) == ("mmodad", "pass", False)
    assert (client / "_forever_" / "WTF" / "ForeverLauncher.wtf").is_file()


def test_linux_play_without_the_hosts_line_names_the_one_command(tmp_path: Path) -> None:
    calls: list[str] = []
    with pytest.raises(PlayRefusal, match=r"sudo tee -a /etc/hosts"):
        forever_play.play(
            FOREVER,
            _server(tmp_path),
            _client(tmp_path),
            os_name="linux",
            seams=_linux_seams(calls, hosts=False),
        )
    assert calls == ["hosts trinity.actual.battle.net"]


def test_the_entry_installs_on_linux_with_its_login_service_off_steams_8081() -> None:
    assert FOREVER.install.supports("linux") and FOREVER.install.supports("windows")
    assert 8081 not in FOREVER.host_ports() and forever_play.world_port(FOREVER) == 8085
