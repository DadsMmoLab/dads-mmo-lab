"""The server's login key in WoW Forever's memory, written from Linux beside a Proton game.

Steam Deck #1's `keywatch.py` (kit v5, 2026-11-14), run by Play on a thread:

* The 70888 client builds its copies of the 12 region-group public keys only when Login is
  pressed, and the server signs its identity 1.5 s after the world connection opens. Under
  Proton the strict key-table check `forever_key` makes on Windows never matches (record 9's
  flag is 1 there, 0 on Windows), so every exact 32-byte copy of the 12 keys is overwritten
  with the server's key instead (Karrz's "certificate key mode").
* Wine lets any process of the same user open `/proc/<pid>/mem` for reading and writing, at
  2.4 GB/s; Windows' ReadProcessMemory through wineserver took 11 to 36 s per pass. Every
  0.15 s the kernel says which pages the game wrote (`PAGEMAP_SCAN`, soft-dirty), the bits
  are cleared (`clear_refs` = 4) and only those pages are searched for the group key. A hit
  replaces it at once and starts a sweep of all resident memory for all 12 keys.
* A background sweep for the group key covers a write that lands between the query and the
  clear. Once the game has held a connection to the world port for 10 s and the all-keys
  sweep is done, it idles until that connection ends.

On a kernel without `PAGEMAP_SCAN` (before 6.7) every pass sweeps instead: slower, the same
result. Deck #1 measured one 137 ms pass and the first Login press getting in.
"""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from bisect import bisect_right
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from yulon.log import get_logger

logger = get_logger(__name__)

KNOWN_REGION_KEYS: tuple[bytes, ...] = tuple(
    bytes.fromhex(h)
    for h in (
        "9b0671c815dff513bfd4a2b26ae1f84ec9106841b2fb620db65f6ade7c21ad06",
        "112451b9843c2799e4750aa3d9b9afabf536a645b863c4adb2078b932b354e04",
        "a6b858485748bf38be193517ab90f8de169eff0989ea9360db346a378b0ffe15",
        "50ffdeb807f8ff73a299a16000aa6575c5945be875adfc8795374ed3415249ac",
        "9f842d078755647c008e5fe3e12b839a981de02a1f520cb2545cc04cd2323e0e",
        "b8cc75864d8ef461d8dd6aa7425e2c63c24d3982066b8d773a1549bfe24e05ee",
        "76bb7bbdd9f34e124a4573c3aa227e3c87cc603506697d054f6fdec342e56ebf",
        "1fd6dd8fa0ec30d39e3f72e755b8a045bde0f70449dc71008b767c2eaa89fb9f",
        "15d618bd7db577bd9a8d45769c59e4fc631633bf447398a4b489b4c26fbc03ad",
        "b34ec592d2ac4993f5ffc85b15c3da9078694051cb224345592af7136d796c99",
        "9e91586dd4b115ab0568b21959e181f6545910bc5e2f30b8975ca09d7bc3efce",
        "769c824a1dadd19aeefa420414d1732daf44537f98aa229fc5477bca55f9d59a",
    )
)
"""The game's own public key for each region group 1 to 12, in order (not secrets)."""

PAGE = 4096
PAGEMAP_SCAN = 0xC0606610  # _IOWR('f', 16, struct pm_scan_arg): 96 bytes
PAGE_IS_PRESENT, PAGE_IS_SWAPPED, PAGE_IS_SOFT_DIRTY = 1 << 3, 1 << 4, 1 << 7
BATCH = 16 << 20
SEP = bytes(32)
"""Between pieces in one search buffer: no key holds 32 zero bytes, so no match spans it."""

GAME_ARGV0 = re.compile(rb"\\_forever_\\wowb?\.exe(?: |$)", re.I)
"""Wine keeps the Windows path as argv[0]: `Z:\\...\\_forever_\\Wow.exe`."""

SKIP_PREFIX = (b"/dev/", b"[vvar", b"[vdso", b"[vsyscall", b"anon_inode:", b"/SYSV")
SKIP_SUFFIX = (b".so", b".exe", b".dll", b".drv", b".sys", b".ocx", b".cpl", b".ax")

