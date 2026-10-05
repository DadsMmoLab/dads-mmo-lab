"""A Windows Job object that holds a streamed command's whole process tree (T299).

**Why a job, when `taskkill /T` already ends a build (T246).** taskkill finds a
tree by parent pid at the moment of Stop, so three things are out of its reach:
a descendant whose parent had already exited, a process started between its
snapshot and its kill, and — the other way round — an unrelated process whose
recorded parent pid was later reused by one of the tree's processes, which it
sweeps in. A job tracks membership, not pids. Every process docker.exe starts,
and everything those start, is in the job however late it was started and
whoever its parent was, and nothing else is.

**Created suspended, assigned, then resumed.** A job can only be assigned once
`Popen` has returned. A running docker.exe could start compose before that,
and compose would then be outside the job, so `runner` asks for
CREATE_SUSPENDED and `Job.start()` puts the child in the job before it has run
an instruction. `subprocess` closes the main thread's handle, so the resume is
`NtResumeProcess` on a process handle — the call psutil's `resume()` and
Process Explorer use — rather than `ResumeThread`.

**What the job does to a command that finishes normally: nothing.** Its
stream `release()`s the job, which clears KILL_ON_JOB_CLOSE before closing
the handle, so anything it left running goes on as before. KILL_ON_JOB_CLOSE
is for the other endings: a stream abandoned or stopped (`close()`), and this
launcher exiting or crashing with a command still running, which before left
a build behind. A child that asks to leave the job with
CREATE_BREAKAWAY_FROM_JOB is let go (BREAKAWAY_OK) rather than refused: a
process that says it must outlive its parent can still be started.

Every failure answers "no job" (`create()`), False (`start()`'s assign,
`end()`), or — only for a child that would otherwise never run — `OSError`.
The caller then keeps T246's taskkill. Off Windows nothing here is called.
"""

from __future__ import annotations

import ctypes
import threading
from typing import Any, Protocol

from yulon.log import get_logger

logger = get_logger(__name__)

CREATE_SUSPENDED = 0x00000004
"""The `creationflags` bit a child that is to join a job is created with."""

_KILL_ON_JOB_CLOSE = 0x00002000
_BREAKAWAY_OK = 0x00000800
_EXTENDED_LIMIT_INFORMATION = 9
"""`JobObjectExtendedLimitInformation`; the basic class (2) cannot carry KILL_ON_JOB_CLOSE."""

_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SUSPEND_RESUME = 0x0800
_START_ACCESS = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SUSPEND_RESUME
"""What `AssignProcessToJobObject` needs (SET_QUOTA, TERMINATE) and `NtResumeProcess` needs."""

_ENDED_EXIT_CODE = 1
"""The code a Stop leaves, the same as `Popen.terminate()`'s on Windows."""


