"""The rewriters carry the player's bot count over; only Reset to default puts 500 back (T117).

T99 let a player set the random-bot count: Min = Max = N, in the WotLK
override's world environment or in a CMaNGOS tree's `etc/aiplayerbot.conf`.
Three other writers of those files rendered the catalog's 500 again, measured
per path below on the tree T117 started from:

* WotLK `generate-compose` -- a Repair, and Update to latest's put-back
  (`_rewrite_what_we_own()` runs the same body) -- rendered
  `composegen.world_env(entry)` (or T101's channel env, which is built on it);
* the channel's Enable press rendered `channel_setup._world_env()`, the same
  catalog env plus the channel's keys;
* the CMaNGOS `conf` stage patched the install table's `500` over the file on
  every run (`conf.apply_table()`).

The fix reads the number off the file on disk, as T102 reads the SELinux label,
and falls back to the catalog's value when there is no usable one. Reset to
default keeps writing 500 on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import context as cm_context
from tests.test_families_cmangos import engine as cm_engine
from yulon import bot_population, channel_setup, reset_defaults, resources, tuning
from yulon.catalog import bot_count, composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallOptions

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
OVERRIDE = composegen.OVERRIDE_FILE
BACKUP = f"{OVERRIDE}{channel_setup.BACKUP_SUFFIX}"
MIN_ENV = bot_population.MIN_ENV
MAX_ENV = bot_population.MAX_ENV
SOAP_ON = 'AC_SOAP_ENABLED: "1"'


def _linux() -> str:
    return "linux"


def _engine() -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        WOTLK,
        seams=native.Seams(
            platform_id=_linux, selinux_enforcing=lambda: False, fs_type=lambda path: "ext4"
        ),
    )


def _state(server_dir: Path) -> native.InstallState:
    return native.InstallState(
        game_id=WOTLK.id,
        install_id=composegen.install_id(server_dir, platform_id=_linux),
        family="azerothcore",
    )


def _generate(server_dir: Path) -> str:
    """The `generate-compose` stage, as an install AND a Repair run it."""
    list(
        _engine().stage_generate_compose(
            native.StageContext(
                server_dir=server_dir,
                client_dir=None,
                state=_state(server_dir),
                cancel=None,
                secrets=native.Secrets(db_password=WOTLK.install.db_password(server_dir)),
            )
        )
    )
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _press(server_dir: Path) -> str:
    channel_setup.enable(
        WOTLK, server_dir, templates_root=resources.installers_dir(), world_running=False
    )
    return (server_dir / OVERRIDE).read_text(encoding="utf-8")


def _tab(server_dir: Path) -> channel_setup.InstallChannel:
    """The Server tab's own setup object: its `roll_back()` is the button."""

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


def _bots(server_dir: Path, entry: object = WOTLK) -> tuple[int | None, int | None]:
    reading = bot_population.read(entry, server_dir)  # type: ignore[arg-type]
    assert reading.problem is None, reading.problem
    return reading.min, reading.max


def _set_env(server_dir: Path, low: str, high: str) -> None:
    """Hand-set the two lines, as a player editing the file would (the box sets Min = Max)."""
    path = server_dir / OVERRIDE
    text = bot_population.patch_env(
        path.read_text(encoding="utf-8"), WOTLK, OVERRIDE, {MIN_ENV: low, MAX_ENV: high}
    )
    path.write_text(text, encoding="utf-8")


# -- WotLK: the override ---------------------------------------------------------


def test_a_repair_keeps_the_players_bot_count(tmp_path: Path) -> None:
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    boxed = (tmp_path / OVERRIDE).read_text(encoding="utf-8")

    repaired = _generate(tmp_path)

    assert _bots(tmp_path) == (60, 60)
    assert repaired == boxed, "the Repair renders the same file the box wrote"


def test_update_to_latests_put_back_keeps_the_players_bot_count(tmp_path: Path) -> None:
    """`_rewrite_what_we_own()` is the Update route's put-back; it runs the stage body."""
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)

    list(_engine()._rewrite_what_we_own(tmp_path, InstallOptions(), _state(tmp_path)))

    assert _bots(tmp_path) == (60, 60)


def test_a_repair_keeps_the_bot_count_and_a_live_channel_together(tmp_path: Path) -> None:
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    pressed = _press(tmp_path)

    repaired = _generate(tmp_path)

    assert repaired == pressed
    assert SOAP_ON in repaired
    assert _bots(tmp_path) == (60, 60)