INTERVAL = 0.15
SETTLE = 10.0
SWEEP_BYTES = 64 << 20
START_WAIT = 600.0

Range = tuple[int, int]
Piece = tuple[int, int, int, int]
"""(start, end, mapping start, mapping end): a run of pages inside one mapping."""


def is_game(argv0: bytes) -> bool:
    """`argv0` is WoW Forever's game under Wine."""
    return GAME_ARGV0.search(argv0) is not None


def writable_mappings(maps: bytes) -> list[Range]:
    """`/proc/<pid>/maps` lines worth searching: heaps and Wine sections, not devices or images."""
    found: list[Range] = []
    for line in maps.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 2 or parts[1][:2] != b"rw":
            continue
        path = parts[5].strip() if len(parts) > 5 else b""
        bare = path[:-10] if path.endswith(b" (deleted)") else path
        low = bare.lower()
        if path.startswith(SKIP_PREFIX) or low.endswith(SKIP_SUFFIX) or b".so." in low:
            continue
        start, end = parts[0].split(b"-")
        found.append((int(start, 16), int(end, 16)))
    return found


def clip(ranges: Iterable[Range], mappings: Sequence[Range]) -> list[Piece]:
    """Page runs cut to the mappings worth searching, both sorted by address."""
    out: list[Piece] = []
    c = 0
    for start, end in ranges:
        while c < len(mappings) and mappings[c][1] <= start:
            c += 1
        j = c
        while j < len(mappings) and mappings[j][0] < end:
            ms, me = mappings[j]
            out.append((max(start, ms), min(end, me), ms, me))
            j += 1
    return out


def take(sweep: list[Piece], i: int, budget: int) -> tuple[list[Piece], int]:
    """The next `budget` bytes of a sweep from index `i`, splitting a piece if need be."""
    part: list[Piece] = []
    while i < len(sweep) and budget > 0:
        start, end, ms, me = sweep[i]
        if end - start > budget:
            part.append((start, start + budget, ms, me))
            sweep[i] = (start + budget, end, ms, me)
            budget = 0
        else:
            part.append(sweep[i])
            budget -= end - start
            i += 1
    return part, i


def world_connected(tcp_tables: Iterable[str], port: int) -> bool:
    """A socket on this machine is connected to 127.0.0.1:`port` (`/proc/net/tcp*` text)."""
    want = f":{port:04X}"
    for table in tcp_tables:
        for line in table.splitlines()[1:]:
            p = line.split()
            if len(p) < 4 or p[3] != "01" or not p[2].endswith(want):
                continue
            ip = p[2][:-5]
            if (len(ip) == 8 and ip.endswith("7F")) or (
                len(ip) == 32
                and (
                    ip == "00000000000000000000000001000000"
                    or (ip.startswith("0000000000000000FFFF0000") and ip.endswith("7F"))
                )
            ):
                return True
    return False


