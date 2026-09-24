"""T104: every module question is asked, and the answers are kept per install.

The owner's decision of 2026-09-24, "ask all, remember answers". Before it the
Modules tab opened its dialog only for a question with no default (and, since
T100, a `choice`), so Stackables on Tortoise/Vanilla/TBC was always 200, the mob
multipliers always their defaults, and so on. And nothing kept an answer: the
T100 cold review installed Hearthstone Tweaks with 5 minutes, pressed Update and
OK, and got 30 minutes back, because the dialog pre-selected the default.

These tests drive the REAL `Applier` over a temporary server folder, with only
the clone and the database faked, and read the record it leaves beside
`.yulon-install.json`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from yulon import apply as apply_module
from yulon import module_answers
from yulon.apply import Applier, ApplyError
from yulon.catalog import native
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import CloneSpec
from yulon.manifest import Manifest, Prompt, parse_manifest
from yulon.manifest_store import load_manifest

HEARTHSTONE_SHIPPED = (
    "Hearthstone_1_Sec.sql",
    "Hearthstone_1_Min.sql",
    "Hearthstone_5_Min.sql",
    "Hearthstone_15_Min.sql",
    "Hearthstone_30_Min.sql",
)


class _Clone:
    """Writes named files into the clone dir instead of cloning."""

    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = dict(files)

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        return True

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return True

    def clone(self, spec: CloneSpec) -> None:
        spec.dest.mkdir(parents=True, exist_ok=True)
        for rel, text in self.files.items():
            path = spec.dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (spec.dest / ".git").mkdir(exist_ok=True)


class _Sql:
    def __init__(self, fail: bool = False) -> None:
        self.files: list[tuple[str, str]] = []
        self.statements: list[tuple[str, str]] = []
        self.fail = fail

    def run_file(self, db: str, path: Path) -> None:
        self.files.append((db, path.name))

    def run_statement(self, db: str, statement: str) -> None:
        if self.fail:
            raise ApplyError("mysql exited 1: ERROR 1146 (42S02)")
        self.statements.append((db, statement))


def _wotlk(kind: str, item_id: str) -> Manifest:
    return wotlk_modules.store().load(kind, item_id)  # type: ignore[arg-type]


def _hearthstone_applier(server_dir: Path, sql: _Sql | None = None) -> Applier:
    files = {name: f"-- {name}\n" for name in HEARTHSTONE_SHIPPED}
    source = _wotlk("mod", "hearthstone-cd").source
    assert source is not None
    # A second install over the clone asks T47's questions; this one answers
    # them as git would for the checkout the first install made.
    return Applier(
        server_dir, git=_Clone(files), sql=sql or _Sql(), remote_url=lambda _dest: source.url
    )


def _shipped() -> list[Manifest]:
    root = wotlk_modules.BUNDLED_MANIFESTS_DIR
    out = []
    for path in sorted(root.rglob("*.json")):
        if path.parent.name == "schema" or path.name.startswith("index"):
            continue
        try:
            out.append(load_manifest(path))
        except Exception:  # noqa: BLE001 - an index or a schema file, not a manifest
            continue
    return out


# ------------------------------------------------------------------ ask all


def test_every_question_an_install_renders_is_asked_even_with_a_default() -> None:
    """The owner's "ask all": a default pre-fills the dialog, it no longer hides it.

    Mutation: put back T100's `default is None or kind == "choice"` and the
    number, text and yes/no prompts below stop being asked.
    """
    for kind, default in (("int", "200"), ("float", "1.5"), ("string", "Hi"), ("bool", "1")):
        prompt = Prompt(key="k", question="q", kind=kind, default=default)
        assert apply_module.must_ask(prompt, "install"), kind


def test_remove_asks_only_what_this_install_has_no_record_of() -> None:
    """Remove's answer must be the one install used, never a silent default (fix wave).

    The mob multipliers render their answers on REMOVE too (`HealthModifier/{hp}`
    undoes `HealthModifier*{hp}`). With a usable record the right answer is known
    and nothing is asked. With NO usable record -- an install made before T104,
    or a file that is damaged -- dividing by the default would be a guess, so the
    question is asked (pre-filled with the default and a note saying why).
    A question with no default at all is asked either way.

    Mutation: make `must_ask(..., "remove")` return `prompt.default is None` and
    the no-record case is not asked.
    """
    defaulted = Prompt(key="hp", question="HP", kind="float", default="0.25")
    bare = Prompt(key="guid", question="GUID", kind="int")
    assert not apply_module.must_ask(defaulted, "remove", {"hp": "0.5"})
    assert apply_module.must_ask(defaulted, "remove", {})
    assert apply_module.must_ask(defaulted, "remove")
    assert apply_module.must_ask(bare, "remove", {"hp": "0.5"})


def test_the_mods_whose_install_compounds_are_exactly_the_four_mob_multipliers() -> None:
    """A manifest whose remove renders install's answers applies them RELATIVE to what is there.

    That is what makes a second install (an Update) compound: x0.5 twice is x0.25.
    Pre-existing and tracked as T115; here it only decides who gets the warning.
    """
    compounding = sorted(m.id for m in _shipped() if apply_module.reapplies_on_top(m))
    assert compounding == ["baby-mobs", "buff-mobs", "nerf-mobs", "xbuff-mobs"]


def test_every_install_is_one_dialog_and_the_largest_is_accountwides_thirteen() -> None:
    """Many questions stay ONE dialog per module; measured across every shipped manifest.

    The dialog is one form, one row per prompt (and scrolls when that is taller
    than the window -- `test_manifest_prompt.py`). Since T92 an install also runs
    the module's configure-time steps, so "ask all" puts accountwide's thirteen
    yes/no flags into its Install dialog, pre-filled, one click to accept (the
    lead's ruling on the T92 merge, 2026-09-24). Next come the mob multipliers'
    four.
    """
    sizes = sorted(
        (len(apply_module.required_prompts(m, "install")), m.id)
        for m in _shipped()
        if apply_module.required_prompts(m, "install")
    )
    assert sizes[-1] == (13, "accountwide")
    assert max(n for n, item in sizes if item != "accountwide") <= 6


# ------------------------------------------------------------ remember answers


def test_an_install_remembers_the_answer_and_the_next_update_is_offered_it(tmp_path: Path) -> None:
    """The T100 cold-review repro: 5 minutes installed, Update pre-filled 30.

    Mutation: drop the record write at the end of `install()` and the second
    applier (a fresh one, as after a restart of the app) remembers nothing.
    """
    manifest = _wotlk("mod", "hearthstone-cd")
    _hearthstone_applier(tmp_path).install(manifest, {"cooldown": "5_Min"})

    fresh = _hearthstone_applier(tmp_path)
    assert fresh.remembered_answers(manifest) == {"cooldown": "5_Min"}
    saved = json.loads((tmp_path / module_answers.ANSWERS_FILE).read_text(encoding="utf-8"))
    assert saved["modules"]["mod/hearthstone-cd"] == {"cooldown": "5_Min"}


def test_an_old_install_with_nothing_saved_gets_the_defaults(tmp_path: Path) -> None:
    """Every install made before T104 has no record, and must behave exactly as before."""
    manifest = _wotlk("mod", "hearthstone-cd")
    applier = _hearthstone_applier(tmp_path)
    assert applier.remembered_answers(manifest) == {}
    assert applier._values(manifest, None) == {"cooldown": "30_Min"}


def test_a_failed_install_remembers_nothing(tmp_path: Path) -> None:
    """An answer is kept only once every step it fed has run."""
    manifest = _wotlk("mod", "hearthstone-cd")
    with pytest.raises(ApplyError):
        _hearthstone_applier(tmp_path, _Sql(fail=True)).install(manifest, {"cooldown": "5_Min"})
    assert not (tmp_path / module_answers.ANSWERS_FILE).exists()
    assert _hearthstone_applier(tmp_path).remembered_answers(manifest) == {}


def test_an_install_handed_no_answers_leaves_the_record_alone(tmp_path: Path) -> None:
    """`values=None` is a caller that asked nothing; it must not overwrite a player's answer."""
    manifest = _wotlk("mod", "hearthstone-cd")
    _hearthstone_applier(tmp_path).install(manifest, {"cooldown": "1_Min"})
    _hearthstone_applier(tmp_path).install(manifest, None)
    assert _hearthstone_applier(tmp_path).remembered_answers(manifest) == {"cooldown": "1_Min"}


def test_a_silent_run_uses_the_remembered_answer_not_the_default(tmp_path: Path) -> None:
    """Any caller that hands no answers gets the player's, not the manifest's.

    Mutation: drop the remembered layer from `_values()` and the second install
    sends the 30-minute reset twice.
    """
    manifest = _wotlk("mod", "hearthstone-cd")
    _hearthstone_applier(tmp_path).install(manifest, {"cooldown": "5_Min"})
    sql = _Sql()
    _hearthstone_applier(tmp_path, sql).install(manifest, None)
    (only,) = sql.statements
    assert "-- Hearthstone_5_Min.sql" in only[1], only


def test_removing_a_mob_multiplier_divides_by_what_install_multiplied_by(tmp_path: Path) -> None:
    """Remembering is what makes Remove right once Install can take another answer.

    `baby-mobs` multiplies on install and divides on remove by the same four
    answers. Asked on install (T104), a player can pick HP x0.5; a remove that
    used the manifest default would then divide by 0.25 and leave every creature
    at twice its stock health.

    Mutation: drop the remembered layer from `_values()` and the remove divides
    by 0.25.
    """
    manifest = _wotlk("mod", "baby-mobs")
    answers = {"hp": "0.5", "dmg": "0.5", "arm": "0.75", "spd": "1.25"}
    Applier(tmp_path, sql=_Sql()).install(manifest, answers)

    sql = _Sql()
    Applier(tmp_path, sql=sql).remove(manifest, None)
    (statement,) = [text for _db, text in sql.statements]
    assert "HealthModifier=HealthModifier/0.5" in statement, statement
    assert "DamageModifier=DamageModifier/0.5" in statement
    assert "ArmorModifier=ArmorModifier/0.75" in statement
    assert "BaseAttackTime=BaseAttackTime/1.25" in statement


def test_answers_are_kept_per_module_and_a_new_one_keeps_the_others(tmp_path: Path) -> None:
    manifest = _wotlk("mod", "hearthstone-cd")
    mobs = _wotlk("mod", "baby-mobs")
    _hearthstone_applier(tmp_path).install(manifest, {"cooldown": "15_Min"})
    Applier(tmp_path, sql=_Sql()).install(
        mobs, {"hp": "0.5", "dmg": "0.25", "arm": "0.25", "spd": "1.5"}
    )
    applier = _hearthstone_applier(tmp_path)
    assert applier.remembered_answers(manifest) == {"cooldown": "15_Min"}
    assert applier.remembered_answers(mobs)["hp"] == "0.5"


def test_the_record_is_per_install(tmp_path: Path) -> None:
    """Two server folders, two records: the answer belongs to the install it was given for."""
    manifest = _wotlk("mod", "hearthstone-cd")
    _hearthstone_applier(tmp_path / "one").install(manifest, {"cooldown": "1_Sec"})
    assert _hearthstone_applier(tmp_path / "two").remembered_answers(manifest) == {}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"modules": []}',
        '{"modules": {"mod/hearthstone-cd": "5_Min"}}',
        '{"modules": {"mod/hearthstone-cd": {"cooldown": 5}}}',
        '{"modules": {"mod/hearthstone-cd": {"cooldown": "2_Min"}}}',
    ],
)
def test_a_damaged_or_stale_record_falls_back_to_the_default(tmp_path: Path, payload: str) -> None:
    """A record this build cannot use is no record: the default is shown, never a guess.

    The last case is a choice that is no longer offered (a manifest that dropped
    an option): `check_answer()` refuses it, so it is not pre-filled.
    """
    (tmp_path / module_answers.ANSWERS_FILE).write_text(payload, encoding="utf-8")
    manifest = _wotlk("mod", "hearthstone-cd")
    applier = _hearthstone_applier(tmp_path)
    assert applier.remembered_answers(manifest) == {}
    assert applier._values(manifest, None) == {"cooldown": "30_Min"}


