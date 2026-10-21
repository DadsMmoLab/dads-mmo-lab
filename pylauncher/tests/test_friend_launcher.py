"""Tests for the friend-server launcher (`yulon.ui.friend_launcher`, T807).

No server is installed and nothing is modal: every question is answered by a
seam before it is asked, every job runs `run_inline`, the launch goes to a
recorder, and the only disk touched is `tmp_path`. Nothing here reads the host.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yulon import play_client, play_launch, ui_settings
from yulon.catalog.catalog import Catalog, load_catalog
from yulon.ui import friend_launcher
from yulon.ui.friend_launcher import (
    FriendLauncher,
    friend_server_dir,
    parse_realm,
)
from yulon.ui.widgets.job import run_inline

CATALOG: Catalog = load_catalog()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        return None


_SAYS = _Recorder()
"""Every notice and warning the window would raise, recorded instead of modal."""


@pytest.fixture(autouse=True)
def _app(qapp: object) -> None:
    """Every test here builds widgets: the session's offscreen QApplication first."""


@pytest.fixture(autouse=True)
def _no_modals(monkeypatch: pytest.MonkeyPatch) -> None:
    _SAYS.calls.clear()
    monkeypatch.setattr(friend_launcher, "show_warning", lambda *a, **k: _SAYS("warning", a))
    monkeypatch.setattr(friend_launcher, "show_information", lambda *a, **k: _SAYS("info", a))


def _client(tmp_path: Path) -> Path:
    """A minimal real WoW client: Wow.exe, a Data folder and one game archive in it."""
    client = tmp_path / "WoW"
    data = client / "Data"
    data.mkdir(parents=True)
    (data / "patch.mpq").write_bytes(b"MPQ" * 1000)
    (client / "Wow.exe").write_bytes(b"MZ")
    return client


def _window(
    tmp_path: Path,
    *,
    picked: list[Path] | None = None,
    answers: list[bool] | None = None,
    launched: _Recorder | None = None,
    catalog: Catalog = CATALOG,
    os_name: str = "windows",
    job_runner: Any = run_inline,
) -> FriendLauncher:
    pick = picked if picked is not None else []
    answer = answers if answers is not None else []
    return FriendLauncher(
        catalog,
        os_name=lambda: os_name,
        home=tmp_path,
        config_dir=tmp_path / "config",
        pick_dir=lambda _parent, _title, _start: pick.pop(0) if pick else None,
        ask_yes=lambda _parent, _title, _text: answer.pop(0) if answer else False,
        launch=launched if launched is not None else _Recorder(),
        which=lambda _name: None,
        env={},
        job_runner=job_runner,
        settings_path=tmp_path / "ui.json",
    )


def _type(window: FriendLauncher, tmp_path: Path, *, realm: str = "192.168.50.49") -> Path:
    """Type the entry in, and hand back the client folder it names (already on disk)."""
    client = _client(tmp_path)
    window.name_field.setText("Mark's server")
    window.realm_field.setText(realm)
    window.client_field.setText(str(client))
    return client


# -- what the picker offers ------------------------------------------------------


def test_only_games_whose_client_is_complete_without_a_server_are_offered(
    tmp_path: Path,
) -> None:
    """WotLK, TBC, Vanilla and Tortoise are pure copies; Centurion and Unbound are not.

    Their packs and Wow.exe patch are fetched and laid by the server's own tab,
    keyed by the server folder — a friend launcher has no server folder, so a
    client made here could never receive them.
    """
    window = _window(tmp_path)
    offered = [window.game_pick.itemData(i) for i in range(window.game_pick.count())]
    assert set(offered) == {"wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"}, offered


# -- the typed realm --------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("192.168.50.49", ("192.168.50.49", 3724)),
        ("my-friend.example.com:3725", ("my-friend.example.com", 3725)),
        ("wow.example.com", ("wow.example.com", 3724)),
        (" 10.0.0.2 ", ("10.0.0.2", 3724)),
    ],
)
def test_a_typed_realm_address_parses(typed: str, expected: tuple[str, int]) -> None:
    assert parse_realm(typed) == expected


