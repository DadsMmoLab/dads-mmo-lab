"""Every rewriter of the compose override keeps the server's time zone (T171).

The zone is one `TZ` line in the world's and the login server's environment in
`docker-compose.override.yml` -- a player's own, written by hand before T171,
or the Tuning tab's since. Until T171 every writer that renders that file again
dropped it: the install's `generate-compose` stage (a Repair, and Update to
latest's put-back), the command channel's Enable press, and Reset to default
(owner decision 2026-09-28: Reset to default does NOT reset the zone). The
channel's rollback put back a backup from before the first press. Each path is
driven for real below, on the files the install itself writes, and the zone is
read back with a YAML parser, as compose would read it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support_native import Recorder
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import context as cm_context
from tests.test_families_cmangos import engine as cm_engine
from yulon import bot_population, channel_setup, reset_defaults, resources, server_time_zone
from yulon.catalog import composegen, native, time_zone
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallOptions

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
OVERRIDE = composegen.OVERRIDE_FILE
BACKUP = f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}"
SOAP_ON = 'AC_SOAP_ENABLED: "1"'
OSLO = "Europe/Oslo"
OSLO_POSIX = "CET-1CEST,M3.5.0,M10.5.0/3"


def _linux() -> str:
    return "linux"


def _engine(**seams: Any) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        WOTLK,
        seams=native.Seams(
            platform_id=_linux,
            selinux_enforcing=lambda: False,
            fs_type=lambda path: "ext4",
            **seams,
        ),
    )


def _state(server_dir: Path) -> native.InstallState:
    return native.InstallState(
        game_id=WOTLK.id,
        install_id=composegen.install_id(server_dir, platform_id=_linux),
        family="azerothcore",
    )


def _context(server_dir: Path) -> native.StageContext:
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=_state(server_dir),
        cancel=None,
        secrets=native.Secrets(db_password=WOTLK.install.db_password(server_dir)),
    )


def _generate(server_dir: Path, **seams: Any) -> str:
    """WotLK's `generate-compose` stage, as an install AND a Repair run it."""
    list(_engine(**seams).stage_generate_compose(_context(server_dir)))
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _generate_tbc(server_dir: Path, **seams: Any) -> str:
    """TBC's `generate-compose` stage: the CMaNGOS family binds the same body."""
    list(cm_engine(Recorder(), **seams).stage_generate_compose(cm_context(server_dir)))
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _press(server_dir: Path) -> str:
    channel_setup.enable(
        WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False
    )
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _tab(server_dir: Path) -> channel_setup.InstallChannel:
    def never(*_args: object) -> object:
        raise AssertionError("a rollback creates no account and opens no channel")

    return channel_setup.InstallChannel(
        WOTLK,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir, platform_id=_linux),
        create=never,
        channel_for=never,
        db_password=WOTLK.install.db_password(server_dir),
    )


def _zones(server_dir: Path, entry: CatalogEntry = WOTLK) -> dict[str, str | None]:
    """`TZ` in the world's and the login server's environment, as compose parses the file."""
    parsed = yaml.safe_load((server_dir / OVERRIDE).read_text(encoding="utf-8")) or {}
    services = parsed.get("services") or {}
    spec = entry.container_spec()
    out: dict[str, str | None] = {}
    for name in (spec.auth, spec.world):
        env = (services.get(name) or {}).get("environment") or {}
        out[name] = env.get("TZ")
    return out


def _set(text: str, entry: CatalogEntry, zone: str = OSLO) -> str:
    """`text` as the Tuning tab's writer leaves it for `zone`: the expected bytes."""
    line = time_zone.line_for(entry, zone)
    assert line is not None
    return time_zone.lay_over(text, entry, dict.fromkeys(time_zone.services(entry), line))


def _hand_write(server_dir: Path, value: str, entry: CatalogEntry = WOTLK) -> None:
    """A player's own `TZ` line under the world's `environment:`, as the Discord reports do it."""
    path = server_dir / OVERRIDE
    text = path.read_text(encoding="utf-8")
    world = entry.container_spec().world
    if f"  {world}:\n    environment:\n" in text:
        text = text.replace("    environment:\n", f'    environment:\n      TZ: "{value}"\n', 1)
    else:
        text = text.replace("services: {}\n", "services:\n")
        text += f'  {world}:\n    environment:\n      TZ: "{value}"\n'
    path.write_text(text, encoding="utf-8")
    assert _zones(server_dir, entry)[world] == value, "control: compose reads the hand line"


