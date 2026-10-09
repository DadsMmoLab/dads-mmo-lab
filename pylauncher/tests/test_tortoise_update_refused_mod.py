"""T611 (1): a refused update of an add-on or database package goes back, like a module's.

Real git, as `test_tortoise_update_refused.py` drives it. Nothing is compiled from such an
item, but its clone and its record must not disagree: the clone left on the rejected
commit is one the record never read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import module_moves
from yulon.apply import CompletionRefused
from yulon.controller_wow_tortoise import autoupdate
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.git import git_available

from .test_apply_put_back import _git, _LocalOrigin
from .test_tortoise_custom import _Db

pytestmark = pytest.mark.skipif(not git_available(), reason="needs a host git")

URL = "https://github.com/you/MobStats"


def _publish(origin: Path, interface: str, label: str) -> str:
    (origin / "MobStats.toc").write_text(
        f"## Interface: {interface}\n## Title: MobStats\nMobStats.lua\n", encoding="utf-8"
    )
    (origin / "MobStats.lua").write_text(f"-- {label}\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", label)
    return _git(origin, "rev-parse", "HEAD")


def _rig(tmp_path: Path) -> tuple[autoupdate.GuardedApplier, Path, Path]:
    origin, server = tmp_path / "origin", tmp_path / "server"
    origin.mkdir()
    server.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    applier = tortoise_modules.applier(
        server,
        sql=_Db(),
        arming=lambda: autoupdate.Arming(enabled=False),
        world_running=lambda: False,
        git=_LocalOrigin(origin),  # type: ignore[arg-type]
        client_dir=None,
    )
    applier.remote_url = lambda _dest: URL  # type: ignore[method-assign]
    return applier, origin, server


def _installed(applier: autoupdate.GuardedApplier) -> object:
    listed = {m.id: m for m in tortoise_modules.store().load_all("mod")}
    return listed["mobstats"]


def test_a_refused_update_of_an_addon_is_put_back_and_not_offered_again(tmp_path: Path) -> None:
    applier, origin, server = _rig(tmp_path)
    first = _publish(origin, "11200", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    clone = server / "sql_scripts" / "clones" / "mobstats"
    assert _git(clone, "rev-parse", "HEAD") == first
    second = _publish(origin, "20400", "v2")  # now made for another game version

    with pytest.raises(CompletionRefused) as refused:
        applier.update(_installed(applier))  # type: ignore[arg-type]

    said = str(refused.value)
    assert "made for another game version" in said
    assert "put it back on the version it was on" in said
    assert _git(clone, "rev-parse", "HEAD") == first, "the clone agrees with its record again"
    ledger = module_moves.read(server)
    assert ledger is not None and ledger.skipped["mod/mobstats"].tip == second


def test_an_addon_update_that_is_accepted_is_kept(tmp_path: Path) -> None:
    applier, origin, server = _rig(tmp_path)
    _publish(origin, "11200", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    second = _publish(origin, "11200", "v2")

    applier.update(_installed(applier))  # type: ignore[arg-type]

    assert _git(server / "sql_scripts" / "clones" / "mobstats", "rev-parse", "HEAD") == second


def test_a_put_back_that_cannot_be_made_says_what_the_clone_is_on(tmp_path: Path) -> None:
    applier, origin, server = _rig(tmp_path)
    _publish(origin, "11200", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    second = _publish(origin, "20400", "v2")

    def cannot(dest: Path, rev: str) -> None:
        raise OSError("disk full")

    applier.git.restore_rev = cannot  # type: ignore[attr-defined,method-assign]
    with pytest.raises(CompletionRefused) as refused:
        applier.update(_installed(applier))  # type: ignore[arg-type]

    said = str(refused.value)
    assert "made for another game version" in said
    assert "could not put it back on the version it was on (disk full)" in said
    assert _git(server / "sql_scripts" / "clones" / "mobstats", "rev-parse", "HEAD") == second


def test_a_refusal_when_nothing_moved_hides_no_version(tmp_path: Path) -> None:
    """A rule that changed (not the author's push) refuses an Update to the commit it is on."""
    applier, origin, server = _rig(tmp_path)
    first = _publish(origin, "11200", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)

    def refuses(manifest: object, clone: Path) -> object:
        raise CompletionRefused("Not any more.")

    applier.recomplete = refuses  # type: ignore[assignment]
    with pytest.raises(CompletionRefused) as refused:
        applier.update(_installed(applier))  # type: ignore[arg-type]

    assert str(refused.value).endswith("nothing else was changed.")
    assert _git(server / "sql_scripts" / "clones" / "mobstats", "rev-parse", "HEAD") == first
    ledger = module_moves.read(server)
    assert ledger is None or "mod/mobstats" not in ledger.skipped
