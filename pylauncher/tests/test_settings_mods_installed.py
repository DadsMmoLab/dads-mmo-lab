"""A mod that only changes settings shows as installed, and its Remove undoes it.

Experience Rates on Tortoise (`xp-rates`) has no repository: its install writes
three keys into `etc/mangosd.conf` and leaves no folder under
`sql_scripts/clones/`. The Modules tab read "installed" off that folder, so the
row said Not installed after a finished install and offered no Remove (live
test, 2026-10-05). The same held for every mod with no repository that only
writes conf keys, on all four games.

The install now writes a receipt into the answers file the record-backed mob
mods already use: which keys it wrote, and the values. An install made before
the receipt existed is recognised when the conf still reads exactly what that
install wrote, with the answers the install saved.

The tab tests press the real row buttons over the real factory wiring
(`ControllerServices.for_entry()`), with the real applier, so the receipt on
disk is the one a real install wrote and the reading is the one the app makes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.support_player_text import command_faults, text_faults
from yulon import apply as apply_module
from yulon import module_answers, runner
from yulon.apply import Applier
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_vanilla import modules as vanilla_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView, remove_question
from yulon.ui.message_box import FittedMessageBox
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TORTOISE = CATALOG.get("wow-tortoise")
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")

WOTLK_CONF = "env/dist/etc/worldserver.conf"
CMANGOS_CONF = "etc/mangosd.conf"

STOCK_XP = "Rate.XP.Kill = 1\nRate.XP.Quest = 1\nRate.XP.Explore = 1\n"


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every process the tab starts answers "nothing running"; no Docker is reached."""
    calls: list[list[str]] = []

    def run(cmd: list[str], *_: object, **__: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner, "run", run)
    return calls


def _server(tmp_path: Path, conf: str, text: str = STOCK_XP) -> Path:
    server_dir = tmp_path / "srv"
    (server_dir / conf).parent.mkdir(parents=True, exist_ok=True)
    (server_dir / conf).write_text(f"# stock\n{text}", encoding="utf-8")
    return server_dir


def _view(
    entry: CatalogEntry, server_dir: Path, answers: dict[str, str]
) -> tuple[ControllerView, list[str]]:
    asked: list[str] = []

    def asker(parent: object, manifest: object, prompts: object, **_: object) -> dict[str, str]:
        asked.append(str(manifest.id))  # type: ignore[attr-defined]
        return dict(answers)

    services = ControllerServices.for_entry(entry, server_dir)
    view = ControllerView(entry, services, status_poll_ms=0, prompt_asker=asker)
    return view, asked


def _conf(server_dir: Path, conf: str) -> str:
    return (server_dir / conf).read_text(encoding="utf-8")


def _row(view: ControllerView, item_id: str) -> object:
    return view.modules_panel.row(item_id)


def _answer_questions(
    monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton
) -> list[tuple[str, str]]:
    """Answer each Yes/No box `answer`, as the int PySide6 really returns, keeping each
    (title, text) it was asked.

    The recorder sits on `QMessageBox.exec`, the only way `ask_yes_no()`'s fitted box is
    answered (T243). Only a fitted box with No as its default is kept and answered: a
    press that went back to the static `QMessageBox.question()` would build Qt's own box,
    which grows with its text and can put Yes and No below the screen. That static call is
    the conftest's, which answers No and never reaches this recorder, so the question
    count is 0 and a Yes test fails too.
    """
    asked: list[tuple[str, str]] = []

    def record(box: QMessageBox) -> int:
        no = QMessageBox.StandardButton.No
        if not isinstance(box, FittedMessageBox) or box.standardButton(box.defaultButton()) != no:
            return int(no.value)
        asked.append((box.windowTitle(), box.text()))
        return int(answer.value)

    monkeypatch.setattr(QMessageBox, "exec", record)
    return asked


# ------------------------------------------------------------ through the tab