# -- WotLK: a player's own line, today --------------------------------------------


def test_a_repair_keeps_a_hand_written_time_zone(tmp_path: Path) -> None:
    _generate(tmp_path)
    _hand_write(tmp_path, "Europe/Oslo")

    _generate(tmp_path)

    assert _zones(tmp_path)["ac-worldserver"] == "Europe/Oslo"


def test_update_to_latests_put_back_keeps_a_hand_written_time_zone(tmp_path: Path) -> None:
    """`_rewrite_what_we_own()` is the Update route's put-back; it runs the stage body."""
    _generate(tmp_path)
    _hand_write(tmp_path, "Europe/Oslo")

    list(_engine()._rewrite_what_we_own(tmp_path, InstallOptions(), _state(tmp_path)))

    assert _zones(tmp_path)["ac-worldserver"] == "Europe/Oslo"


def test_the_channel_enable_press_keeps_a_hand_written_time_zone(tmp_path: Path) -> None:
    _generate(tmp_path)
    _hand_write(tmp_path, "Europe/Oslo")

    pressed = _press(tmp_path)

    assert SOAP_ON in pressed
    assert _zones(tmp_path)["ac-worldserver"] == "Europe/Oslo"


def test_a_zone_set_while_the_channel_was_on_survives_its_rollback(tmp_path: Path) -> None:
    """The backup is the file before the FIRST press, so it holds no zone set after it."""
    _generate(tmp_path)
    before = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    _press(tmp_path)
    server_time_zone.write(WOTLK, tmp_path, OSLO)

    assert _tab(tmp_path).roll_back()

    after = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert SOAP_ON not in after
    assert _zones(tmp_path) == {"ac-authserver": OSLO, "ac-worldserver": OSLO}
    assert not (tmp_path / BACKUP).exists(), "the rollback recognised its own press"
    assert after == _set(before, WOTLK), "the pre-press file, and the zone, nothing else"


def test_reset_to_default_keeps_the_time_zone(tmp_path: Path) -> None:
    """Owner decision 2026-09-28: the reset puts the file back, the zone stays."""
    installed = _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    _hand_write(tmp_path, "Europe/Oslo")

    texts, reasons = reset_defaults.default_texts(
        WOTLK,
        tmp_path,
        [OVERRIDE],
        seams=reset_defaults.Seams(
            image_present=lambda ref: False,
            copy_from_image=lambda *args: None,
            bind_label=lambda path: "",
            platform_id=_linux,
        ),
    )

    assert reasons == {}
    (tmp_path / OVERRIDE).write_text(texts[OVERRIDE], encoding="utf-8")
    assert _zones(tmp_path)["ac-worldserver"] == "Europe/Oslo"
    assert 'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "500"' in texts[OVERRIDE], "the count IS reset"
    assert texts[OVERRIDE] != installed


def test_the_bot_count_box_keeps_the_time_zone(tmp_path: Path) -> None:
    _generate(tmp_path)
    _hand_write(tmp_path, "Europe/Oslo")

    bot_population.write(WOTLK, tmp_path, 60)

    assert _zones(tmp_path)["ac-worldserver"] == "Europe/Oslo"


# -- CMaNGOS: the same file, the same rule -----------------------------------------


def test_a_tbc_repair_keeps_a_time_zone_in_its_override(tmp_path: Path) -> None:
    _generate_tbc(tmp_path)
    _hand_write(tmp_path, OSLO_POSIX, TBC)

    _generate_tbc(tmp_path)

    assert _zones(tmp_path, TBC)[TBC.container_spec().world] == OSLO_POSIX


@pytest.mark.parametrize("value", ["Mars/Olympus", "<+0530>-5:30"])
def test_a_value_not_in_any_list_is_kept_as_it_is(tmp_path: Path, value: str) -> None:
    """Owner: a hand-written TZ is kept, not overwritten, until the player changes it."""
    _generate(tmp_path)
    _hand_write(tmp_path, value)

    _generate(tmp_path)

    assert _zones(tmp_path)["ac-worldserver"] == value


# -- a zone set on the Tuning tab, through every rewriter --------------------------


