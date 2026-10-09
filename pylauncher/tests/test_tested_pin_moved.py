"""T588 + T586: an install learns that the catalog's tested pin moved past it.

The Discord player's case: a WotLK install that never pressed "Update the
server to latest…" sits on the core it was installed at (7f12e89e); this
version of Yu'lon is tested with f19a1879. Until T588 the Server build menu
never offered "Return to the tested pin…" there, because the offer compared the
checkout with the pin RECORDED by an earlier update press, and an install that
never pressed one has no record at all.

The reading is local: each moving source's `.git/HEAD` against the catalog's
`rev`, read the way `sources_still_off()` reads it (no git run, no network).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY
from yulon import install_wiring, server_build_presses
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallStopped

CORE = "mod-playerbots/azerothcore-wotlk"
BOTS = "mod-playerbots/mod-playerbots"
OLD_CORE = "7f12e89ee5f467a50e62eba1d525eac7dc953d03"
NEW_CORE = "f19a18799a35f7c24bdcdc9ea399c601f166259b"
OLD_BOTS = "b949b50bfcdd4fab937781bac2d7765e39330e4b"
NEW_BOTS = "037c01418b5d01506917a3db9b44fd56ac5f965c"
AHEAD = "c" * 40

BACK = server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)


def _state(*revs: native.SourceRev) -> native.InstallState:
    return native.InstallState(game_id="wow-wotlk", install_id="x", source_revs=revs)


def _pin(repo: str, rev: str, head: str | None) -> native.CatalogPin:
    return native.CatalogPin(repo=repo, rev=rev, head=head)


# -- the reading ---------------------------------------------------------------


def test_an_install_that_never_pressed_update_is_offered_a_pin_that_moved() -> None:
    """The owner's case: no record, the checkout on 7f12e89e, the catalog on f19a1879."""
    pins = (_pin(CORE, NEW_CORE, OLD_CORE), _pin(BOTS, NEW_BOTS, OLD_BOTS))

    said = native.source_version(None, (), pins)

    assert said.past_the_pin is True
    assert said.pin_moved is True
    rows = said.line.splitlines()
    assert rows[0] == f"{CORE}: built from {OLD_CORE[:7]}; the tested pin is {NEW_CORE[:7]}"
    assert rows[1] == f"{BOTS}: built from {OLD_BOTS[:7]}; the tested pin is {NEW_BOTS[:7]}"
    assert rows[2] == native.PIN_MOVED_NOTE
    assert BACK in native.PIN_MOVED_NOTE
    # The same with an empty record (a state file with no `source_revs`).
    assert native.source_version(_state(), (), pins) == said


def test_an_install_on_the_catalog_pin_is_offered_nothing() -> None:
    pins = (_pin(CORE, NEW_CORE, NEW_CORE), _pin(BOTS, NEW_BOTS, NEW_BOTS))
    assert native.source_version(None, (), pins) == native.SourceVersion("", past_the_pin=False)


def test_a_head_nobody_could_read_offers_nothing() -> None:
    """Fail closed: "cannot say" is not "off the pin", which would offer an hour's compile."""
    pins = (_pin(CORE, NEW_CORE, None), _pin(BOTS, NEW_BOTS, NEW_BOTS))
    assert native.source_version(None, (), pins) == native.SourceVersion("", past_the_pin=False)


def test_one_source_off_a_moved_pin_is_named_alone() -> None:
    pins = (_pin(CORE, NEW_CORE, OLD_CORE), _pin(BOTS, NEW_BOTS, NEW_BOTS))
    said = native.source_version(None, (), pins)
    assert said.past_the_pin is True
    assert said.line.splitlines() == [
        f"Built from {OLD_CORE[:7]}; the tested pin is {NEW_CORE[:7]}",
        native.PIN_MOVED_NOTE,
    ]


def test_a_record_on_the_old_pin_is_read_against_the_new_one() -> None:
    """Updated and returned under the OLD catalog: the record says "on the tested pin"."""
    row = native.SourceRev(CORE, f"{OLD_CORE[:7]} · 2026-09-20", pin=OLD_CORE, ahead=0)
    said = native.source_version(_state(row), (), (_pin(CORE, NEW_CORE, OLD_CORE),))
    assert said.past_the_pin is True
    assert said.line.splitlines() == [
        f"Built from {OLD_CORE[:7]} (2026-09-20); the tested pin is {NEW_CORE[:7]}",
        native.PIN_MOVED_NOTE,
    ]