@pytest.mark.parametrize(
    ("entry", "conf", "answers", "installed_line"),
    [
        (WOTLK, WOTLK_CONF, {"kill": "3", "quest": "3", "explore": "3"}, "Rate.XP.Kill = 3"),
        (TORTOISE, CMANGOS_CONF, {"xp_rate": "3"}, "Rate.XP.Kill = 3"),
        (TBC, CMANGOS_CONF, {"kill": "3", "quest": "3", "explore": "3"}, "Rate.XP.Kill = 3"),
        (VANILLA, CMANGOS_CONF, {"kill": "3", "quest": "3", "explore": "3"}, "Rate.XP.Kill = 3"),
    ],
    ids=["wotlk", "tortoise", "tbc", "vanilla"],
)
def test_xp_rates_installs_shows_installed_and_removes_through_the_row_buttons(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: CatalogEntry,
    conf: str,
    answers: dict[str, str],
    installed_line: str,
) -> None:
    """The ticket's sequence: Install, the row says Installed and offers Remove; Remove,
    answered Yes, puts the stock values back and the row says Not installed again.

    Mutation: drop the receipt write in `Applier.install()` and the receipt is not
    there (the row itself would still read Installed, off the keys and the saved
    answers, which is why the receipt is asserted directly).
    """
    server_dir = _server(tmp_path, conf)
    view, asked = _view(entry, server_dir, answers)
    questions = _answer_questions(monkeypatch, QMessageBox.StandardButton.Yes)
    row = _row(view, "xp-rates")
    assert row.data.badge == "Not installed"  # type: ignore[attr-defined]

    row.install_button.click()  # type: ignore[attr-defined]

    assert asked == ["xp-rates"] and questions == [], "Install asks only its own question"
    assert installed_line in _conf(server_dir, conf)
    assert "mod/xp-rates" in module_answers.settings_keys(server_dir)
    row = _row(view, "xp-rates")
    assert row.data.installed and row.data.badge == "Installed"  # type: ignore[attr-defined]
    assert row.remove_button is not None and row.install_button is None  # type: ignore[attr-defined]

    row.remove_button.click()  # type: ignore[attr-defined]

    assert len(questions) == 1 and questions[0][0].startswith("Remove "), questions
    assert "mod/xp-rates" not in module_answers.settings_keys(server_dir)
    text = _conf(server_dir, conf)
    assert installed_line not in text and "Rate.XP.Kill" in text
    assert "= 3" not in text, text
    row = _row(view, "xp-rates")
    assert not row.data.installed and row.data.badge == "Not installed"  # type: ignore[attr-defined]
    assert row.install_button is not None  # type: ignore[attr-defined]


def test_the_tuning_tab_lists_an_installed_settings_mods_keys(qapp: object, tmp_path: Path) -> None:
    """The Server rates card reads "Experience Rates is installed" off the Tuning tab's
    module rows, which come only from installed mods. Through the same reader.

    Mutation: wire Tortoise's `installed_modules` back to `installed_clones` and
    no xp-rates row appears.
    """
    server_dir = _server(tmp_path, CMANGOS_CONF)
    view, _asked = _view(TORTOISE, server_dir, {"xp_rate": "3"})
    _row(view, "xp-rates").install_button.click()  # type: ignore[attr-defined]

    view.reload_tuning()

    keys = {(row.module_id, row.key, row.current) for row in view._tuning_rows}
    assert ("xp-rates", "Rate.XP.Kill", "3") in keys, keys


# ------------------------------------------------------------ the receipt


def _manifest(store: object, item_id: str = "xp-rates") -> Manifest:
    return store.load("mod", item_id)  # type: ignore[attr-defined,no-any-return]


def test_the_receipt_names_the_keys_and_values_the_install_wrote(tmp_path: Path) -> None:
    server_dir = _server(tmp_path, CMANGOS_CONF)
    manifest = _manifest(tortoise_modules.store())

    report = Applier(server_dir).install(manifest, {"xp_rate": "2.5"})

    saved = json.loads((server_dir / module_answers.ANSWERS_FILE).read_text(encoding="utf-8"))
    assert saved["settings"]["mod/xp-rates"] == {
        CMANGOS_CONF: {"Rate.XP.Kill": "2.5", "Rate.XP.Quest": "2.5", "Rate.XP.Explore": "2.5"}
    }
    assert report.skipped == (), report.skipped
    assert apply_module.settings_installed(server_dir, manifest)


