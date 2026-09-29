"""The Tuning tab's "Server time zone" route: what the file says, and one zone set in it (T171).

Real files: each install's override is the one `composegen.render()` makes on
the real templates, and each write goes through the route the tab presses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import reset_defaults, resources, server_time_zone, tuning
from yulon.catalog import composegen, time_zone
from yulon.catalog.catalog import CatalogEntry, load_catalog

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
OVERRIDE = composegen.OVERRIDE_FILE
OSLO = "Europe/Oslo"


def _installed(server_dir: Path, entry: CatalogEntry = WOTLK) -> str:
    """A Yu'lon install's compose files: a marked base and the override the install renders."""
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password="pw",
        platform_id=lambda: "linux",
    )
    (server_dir / composegen.BASE_FILE).write_text(plan.base, encoding="utf-8")
    (server_dir / OVERRIDE).write_text(plan.override, encoding="utf-8")
    return plan.override


def _override(server_dir: Path) -> str:
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


# -- read ---------------------------------------------------------------------------


def test_an_install_without_a_line_reads_as_utc(tmp_path: Path) -> None:
    _installed(tmp_path)
    reading = server_time_zone.read(WOTLK, tmp_path)
    assert reading == server_time_zone.Reading(OVERRIDE, None, time_zone.UTC)
    assert reading.shown == time_zone.UTC


@pytest.mark.parametrize("entry", [WOTLK, TBC], ids=lambda e: e.id)
def test_a_zone_the_tab_wrote_reads_back_as_that_zone(tmp_path: Path, entry: CatalogEntry) -> None:
    _installed(tmp_path, entry)
    server_time_zone.write(entry, tmp_path, OSLO)
    reading = server_time_zone.read(entry, tmp_path)
    assert (reading.zone, reading.problem, reading.note) == (OSLO, None, None)


def test_a_hand_written_value_is_shown_as_it_is(tmp_path: Path) -> None:
    text = _installed(tmp_path).replace("    environment:\n", "    environment:\n      TZ: Mars\n")
    (tmp_path / OVERRIDE).write_text(text, encoding="utf-8")
    reading = server_time_zone.read(WOTLK, tmp_path)
    assert (reading.value, reading.zone, reading.shown, reading.problem) == (
        "Mars",
        None,
        "Mars",
        None,
    )


def test_a_bare_name_on_a_cmangos_server_says_it_runs_as_utc(tmp_path: Path) -> None:
    """The CMaNGOS images have no zone files, so a hand-written `Europe/Oslo` is UTC there.

    Until its file is copied and bound: the next Repair or Apply does both.
    """
    text = _installed(tmp_path, TBC)
    world = TBC.container_spec().world
    text = text.replace("    ports:\n", "    environment:\n      TZ: Europe/Oslo\n    ports:\n")
    (tmp_path / OVERRIDE).write_text(text, encoding="utf-8")
    assert f"  {world}:\n    environment:" in text, "control: the line is under the world"

    reading = server_time_zone.read(TBC, tmp_path)

    assert (reading.value, reading.zone) == (OSLO, OSLO)
    assert reading.note is not None and "UTC" in reading.note, "no file, no bind: it is UTC"


def test_the_login_servers_own_value_is_read_too(tmp_path: Path) -> None:
    text = _installed(tmp_path) + '  ac-authserver:\n    environment:\n      TZ: "Asia/Tokyo"\n'
    (tmp_path / OVERRIDE).write_text(text, encoding="utf-8")
    reading = server_time_zone.read(WOTLK, tmp_path)
    assert (reading.zone, reading.login) == (time_zone.UTC, "Asia/Tokyo")


@pytest.mark.parametrize(
    ("setup", "said"),
    [
        ("missing", "not on disk"),
        ("foreign", "not made by Yu'lon"),
        ("twice", "Remove the extra line"),
        ("list", "list"),
        ("latin1", "not UTF-8"),
    ],
)
def test_a_file_the_tab_cannot_change_says_why(tmp_path: Path, setup: str, said: str) -> None:
    text = _installed(tmp_path)
    path = tmp_path / OVERRIDE
    if setup == "missing":
        path.unlink()
    elif setup == "foreign":
        (tmp_path / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    elif setup == "twice":
        path.write_text(
            text.replace(
                "    environment:\n", '    environment:\n      TZ: "UTC"\n      TZ: "UTC"\n'
            ),
            encoding="utf-8",
        )
    elif setup == "list":
        path.write_text(
            text + "  ac-authserver:\n    environment:\n      - TZ=UTC\n", encoding="utf-8"
        )
    else:
        path.write_bytes(text.encode("utf-8") + b"# \xe6\n")

    reading = server_time_zone.read(WOTLK, tmp_path)

    assert reading.problem is not None and said in reading.problem
    with pytest.raises(server_time_zone.TimeZoneSettingError, match=said):
        server_time_zone.write(WOTLK, tmp_path, OSLO)
    assert not list(tmp_path.glob("*.bak")), "a refusal writes nothing"


# -- write --------------------------------------------------------------------------


def test_a_wotlk_write_sets_both_servers_and_backs_the_file_up(tmp_path: Path) -> None:
    before = _installed(tmp_path)

    written = server_time_zone.write(WOTLK, tmp_path, OSLO)

    assert written.rule == "recreate" == reset_defaults.apply_rule(OVERRIDE)
    assert (written.before, written.after) == (time_zone.UTC, OSLO)
    assert written.backup is not None and written.backup.read_text(encoding="utf-8") == before
    assert tuning.backups_of(tmp_path / OVERRIDE) == (written.backup,)
    assert _override(tmp_path) == before + (
        '      TZ: "Europe/Oslo"\n  ac-authserver:\n    environment:\n      TZ: "Europe/Oslo"\n'
    )


def test_a_tbc_write_is_the_name_the_bind_and_the_copied_file(tmp_path: Path) -> None:
    _installed(tmp_path, TBC)
    server_time_zone.write(TBC, tmp_path, OSLO)
    text = _override(tmp_path)
    assert text.count('TZ: "Europe/Oslo"') == 2
    assert text.count("- ./zoneinfo:/usr/share/zoneinfo:ro\n") == 2
    assert time_zone.ready(TBC, tmp_path, OSLO)
    assert server_time_zone.read(TBC, tmp_path).note is None


def test_a_write_on_an_enforcing_install_labels_the_bind_as_the_install_did(
    tmp_path: Path,
) -> None:
    """The label is read off the install's own files (T102), never asked of the host again."""
    plan = composegen.render(
        TBC,
        tmp_path,
        templates_root=resources.installers_dir(),
        db_password="pw",
        bind_label=":z",
        platform_id=lambda: "linux",
    )
    (tmp_path / composegen.BASE_FILE).write_text(plan.base, encoding="utf-8")
    (tmp_path / OVERRIDE).write_text(plan.override, encoding="utf-8")

    server_time_zone.write(TBC, tmp_path, OSLO)

    assert _override(tmp_path).count("- ./zoneinfo:/usr/share/zoneinfo:ro,z\n") == 2