def _tab_set(server_dir: Path, entry: CatalogEntry = WOTLK, zone: str = OSLO) -> str:
    written = server_time_zone.write(entry, server_dir, zone)
    assert written.changed, "control: the tab wrote the zone"
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def test_a_repair_renders_the_file_the_tab_wrote(tmp_path: Path) -> None:
    _generate(tmp_path)
    tabbed = _tab_set(tmp_path)

    assert _generate(tmp_path) == tabbed


def test_a_tbc_repair_renders_the_file_the_tab_wrote(tmp_path: Path) -> None:
    _generate_tbc(tmp_path)
    tabbed = _tab_set(tmp_path, TBC)
    spec = TBC.container_spec()
    assert _zones(tmp_path, TBC) == {spec.auth: OSLO_POSIX, spec.world: OSLO_POSIX}

    assert _generate_tbc(tmp_path) == tabbed


def test_the_zone_the_count_and_the_channel_ride_a_repair_together(tmp_path: Path) -> None:
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    _tab_set(tmp_path)
    pressed = _press(tmp_path)

    assert _generate(tmp_path) == pressed
    assert SOAP_ON in pressed and 'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "60"' in pressed
    assert _zones(tmp_path) == {"ac-authserver": OSLO, "ac-worldserver": OSLO}


def test_the_channel_on_and_off_again_keeps_a_zone_set_before_it(tmp_path: Path) -> None:
    _generate(tmp_path)
    tabbed = _tab_set(tmp_path)
    _press(tmp_path)

    assert _tab(tmp_path).roll_back()

    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == tabbed
    assert not (tmp_path / BACKUP).exists()


def test_a_reset_keeps_a_zone_the_tab_set(tmp_path: Path) -> None:
    installed = _generate(tmp_path)
    tabbed = _tab_set(tmp_path)
    (tmp_path / OVERRIDE).write_text(
        tabbed.replace("    environment:\n", '    environment:\n      MY_OWN: "x"\n'),
        encoding="utf-8",
    )

    report = reset_defaults.reset(
        WOTLK,
        tmp_path,
        [OVERRIDE],
        seams=reset_defaults.Seams(bind_label=lambda path: "", platform_id=_linux),
    )

    assert [r.outcome for r in report.results] == ["reset"]
    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == _set(installed, WOTLK) == tabbed


def test_a_new_install_gets_this_computers_zone(tmp_path: Path) -> None:
    """Owner decision 2026-09-28: a NEW server's default is "Same as this computer"."""
    _generate(tmp_path, host_zone=lambda: OSLO)
    assert _zones(tmp_path) == {"ac-authserver": OSLO, "ac-worldserver": OSLO}

    (tmp_path / "tbc").mkdir()
    _generate_tbc(tmp_path / "tbc", host_zone=lambda: OSLO)
    spec = TBC.container_spec()
    assert _zones(tmp_path / "tbc", TBC) == {spec.auth: OSLO_POSIX, spec.world: OSLO_POSIX}


def test_an_installed_server_keeps_utc_when_repaired_on_a_computer_elsewhere(
    tmp_path: Path,
) -> None:
    """Owner: an EXISTING server keeps what it has -- no line, UTC -- until it is changed."""
    installed = _generate(tmp_path)
    assert "TZ" not in installed, "control: the suite's computer is in UTC"

    assert _generate(tmp_path, host_zone=lambda: OSLO) == installed


def test_a_new_install_on_a_utc_computer_writes_no_line(tmp_path: Path) -> None:
    """UTC is both images' own clock: the file an install wrote before T171, byte for byte."""
    assert "TZ" not in _generate(tmp_path, host_zone=lambda: time_zone.UTC)


def test_a_mis_indented_zone_line_is_not_carried_so_the_reset_still_mends_the_file(
    tmp_path: Path,
) -> None:
    """The break owner decision 4 exists for: a `TZ` line level with `environment:`.

    Not a file YAML can read, so nothing starts. The line is in no environment,
    so no rewrite carries it, and the reset gives back the file the install
    wrote, byte for byte.
    """
    installed = _generate(tmp_path)
    broken = installed.replace("    environment:\n", "    environment:\n    TZ: Europe/Oslo\n")
    (tmp_path / OVERRIDE).write_text(broken, encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken)  # control: compose cannot read this file at all

    texts, reasons = reset_defaults.default_texts(
        WOTLK,
        tmp_path,
        [OVERRIDE],
        seams=reset_defaults.Seams(bind_label=lambda path: "", platform_id=_linux),
    )

    assert reasons == {}
    assert texts[OVERRIDE] == installed