def test_remove_drops_the_receipt_and_keeps_the_answers(tmp_path: Path) -> None:
    """The answers stay after a Remove (T104), so a re-install is pre-filled; the receipt
    goes, so the row reads Not installed."""
    server_dir = _server(tmp_path, CMANGOS_CONF)
    manifest = _manifest(tortoise_modules.store())
    applier = Applier(server_dir)
    applier.install(manifest, {"xp_rate": "2"})

    applier.remove(manifest)

    assert not apply_module.settings_installed(server_dir, manifest)
    assert "xp-rates" not in apply_module.installed_modules(server_dir).get("mod", frozenset())
    assert module_answers.read_answers(server_dir, manifest) == {"xp_rate": "2"}
    assert "Rate.XP.Kill = 1" in _conf(server_dir, CMANGOS_CONF)


def test_a_receipt_that_cannot_be_written_is_said_in_the_report(tmp_path: Path) -> None:
    """An answers file this build cannot use is left alone, and the report says the row
    will keep reading Not installed."""
    server_dir = _server(tmp_path, CMANGOS_CONF)
    (server_dir / module_answers.ANSWERS_FILE).write_text("{ not json", encoding="utf-8")

    report = Applier(server_dir).install(_manifest(tortoise_modules.store()), {"xp_rate": "2"})

    assert "Rate.XP.Kill = 2" in _conf(server_dir, CMANGOS_CONF)
    said = [line for line in report.skipped if "Not installed" in line]
    assert said and module_answers.ANSWERS_FILE in said[0], report.skipped


def test_no_receipt_when_the_conf_is_not_there(tmp_path: Path) -> None:
    """The install writes nothing into a conf that is missing, and says so; a receipt then
    would read the mod as installed over keys nobody wrote (cold review).

    Mutation: drop the `is_file()` filter in `Applier._record_settings()` and the receipt
    is written.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()

    Applier(server_dir).install(_manifest(tortoise_modules.store()), {"xp_rate": "2"})

    assert not (server_dir / CMANGOS_CONF).exists()
    assert "mod/xp-rates" not in module_answers.settings_keys(server_dir)
    assert "xp-rates" not in apply_module.installed_modules(server_dir).get("mod", frozenset())


def test_a_mod_with_sql_or_a_repository_gets_no_settings_receipt(tmp_path: Path) -> None:
    """Only mods that change settings and nothing else: a mob multiplier keeps its own
    record, and a cloned mod is marked by its folder."""
    assert not apply_module.settings_only(_manifest(wotlk_modules.store(), "baby-mobs"))
    assert not apply_module.settings_only(_manifest(tortoise_modules.store(), "all-stackables"))
    assert not apply_module.settings_only(
        _manifest(tortoise_modules.store(), "tortoise-bots-manager")
    )
    assert apply_module.settings_only(_manifest(tortoise_modules.store(), "motd"))


SETTINGS_ONLY = {
    "wow-tbc": {"all-flight-paths", "cross-faction", "motd", "xp-rates"},
    "wow-tortoise": {"motd", "perf-report", "xp-rates"},
    "wow-vanilla": {"all-flight-paths", "cross-faction", "motd", "xp-rates"},
    "wow-wotlk": {"xp-rates"},
}


@pytest.mark.parametrize(
    ("game", "store"),
    [
        ("wow-tbc", tbc_modules.store()),
        ("wow-tortoise", tortoise_modules.store()),
        ("wow-vanilla", vanilla_modules.store()),
        ("wow-wotlk", wotlk_modules.store()),
    ],
)
def test_the_settings_only_mods_are_the_shipped_conf_only_ones(game: str, store: object) -> None:
    found = {
        m.id
        for kind in ("module", "ale", "keg", "mod")
        for m in store.load_all(kind)  # type: ignore[attr-defined]
        if apply_module.settings_only(m)
    }
    assert found == SETTINGS_ONLY[game]


# ------------------------------------------------------------ installs from before


def _legacy(server_dir: Path, manifest: Manifest, answers: dict[str, str]) -> None:
    """What an install before the receipt left: the answers, and nothing else."""
    assert module_answers.record_answers(server_dir, manifest, answers) == ""


def test_an_older_install_is_recognised_by_its_keys_and_its_saved_answers(
    tmp_path: Path,
) -> None:
    """The live box's state before the hand removal: keys at 3, the answer 3 saved."""
    server_dir = _server(tmp_path, CMANGOS_CONF, STOCK_XP.replace("1", "3"))
    manifest = _manifest(tortoise_modules.store())
    _legacy(server_dir, manifest, {"xp_rate": "3"})

    assert apply_module.settings_installed(server_dir, manifest)
    assert "xp-rates" in apply_module.installed_modules(server_dir, manifests=[manifest])["mod"]


