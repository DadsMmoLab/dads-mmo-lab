"""T619: an existing Tortoise server is OFFERED the one-key SQL-log fix; nothing else is touched.

#411 made a new Tortoise install write `LogFilter_SQLText = 1` into `mangosd.conf`. An
install made before that carries the dist's `0`, which prints every database statement
into the world log. A `0` is the same bytes whether the old install wrote it or the player
chose it, so Yu'lon cannot tell and OFFERS the change (once) instead of applying it.

The key name and the file are spelled as literals here, never read back from the module.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from yulon import sql_log_offer
from yulon.catalog.catalog import CatalogEntry, load_catalog

MANGOSD = "etc/mangosd.conf"
REALMD = "etc/realmd.conf"
DIST = (
    "[MangosdConf]\n"
    "ConfVersion = 2\n"
    "GM.StartLevel = 1\n"
    "LogFilter_SQLText = 0\n"
    "Rate.XP.Kill = 7\n"
)


def tortoise() -> CatalogEntry:
    return load_catalog().get("wow-tortoise")


def wotlk() -> CatalogEntry:
    return load_catalog().get("wow-wotlk")


def lay(server: Path, text: str = DIST, name: str = MANGOSD) -> Path:
    path = server / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def with_realmd_key() -> CatalogEntry:
    """The catalog with the key set in the login server's conf, as #435 (T618) sets it."""
    entry = copy.deepcopy(tortoise())
    native = entry.install.native
    assert native is not None and native.cmangos is not None
    native.cmangos.conf.files["realmd.conf"].keys["LogFilter_SQLText"] = "1"
    return entry


# -- who is offered -----------------------------------------------------------


def test_the_dist_zero_is_offered(tmp_path: Path) -> None:
    lay(tmp_path)
    assert [(o.file, o.now) for o in sql_log_offer.offers(tortoise(), tmp_path)] == [(MANGOSD, "0")]


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES", '"1"'])
def test_a_value_the_server_reads_as_on_is_never_offered(tmp_path: Path, value: str) -> None:
    lay(tmp_path, DIST.replace("= 0", f"= {value}"))
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


@pytest.mark.parametrize(
    "value", ["0", "false", "False", '"0"', "on", "y", "True", "Yes", "maybe", "2", ""]
)
def test_every_value_the_server_reads_as_off_is_offered(tmp_path: Path, value: str) -> None:
    """`GetBoolDefault` (tortoise-wow 187af788) is on for exactly true/TRUE/yes/YES/1.

    So `on`, `y`, `True` and `2` print the statements exactly as `0` does, and the player who
    wrote one of them thinking it was on is the player this offer is for.
    """
    lay(tmp_path, DIST.replace("= 0", f"= {value}"))
    assert len(sql_log_offer.offers(tortoise(), tmp_path)) == 1


def test_a_key_missing_from_the_file_is_offered_and_a_commented_one_does_not_count(
    tmp_path: Path,
) -> None:
    lay(tmp_path, DIST.replace("LogFilter_SQLText = 0", "# LogFilter_SQLText = 1"))
    (offer,) = sql_log_offer.offers(tortoise(), tmp_path)
    assert offer.now == ""


def test_the_last_of_two_active_lines_is_the_one_the_server_reads(tmp_path: Path) -> None:
    lay(tmp_path, DIST + "LogFilter_SQLText = 1\n")
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()
    lay(tmp_path, DIST.replace("= 0", "= 1") + "LogFilter_SQLText = 0\n")
    assert len(sql_log_offer.offers(tortoise(), tmp_path)) == 1


def test_the_key_in_two_sections_is_ambiguous_and_left_alone(tmp_path: Path) -> None:
    lay(tmp_path, DIST + "[Other]\nLogFilter_SQLText = 0\n")
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


def test_no_conf_on_disk_no_offer(tmp_path: Path) -> None:
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


def test_a_conf_that_is_not_text_is_not_offered_and_not_touched(tmp_path: Path) -> None:
    path = tmp_path / MANGOSD
    path.parent.mkdir(parents=True)
    path.write_bytes(b"LogFilter_SQLText = 0\n\xff\xfe\n")
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


def test_a_game_whose_catalog_does_not_set_the_key_is_never_offered(tmp_path: Path) -> None:
    lay(tmp_path, DIST)
    assert sql_log_offer.offers(wotlk(), tmp_path) == ()


# -- turning it off -----------------------------------------------------------


def test_turning_it_off_changes_that_one_line_and_nothing_else_and_backs_the_file_up(
    tmp_path: Path,
) -> None:
    path = lay(tmp_path)
    done = sql_log_offer.turn_off(tortoise(), tmp_path, [MANGOSD])
    assert (
        path.read_bytes() == DIST.replace("LogFilter_SQLText = 0", "LogFilter_SQLText = 1").encode()
    )
    (one,) = done
    assert one.file == MANGOSD
    assert one.backup.read_bytes() == DIST.encode()
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


