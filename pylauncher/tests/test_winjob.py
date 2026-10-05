"""Tests for `yulon.winjob`: the Windows Job object a Stop ends a whole build with (T299).

Linux CI cannot call kernel32, so the module's logic runs here against `_FakeApi`, which
stands in for the thin ctypes layer (`winjob._Native`) one method per Win32 call. What a
fake cannot prove — the ctypes signatures and the real kernel's answers — is owed to the
Windows live proof. The structure layout is NOT faked: `ctypes` lays it out the same way
on any 64-bit host, so its size and offsets are checked against the Windows x64 ABI here.
"""

from __future__ import annotations

import ctypes

import pytest

from yulon import winjob

_JOB = 0x1A0
_PROC = 0x2B0
_PID = 4242


class _FakeApi:
    """One method per Win32 call `winjob` makes; each call is recorded in `calls`.

    `fails` names the calls that report failure: `create_job` and `open_process`
    return 0, the BOOL calls return False, `resume` returns a non-zero NTSTATUS,
    and a name in `raises` raises instead.
    """

    def __init__(self, *, fails: tuple[str, ...] = (), raises: tuple[str, ...] = ()) -> None:
        self.fails = fails
        self.raises = raises
        self.calls: list[tuple[object, ...]] = []
        self.flags_seen: int | None = None

    def _call(self, name: str, *args: object) -> bool:
        self.calls.append((name, *args))
        if name in self.raises:
            raise OSError(87, f"{name} went wrong")
        return name not in self.fails

    def names(self) -> list[str]:
        return [str(call[0]) for call in self.calls]

    def create_job(self) -> int:
        return _JOB if self._call("create_job") else 0

    def set_information(self, job: int, info_class: int, info: winjob.ExtendedLimits) -> bool:
        self.flags_seen = int(info.BasicLimitInformation.LimitFlags)
        return self._call("set_information", job, info_class)

    def open_process(self, access: int, pid: int) -> int:
        return _PROC if self._call("open_process", access, pid) else 0

    def assign(self, job: int, process: int) -> bool:
        return self._call("assign", job, process)

    def resume(self, process: int) -> int:
        return 0 if self._call("resume", process) else -1073741790  # STATUS_ACCESS_DENIED

    def terminate_job(self, job: int, exit_code: int) -> bool:
        return self._call("terminate_job", job, exit_code)

    def close(self, handle: int) -> bool:
        return self._call("close", handle)

    def last_error(self) -> int:
        return 5


def _with(monkeypatch: pytest.MonkeyPatch, api: _FakeApi) -> _FakeApi:
    monkeypatch.setattr(winjob, "_native", lambda: api)
    return api


# --- the structure ----------------------------------------------------------


@pytest.mark.skipif(
    ctypes.sizeof(ctypes.c_void_p) != 8, reason="the x64 layout needs a 64-bit host"
)
def test_the_limit_structure_has_the_windows_x64_layout() -> None:
    """144 bytes with `LimitFlags` at 16, as `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` has on x64.

    `SetInformationJobObject` is handed this structure's size and reads the
    flags at a fixed offset: a field of the wrong width moves `LimitFlags` and
    the call either fails (ERROR_BAD_LENGTH) or sets limits nobody asked for.

    The widths are checked field by field as well, because alignment padding
    hides a field that is too narrow or too wide where it is followed by an
    8-byte one: a 32-bit `PerProcessUserTimeLimit` or a 64-bit `LimitFlags`
    leaves every offset and the size unchanged (both survived the first
    mutation run).

    Mutations this catches: the IO counters dropped or narrowed, a `SIZE_T`
    spelled as a DWORD (offsets, size), and any field of the wrong width (widths).
    """
    widths = {
        "PerProcessUserTimeLimit": 8,  # LARGE_INTEGER
        "PerJobUserTimeLimit": 8,
        "LimitFlags": 4,  # DWORD
        "MinimumWorkingSetSize": 8,  # SIZE_T
        "MaximumWorkingSetSize": 8,
        "ActiveProcessLimit": 4,
        "Affinity": 8,  # ULONG_PTR
        "PriorityClass": 4,
        "SchedulingClass": 4,
    }
    assert {name: getattr(winjob.BasicLimits, name).size for name in widths} == widths
    assert all(getattr(winjob.IoCounters, name).size == 8 for name, _ in winjob.IoCounters._fields_)
    assert ctypes.sizeof(winjob.IoCounters) == 48
    basic = winjob.ExtendedLimits.BasicLimitInformation
    assert ctypes.sizeof(winjob.ExtendedLimits) == 144
    assert basic.offset == 0
    assert winjob.BasicLimits.LimitFlags.offset == 16
    assert winjob.BasicLimits.Affinity.offset == 48
    assert ctypes.sizeof(winjob.BasicLimits) == 64
    assert winjob.ExtendedLimits.IoInfo.offset == 64
    assert winjob.ExtendedLimits.ProcessMemoryLimit.offset == 112


# --- create ------------------------------------------------------------------


