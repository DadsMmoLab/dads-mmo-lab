"""A module update adds NEW conf keys, never touching edited values (T818).

A module update re-runs `Applier._conf`, which used to leave a live conf
alone except for the catalog's own `conf.keys` (which DO overwrite). Keys the
module's new `.conf.dist` adds never reached the player's file, so the server
ran on the module's built-in default while logging "Missing property". The
fix: when the live conf shares at least one key with this same module's
template, append only the keys the dist has and the conf lacks, with their
comment blocks, keeping every value the player set. A key the player deleted
on purpose comes back with the default (only a commented-out key counts as
present); the done line says so in plain words.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_apply import _THING_MODULE, _FakeGit, _Origins
from yulon import tuning
from yulon.apply import Applier
from yulon.catalog.families import conf as conf_family
from yulon.manifest import parse_manifest

REPO_URL = "https://github.com/azerothcore/mod-thing.git"

DIST_V1 = "Thing.Enabled = 1\nThing.Level = 3\n"
DIST_V2 = (
    "# The thing's own switch.\n"
    "Thing.Enabled = 1\n"
    "Thing.Level = 3\n"
    "# How loud the thing is.\n"
    "# Louder costs more.\n"
    "Thing.NewKey = 9\n"
)


def _manifest() -> Any:
    return parse_manifest(_THING_MODULE)


def _deployed(server: Path) -> Path:
    return server / "env/dist/etc/modules/thing.conf"


def _install(server: Path, dist: str) -> None:
    report = Applier(server, git=_FakeGit({"conf/thing.conf.dist": dist})).install(_manifest())
    assert report.restart_recommended is True


def _update(server: Path, dist: str):
    """The press the Modules tab takes: `update()` over the install above, new dist in."""
    return Applier(
        server,
        git=_FakeGit({"conf/thing.conf.dist": dist}),
        remote_url=_Origins(REPO_URL),
        unmodified=lambda _dest, _path: True,
        no_local_commits=lambda _dest, _branch: True,
    ).update(_manifest())


def test_an_update_adds_the_new_key_and_keeps_the_edited_value(tmp_path: Path) -> None:
    """The failing case: the new dist's key lands with its default, the edited one stays."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Enabled = 0\nThing.Level = 3\n", encoding="utf-8")

    report = _update(tmp_path, DIST_V2)

    text = deployed.read_text(encoding="utf-8")
    assert "Thing.Enabled = 0\n" in text, "the player's own value is not touched"
    assert "Thing.NewKey = 9\n" in text, "the new key arrives with the module's default"
    assert "# The thing's own switch.\n" not in text, "kept keys bring no comment block"
    assert "# Louder costs more.\n" in text, "the new key brings its comment block"
    assert any(
        "added 1 new setting(s) to env/dist/etc/modules/thing.conf" in line
        and "your own values were not changed" in line
        for line in report.done
    ), report.done
    assert report.restart_recommended is True


def test_an_update_never_overwrites_an_edited_value(tmp_path: Path) -> None:
    """Mutation: writing the dist's value over the player's (red if values move)."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Enabled = 0\nThing.Level = 8\n", encoding="utf-8")

    _update(tmp_path, DIST_V2)

    text = deployed.read_text(encoding="utf-8")
    assert "Thing.Enabled = 0\n" in text
    assert "Thing.Level = 8\n" in text
    assert "Thing.Enabled = 1\n" not in text


def test_a_commented_out_key_is_present_and_is_not_re_added(tmp_path: Path) -> None:
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Enabled = 0\nThing.Level = 3\n#Thing.NewKey = 5\n", encoding="utf-8")

    report = _update(tmp_path, DIST_V2)

    text = deployed.read_text(encoding="utf-8")
    assert text.count("Thing.NewKey") == 1, "the commented-out key counts as present"
    assert not any("added" in line for line in report.done)


def test_nothing_to_add_writes_nothing_and_says_nothing(tmp_path: Path) -> None:
    """Mutation: an unconditional write (no missing keys still touches the file)."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    before = deployed.read_bytes()
    backups_before = set(tuning.backups_of(deployed))
    report = _update(tmp_path, DIST_V1)

    assert deployed.read_bytes() == before, "byte-identical when there is nothing to add"
    assert set(tuning.backups_of(deployed)) == backups_before, "no backup taken"
    assert not any("added" in line for line in report.done)