def test_only_declared_questions_are_recorded(tmp_path: Path) -> None:
    manifest = _wotlk("mod", "hearthstone-cd")
    _hearthstone_applier(tmp_path).install(manifest, {"cooldown": "5_Min", "stray": "x"})
    saved = json.loads((tmp_path / module_answers.ANSWERS_FILE).read_text(encoding="utf-8"))
    assert saved["modules"]["mod/hearthstone-cd"] == {"cooldown": "5_Min"}


def test_the_record_is_one_of_the_apps_own_files_in_a_server_folder() -> None:
    """A folder holding only the app's bookkeeping is still the app's folder.

    `native.OUR_OWN_FILES` is what the installer looks past when it asks "is this
    folder somebody else's?"; `networking.INTENT_FILE` was added to it for the
    same reason on 2026-09-05.
    """
    assert module_answers.ANSWERS_FILE in native.OUR_OWN_FILES
    assert module_answers.ANSWERS_FILE.startswith(".yulon-")


# ----------------------------------------------------------- unused questions


def test_no_shipped_manifest_declares_a_question_nothing_uses() -> None:
    """Each declared question is rendered by some action, or it is gone (T104 item 3).

    Before: accountwide `reputation_variant`, npc-teleporter `ony_level`,
    `capital`, `startzone`, portals-capitals `go_template`, `go_spawn` and WotLK
    all-stackables `stack_size` were declared and rendered by no template, so no
    answer to them could ever change anything.
    """
    dead = []
    for manifest in _shipped():
        used = {
            prompt.key
            for action in ("install", "remove", "configure")
            for prompt in apply_module.required_prompts(manifest, action)  # type: ignore[arg-type]
        }
        dead += [
            f"{manifest.game}/{manifest.id}:{p.key}" for p in manifest.prompts if p.key not in used
        ]
    assert dead == []


