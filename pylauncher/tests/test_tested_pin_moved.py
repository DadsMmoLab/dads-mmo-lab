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

    text = controller_view._format_report(report, server_moves=True)

    assert server_build_presses.UPDATE_TO_LATEST in text
    assert server_build_presses.RETURN_TO_PIN in text
    assert "mod-ale" in text.split(server_build_presses.UPDATE_TO_LATEST)[0].rsplit("\n", 1)[-1]
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