REFUSED_REALMS = (
    "",
    "   ",
    "some host",  # whitespace
    "host:",  # empty port
    "host:abc",  # not a number
    "host:0",  # below the range
    "host:70000",  # above the range
    "host:37 24",  # whitespace in the port half
    "::1",  # IPv6: the realmlist line reaches a v4 auth server only
    "192.168.1.1; rm -rf x",  # never a command line
)


@pytest.mark.parametrize("typed", REFUSED_REALMS)
def test_a_realm_address_the_game_cannot_reach_is_refused(typed: str) -> None:
    assert parse_realm(typed) is None, typed


def test_a_bad_realm_makes_nothing(tmp_path: Path) -> None:
    window = _window(tmp_path)
    _type(window, tmp_path, realm="not a realm")
    window.make_client()
    assert ui_settings.friend_servers(tmp_path / "ui.json") == ()
    assert not (tmp_path / "friend-servers").exists(), "no client was made for a bad realm"
    assert "Nothing was made" in window.status.text()


# -- make ------------------------------------------------------------------------


def test_make_client_builds_a_marked_client_and_writes_the_typed_realm(tmp_path: Path) -> None:
    window = _window(tmp_path)
    original = _type(window, tmp_path)
    window.make_client()

    play = friend_server_dir("Mark's server")  # the SYNTHETIC identity, never a real folder
    saved = ui_settings.friend_servers(tmp_path / "ui.json")
    assert len(saved) == 1 and saved[0].name == "Mark's server"
    record = saved[0]
    assert record.realm == "192.168.50.49"  # the standard port is left off the saved line
    assert record.client_folder == original
    target = record.play_folder
    marker = play_client.read_marker(target)
    assert marker is not None
    assert marker.game == "wow-wotlk" and marker.server_dir == play
    assert marker.source_client_dir == original
    realm_line = (target / "Data" / "enUS" / "realmlist.wtf").read_text(encoding="utf-8")
    assert "set realmlist 192.168.50.49" in realm_line
    assert "The ready-to-play client is at" in window.status.text()


def test_make_client_keeps_the_players_own_client_intact(tmp_path: Path) -> None:
    window = _window(tmp_path)
    original = _type(window, tmp_path)
    window.make_client()
    assert (original / "Wow.exe").read_bytes() == b"MZ"
    assert (original / "Data" / "patch.mpq").is_file()


def test_a_full_copy_is_only_made_once_the_player_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Server tab's rule: nothing big is written before the size is agreed to."""
    window = _window(tmp_path, answers=[False])
    _type(window, tmp_path)
    # Another drive: a stub plan that says the copy cannot share.
    monkeypatch.setattr(
        friend_launcher,
        "_plan_work",
        lambda original, target, *, name, entry, realm: friend_launcher._Plan(
            original,
            target,
            play_client.BuildPlan(
                linked=(),
                copied=(),
                shared_bytes=17 * 1024**3,
                own_bytes=2 * 1024**3,
                same_volume=False,
                skipped_links=(),
            ),
            name=name,
            entry=entry,
            realm=realm,
        ),
    )
    created = _Recorder()
    monkeypatch.setattr(friend_launcher.play_client, "create", created)
    window.make_client()

    assert created.calls == [], "a full copy must not be built before the player agrees"
    assert "you said no" in window.status.text()
    assert ui_settings.friend_servers(tmp_path / "ui.json") == ()


def test_the_agreed_full_copy_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _window(tmp_path, answers=[True])
    _type(window, tmp_path)
    monkeypatch.setattr(
        friend_launcher,
        "_plan_work",
        lambda original, target, *, name, entry, realm: friend_launcher._Plan(
            original,
            target,
            play_client.BuildPlan(
                linked=(),
                copied=(),
                shared_bytes=17 * 1024**3,
                own_bytes=2 * 1024**3,
                same_volume=False,
                skipped_links=(),
            ),
            name=name,
            entry=entry,
            realm=realm,
        ),
    )
    created = _Recorder()
    monkeypatch.setattr(friend_launcher.play_client, "create", created)
    window.make_client()

    ((args, kwargs),) = created.calls
    assert kwargs["allow_full_copy"] is True
    assert kwargs["server_dir"] == friend_server_dir("Mark's server")
    assert ui_settings.friend_servers(tmp_path / "ui.json"), "the entry is saved once agreed"


