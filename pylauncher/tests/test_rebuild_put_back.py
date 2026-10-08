"""A Rebuild that fails on an updated module puts that module back (T557 step 4).

`install_wiring.rebuild_for_app()` around a fake engine whose lines and failure
are the test's, over real git clones (`tests.test_apply_put_back`'s rig): the
put-back is the real `apply.failed_build_put_back()` over a real `Applier`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY
from tests.test_apply_put_back import URL, _git, _LocalOrigin, _manifest, _publish
from yulon import install_wiring, module_moves
from yulon.apply import Applier, failed_build_put_back
from yulon.catalog.installer import InstallerError, InstallStopped, WorldStoppedAfterReadyError
from yulon.catalog.native import RebuildChangedTheServer
from yulon.git import CloneSpec, git_available
from yulon.manifest import Manifest

pytestmark = pytest.mark.skipif(not git_available(), reason="needs a host git")

ERROR_X = (
    "#12 512.3 /azerothcore/modules/mod-x/src/x.cpp:376:68: fatal error: too many arguments "
    "to function call, expected 4, have 5"
)
FAILED = "the build failed (exit 1). Its last words were: …"


class _Engine:
    def __init__(self, lines: Sequence[str], error: BaseException | None) -> None:
        self.lines = lines
        self.error = error
        self.closed = False

    def _run(self) -> Iterator[str]:
        try:
            yield from self.lines
            if self.error is not None:
                raise self.error
        finally:
            self.closed = True

    def rebuild(self, options: Any, *, cancel: Any = None, missing_images_ok: bool = False) -> Any:
        # Held, so only an explicit close() can end it early: a generator nothing
        # refers to is closed by the collector, which would hide a missing close.
        self.running = self._run()
        return self.running

    def update_to_latest(self, options: Any, **_kwargs: Any) -> Any:
        self.running = self._run()
        return self.running


class _Origins:
    """The clone seam for one origin per module, chosen by the clone's folder name."""

    def __init__(self, origins: dict[str, Path]) -> None:
        self.by_id = {item: _LocalOrigin(origin) for item, origin in origins.items()}

    def clone(self, spec: CloneSpec) -> None:
        self.by_id[spec.dest.name].clone(spec)

    def clone_lines(self, spec: CloneSpec, *, stage: str = "clone") -> Iterator[str]:
        return self.by_id[spec.dest.name].clone_lines(spec, stage=stage)


class _Two:
    """Two compiled modules, mod-x and mod-y, on one server, each installed at A."""

    def __init__(self, tmp_path: Path) -> None:
        self.server = tmp_path / "server"
        self.server.mkdir()
        self.origins = {item: tmp_path / item for item in ("mod-x", "mod-y")}
        for origin in self.origins.values():
            origin.mkdir()
            _git(origin, "init", "-q", "-b", "main")
        self.manifests = {
            "mod-x": _manifest(),
            "mod-y": _manifest().model_copy(update={"id": "mod-y"}),
        }
        self.applier = Applier(
            self.server, git=_Origins(self.origins), remote_url=lambda _dest: URL
        )
        self.a = {item: _publish(origin, "A") for item, origin in self.origins.items()}
        for manifest in self.manifests.values():
            self.applier.install(manifest)

    def update(self, *items: str) -> dict[str, str]:
        tips = {item: _publish(self.origins[item], "B") for item in items}
        for item in items:
            self.applier.update(self.manifests[item])
        return tips

    def head(self, item: str) -> str:
        return _git(self.server / "modules" / item, "rev-parse", "HEAD")

    def load(self, kind: str, item_id: str) -> Manifest:
        return self.manifests[item_id]

    def ledger(self) -> module_moves.Ledger:
        ledger = module_moves.read(self.server)
        assert ledger is not None
        return ledger


def _press(
    monkeypatch: pytest.MonkeyPatch,
    two: _Two,
    lines: Sequence[str],
    error: BaseException | None,
    *,
    cancel: threading.Event | None = None,
) -> tuple[list[str], _Engine]:
    engine = _Engine(lines, error)
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: engine)
    rebuild = install_wiring.rebuild_for_app(
        ENTRY, two.server, put_back=failed_build_put_back(two.applier, two.load)
    )
    said: list[str] = []
    for line in rebuild(cancel):
        said.append(line)
    return said, engine


