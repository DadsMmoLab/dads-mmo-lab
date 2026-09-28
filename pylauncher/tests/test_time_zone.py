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


@pytest.mark.parametrize(
    ("zone", "rule"),
    [
        (OSLO, OSLO_POSIX),
        ("America/New_York", "EST5EDT,M3.2.0,M11.1.0"),
        ("Asia/Kolkata", "IST-5:30"),
        ("UTC", "UTC0"),
    ],
)
def test_a_zones_posix_rule_is_its_tz_files_own_footer(zone: str, rule: str) -> None:
    assert time_zone.posix(zone) == rule


@pytest.mark.parametrize("name", ["Mars/Olympus", "../../../etc/passwd", "", "zoneinfo/UTC"])
def test_a_name_that_is_not_a_zone_has_no_rule(name: str) -> None:
    assert time_zone.posix(name) is None


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(time, "tzset"),
    reason="measures glibc, the C library in every server image",
)
def test_every_rule_keeps_the_zones_own_clock_without_any_zone_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CMaNGOS images carry no tzdata, so their glibc gets the rule and nothing else.

    For every zone the app ships: the local time glibc gives for the POSIX rule
    with an EMPTY zone folder, against the local time it gives for the zone's
    own TZif file, every six hours for a year from now. Measured wider when the
    approach was chosen (2026-09-28, tzdata 2026d): 598 zones, 14,640 instants
    over five years, glibc 2.39 (Tortoise's ubuntu:24.04) and the jammy
    libc6 2.35 (TBC's and Vanilla's ubuntu:22.04) -- no difference anywhere.
    """
    shipped = Path(str(import_resources.files("tzdata") / "zoneinfo"))
    empty = tmp_path / "no-zoneinfo"
    empty.mkdir()
    now = int(time.time())
    stamps = range(now, now + 366 * 86400, 6 * 3600)

    def clock(tz: str, folder: Path) -> list[tuple[int, ...]]:
        os.environ["TZ"], os.environ["TZDIR"] = tz, str(folder)
        time.tzset()
        return [tuple(time.localtime(t)[:6]) + (time.localtime(t).tm_gmtoff,) for t in stamps]

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("TZDIR", str(empty))
    try:
        assert clock(OSLO, empty) == clock("UTC", empty), "control: a bare name is UTC there"
        wrong = [
            zone
            for zone in sorted(time_zone.zones())
            if clock(time_zone.posix(zone) or "", empty) != clock(zone, shipped)
        ]
    finally:
        monkeypatch.undo()
        time.tzset()
    assert wrong == []


@pytest.mark.parametrize("entry", [TBC, VANILLA, TORTOISE], ids=lambda e: e.id)
def test_a_cmangos_server_gets_the_rule_and_the_name_beside_it(entry: CatalogEntry) -> None:
    assert time_zone.line_for(entry, OSLO) == time_zone.Line(OSLO_POSIX, OSLO)


def test_a_wotlk_server_gets_the_name_its_image_looks_up() -> None:
    """AzerothCore's image installs tzdata (`apps/docker/Dockerfile`, skeleton stage)."""
    assert time_zone.line_for(WOTLK, OSLO) == time_zone.Line(OSLO)


def test_no_line_is_made_for_a_name_that_is_not_a_zone() -> None:
    assert time_zone.line_for(WOTLK, "Mars/Olympus") is None
    assert time_zone.line_for(TBC, "Mars/Olympus") is None


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
    lines = dict.fromkeys(time_zone.services(TBC), time_zone.Line(OSLO_POSIX, OSLO))
    once = time_zone.lay_over(_render(TBC, tmp_path), TBC, lines)
    assert time_zone.lay_over(once, TBC, lines) == once


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
    line = time_zone.Line(OSLO_POSIX, OSLO)

    after = time_zone.lay_over(before, TBC, dict.fromkeys(time_zone.services(TBC), line))

    spec = TBC.container_spec()
    assert after == (
        "# generated by Yu'lon — runtime settings for the native stack.\nservices:\n"
        f'  {spec.auth}:\n    environment:\n      TZ: "{OSLO_POSIX}"  # {OSLO}\n'
        f'  {spec.world}:\n    environment:\n      TZ: "{OSLO_POSIX}"  # {OSLO}\n'
    )
    assert _tz(after, TBC) == {spec.auth: OSLO_POSIX, spec.world: OSLO_POSIX}


def test_a_cmangos_channel_block_keeps_its_port(tmp_path: Path) -> None:
    before = _render(TORTOISE, tmp_path)
    assert "ports:" in before, "control: Tortoise's override publishes the channel's port"
    line = time_zone.Line(OSLO_POSIX, OSLO)

    after = time_zone.lay_over(before, TORTOISE, dict.fromkeys(time_zone.services(TORTOISE), line))

    parsed = yaml.safe_load(after)["services"]
    spec = TORTOISE.container_spec()
    assert parsed[spec.world]["ports"] == yaml.safe_load(before)["services"][spec.world]["ports"]
    assert _tz(after, TORTOISE) == {spec.auth: OSLO_POSIX, spec.world: OSLO_POSIX}


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
        ("TZ: 'Mars/Olympus'  # mine", time_zone.Line("Mars/Olympus")),
        (f'TZ: "{OSLO_POSIX}"  # {OSLO}', time_zone.Line(OSLO_POSIX, OSLO)),
        (f'TZ: "{OSLO_POSIX}"  # Mars/Olympus', time_zone.Line(OSLO_POSIX)),
        ('TZ: "${HOME}"', None),
        ('TZ: "Europe/Oslo', None),
        ('TZ: "Europe/Oslo" junk', None),
        ('TZ: ""', None),
    ],
    ids=[
        "quoted",
        "bare",
        "not-a-zone",
        "rule-and-name",
        "rule-and-a-name-that-is-not-a-zone",
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


def test_a_new_install_gets_the_zone_it_is_handed(tmp_path: Path) -> None:
    rendered = _render(TBC, tmp_path, new_install_zone=OSLO)
    spec = TBC.container_spec()
    assert _tz(rendered, TBC) == {spec.auth: OSLO_POSIX, spec.world: OSLO_POSIX}


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