def test_a_conf_from_another_template_is_untouched(tmp_path: Path) -> None:
    """Mutation: skipping the same-template check (AH Bot's conf under AH Bot Plus).

    The aside only runs on a first install; on the update path the vocabulary
    guard is what leaves a foreign conf alone: no shared key, nothing added.
    """
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Other.Enabled = 0\nOther.Extra = 2\n", encoding="utf-8")
    before = deployed.read_bytes()
    report = _update(tmp_path, "Thing.Enabled = 1\nThing.New = 3\n")

    assert deployed.read_bytes() == before, "same filename, different module: untouched"

    assert not any("added" in line for line in report.done)


def test_a_player_own_key_does_not_disqualify_the_conf(tmp_path: Path) -> None:
    """A key the template never named is the player's own; the new keys still land."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Enabled = 0\nThing.Level = 3\nThing.Mine = 7\n", encoding="utf-8")

    _update(tmp_path, DIST_V2)

    text = deployed.read_text(encoding="utf-8")
    assert "Thing.Mine = 7\n" in text
    assert "Thing.NewKey = 9\n" in text


def test_the_update_takes_a_backup_first(tmp_path: Path) -> None:
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Enabled = 0\nThing.Level = 3\n", encoding="utf-8")

    _update(tmp_path, DIST_V2)

    backups = tuning.backups_of(deployed)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "Thing.Enabled = 0\nThing.Level = 3\n"


def test_a_deleted_key_comes_back_with_the_default(tmp_path: Path) -> None:
    """Documented caveat: the app cannot tell a deliberate delete from a never-seen key."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Level = 3\n", encoding="utf-8")

    _update(tmp_path, DIST_V1)

    assert "Thing.Enabled = 1\n" in deployed.read_text(encoding="utf-8")


def test_the_added_keys_survive_a_reinstall_unchanged(tmp_path: Path) -> None:
    """Second update adds nothing twice: what was added counts as present now."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("Thing.Enabled = 0\nThing.Level = 3\n", encoding="utf-8")

    first = _update(tmp_path, DIST_V2)
    text_after_first = deployed.read_bytes()
    second = _update(tmp_path, DIST_V2)

    assert deployed.read_bytes() == text_after_first
    assert not any("added" in line for line in second.done)
    assert first.restart_recommended is True


def test_add_missing_keys_is_generic_text_in_text_out() -> None:
    """The helper itself: no prefix baked in, values kept, comments travel with keys."""
    text, added = conf_family.add_missing_keys(
        "Mine.On = no\n", "# Loud.\nMine.On = yes\nMine.New = 9\n"
    )
    assert added == 1
    assert "Mine.On = no\n" in text, "the live value is never rewritten"
    assert "# Loud.\n" not in text, "a kept key's comment block is not copied"
    assert "Mine.New = 9\n" in text


def test_add_missing_keys_counts_commented_keys_as_present() -> None:
    text, added = conf_family.add_missing_keys("#Mine.New = 5\n", "Mine.New = 9\n")
    assert (text, added) == ("#Mine.New = 5\n", 0)


def test_an_empty_conf_gets_nothing(tmp_path: Path) -> None:
    """No shared key means no evidence it is this module's file: left alone."""
    _install(tmp_path, DIST_V1)
    deployed = _deployed(tmp_path)
    deployed.write_text("", encoding="utf-8")

    report = _update(tmp_path, DIST_V2)

    assert deployed.read_bytes() == b""
    assert not any("added" in line for line in report.done)
