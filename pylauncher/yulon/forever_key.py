"""WoW Forever's login key, written into the running client (`catalog.ForeverLogin`).

The 70707 client accepts a login server only if the key it announces is one of 12 public
keys the client holds in private read/write memory: 12 records of 40 bytes (uint32 id
1..12, the 32-byte key, a flag byte, 0x00 or 0x7f, two zero bytes). The server announces
the record of its region group (worldserver `Network.EnterEncryptedModeRegionGroup`), so
Play writes the server's PUBLIC key into that record and keeps it there while the game
runs: the client may rebuild the table, and the key is then written again. Nothing on
disk is changed and no program code is patched.

After Karrz's `Play-WoWForever.ps1` (WoW Forever Bots 0.1.34, GPL-2), through the
standard-library port in C:/DML/servers/wowforever/play_forever.py that played on
2026-11-10. Windows only: on Linux the client runs under Proton, where this runs inside
the prefix (the Steam Deck kit's play_logged.py), not here.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon.log import get_logger

logger = get_logger(__name__)

ENTRIES, ENTRY_SIZE = 12, 40
BLOCK_SIZE = ENTRIES * ENTRY_SIZE
FLAGS = (1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1)
"""Each record's flag byte, as the 70707 client lays the table out (measured 2026-11-10)."""

CHUNK = 8 * 1024 * 1024
"""How much of a memory region is read at a time while looking for the table."""

# Every record starts with its id as uint32 LE (1..12), 40 bytes apart: one regex finds candidates.
_TABLE_RE = re.compile(
    b"(?=" + b".{36}".join(bytes((i, 0, 0, 0)) for i in range(1, ENTRIES + 1)) + b")", re.DOTALL
)

START_WAIT_SECONDS = 90.0
"""How long the watch waits for the started game to show up as a process."""

POLL_SECONDS = 3.0
"""How often the watch checks the written key while the game runs."""

NO_GIVE_UP = (
    "The watch never stops looking for a table it has not found: 70888 builds its table only "
    "DURING the first Login press (that press fails, 'server identity check failed "
    "regionGroup=8'; the key is written; the second press works), however long the player "
    "waits at the login screen first (measured 2026-11-13). A time limit broke exactly that."
)


def looks_like_table(block: bytes, offset: int = 0) -> bool:
    """True if `block[offset:offset + BLOCK_SIZE]` has the layout of the login key table."""
    if len(block) - offset < BLOCK_SIZE:
        return False
    for index in range(ENTRIES):
        at = offset + index * ENTRY_SIZE
        if int.from_bytes(block[at : at + 4], "little") != index + 1:
            return False
        if block[at + 36] != FLAGS[index] or block[at + 38] != 0 or block[at + 39] != 0:
            return False
        if block[at + 37] not in (0, 0x7F):
            return False
        if not any(block[at + 4 : at + 36]):
            return False
    return True


def tables_in(buffer: bytes, base: int) -> list[int]:
    """Addresses (`base` + offset) of every 4-byte-aligned key table inside `buffer`."""
    found = []
    for match in _TABLE_RE.finditer(buffer):
        offset = match.start()
        if (base + offset) % 4 == 0 and looks_like_table(buffer, offset):
            found.append(base + offset)
    return found


def record_key(block: bytes, group: int) -> bytes:
    """The 32-byte key the table `block` holds for `group` (1..12)."""
    at = (group - 1) * ENTRY_SIZE + 4
    return block[at : at + 32]


class Memory(Protocol):
    """What the watch needs of the game's memory: find the tables, read one, write a key."""

    def find_tables(self) -> list[int]: ...

    def read(self, address: int, size: int) -> bytes: ...

    def write(self, address: int, data: bytes) -> bool: ...

    def close(self) -> None: ...


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong),
        ("PartitionId", ctypes.c_ushort),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
    ]


_ACCESS = 0x0400 | 0x0010 | 0x0020 | 0x0008  # query information, VM read, VM write, VM operation
_MEM_COMMIT, _MEM_PRIVATE, _PAGE_READWRITE = 0x1000, 0x20000, 0x04


