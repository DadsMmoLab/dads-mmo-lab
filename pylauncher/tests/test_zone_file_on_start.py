"""Every Start puts a CMaNGOS server's zone file back, and never refuses over it (T171 round 3).

The images carry no zone files, so a `zoneinfo/<Area>/<City>` deleted, cut
short or left from an older Yu'lon would start the server on UTC without a
word. `Controller.start()` is the one door every Start, Restart, recreate and
the Tuning tab's recreate goes through; the install's `up` and a rebuild's
recreate, which start without the controller, ask the same function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import docker, resources, server_time_zone
from yulon.catalog import composegen, time_zone
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller import Controller

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
OSLO = "Europe/Oslo"


def _installed_with_oslo(server_dir: Path, entry: CatalogEntry = TBC) -> Path:
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password="pw",
        platform_id=lambda: "linux",
    )
    (server_dir / composegen.BASE_FILE).write_text(plan.base, encoding="utf-8")
    (server_dir / composegen.OVERRIDE_FILE).write_text(plan.override, encoding="utf-8")
    server_time_zone.write(entry, server_dir, OSLO)
    return server_dir / "zoneinfo" / "Europe" / "Oslo"


def _start(
    monkeypatch: pytest.MonkeyPatch, server_dir: Path, entry: CatalogEntry = TBC
) -> Controller:
    started: list[Path] = []

    def start_staged(spec: docker.ContainerSpec, where: Path, **_kw: object) -> bool:
        started.append(where)
        return True

    monkeypatch.setattr(docker, "start_staged", start_staged)
    controller = Controller(entry.container_spec(), server_dir)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    controller.start()
    assert started == [server_dir], "the Start ran"
    return controller


@pytest.mark.parametrize(
    "damage",
    [
        lambda path: path.unlink(),
        lambda path: path.write_bytes(path.read_bytes()[:40]),
        lambda path: path.write_bytes(b"TZif2 the rule an older Yu'lon shipped"),
    ],
    ids=["deleted", "truncated", "outdated"],
)
def test_start_puts_the_zone_file_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: object
) -> None:
    placed = _installed_with_oslo(tmp_path)
    damage(placed)  # type: ignore[operator]
    assert not time_zone.ready(TBC, tmp_path, OSLO), "control: the file is not the zone's now"

    controller = _start(monkeypatch, tmp_path)

    assert placed.read_bytes() == time_zone.zone_file(OSLO)
    assert controller.zone_problem is None


def test_a_zone_file_that_cannot_be_put_back_still_lets_the_server_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    _installed_with_oslo(tmp_path)
    shutil.rmtree(tmp_path / "zoneinfo")
    (tmp_path / "zoneinfo").write_text("a file where the folder was", encoding="utf-8")

    controller = _start(monkeypatch, tmp_path)

    said = controller.zone_problem
    assert said is not None and "UTC" in said and "Repair" in said and "Apply" in said
    assert "zoneinfo/Europe/Oslo" in said


def test_a_good_zone_file_is_left_alone_by_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    placed = _installed_with_oslo(tmp_path)
    stamp = placed.stat().st_mtime_ns

    _start(monkeypatch, tmp_path)

    assert placed.stat().st_mtime_ns == stamp


def test_wotlk_start_brings_no_zone_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AzerothCore's image has its own tzdata."""
    _installed_with_oslo(tmp_path, WOTLK)

    controller = _start(monkeypatch, tmp_path, WOTLK)

    assert not (tmp_path / "zoneinfo").exists() and controller.zone_problem is None


def test_the_dashboards_restart_says_it_too(tmp_path: Path) -> None:
    """The bot dashboard's Off restarts through the same controller; its log says the rest."""
    from yulon.controller_wow_tortoise import botdash

    tortoise = CATALOG.get("wow-tortoise")

    class _Restarted(Controller):
        def stop(self) -> bool:
            return True

        def start(self) -> None:
            self.zone_problem = "the time zone file could not be put back (x), so UTC"

    dashboard = botdash.Dashboard(
        tortoise, tmp_path, _Restarted(tortoise.container_spec(), tmp_path)
    )

    said = list(dashboard.restart_world())

    assert said[-1] == "Note: the time zone file could not be put back (x), so UTC"
