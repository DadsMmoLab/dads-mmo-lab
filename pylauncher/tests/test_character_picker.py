"""T637: a prompt that asks for a character shows the server's own characters to pick from.

`mod-ah-bot` and `mod-ah-bot-plus` asked for a GUID (and an account id) typed as a
number, and nothing in Yu'lon showed either. These tests go through the real
manifests, the real `check_answer()`, the real `Applier` and the real dialog.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QComboBox

from yulon import apply as apply_module
from yulon import character_pick
from yulon.apply import Applier, ApplyRefusal, check_answer
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import parse_manifest
from yulon.ui.widgets.job import run_inline
from yulon.ui.widgets.manifest_prompt import ManifestPromptDialog

WOTLK = load_catalog().get("wow-wotlk")
UNBOUND = load_catalog().get("wow-unbound")


def _shipped(item_id: str) -> Any:
    return wotlk_modules.store().load("module", item_id)


def _manifest(prompts: list[dict[str, object]]) -> Any:
    return parse_manifest(
        {
            "id": "pick",
            "name": "Pick",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": prompts,
        }
    )


# --------------------------------------------------------------- the manifest


def test_the_two_ah_bot_manifests_ask_for_a_character_not_a_number() -> None:
    single = _shipped("mod-ah-bot")
    kinds = {p.key: (p.kind, p.multi, p.part, p.follows) for p in single.prompts}
    assert kinds == {
        "bot_guid": ("character", False, "guid", None),
        "bot_account": ("character", False, "account", "bot_guid"),
    }
    plus = _shipped("mod-ah-bot-plus")
    assert [(p.key, p.kind, p.multi) for p in plus.prompts] == [("bot_guid", "character", True)]


def test_the_exists_checks_stay_on_the_character_prompts() -> None:
    for item in ("mod-ah-bot", "mod-ah-bot-plus"):
        assert all(p.exists is not None for p in _shipped(item).prompts), item


def test_multi_belongs_to_a_character_prompt_only() -> None:
    with pytest.raises(ValueError, match="multi"):
        _manifest([{"key": "k", "question": "q", "kind": "int", "multi": True}])


def test_a_prompt_that_follows_another_must_follow_a_character_prompt() -> None:
    with pytest.raises(ValueError, match="follows"):
        _manifest(
            [
                {"key": "a", "question": "q", "kind": "int"},
                {
                    "key": "b",
                    "question": "q",
                    "kind": "character",
                    "part": "account",
                    "follows": "a",
                },
            ]
        )
    with pytest.raises(ValueError, match="follows"):
        _manifest(
            [
                {
                    "key": "b",
                    "question": "q",
                    "kind": "character",
                    "part": "account",
                    "follows": "nothing",
                }
            ]
        )
    with pytest.raises(ValueError, match="part"):
        _manifest([{"key": "b", "question": "q", "kind": "character", "part": "account"}])


# ------------------------------------------------------------- check_answer


def _prompt(**extra: object) -> Any:
    return _manifest([{"key": "k", "question": "q", "kind": "character", **extra}]).prompts[0]


@pytest.mark.parametrize("good", ["1", "42", "4294967295"])
def test_a_single_character_answer_is_one_guid(good: str) -> None:
    assert check_answer(_prompt(), good) == ""


@pytest.mark.parametrize("bad", ["", "four", "4,5", " 5", "4294967296", "-1", "1_0"])
def test_a_single_character_answer_refuses_what_is_not_one_guid(bad: str) -> None:
    assert check_answer(_prompt(), bad) != "", bad


@pytest.mark.parametrize("good", ["7", "7,8", "1,2,3"])
def test_a_multi_answer_is_a_comma_list_of_guids(good: str) -> None:
    assert check_answer(_prompt(multi=True), good) == ""


@pytest.mark.parametrize(
    "bad", ["", "7,", ",7", "7,,8", "7, 8", "7 8", "7;8", "7,x", "7,4294967296"]
)
def test_a_multi_answer_refuses_a_list_the_server_would_misread(bad: str) -> None:
    assert check_answer(_prompt(multi=True), bad) != "", bad


# ---------------------------------------------------------- the applier path


AHBOT_DIST = (
    "[worldserver]\nAuctionHouseBot.Account = 0\nAuctionHouseBot.GUID = 0\n"
    "AuctionHouseBot.GUIDs = 0\nAuctionHouseBot.EnableSeller = 0\n"
)


class _Git:
    """Enough git to refuse before cloning: any call at all is the failure."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __getattr__(self, name: str) -> Any:
        def call(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args))
            raise AssertionError(f"git.{name} was called before the answers were checked")

        return call