def test_the_onyxia_level_answer_reaches_the_row_upstream_sets_from_it(tmp_path: Path) -> None:
    """WIRED. `@ONY_LEVEL` is used upstream in exactly one row of `teleporter_capital.dist`.

    Read off Zoidwaffle/sql-npc-teleporter @06e5242, line 189:
    `(15, @GOSSIP_MENU+4, 11, 27, @ONY_LEVEL, 3, 0, "Portal Master - Level req")`
    with `@GOSSIP_MENU := 50000`, in an INSERT whose columns are
    `(SourceTypeOrReferenceId, SourceGroup, SourceEntry, ConditionTypeOrReference,
    ConditionValue1, ...)`. The file is sent as upstream wrote it (60) and the
    answer is written into that row after it, so the clone stays unmodified and
    a later Update is not refused over it.
    """
    manifest = _wotlk("mod", "npc-teleporter")
    files = {
        "data/sql/db-world/teleporter_capital.dist": "-- capital\n",
        "data/sql/db-world/teleporter_starting_zone.dist": "-- start\n",
    }
    sql = _Sql()
    Applier(tmp_path, git=_Clone(files), sql=sql).install(manifest, {"ony_level": "80"})
    assert sql.files == [
        ("world", "teleporter_capital.dist"),
        ("world", "teleporter_starting_zone.dist"),
    ]
    assert sql.statements == [
        (
            "world",
            "UPDATE conditions SET ConditionValue1=80 WHERE SourceTypeOrReferenceId=15 "
            "AND SourceGroup=50004 AND SourceEntry=11 AND ConditionTypeOrReference=27;",
        )
    ]
    assert [p.key for p in manifest.prompts] == ["ony_level"]


