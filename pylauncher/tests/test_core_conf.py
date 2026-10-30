"""The AzerothCore family owns its confs on the Tuning tab, with locked keys (T819).

`yulon.core_conf` is the pure rule: which keys of `worldserver.conf`,
`authserver.conf` and `modules/playerbots.conf` Yu'lon sets through the
container environment or must keep fixed, so the editor shows them but a Save
that touches one is refused. No Qt here: the view and the panel draw what this
module decides.
"""

from __future__ import annotations

import re
from pathlib import Path

from yulon import tuning
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

WOTLK = load_catalog().get("wow-wotlk")

WORLD_CONF = "env/dist/etc/worldserver.conf"
AUTH_CONF = "env/dist/etc/authserver.conf"
BOTS_CONF = "env/dist/etc/modules/playerbots.conf"


def test_every_ac_name_in_every_azerothcore_base_template_is_declared() -> None:
    """The base-environment tuples pin every name the base templates set (M5)."""
    from yulon import core_conf

    catalog = load_catalog()
    for game in ("wow-wotlk", "wow-unbound"):
        entry = catalog.get(game)
        assert entry.install.native is not None
        template_dir = (
            Path(__file__).resolve().parent.parent
            / "catalog"
            / "installers"
            / entry.install.native.templates
        )
        text = (template_dir / "base.yml.tmpl").read_text(encoding="utf-8")
        worlds: list[str] = []
        auths: list[str] = []
        service: list[str] | None = None
        for line in text.splitlines():
            if re.match(r"^  \{\{CONTAINER_PREFIX\}\}worldserver:", line):
                service = worlds
            elif re.match(r"^  \{\{CONTAINER_PREFIX\}\}authserver:", line):
                service = auths
            elif re.match(r"^  \{\{CONTAINER_PREFIX\}\}\w", line):
                service = None
            elif service is not None:
                found = re.match(r"^\s+(AC_[A-Z0-9_]+):", line)
                if found:
                    service.append(found.group(1))
        assert sorted(set(worlds)) == sorted(core_conf.BASE_WORLD_ENV), (game, worlds)
        assert sorted(set(auths)) == sorted(core_conf.BASE_AUTH_ENV), (game, auths)
        for name in (*core_conf.BASE_WORLD_ENV, *core_conf.BASE_AUTH_ENV):
            assert any(
                composegen.env_name_for(key) == name for key in core_conf.KNOWN_CONF_KEYS
            ), name


def test_a_world_env_key_is_locked_with_its_env_name(tmp_path: Path) -> None:
    """The bot counts ride the override, so the file must not pretend to set them."""
    from yulon import core_conf

    text = "[worldserver]\nAiPlayerbot.MinRandomBots = 50\n"
    locked = core_conf.locked(WOTLK, tmp_path, BOTS_CONF, text, text)
    assert locked["AiPlayerbot.MinRandomBots"] == core_conf.ENV_REASON(
        "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"
    )


def test_the_renamed_bot_key_is_locked_in_both_spellings(tmp_path: Path) -> None:
    """A renamed module reads the new spelling; the env row beats both (T657)."""
    from yulon import core_conf

    dist = tmp_path / "modules" / "mod-playerbots" / "conf" / "playerbots.conf.dist"
    dist.parent.mkdir(parents=True)
    dist.write_text("[playerbots]\nPlayerbots.MinRandomBots = 500\n", encoding="utf-8")
    text = "[playerbots]\nPlayerbots.MinRandomBots = 50\n"
    locked = core_conf.locked(WOTLK, tmp_path, BOTS_CONF, text, text)
    assert "Playerbots.MinRandomBots" in locked


def test_channel_keys_are_locked_while_the_channel_is_off(tmp_path: Path) -> None:
    """A press would shadow them, and the closed ports must never be opened (M1)."""
    from yulon import core_conf

    text = "[worldserver]\nSOAP.Enabled = 1\nAiPlayerbot.CommandServerPort = 8888\n"
    locked = core_conf.locked(WOTLK, tmp_path, WORLD_CONF, text, text)
    assert "SOAP.Enabled" in locked
    assert "AiPlayerbot.CommandServerPort" in locked
    assert not (tmp_path / "docker-compose.override.yml.before-channel").exists()