class _Reader:
    def __init__(self, found: set[str]) -> None:
        self.found = found
        self.queries: list[str] = []

    def query(self, db: str, statement: str) -> str:
        self.queries.append(statement)
        guid = statement.rsplit("=", 1)[-1].strip()
        return "Ahbot\n" if guid in self.found else ""

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("a check must not write")


def test_a_list_answer_is_checked_one_guid_at_a_time_and_names_the_missing_one(
    tmp_path: Path,
) -> None:
    reader = _Reader({"7"})
    applier = Applier(tmp_path, git=_Git(), sql=reader)  # type: ignore[arg-type]
    with pytest.raises(ApplyRefusal) as refusal:
        applier.install(_shipped("mod-ah-bot-plus"), {"bot_guid": "7,8"})
    assert "GUID 8" in str(refusal.value), str(refusal.value)
    assert "GUID 7" not in str(refusal.value)
    assert all("," not in q for q in reader.queries), reader.queries
    assert len(reader.queries) == 2


def test_a_list_answer_with_every_guid_present_passes_the_check(tmp_path: Path) -> None:
    reader = _Reader({"7", "8"})
    applier = Applier(tmp_path, git=_Git(), sql=reader)  # type: ignore[arg-type]
    plus = _shipped("mod-ah-bot-plus")
    log = apply_module._Log()  # noqa: SLF001
    applier._check_values(plus, "install", {"bot_guid": "7,8"}, log)  # noqa: SLF001
    assert len(reader.queries) == 2


def test_a_single_guid_with_a_comma_still_cannot_reach_a_query(tmp_path: Path) -> None:
    applier = Applier(tmp_path, git=_Git(), sql=_Reader({"7"}))  # type: ignore[arg-type]
    with pytest.raises(ApplyRefusal):
        applier.install(_shipped("mod-ah-bot"), {"bot_guid": "7,8", "bot_account": "1"})


# ----------------------------------------------------------------- the roster


class _Sql:
    def __init__(self, answer: str = "", fail: str = "") -> None:
        self.answer = answer
        self.fail = fail
        self.asked: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        if self.fail:
            raise RuntimeError(self.fail)
        return self.answer

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("a read must not write")


# guid, name, account id, account name, level, race, class, online, is a bot
ROWS = (
    "5\tGuglu\t3\tAHBOT\t80\t1\t1\t0\t0\n"
    "6\tKarl\t4\tPERZI\t12\t2\t8\t1\t0\n"
    "9\tRndbotone\t77\tRNDBOT1\t60\t5\t4\t1\t1\n"
)


def _roster(sql: _Sql, entry: Any = WOTLK, tmp_path: Path | None = None, **kw: Any) -> Any:
    return character_pick.read_roster(sql, entry, tmp_path or Path("/nonexistent"), **kw)


def test_the_roster_reads_name_account_level_race_and_class(tmp_path: Path) -> None:
    sql = _Sql(ROWS)
    roster = _roster(sql, tmp_path=tmp_path)
    assert roster.problem == ""
    first = roster.characters[0]
    assert (first.guid, first.name, first.account_id, first.account) == (5, "Guglu", 3, "AHBOT")
    assert (first.level, first.online) == (80, False)
    assert character_pick.describe(first) == "Guglu — account AHBOT, level 80, Human Warrior"
    db, statement = sql.asked[0]
    assert db == "characters"
    for column in ("guid", "name", "race", "class", "level", "online", "username"):
        assert column in statement, column


def test_playerbots_characters_are_left_out_and_counted(tmp_path: Path) -> None:
    roster = _roster(_Sql(ROWS), tmp_path=tmp_path)
    assert [c.name for c in roster.characters] == ["Guglu", "Karl"]
    assert roster.bots_left_out == 1


def test_the_bot_test_is_the_servers_own_marker_clause(tmp_path: Path) -> None:
    sql = _Sql(ROWS)
    _roster(sql, tmp_path=tmp_path)
    assert "playerbots_account_type" in sql.asked[0][1] or "LIKE 'RNDBOT%'" in sql.asked[0][1]


def test_an_online_character_is_marked_to_be_logged_out_first(tmp_path: Path) -> None:
    roster = _roster(_Sql(ROWS), tmp_path=tmp_path)
    karl = roster.characters[1]
    assert karl.online
    assert character_pick.describe(karl).endswith("online: log it out first")


def test_a_failed_read_is_a_problem_not_an_empty_server(tmp_path: Path) -> None:
    roster = _roster(_Sql(fail="container not running"), tmp_path=tmp_path)
    assert roster.characters == ()
    assert "container not running" in roster.problem