def test_make_client_over_an_entry_that_already_has_its_client_is_refused(
    tmp_path: Path,
) -> None:
    """`create()` refuses the folder it already made; the client is left as it was."""
    window = _window(tmp_path)
    _type(window, tmp_path)
    window.make_client()
    first = ui_settings.friend_servers(tmp_path / "ui.json")[0].play_folder
    marker_text = (first / play_client.MARKER).read_text(encoding="utf-8")

    window.make_client()  # the plan passes; create refuses the intact folder

    assert (first / play_client.MARKER).read_text(encoding="utf-8") == marker_text
    assert _SAYS.calls, "the refusal was said, not swallowed"


def test_one_job_at_a_time(tmp_path: Path) -> None:
    window = _window(tmp_path)
    window._pending = ("play", "Mark's server")
    launched = _Recorder()
    window._launch = launched
    window.make_client()
    window.play_selected()
    assert launched.calls == []


# -- play -------------------------------------------------------------------------


def _saved_window(tmp_path: Path, *, launched: _Recorder | None = None) -> FriendLauncher:
    window = _window(tmp_path, launched=launched)
    _type(window, tmp_path)
    window.make_client()
    window.saved_list.setCurrentRow(0)
    return window


def test_play_starts_the_saved_client_against_the_typed_realm(tmp_path: Path) -> None:
    launched = _Recorder()
    window = _saved_window(tmp_path, launched=launched)
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]

    window.play_selected()

    assert len(launched.calls) == 1
    (spec,), _kwargs = launched.calls[0]
    assert isinstance(spec, play_launch.LaunchSpec)
    assert spec.argv[0].endswith("Wow.exe"), spec.argv
    assert spec.cwd == server.play_folder
    realm_line = (server.play_folder / "Data" / "enUS" / "realmlist.wtf").read_text(
        encoding="utf-8"
    )
    assert "set realmlist 192.168.50.49" in realm_line


def test_play_keeps_a_typed_port_on_the_realm_line(tmp_path: Path) -> None:
    launched = _Recorder()
    window = _window(tmp_path, launched=launched)
    _type(window, tmp_path, realm="192.168.50.49:3725")
    window.make_client()
    window.saved_list.setCurrentRow(0)
    window.play_selected()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    realm_line = (server.play_folder / "Data" / "enUS" / "realmlist.wtf").read_text(
        encoding="utf-8"
    )
    assert "set realmlist 192.168.50.49:3725" in realm_line


def test_play_refuses_a_folder_that_is_not_this_ones(tmp_path: Path) -> None:
    """The marker is the proof: a stranger in the play folder is not started."""
    launched = _Recorder()
    window = _saved_window(tmp_path, launched=launched)
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    (server.play_folder / play_client.MARKER).unlink()

    window.play_selected()

    assert launched.calls == []
    assert "not this friend server's" in window.status.text()


def test_play_of_a_gone_client_says_so_and_starts_nothing(tmp_path: Path) -> None:
    launched = _Recorder()
    window = _saved_window(tmp_path, launched=launched)
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    import shutil as _shutil

    _shutil.rmtree(server.play_folder)

    window.play_selected()

    assert launched.calls == []
    assert "not this friend server's" in window.status.text()


# -- remove ------------------------------------------------------------------------


def test_remove_deletes_the_made_client_and_never_the_players_own(
    tmp_path: Path,
) -> None:
    window = _window(tmp_path, answers=[True])
    _type(window, tmp_path)
    window.make_client()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    original = server.client_folder

    window.remove_selected()

    assert not server.play_folder.exists(), "the ready-to-play client went"
    assert (original / "Wow.exe").read_bytes() == b"MZ", "the player's client did not"
    assert (original / "Data" / "patch.mpq").is_file(), "the player's client did not"
    assert ui_settings.friend_servers(tmp_path / "ui.json") == (), "the entry is forgotten"