def test_an_update_that_landed_on_the_new_pin_is_not_offered_a_no_op_compile() -> None:
    """Update to latest landed on f19a1879 when the old catalog pinned 7f12e89e.

    Read against the recorded pin this is "62 commits past the tested pin" and
    offers an hour's compile that moves nothing; against the catalog it is on it.
    """
    row = native.SourceRev(CORE, f"{NEW_CORE[:7]} · 2026-10-02", pin=OLD_CORE, ahead=62)
    said = native.source_version(_state(row), (), (_pin(CORE, NEW_CORE, NEW_CORE),))
    assert said == native.SourceVersion(
        line=f"On the tested pin {NEW_CORE[:7]} (2026-10-02)", past_the_pin=False
    )


def test_a_record_against_the_current_pin_reads_as_it_always_did() -> None:
    row = native.SourceRev(CORE, f"{AHEAD[:7]} · 2026-10-05", pin=NEW_CORE, ahead=3)
    with_pins = native.source_version(_state(row), (), (_pin(CORE, NEW_CORE, AHEAD),))
    assert with_pins == native.source_version(_state(row))
    assert native.PIN_MOVED_NOTE not in with_pins.line


def test_tested_pins_reads_each_head_off_disk(tmp_path: Path) -> None:
    for source in ENTRY.emulator.sources:
        git_dir = tmp_path / source.dest / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "HEAD").write_text(f"{OLD_CORE}\n", encoding="utf-8")

    pins = native.tested_pins(tmp_path, ENTRY.emulator.sources)

    assert [(p.repo, p.rev, p.head) for p in pins] == [
        (s.repo, s.rev, OLD_CORE) for s in ENTRY.emulator.sources
    ]


def test_a_source_with_no_pin_means_no_tested_pin_to_offer(tmp_path: Path) -> None:
    """The press refuses an entry that does not pin every source, so nothing is offered."""
    unpinned = (
        ENTRY.emulator.sources[0],
        ENTRY.emulator.sources[1].model_copy(update={"rev": None}),
    )
    assert native.tested_pins(tmp_path, unpinned) == ()


# -- the wiring: the tab's reading and the press's question -----------------------------


def _checkout(server_dir: Path, heads: dict[str, str]) -> None:
    for source in ENTRY.emulator.sources:
        git_dir = server_dir / source.dest / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "HEAD").write_text(f"{heads[source.repo]}\n", encoding="utf-8")


def _catalog_pins() -> dict[str, str]:
    return {source.repo: source.rev or "" for source in ENTRY.emulator.sources}


def test_the_route_offers_the_return_on_an_install_behind_the_catalog(tmp_path: Path) -> None:
    _checkout(tmp_path, {CORE: OLD_CORE, BOTS: OLD_BOTS})
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path)
    assert route is not None

    said = route.source_version()

    assert said.past_the_pin is True
    assert native.PIN_MOVED_NOTE in said.line
    question = route.pin_confirmation()
    pinned = _catalog_pins()
    assert OLD_CORE[:7] in question and pinned[CORE][:7] in question
    # The old question says "back" and "the newer server already wrote", both
    # false of a move onto a newer tested commit; this one says neither.
    assert "newer server" not in question
    assert "back on" not in question


def test_the_route_offers_nothing_on_the_catalog_pin(tmp_path: Path) -> None:
    _checkout(tmp_path, _catalog_pins())
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path)
    assert route is not None

    assert route.source_version() == native.SourceVersion("", past_the_pin=False)
    assert route.pin_confirmation() == native.return_to_pin_confirmation(
        ENTRY, tmp_path, ENTRY.emulator.sources[0].repo
    )


# -- T586: a module that needs a newer core ------------------------------------------------

MODULE_ERROR = (
    "#12 512.3 /azerothcore/modules/mod-ale/src/LuaEngine/methods/PlayerMethods.h:5205:24: "
    "error: no member named 'IsHeadless' in 'WorldSession'"
)
FAILED = "the build failed (exit 1). Its last words were: …"