def test_create_makes_a_job_that_kills_on_close_and_lets_breakaway_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KILL_ON_JOB_CLOSE (0x2000) and BREAKAWAY_OK (0x800), set as the extended class (9).

    KILL_ON_JOB_CLOSE is what ends a build whose launcher died or closed the
    handle. BREAKAWAY_OK keeps a child that asks to leave the job able to start
    at all: without it CreateProcess with CREATE_BREAKAWAY_FROM_JOB fails.

    Mutations this catches: either flag dropped; the basic class (2) instead of
    the extended one, which has no KILL_ON_JOB_CLOSE.
    """
    api = _with(monkeypatch, _FakeApi())

    job = winjob.create()

    assert job is not None
    assert api.calls == [("create_job",), ("set_information", _JOB, 9)]
    assert api.flags_seen == 0x2000 | 0x800


def test_create_answers_none_when_no_job_can_be_made(monkeypatch: pytest.MonkeyPatch) -> None:
    """No job is no harm: the caller spawns as before and keeps taskkill for a Stop."""
    api = _with(monkeypatch, _FakeApi(fails=("create_job",)))

    assert winjob.create() is None
    assert api.names() == ["create_job"]


def test_create_closes_a_job_whose_limits_it_could_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job without KILL_ON_JOB_CLOSE is not the job this module promises, so it is let go.

    Mutation this catches: returning the job anyway, or leaking its handle.
    """
    api = _with(monkeypatch, _FakeApi(fails=("set_information",)))

    assert winjob.create() is None
    assert api.names() == ["create_job", "set_information", "close"]
    assert api.calls[-1] == ("close", _JOB)


def test_create_answers_none_when_kernel32_cannot_be_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever goes wrong while binding the calls, a spawn must still happen.

    Mutation this catches: an exception escaping `create()` into `stream()`,
    which would turn every Windows command into a failure.
    """

    def broken() -> winjob._Api:
        raise AttributeError("function 'CreateJobObjectW' not found")

    monkeypatch.setattr(winjob, "_native", broken)

    assert winjob.create() is None


# --- start -------------------------------------------------------------------


def _made(monkeypatch: pytest.MonkeyPatch, **fake: tuple[str, ...]) -> tuple[winjob.Job, _FakeApi]:
    api = _with(monkeypatch, _FakeApi(**fake))
    job = winjob.create()
    assert job is not None
    api.calls.clear()
    return job, api


def test_start_puts_the_suspended_child_in_the_job_and_only_then_resumes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assign, then resume: docker.exe is in the job before it has run one instruction.

    That order is the whole reason for CREATE_SUSPENDED. Resumed first, it could
    start compose before it joined, and compose would be outside the job.

    Mutations this catches: resume before assign; a missing right in the open
    (PROCESS_SET_QUOTA 0x100 and PROCESS_TERMINATE 0x1 for the assign,
    PROCESS_SUSPEND_RESUME 0x800 for the resume); the process handle leaked.
    """
    job, api = _made(monkeypatch)

    assert job.start(_PID) is True

    assert api.calls == [
        ("open_process", 0x0100 | 0x0001 | 0x0800, _PID),
        ("assign", _JOB, _PROC),
        ("resume", _PROC),
        ("close", _PROC),
    ]


def test_start_still_resumes_a_child_it_could_not_put_in_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed assign costs the job, never the command: the child runs, and False says no job.

    Mutation this catches: returning before the resume, which leaves docker.exe
    suspended for ever and the build hung with no output.
    """
    job, api = _made(monkeypatch, fails=("assign",))

    assert job.start(_PID) is False

    assert api.names() == ["open_process", "assign", "resume", "close"]


def test_start_treats_an_assign_that_raises_like_one_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation this catches: an assign exception skipping the resume."""
    job, api = _made(monkeypatch, raises=("assign",))

    assert job.start(_PID) is False

    assert api.names() == ["open_process", "assign", "resume", "close"]


def test_start_raises_when_the_child_cannot_be_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child left suspended is a command that never runs, so the caller must hear of it.

    Mutation this catches: returning normally after a failed resume, which
    hands `stream()` a child that will never print a line or exit.
    """
    job, api = _made(monkeypatch, fails=("resume",))

    with pytest.raises(OSError, match="could not resume"):
        job.start(_PID)

    assert api.names() == ["open_process", "assign", "resume", "close"]


def test_start_raises_when_the_child_cannot_be_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """No handle means neither the assign nor the resume can happen: the child would never run.

    Mutation this catches: answering False ("no job") here, which the caller
    takes as "running, just not in a job" while the child is still suspended.
    """
    job, api = _made(monkeypatch, fails=("open_process",))

    with pytest.raises(OSError, match="could not open"):
        job.start(_PID)

    assert api.names() == ["open_process"]


# --- end and close -----------------------------------------------------------


def test_end_terminates_every_process_in_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit code 1, the code `Popen.terminate()` gives on Windows, so a Stop reads the same."""
    job, api = _made(monkeypatch)

    assert job.end() is True

    assert api.calls == [("terminate_job", _JOB, 1)]


@pytest.mark.parametrize("how", ["fails", "raises"])
def test_end_answers_false_when_the_job_could_not_be_ended(
    monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """False, never an exception: the caller then falls back to taskkill.

    Mutations this catches: answering True regardless (taskkill would never
    run); an exception escaping into `_end_child`, which would lose the Stop.
    """
    job, _ = _made(monkeypatch, **{how: ("terminate_job",)})

    assert job.end() is False


def test_close_closes_the_handle_once_and_a_closed_job_is_never_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After `close()` the handle's number may belong to some other job, even another stream's.

    So `end()` on a closed job must not reach `TerminateJobObject` with it, and
    a second `close()` must not close whatever now holds that number.

    Mutations this catches: `end()` not checking for a closed job; `close()`
    closing twice.
    """
    job, api = _made(monkeypatch)

    job.close()
    job.close()
    ended = job.end()

    assert api.calls == [("close", _JOB)]
    assert ended is False