def test_failed_rebuild_naming_an_updated_module_puts_only_it_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test 5: the error names mod-x; both were updated; only mod-x goes back (D6)."""
    two = _Two(tmp_path)
    tips = two.update("mod-x", "mod-y")

    with pytest.raises(InstallerError) as failed:
        _press(
            monkeypatch, two, ["Rebuilding", ERROR_X, "make: *** Error 1"], InstallerError(FAILED)
        )

    assert two.head("mod-x") == two.a["mod-x"]
    assert two.head("mod-y") == tips["mod-y"], "a module the error did not name was touched"
    message = str(failed.value)
    assert message.startswith(FAILED)
    assert (
        "The build stopped on an error in mod-x, which was updated after your last build that "
        f"worked. Yu'lon put mod-x back on the version it had before that update "
        f"({two.a['mod-x'][:7]}). Your server is still running the build it had. Press "
        "“Rebuild the server…” to build your other updates without it."
    ) in message
    ledger = two.ledger()
    assert "module/mod-x" not in ledger.moves
    assert ledger.skipped["module/mod-x"].tip == tips["mod-x"]
    assert ledger.moves["module/mod-y"].to_sha == tips["mod-y"], "mod-y's update is still waiting"


def test_a_put_back_with_nothing_else_waiting_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    two = _Two(tmp_path)
    two.update("mod-x")

    with pytest.raises(InstallerError) as failed:
        _press(monkeypatch, two, [ERROR_X], InstallerError(FAILED))

    assert two.head("mod-x") == two.a["mod-x"]
    assert str(failed.value).endswith(
        "Your server is still running the build it had, and nothing else is waiting to be built."
    )


def test_the_failure_keeps_its_type_and_its_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Own(InstallerError):
        pass

    two = _Two(tmp_path)
    two.update("mod-x")
    error = _Own(FAILED, detail="docker compose logs")

    with pytest.raises(_Own) as failed:
        _press(monkeypatch, two, [ERROR_X], error)

    assert failed.value is error and failed.value.detail == "docker compose logs"


def test_good_rebuild_settles_every_move(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test 11: the press returned, so every clone on disk was compiled; skips are kept."""
    two = _Two(tmp_path)
    two.update("mod-x", "mod-y")
    module_moves.skip(two.server, "module/mod-z", tip="f" * 40)
    said: list[str] = []

    def seen(line: str) -> None:
        said.append(line)
        assert two.ledger().moves, "settled before the build finished"

    engine = _Engine(["one", "two"], None)
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: engine)
    for line in install_wiring.rebuild_for_app(ENTRY, two.server)(None):
        seen(line)

    assert said == ["one", "two"]
    ledger = two.ledger()
    assert ledger.moves == {}
    assert ledger.skipped["module/mod-z"].tip == "f" * 40


def test_a_kept_build_settles_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The compile finished and the world came up, then stopped: the build was kept."""
    two = _Two(tmp_path)
    two.update("mod-x")

    with pytest.raises(WorldStoppedAfterReadyError):
        _press(monkeypatch, two, ["built"], WorldStoppedAfterReadyError("kept"))

    assert two.ledger().moves == {}


@pytest.mark.parametrize("how", ["stop-said", "cancel-set"])
def test_stopped_rebuild_neither_settles_nor_puts_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, how: str
) -> None:
    """Test 12."""
    two = _Two(tmp_path)
    tips = two.update("mod-x")
    cancel = threading.Event()
    if how == "cancel-set":
        cancel.set()
        error: InstallerError = InstallerError(FAILED)
    else:
        error = InstallStopped("Stopped.")
    before = (two.server / module_moves.MOVES_FILE).read_text(encoding="utf-8")

    with pytest.raises(InstallerError) as failed:
        _press(monkeypatch, two, [ERROR_X], error, cancel=cancel)

    assert failed.value is error and "put" not in str(error)
    assert two.head("mod-x") == tips["mod-x"]
    assert (two.server / module_moves.MOVES_FILE).read_text(encoding="utf-8") == before


def test_a_failure_the_old_build_is_not_back_from_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`TrueAfterStop`: the new build may be what runs; the sources stay with it."""
    two = _Two(tmp_path)
    tips = two.update("mod-x")

    with pytest.raises(RebuildChangedTheServer):
        _press(monkeypatch, two, [ERROR_X], RebuildChangedTheServer("changed", up=False))

    assert two.head("mod-x") == tips["mod-x"]
    assert "module/mod-x" in two.ledger().moves