def test_an_older_install_shows_installed_on_the_tab_and_removes(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _server(tmp_path, CMANGOS_CONF, STOCK_XP.replace("1", "3"))
    _legacy(server_dir, _manifest(tortoise_modules.store()), {"xp_rate": "3"})
    view, _asked = _view(TORTOISE, server_dir, {"xp_rate": "3"})
    row = _row(view, "xp-rates")
    assert row.data.badge == "Installed"  # type: ignore[attr-defined]

    questions = _answer_questions(monkeypatch, QMessageBox.StandardButton.Yes)

    row.remove_button.click()  # type: ignore[attr-defined]

    assert len(questions) == 1
    assert "Rate.XP.Kill = 1" in _conf(server_dir, CMANGOS_CONF)
    assert _row(view, "xp-rates").data.badge == "Not installed"  # type: ignore[attr-defined]


def test_keys_that_match_with_no_saved_answer_are_not_an_install(tmp_path: Path) -> None:
    """XP set to 2 by hand (or on the Server rates card) is not Experience Rates installed,
    even though 2 is the mod's own default answer."""
    server_dir = _server(tmp_path, CMANGOS_CONF, STOCK_XP.replace("1", "2"))
    manifest = _manifest(tortoise_modules.store())

    assert not apply_module.settings_installed(server_dir, manifest)


def test_keys_that_moved_since_the_older_install_are_not_an_install(tmp_path: Path) -> None:
    server_dir = _server(tmp_path, CMANGOS_CONF, STOCK_XP.replace("1", "4"))
    manifest = _manifest(tortoise_modules.store())
    _legacy(server_dir, manifest, {"xp_rate": "3"})

    assert not apply_module.settings_installed(server_dir, manifest)


def test_keys_at_the_stock_values_are_not_an_install(tmp_path: Path) -> None:
    """Installed at 1, or removed by hand: the conf is what Remove would leave, so there is
    nothing to remove and the row offers Install."""
    server_dir = _server(tmp_path, CMANGOS_CONF)
    manifest = _manifest(tortoise_modules.store())
    _legacy(server_dir, manifest, {"xp_rate": "1"})

    assert not apply_module.settings_installed(server_dir, manifest)


def test_a_mod_with_no_questions_is_recognised_by_its_keys_alone(tmp_path: Path) -> None:
    """Cross-Faction Play asks nothing, so there is no saved answer to require: its ten
    keys at 1, where Remove writes 0, are the install."""
    manifest = _manifest(vanilla_modules.store(), "cross-faction")
    keys = [key.key for conf in manifest.conf for key in conf.keys]
    server_dir = _server(tmp_path, CMANGOS_CONF, "".join(f"{key} = 1\n" for key in keys))

    assert apply_module.settings_installed(server_dir, manifest)
    stock = _server(tmp_path / "stock", CMANGOS_CONF, "".join(f"{key} = 0\n" for key in keys))
    assert not apply_module.settings_installed(stock, manifest)


def test_no_conf_file_is_not_an_install(tmp_path: Path) -> None:
    manifest = _manifest(tortoise_modules.store())
    _legacy(tmp_path, manifest, {"xp_rate": "3"})

    assert not apply_module.settings_installed(tmp_path, manifest)


def test_keys_at_the_stock_values_spelled_another_way_are_not_an_install(
    tmp_path: Path,
) -> None:
    """TBC's remove writes `Rate.XP.Kill    = 1`, the install `Rate.XP.Kill = 1`: the same
    value, so Remove has nothing to undo, whatever the spacing.

    Mutation: compare the conf's text before and after the remove patches instead
    of each key's value, and an install at 1 reads Installed.
    """
    server_dir = _server(tmp_path, CMANGOS_CONF)
    manifest = _manifest(tbc_modules.store())
    _legacy(server_dir, manifest, {"kill": "1", "quest": "1", "explore": "1"})

    assert not apply_module.settings_installed(server_dir, manifest)


def test_a_receipt_keeps_the_mod_installed_after_its_keys_are_changed(tmp_path: Path) -> None:
    """The receipt is what says installed: XP changed afterwards (by hand, or on the
    Server rates card) does not make the row offer Install again.

    Mutation: drop the receipt write, or stop reading it, and the keys at 5 against
    the saved answer 3 read Not installed.
    """
    server_dir = _server(tmp_path, CMANGOS_CONF)
    manifest = _manifest(tortoise_modules.store())
    Applier(server_dir).install(manifest, {"xp_rate": "3"})
    conf = server_dir / CMANGOS_CONF
    conf.write_text(conf.read_text(encoding="utf-8").replace("= 3", "= 5"), encoding="utf-8")

    assert apply_module.settings_installed(server_dir, manifest)
    assert "xp-rates" in apply_module.installed_modules(server_dir, manifests=[manifest])["mod"]
    assert "xp-rates" in apply_module.installed_modules(server_dir)["mod"]


@pytest.mark.parametrize(
    "change",
    [
        {"sql": ({"db": "world", "statement": "SELECT 1;"},)},
        {"deploy": ({"src": "a.lua", "dest": "lua_scripts/a.lua"},)},
        {"client": ({"src": "Patch-Z.MPQ", "dest": "data"},)},
        {"source": {"repo": "someone/xp-rates"}},
        {"patches": ({"file": "x.conf", "find": "a", "replace": "b", "in_clone": True},)},
        {"conf": ({"file": "etc/mangosd.conf", "keys": ({"key": "Rate.XP.Kill"},)},)},
        {"conf": ({"file": "lua_scripts/xp.lua", "keys": ({"key": "Rate", "default": "1"},)},)},
    ],
    ids=["sql", "deploy", "client", "source", "patch-in-clone", "no-value", "not-a-conf"],
)
def test_anything_beyond_conf_keys_is_not_settings_only(change: dict[str, object]) -> None:
    data = _manifest(tortoise_modules.store()).model_dump(mode="json", exclude_none=True)
    data.update(json.loads(json.dumps(change)))
    from yulon.manifest import parse_manifest

    assert not apply_module.settings_only(parse_manifest(data))


@pytest.mark.parametrize(
    ("entry", "store", "conf", "answers"),
    [
        (WOTLK, wotlk_modules.store(), WOTLK_CONF, {"kill": "3", "quest": "3", "explore": "3"}),
        (TORTOISE, tortoise_modules.store(), CMANGOS_CONF, {"xp_rate": "3"}),
        (TBC, tbc_modules.store(), CMANGOS_CONF, {"kill": "3", "quest": "3", "explore": "3"}),
        (
            VANILLA,
            vanilla_modules.store(),
            CMANGOS_CONF,
            {"kill": "3", "quest": "3", "explore": "3"},
        ),
    ],
    ids=["wotlk", "tortoise", "tbc", "vanilla"],
)
def test_the_real_services_recognise_an_older_install(
    tmp_path: Path, entry: CatalogEntry, store: object, conf: str, answers: dict[str, str]
) -> None:
    """The wiring, not a fixture: each game's reader is handed its settings-only mods.

    Mutation: drop `manifests=` from one game's `installed_modules` and its older
    install reads Not installed.
    """
    server_dir = _server(tmp_path, conf, STOCK_XP.replace("1", "3"))
    _legacy(server_dir, _manifest(store), answers)
    services = ControllerServices.for_entry(entry, server_dir)

    assert services.installed_modules is not None
    assert "xp-rates" in services.installed_modules()["mod"]


# ------------------------------------------------------------ Remove asks first


def _installed_on_tortoise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton
) -> tuple[ControllerView, Path, list[tuple[str, str]]]:
    server_dir = _server(tmp_path, CMANGOS_CONF)
    view, _asked = _view(TORTOISE, server_dir, {"xp_rate": "3"})
    _row(view, "xp-rates").install_button.click()  # type: ignore[attr-defined]
    return view, server_dir, _answer_questions(monkeypatch, answer)