def test_the_world_env_never_locks_an_authserver_key(tmp_path: Path) -> None:
    """The override's rows reach the world alone; the login server reads its file (M2)."""
    from yulon import core_conf

    world = "[worldserver]\nAiPlayerbot.MinRandomBots = 500\n"
    auth = "[authserver]\nAiPlayerbot.MinRandomBots = 500\n"
    assert "AiPlayerbot.MinRandomBots" in core_conf.locked(
        WOTLK, tmp_path, WORLD_CONF, world, world
    )
    assert "AiPlayerbot.MinRandomBots" not in core_conf.locked(
        WOTLK, tmp_path, AUTH_CONF, auth, auth
    )


def test_container_ports_and_realm_id_are_locked(tmp_path: Path) -> None:
    """The publications and the realm row are Yu'lon's, not the file's."""
    from yulon import core_conf

    world = "[worldserver]\nWorldServerPort = 1\nSOAP.Port = 1\nRa.Port = 1\nRealmID = 1\n"
    locked = core_conf.locked(WOTLK, tmp_path, WORLD_CONF, world, world)
    for key in ("WorldServerPort", "SOAP.Port", "Ra.Port", "RealmID"):
        assert key in locked, key
    auth = "[authserver]\nRealmServerPort = 1\n"
    assert "RealmServerPort" in core_conf.locked(WOTLK, tmp_path, AUTH_CONF, auth, auth)


def test_a_plain_key_stays_editable(tmp_path: Path) -> None:
    """Rates and the greeting are the player's to change."""
    from yulon import core_conf

    text = "[worldserver]\nRate.XP.Kill = 1\nMotd = hi\n"
    locked = core_conf.locked(WOTLK, tmp_path, WORLD_CONF, text, text)
    assert "Rate.XP.Kill" not in locked
    assert "Motd" not in locked


def test_the_password_never_reaches_the_editor_and_its_bytes_survive_a_save() -> None:
    """Masked in the view, original on disk: the editor never holds the secret (M4)."""
    from yulon import core_conf

    raw = (
        "[worldserver]\r\n"
        'LoginDatabaseInfo = "127.0.0.1;3306;acore;s3cret;acore_auth"\r\n'
        "Motd = hi\r\n"
    )
    view = core_conf.masked_view(raw)
    assert "s3cret" not in view
    changed = view.replace("Motd = hi", "Motd = hello")
    assert tuning.save_text(raw, core_conf.unmask(changed, raw)) == (
        "[worldserver]\r\n"
        'LoginDatabaseInfo = "127.0.0.1;3306;acore;s3cret;acore_auth"\r\n'
        "Motd = hello\r\n"
    )


def test_a_password_key_is_locked_masked_and_never_saved_as_asterisks(tmp_path: Path) -> None:
    """Every `*password*` key is a secret: shown masked, locked, and restored on a save (Opus)."""
    from yulon import core_conf

    raw = "[worldserver]\r\nSomePassword = s3cret\r\nMotd = hi\r\n"
    locked = core_conf.locked(WOTLK, tmp_path, WORLD_CONF, raw, raw)
    assert locked["SomePassword"] == core_conf.SECRET_REASON

    view = core_conf.masked_view(raw)
    assert "s3cret" not in view
    assert "SomePassword = ***" in view

    edited = view.replace("SomePassword = ***", "SomePassword = hunter2")
    assert core_conf.locked_edits(raw, core_conf.unmask(edited, raw), locked) == ("SomePassword",)

    kept = view.replace("Motd = hi", "Motd = hello")
    assert tuning.save_text(raw, core_conf.unmask(kept, raw)) == (
        "[worldserver]\r\nSomePassword = s3cret\r\nMotd = hello\r\n"
    )