class WindowsMemory:
    """The game process's memory through OpenProcess / VirtualQueryEx / Read/WriteProcessMemory."""

    def __init__(self, pid: int) -> None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.VirtualQueryEx.restype = ctypes.c_size_t
        k.VirtualQueryEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_MBI),
            ctypes.c_size_t,
        ]
        for call in (k.ReadProcessMemory, k.WriteProcessMemory):
            call.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
            ]
        self._k = k
        self._handle = k.OpenProcess(_ACCESS, False, pid)
        if not self._handle:
            raise OSError(
                ctypes.get_last_error(),  # type: ignore[attr-defined,unused-ignore]
                "The game's process could not be opened. Start Yu'lon and the game as the "
                "same user, neither of them as administrator.",
            )

    def close(self) -> None:
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None

    def read(self, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = self._k.ReadProcessMemory(
            self._handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got)
        )
        return buffer.raw[: got.value] if ok else b""

    def write(self, address: int, data: bytes) -> bool:
        done = ctypes.c_size_t(0)
        buffer = ctypes.create_string_buffer(data, len(data))
        ok = self._k.WriteProcessMemory(
            self._handle, ctypes.c_void_p(address), buffer, len(data), ctypes.byref(done)
        )
        return bool(ok) and done.value == len(data)

    def find_tables(self) -> list[int]:
        """Every key table in the process's committed private read/write memory."""
        found: list[int] = []
        address, info = 0, _MBI()
        while address < 0x7FFFFFFF0000:
            if not self._k.VirtualQueryEx(
                self._handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
            ):
                break
            base, size = info.BaseAddress or 0, info.RegionSize
            if size <= 0:
                break
            if (
                info.State == _MEM_COMMIT
                and info.Type == _MEM_PRIVATE
                and info.Protect == _PAGE_READWRITE
            ):
                position = 0
                while position < size:
                    take = min(CHUNK, size - position)
                    buffer = self.read(base + position, take)
                    if not buffer:
                        break
                    found += tables_in(buffer, base + position)
                    if position + take >= size:
                        break
                    position += take - BLOCK_SIZE  # overlap: a table on a chunk border is found
            address = base + size
        return sorted(set(found))


