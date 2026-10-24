"""T689: the catalog's low findings from the v0.9.16 audit, each through the real applier path."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import pytest

from tests.test_apply import AHBOT_DIST, LUA, _FakeGit, _FakeReader, _have_requirements, _shipped
from tests.test_apply_put_back import _manifest
from tests.test_tbc_modules import _RecordingSql, _server_dir_with_conf
from yulon import apply as apply_module
from yulon.apply import Applier, ApplyError, ApplyRefusal, _set_conf_key
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import parse_manifest
from yulon.manifest_store import FAMILY_FILES, ManifestStore
from yulon.resources import manifests_dir

# -------------------------------------------------- a failed build's sentence


def test_a_failed_build_in_a_module_with_no_update_to_put_back_names_remove(
    tmp_path: Path,
) -> None:
    """A first install that never built (City Bots on v0.9.16) has no update to put back.

    It was told "was not updated since your last build that worked", which is false
    for a module that never built, and nothing named the press that builds without it.
    """
    said = Applier(tmp_path).after_failed_build(("mod-x",), lambda _id: _manifest())

    assert "press Remove on its row" in said
    assert "was not updated since your last build that worked" not in said


def test_a_failed_build_in_a_module_installed_since_the_last_build_says_it_is_new(
    tmp_path: Path,
) -> None:
    """T795: a fresh install (mod-ale on an old core) is in no build, so nothing is put back."""
    from yulon import module_answers

    manifest = _manifest()
    assert module_answers.record_rebuild_owed(tmp_path, manifest, None, never_built=True) == ""

    said = Applier(tmp_path).after_failed_build(("mod-x",), lambda _id: manifest)

    assert "new since your last build" in said
    assert "still running that build" in said
    assert "press Remove on its row" in said
    assert "was not updated since your last build that worked" not in said
    # With no record of the install, the sentence for a module with no update is unchanged.
    plain = Applier(tmp_path / "other").after_failed_build(("mod-x",), lambda _id: manifest)
    assert "new since your last build" not in plain
    assert "has no update of mod-x to put back" in plain


# -------------------------------------------------- a typed answer in a conf


def _motd(tmp_path: Path, answer: str) -> str:
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tbc_modules.store().load("mod", "motd")
    Applier(server_dir, sql=_RecordingSql()).install(manifest, {"motd": answer})
    return (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")


def test_a_backslash_in_a_message_of_the_day_is_written_as_typed(tmp_path: Path) -> None:
    """`Welcome \\o/` raised `re.PatternError: bad escape \\o` from inside the install."""
    assert 'Motd = "Welcome \\o/"\n' in _motd(tmp_path, "Welcome \\o/")


def test_a_typed_backslash_n_does_not_become_a_second_conf_line(tmp_path: Path) -> None:
    """The conf-key writer writes one line: a typed `\\n` is two characters, not a newline."""
    text = _motd(tmp_path, "Hi\\nRate.XP.Kill = 50")
    assert "\nRate.XP.Kill = 50" not in text
    assert 'Motd = "Hi\\nRate.XP.Kill = 50"\n' in text


# -------------------------------------------------- a patch that stopped matching


def _activechat_upstream() -> dict[str, str]:
    """The lines svey-xyz/ActiveChat 3ba6603 loads its data with, verbatim."""
    return {
        "AzerothChatter/logic/context.lua": '    local ok, m = pcall(require, "data.context")\n',
        "AzerothChatter/logic/chatter.lua": (
            '    local ok, m = pcall(require, "data.context")\n'
            'local world = require("data.chatter")    -- { shared/alliance/horde }\n'
        ),
        "AzerothChatter/data/context.lua": "return {}\n",
        "AzerothChatter/data/chatter.lua": "return {}\n",
    }


def test_activechat_loads_its_context_data_after_the_rename(tmp_path: Path) -> None:
    """`data/context.lua` is renamed, so every way upstream loads it must follow the rename."""
    m = wotlk_modules.store().load("ale", "activechat")
    _have_requirements(tmp_path, m)
    Applier(tmp_path, git=_FakeGit(_activechat_upstream())).install(m)

    logic = tmp_path / LUA / "AzerothChatter" / "logic"
    assert '"data.context_data"' in (logic / "context.lua").read_text(encoding="utf-8")
    assert '"data.context_data"' in (logic / "chatter.lua").read_text(encoding="utf-8")


def test_a_patch_that_matches_nothing_is_reported_and_not_called_applied(tmp_path: Path) -> None:
    m = parse_manifest(
        {
            "id": "p",
            "name": "P",
            "type": "ale",
            "game": "wow-wotlk",
            "source": {"repo": "x/p"},
            "deploy": [{"src": "p.lua", "dest": f"{LUA}/"}],
            "patches": [{"file": f"{LUA}/p.lua", "find": "nowhere", "replace": "x"}],
        }
    )
    report = Applier(tmp_path, git=_FakeGit({"p.lua": "local a = 1\n"})).install(m)

    assert not any("already applied" in line for line in report.done)
    assert any("did not match" in line for line in report.skipped)


# -------------------------------------------------- a conf left by an earlier install

PLUS_DIST = (
    "[worldserver]\nAuctionHouseBot.GUIDs = 0\nAuctionHouseBot.EnableSeller = false\n"
    "AuctionHouseBot.Buyer.Enabled = false\nAuctionHouseBot.ItemsPerCycle = 150\n"
    "AuctionHouseBot.MinutesBetweenSellCycle = 1\n"
    "AuctionHouseBot.AdvancedListingRules.UseDropRates.Enabled = true\n"
    "AuctionHouseBot.DEBUG_CONFIG = false\n"
)


def _ah_bot_then_plus(tmp_path: Path) -> Path:
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    applier = Applier(tmp_path, git=git, sql=_FakeReader(rows="Ahbot\n"))
    applier.install(_shipped("mod-ah-bot"), {"bot_guid": "42", "bot_account": "7"})
    applier.remove(_shipped("mod-ah-bot"))
    git.files = {"conf/mod_ahbot.conf.dist": PLUS_DIST}
    applier.install(_shipped("mod-ah-bot-plus"), {"bot_guid": "42"})
    return tmp_path / "env/dist/etc/modules/mod_ahbot.conf"


def test_ah_bot_plus_after_ah_bot_runs_on_its_own_conf(tmp_path: Path) -> None:
    """C6: Plus started from AH Bot's leftover conf, without 443 of its 447 keys."""
    conf = _ah_bot_then_plus(tmp_path).read_text(encoding="utf-8")
    assert "AuctionHouseBot.DEBUG_CONFIG = false" in conf


