"""The Tuning tab draws a card for a module added from outside (T590), end to end.

A module derived from a link has its `.conf.dist` activated as the live `.conf` and no
declared keys. The tab builds a card from that `.conf.dist` and saves it through the one
tuning writer. Fixtures are `tests/test_conf_dist.py`'s real-file shapes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests.test_controller_view import _deploy, _Ps, _services, _with_the_core_confs
from yulon import module_source, runner, tuning
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import modules
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
MODULES = "env/dist/etc/modules"
CONF = f"{MODULES}/mod_x.conf"
TRANSMOG = f"{MODULES}/transmog.conf"

DIST = """\
[worldserver]

#
#    Mod.Enable
#        Description: Turns the module on.
#        Default:     1 - (Enabled)
#                     0 - (Disabled)
#

Mod.Enable = 1

#
#    Mod.MinLevel
#        Description: The lowest level that takes part.
#        Default:     10
#

Mod.MinLevel = 10

# Greeting players see
Mod.Greeting = "Hello"
"""


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _ids(view: ControllerView) -> list[str]:
    return [c.card.module_id for c in view.tuning_panel.cards()]


def _outside_view(
    ps: _Ps, server: Path, user_root: Path, *, conf: str | None, dist: str = DIST
) -> ControllerView:
    """A WotLK view with `mod-x` added by link: its clone has the dist, its manifest is derived."""
    _with_the_core_confs(server)
    clone = server / "modules" / "mod-x"
    _deploy(server, "modules/mod-x/conf/mod_x.conf.dist", dist)
    derived = module_source.complete(
        module_source.derive_link("someone/mod-x", "wow-wotlk", today=date(2026, 10, 9)), clone
    )
    module_source.persist(user_root, derived, shipped_ids=())
    if conf is not None:
        _deploy(server, CONF, conf)
    services: Any = _services(ps, server, [])
    object.__setattr__(services, "store", modules.store(user_root=user_root))
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-x", "mod-transmog"})}
    )
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_a_module_added_by_link_has_a_card_made_from_its_conf_dist(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _outside_view(
        ps, tmp_path / "s", tmp_path / "u", conf="Mod.Enable = 0\nMod.MinLevel = 25\n"
    )
    card = view.tuning_panel.card("mod-x")
    assert list(card.editors) == ["Mod.Enable", "Mod.MinLevel", "Mod.Greeting"]
    assert card.card.module_name and card.card.files == (CONF,)
    rows = {row.key: row for row in card.card.rows}
    assert (rows["Mod.Enable"].type, rows["Mod.Enable"].current) == ("bool", "0")
    assert (rows["Mod.MinLevel"].type, rows["Mod.MinLevel"].current) == ("int", "25")
    assert rows["Mod.Greeting"].current is None
    assert rows["Mod.Enable"].explain == "Turns the module on."
    assert card.editors["Mod.Enable"].value() == "0"


def test_the_card_saves_one_line_and_leaves_every_other_byte(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    original = "# mine\r\nMod.Enable = 0\r\n\r\nMod.MinLevel = 25\r\n"
    view = _outside_view(ps, tmp_path / "s", tmp_path / "u", conf=original)
    path = tmp_path / "s" / CONF
    card = view.tuning_panel.card("mod-x")
    card.editors["Mod.Enable"].control.setChecked(True)
    assert card.save_button is not None
    card.save_button.click()
    assert path.read_bytes() == original.replace("Mod.Enable = 0", "Mod.Enable = 1").encode()
    (backup,) = tuning.backups_of(path)
    assert backup.read_bytes() == original.encode()
    assert backup.name in view.tuning_report.toPlainText()
    assert view.tuning_panel.card("mod-x").editors["Mod.Enable"].value() == "1"


def test_a_value_that_fails_its_inferred_type_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    original = "Mod.Enable = 0\nMod.MinLevel = 25\n"
    view = _outside_view(ps, tmp_path / "s", tmp_path / "u", conf=original)
    path = tmp_path / "s" / CONF
    card = view.tuning_panel.card("mod-x")
    card.editors["Mod.MinLevel"].control.setText("lots")
    assert card.save_button is not None
    card.save_button.click()
    assert path.read_bytes() == original.encode()
    assert tuning.backups_of(path) == ()
    assert "Mod.MinLevel" in view.tuning_report.toPlainText()


def test_a_conf_that_was_linked_out_after_the_card_was_drawn_is_not_written(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _outside_view(ps, tmp_path / "s", tmp_path / "u", conf="Mod.Enable = 0\n")
    path = tmp_path / "s" / CONF
    other = tmp_path / "elsewhere.conf"
    other.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(other)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege here")
    card = view.tuning_panel.card("mod-x")
    card.editors["Mod.Enable"].control.setChecked(True)
    assert card.save_button is not None
    card.save_button.click()
    assert other.read_bytes() == b"Mod.Enable = 0\n"
    assert tuning.backups_of(path) == ()


def test_no_live_conf_means_no_card(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """A Save must never create the module's conf."""
    view = _outside_view(ps, tmp_path / "s", tmp_path / "u", conf=None)
    assert "mod-x" not in _ids(view)
    assert not (tmp_path / "s" / CONF).exists()


def test_the_card_comes_after_the_catalogs_own_and_a_declared_conf_has_one_card(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    server = tmp_path / "s"
    view = _outside_view(ps, server, tmp_path / "u", conf="Mod.Enable = 0\n")
    _deploy(server, TRANSMOG, "[worldserver]\nTransmogrification.Enable = 1\n")
    view.reload_tuning()
    assert _ids(view) == ["server-rates", "mod-transmog", "mod-x"], _ids(view)


def test_the_servers_own_conf_never_gets_a_card_from_a_module_that_names_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A derived manifest whose dist is named like the server's `playerbots.conf`."""
    server = tmp_path / "s"
    _with_the_core_confs(server)
    own = f"{MODULES}/playerbots.conf"
    _deploy(server, own, "AiPlayerbot.Enabled = 1\n")
    _deploy(server, f"{own}.dist", "AiPlayerbot.Enabled = 1\n")
    _deploy(server, "modules/mod-evil/conf/playerbots.conf.dist", "AiPlayerbot.Enabled = 1\n")
    derived = module_source.complete(
        module_source.derive_link("someone/mod-evil", "wow-wotlk", today=date(2026, 10, 9)),
        server / "modules" / "mod-evil",
    )
    module_source.persist(tmp_path / "u", derived, shipped_ids=())
    services: Any = _services(ps, server, [])
    object.__setattr__(services, "store", modules.store(user_root=tmp_path / "u"))
    object.__setattr__(services, "installed_modules", lambda: {"module": frozenset({"mod-evil"})})
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert _ids(view) == ["server-rates"], "the Server rates card is the tab's own"


def test_one_reload_reads_the_installed_modules_once(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Codex review: the declared cards and the dist cards must come from one snapshot."""
    view = _outside_view(ps, tmp_path / "s", tmp_path / "u", conf="Mod.Enable = 0\n")
    reads: list[int] = []
    was = view.services.installed_modules
    assert was is not None

    def counted() -> Any:
        reads.append(1)
        return was()

    object.__setattr__(view.services, "installed_modules", counted)
    view.reload_tuning()
    assert len(reads) == 1, reads