def test_the_dropped_questions_are_gone() -> None:
    """DROPPED where upstream offers nothing the engine can render (rulings in the ticket)."""
    assert [p.key for p in _wotlk("mod", "portals-capitals").prompts] == []
    assert [p.key for p in _wotlk("mod", "all-stackables").prompts] == []
    assert "reputation_variant" not in {p.key for p in _wotlk("ale", "accountwide").prompts}


ACCOUNTWIDE_UPSTREAM = (
    "00_AccountWideUtils.lua",
    "AccountAchievements.lua",
    "AccountCurrency.lua",
    "AccountMoney.lua",
    "AccountMounts.lua",
    "AccountPets.lua",
    "AccountPlaytime.lua",
    "AccountProfessions.lua",
    "AccountPvPRank.lua",
    "AccountReputation (default AC-Wotlk).lua",
    "AccountReputation (modified for Ashen Order).lua",
    "AccountTaxiPaths.lua",
    "AccountTitles.lua",
)
"""`lua_scripts/AccountWide/` of Aldori15/azerothcore-eluna-accountwide @fddbd8c, listed 2026-09-24.

There is NO `AccountReputation.lua` upstream: each reputation variant carries
`local ENABLE_ACCOUNTWIDE_REPUTATION = false` at line 7 under its own long name.
"""


