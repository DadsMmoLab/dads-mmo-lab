"""Finding servers that live inside a WSL2 distro (`pyplan/wsl-resident-servers.md`).

The fixtures here are real output captured from a Windows 11 box with a real
WSL-resident WoW server on it (2026-08-26), not hand-written approximations —
`wsl.exe`'s UTF-16 and the `*` that marks the default distro are exactly the
details a plausible-looking fixture would smooth over.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import wsl

# `wsl -l -q`, decoded from UTF-16LE. Names only: no header, no `*` marking the
# default distro, and no STATE column - which is the part `wsl.exe` translates,
# and the reason this listing is used instead of `-v`.
WSL_LIST_Q = "dml-arch\r\ndocker-desktop\r\n"

# `docker compose ls --all --format json`, run inside `dml-arch`.
COMPOSE_LS = (
    '[{"Name":"wow-server-playerbots","Status":"running(1)",'
    '"ConfigFiles":"/home/dml/games/wow-server-playerbots/docker-compose.yml,'
    '/home/dml/games/wow-server-playerbots/docker-compose.override.yml"}]'
)


# `wsl -d yulon-no-such-distro -- docker ps` on the same box (2026-08-26), as
# `runner.run()` hands it over: text mode, UTF-8, `errors="replace"`. wsl.exe
# writes UTF-16LE, so every ASCII character arrives followed by a NUL - and it
# writes this to STDOUT, with stderr empty, which is why `docker._run()`'s
# message ended at "docker ps exited 4294967295: " and said nothing more.
WSL_NO_SUCH_DISTRO_STDOUT = (
    "T\x00h\x00e\x00r\x00e\x00 \x00i\x00s\x00 \x00n\x00o\x00 \x00"
    "d\x00i\x00s\x00t\x00r\x00i\x00b\x00u\x00t\x00i\x00o\x00n\x00"
    " \x00w\x00i\x00t\x00h\x00 \x00t\x00h\x00e\x00 \x00s\x00u\x00"
    "p\x00p\x00l\x00i\x00e\x00d\x00 \x00n\x00a\x00m\x00e\x00.\x00"
    "\n\x00\n\x00E\x00r\x00r\x00o\x00r\x00 \x00c\x00o\x00d\x00e\x00"
    ":\x00 \x00W\x00s\x00l\x00/\x00S\x00e\x00r\x00v\x00i\x00c\x00"
    "e\x00/\x00W\x00S\x00L\x00_\x00E\x00_\x00D\x00I\x00S\x00T\x00"
    "R\x00O\x00_\x00N\x00O\x00T\x00_\x00F\x00O\x00U\x00N\x00D\x00"
    "\n\x00\n\x00"
)

WSL_NO_SUCH_DISTRO_RETURNCODE = 4294967295
"""0xFFFFFFFF, captured from the same run. Windows' unsigned DWORD exit status."""


def test_distro_names_come_from_a_listing_no_locale_translates() -> None:
    """`wsl -l -v`'s STATE column is TRANSLATED, and reading it broke everything.

    On German Windows a running distro reads "Wird ausgeführt", so a
    `state == "running"` test is False for every distro: all of them look
    stopped, and discovery - which refuses to probe stopped distros - finds
    nothing at all on a machine where everything works. Reproduced before this
    changed. `-q` prints names only, which no locale rewrites.
    """
    assert wsl.parse_distro_names(WSL_LIST_Q) == ("dml-arch", "docker-desktop")
    assert wsl.parse_distro_names("") == ()


def test_distro_states_pairs_the_two_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running-ness comes from `--running` naming it, not from parsing a word."""
    monkeypatch.setattr(
        wsl,
        "_wsl_list",
        lambda *args: ("dml-arch",) if args else ("dml-arch", "docker-desktop"),
    )
    assert wsl.distro_states() == (
        wsl.Distro(name="dml-arch", running=True),
        wsl.Distro(name="docker-desktop", running=False),
    )


def test_a_deleted_distro_is_named_instead_of_wsls_bare_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user whose distro is gone was shown "docker ps exited 4294967295: ".

    Nothing after the colon, because wsl.exe writes its complaint to stdout and
    the message quotes stderr - so the number was the entire explanation, and it
    named neither the distro nor anything to do about it. The refusal has to say
    which distro is missing and that the tab cannot reach it until it is back or
    the server is re-adopted.

    `distro_states` is made fatal here to prove the error code alone settles it:
    recognising this must not spawn `wsl -l -q` on a path that has already
    failed once.
    """

    def no_listing() -> tuple[wsl.Distro, ...]:
        raise AssertionError("the listing was asked for a failure wsl.exe already explained")

    monkeypatch.setattr(wsl, "distro_states", no_listing)

    problem = wsl.missing_distro_problem(
        "dml-arch", WSL_NO_SUCH_DISTRO_RETURNCODE, WSL_NO_SUCH_DISTRO_STDOUT
    )
    assert problem is not None
    assert "dml-arch" in problem
    assert "4294967295" not in problem
    assert "Use existing" in problem