def test_the_earlier_conf_is_kept_beside_it(tmp_path: Path) -> None:
    conf = _ah_bot_then_plus(tmp_path)
    kept = conf.with_name(conf.name + apply_module.EARLIER_CONF_SUFFIX)
    assert "AuctionHouseBot.GUID = 42\n" in kept.read_text(encoding="utf-8")


def test_a_reinstall_over_the_checkout_keeps_the_conf_it_has(tmp_path: Path) -> None:
    """Only a FIRST install sets a conf aside: an Update keeps the player's settings."""
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    applier = Applier(tmp_path, git=git, sql=_FakeReader(rows="Ahbot\n"))
    applier.remote_url = lambda _dest: _shipped("mod-ah-bot").source.url  # type: ignore[union-attr]
    applier.unmodified = lambda _dest, _path: True
    applier.no_local_commits = lambda _dest, _branch: True
    applier.install(_shipped("mod-ah-bot"), {"bot_guid": "42", "bot_account": "7"})
    conf = tmp_path / "env/dist/etc/modules/mod_ahbot.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "# tuned by hand\n", encoding="utf-8")

    applier.install(_shipped("mod-ah-bot"), {"bot_guid": "42", "bot_account": "7"})

    assert "# tuned by hand" in conf.read_text(encoding="utf-8")
    assert not conf.with_name(conf.name + apply_module.EARLIER_CONF_SUFFIX).exists()


# -------------------------------------------------- manifest SQL against upstream

LISTING = Path(__file__).parent / "data" / "module-sql-listing.json"
"""Every `.sql` (and `*sql*` `.dist`) file at each module repository's default-branch HEAD,
recorded 2026-10-11 by `tests/data/record_module_sql_listing.py` (names only, no network in
the test). Modules track their latest commit, so a re-record is due when upstream moves."""


def _matches(pattern: str, path: str) -> bool:
    """`Path.glob()`'s rule for a pattern with no `**`: one component per component."""
    pat, got = PurePosixPath(pattern).parts, PurePosixPath(path).parts
    return len(pat) == len(got) and all(fnmatchcase(g, p) for p, g in zip(pat, got, strict=True))


def _sql_steps() -> list[tuple[str, str, str, str, str]]:
    """(item, repo, db, path, applied_by) for every shipped SQL file step, any game."""
    out = []
    for game_dir in sorted(p for p in manifests_dir().iterdir() if p.is_dir()):
        store = ManifestStore(manifests_dir(), game_dir.name)
        for family, index in FAMILY_FILES.items():
            if not (game_dir / f"{index}.json").is_file():
                continue  # a game without this family
            for m in store.load_all(family):
                if m.source is None:
                    continue
                for step in m.sql:
                    if step.path is not None and "{" not in step.path:
                        out.append((m.id, m.source.repo, step.db, step.path, step.applied_by))
    return out