def _accountwide_clone() -> dict[str, str]:
    """Upstream's own file NAMES, each holding the flag lines the manifest's patches edit.

    Since T92 an install runs the configure-time patches too, so the fake clone
    must carry the lines they rewrite (upstream ships each as
    `local ENABLE_... = false`). The names are upstream's, NOT the patch
    targets': a patch whose target exists only after a `deploy.rename` is traced
    back to the file upstream actually ships, so a patch aimed at a name nobody
    ships fails here as it would against the real clone (the T92 merge found
    `AccountReputation.lua` doing exactly that).
    """
    manifest = _wotlk("ale", "accountwide")
    shipped_as = {new: old for step in manifest.deploy for old, new in step.rename}
    body = {name: "" for name in ACCOUNTWIDE_UPSTREAM}
    for patch in manifest.patches:
        key = re.search(r"local\\s\+(\w+)", patch.find)
        assert key is not None, patch.find
        name = Path(patch.file).name
        name = shipped_as.get(name, name)
        if name in body:
            body[name] += f"local {key.group(1)} = false\n"
    ashen = "AccountReputation (modified for Ashen Order).lua"
    body[ashen] += "local ENABLE_ACCOUNTWIDE_REPUTATION = false\n"
    files = {f"lua_scripts/AccountWide/{n}": text or "-- lua\n" for n, text in body.items()}
    files["sql/create_accountwide_tables.sql"] = "-- tables\n"
    return files


def test_accountwide_installs_over_upstreams_own_file_names_with_every_flag_written(
    tmp_path: Path,
) -> None:
    """Every configure patch finds its line in a file upstream ships (T92 merge finding).

    `upstream/Yulon` @0726a0ab refused EVERY accountwide install with `patch target
    missing: .../AccountReputation.lua`: T92 made install run the configure-time
    patches, and the reputation patch names a file upstream does not ship.
    Measured against the real upstream clone @fddbd8c through the real
    `Applier`, not only against this fixture. The stock variant is now deployed
    AS `AccountReputation.lua`.

    Mutation: drop that rename and the install raises again.
    """
    manifest = _wotlk("ale", "accountwide")
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    every = {p.key: "true" for p in apply_module.required_prompts(manifest, "install")}
    Applier(tmp_path, git=_Clone(_accountwide_clone()), sql=_Sql()).install(manifest, every)

    deployed = tmp_path / "env/dist/etc/modules/lua_scripts/accountwide"
    for patch in manifest.patches:
        text = (tmp_path / patch.file).read_text(encoding="utf-8")
        key = re.search(r"local\\s\+(\w+)", patch.find)
        assert key is not None
        assert f"local {key.group(1)} = true" in text, (patch.file, text)
    assert "local ENABLE_ACCOUNTWIDE_REPUTATION = true" in (
        deployed / "AccountReputation.lua"
    ).read_text(encoding="utf-8")