def test_a_failure_wsl_did_not_spell_out_is_settled_by_the_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The streamed seams may hold no output carrying the error code.

    `follow_logs()` yields lines and `run_attached()` keeps only a bounded tail,
    so the code can be off the end of what the caller still has. The listing is
    authoritative about which distros exist, and reading it starts nothing.
    """
    monkeypatch.setattr(wsl, "distro_states", lambda: (wsl.Distro("docker-desktop", False),))
    problem = wsl.missing_distro_problem("dml-arch", WSL_NO_SUCH_DISTRO_RETURNCODE)
    assert problem is not None
    assert "dml-arch" in problem


def test_a_distro_that_still_exists_is_never_reported_as_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wsl.exe exits 4294967295 for more than a missing name.

    A stopped or wedged distro fails the same way, and telling that user their
    distro was deleted sends them off to re-adopt a server sitting exactly where
    they left it. The listing still naming it is the veto.
    """
    monkeypatch.setattr(wsl, "distro_states", lambda: (wsl.Distro("dml-arch", False),))
    assert wsl.missing_distro_problem("dml-arch", WSL_NO_SUCH_DISTRO_RETURNCODE) is None


def test_a_cancelled_run_is_not_mistaken_for_a_deleted_distro() -> None:
    """`docker.CANCELLED_RETURNCODE` is -1, which is 4294967295 written signed.

    A user cancelling their own build would otherwise be told their distro had
    been deleted. Only the unsigned value Windows actually reports counts.
    """
    assert wsl.missing_distro_problem("dml-arch", -1, WSL_NO_SUCH_DISTRO_STDOUT) is None


def test_an_ordinary_docker_failure_keeps_its_own_message() -> None:
    """Compose failing inside a healthy distro must not be blamed on WSL.

    Its exit code and its own stderr are the explanation the user needs; a WSL
    refusal pasted over the top would hide the port clash or the bad image.
    """
    assert (
        wsl.missing_distro_problem("dml-arch", 1, "Error response from daemon: no such image")
        is None
    )


def test_a_plain_windows_install_has_no_distro_to_blame() -> None:
    """Docker Desktop installs pass `wsl_distro=None` through every docker seam.

    Answering None here is what lets those seams ask unconditionally, in one
    line, rather than each guarding first - which is how this stays out of
    `docker.py`'s 33 functions.
    """
    assert wsl.missing_distro_problem(None, WSL_NO_SUCH_DISTRO_RETURNCODE) is None


def test_a_windows_folder_mounted_into_a_distro_is_not_a_wsl_server() -> None:
    """Docker Desktop's integration distros surface the user's Windows projects.

    A project whose compose file is at `/mnt/c/...` is a Windows folder reached
    through the distro, not a server living in it. Adopting one would hand back
    a UNC path back into `mnt/c/Users/user/proj` - a local folder taken the long
    way round, then managed through the wrong daemon. "Use existing…" adopts
    those as themselves.
    """
    windows_project = (
        '[{"Name":"myapp","Status":"running(1)",'
        '"ConfigFiles":"/mnt/c/Users/user/proj/docker-compose.yml"}]'
    )
    assert wsl.parse_compose_ls("Ubuntu", windows_project) == ()


def test_found_servers_carry_a_windows_path_the_rest_of_the_app_can_read() -> None:
    """Discovery's output has to be usable as a `server_dir`.

    Docker answers in the distro's own spelling (`/home/dml/...`). Everything
    Windows-side — the compose-file check, the folder rule, the controller tab's
    label — needs the UNC form, so the conversion happens here rather than being
    left for each consumer to remember.
    """
    found = wsl.parse_compose_ls("dml-arch", COMPOSE_LS)
    assert len(found) == 1
    server = found[0]
    assert server.distro == "dml-arch"
    assert server.project == "wow-server-playerbots"
    assert server.running is True
    assert server.server_dir == Path(
        r"\\wsl.localhost\dml-arch\home\dml\games\wow-server-playerbots"
    )