def test_every_shipped_sql_path_matches_a_file_upstream() -> None:
    """C7: ten modules named globs their repositories do not have, so the report said
    "nothing to apply" for files the importer does apply."""
    repos = json.loads(LISTING.read_text(encoding="utf-8"))["repos"]
    steps = _sql_steps()
    assert len(steps) > 20, "the manifests were not found, so this asserted nothing"
    missing = [
        f"{item}: {path} matches nothing in {repo}"
        for item, repo, _db, path, _by in steps
        if not any(_matches(path, f) for f in repos[repo]["files"])
    ]
    assert missing == []


def test_every_db_import_step_names_the_database_its_folder_feeds() -> None:
    """mod-1v1-arena declared `characters` for `data/sql/db-world/base/1v1_Battlemaster.sql`."""
    wrong = [
        f"{item}: {path} is read into {PurePosixPath(path).parts[2]}, not {db}"
        for item, _repo, db, path, by in _sql_steps()
        if by == "db-import" and db not in PurePosixPath(path).parts[2]
    ]
    assert wrong == []


def test_a_direct_file_in_a_world_folder_withholds_its_module(tmp_path: Path) -> None:
    """The core updater walks `data/sql/world/` too (`dirName.find("world")`): a file this app
    runs itself there would be applied twice, so its module is not handed to the updater."""
    m = parse_manifest(
        {
            "id": "mod-w",
            "name": "W",
            "type": "module",
            "game": "wow-wotlk",
            "source": {"repo": "x/mod-w"},
            "sql": [{"db": "world", "path": "data/sql/world/base/w.sql", "applied_by": "direct"}],
        }
    )
    sql = tmp_path / "modules" / "mod-w" / "data" / "sql" / "world" / "base"
    sql.mkdir(parents=True)
    (sql / "w.sql").write_text("-- w", encoding="utf-8")

    plan = apply_module.module_sql_plan(tmp_path, [m], ["mod-w"])

    assert plan.withheld == ("mod-w",)


# ---------------------------------------------------------------- review round 2


def test_a_failed_build_in_a_folder_no_manifest_describes_says_what_to_do(
    tmp_path: Path,
) -> None:
    """A module folder put in by hand has no row on the Modules tab to press Remove on."""
    said = Applier(tmp_path).after_failed_build(("mod-by-hand",), lambda _id: None)

    assert "press Remove" not in said
    assert "move the folder modules/mod-by-hand out of your server folder" in said


def test_a_real_line_break_in_a_typed_answer_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    server_dir = _server_dir_with_conf(tmp_path)
    before = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    manifest = tbc_modules.store().load("mod", "motd")

    with pytest.raises(ApplyError):
        Applier(server_dir, sql=_RecordingSql()).install(
            manifest, {"motd": "Hi\nRate.XP.Kill = 50"}
        )

    assert (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8") == before


def test_the_conf_writer_itself_refuses_a_line_break(tmp_path: Path) -> None:
    """Belt for a value no question checked (a catalog default, a rendered template)."""
    conf = tmp_path / "x.conf"
    conf.write_text('Motd = "x"\n', encoding="utf-8")

    with pytest.raises(ApplyRefusal):
        _set_conf_key(conf, "Motd", '"Hi\nRate.XP.Kill = 50"')

    assert conf.read_text(encoding="utf-8") == 'Motd = "x"\n'


def test_remove_and_install_of_the_same_module_keeps_the_players_tuned_conf(
    tmp_path: Path,
) -> None:
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    applier = Applier(tmp_path, git=git, sql=_FakeReader(rows="Ahbot\n"))
    answers = {"bot_guid": "42", "bot_account": "7"}
    applier.install(_shipped("mod-ah-bot"), answers)
    conf = tmp_path / "env/dist/etc/modules/mod_ahbot.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "# tuned by hand\n", encoding="utf-8")
    applier.remove(_shipped("mod-ah-bot"))

    applier.install(_shipped("mod-ah-bot"), answers)

    assert "# tuned by hand" in conf.read_text(encoding="utf-8")
    assert not conf.with_name(conf.name + apply_module.EARLIER_CONF_SUFFIX).exists()


def test_a_question_s_check_refuses_a_line_break_before_anything_runs() -> None:
    """The dialog's own check (and the applier's `_check_values`) says it under the box."""
    prompt = tbc_modules.store().load("mod", "motd").prompts[0]
    assert apply_module.check_answer(prompt, "Hi\nRate.XP.Kill = 50") != ""