class BasicLimits(ctypes.Structure):
    """`JOBOBJECT_BASIC_LIMIT_INFORMATION`, in portable widths so any 64-bit host lays it out alike.

    `LARGE_INTEGER` is 8 bytes, `SIZE_T` and `ULONG_PTR` are pointer-sized,
    `DWORD` is 4. `wintypes` is not used because its names do not all exist off
    Windows, and the layout is checked by the Linux suite.
    """

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class IoCounters(ctypes.Structure):
    """`IO_COUNTERS`: six ULONGLONGs."""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class ExtendedLimits(ctypes.Structure):
    """`JOBOBJECT_EXTENDED_LIMIT_INFORMATION`: 144 bytes on x64."""

    _fields_ = [
        ("BasicLimitInformation", BasicLimits),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Api(Protocol):
    """The Win32 calls this module makes, one method each: the seam the tests replace."""

    def create_job(self) -> int: ...
    def set_information(self, job: int, info_class: int, info: ExtendedLimits) -> bool: ...
    def open_process(self, access: int, pid: int) -> int: ...
    def assign(self, job: int, process: int) -> bool: ...
    def resume(self, process: int) -> int: ...
    def terminate_job(self, job: int, exit_code: int) -> bool: ...
    def close(self, handle: int) -> bool: ...
    def last_error(self) -> int: ...


class _Native:  # pragma: no cover - Windows only
    """kernel32 and ntdll through ctypes, with every signature declared.

    **A HANDLE is a pointer and ctypes assumes a C `int`** (`selfupdate.layout`
    learned it in round 5): without `restype` the top half of each handle is
    lost. `use_last_error=True` so `last_error()` reads the call's own error,
    not whatever ctypes did since. `getattr` for the Windows-only names, which
    the other two platforms CI type-checks do not have.
    """

    def __init__(self) -> None:
        from ctypes import wintypes

        windll = getattr(ctypes, "WinDLL")  # noqa: B009 - Windows-only attribute
        self._get_last_error = getattr(ctypes, "get_last_error")  # noqa: B009
        k = windll("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        nt = windll("ntdll")
        nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
        nt.NtResumeProcess.restype = wintypes.LONG
        self._k: Any = k
        self._nt: Any = nt

    def create_job(self) -> int:
        return int(self._k.CreateJobObjectW(None, None) or 0)

    def set_information(self, job: int, info_class: int, info: ExtendedLimits) -> bool:
        size = ctypes.sizeof(info)
        return bool(self._k.SetInformationJobObject(job, info_class, ctypes.byref(info), size))

    def open_process(self, access: int, pid: int) -> int:
        return int(self._k.OpenProcess(access, False, pid) or 0)

    def assign(self, job: int, process: int) -> bool:
        return bool(self._k.AssignProcessToJobObject(job, process))

    def resume(self, process: int) -> int:
        return int(self._nt.NtResumeProcess(process))

    def terminate_job(self, job: int, exit_code: int) -> bool:
        return bool(self._k.TerminateJobObject(job, exit_code))

    def close(self, handle: int) -> bool:
        return bool(self._k.CloseHandle(handle))

    def last_error(self) -> int:
        return int(self._get_last_error())


_NATIVE: _Api | None = None
_NATIVE_LOCK = threading.Lock()


def _native() -> _Api:  # pragma: no cover - Windows only; the tests replace it
    """The one `_Native`, bound on first use."""
    global _NATIVE
    with _NATIVE_LOCK:
        if _NATIVE is None:
            _NATIVE = _Native()
        return _NATIVE


class Job:
    """One job, holding one streamed command's tree. Made by `create()`.

    `end()` and `close()` share a lock and `close()` is final: once the handle
    is closed its number can be handed to another job — another stream's — so
    a late Stop must never reach `TerminateJobObject` with it.
    """

    def __init__(self, api: _Api, handle: int) -> None:
        self._api = api
        self._handle = handle
        self._lock = threading.Lock()

    def start(self, pid: int) -> bool:
        """Put the suspended child `pid` in this job, then resume it. True if it joined.

        The child is resumed whether or not it joined: a failed assign costs the
        job, never the command. Raises `OSError` only when the child cannot be
        opened or resumed, since it would then never run; the caller ends it.
        """
        process = self._api.open_process(_START_ACCESS, pid)
        if not process:
            raise OSError(f"could not open pid {pid} (error {self._api.last_error()})")
        try:
            try:
                joined = self._api.assign(self._handle, process)
                if not joined:
                    logger.warning(
                        f"pid {pid} could not join its job (error {self._api.last_error()}); "
                        "a Stop falls back to taskkill"
                    )
            except Exception as exc:  # noqa: BLE001 - the child must be resumed whatever happened
                logger.warning(f"pid {pid} could not join its job: {exc!r}")
                joined = False
            status = self._api.resume(process)
            if status != 0:
                raise OSError(f"could not resume pid {pid} (NTSTATUS {status & 0xFFFFFFFF:#010x})")
            return joined
        finally:
            self._api.close(process)

    def end(self) -> bool:
        """End every process in the job. False, never an exception, if it could not."""
        with self._lock:
            if not self._handle:
                return False
            try:
                ended = self._api.terminate_job(self._handle, _ENDED_EXIT_CODE)
            except Exception as exc:  # noqa: BLE001 - a Stop must reach its fallback
                logger.warning(f"could not end a job: {exc!r}")
                return False
            if not ended:
                logger.warning(f"could not end a job (error {self._api.last_error()})")
            return bool(ended)

    def close(self) -> None:
        """Let the job go, ending whatever is still in it (KILL_ON_JOB_CLOSE). Once only."""
        handle = self._take()
        if handle:
            self._close(handle)

    def release(self) -> None:
        """Let the job go WITHOUT ending what is still in it: for a command that ran out by itself.

        Kill-on-close is cleared before the handle is closed. A command that
        finished normally keeps what it did before jobs existed: anything it
        left running goes on (Codex adversarial review of T299). If the flag
        cannot be cleared the handle is closed anyway, and what is left ends.
        """
        handle = self._take()
        if not handle:
            return
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _BREAKAWAY_OK
            if not self._api.set_information(handle, _EXTENDED_LIMIT_INFORMATION, limits):
                logger.warning(
                    f"could not clear a job's kill-on-close (error {self._api.last_error()})"
                )
        except Exception as exc:  # noqa: BLE001 - called from `finally` blocks
            logger.warning(f"could not clear a job's kill-on-close: {exc!r}")
        self._close(handle)

    def _take(self) -> int:
        """The handle, now nobody else's to use: 0 if the job was already let go."""
        with self._lock:
            handle, self._handle = self._handle, 0
        return handle

    def _close(self, handle: int) -> None:
        try:
            self._api.close(handle)
        except Exception as exc:  # noqa: BLE001 - called from `finally` blocks
            logger.warning(f"could not close a job: {exc!r}")


def create() -> Job | None:
    """A new job that kills on close and lets breakaway through, or None if one cannot be made."""
    try:
        api = _native()
        handle = api.create_job()
        if not handle:
            logger.warning(f"could not create a job (error {api.last_error()})")
            return None
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _BREAKAWAY_OK
        if not api.set_information(handle, _EXTENDED_LIMIT_INFORMATION, limits):
            logger.warning(f"could not set a job's limits (error {api.last_error()})")
            api.close(handle)
            return None
    except Exception as exc:  # noqa: BLE001 - no job is no harm; a failed spawn is
        logger.warning(f"could not create a job: {exc!r}")
        return None
    return Job(api, handle)