def _image_pids(match: Callable[[str], bool]) -> list[int]:
    """Process ids whose image path `match` accepts (Windows); [] elsewhere or if none.

    One enumeration for both questions this module asks: "is THE game at this exact path
    running" (`pids_of`) and "is any program under this client folder running"
    (`pids_under`, which is what Play refuses on).
    """
    if sys.platform != "win32":
        return []
    psapi = ctypes.WinDLL("psapi")  # type: ignore[attr-defined,unused-ignore]
    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined,unused-ignore]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    ids = (ctypes.c_ulong * 4096)()
    needed = ctypes.c_ulong()
    if not psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
        return []
    found: list[int] = []
    buffer = ctypes.create_unicode_buffer(32768)
    for pid in ids[: needed.value // ctypes.sizeof(ctypes.c_ulong)]:
        if not pid:
            continue
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            size = ctypes.c_ulong(len(buffer))
            if kernel32.QueryFullProcessImageNameW(
                ctypes.c_void_p(handle), 0, buffer, ctypes.byref(size)
            ) and match(os.path.normcase(buffer.value)):
                found.append(int(pid))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    return found


def pids_of(exe: Path) -> list[int]:
    """Process ids whose image is exactly `exe` (Windows); [] elsewhere or if none."""
    if sys.platform != "win32":
        return []
    want = os.path.normcase(str(exe.resolve()))
    return _image_pids(lambda image: image == want)


def pids_under(folder: Path) -> list[int]:
    """Process ids whose image is inside `folder` (Windows); [] elsewhere or if none.

    Play's "this client is already running" question. `client_dir` holds the game at
    `_forever_/Wow.exe` and everything else the player put there, and a second copy of that
    game under it is what two Play presses would make.
    """
    if sys.platform != "win32":
        return []
    root = os.path.normcase(str(folder.resolve()))
    if not root.endswith(os.sep):
        root += os.sep
    return _image_pids(lambda image: image.startswith(root))


@dataclass(frozen=True)
class KeySeams:
    """Everything the watch reaches outside itself through. Real by default."""

    pids: Callable[[Path], Sequence[int]] = pids_of
    open_memory: Callable[[int], Memory] = WindowsMemory
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def watch(
    exe: Path,
    key: bytes,
    group: int,
    *,
    before: Collection[int] = (),
    stop: threading.Event | None = None,
    say: Callable[[str], None] = logger.info,
    seams: KeySeams | None = None,
) -> bool:
    """Keep `key` in record `group` of the game's key table until the game exits.

    Waits for `exe` to show up as a process, finds the one table, writes the key and
    checks it every few seconds, writing it again if the client rebuilt its table.
    True once the key was applied at least once; False if the game never started or exited
    before it could be. It never gives up while the game runs (`NO_GIVE_UP`).

    `before` is the set of process ids that matched `exe` BEFORE Play launched the game
    (T888): an older copy of the game that is still running is not this launch, and writing
    this server's key into it is the wrong game's memory. Only a pid that was not in
    `before` is attached to.
    """
    if len(key) != 32 or not 1 <= group <= ENTRIES:
        raise ValueError("a login key is 32 bytes, for a region group of 1 to 12")
    s = seams if seams is not None else KeySeams()
    halt = stop if stop is not None else threading.Event()
    deadline = s.clock() + START_WAIT_SECONDS
    pids: Sequence[int] = ()
    while not halt.is_set():
        pids = [pid for pid in s.pids(exe) if pid not in before]
        if pids or s.clock() >= deadline:
            break
        s.sleep(1.0)
    if not pids:
        say(f"{exe.name} did not start, so no login key was written.")
        return False
    pid = pids[0]
    memory = s.open_memory(pid)
    applied = False
    table: int | None = None
    try:
        while not halt.is_set() and pid in s.pids(exe):
            if table is not None:
                block = memory.read(table, BLOCK_SIZE)
                if len(block) == BLOCK_SIZE and looks_like_table(block):
                    if record_key(block, group) == key:
                        s.sleep(POLL_SECONDS)
                        continue
                    if _write(memory, table, group, key):
                        say("The game rebuilt its key table; the login key was written again.")
                    else:
                        s.sleep(1.0)
                    continue
                table = None  # freed: look again
            found = memory.find_tables()
            if len(found) == 1 and _write(memory, found[0], group, key):
                table = found[0]
                if not applied:
                    say(
                        "The server's login key is in the game. If the first login fails, "
                        "press Login again without closing the game."
                    )
                applied = True
                continue
            s.sleep(2.0)  # not built yet, or two tables at once while the client swaps them
    finally:
        memory.close()
    return applied


def _write(memory: Memory, table: int, group: int, key: bytes) -> bool:
    """Write `key` into record `group` of the table at `table`; True once it reads back."""
    if not memory.write(table + (group - 1) * ENTRY_SIZE + 4, key):
        return False
    block = memory.read(table, BLOCK_SIZE)
    return len(block) == BLOCK_SIZE and looks_like_table(block) and record_key(block, group) == key


_WATCHES: dict[str, threading.Thread] = {}
"""The login-key watch running for each game path this launch started (T888).

One game, one watch: a second press of Play while the first watch still runs must not put
two threads in the same process's memory, both rewriting the same table. The dict holds
the thread under `os.path.normcase(str(exe))`; a finished thread is replaced.
"""

_WATCHES_LOCK = threading.Lock()


def start_watch(
    exe: Path,
    key: bytes,
    group: int,
    *,
    before: Collection[int] = (),
    say: Callable[[str], None] = logger.info,
) -> threading.Thread:
    """`watch()` on a daemon thread, so Play returns while the key is kept in the game.

    Never a second watch for the same game while one runs: the thread already watching is
    returned instead.
    """
    key_path = os.path.normcase(str(exe))
    with _WATCHES_LOCK:
        live = _WATCHES.get(key_path)
        if live is not None and live.is_alive():
            logger.info(
                f"the login key watch for {exe.name} is already running; not starting another"
            )
            return live
        thread = threading.Thread(
            target=lambda: _logged_watch(exe, key, group, before, say),
            name="forever-login-key",
            daemon=True,
        )
        _WATCHES[key_path] = thread
        thread.start()
        return thread


def _logged_watch(
    exe: Path,
    key: bytes,
    group: int,
    before: Collection[int],
    say: Callable[[str], None],
) -> None:
    try:
        watch(exe, key, group, before=before, say=say)
    except OSError as exc:
        say(f"The login key could not be written into the game: {exc}")
