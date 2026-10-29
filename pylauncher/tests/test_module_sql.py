"""The WotLK binding for "apply the module SQL that was never applied" (Lane C, 8.x).

`yulon/docker.py` owns the refusals and the argv; this file is only about the
per-game half — that the route exists at all, and that it reaches the importer
this game declares rather than one spelled a second time here (style-guide §3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yulon import docker
from yulon.controller_wow_wotlk import docker_ctl, modules


def test_the_module_route_runs_this_games_own_importer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service name is `docker_ctl.SPEC`'s, never a second copy of "ac-db-import".

    A per-game fact spelled twice is a per-game fact that can disagree with
    itself, and this one decides which container is allowed to write the
    databases.
    """
    seen: dict[str, Any] = {}

    def fake(spec: docker.ContainerSpec, server_dir: Path, **kwargs: Any) -> object:
        seen["spec"] = spec
        seen["server_dir"] = server_dir
        seen["kwargs"] = kwargs
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "apply_module_sql", fake)
    sink: list[str] = []
    run = modules.apply_module_sql(
        Path("/tmp/wow"), spec=docker_ctl.SPEC, output=sink.append, ledger=None
    )
    assert seen["spec"] is docker_ctl.SPEC
    assert seen["spec"].import_service == "ac-db-import"
    assert seen["server_dir"] == Path("/tmp/wow")
    assert seen["kwargs"]["output"] is not None
    assert run.returncode == 0


def test_the_coa_press_does_not_hand_the_importer_its_cores_own_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T812 D14: the CoA importer adds `mod-ascension` itself; a second one aborts it."""
    seen: dict[str, Any] = {}

    def fake(spec: docker.ContainerSpec, server_dir: Path, **kwargs: Any) -> object:
        seen["modules"] = kwargs["modules"]
        return docker.AttachedRun(0, ("done",))

    for name in ("mod-ah-bot-plus", "mod-ascension", "mod-playerbots"):
        (tmp_path / "modules" / name).mkdir(parents=True)
    monkeypatch.setattr(docker, "apply_module_sql", fake)
    said: list[str] = []
    modules.apply_module_sql(
        tmp_path, spec=docker_ctl.SPEC, output=said.append, ledger=None, user_game="wow-coa"
    )

    assert seen["modules"] == "mod-ah-bot-plus,mod-playerbots"
