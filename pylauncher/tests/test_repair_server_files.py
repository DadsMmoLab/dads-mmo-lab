"""T106: "Repair server files…" re-renders an old CMaNGOS install's docker-compose.yml.

After an app update an installed Tortoise, Vanilla or TBC server kept the
`docker-compose.yml` it was installed with: nothing rewrote it, so T98's
shutdown protection (the database outlives the world) reached new installs only.
The owner's decision (2026-09-24): a Repair the player presses, offered by a
banner when the install's base compose is not what this version of Yu'lon
renders for it, with a backup first and nothing changed behind anybody's back.

The "old" file in these tests is a real render with T98's database block cut
out, which is the exact shape of every CMaNGOS install made before T98.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tests.test_controller_view import _Ps, _services
from yulon import resources, runner
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.install_wiring import repair_compose_for_app
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import TUNING_RECREATE_LABEL, ControllerView
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
TBC = CATALOG.get("wow-tbc")
CMANGOS = [CATALOG.get(game) for game in ("wow-tbc", "wow-vanilla", "wow-tortoise")]
WOTLK = CATALOG.get("wow-wotlk")

T98_FIRST = "    # THE DATABASE OUTLIVES THE WORLD ON EVERY STOP PATH (T98)."
T98_LAST = '    command: ["mariadbd"]'


def engine(entry: CatalogEntry = TBC, installers_root: Path | None = None) -> CmangosInstaller:
    """A real CMaNGOS engine whose host questions answer like a plain Linux box."""
    return CmangosInstaller(
        entry,
        installers_root=installers_root or resources.installers_dir(),
        seams=native.Seams(
            platform_id=lambda: "linux",
            selinux_enforcing=lambda: False,
            fs_type=lambda path: "ext4",
        ),
    )


def installed(tmp_path: Path, entry: CatalogEntry = TBC) -> Path:
    """A server dir holding what the install's own `generate-compose` stage wrote."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    plan = entry.install.password
    assert plan.file is not None, f"{entry.id} no longer generates its password"
    # Built at runtime: a literal `<word>-<16 hex>` is what the secret scan looks for.
    (server_dir / plan.file).write_text(f"{plan.prefix}{'0' * 16}\n", encoding="utf-8")
    eng = engine(entry)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
        ),
        cancel=None,
        secrets=eng.resolve_secrets(server_dir),
    )
    list(eng.stage_generate_compose(ctx))
    assert (server_dir / composegen.BASE_FILE).is_file()
    return server_dir


def before_t98(text: str) -> str:
    """`text` with T98's database block cut out: a CMaNGOS base file as installed before T98."""
    lines = text.split("\n")
    first = next(i for i, line in enumerate(lines) if line.startswith(T98_FIRST))
    last = lines.index(T98_LAST)
    assert first < last, "T98's block moved; re-read what an old install's file looks like"
    return "\n".join(lines[:first] + lines[last + 1 :])


def make_old(server_dir: Path) -> tuple[str, str]:
    """Put the pre-T98 file in place; return (what was installed, the old text)."""
    path = server_dir / composegen.BASE_FILE
    fresh = path.read_text(encoding="utf-8")
    old = before_t98(fresh)
    assert old != fresh and "yulon-db" not in old and "yulon-db" in fresh
    path.write_text(old, encoding="utf-8", newline="\n")
    return fresh, old


def repair_backups(server_dir: Path) -> list[Path]:
    return sorted(server_dir.glob(composegen.BASE_FILE + ".*.repair.bak"))


# -- the check ---------------------------------------------------------------


@pytest.mark.parametrize("entry", CMANGOS, ids=lambda e: e.id)
def test_a_compose_from_before_t98_is_stale(tmp_path: Path, entry: CatalogEntry) -> None:
    server_dir = installed(tmp_path, entry)
    make_old(server_dir)
    check = engine(entry).base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale", check


def test_the_compose_the_install_wrote_is_current(tmp_path: Path) -> None:
    server_dir = installed(tmp_path)
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "current", check


def test_a_difference_in_comments_alone_asks_for_nothing(tmp_path: Path) -> None:
    """A template whose comments were reworded is not worth a recreate of anybody's server."""
    server_dir = installed(tmp_path)
    path = server_dir / composegen.BASE_FILE
    text = path.read_text(encoding="utf-8")
    edited = text.replace(T98_FIRST, "    # A reworded comment.\n\n" + T98_FIRST, 1)
    assert edited != text
    path.write_text(edited, encoding="utf-8", newline="\n")
    assert engine().base_compose_check(InstallOptions(server_dir=server_dir)).state == "current"