def test_remove_leaves_a_folder_that_is_not_ours_alone(tmp_path: Path) -> None:
    """An unmarked folder in the play folder's place is refused, and the entry survives."""
    window = _window(tmp_path, answers=[True])
    _type(window, tmp_path)
    window.make_client()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    (server.play_folder / play_client.MARKER).unlink()
    said = (server.play_folder / "mine.txt").write_text("keep me", encoding="utf-8")
    assert said

    window.remove_selected()

    assert (server.play_folder / "mine.txt").is_file(), "the folder was not Yu'lon's to delete"
    assert ui_settings.friend_servers(tmp_path / "ui.json"), "the entry was not forgotten either"


def test_remove_without_the_players_yes_deletes_nothing(tmp_path: Path) -> None:
    window = _window(tmp_path, answers=[False])
    _type(window, tmp_path)
    window.make_client()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]

    window.remove_selected()

    assert server.play_folder.is_dir()
    assert ui_settings.friend_servers(tmp_path / "ui.json"), "the entry is still on the list"


def test_removal_of_a_folder_of_another_server_is_refused_by_the_marker(
    tmp_path: Path,
) -> None:
    """A marker that names another server is the Server tab's client, not this one's."""
    window = _window(tmp_path, answers=[True])
    _type(window, tmp_path)
    window.make_client()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    other = play_client.Marker(
        game="wow-wotlk",
        server_dir=Path("real-server"),
        source_client_dir=server.client_folder,
        created_at=datetime.now(UTC),
    )
    (server.play_folder / play_client.MARKER).write_text(
        other.model_dump_json(indent=2), encoding="utf-8"
    )

    window.remove_selected()

    assert server.play_folder.is_dir(), "another server's client was left as it was"
    assert ui_settings.friend_servers(tmp_path / "ui.json"), "the entry is kept"


# -- T807 review: the plan's identity, a Remove that waits, names that fold together ---


class _Deferred:
    """A job runner that holds each job until the test lets it run (a worker still going)."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Any, Any, Any]] = []

    def __call__(self, work: Any, on_done: Any, on_error: Any) -> None:
        self.jobs.append((work, on_done, on_error))

    def run_next(self) -> None:
        work, on_done, on_error = self.jobs.pop(0)
        try:
            result = work()
        except Exception as exc:  # the real runner's contract
            on_error(exc)
        else:
            on_done(result)


def test_a_field_edit_while_the_plan_runs_cannot_change_the_planned_identity(
    tmp_path: Path,
) -> None:
    jobs = _Deferred()
    window = _window(tmp_path, job_runner=jobs)
    _type(window, tmp_path)
    window.make_client()
    assert len(jobs.jobs) == 1, "the plan is out on its worker"

    # The fields stay editable while the client is walked.
    window.name_field.setText("Someone else's server")
    window.realm_field.setText("10.0.0.9:3725")
    other = [i for i in range(window.game_pick.count()) if i != window.game_pick.currentIndex()]
    assert other, "the picker offers more than one game"
    window.game_pick.setCurrentIndex(other[0])
    jobs.run_next()  # the plan answers; the create job is queued
    jobs.run_next()  # the build runs

    (saved,) = ui_settings.friend_servers(tmp_path / "ui.json")
    assert saved.name == "Mark's server"
    assert saved.realm == "192.168.50.49"
    assert saved.game == "wow-wotlk"
    marker = play_client.read_marker(saved.play_folder)
    assert marker is not None
    assert marker.game == "wow-wotlk"
    assert marker.server_dir == friend_server_dir("Mark's server")
    realmlist = networking_realmlist_text(saved.play_folder)
    assert "192.168.50.49" in realmlist and "10.0.0.9" not in realmlist


def networking_realmlist_text(folder: Path) -> str:
    """Every realmlist file the build wrote, as one text."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in folder.rglob("*")
        if path.is_file() and path.name.lower().startswith("realmlist")
    )