def test_the_channel_enable_press_keeps_the_players_bot_count(tmp_path: Path) -> None:
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)

    pressed = _press(tmp_path)

    assert SOAP_ON in pressed
    assert _bots(tmp_path) == (60, 60)


def test_the_channel_on_and_off_again_leaves_the_bot_count_and_the_file(tmp_path: Path) -> None:
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    before = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    _press(tmp_path)

    assert _tab(tmp_path).roll_back()

    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == before
    assert not (tmp_path / BACKUP).exists(), "the rollback recognised its own press"
    assert not composegen.channel_is_on(tmp_path)


def test_a_count_changed_while_the_channel_was_on_survives_its_rollback(tmp_path: Path) -> None:
    """The backup is the file before the FIRST press, so it holds the count from then."""
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    _press(tmp_path)
    bot_population.write(WOTLK, tmp_path, 70)

    assert _tab(tmp_path).roll_back()

    after = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert SOAP_ON not in after
    assert _bots(tmp_path) == (70, 70)
    assert not (tmp_path / BACKUP).exists()


def test_a_consistent_hand_set_range_is_carried_as_it_is(tmp_path: Path) -> None:
    _generate(tmp_path)
    _set_env(tmp_path, "40", "60")

    _generate(tmp_path)

    assert _bots(tmp_path) == (40, 60)


@pytest.mark.parametrize(
    ("low", "high"),
    [("80", "60"), ("sixty", "60"), ("60", ""), ("-1", "60")],
    ids=["min-above-max", "not-a-number", "empty", "negative"],
)
def test_an_unusable_count_falls_back_to_the_catalogs(tmp_path: Path, low: str, high: str) -> None:
    installed = _generate(tmp_path)
    _set_env(tmp_path, low, high)

    assert _generate(tmp_path) == installed
    assert _bots(tmp_path) == (500, 500)


def test_a_key_set_twice_falls_back_to_the_catalogs(tmp_path: Path) -> None:
    """Which line the server reads is not knowable here, so neither is carried."""
    installed = _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    path = tmp_path / OVERRIDE
    line = next(x for x in path.read_text(encoding="utf-8").splitlines() if MAX_ENV in x)
    path.write_text(
        path.read_text(encoding="utf-8").replace(line, f"{line}\n{line}"), encoding="utf-8"
    )

    assert _generate(tmp_path) == installed


def test_reset_to_default_still_puts_the_installed_count_back(tmp_path: Path) -> None:
    """The one rewriter that is MEANT to write 500."""
    installed = _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
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
    assert texts[OVERRIDE] == installed
    assert f'{MAX_ENV}: "500"' in installed


# -- CMaNGOS: etc/aiplayerbot.conf -----------------------------------------------


def _aiplayerbot(server_dir: Path) -> Path:
    return server_dir / bot_population.CONF_FILE


def _conf_bots(server_dir: Path) -> tuple[str | None, str | None]:
    text = _aiplayerbot(server_dir).read_text(encoding="utf-8")
    return (
        tuning.conf_value(text, bot_population.MIN_KEY),
        tuning.conf_value(text, bot_population.MAX_KEY),
    )


def test_the_conf_stage_run_again_keeps_the_players_bot_count(tmp_path: Path) -> None:
    eng = cm_engine(Recorder())
    list(eng._conf(cm_context(tmp_path)))
    assert _conf_bots(tmp_path) == ("500", "500"), "control: the install writes the catalog's"
    bot_population.write(TBC, tmp_path, 60)
    boxed = _aiplayerbot(tmp_path).read_bytes()

    said = list(eng._conf(cm_context(tmp_path, completed=["conf"])))

    assert _conf_bots(tmp_path) == ("60", "60")
    assert _aiplayerbot(tmp_path).read_bytes() == boxed
    assert not [line for line in said if line.startswith("Patched aiplayerbot")]


