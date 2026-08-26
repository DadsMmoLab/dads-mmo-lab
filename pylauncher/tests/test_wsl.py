"""Finding servers that live inside a WSL2 distro (`pyplan/wsl-resident-servers.md`).

The fixtures here are real output captured from a Windows 11 box with a real
WSL-resident WoW server on it (2026-08-26), not hand-written approximations —
`wsl.exe`'s UTF-16 and the `*` that marks the default distro are exactly the
details a plausible-looking fixture would smooth over.
"""

from __future__ import annotations

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
    a UNC path back into `mnt/c/Users/pk/proj` - a local folder taken the long
    way round, then managed through the wrong daemon. "Use existing…" adopts
    those as themselves.
    """
    windows_project = (
        '[{"Name":"myapp","Status":"running(1)",'
        '"ConfigFiles":"/mnt/c/Users/pk/proj/docker-compose.yml"}]'
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