class _Engine:
    def __init__(self, lines: Sequence[str], error: BaseException | None) -> None:
        self.lines = lines
        self.error = error

    def _run(self) -> Iterator[str]:
        yield from self.lines
        if self.error is not None:
            raise self.error

    def rebuild(self, options: Any, **_kwargs: Any) -> Iterator[str]:
        return self._run()

    def update_to_latest(self, options: Any, **_kwargs: Any) -> Iterator[str]:
        return self._run()


def _rebuild(
    monkeypatch: pytest.MonkeyPatch,
    server_dir: Path,
    lines: Sequence[str],
    error: BaseException,
    put_back: Any = None,
) -> str:
    monkeypatch.setattr(
        install_wiring, "installer_for_app", lambda _e, **_kw: _Engine(lines, error)
    )
    rebuild = install_wiring.rebuild_for_app(ENTRY, server_dir, put_back=put_back)
    with pytest.raises(InstallerError) as failed:
        list(rebuild(threading.Event()))
    return str(failed.value)


def test_a_rebuild_failing_in_a_module_on_a_core_off_its_moved_pin_names_the_return(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _checkout(tmp_path, {CORE: OLD_CORE, BOTS: OLD_BOTS})
    put = "Yu'lon put mod-ale back on the version it had before that update (bd74eae)."

    message = _rebuild(
        monkeypatch, tmp_path, [MODULE_ERROR], InstallerError(FAILED), put_back=lambda named: put
    )

    assert message.startswith(f"{FAILED} {put} ")
    note = message[len(f"{FAILED} {put} ") :]
    assert BACK in note
    assert OLD_CORE[:7] in note and _catalog_pins()[CORE][:7] in note
    assert "mod-ale" in note
    assert server_build_presses.REBUILD in note, "the 'no Rebuild in between' order is not said"


def test_a_rebuild_failing_in_a_module_on_the_catalog_pin_adds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _checkout(tmp_path, _catalog_pins())
    assert _rebuild(monkeypatch, tmp_path, [MODULE_ERROR], InstallerError(FAILED)) == FAILED


def test_a_rebuild_failing_outside_any_module_adds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _checkout(tmp_path, {CORE: OLD_CORE, BOTS: OLD_BOTS})
    core_error = "#12 512.3 /azerothcore/src/server/game/Entities/Player.cpp:9:1: error: x"
    assert _rebuild(monkeypatch, tmp_path, [core_error], InstallerError(FAILED)) == FAILED


def test_a_stopped_rebuild_adds_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _checkout(tmp_path, {CORE: OLD_CORE, BOTS: OLD_BOTS})
    stopped = InstallStopped("Stopped.")
    assert _rebuild(monkeypatch, tmp_path, [MODULE_ERROR], stopped) == "Stopped."


@pytest.mark.parametrize("press", ["press", "to_pin"])
def test_a_server_move_failing_in_a_module_says_to_update_it_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, press: str
) -> None:
    """Old mod-ale on the new core: the move is put back, and the order is said."""
    engine = _Engine([MODULE_ERROR], InstallerError(FAILED))
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _e, **_kw: engine)
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path)
    assert route is not None

    with pytest.raises(InstallerError) as failed:
        list(getattr(route, press)(None))

    message = str(failed.value)
    assert message.startswith(f"{FAILED} ")
    note = message[len(f"{FAILED} ") :]
    assert "mod-ale" in note and "Modules tab" in note
    assert server_build_presses.REBUILD in note
    label = (
        server_build_presses.UPDATE_TO_LATEST
        if press == "press"
        else server_build_presses.RETURN_TO_PIN
    )
    assert label in note, "the note does not send the player back to the press that failed"


# -- T586: the Modules tab's report after an update, before any build ----------------------