class Memory:
    """One game process's memory as `/proc` gives it. Linux only; tests use a fake."""

    def __init__(self, pid: int, proc: Path = Path("/proc")) -> None:
        self.pid = pid
        self.root = proc / str(pid)
        self._mem: BinaryIO = (self.root / "mem").open("r+b", buffering=0)
        self._pagemap: BinaryIO = (self.root / "pagemap").open("rb", buffering=0)
        self._clear: BinaryIO = (self.root / "clear_refs").open("wb", buffering=0)
        self.argv0 = (self.root / "cmdline").read_bytes().split(b"\0", 1)[0]
        self._vec = (_PageRegion * 8192)()

    def close(self) -> None:
        for handle in (self._mem, self._pagemap, self._clear):
            try:
                handle.close()
            except OSError:
                pass

    def alive(self) -> bool:
        try:
            return (self.root / "cmdline").read_bytes().split(b"\0", 1)[0] == self.argv0
        except OSError:
            return False

    def maps(self) -> bytes:
        return (self.root / "maps").read_bytes()

    def read(self, start: int, end: int) -> bytes:
        """`end - start` bytes; an unreadable page reads as zeros."""
        try:
            self._mem.seek(start)
            data = self._mem.read(end - start) or b""
            if len(data) == end - start:
                return data
        except OSError:
            pass
        out = bytearray()
        for p in range(start, end, PAGE):
            n = min(PAGE - (p % PAGE), end - p)
            try:
                self._mem.seek(p)
                got = self._mem.read(n) or b""
            except OSError:
                got = b""
            out += got if len(got) == n else bytes(n)
        return bytes(out)

    def write(self, at: int, data: bytes) -> bool:
        try:
            self._mem.seek(at)
            return self._mem.write(data) == len(data)
        except OSError:
            return False

    def page_ranges(self, mask: int, anyof: int) -> list[Range]:
        """Runs of pages whose `PAGEMAP_SCAN` categories match. Raises OSError without it."""
        arg = _PmScanArg()
        arg.size = ctypes.sizeof(arg)
        arg.vec = ctypes.addressof(self._vec)
        arg.vec_len = len(self._vec)
        arg.category_mask, arg.category_anyof_mask = mask, anyof
        arg.return_mask = mask | anyof
        out: list[Range] = []
        pos, end = 0, 0x7FFFFFFFF000
        ioctl = _libc_ioctl()
        while pos < end:
            arg.start, arg.end, arg.walk_end = pos, end, 0
            n = ioctl(self._pagemap.fileno(), PAGEMAP_SCAN, ctypes.byref(arg))
            if n < 0:
                errno = ctypes.get_errno()
                raise OSError(errno, "PAGEMAP_SCAN failed")
            out.extend((self._vec[i].start, self._vec[i].end) for i in range(n))
            if arg.walk_end <= pos:
                break
            pos = arg.walk_end
        return out

    def clear_dirty(self) -> None:
        self._clear.seek(0)
        self._clear.write(b"4")


class _PageRegion(ctypes.Structure):
    _fields_ = [
        ("start", ctypes.c_uint64),
        ("end", ctypes.c_uint64),
        ("categories", ctypes.c_uint64),
    ]


class _PmScanArg(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "size",
            "flags",
            "start",
            "end",
            "walk_end",
            "vec",
            "vec_len",
            "max_pages",
            "category_inverted",
            "category_mask",
            "category_anyof_mask",
            "return_mask",
        )
    ]


def _libc_ioctl() -> Callable[..., int]:
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.ioctl
    fn.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    fn.restype = ctypes.c_int
    return fn


def search(memory: Memory, pieces: Iterable[Piece], keys: Sequence[bytes]) -> tuple[set[int], int]:
    """Where `keys` sit in `pieces` (each widened by 31 bytes in its mapping), and bytes read."""
    hits: set[int] = set()
    buf = bytearray()
    offs: list[int] = []
    addrs: list[int] = []
    total = 0

    def find_in(data: bytes | bytearray, base_of: Callable[[int], int]) -> None:
        for key in keys:
            i = data.find(key)
            while i != -1:
                hits.add(base_of(i))
                i = data.find(key, i + 1)

    def base_in_buf(i: int) -> int:
        k = bisect_right(offs, i) - 1
        return addrs[k] + i - offs[k]

    def flush() -> None:
        find_in(buf, base_in_buf)
        buf.clear()
        offs.clear()
        addrs.clear()

    for start, end, ms, me in pieces:
        start, end = max(ms, start - 31), min(me, end + 31)
        while start < end:
            stop = min(end, start + BATCH)
            data = memory.read(start, stop)
            total += len(data)
            if len(data) >= 1 << 20:
                find_in(data, _offset_by(start))
            else:
                offs.append(len(buf))
                addrs.append(start)
                buf += data
                buf += SEP
                if len(buf) >= BATCH:
                    flush()
            start = stop - 31 if stop < end else stop
    if buf:
        flush()
    return hits, total


def _offset_by(base: int) -> Callable[[int], int]:
    """An index in a buffer read at `base` as the address it came from."""
    return lambda i: base + i


