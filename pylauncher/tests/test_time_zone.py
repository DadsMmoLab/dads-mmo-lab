"""The server time zone's data, its one line in the override, and how a render carries it (T171).

Real data throughout: the zone names and the POSIX rules come from the `tzdata`
package the app ships, the overrides from `composegen.render()` on the real
templates, and each result is read back with a YAML parser, as compose reads it.
"""

from __future__ import annotations

import os
import sys
import time
from importlib import resources as import_resources
from pathlib import Path
from typing import Any

import pytest
import yaml

from yulon import resources
from yulon.catalog import composegen, time_zone
from yulon.catalog.catalog import CatalogEntry, load_catalog

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")
TORTOISE = CATALOG.get("wow-tortoise")
OSLO = "Europe/Oslo"
OSLO_POSIX = "CET-1CEST,M3.5.0,M10.5.0/3"
REAL_HOST_ZONE = time_zone.host_zone
"""Taken at import, before conftest pins it for each test."""


def _render(entry: CatalogEntry, server_dir: Path, **kw: Any) -> str:
    return composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password="pw",
        platform_id=lambda: "linux",
        **kw,
    ).override


def _parsed(text: str, entry: CatalogEntry) -> dict[str, Any]:
    """Each of the two services' environment as compose would read it."""
    services = (yaml.safe_load(text) or {}).get("services") or {}
    spec = entry.container_spec()
    return {name: (services.get(name) or {}).get("environment") for name in (spec.auth, spec.world)}


def _tz(text: str, entry: CatalogEntry) -> dict[str, str | None]:
    return {name: (env or {}).get("TZ") for name, env in _parsed(text, entry).items()}


# -- the data ------------------------------------------------------------------------


def test_the_zone_names_are_the_shipped_tz_databases() -> None:
    names = time_zone.zones()
    assert {OSLO, "UTC", "America/New_York", "Asia/Kolkata", "Europe/Kiev"} <= names
    assert "Mars/Olympus" not in names
    assert len(names) > 500


def test_the_list_a_player_picks_from_is_one_place_per_country_region() -> None:
    """`zone.tab`: Oslo is there for Norway; old aliases and the Etc offsets are not."""
    picker = time_zone.picker_zones()
    assert OSLO in picker and "America/Argentina/Buenos_Aires" in picker
    assert "US/Eastern" not in picker and "Etc/GMT+5" not in picker and "UTC" not in picker
    assert list(picker) == sorted(picker)
    assert set(picker) <= time_zone.zones()


@pytest.mark.parametrize("zone", [OSLO, "America/New_York", "Australia/Sydney", "UTC"])
def test_a_zones_file_is_the_shipped_databases_own(zone: str) -> None:
    shipped = import_resources.files("tzdata") / "zoneinfo"
    for part in zone.split("/"):
        shipped = shipped / part
    assert time_zone.zone_file(zone) == shipped.read_bytes()


@pytest.mark.parametrize("name", ["Mars/Olympus", "../../../etc/passwd", "", "zoneinfo/UTC"])
def test_a_name_that_is_not_a_zone_has_no_file(name: str) -> None:
    assert time_zone.zone_file(name) is None


@pytest.mark.parametrize("entry", [WOTLK, TBC, VANILLA, TORTOISE], ids=lambda e: e.id)
def test_every_game_gets_the_zones_name(entry: CatalogEntry) -> None:
    """The name glibc looks up: in the image's tzdata (WotLK) or in the bound copy (CMaNGOS)."""
    assert time_zone.line_for(entry, OSLO) == time_zone.Line(OSLO)


def test_only_the_cmangos_images_need_the_files_brought() -> None:
    """AzerothCore's image installs tzdata (`apps/docker/Dockerfile`, skeleton stage)."""
    assert [time_zone.needs_files(e) for e in (WOTLK, TBC, VANILLA, TORTOISE)] == [
        False,
        True,
        True,
        True,
    ]


