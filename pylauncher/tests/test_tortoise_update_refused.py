"""T596 step 2 (PR-B): an update an outside server module's new code makes unacceptable goes back.

Real git (a `file://` origin and the host's `git`), as `test_apply_put_back.py` drives it: the
checkout is on the rejected commit by the time the module is read again, so the only honest
answer to a refusal is to put it back where it was.
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

URL = "https://github.com/you/mod-pb"


def _publish(origin: Path, conf: str, label: str) -> str:
    (origin / "src").mkdir(parents=True, exist_ok=True)
    (origin / "conf").mkdir(parents=True, exist_ok=True)
    (origin / "src" / "x.cpp").write_text(f"// {label}\n", encoding="utf-8")
    (origin / "conf" / "mod-pb.conf.dist").write_text(conf, encoding="utf-8")
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


def test_an_update_the_new_settings_file_refuses_is_put_back_and_not_offered_again(
    tmp_path: Path,
) -> None:
    applier, origin, server = _rig(tmp_path)
    first = _publish(origin, "[Pb]\nOn = 1\n", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    clone = server / "modules" / "mod-pb"
    assert _git(clone, "rev-parse", "HEAD") == first
    second = _publish(origin, "On = 1\n", "v2")  # no [Section]: the world would not start

    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    with pytest.raises(CompletionRefused) as refused:
        applier.update(listed["mod-pb"])

    said = str(refused.value)
    assert "no [Section] line" in said
    assert "put it back on the version it was on" in said
    assert _git(clone, "rev-parse", "HEAD") == first, "the rejected commit is not what is built"
    ledger = module_moves.read(server)
    assert ledger is not None and ledger.skipped["module/mod-pb"].tip == second
    assert (
        (server / "etc" / "modules" / "mod-pb.conf").read_text(encoding="utf-8").startswith("[Pb]")
    )


def test_an_update_the_new_code_does_not_refuse_is_kept(tmp_path: Path) -> None:
    applier, origin, server = _rig(tmp_path)
    _publish(origin, "[Pb]\nOn = 1\n", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    second = _publish(origin, "[Pb]\nOn = 2\n", "v2")

    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    applier.update(listed["mod-pb"])

    assert _git(server / "modules" / "mod-pb", "rev-parse", "HEAD") == second


def test_a_put_back_that_is_itself_refused_keeps_the_first_refusal_and_says_what_is_built(
    tmp_path: Path,
) -> None:
    """Codex review: PutBackRefused is an ApplyError, so it is caught, and said, not swapped in."""
    from yulon.apply import PutBackRefused

    applier, origin, server = _rig(tmp_path)
    _publish(origin, "[Pb]\nOn = 1\n", "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    second = _publish(origin, "On = 1\n", "v2")

    def refuses(*args: object, **kwargs: object) -> object:
        raise PutBackRefused("it has been changed since its last update.", edited=False)

    applier.put_back = refuses  # type: ignore[method-assign]
    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    with pytest.raises(CompletionRefused) as refused:
        applier.update(listed["mod-pb"])

    said = str(refused.value)
    assert said.startswith("mod-pb's settings file conf/mod-pb.conf.dist has no [Section] line")
    assert "could not put it back on the version it was on" in said
    assert "it has been changed since its last update" in said
    assert "the version that was just fetched" in said
    assert _git(server / "modules" / "mod-pb", "rev-parse", "HEAD") == second