@pytest.mark.parametrize("action", ["install", "update"])
def test_a_cpp_module_report_names_both_server_presses_where_the_install_has_them(
    action: str,
) -> None:
    """The report cannot know whether the new commit needs a newer core, so it says both routes."""
    from yulon.apply import ApplyReport
    from yulon.ui import controller_view

    report = ApplyReport(action, "mod-ale", family="module", rebuild_required=True)  # type: ignore[arg-type]

    text = controller_view._format_report(report, server_moves=True, pin_moved=True)

    assert server_build_presses.UPDATE_TO_LATEST in text
    assert server_build_presses.RETURN_TO_PIN in text
    assert "mod-ale" in text.split(server_build_presses.UPDATE_TO_LATEST)[0].rsplit("\n", 1)[-1]
    # Codex adversarial round 3: off a pin that did NOT move (an Update to latest
    # left the server ahead of it), Return goes to older code: not named as the cure.
    ahead = controller_view._format_report(report, server_moves=True)
    assert server_build_presses.UPDATE_TO_LATEST in ahead
    assert server_build_presses.RETURN_TO_PIN not in ahead
    # Where the install has no such presses, the report names none.
    plain = controller_view._format_report(report)
    assert server_build_presses.UPDATE_TO_LATEST not in plain
    assert server_build_presses.RETURN_TO_PIN not in plain


def test_a_removal_report_names_no_server_move() -> None:
    from yulon.apply import ApplyReport
    from yulon.ui import controller_view

    report = ApplyReport("remove", "mod-ale", family="module", rebuild_required=True)
    assert server_build_presses.UPDATE_TO_LATEST not in controller_view._format_report(
        report, server_moves=True
    )


def test_every_new_sentence_reads_as_meant_for_the_player(tmp_path: Path) -> None:
    from tests.support_player_text import text_faults

    moved = (_pin(CORE, NEW_CORE, OLD_CORE), _pin(BOTS, NEW_BOTS, None))
    said = [
        native.PIN_MOVED_NOTE,
        native.moved_pin_confirmation(ENTRY, tmp_path, moved),
        native.core_off_its_moved_pin_note(moved, ("mod-ale",)),
        native.module_order_note(("mod-ale", "mod-x"), server_build_presses.RETURN_TO_PIN),
    ]
    assert [text_faults(text) for text in said] == [[], [], [], []]
    assert f"{BOTS} is tested with {NEW_BOTS[:7]}" in said[1], "an unread HEAD is not invented"


def test_a_stale_record_whose_folder_is_already_on_the_new_pin_is_offered_nothing(
    tmp_path: Path,
) -> None:
    """Codex adversarial (high): the record says 7f12e89e, the folder is on f19a1879 already.

    Read off the record alone this offers an hour's compile that moves nothing,
    and its question would say "is on f19a187, tested with f19a187". The
    folder's HEAD is the fact; the record is the fallback when HEAD is unread.
    """
    row = native.SourceRev(CORE, f"{OLD_CORE[:7]} · 2026-09-20", pin=OLD_CORE, ahead=0)
    on_it = (_pin(CORE, NEW_CORE, NEW_CORE),)
    said = native.source_version(_state(row), (), on_it)
    assert said.past_the_pin is False
    assert said.line == f"On the tested pin {NEW_CORE[:7]}"
    assert native.moved_pins(_state(row), on_it) == ()
    # HEAD unread: the record still decides, as before.
    unread = native.source_version(_state(row), (), (_pin(CORE, NEW_CORE, None),))
    assert unread.past_the_pin is True


def test_a_recorded_source_the_catalog_no_longer_moves_offers_nothing() -> None:
    """Codex adversarial round 2: a row for a repo the press no longer moves cannot drive it.

    The press moves `sources_that_move()` only; a row left from a source the
    catalog renamed or dropped would keep an hour's compile on offer that never
    moves it. It stays on the line as history.
    """
    gone = native.SourceRev("old/renamed", f"{AHEAD[:7]} · 2026-09-01", pin=OLD_CORE, ahead=4)
    said = native.source_version(_state(gone), (), (_pin(CORE, NEW_CORE, NEW_CORE),))
    assert said.past_the_pin is False
    assert said.line.startswith(f"Built from {AHEAD[:7]}")
    # With no catalog reading at all, the record alone decides, as before T588.
    assert native.source_version(_state(gone)).past_the_pin is True


def test_an_update_ahead_of_an_unmoved_pin_is_not_a_moved_pin() -> None:
    row = native.SourceRev(CORE, f"{AHEAD[:7]} · 2026-10-05", pin=NEW_CORE, ahead=3)
    said = native.source_version(_state(row), (), (_pin(CORE, NEW_CORE, AHEAD),))
    assert said.past_the_pin is True and said.pin_moved is False