def test_no_line_is_made_for_a_name_that_is_not_a_zone() -> None:
    assert time_zone.line_for(WOTLK, "Mars/Olympus") is None
    assert time_zone.line_for(TBC, "Mars/Olympus") is None


def test_the_zone_files_bind_is_read_only_and_labelled_like_every_other() -> None:
    assert time_zone.bind_line("") == "./zoneinfo:/usr/share/zoneinfo:ro"
    assert time_zone.bind_line(":z") == "./zoneinfo:/usr/share/zoneinfo:ro,z"
    assert composegen.bind_label_of("    - " + time_zone.bind_line(":z")) == ":z"
    assert composegen.bind_label_of("    - " + time_zone.bind_line("")) == ""


# -- the zone files in the server folder ---------------------------------------------


def _tbc_override(zone: str = OSLO) -> str:
    lines = dict.fromkeys(time_zone.services(TBC), time_zone.Line(zone))
    return time_zone.lay_over("services: {}\n", TBC, lines)


def test_place_copies_the_named_zone_from_the_apps_own_database(tmp_path: Path) -> None:
    written = time_zone.place(TBC, tmp_path, _tbc_override())

    target = tmp_path / "zoneinfo" / "Europe" / "Oslo"
    assert written == (target,)
    assert target.read_bytes() == time_zone.zone_file(OSLO)
    assert time_zone.ready(TBC, tmp_path, OSLO)
    assert [p.name for p in (tmp_path / "zoneinfo").rglob("*")] == ["Europe", "Oslo"]


def test_place_copies_again_only_what_differs(tmp_path: Path) -> None:
    """A rule a Yu'lon update brings reaches the server; an equal file is left alone."""
    time_zone.place(TBC, tmp_path, _tbc_override())
    target = tmp_path / "zoneinfo" / "Europe" / "Oslo"
    assert time_zone.place(TBC, tmp_path, _tbc_override()) == ()
    target.write_bytes(b"TZif2 an older rule")
    assert not time_zone.ready(TBC, tmp_path, OSLO)

    assert time_zone.place(TBC, tmp_path, _tbc_override()) == (target,)
    assert target.read_bytes() == time_zone.zone_file(OSLO)


def test_place_brings_nothing_for_wotlk_or_for_a_value_that_is_not_a_zone(
    tmp_path: Path,
) -> None:
    wotlk = time_zone.lay_over(
        "services:\n", WOTLK, dict.fromkeys(time_zone.services(WOTLK), time_zone.Line(OSLO))
    )
    assert time_zone.place(WOTLK, tmp_path, wotlk) == ()
    assert time_zone.place(TBC, tmp_path, _tbc_override("Mars/Olympus")) == ()
    assert not (tmp_path / "zoneinfo").exists()


def test_place_refuses_a_zoneinfo_that_is_not_a_folder(tmp_path: Path) -> None:
    (tmp_path / "zoneinfo").write_text("not a folder", encoding="utf-8")
    with pytest.raises(time_zone.TimeZoneError):
        time_zone.place(TBC, tmp_path, _tbc_override())


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(time, "tzset"),
    reason="measures glibc, the C library in every server image",
)
def test_glibc_reads_the_placed_folder_as_the_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """glibc looks a name up in the folder `place()` makes; without it the name is UTC.

    The images' own glibc (2.35, 2.39) did the same with this folder bound at
    `/usr/share/zoneinfo`, measured on a Linux test box 2026-09-28 in all three built
    CMaNGOS images: `TZ=Europe/Oslo` gave CEST +0200 with the bind, +0000
    without it.
    """
    time_zone.place(TBC, tmp_path, _tbc_override())
    july = 1814443200  # 2027-07-01 12:00 UTC

    def offset(folder: Path) -> int:
        os.environ["TZ"] = "UTC"  # glibc keeps a zone it already read under the same TZ
        time.tzset()
        os.environ["TZ"], os.environ["TZDIR"] = OSLO, str(folder)
        time.tzset()
        return time.localtime(july).tm_gmtoff

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("TZDIR", str(tmp_path))
    try:
        placed, bare = offset(tmp_path / "zoneinfo"), offset(tmp_path)
    finally:
        monkeypatch.undo()
        time.tzset()
    assert (placed, bare) == (7200, 0)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Qt reads $TZ on Linux")