ALE_LOADS = (".lua", ".dll", ".so", ".ext", ".moon", ".out")
"""What mod-ale @bd74eae `ALE::AddScriptPath` loads: the text after the LAST dot, one of these."""


def test_accountwide_deploys_one_reputation_script_not_both(tmp_path: Path) -> None:
    """DROPPED `reputation_variant`, and the defect behind it fixed: both variants loaded.

    Upstream's README: a stock AzerothCore (no custom races, stock Faction.dbc)
    uses `AccountReputation (default AC-Wotlk).lua`; the Ashen Order one is for
    that server's modified DBC -- "Be sure to delete out the other file". The
    whole directory was deployed, so both registered the same five player
    events. The Ashen file is renamed on deploy to a name ALE does not load.
    """
    manifest = _wotlk("ale", "accountwide")
    files = _accountwide_clone()
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    Applier(tmp_path, git=_Clone(files), sql=_Sql()).install(manifest, None)

    deployed = tmp_path / "env/dist/etc/modules/lua_scripts/accountwide"
    loaded = sorted(
        p.name
        for p in deployed.iterdir()
        if "." in p.name and p.name[p.name.rfind(".") :] in ALE_LOADS
    )
    assert [n for n in loaded if "Reputation" in n] == ["AccountReputation.lua"]
    assert "00_AccountWideUtils.lua" in loaded
    assert (deployed / "AccountReputation (modified for Ashen Order).lua.unused").is_file()


def _accountwide_installed(tmp_path: Path) -> tuple[Applier, Manifest, Path]:
    manifest = _wotlk("ale", "accountwide")
    files = _accountwide_clone()
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    source = manifest.source
    assert source is not None
    applier = Applier(tmp_path, git=_Clone(files), sql=_Sql(), remote_url=lambda _dest: source.url)
    applier.install(manifest, None)
    return applier, manifest, tmp_path / "env/dist/etc/modules/lua_scripts/accountwide"


ASHEN = "AccountReputation (modified for Ashen Order).lua"


def test_removing_a_pre_t104_accountwide_install_deletes_the_real_ashen_file(
    tmp_path: Path,
) -> None:
    """An install made before T104 has the Ashen Order script under its REAL name.

    The rename list maps it to `.lua.unused`, and `_undeploy()` re-derives what
    it deployed from that list -- so Remove deleted a `.lua.unused` that was
    never there and left the live `.lua` behind, loaded by ALE, in silence
    (cold review, fix wave). Remove now deletes both names and says which it found.

    Mutation: drop the old name from `_undeploy()`'s set and the `.lua` survives.
    """
    applier, manifest, deployed = _accountwide_installed(tmp_path)
    (deployed / f"{ASHEN}.unused").replace(deployed / ASHEN)  # the pre-T104 shape

    report = applier.remove(manifest, None)
    assert not (deployed / ASHEN).exists()
    assert not (deployed / f"{ASHEN}.unused").exists()
    rel = "env/dist/etc/modules/lua_scripts/accountwide"
    assert f"rm {rel}/{ASHEN}" in report.done, report.done


def test_removing_a_t104_accountwide_install_deletes_the_renamed_file(tmp_path: Path) -> None:
    applier, manifest, deployed = _accountwide_installed(tmp_path)
    report = applier.remove(manifest, None)
    assert not (deployed / f"{ASHEN}.unused").exists()
    rel = "env/dist/etc/modules/lua_scripts/accountwide"
    assert f"rm {rel}/{ASHEN}.unused" in report.done, report.done
    assert f"rm {rel}/{ASHEN}" not in report.done