def test_a_stopped_project_is_found_and_reported_as_stopped() -> None:
    """A server that is merely off is still a server worth adopting."""
    stopped = COMPOSE_LS.replace('"running(1)"', '"exited(3)"')
    server = wsl.parse_compose_ls("dml-arch", stopped)[0]
    assert server.running is False
    assert server.project == "wow-server-playerbots"


def test_compose_output_that_is_not_json_is_no_servers_rather_than_a_crash() -> None:
    """An older compose, or an error on stdout, must not take the dialog down."""
    assert wsl.parse_compose_ls("dml-arch", "") == ()
    assert wsl.parse_compose_ls("dml-arch", "not json at all") == ()
    assert wsl.parse_compose_ls("dml-arch", "[]") == ()


def test_a_project_with_no_config_files_is_skipped() -> None:
    """Without a path there is nothing to adopt, and no folder to show the user."""
    empty = COMPOSE_LS.replace(
        '"ConfigFiles":"/home/dml/games/wow-server-playerbots/docker-compose.yml,'
        '/home/dml/games/wow-server-playerbots/docker-compose.override.yml"',
        '"ConfigFiles":""',
    )
    assert wsl.parse_compose_ls("dml-arch", empty) == ()


def test_find_servers_never_probes_a_stopped_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probing a distro STARTS it, and booting someone's distros is not a side
    effect a dialog gets to have.

    Measured 2026-08-26: `wsl -d docker-desktop -- true` flipped that distro
    from Stopped to Running. So a scan of everything would boot everything —
    slow, and nobody asked for it. Asserted through the run seam, so it fails if
    anything shells into a stopped distro at all.
    """
    asked: list[str] = []

    def fake_probe(distro: str) -> str:
        asked.append(distro)
        return COMPOSE_LS

    monkeypatch.setattr(wsl, "_compose_ls", fake_probe)
    monkeypatch.setattr(
        wsl,
        "distro_states",
        lambda: (wsl.Distro("dml-arch", True), wsl.Distro("docker-desktop", False)),
    )

    found = wsl.find_servers()
    assert asked == ["dml-arch"], f"a stopped distro was started: {asked}"
    assert [s.distro for s in found] == ["dml-arch"]


def test_find_servers_probes_a_stopped_distro_only_when_asked_for_it_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opt-in: a user who knows their server is in a stopped distro can say so.

    Naming it is the consent — the caller has already told the user that
    checking will start it.
    """
    asked: list[str] = []
    monkeypatch.setattr(wsl, "_compose_ls", lambda d: asked.append(d) or COMPOSE_LS)  # type: ignore[func-returns-value]
    monkeypatch.setattr(
        wsl,
        "distro_states",
        lambda: (wsl.Distro("dml-arch", True), wsl.Distro("docker-desktop", False)),
    )

    wsl.find_servers(include=("docker-desktop",))
    assert asked == ["dml-arch", "docker-desktop"]


def test_find_servers_survives_one_distro_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """One broken distro must not hide the servers in the others."""

    def flaky(distro: str) -> str:
        if distro == "broken":
            raise OSError("wsl.exe fell over")
        return COMPOSE_LS

    monkeypatch.setattr(wsl, "_compose_ls", flaky)
    monkeypatch.setattr(
        wsl,
        "distro_states",
        lambda: (wsl.Distro("broken", True), wsl.Distro("dml-arch", True)),
    )
    assert [s.distro for s in wsl.find_servers()] == ["dml-arch"]