def test_remove_waits_while_a_job_for_that_entry_is_pending(tmp_path: Path) -> None:
    answers = [True]
    window = _window(tmp_path, answers=answers)
    _type(window, tmp_path)
    window.make_client()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    jobs = _Deferred()
    window._jobs = jobs
    window.play_selected()  # a play job for this entry is out
    assert len(jobs.jobs) == 1

    window.remove_selected()

    assert answers == [True], "the player was not even asked"
    assert server.play_folder.is_dir(), "the folder a job is using was not deleted"
    assert not window.remove_button.isEnabled(), "the button is locked while a job runs"
    assert "Remove" in window.status.text()
    jobs.run_next()
    assert window.remove_button.isEnabled(), "and unlocked when the job ends"


def test_remove_deletes_on_a_worker_not_in_the_press(tmp_path: Path) -> None:
    window = _window(tmp_path, answers=[True])
    _type(window, tmp_path)
    window.make_client()
    server = ui_settings.friend_servers(tmp_path / "ui.json")[0]
    jobs = _Deferred()
    window._jobs = jobs

    window.remove_selected()

    assert server.play_folder.is_dir(), "the press itself deleted nothing"
    assert len(jobs.jobs) == 1 and window._pending == ("remove", "Mark's server")
    assert not window.make_button.isEnabled() and not window.play_button.isEnabled()
    jobs.run_next()
    assert not server.play_folder.exists()
    assert ui_settings.friend_servers(tmp_path / "ui.json") == ()
    assert window._pending is None and window.make_button.isEnabled()


def test_a_name_that_folds_to_another_entrys_identity_is_refused(tmp_path: Path) -> None:
    assert friend_server_dir("Mark's server") == friend_server_dir("Mark_s server")
    window = _window(tmp_path)
    client = _type(window, tmp_path)
    window.make_client()
    (first,) = ui_settings.friend_servers(tmp_path / "ui.json")

    window.name_field.setText("Mark_s server")
    window.client_field.setText(str(client))
    window.make_client()

    assert ui_settings.friend_servers(tmp_path / "ui.json") == (first,), "no second entry"
    assert "Mark's server" in window.status.text()
    assert "different name" in window.status.text()
    assert (first.play_folder / play_client.MARKER).is_file(), "the first client is intact"


# -- persistence -------------------------------------------------------------------


def test_friend_servers_round_trip_through_ui_json(tmp_path: Path) -> None:
    """Saved next to Yu'lon's other settings, one per name, replace on the same name."""
    path = tmp_path / "ui.json"
    server = ui_settings.FriendServer(
        name="Mark's server",
        game="wow-wotlk",
        realm="192.168.50.49",
        host="192.168.50.49",
        auth_port=3724,
        client_folder=tmp_path / "WoW",
        play_folder=friend_server_dir("Mark's server"),
    )
    assert ui_settings.remember_friend_server(server, path) is True
    again = server.model_copy(update={"realm": "10.0.0.9", "host": "10.0.0.9"})
    ui_settings.remember_friend_server(again, path)
    kept = ui_settings.friend_servers(path)
    assert [(one.name, one.host, one.auth_port) for one in kept] == [
        ("Mark's server", "10.0.0.9", 3724)
    ]
    ui_settings.forget_friend_server("Mark's server", path)
    assert ui_settings.friend_servers(path) == ()
    assert ui_settings.forget_friend_server("nobody", path) is True


def test_friend_servers_survive_other_ui_writes(tmp_path: Path) -> None:
    """One settings file: a theme write must not drop the friend servers, and back."""
    path = tmp_path / "ui.json"
    server = ui_settings.FriendServer(
        name="A",
        game="wow-tbc",
        realm="1.2.3.4",
        host="1.2.3.4",
        auth_port=3724,
        client_folder=tmp_path,
        play_folder=friend_server_dir("A"),
    )
    ui_settings.remember_friend_server(server, path)
    ui_settings.remember_theme("98x-light", path)
    assert [one.name for one in ui_settings.friend_servers(path)] == ["A"]
    assert ui_settings.load_ui_settings(path).theme == "98x-light"