def test_no_reader_is_a_problem(tmp_path: Path) -> None:
    roster = character_pick.read_roster(None, WOTLK, tmp_path)
    assert roster.problem != ""


def test_the_database_is_started_alone_first_and_a_failed_start_is_the_problem(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    sql = _Sql(ROWS)
    sql_query = sql.query
    sql.query = lambda db, st: (order.append("read"), sql_query(db, st))[1]  # type: ignore[method-assign]

    def start() -> bool:
        order.append("start")
        return True

    _roster(sql, tmp_path=tmp_path, start_database=start)
    assert order == ["start", "read"]

    def boom() -> bool:
        raise RuntimeError("compose would not start it")

    roster = _roster(_Sql(ROWS), tmp_path=tmp_path, start_database=boom)
    assert "compose would not start it" in roster.problem


def test_unbound_reads_through_its_own_schema_names(tmp_path: Path) -> None:
    sql = _Sql(ROWS)
    roster = _roster(sql, entry=UNBOUND, tmp_path=tmp_path)
    assert roster.problem == ""
    assert UNBOUND.schema_map()["characters"] in sql.asked[0][1]
    assert UNBOUND.schema_map()["auth"] in sql.asked[0][1]


def test_an_unknown_race_or_class_is_shown_as_its_number() -> None:
    row = character_pick.Pickable(1, "X", 1, "A", 5, 99, 98, False, False)
    assert character_pick.describe(row) == "X — account A, level 5, race 99 class 98"


def test_the_applier_reads_the_roster_through_its_own_seams(tmp_path: Path) -> None:
    started: list[bool] = []
    applier = Applier(
        tmp_path,
        sql=_Sql(ROWS),  # type: ignore[arg-type]
        start_database=lambda: started.append(True) or True,
    )
    roster = applier.character_roster(WOTLK)
    assert [c.name for c in roster.characters] == ["Guglu", "Karl"]
    assert started == [True]


# ------------------------------------------------------------------ the dialog


def _roster_of(sql_rows: str = ROWS, tmp_path: Path = Path("/nonexistent")) -> Any:
    return character_pick.read_roster(_Sql(sql_rows), WOTLK, tmp_path)


def _dialog(
    item: str = "mod-ah-bot",
    roster: Any = None,
    remembered: dict[str, str] | None = None,
    jobs: Any = None,
    loads: list[int] | None = None,
) -> ManifestPromptDialog:
    manifest = _shipped(item)
    wanted = roster if roster is not None else _roster_of()

    def load() -> Any:
        if loads is not None:
            loads.append(1)
        return wanted

    return ManifestPromptDialog(
        None,
        manifest,
        manifest.prompts,
        remembered=remembered,
        characters=load,
        run_job=jobs or run_inline,
    )


def _picker(dialog: ManifestPromptDialog, key: str = "bot_guid") -> QComboBox:
    return dialog._controls[key].combo  # type: ignore[attr-defined,no-any-return]  # noqa: SLF001


def test_the_picker_lists_characters_with_account_level_race_and_class(qapp: object) -> None:
    dialog = _dialog()
    texts = [_picker(dialog).itemText(i) for i in range(_picker(dialog).count())]
    assert any(t == "Guglu — account AHBOT, level 80, Human Warrior" for t in texts), texts
    assert not any("Rndbotone" in t for t in texts), texts
    assert any(t.startswith("Karl") and "log it out first" in t for t in texts), texts


def test_one_pick_fills_both_the_guid_and_the_account(qapp: object) -> None:
    dialog = _dialog()
    combo = _picker(dialog)
    combo.setCurrentIndex(combo.findData(5))
    assert dialog.answers() == {"bot_guid": "5", "bot_account": "3"}
    assert dialog.problem() == ""
    # The followed prompt is not a second row: one question, one picker.
    assert len(dialog.questions()) == 1


def test_no_pick_yet_means_ok_is_not_available(qapp: object) -> None:
    dialog = _dialog()
    assert dialog.problem() != ""


def test_a_list_prompt_gives_a_comma_list_of_the_ticked_characters(qapp: object) -> None:
    dialog = _dialog("mod-ah-bot-plus")
    picker = dialog._controls["bot_guid"]  # noqa: SLF001
    picker.tick(5, True)  # type: ignore[attr-defined]
    picker.tick(6, True)  # type: ignore[attr-defined]
    assert dialog.answers() == {"bot_guid": "5,6"}
    picker.tick(5, False)  # type: ignore[attr-defined]
    assert dialog.answers() == {"bot_guid": "6"}


def test_saved_numbers_from_an_older_install_select_the_same_character(qapp: object) -> None:
    dialog = _dialog(remembered={"bot_guid": "5", "bot_account": "3"})
    combo = _picker(dialog)
    assert combo.currentData() == 5
    assert dialog.answers() == {"bot_guid": "5", "bot_account": "3"}
    assert dialog.problem() == ""


def test_a_saved_list_ticks_the_characters_it_names(qapp: object) -> None:
    dialog = _dialog("mod-ah-bot-plus", remembered={"bot_guid": "5,6"})
    assert dialog.answers() == {"bot_guid": "5,6"}
    assert dialog._controls["bot_guid"].ticked() == [5, 6]  # type: ignore[attr-defined]  # noqa: SLF001


def test_a_saved_guid_that_is_not_on_this_server_is_kept_and_said(qapp: object) -> None:
    dialog = _dialog(remembered={"bot_guid": "999", "bot_account": "3"})
    assert dialog.answers() == {"bot_guid": "999", "bot_account": "3"}
    texts = [_picker(dialog).itemText(i) for i in range(_picker(dialog).count())]
    assert any("999" in t and "not" in t for t in texts), texts


def test_a_server_with_no_characters_says_what_to_do(qapp: object) -> None:
    empty = character_pick.Roster(characters=())
    dialog = _dialog(roster=empty)
    said = dialog._controls["bot_guid"].status()  # type: ignore[attr-defined]  # noqa: SLF001
    assert "Accounts tab" in said and "ONE character" in said and "log it out" in said, said
    assert dialog.problem() != ""


def test_a_roster_that_could_not_be_read_falls_back_to_typing_the_numbers(qapp: object) -> None:
    broken = character_pick.Roster(characters=(), problem="the database is not answering")
    dialog = _dialog(roster=broken)
    control = dialog._controls["bot_guid"]  # noqa: SLF001
    assert "the database is not answering" in control.status()  # type: ignore[attr-defined]
    dialog.set_answer("bot_guid", "42")
    dialog.set_answer("bot_account", "7")
    assert dialog.problem() == ""
    assert dialog.answers() == {"bot_guid": "42", "bot_account": "7"}


def test_the_read_goes_through_the_job_runner_and_not_the_dialogs_own_thread(
    qapp: object,
) -> None:
    pending: list[Any] = []

    def runner(work: Any, done: Any, failed: Any) -> None:
        pending.append((work, done, failed))

    loads: list[int] = []
    dialog = _dialog(jobs=runner, loads=loads)
    assert loads == [], "the roster was read on the calling thread"
    assert "Reading" in dialog._controls["bot_guid"].status()  # type: ignore[attr-defined]  # noqa: SLF001
    work, done, _failed = pending[0]
    done(work())
    assert loads == [1]
    assert _picker(dialog).count() > 0


def test_a_read_that_raises_is_said_and_typing_still_works(qapp: object) -> None:
    def runner(work: Any, done: Any, failed: Any) -> None:
        failed(RuntimeError("no docker"))

    dialog = _dialog(jobs=runner)
    assert "no docker" in dialog._controls["bot_guid"].status()  # type: ignore[attr-defined]  # noqa: SLF001
    dialog.set_answer("bot_guid", "42")
    dialog.set_answer("bot_account", "7")
    assert dialog.problem() == ""


def test_unbound_loads_the_same_character_question_through_its_user_game_store() -> None:
    from yulon.controller_wow_wotlk import modules

    manifest = modules.store(user_game="wow-unbound").load("module", "mod-ah-bot")
    assert [(p.key, p.kind) for p in manifest.prompts] == [
        ("bot_guid", "character"),
        ("bot_account", "character"),
    ]


def test_the_picker_says_how_many_bots_it_left_out(qapp: object) -> None:
    dialog = _dialog()
    said = dialog._controls["bot_guid"].status()  # type: ignore[attr-defined]  # noqa: SLF001
    assert "1 playerbots character is not listed" in said, said


def test_the_typed_account_box_appears_only_when_the_picker_is_typed(qapp: object) -> None:
    listed = _dialog()
    label, box = listed._follower_rows["bot_account"]  # noqa: SLF001
    assert label.isHidden() and box.isHidden()
    broken = _dialog(roster=character_pick.Roster(problem="down"))
    label, box = broken._follower_rows["bot_account"]  # noqa: SLF001
    assert not label.isHidden() and not box.isHidden()


@pytest.mark.parametrize("old", [" 5", "+5", "5 "])
def test_an_answer_stored_by_an_older_build_as_an_int_still_loads(old: str) -> None:
    single = _prompt()
    assert apply_module.stored_answer(single, old) == "5"
    assert check_answer(single, apply_module.stored_answer(single, old)) == ""
    assert apply_module.stored_answer(_prompt(multi=True), old) == "5"