# ------------------------------------------------------ the record's own write


def test_a_record_that_cannot_be_parsed_is_left_alone_not_overwritten(tmp_path: Path) -> None:
    """A damaged record might be a newer build's, or a hand edit: never clobbered (fix wave).

    Mutation: treat an unparseable file as empty on write and it is replaced.
    """
    path = tmp_path / module_answers.ANSWERS_FILE
    path.write_text("{ this is not json", encoding="utf-8")
    manifest = _wotlk("mod", "hearthstone-cd")
    report = _hearthstone_applier(tmp_path).install(manifest, {"cooldown": "5_Min"})
    assert path.read_text(encoding="utf-8") == "{ this is not json"
    assert any(module_answers.ANSWERS_FILE in line for line in report.skipped), report.skipped


@pytest.mark.parametrize("payload", ["[]", '{"modules": []}', '"x"'])
def test_a_record_of_a_shape_this_build_does_not_know_is_left_alone(
    tmp_path: Path, payload: str
) -> None:
    path = tmp_path / module_answers.ANSWERS_FILE
    path.write_text(payload, encoding="utf-8")
    problem = module_answers.record_answers(
        tmp_path, _wotlk("mod", "hearthstone-cd"), {"cooldown": "5_Min"}
    )
    assert problem != ""
    assert path.read_text(encoding="utf-8") == payload


def test_unknown_fields_and_a_newer_schema_version_survive_a_write(tmp_path: Path) -> None:
    path = tmp_path / module_answers.ANSWERS_FILE
    path.write_text(
        json.dumps(
            {
                "schema_version": 7,
                "future": {"kept": True},
                "modules": {"mod/other": {"a": "1", "extra": [1, 2]}},
            }
        ),
        encoding="utf-8",
    )
    assert (
        module_answers.record_answers(
            tmp_path, _wotlk("mod", "hearthstone-cd"), {"cooldown": "5_Min"}
        )
        == ""
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 7
    assert saved["future"] == {"kept": True}
    assert saved["modules"]["mod/other"] == {"a": "1", "extra": [1, 2]}
    assert saved["modules"]["mod/hearthstone-cd"] == {"cooldown": "5_Min"}


def test_the_write_goes_through_a_unique_temp_file_in_the_same_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a fixed `.new` name two writers could share; and a failed rename leaves no debris."""
    made: list[tuple[object, object]] = []
    real = module_answers.tempfile.mkstemp

    def spy(*args: Any, **kwargs: Any) -> Any:
        made.append((kwargs.get("dir"), kwargs.get("prefix")))
        return real(*args, **kwargs)

    monkeypatch.setattr(module_answers.tempfile, "mkstemp", spy)
    manifest = _wotlk("mod", "hearthstone-cd")
    assert module_answers.record_answers(tmp_path, manifest, {"cooldown": "5_Min"}) == ""
    assert made == [(tmp_path, module_answers.ANSWERS_FILE + ".")]

    def refuse(*_a: object) -> None:
        raise OSError("disk says no")

    monkeypatch.setattr(module_answers.os, "replace", refuse)
    assert module_answers.record_answers(tmp_path, manifest, {"cooldown": "1_Min"}) != ""
    assert sorted(p.name for p in tmp_path.iterdir()) == [module_answers.ANSWERS_FILE]
    saved = json.loads((tmp_path / module_answers.ANSWERS_FILE).read_text(encoding="utf-8"))
    assert saved["modules"]["mod/hearthstone-cd"] == {"cooldown": "5_Min"}


def test_the_accountwide_manifest_still_parses(tmp_path: Path) -> None:
    raw: dict[str, Any] = json.loads(
        (wotlk_modules.BUNDLED_MANIFESTS_DIR / "wow-wotlk" / "ale" / "accountwide.json").read_text(
            encoding="utf-8"
        )
    )
    assert parse_manifest(raw).id == "accountwide"