def test_error_naming_no_updated_module_changes_nothing_and_lists_waiting_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test 14."""
    two = _Two(tmp_path)
    tips = two.update("mod-x", "mod-y")

    with pytest.raises(InstallerError) as failed:
        _press(monkeypatch, two, ["make: *** Error 1"], InstallerError(FAILED))

    assert two.head("mod-x") == tips["mod-x"] and two.head("mod-y") == tips["mod-y"]
    assert str(failed.value).endswith(
        "The build stopped, and the error does not say which module caused it. These modules "
        "were updated since your last build that worked: mod-x, mod-y. To build without one of "
        "them, right-click it on the Modules tab, choose Put back the last update, then press "
        "“Rebuild the server…”."
    )


def test_an_error_in_a_module_that_was_not_updated_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    two = _Two(tmp_path)
    tips = two.update("mod-y")

    with pytest.raises(InstallerError) as failed:
        _press(monkeypatch, two, [ERROR_X], InstallerError(FAILED))

    assert two.head("mod-x") == two.a["mod-x"] and two.head("mod-y") == tips["mod-y"]
    assert (
        "The build stopped on an error in mod-x. mod-x was not updated since your last build "
        "that worked, so Yu'lon did not change it."
    ) in str(failed.value)


def test_an_update_known_only_from_the_reflog_is_not_put_back_by_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D4: the reported player's own state, a pre-T557 update."""
    two = _Two(tmp_path)
    tips = two.update("mod-x")
    (two.server / module_moves.MOVES_FILE).unlink()

    with pytest.raises(InstallerError) as failed:
        _press(monkeypatch, two, [ERROR_X], InstallerError(FAILED))

    assert two.head("mod-x") == tips["mod-x"]
    assert "right-click mod-x on the Modules tab, choose Put back the last update" in str(
        failed.value
    )


def test_unreadable_ledger_puts_nothing_back_automatically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test 18: fail closed, and the torn record is left as it was."""
    two = _Two(tmp_path)
    tips = two.update("mod-x")
    (two.server / module_moves.MOVES_FILE).write_text("{torn", encoding="utf-8")

    with pytest.raises(InstallerError):
        _press(monkeypatch, two, [ERROR_X], InstallerError(FAILED))

    assert two.head("mod-x") == tips["mod-x"]
    assert (two.server / module_moves.MOVES_FILE).read_text(encoding="utf-8") == "{torn"


def test_a_put_back_refused_on_an_edited_tree_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    two = _Two(tmp_path)
    tips = two.update("mod-x")
    (two.server / "modules" / "mod-x" / "src" / "x.cpp").write_text("// mine\n", encoding="utf-8")

    with pytest.raises(InstallerError) as failed:
        _press(monkeypatch, two, [ERROR_X], InstallerError(FAILED))

    assert two.head("mod-x") == tips["mod-x"]
    assert "files in its folder were changed after the update" in str(failed.value)


def test_update_to_latest_failure_never_puts_a_module_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test 13: the T64 route's failure leaves the module where it is."""
    two = _Two(tmp_path)
    tips = two.update("mod-x")
    engine = _Engine([ERROR_X], InstallerError(FAILED))
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: engine)
    route = install_wiring.update_to_latest_for_app(ENTRY, two.server)
    assert route is not None

    with pytest.raises(InstallerError) as failed:
        list(route.press(None))

    assert str(failed.value) == FAILED
    assert two.head("mod-x") == tips["mod-x"]
    assert "module/mod-x" in two.ledger().moves


@pytest.mark.parametrize("press", ["press", "to_pin"])
def test_update_to_latest_success_settles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, press: str
) -> None:
    two = _Two(tmp_path)
    two.update("mod-x")
    engine = _Engine(["done"], None)
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: engine)
    route = install_wiring.update_to_latest_for_app(ENTRY, two.server)
    assert route is not None

    assert list(getattr(route, press)(None)) == ["done"]

    assert two.ledger().moves == {}


def test_closing_the_press_closes_the_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A consumer that stops reading early still lets the engine's own cleanup run."""
    two = _Two(tmp_path)
    engine = _Engine(["one", "two"], None)
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda _entry, **_kw: engine)
    lines = install_wiring.rebuild_for_app(ENTRY, two.server)(None)

    assert next(lines) == "one"
    lines.close()

    assert engine.closed