def replace(
    memory: Memory, hits: Iterable[int], server_key: bytes, group_key: bytes
) -> tuple[int, int, int]:
    """Overwrite every hit still holding a region key: (written, failed, of them `group_key`)."""
    known = set(KNOWN_REGION_KEYS)
    done = bad = ngroup = 0
    for at in sorted(hits):
        was = memory.read(at, at + 32)
        if was not in known:
            continue
        if memory.write(at, server_key) and memory.read(at, at + 32) == server_key:
            done += 1
            ngroup += was == group_key
        else:
            bad += 1
    return done, bad, ngroup


@dataclass
class WatchSeams:
    """What the watch reaches outside itself through. Real by default (Linux)."""

    find_game: Callable[[Path | None], int | None] = lambda client: game_pid(Path("/proc"), client)
    open_memory: Callable[[int], Memory] = Memory
    tcp_tables: Callable[[], list[str]] = field(default=lambda: _tcp_tables(Path("/proc")))
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def wine_path(argv0: bytes) -> Path | None:
    """The Linux path behind Wine's Windows `argv[0]`, or None when it is another drive.

    Wine hands the game a Windows path for a file on this filesystem: `Z:\\home\\deck\\...`
    (the Z: drive is `/`). A `C:` path lives inside a prefix and is not a path this side
    can compare.
    """
    text = argv0.decode("utf-8", "replace").strip().strip('"')
    if len(text) < 2 or text[1] != ":":
        return None
    drive, rest = text[0].upper(), text[2:].replace("\\", "/")
    if drive != "Z":
        return None
    return Path("/" + rest.lstrip("/"))


def is_this_game(argv0: bytes, client_dir: Path | None) -> bool:
    """`argv0` is WoW Forever's game AND it runs from `client_dir` (when one is named).

    T888: the same client folder on the same Deck is what Play launched; a second copy
    elsewhere (another install's folder, a desktop game) is not this server's game.
    """
    if not is_game(argv0):
        return False
    if client_dir is None:
        return True
    path = wine_path(argv0)
    return path is not None and path.is_relative_to(client_dir)


def game_pid(proc: Path, client_dir: Path | None = None) -> int | None:
    """The pid of this client's game under Wine, or None (`is_this_game`)."""
    return next(iter(game_pids(proc, client_dir)), None)


def game_pids(proc: Path, client_dir: Path | None = None) -> list[int]:
    """Every pid of this client's game under Wine, in `proc`'s order (Linux)."""
    found: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != _uid():
                continue
            argv0 = (entry / "cmdline").read_bytes().split(b"\0", 1)[0]
        except OSError:
            continue
        if is_this_game(argv0, client_dir):
            found.append(int(entry.name))
    return found


def _uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else -1


def _tcp_tables(proc: Path) -> list[str]:
    tables = []
    for name in ("tcp", "tcp6"):
        try:
            tables.append((proc / "net" / name).read_text())
        except OSError:
            pass
    return tables


def watch(
    server_key: bytes,
    region_group: int,
    world_port: int,
    *,
    client_dir: Path | None = None,
    before: Collection[int] = (),
    say: Callable[[str], None] = logger.info,
    halt: threading.Event | None = None,
    seams: WatchSeams | None = None,
) -> bool:
    """Watch the game until it closes; True once the server's key was written at least once.

    `client_dir` is the player's client folder: only a game running from under it is this
    server's game (T888). `before` is the set of pids that matched before Play launched,
    and only a pid outside it is attached to -- an older copy that was already running is
    somebody else's game.
    """
    s = seams if seams is not None else WatchSeams()
    stop = halt if halt is not None else threading.Event()
    group_key = KNOWN_REGION_KEYS[region_group - 1]
    started = s.clock()
    pid = s.find_game(client_dir)
    while pid is None or pid in before:
        if stop.is_set() or s.clock() - started > START_WAIT:
            say("The game did not start, so no login key was written.")
            return False
        s.sleep(0.25)
        pid = s.find_game(client_dir)
    memory: Memory | None = None
    for _ in range(120):  # Wine allows ptrace a moment after the process starts
        try:
            memory = s.open_memory(pid)
            break
        except PermissionError:
            s.sleep(0.25)
    if memory is None:
        say(f"The game's memory (process {pid}) could not be opened, so no login key was written.")
        return False
    try:
        return _run(memory, server_key, group_key, world_port, say, stop, s)
    finally:
        memory.close()