def test_a_listing_that_could_not_answer_never_accuses_a_distro_of_being_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty listing is not the same fact as an empty machine.

    `_wsl_list()` answers `()` for four different things - no wsl.exe on PATH,
    OSError, a timeout, and a non-zero exit - and only one of them means "there
    are no distros". The failure that reaches tier 2 is a 0xFFFFFFFF carrying no
    WSL_E_DISTRO_NOT_FOUND, which is WSL broken at the SERVICE level; that is
    exactly the state in which `wsl -l -q` also fails and answers `()`.

    So the first version told the user their distro had been deleted whenever
    WSL itself was wedged, and sent them off to re-adopt a server that was never
    missing - in precisely the case tier 2 exists to judge.
    """
    monkeypatch.setattr(wsl, "distro_states", lambda: ())
    assert (
        wsl.missing_distro_problem("dml-arch", WSL_NO_SUCH_DISTRO_RETURNCODE, "terminated") is None
    )
    # And the tier-1 path is unaffected: wsl.exe naming the code is proof on its
    # own and never consults the listing.
    said = wsl.missing_distro_problem(
        "dml-arch", WSL_NO_SUCH_DISTRO_RETURNCODE, WSL_NO_SUCH_DISTRO_STDOUT
    )
    assert said is not None and "dml-arch" in said


# -- T132: holding a distro open while its server runs --------------------------------
#
# Measured on yulon-win11 (WSL 2.7.12, 2026-09-26), `.notes/gates/t132-*`: a distro
# stops 15-25 s after the last wsl.exe session exits -- with systemd, dockerd and a
# running container inside it, and with or without Docker Desktop -- while one
# held `wsl.exe -d <distro> -- sleep infinity` kept it up indefinitely.


class _FakeProc:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class _FakePopen:
    def __init__(self, returncode: int | None = None, refuse_breakaway: bool = False) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.returncode = returncode
        self.refuse_breakaway = refuse_breakaway

    def __call__(self, argv: list[str], **kwargs: object) -> _FakeProc:
        self.calls.append((argv, kwargs))
        flags = int(kwargs.get("creationflags", 0))  # type: ignore[call-overload]
        if self.refuse_breakaway and flags & wsl.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")
        return _FakeProc(self.returncode)


@pytest.fixture
def _wsl_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl.platform, "_which", lambda name: "C:/Windows/System32/wsl.exe")


def _hold(popen: _FakePopen, key: str = "k") -> wsl.Hold:
    return wsl.hold("Ubuntu", key, popen=popen, sleep=lambda s: None)


def test_hold_runs_one_flocked_sleep_in_the_distro_without_a_shell_in_between(
    _wsl_exe: None,
) -> None:
    """`--exec`, so no login shell re-reads the script and expands `$$` for itself."""
    popen = _FakePopen(returncode=None)
    made = _hold(popen, "vanilla-mangosd")
    assert made.held is True and made.alive() is True
    [(argv, kwargs)] = popen.calls
    assert argv[:6] == ["C:/Windows/System32/wsl.exe", "-d", "Ubuntu", "--exec", "sh", "-c"]
    script = argv[6]
    assert f"flock -n -E {wsl.HOLD_ALREADY_HELD}" in script
    assert "yulon-hold-vanilla-mangosd.lock" in script
    assert "yulon-hold-vanilla-mangosd.pid" in script
    assert "exec sleep infinity" in script
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_the_hold_files_live_in_a_directory_of_the_users_own(_wsl_exe: None) -> None:
    """World-writable `/dev/shm` itself would let another account plant a pid."""
    text = wsl.hold_script("k")
    assert "/dev/shm/yulon-$(id -u)" in text
    assert "mkdir -p -m 700" in text
    assert '[ ! -L "$d" ] && [ -O "$d" ]' in text


def test_the_hold_is_spawned_to_outlive_the_app(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its own process group, no console, and out of any job the app runs in.

    A job with kill-on-close would take the hold down with the app, which is the
    moment it exists for.
    """
    monkeypatch.setattr(wsl.sys, "platform", "win32")
    popen = _FakePopen(returncode=None)
    _hold(popen)
    flags = int(popen.calls[0][1]["creationflags"])  # type: ignore[call-overload]
    for flag in (
        wsl.CREATE_NO_WINDOW,
        wsl.CREATE_NEW_PROCESS_GROUP,
        wsl.CREATE_BREAKAWAY_FROM_JOB,
    ):
        assert flags & flag, hex(flag)


def test_a_job_that_refuses_breakaway_still_gets_a_hold(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wsl.sys, "platform", "win32")
    popen = _FakePopen(returncode=None, refuse_breakaway=True)
    assert _hold(popen).held is True
    assert len(popen.calls) == 2
    assert not int(popen.calls[1][1]["creationflags"]) & wsl.CREATE_BREAKAWAY_FROM_JOB  # type: ignore[call-overload]