def test_a_crlf_conf_keeps_its_line_endings(tmp_path: Path) -> None:
    crlf = DIST.replace("\n", "\r\n")
    path = lay(tmp_path, crlf)
    sql_log_offer.turn_off(tortoise(), tmp_path, [MANGOSD])
    assert path.read_bytes() == crlf.replace("SQLText = 0", "SQLText = 1").encode()


def test_a_missing_key_is_appended_and_the_rest_of_the_file_is_kept(tmp_path: Path) -> None:
    text = DIST.replace("LogFilter_SQLText = 0\n", "")
    path = lay(tmp_path, text)
    sql_log_offer.turn_off(tortoise(), tmp_path, [MANGOSD])
    after = path.read_text(encoding="utf-8")
    assert after.startswith(text.rstrip("\n"))
    assert after.rstrip().endswith("LogFilter_SQLText = 1")


def test_a_value_changed_to_on_since_the_offer_is_not_written_again(tmp_path: Path) -> None:
    path = lay(tmp_path, DIST.replace("= 0", "= 1"))
    assert sql_log_offer.turn_off(tortoise(), tmp_path, [MANGOSD]) == ()
    assert path.read_bytes() == DIST.replace("= 0", "= 1").encode()
    assert list(path.parent.glob("*.bak")) == []


def test_only_the_files_asked_for_are_written(tmp_path: Path) -> None:
    entry = with_realmd_key()
    mangosd = lay(tmp_path)
    realmd = lay(tmp_path, "LogFilter_SQLText = 0\n", REALMD)
    sql_log_offer.turn_off(entry, tmp_path, [MANGOSD])
    assert b"SQLText = 1" in mangosd.read_bytes()
    assert realmd.read_bytes() == b"LogFilter_SQLText = 0\n"


def test_a_conf_reached_through_a_link_out_of_the_server_is_refused(tmp_path: Path) -> None:
    from yulon import tuning

    outside = tmp_path / "outside"
    outside.mkdir()
    lay(outside, DIST, "mangosd.conf")
    server = tmp_path / "server"
    server.mkdir()
    (server / "etc").symlink_to(outside, target_is_directory=True)
    with pytest.raises(tuning.TuningError):
        sql_log_offer.turn_off(tortoise(), server, [MANGOSD])
    assert (outside / "mangosd.conf").read_bytes() == DIST.encode()


# -- asked once ---------------------------------------------------------------


def test_keeping_it_as_it_is_writes_no_conf_and_the_offer_is_not_made_again(
    tmp_path: Path,
) -> None:
    path = lay(tmp_path)
    sql_log_offer.keep(tortoise(), tmp_path, [MANGOSD])
    assert path.read_bytes() == DIST.encode()
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


def test_turning_it_off_is_also_an_answer_so_a_later_hand_edit_back_to_zero_is_not_nagged(
    tmp_path: Path,
) -> None:
    path = lay(tmp_path)
    sql_log_offer.turn_off(tortoise(), tmp_path, [MANGOSD])
    path.write_bytes(DIST.encode())
    assert sql_log_offer.offers(tortoise(), tmp_path) == ()


def test_the_answer_is_per_file(tmp_path: Path) -> None:
    entry = with_realmd_key()
    lay(tmp_path)
    lay(tmp_path, "LogFilter_SQLText = 0\n", REALMD)
    sql_log_offer.keep(entry, tmp_path, [MANGOSD])
    assert [o.file for o in sql_log_offer.offers(entry, tmp_path)] == [REALMD]


def test_a_record_that_cannot_be_read_asks_again_and_is_replaced_by_the_next_answer(
    tmp_path: Path,
) -> None:
    lay(tmp_path)
    record = tmp_path / ".yulon-sql-log-offer.json"
    record.write_text("{not json", encoding="utf-8")
    assert len(sql_log_offer.offers(tortoise(), tmp_path)) == 1
    sql_log_offer.keep(tortoise(), tmp_path, [MANGOSD])
    assert json.loads(record.read_text(encoding="utf-8"))["answered"] == {MANGOSD: "kept"}


# -- the realmd.conf hook -----------------------------------------------------


def test_realmd_conf_is_offered_the_day_the_catalog_sets_the_key_there(tmp_path: Path) -> None:
    lay(tmp_path)
    lay(tmp_path, "LogFilter_SQLText = 0\n", REALMD)
    # Since #435 (T618) the shipped catalog sets the key under realmd.conf as well.
    assert [o.file for o in sql_log_offer.offers(tortoise(), tmp_path)] == [MANGOSD, REALMD]
    assert [o.file for o in sql_log_offer.offers(with_realmd_key(), tmp_path)] == [MANGOSD, REALMD]


