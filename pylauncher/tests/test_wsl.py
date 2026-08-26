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