def test_a_hold_that_is_already_there_counts_as_held(_wsl_exe: None) -> None:
    """flock's conflict code: a second hold must not stack a second session."""
    made = _hold(_FakePopen(returncode=wsl.HOLD_ALREADY_HELD))
    assert made.held is True
    assert made.alive() is True, "a hold another launch made cannot be watched from here"


def test_a_hold_that_died_at_once_is_reported_not_trusted(
    _wsl_exe: None, caplog: pytest.LogCaptureFixture
) -> None:
    """No flock in the distro, say: the server still runs, and the log says why it may stop."""
    made = _hold(_FakePopen(returncode=127))
    assert made.held is False and made.alive() is False
    assert "127" in caplog.text


def test_a_hold_that_dies_later_is_seen_to_be_gone(_wsl_exe: None) -> None:
    popen = _FakePopen(returncode=None)
    made = _hold(popen)
    assert made.alive() is True
    made.proc.returncode = 1
    assert made.alive() is False


def test_no_wsl_exe_means_no_hold_and_no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl.platform, "_which", lambda name: None)
    popen = _FakePopen()
    assert _hold(popen).held is False
    assert popen.calls == []


def test_the_hold_key_cannot_reach_the_script_as_anything_but_a_file_name(_wsl_exe: None) -> None:
    popen = _FakePopen(returncode=None)
    _hold(popen, "a b;rm -rf /$(x)'")
    script = popen.calls[0][0][6]
    assert "yulon-hold-a_b_rm_-rf____x__.lock" in script
    assert ";rm" not in script and "$(x)" not in script