def test_this_computers_zone_is_what_qt_says_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time_zone, "host_zone", REAL_HOST_ZONE)
    monkeypatch.setenv("TZ", "America/New_York")
    assert time_zone.host_zone() == "America/New_York"


def test_a_zone_qt_cannot_name_is_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QByteArray, QTimeZone

    monkeypatch.setattr(time_zone, "host_zone", REAL_HOST_ZONE)
    monkeypatch.setattr(QTimeZone, "systemTimeZoneId", lambda: QByteArray(b"Mars/Olympus"))
    assert time_zone.host_zone() == "UTC"

    def broken() -> QByteArray:
        raise RuntimeError("no zone")

    monkeypatch.setattr(QTimeZone, "systemTimeZoneId", broken)
    assert time_zone.host_zone() == "UTC"


@pytest.mark.parametrize("name", ["Etc/UTC", "Etc/Zulu", "UCT", "GMT", "Etc/Greenwich"])
def test_a_computer_on_a_utc_alias_is_in_utc(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Cold review NIT: `Etc/UTC` is UTC, so a new server there writes no line."""
    from PySide6.QtCore import QByteArray, QTimeZone

    monkeypatch.setattr(time_zone, "host_zone", REAL_HOST_ZONE)
    monkeypatch.setattr(QTimeZone, "systemTimeZoneId", lambda: QByteArray(name.encode()))
    assert time_zone.host_zone() == "UTC"
    assert time_zone.for_render(WOTLK, None, installed=False, new_zone=name) == {}


def test_the_suite_pins_this_computers_zone_to_utc() -> None:
    """conftest: a render must not depend on the zone of the box running the tests."""
    assert time_zone.host_zone() == "UTC"


# -- the line in the override --------------------------------------------------------


def test_a_wotlk_zone_is_one_line_in_each_servers_environment(tmp_path: Path) -> None:
    """The world's line after its last key; the login server's block after the world's."""
    before = _render(WOTLK, tmp_path)
    line = time_zone.Line(OSLO)

    after = time_zone.lay_over(before, WOTLK, dict.fromkeys(time_zone.services(WOTLK), line))

    assert after == before + (
        '      TZ: "Europe/Oslo"\n  ac-authserver:\n    environment:\n      TZ: "Europe/Oslo"\n'
    )
    assert _tz(after, WOTLK) == {"ac-authserver": OSLO, "ac-worldserver": OSLO}


def test_laying_the_same_zone_twice_is_laying_it_once(tmp_path: Path) -> None:
    lines = dict.fromkeys(time_zone.services(TBC), time_zone.Line(OSLO))
    once = time_zone.lay_over(_render(TBC, tmp_path), TBC, lines, label=":z")
    assert time_zone.lay_over(once, TBC, lines, label=":z") == once


def test_a_new_zone_replaces_the_old_line_where_it_stands(tmp_path: Path) -> None:
    """A player's own line keeps its place, its indent and the notes around it."""
    text = _render(WOTLK, tmp_path).replace(
        "    environment:\n",
        "    environment:\n      # my note\n      TZ: 'Europe/Oslo'   # hand-added\n",
    )
    after = time_zone.lay_over(text, WOTLK, {"ac-worldserver": time_zone.Line("Asia/Tokyo")})
    assert after == text.replace("TZ: 'Europe/Oslo'   # hand-added", "TZ: 'Asia/Tokyo'")
    assert _tz(after, WOTLK)["ac-worldserver"] == "Asia/Tokyo"


def test_a_crlf_override_gets_crlf_lines(tmp_path: Path) -> None:
    text = _render(WOTLK, tmp_path).replace("\n", "\r\n")
    after = time_zone.lay_over(
        text, WOTLK, dict.fromkeys(time_zone.services(WOTLK), time_zone.Line(OSLO))
    )
    assert "\n" not in after.replace("\r\n", "")
    assert _tz(after, WOTLK) == {"ac-authserver": OSLO, "ac-worldserver": OSLO}


def test_a_cmangos_override_with_no_services_gets_both() -> None:
    """`services: {}` is what the template renders when the base file publishes the port."""
    before = "# generated by Yu'lon — runtime settings for the native stack.\nservices: {}\n"

    after = time_zone.lay_over(
        before, TBC, dict.fromkeys(time_zone.services(TBC), time_zone.Line(OSLO))
    )

    spec = TBC.container_spec()
    block = '    environment:\n      TZ: "Europe/Oslo"\n    volumes:\n'
    block += "      - ./zoneinfo:/usr/share/zoneinfo:ro\n"
    assert after == (
        "# generated by Yu'lon — runtime settings for the native stack.\nservices:\n"
        f"  {spec.auth}:\n{block}  {spec.world}:\n{block}"
    )
    assert _tz(after, TBC) == {spec.auth: OSLO, spec.world: OSLO}


def test_a_cmangos_channel_block_keeps_its_port_and_gains_the_bind(tmp_path: Path) -> None:
    before = _render(TORTOISE, tmp_path)
    assert "ports:" in before, "control: Tortoise's override publishes the channel's port"
    lines = dict.fromkeys(time_zone.services(TORTOISE), time_zone.Line(OSLO))

    after = time_zone.lay_over(before, TORTOISE, lines, label=":z")

    parsed = yaml.safe_load(after)["services"]
    spec = TORTOISE.container_spec()
    assert parsed[spec.world]["ports"] == yaml.safe_load(before)["services"][spec.world]["ports"]
    assert _tz(after, TORTOISE) == {spec.auth: OSLO, spec.world: OSLO}
    for name in (spec.auth, spec.world):
        assert parsed[name]["volumes"] == ["./zoneinfo:/usr/share/zoneinfo:ro,z"]


def test_a_bind_of_the_folder_already_there_is_replaced_not_doubled() -> None:
    text = (
        "services:\n  tbc-mangosd:\n    volumes:\n      - ./x:/x\n"
        "      - ./old:/usr/share/zoneinfo\n"
    )
    after = time_zone.lay_over(text, TBC, {"tbc-mangosd": time_zone.Line(OSLO)})
    assert yaml.safe_load(after)["services"]["tbc-mangosd"]["volumes"] == [
        "./x:/x",
        "./zoneinfo:/usr/share/zoneinfo:ro",
    ]


def test_a_value_that_is_not_a_zone_gets_no_bind() -> None:
    """A hand-written rule is kept as it is, and there is no file to show for it."""
    after = time_zone.lay_over(
        "services:\n", TBC, {"tbc-mangosd": time_zone.Line("CET-1CEST,M3.5.0,M10.5.0/3")}
    )
    assert "volumes" not in after and "zoneinfo" not in after


@pytest.mark.parametrize(
    "environment",
    [
        "    environment:\n      - TZ=Europe/Oslo\n",
        "    environment: {}\n",
        '    environment:\n      TZ: "a"\n      TZ: "b"\n',
    ],
    ids=["a-list", "inline", "twice"],
)
def test_an_environment_this_does_not_rewrite_is_refused(environment: str) -> None:
    text = f"services:\n  ac-worldserver:\n{environment}"
    with pytest.raises(time_zone.TimeZoneError):
        time_zone.lay_over(text, WOTLK, {"ac-worldserver": time_zone.Line(OSLO)})


# -- what a rewrite carries ---------------------------------------------------------


def _env(value_line: str, service: str = "ac-worldserver") -> str:
    return f'services:\n  {service}:\n    environment:\n      A: "1"\n      {value_line}\n'


@pytest.mark.parametrize(
    ("value_line", "carried"),
    [
        ('TZ: "Europe/Oslo"', time_zone.Line(OSLO)),
        ("TZ: Europe/Oslo", time_zone.Line(OSLO)),
        ("TZ: 'Mars/Olympus'  # mine", time_zone.Line("Mars/Olympus", "mine")),
        (f'TZ: "{OSLO_POSIX}"  # {OSLO}', time_zone.Line(OSLO_POSIX, OSLO)),
        ("TZ: Europe/Oslo # set by me", time_zone.Line(OSLO, "set by me")),
        ('TZ: "${HOME}"', None),
        ('TZ: "Europe/Oslo', None),
        ('TZ: "Europe/Oslo" junk', None),
        ('TZ: ""', None),
    ],
    ids=[
        "quoted",
        "bare",
        "not-a-zone",
        "a-hand-rule-and-its-note",
        "a-note",
        "interpolated",
        "unclosed-quote",
        "not-a-comment-after",
        "empty",
    ],
)
def test_a_line_is_carried_only_as_something_the_file_can_say_again(
    value_line: str, carried: time_zone.Line | None
) -> None:
    got = time_zone.carried(_env(value_line), WOTLK)
    assert got == ({} if carried is None else {"ac-worldserver": carried})


def test_a_zone_set_twice_is_not_carried() -> None:
    """Which line the container reads is not knowable here (T117's rule for the bot count)."""
    assert time_zone.carried(_env('TZ: "UTC"\n      TZ: "Europe/Oslo"'), WOTLK) == {}


def test_each_server_carries_its_own_line() -> None:
    text = _env('TZ: "Europe/Oslo"') + '  ac-authserver:\n    environment:\n      TZ: "UTC"\n'
    assert time_zone.carried(text, WOTLK) == {
        "ac-worldserver": time_zone.Line(OSLO),
        "ac-authserver": time_zone.Line("UTC"),
    }


def test_what_a_render_lays_over() -> None:
    kept = _env('TZ: "Asia/Tokyo"')
    tokyo = {"ac-worldserver": time_zone.Line("Asia/Tokyo")}
    both = dict.fromkeys(time_zone.services(WOTLK), time_zone.Line(OSLO))
    assert time_zone.for_render(WOTLK, kept, installed=True, new_zone=OSLO) == tokyo
    assert time_zone.for_render(WOTLK, kept, installed=False, new_zone=OSLO) == tokyo
    assert time_zone.for_render(WOTLK, _env('B: "2"'), installed=True, new_zone=OSLO) == {}
    assert time_zone.for_render(WOTLK, None, installed=False, new_zone=OSLO) == both
    assert time_zone.for_render(WOTLK, None, installed=False, new_zone="UTC") == {}
    assert time_zone.for_render(WOTLK, None, installed=False, new_zone=None) == {}
    assert time_zone.for_render(WOTLK, None, installed=False, new_zone="Mars/Olympus") == {}


# -- through composegen.render() -----------------------------------------------------


def test_a_render_carries_the_zone_off_the_override_it_replaces(tmp_path: Path) -> None:
    (tmp_path / composegen.OVERRIDE_FILE).write_text(_env('TZ: "Asia/Tokyo"'), encoding="utf-8")
    assert _tz(_render(WOTLK, tmp_path), WOTLK) == {
        "ac-authserver": None,
        "ac-worldserver": "Asia/Tokyo",
    }


def test_a_new_install_gets_the_zone_it_is_handed_and_the_bind_its_label(tmp_path: Path) -> None:
    rendered = _render(TBC, tmp_path, new_install_zone=OSLO, bind_label=":z")
    spec = TBC.container_spec()
    assert _tz(rendered, TBC) == {spec.auth: OSLO, spec.world: OSLO}
    assert composegen.bind_label_of(rendered) == ":z"
    assert not (tmp_path / "zoneinfo").exists(), "a render writes nothing; the writer places"


def test_an_installed_server_with_no_zone_is_not_handed_one(tmp_path: Path) -> None:
    """Owner: an existing server keeps what it has -- UTC, today -- until the player changes it."""
    (tmp_path / composegen.BASE_FILE).write_text(
        composegen.GENERATED_MARKER + "\nservices: {}\n", encoding="utf-8"
    )
    assert _tz(_render(WOTLK, tmp_path, new_install_zone=OSLO), WOTLK) == {
        "ac-authserver": None,
        "ac-worldserver": None,
    }


def test_a_render_without_a_zone_is_the_file_it_always_was(tmp_path: Path) -> None:
    """A16: nothing to carry and nothing handed in renders the pre-T171 bytes."""
    rendered = _render(WOTLK, tmp_path)
    assert "TZ" not in rendered and "ac-authserver" not in rendered
    assert "TZ" not in _render(TBC, tmp_path)


def test_a_carried_value_is_refused_what_composegen_refuses() -> None:
    """`time_zone` keeps its own copy because `composegen` imports it; the two must agree."""
    assert time_zone.UNSAFE == composegen._UNSAFE_SCALAR_CHARS


def test_a_four_space_file_gets_four_space_blocks() -> None:
    """YAML refuses keys that do not line up, so new lines take the file's own indent step."""
    text = "services:\n    ac-worldserver:\n        image: x\n"
    lines = dict.fromkeys(time_zone.services(WOTLK), time_zone.Line(OSLO))

    after = time_zone.lay_over(text, WOTLK, lines)

    assert after == (
        "services:\n    ac-worldserver:\n        image: x\n"
        '        environment:\n            TZ: "Europe/Oslo"\n'
        '    ac-authserver:\n        environment:\n            TZ: "Europe/Oslo"\n'
    )
    assert _tz(after, WOTLK) == {"ac-authserver": OSLO, "ac-worldserver": OSLO}


# -- round 3: nothing is written through a link ------------------------------------


def test_a_linked_area_folder_is_refused_and_nothing_is_written_outside(tmp_path: Path) -> None:
    server, outside = tmp_path / "server", tmp_path / "outside"
    (server / "zoneinfo").mkdir(parents=True)
    outside.mkdir()
    (server / "zoneinfo" / "Europe").symlink_to(outside, target_is_directory=True)

    with pytest.raises(time_zone.TimeZoneError, match=r"zoneinfo/Europe is a link"):
        time_zone.place(TBC, server, _tbc_override())

    assert list(outside.iterdir()) == []


def test_a_linked_zoneinfo_folder_is_refused(tmp_path: Path) -> None:
    server, outside = tmp_path / "server", tmp_path / "outside"
    server.mkdir()
    outside.mkdir()
    (server / "zoneinfo").symlink_to(outside, target_is_directory=True)

    with pytest.raises(time_zone.TimeZoneError, match=r"server/zoneinfo is a link"):
        time_zone.place(TBC, server, _tbc_override())

    assert list(outside.iterdir()) == []


def test_a_link_where_the_zone_file_belongs_is_refused_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not yours")
    (tmp_path / "zoneinfo" / "Europe").mkdir(parents=True)
    (tmp_path / "zoneinfo" / "Europe" / "Oslo").symlink_to(outside)

    with pytest.raises(time_zone.TimeZoneError, match=r"Europe/Oslo is a link"):
        time_zone.place(TBC, tmp_path, _tbc_override())

    assert outside.read_bytes() == b"not yours"


def test_a_link_left_at_an_old_temporary_name_is_not_followed(tmp_path: Path) -> None:
    """Round 2 wrote `.Oslo.yulon-tmp` by name; a link planted there would have been written."""
    outside = tmp_path / "outside.txt"
    folder = tmp_path / "zoneinfo" / "Europe"
    folder.mkdir(parents=True)
    (folder / ".Oslo.yulon-tmp").symlink_to(outside)

    assert time_zone.place(TBC, tmp_path, _tbc_override()) == (folder / "Oslo",)

    assert not outside.exists(), "the link's target was written"
    assert (folder / "Oslo").read_bytes() == time_zone.zone_file(OSLO)
    assert sorted(p.name for p in folder.iterdir()) == [".Oslo.yulon-tmp", "Oslo"]


@pytest.mark.parametrize("name", ["../evil", "Europe/../../evil", "/etc/evil", "Europe//Oslo"])
def test_a_name_that_would_leave_the_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Names come from the database's own list; the rule holds even if one did not."""
    monkeypatch.setattr(time_zone, "zones", lambda: frozenset({name}))
    monkeypatch.setattr(time_zone, "zone_file", lambda zone: b"TZif2 planted")
    (tmp_path / "server").mkdir()
    override = f'services:\n  tbc-mangosd:\n    environment:\n      TZ: "{name}"\n'

    with pytest.raises(time_zone.TimeZoneError, match="not a zone name"):
        time_zone.place(TBC, tmp_path / "server", override)

    assert not (tmp_path / "evil").exists() and not (tmp_path / "server" / "zoneinfo").exists()


# -- round 4: a Windows junction is a link, and zoneinfo must be the server's own --


def _reparse_at(monkeypatch: pytest.MonkeyPatch, junction: Path) -> None:
    """`lstat` as Windows answers it for a directory junction at `junction`, and only there."""
    import stat
    from types import SimpleNamespace

    real = os.lstat

    def lstat(path: Any) -> Any:
        found = real(path)
        if Path(path) == junction:
            return SimpleNamespace(
                st_mode=found.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return found

    monkeypatch.setattr(time_zone, "_lstat", lstat)


@pytest.mark.parametrize("where", ["zoneinfo", "zoneinfo/Europe"])
def test_a_junction_is_refused_like_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """`Path.is_symlink()` does not see a junction; its reparse attribute does (Codex, round 4)."""
    (tmp_path / where).mkdir(parents=True)
    _reparse_at(monkeypatch, tmp_path / where)
    assert not (tmp_path / where).is_symlink(), "control: the stand-in is not a symlink"

    with pytest.raises(time_zone.TimeZoneError, match=rf"{where} is a link"):
        time_zone.place(TBC, tmp_path, _tbc_override())

    assert not (tmp_path / "zoneinfo" / "Europe" / "Oslo").exists()


def test_a_zoneinfo_that_resolves_outside_the_server_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """However it gets there -- a reparse point no check names -- the resolution decides."""
    server, elsewhere = tmp_path / "server", tmp_path / "elsewhere"
    (server / "zoneinfo").mkdir(parents=True)
    elsewhere.mkdir()
    real = Path.resolve

    def resolve(path: Path) -> Path:
        return elsewhere if path == server / "zoneinfo" else real(path)

    monkeypatch.setattr(time_zone, "_real", resolve)

    with pytest.raises(time_zone.TimeZoneError, match="not into the server folder"):
        time_zone.place(TBC, server, _tbc_override())

    assert list(elsewhere.iterdir()) == [] and list((server / "zoneinfo").iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_placed_zone_file_is_readable_by_every_user(tmp_path: Path) -> None:
    """`mkstemp` makes 0600; a server running as another uid would read UTC (live, Vanilla)."""
    import stat

    (placed,) = time_zone.place(TBC, tmp_path, _tbc_override())

    assert stat.S_IMODE(placed.stat().st_mode) == 0o644