# -- the Tuning tab's offer ---------------------------------------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch):
    """`test_controller_view`'s Docker-free `runner.run`."""
    from tests.test_controller_view import _Ps
    from yulon import runner
    from yulon.ui import controller_view as cv
    from yulon.ui.widgets.job import run_inline

    # "Turn it off" is a Tuning write, run on the job runner inside the server hold (T622).
    monkeypatch.setattr(cv, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def view_for(entry: CatalogEntry, server: Path):
    from yulon.ui.controller_view import ControllerServices, ControllerView

    return ControllerView(entry, ControllerServices.for_entry(entry, server), status_poll_ms=0)


def shown(view) -> bool:
    return not view.tuning_log_offer.isHidden()


def test_the_offer_is_one_line_naming_the_file_the_key_and_the_two_answers(
    qapp: object, ps, tmp_path: Path
) -> None:
    lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    assert shown(view)
    text = view.tuning_log_offer_label.text()
    assert "mangosd.conf" in text and "LogFilter_SQLText" in text
    assert "Reset to default" not in text
    assert view.tuning_log_offer_off_button.text() == "Turn it off"
    assert view.tuning_log_offer_keep_button.text() == "Keep it as it is"


def test_no_offer_when_the_conf_already_says_on(qapp: object, ps, tmp_path: Path) -> None:
    lay(tmp_path, DIST.replace("= 0", "= 1"))
    assert not shown(view_for(tortoise(), tmp_path))


def test_no_offer_before_the_server_has_laid_its_conf(qapp: object, ps, tmp_path: Path) -> None:
    assert not shown(view_for(tortoise(), tmp_path))


def test_no_offer_on_a_game_that_does_not_set_the_key(qapp: object, ps, tmp_path: Path) -> None:
    lay(tmp_path)
    assert not shown(view_for(wotlk(), tmp_path))


def test_turn_it_off_writes_the_one_line_owes_a_restart_and_hides_the_offer(
    qapp: object, ps, tmp_path: Path
) -> None:
    path = lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    view.tuning_log_offer_off_button.click()
    assert path.read_bytes() == DIST.replace("SQLText = 0", "SQLText = 1").encode()
    assert view._tuning_owed == {"restart": {MANGOSD}}
    assert not shown(view)
    assert "backup" in view.tuning_report.toPlainText().lower()
    assert not shown(view_for(tortoise(), tmp_path))


def test_keep_it_writes_nothing_owes_nothing_and_is_not_asked_again(
    qapp: object, ps, tmp_path: Path
) -> None:
    path = lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    view.tuning_log_offer_keep_button.click()
    assert path.read_bytes() == DIST.encode()
    assert view._tuning_owed == {}
    assert not shown(view)
    assert not shown(view_for(tortoise(), tmp_path))


def test_a_refused_write_says_so_and_leaves_the_offer_up(
    qapp: object, ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = lay(tmp_path)
    view = view_for(tortoise(), tmp_path)

    def refuse(*_a: object, **_k: object):
        raise OSError("disk full")

    monkeypatch.setattr(sql_log_offer.tuning, "write", refuse)
    view.tuning_log_offer_off_button.click()
    assert path.read_bytes() == DIST.encode()
    assert "disk full" in view.tuning_report.toPlainText()
    assert shown(view)


def test_turn_it_off_while_another_yulon_holds_the_server_writes_nothing(
    qapp: object, ps, tmp_path: Path
) -> None:
    """T622's rule for every conf write, on T619's button: the hold first, then the file.

    Mutation this catches: `turn_off_sql_log()` writing straight through again.
    """
    from tests.test_more_writes_hold import HELD, _Hold

    path = lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    object.__setattr__(view.services, "hold_server", _Hold(refuse=True))
    view.tuning_log_offer_off_button.click()
    assert path.read_bytes() == DIST.encode()
    assert not list(path.parent.glob("mangosd.conf.*")), "a backup was made under the hold"
    assert view._tuning_owed == {}
    assert HELD in view.tuning_report.toPlainText()
    assert shown(view)


def test_turn_it_off_is_greyed_while_a_job_runs_and_back_when_it_ends(
    qapp: object, ps, tmp_path: Path
) -> None:
    lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    assert view.tuning_log_offer_off_button.isEnabled()
    view._set_busy(True, "Rebuild")
    assert shown(view) and not view.tuning_log_offer_off_button.isEnabled()
    view._set_busy(False)
    assert view.tuning_log_offer_off_button.isEnabled()


def test_a_press_that_gets_through_during_a_job_writes_nothing(
    qapp: object, ps, tmp_path: Path
) -> None:
    path = lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    view._set_busy(True, "Rebuild")
    view.turn_off_sql_log()
    assert path.read_bytes() == DIST.encode()
    assert list(path.parent.glob("*.bak")) == []


def test_keep_it_stays_live_during_a_job_because_it_writes_no_conf(
    qapp: object, ps, tmp_path: Path
) -> None:
    lay(tmp_path)
    view = view_for(tortoise(), tmp_path)
    view._set_busy(True, "Rebuild")
    assert view.tuning_log_offer_keep_button.isEnabled()


def test_a_value_the_server_reads_as_off_is_written_over_with_one(tmp_path: Path) -> None:
    path = lay(tmp_path, DIST.replace("= 0", "= on"))
    sql_log_offer.turn_off(tortoise(), tmp_path, [MANGOSD])
    assert path.read_bytes() == DIST.replace("= 0", "= 1").encode()