def test_remove_answered_no_changes_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One press of Remove put XP back to 1, a rate set since included, with no question
    (cold review). Now it asks, No by default, and No leaves everything as it was.

    Mutation: drop the question and the conf reads 1 after a No.
    """
    view, server_dir, questions = _installed_on_tortoise(
        tmp_path, monkeypatch, QMessageBox.StandardButton.No
    )
    before = _conf(server_dir, CMANGOS_CONF)

    _row(view, "xp-rates").remove_button.click()  # type: ignore[attr-defined]

    assert len(questions) == 1
    assert _conf(server_dir, CMANGOS_CONF) == before
    assert "mod/xp-rates" in module_answers.settings_keys(server_dir)
    assert _row(view, "xp-rates").data.badge == "Installed"  # type: ignore[attr-defined]
    assert view.module_report.toPlainText() == (
        "remove xp-rates: cancelled — nothing on this machine was changed."
    )


def test_remove_asks_in_plain_words_what_goes_back(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Built from the remove patches over the conf as it is: each setting, by its name on
    the Tuning tab, and the value Remove gives it. No key name, no developer words."""
    view, _server_dir, questions = _installed_on_tortoise(
        tmp_path, monkeypatch, QMessageBox.StandardButton.Yes
    )

    _row(view, "xp-rates").remove_button.click()  # type: ignore[attr-defined]

    ((title, text),) = questions
    assert title == "Remove Experience Rates?"
    assert text == (
        "XP from kills, XP from quests and XP from exploring go back to 1, including any "
        "value you set since Experience Rates was installed.\n\n"
        "Restart the server for this to take effect."
    ), text
    assert "Rate.XP" not in text
    assert text_faults(title + text) == [] and command_faults(title + text) == []