def test_changed_keys_reads_the_masked_view_not_the_raw_bytes() -> None:
    """A view compared with the raw file would report a secret as an edit (Opus)."""
    from yulon import core_conf

    raw = (
        "[worldserver]\n"
        'LoginDatabaseInfo = "127.0.0.1;3306;acore;s3cret;acore_auth"\n'
        "Motd = hi\n"
    )
    assert core_conf.changed_keys(raw, core_conf.masked_view(raw)) == ()
    edited = core_conf.masked_view(raw).replace("Motd = hi", "Motd = yo")
    assert core_conf.changed_keys(raw, edited) == (("Motd", "hi", "yo"),)


def test_the_locked_set_covers_a_key_the_edit_adds(tmp_path: Path) -> None:
    """A key the file does not carry is still locked when the edit adds it (Opus)."""
    from yulon import core_conf

    raw = "[playerbots]\n"
    added = raw + "AiPlayerbot.MinRandomBots = 50\n"
    assert "AiPlayerbot.MinRandomBots" not in core_conf.locked(WOTLK, tmp_path, BOTS_CONF, raw)
    assert "AiPlayerbot.MinRandomBots" in core_conf.locked(WOTLK, tmp_path, BOTS_CONF, raw, added)


def test_locked_edits_catch_a_change_a_comment_out_and_an_added_line(tmp_path: Path) -> None:
    """A refusal that reads the value the core reads: first active line wins."""
    from yulon import core_conf

    raw = "[worldserver]\nWorldServerPort = 8085\nMotd = hi\n"
    locked = core_conf.locked(WOTLK, tmp_path, WORLD_CONF, raw, raw)
    changed = "[worldserver]\nWorldServerPort = 9\nMotd = hi\n"
    assert core_conf.locked_edits(raw, changed, locked) == ("WorldServerPort",)
    assert core_conf.locked_edits(
        raw, "[worldserver]\n#WorldServerPort = 8085\nMotd = hi\n", locked
    ) == ("WorldServerPort",)
    assert core_conf.locked_edits(raw, raw + "RealmID = 2\n", locked) == ("RealmID",)
    assert (
        core_conf.locked_edits(raw, "[worldserver]\nWorldServerPort = 8085\nMotd = yo\n", locked)
        == ()
    )


def test_changed_keys_mask_secrets_and_default_lines_read_the_first_active_dist_line() -> None:
    """The confirm names keys, never passwords; the default is the shipped line."""
    from yulon import core_conf

    raw = (
        "[worldserver]\n"
        'LoginDatabaseInfo = "127.0.0.1;3306;acore;s3cret;acore_auth"\n'
        "Rate.XP.Kill = 1\n"
    )
    edited = (
        "[worldserver]\n"
        'LoginDatabaseInfo = "127.0.0.1;3306;acore;s3cret;acore_auth"\n'
        "Rate.XP.Kill = 2\n"
    )
    assert core_conf.changed_keys(raw, edited) == (("Rate.XP.Kill", "1", "2"),)
    dist = "#Rate.XP.Kill = 5\nRate.XP.Kill = 1\nRate.XP.Kill = 9\n"
    assert core_conf.default_lines(dist) == {"Rate.XP.Kill": "Rate.XP.Kill = 1"}


def test_no_catalog_manifest_declares_a_locked_key_on_a_core_conf(tmp_path: Path) -> None:
    """A card that wrote a locked key would fight the environment it cannot beat."""
    from yulon import core_conf
    from yulon.manifest_store import ManifestStore

    roots = sorted((Path(__file__).resolve().parent.parent / "manifests").glob("wow-*"))
    assert roots, "no shipped manifest trees found"
    for root in roots:
        store = ManifestStore(root.parent, root.name)
        for family in ("module", "ale", "mod", "keg"):
            try:
                manifests = list(store.load_all(family))  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - a family folder a tree does not carry
                continue
            for manifest in manifests:
                for conf in manifest.conf:
                    if conf.file not in (WORLD_CONF, AUTH_CONF, BOTS_CONF):
                        continue
                    faked = "A = 1\n" + "".join(f"{item.key} = 1\n" for item in conf.keys)
                    locked_here = core_conf.locked(WOTLK, tmp_path, conf.file, faked, faked)
                    for item in conf.keys:
                        assert item.key not in locked_here, (manifest.id, conf.file, item.key)