def test_a_file_without_yulons_first_line_is_never_offered(tmp_path: Path) -> None:
    server_dir = installed(tmp_path)
    path = server_dir / composegen.BASE_FILE
    fresh, old = make_old(server_dir)
    path.write_text("# my own compose file\n" + old.split("\n", 1)[1], encoding="utf-8")
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "foreign", check
    assert "does not start with" in check.why


def test_a_moved_install_is_never_offered_because_a_rewrite_would_lose_its_volumes(
    tmp_path: Path,
) -> None:
    """The file's `name:` is the project its characters' volume belongs to (`project_name()`).

    A folder moved after install renders a DIFFERENT project name; writing it
    would bring the server up under a new project with an empty database volume.
    """
    server_dir = installed(tmp_path)
    moved = tmp_path / "moved"
    server_dir.rename(moved)
    make_old(moved)
    check = engine().base_compose_check(InstallOptions(server_dir=moved))
    assert check.state == "moved", check
    before = (moved / composegen.BASE_FILE).read_bytes()
    with pytest.raises(InstallerError, match="new project"):
        engine().repair_base_compose(InstallOptions(server_dir=moved))
    assert (moved / composegen.BASE_FILE).read_bytes() == before
    assert repair_backups(moved) == []


def test_a_missing_compose_is_not_made_by_a_repair(tmp_path: Path) -> None:
    server_dir = installed(tmp_path)
    (server_dir / composegen.BASE_FILE).unlink()
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "missing", check
    with pytest.raises(InstallerError):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert not (server_dir / composegen.BASE_FILE).exists()


# -- the repair --------------------------------------------------------------


def test_repair_writes_exactly_the_fresh_render_backs_up_and_keeps_the_mode(
    tmp_path: Path,
) -> None:
    server_dir = installed(tmp_path)
    path = server_dir / composegen.BASE_FILE
    fresh, old = make_old(server_dir)
    os.chmod(path, 0o640)
    others = {
        name: (server_dir / name).read_bytes()
        for name in (composegen.OVERRIDE_FILE, composegen.BUILD_FILE, composegen.DOTENV_FILE)
    }

    done = engine().repair_base_compose(InstallOptions(server_dir=server_dir))

    assert path.read_text(encoding="utf-8") == fresh, "the repair did not write the fresh render"
    assert path.read_bytes() == fresh.encode("utf-8"), "line endings or encoding changed"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640, "the file's mode was not kept"
    assert done.backup is not None and done.backup.is_file()
    assert repair_backups(server_dir) == [done.backup]
    assert done.backup.read_text(encoding="utf-8") == old, "the backup is not the old file"
    for name, before in others.items():
        assert (server_dir / name).read_bytes() == before, f"{name} was touched"
    assert not list(server_dir.glob("*.yulon-new")), "a temp file was left behind"
    assert engine().base_compose_check(InstallOptions(server_dir=server_dir)).state == "current"


def test_a_repair_of_a_current_file_writes_nothing(tmp_path: Path) -> None:
    server_dir = installed(tmp_path)
    path = server_dir / composegen.BASE_FILE
    mtime = path.stat().st_mtime_ns
    done = engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert done.backup is None
    assert path.stat().st_mtime_ns == mtime
    assert repair_backups(server_dir) == []


def test_a_render_failure_writes_nothing(tmp_path: Path) -> None:
    server_dir = installed(tmp_path)
    make_old(server_dir)
    path = server_dir / composegen.BASE_FILE
    before = path.read_bytes()
    broken = engine(installers_root=tmp_path / "no-templates-here")
    check = broken.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "error", check
    with pytest.raises(InstallerError, match="Nothing was written"):
        broken.repair_base_compose(InstallOptions(server_dir=server_dir))
    assert path.read_bytes() == before
    assert repair_backups(server_dir) == []
    assert sorted(p.name for p in server_dir.iterdir()) == sorted(
        [composegen.BASE_FILE, composegen.OVERRIDE_FILE, composegen.BUILD_FILE]
        + [composegen.DOTENV_FILE, TBC.install.password.file or ""]
    )