def _run(
    memory: Memory,
    server_key: bytes,
    group_key: bytes,
    world_port: int,
    say: Callable[[str], None],
    stop: threading.Event,
    s: WatchSeams,
) -> bool:
    try:
        memory.page_ranges(PAGE_IS_SOFT_DIRTY, PAGE_IS_PRESENT)
        dirty_pages = True
    except OSError:
        dirty_pages = False
        say("This kernel cannot list changed pages; every pass sweeps the game's memory instead.")
    written = False
    total = 0
    all_keys = False
    in_world, conn_since = False, None
    sweep: list[Piece] = []
    sweep_i = 0
    while memory.alive() and not stop.is_set():
        if world_connected(s.tcp_tables(), world_port):
            conn_since = conn_since or s.clock()
            if in_world or (s.clock() - conn_since >= SETTLE and not all_keys):
                in_world = True
                s.sleep(0.5)
                continue
        else:
            conn_since = None
            in_world = False
        t0 = s.clock()
        mappings = writable_mappings(memory.maps())
        hits: set[int] = set()
        if dirty_pages:
            ranges = memory.page_ranges(PAGE_IS_SOFT_DIRTY, PAGE_IS_PRESENT | PAGE_IS_SWAPPED)
            memory.clear_dirty()
            found, _ = search(memory, clip(ranges, mappings), [group_key])
            hits |= found
        if sweep_i >= len(sweep):
            all_keys = False
            whole = memory.page_ranges(0, PAGE_IS_PRESENT) if dirty_pages else mappings
            sweep, sweep_i = clip(whole, mappings), 0
        part, sweep_i = take(sweep, sweep_i, SWEEP_BYTES if dirty_pages else 1 << 62)
        found, _ = search(memory, part, KNOWN_REGION_KEYS if all_keys else [group_key])
        hits |= found
        if hits:
            done, bad, ngroup = replace(memory, hits, server_key, group_key)
            total += done
            if ngroup:
                say(f"Replaced {done} copies of the game's region keys with the server's key.")
                if not written:
                    written = True
                    say("The server's login key is in the game.")
                all_keys = True
                whole = memory.page_ranges(0, PAGE_IS_PRESENT) if dirty_pages else mappings
                sweep, sweep_i = clip(whole, mappings), 0
            if bad:
                say(f"{bad} key copies could not be written; trying again.")
        s.sleep(max(0.0, INTERVAL - (s.clock() - t0)))
    logger.info(f"key watch ended: {total} key copies replaced")
    return written


_WATCHES: dict[str, threading.Thread] = {}
"""The key watch running for each client folder (T888): one game, one watch."""

_WATCHES_LOCK = threading.Lock()


def start_watch(
    server_key: bytes,
    region_group: int,
    world_port: int,
    *,
    client_dir: Path | None = None,
    before: Collection[int] = (),
    say: Callable[[str], None] = logger.info,
) -> threading.Thread:
    """`watch` on a daemon thread, so Play returns while the game starts.

    Never a second watch for the same client folder while one runs: the thread already
    watching is returned instead (`watch` itself ends with the game).
    """
    key_path = "" if client_dir is None else str(client_dir)
    with _WATCHES_LOCK:
        live = _WATCHES.get(key_path)
        if live is not None and live.is_alive():
            logger.info(
                "a key watch for this client folder is already running; not starting another"
            )
            return live
        thread = threading.Thread(
            target=watch,
            args=(server_key, region_group, world_port),
            kwargs={"client_dir": client_dir, "before": before, "say": say},
            name="forever-keywatch",
            daemon=True,
        )
        _WATCHES[key_path] = thread
        thread.start()
        return thread