def test_a_zone_whose_file_is_gone_says_so_and_apply_brings_it_back(tmp_path: Path) -> None:
    _installed(tmp_path, TBC)
    server_time_zone.write(TBC, tmp_path, OSLO)
    (tmp_path / "zoneinfo" / "Europe" / "Oslo").unlink()

    reading = server_time_zone.read(TBC, tmp_path)
    assert reading.zone == OSLO and reading.note is not None and "UTC" in reading.note

    assert not server_time_zone.write(TBC, tmp_path, OSLO).changed, "the override already says it"
    assert (
        time_zone.ready(TBC, tmp_path, OSLO) and server_time_zone.read(TBC, tmp_path).note is None
    )


def test_a_second_zone_replaces_the_first_and_nothing_else(tmp_path: Path) -> None:
    _installed(tmp_path)
    server_time_zone.write(WOTLK, tmp_path, OSLO)
    once = _override(tmp_path)

    written = server_time_zone.write(WOTLK, tmp_path, "Asia/Tokyo")

    assert written.before == OSLO
    assert _override(tmp_path) == once.replace(OSLO, "Asia/Tokyo")


def test_the_zone_the_file_already_says_writes_nothing(tmp_path: Path) -> None:
    before = _installed(tmp_path)
    assert not server_time_zone.write(WOTLK, tmp_path, time_zone.UTC).changed
    assert _override(tmp_path) == before and not list(tmp_path.glob("*.bak"))

    server_time_zone.write(WOTLK, tmp_path, OSLO)
    after = _override(tmp_path)
    assert not server_time_zone.write(WOTLK, tmp_path, OSLO).changed
    assert _override(tmp_path) == after


def test_utc_over_a_zone_is_written_as_utc(tmp_path: Path) -> None:
    _installed(tmp_path)
    server_time_zone.write(WOTLK, tmp_path, OSLO)
    assert server_time_zone.write(WOTLK, tmp_path, time_zone.UTC).changed
    assert server_time_zone.read(WOTLK, tmp_path).zone == time_zone.UTC


def test_a_hand_written_value_is_replaced_when_the_player_picks_a_zone(tmp_path: Path) -> None:
    text = _installed(tmp_path).replace("    environment:\n", "    environment:\n      TZ: Mars\n")
    (tmp_path / OVERRIDE).write_text(text, encoding="utf-8")

    server_time_zone.write(WOTLK, tmp_path, OSLO)

    assert "Mars" not in _override(tmp_path)
    assert server_time_zone.read(WOTLK, tmp_path).zone == OSLO


@pytest.mark.parametrize("zone", ["Mars/Olympus", "", "Europe/Oslo\n"])
def test_a_name_that_is_not_a_zone_is_refused(tmp_path: Path, zone: str) -> None:
    before = _installed(tmp_path)
    with pytest.raises(server_time_zone.TimeZoneSettingError, match="not a time zone"):
        server_time_zone.write(WOTLK, tmp_path, zone)
    assert _override(tmp_path) == before


def test_the_question_says_where_what_and_what_it_owes() -> None:
    reading = server_time_zone.Reading(OVERRIDE)
    wotlk = server_time_zone.question(WOTLK, reading, OSLO)
    assert "Europe/Oslo" in wotlk and "ac-worldserver" in wotlk and "ac-authserver" in wotlk
    assert "RECREATED" in wotlk and "Reset to default keeps" in wotlk
    assert "zoneinfo" not in wotlk
    assert "zoneinfo/Europe/Oslo" in server_time_zone.question(TBC, reading, OSLO)


def test_every_game_yulon_makes_compose_files_for_has_the_route(tmp_path: Path) -> None:
    for entry in CATALOG.games:
        assert server_time_zone.time_zone_route(entry, tmp_path) is not None, entry.id