def test_the_tab_tells_the_report_whether_the_pin_moved(tmp_path: Path) -> None:
    """The view keeps the moved-pin answer of its last version reading for the module report."""
    from yulon.ui import controller_view

    _checkout(tmp_path, {CORE: OLD_CORE, BOTS: OLD_BOTS})
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path)
    assert route is not None
    said = route.source_version()
    assert said.pin_moved is True
    assert controller_view.ControllerView._module_report_options(said, route) == {
        "server_moves": True,
        "pin_moved": True,
    }
    assert controller_view.ControllerView._module_report_options(None, None) == {
        "server_moves": False,
        "pin_moved": False,
    }


# -- cold review: an install an Update took PAST its old pin is not "catching up" ----------


def _updated_past_the_old_pin() -> native.SourceRev:
    """Update to latest pressed after 2 Oct, before the catalog moved: X is 40 past 7f12e89e."""
    return native.SourceRev(CORE, f"{AHEAD[:7]} · 2026-10-05", pin=OLD_CORE, ahead=40)


def test_an_install_updated_past_its_old_pin_keeps_the_way_back_wording() -> None:
    """The press moves X BACK to f19a1879: a downgrade, never framed as catching up."""
    state = _state(_updated_past_the_old_pin())
    pins = (_pin(CORE, NEW_CORE, AHEAD),)

    said = native.source_version(state, (), pins)

    assert said.past_the_pin is True, "the way back stays offered"
    assert said.pin_moved is False
    assert native.PIN_MOVED_NOTE not in said.line
    assert native.moved_pins(state, pins) == ()


def test_one_source_updated_past_its_pin_keeps_the_way_back_wording_for_the_whole_press() -> None:
    """Mixed: the core sits on its old pin (catch-up), the bots were updated past theirs.

    The press moves both, one of them backwards, so the question must keep the
    warning about what a newer server wrote.
    """
    core = native.SourceRev(CORE, f"{OLD_CORE[:7]} · 2026-09-20", pin=OLD_CORE, ahead=0)
    bots = native.SourceRev(BOTS, f"{AHEAD[:7]} · 2026-10-05", pin=NEW_BOTS, ahead=3)
    pins = (_pin(CORE, NEW_CORE, OLD_CORE), _pin(BOTS, NEW_BOTS, AHEAD))

    said = native.source_version(_state(core, bots), (), pins)

    assert said.past_the_pin is True
    assert said.pin_moved is False
    assert native.moved_pins(_state(core, bots), pins) == ()


def test_the_route_asks_the_way_back_question_of_an_install_updated_past_its_old_pin(
    tmp_path: Path,
) -> None:
    _checkout(tmp_path, {CORE: AHEAD, BOTS: _catalog_pins()[BOTS]})
    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk",
            install_id="x",
            source_revs=(_updated_past_the_old_pin(),),
        ),
    )
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path)
    assert route is not None

    assert route.source_version().past_the_pin is True
    assert route.pin_confirmation() == native.return_to_pin_confirmation(
        ENTRY, tmp_path, ENTRY.emulator.sources[0].repo
    )
    assert "newer server already wrote" in route.pin_confirmation()


def test_a_head_file_that_is_not_text_reads_as_unknown(tmp_path: Path) -> None:
    """Cold review: a non-UTF-8 HEAD or packed-refs raised UnicodeDecodeError into the tab."""
    for source in ENTRY.emulator.sources:
        git_dir = tmp_path / source.dest / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "HEAD").write_bytes(b"ref: refs/heads/\xff\xfe\n")
    (tmp_path / ".git" / "HEAD").write_bytes(b"\xff\xfe\x00garbage")
    bots = tmp_path / ENTRY.emulator.sources[1].dest / ".git"
    (bots / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (bots / "packed-refs").write_bytes(b"\xff\xfe not text\n")

    pins = native.tested_pins(tmp_path, ENTRY.emulator.sources)

    assert [pin.head for pin in pins] == [None, None]
    assert native.source_version(None, (), pins) == native.SourceVersion("", past_the_pin=False)