def test_a_write_that_fails_leaves_the_old_file_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = installed(tmp_path)
    _fresh, old = make_old(server_dir)

    def refuse(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(native.os, "replace", refuse)
    with pytest.raises(InstallerError, match="No space left"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == old
    assert not list(server_dir.glob("*.yulon-new"))


# -- which installs get it ---------------------------------------------------


@pytest.mark.parametrize("entry", CMANGOS, ids=lambda e: e.id)
def test_every_cmangos_install_is_wired_a_repair(tmp_path: Path, entry: CatalogEntry) -> None:
    assert repair_compose_for_app(entry, tmp_path) is not None


def test_wotlk_is_left_alone(tmp_path: Path) -> None:
    """WotLK's compose is rewritten by Update-to-latest; its override is T94/T101's."""
    assert repair_compose_for_app(WOTLK, tmp_path) is None


def test_a_server_inside_a_wsl_distro_is_not_offered_it(tmp_path: Path) -> None:
    assert repair_compose_for_app(TBC, tmp_path, wsl_distro="Ubuntu") is None


def test_the_wiring_repairs_the_install_it_was_built_for(tmp_path: Path) -> None:
    server_dir = installed(tmp_path)
    fresh, _old = make_old(server_dir)
    route = repair_compose_for_app(TBC, server_dir)
    assert route is not None
    assert route.check().state == "stale"
    # The wiring's engine asks the real host its SELinux/filesystem questions;
    # on a host where they add a `:z` this comparison would say so.
    route.repair()
    assert route.check().state == "current"


# -- the Server tab ----------------------------------------------------------


class _Route:
    """A `ComposeRepairRoute` double that records its presses."""

    def __init__(self, state: str = "stale", fail: str | None = None) -> None:
        self.state = state
        self.fail = fail
        self.repairs = 0
        self.checks = 0

    def check(self) -> native.ComposeCheck:
        self.checks += 1
        return native.ComposeCheck(self.state)  # type: ignore[arg-type]

    def repair(self) -> native.ComposeRepaired:
        self.repairs += 1
        if self.fail is not None:
            raise InstallerError(self.fail)
        self.state = "current"
        return native.ComposeRepaired(
            Path("srv") / composegen.BASE_FILE,
            Path("srv") / (composegen.BASE_FILE + ".20260924-120000-000000.repair.bak"),
        )

    def route(self) -> native.ComposeRepairRoute:
        return native.ComposeRepairRoute(check=self.check, repair=self.repair)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, tmp_path: Path, route: _Route | None) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.repair_compose = route.route() if route is not None else None
    return ControllerView(TBC, services, status_poll_ms=0)


def _answer(monkeypatch: pytest.MonkeyPatch, yes: bool) -> list[str]:
    asked: list[str] = []
    from PySide6.QtWidgets import QMessageBox

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> int:
        asked.append(text)
        button = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        return int(button.value)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_a_stale_compose_shows_the_banner(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    assert route.checks == 1, "the tab did not ask when it was built"
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.text() == controller_view_module.REPAIR_FILES_LABEL
    assert "docker-compose.yml" in view.compose_banner_label.text()


@pytest.mark.parametrize("state", ["current", "foreign", "moved", "missing", "error"])
def test_anything_but_stale_gets_no_banner(
    qapp: object, ps: _Ps, tmp_path: Path, state: str
) -> None:
    view = _view(ps, tmp_path, _Route(state))
    assert view.compose_banner.isHidden()


def test_a_tab_with_no_route_has_no_banner_and_asks_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path, None)
    assert view.compose_banner.isHidden()


def test_pressing_repair_asks_first_and_no_changes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    assert len(asked) == 1 and "backup" in asked[0].lower()
    assert route.repairs == 0
    assert view.compose_banner_button.text() == controller_view_module.REPAIR_FILES_LABEL


def test_a_repair_then_offers_the_recreate_that_applies_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert route.repairs == 1
    assert not view.compose_banner.isHidden()
    assert view.compose_banner_button.text() == TUNING_RECREATE_LABEL
    assert ".repair.bak" in view.compose_banner_label.text(), "the backup is not named"
    # The Tuning tab's own owed-recreate list carries it, so both banners agree.
    assert "docker-compose.yml" in view._tuning_owed.get("recreate", set())
    assert not view.tuning_banner.isHidden()

    removed: list[int] = []
    started: list[int] = []
    view.services.controller.remove = lambda: removed.append(1) or True  # type: ignore
    view.services.controller.start = lambda: started.append(1)  # type: ignore
    view.compose_banner_button.click()
    assert (removed, started) == ([1], [1]), "the banner's Recreate did not recreate"
    assert view.compose_banner.isHidden(), "the banner outlived the recreate that applied it"


def test_a_failed_repair_says_why_and_still_offers_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _Route("stale", fail="the compose template could not be read. Nothing was written.")
    view = _view(ps, tmp_path, route)
    _answer(monkeypatch, yes=True)
    view.compose_banner_button.click()
    assert route.repairs == 1
    assert "Nothing was written" in view.problem_label.text()
    assert view.compose_banner_button.text() == controller_view_module.REPAIR_FILES_LABEL
    assert "docker-compose.yml" not in view._tuning_owed.get("recreate", set())


def test_refresh_asks_again(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    route = _Route("current")
    view = _view(ps, tmp_path, route)
    assert view.compose_banner.isHidden()
    route.state = "stale"
    view.recheck()
    assert route.checks == 2
    assert not view.compose_banner.isHidden()