@pytest.mark.parametrize(
    ("low", "high"),
    [("80", "60"), ("many", "60")],
    ids=["min-above-max", "not-a-number"],
)
def test_an_unusable_conf_count_falls_back_to_the_catalogs(
    tmp_path: Path, low: str, high: str
) -> None:
    eng = cm_engine(Recorder())
    list(eng._conf(cm_context(tmp_path)))
    path = _aiplayerbot(tmp_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        f"{bot_population.MIN_KEY} = 500", f"{bot_population.MIN_KEY} = {low}"
    ).replace(f"{bot_population.MAX_KEY} = 500", f"{bot_population.MAX_KEY} = {high}")
    path.write_text(text, encoding="utf-8")
    assert _conf_bots(tmp_path) == (low, high), "control: the file says the bad pair"

    list(eng._conf(cm_context(tmp_path, completed=["conf"])))

    assert _conf_bots(tmp_path) == ("500", "500")


def test_a_conf_the_stage_never_finished_is_not_the_players_number(tmp_path: Path) -> None:
    """An install stopped between the copy and the patch leaves the IMAGE's numbers.

    Without a `conf` record nothing has patched that file yet, so a Min/Max in
    it is the template's (the shipped `.dist` says 50) and not a choice.
    """
    rec = Recorder()
    rec.conf_dist["aiplayerbot.conf.dist"] = (
        f"{bot_population.MIN_KEY} = 50\n{bot_population.MAX_KEY} = 50\n"
    )
    eng = cm_engine(rec)
    list(eng._conf(cm_context(tmp_path)))
    _aiplayerbot(tmp_path).write_text(rec.conf_dist["aiplayerbot.conf.dist"], encoding="utf-8")

    list(eng._conf(cm_context(tmp_path)))

    assert _conf_bots(tmp_path) == ("500", "500")


def test_a_record_that_outlived_its_conf_does_not_carry_the_templates_number(
    tmp_path: Path,
) -> None:
    """The pair is read BEFORE the copy: a conf copied this run is the image's, not a choice."""
    rec = Recorder()
    rec.conf_dist["aiplayerbot.conf.dist"] = (
        f"{bot_population.MIN_KEY} = 50\n{bot_population.MAX_KEY} = 50\n"
    )

    list(cm_engine(rec)._conf(cm_context(tmp_path, completed=["conf"])))

    assert rec.copied, "control: the conf was copied out of the image this run"
    assert _conf_bots(tmp_path) == ("500", "500")


# -- the one reader --------------------------------------------------------------


@pytest.mark.parametrize(
    ("low", "high", "kept"),
    [
        ("60", "60", ("60", "60")),
        (" 40", "60 ", ("40", "60")),
        ("0", "0", ("0", "0")),
        ("060", "60", ("60", "60")),
        ("61", "60", None),
        ("+5", "60", None),
        ("1_000", "2_000", None),
        ("-1", "60", None),
        ("", "60", None),
        (None, "60", None),
        ("60", str(bot_count.LARGEST + 1), None),
    ],
)
def test_a_pair_is_kept_only_when_a_server_would_honour_it(
    low: str | None, high: str | None, kept: tuple[str, str] | None
) -> None:
    assert bot_count.pair(low, high) == kept


def test_no_file_an_unreadable_file_and_a_non_utf8_file_carry_nothing(tmp_path: Path) -> None:
    assert bot_count.in_conf(tmp_path / "absent.conf") == {}
    assert bot_count.in_override(WOTLK, tmp_path) == {}
    (tmp_path / "dir.conf").mkdir()
    assert bot_count.in_conf(tmp_path / "dir.conf") == {}
    bad = tmp_path / "latin1.conf"
    bad.write_bytes(b"AiPlayerbot.MinRandomBots = 60\nAiPlayerbot.MaxRandomBots = 60\n# \xe6\n")
    assert bot_count.in_conf(bad) == {}
    assert bot_count.world_env(WOTLK, tmp_path, None) is None, "render()'s own default"


def test_a_crlf_override_is_read_like_any_other(tmp_path: Path) -> None:
    _generate(tmp_path)
    bot_population.write(WOTLK, tmp_path, 60)
    path = tmp_path / OVERRIDE
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert bot_count.in_override(WOTLK, tmp_path) == {MIN_ENV: "60", MAX_ENV: "60"}


def test_a_game_with_no_bot_env_carries_none_from_its_override(tmp_path: Path) -> None:
    """TBC's override publishes ports; its count lives in aiplayerbot.conf."""
    (tmp_path / OVERRIDE).write_text(
        f"services:\n  {TBC.containers.world}:\n    environment:\n"
        f'      {MIN_ENV}: "60"\n      {MAX_ENV}: "60"\n',
        encoding="utf-8",
    )
    assert bot_count.in_override(TBC, tmp_path) == {}