def test_release_never_boots_a_stopped_distro(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stopped distro holds nothing, and `wsl -d` would start it (§2)."""
    ran: list[list[str]] = []
    monkeypatch.setattr(wsl, "known_stopped", lambda distro: True)
    assert wsl.release("Ubuntu", "k", run=lambda argv: ran.append(argv)) is True
    assert ran == []


def test_release_ends_every_named_hold_in_one_call(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(wsl, "known_stopped", lambda distro: False)
    wsl.release(
        "Ubuntu",
        "vanilla-mangosd",
        "vanilla-db",
        run=lambda argv: ran.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    [argv] = ran
    assert argv[:6] == ["C:/Windows/System32/wsl.exe", "-d", "Ubuntu", "--exec", "sh", "-c"]
    script = argv[6]
    assert "yulon-hold-vanilla-mangosd yulon-hold-vanilla-db" in script
    assert "/proc/$1/comm" in script and "/proc/$1/stat" in script and "kill" in script


def test_release_with_no_keys_asks_nothing(_wsl_exe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(wsl, "known_stopped", lambda distro: False)
    wsl.release("Ubuntu", run=lambda argv: ran.append(argv))
    assert ran == []


def test_the_suite_refuses_a_real_hold_or_release(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conftest guard's own guard: the defaults really are refused in a test."""
    with pytest.raises(pytest.fail.Exception, match="real WSL hold"):
        wsl.hold("Ubuntu", "k", sleep=lambda s: None)
    monkeypatch.setattr(wsl, "known_stopped", lambda distro: False)
    with pytest.raises(pytest.fail.Exception, match="real WSL hold"):
        wsl.release("Ubuntu", "k")


# The scripts, run by a real `sh` (dash on Ubuntu) and `flock` with the directory moved
# into tmp_path: the argv tests above check spelling, these check that it does what it says.


def _real_shell(tmp_path: Path) -> Path:
    import shutil

    if shutil.which("flock") is None or shutil.which("sh") is None:
        pytest.skip("needs sh and flock (util-linux)")
    return tmp_path / "own"


def _local(script: str, own: Path) -> str:
    return script.replace(wsl.HOLD_DIR, str(own))


def _wait_for(path: Path) -> None:
    import time

    for _ in range(100):
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def test_the_hold_script_really_runs_and_release_really_ends_it(tmp_path: Path) -> None:
    own = _real_shell(tmp_path)
    first = subprocess.Popen(["sh", "-c", _local(wsl.hold_script("k"), own)])
    try:
        _wait_for(own / "yulon-hold-k.pid")
        assert oct(own.stat().st_mode & 0o777) == "0o700"
        second = subprocess.run(["sh", "-c", _local(wsl.hold_script("k"), own)], timeout=10)
        assert second.returncode == wsl.HOLD_ALREADY_HELD
        assert first.poll() is None
        subprocess.run(["sh", "-c", _local(wsl.release_script("k"), own)], timeout=10)
        assert first.wait(timeout=10) is not None
        assert not (own / "yulon-hold-k.pid").exists()
    finally:
        if first.poll() is None:
            first.kill()


def test_release_leaves_an_unrelated_sleep_on_a_reused_pid_alone(tmp_path: Path) -> None:
    """A pid file naming somebody else's `sleep` -- a recycled number -- kills nothing.

    Same pid and same `comm` as a hold would have; only the start time differs,
    which is what the recorded start time is there to catch (review, Codex).
    """
    own = _real_shell(tmp_path)
    own.mkdir(mode=0o700)
    stranger = subprocess.Popen(["sleep", "30"])
    try:
        (own / "yulon-hold-k.pid").write_text(f"{stranger.pid} 1\n")
        subprocess.run(["sh", "-c", _local(wsl.release_script("k"), own)], timeout=10)
        with pytest.raises(subprocess.TimeoutExpired):
            stranger.wait(timeout=1)  # a kill that landed would end it well inside this
    finally:
        stranger.kill()


def test_a_hold_directory_that_is_not_the_users_own_is_refused(tmp_path: Path) -> None:
    """A symlink planted where the private directory goes: nothing is written through it."""
    own = _real_shell(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    own.symlink_to(elsewhere)
    held = subprocess.run(["sh", "-c", _local(wsl.hold_script("k"), own)], timeout=10)
    assert held.returncode == wsl.HOLD_DIR_REFUSED
    released = subprocess.run(["sh", "-c", _local(wsl.release_script("k"), own)], timeout=10)
    assert released.returncode == wsl.HOLD_DIR_REFUSED
    assert list(elsewhere.iterdir()) == []


def test_a_pre_existing_hold_directory_open_to_others_is_closed_before_use(tmp_path: Path) -> None:
    """Same owner, but mode 0777: another account could plant a pid in it (Codex, final pass).

    `mkdir -p -m 700` leaves an existing directory's mode alone, so the scripts
    must close it themselves -- and refuse if they cannot -- before trusting it.
    """
    own = _real_shell(tmp_path)
    own.mkdir()
    own.chmod(0o777)
    first = subprocess.Popen(["sh", "-c", _local(wsl.hold_script("k"), own)])
    try:
        _wait_for(own / "yulon-hold-k.pid")
        assert oct(own.stat().st_mode & 0o777) == "0o700"
    finally:
        first.kill()
    own.chmod(0o777)
    subprocess.run(["sh", "-c", _local(wsl.release_script("k"), own)], timeout=10)
    assert oct(own.stat().st_mode & 0o777) == "0o700"


def test_a_release_the_distro_refused_is_a_warning_and_false(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The script's own exit code is the answer, not the absence of an exception."""
    monkeypatch.setattr(wsl, "known_stopped", lambda distro: False)

    def refused(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, wsl.HOLD_DIR_REFUSED, "", "not ours")

    with caplog.at_level("INFO", logger=wsl.logger.name):
        assert wsl.release("Ubuntu", "k", run=refused) is False
    assert "released the hold" not in caplog.text
    assert any(r.levelname == "WARNING" and "not ours" in r.getMessage() for r in caplog.records)


def test_a_release_that_ran_cleanly_is_true(
    _wsl_exe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wsl, "known_stopped", lambda distro: False)
    done = wsl.release("Ubuntu", "k", run=lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    assert done is True


def test_a_release_that_ended_its_sleep_exits_zero_and_drops_the_record(tmp_path: Path) -> None:
    """The success half of the exit code `release()` now reads: a matching record, killed, gone."""
    own = _real_shell(tmp_path)
    own.mkdir(mode=0o700)
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        start = (Path(f"/proc/{sleeper.pid}/stat").read_text().rsplit(")", 1)[1].split())[19]
        (own / "yulon-hold-k.pid").write_text(f"{sleeper.pid} {start}\n")
        done = subprocess.run(["sh", "-c", _local(wsl.release_script("k"), own)], timeout=10)
        assert done.returncode == 0
        assert sleeper.wait(timeout=5) is not None
        assert not (own / "yulon-hold-k.pid").exists()
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