SETTINGS_STORES = [
    ("wow-tbc", tbc_modules.store()),
    ("wow-tortoise", tortoise_modules.store()),
    ("wow-vanilla", vanilla_modules.store()),
    ("wow-wotlk", wotlk_modules.store()),
]


@pytest.mark.parametrize(("game", "store"), SETTINGS_STORES, ids=[g for g, _ in SETTINGS_STORES])
def test_every_settings_mods_remove_question_passes_the_player_text_rules(
    tmp_path: Path, game: str, store: object
) -> None:
    """Every shipped settings-only mod, installed over a conf at the opposite values: the
    question names what changes and carries no ticket, command or backtick."""
    for manifest in store.load_all("mod"):  # type: ignore[attr-defined]
        if not apply_module.settings_only(manifest):
            continue
        server_dir = tmp_path / game / manifest.id
        written = apply_module.settings_written(manifest, {p.key: "7" for p in manifest.prompts})
        for file, keys in written.items():
            (server_dir / file).parent.mkdir(parents=True, exist_ok=True)
            (server_dir / file).write_text(
                "".join(f"{key} = {value}\n" for key, value in keys.items()), encoding="utf-8"
            )
        changes = apply_module.settings_removal(server_dir, manifest)
        assert changes, f"{game} {manifest.id}: Remove changes nothing over an install"
        title, text = remove_question(manifest, changes)
        assert manifest.name in title
        assert text_faults(title + text) == [], (manifest.id, text)
        assert command_faults(title + text) == [], (manifest.id, text)
        assert "`" not in text
