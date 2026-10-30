"""T829: a bring-in puts a module's requirement in before the module, and never sticks on one.

A player's whole-server package held Loot Pet (an ale script) and mod-ale (the Lua engine it
needs). The pack lists its modules folder by folder in name order, `ale_scripts/` before
`modules/`, so the bring-in asked for Loot Pet first. The applier refused it ("lootpet needs
mod-ale, which is not installed here"), the move said press again, and the next press asked for
Loot Pet first again: the same sentence on every press, and mod-ale never reached.

The applier here is a double that answers a requirement the way the real one does: it reads
the server folder through `apply.missing_requirements()` and `apply.installed_modules()`, and
an install leaves its clone folder behind.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.test_move_server_flow import CONF, STORE, WOTLK, Move, whole_facts
from yulon import apply, move_server
from yulon.catalog import native
from yulon.manifest import Manifest
from yulon.move import PackedModule, PackFile, ServerFacts, ServerSpec

LOOTPET = "b" * 40
MOD_ALE = "e" * 40
LEFT_OUT = (
    "!! Loot Pet was left out because it needs AzerothCore Lua Engine (ALE), which the package "
    "does not hold; once this server runs, add AzerothCore Lua Engine (ALE) and then Loot Pet "
    "on the Modules tab."
)


def _packed(kind: str, item_id: str, commit: str) -> PackedModule:
    manifest = STORE.load(kind, item_id)  # type: ignore[arg-type]
    assert manifest.source is not None
    return PackedModule(
        type=kind,  # type: ignore[arg-type]
        id=item_id,
        origin="catalog",
        repo=manifest.source.repo,
        commit=commit,
    )


def _facts(*modules: PackedModule) -> ServerFacts:
    base = whole_facts()
    return ServerFacts(
        spec=ServerSpec(sources=base.spec.sources, modules=modules),
        files=(PackFile(kind="conf", target="env/dist/etc/worldserver.conf", data=CONF),),
    )


def _as_the_pack_lists_them() -> ServerFacts:
    """Loot Pet first: `ale_scripts/` sorts before `modules/` (the reported package)."""
    return _facts(_packed("ale", "lootpet", LOOTPET), _packed("module", "mod-ale", MOD_ALE))


def _ale_first() -> ServerFacts:
    return _facts(_packed("module", "mod-ale", MOD_ALE), _packed("ale", "lootpet", LOOTPET))


def _without_ale() -> ServerFacts:
    return _facts(_packed("ale", "lootpet", LOOTPET))


def _requiring_applier(mv: Move) -> None:
    """The real applier's requirement refusal, read off the disk; an install leaves its folder."""
    real = mv.applier.install
    server_dir = mv.target.server_dir

    def install(
        manifest: Manifest, values: Mapping[str, str] | None = None, **kw: bool
    ) -> apply.ApplyReport:
        missing = apply.missing_requirements(manifest, apply.installed_modules(server_dir))
        if missing:
            raise apply.ApplyRefusal(
                apply.requirement_refusal(manifest.id, missing[0]) + " Nothing was changed."
            )
        report = real(manifest, values, **kw)
        (server_dir / apply.CLONE_DIRS[manifest.type] / manifest.id).mkdir(parents=True)
        return report

    mv.applier.install = install  # type: ignore[method-assign]


def _installed(mv: Move) -> list[str]:
    return [item_id for item_id, _rev, _values in mv.applier.installed]


def test_the_pack_lists_an_ale_script_before_the_engine_it_needs(tmp_path: Path) -> None:
    """The reported package's order, from the real pack: Loot Pet is listed before mod-ale."""
    server_dir = tmp_path / "old"
    for folder, item_id, sha in (
        ("modules", "mod-ale", MOD_ALE),
        ("ale_scripts", "lootpet", LOOTPET),
    ):
        git = server_dir / folder / item_id / ".git"
        git.mkdir(parents=True)
        (git / "HEAD").write_text(sha + "\n", encoding="utf-8")

    def load(kind: str, item_id: str) -> Manifest | None:
        try:
            return STORE.load(kind, item_id)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            return None

    modules, _files, refusals = move_server._modules_of(WOTLK, server_dir, load)
    assert refusals == []
    assert [m.id for m in modules] == ["lootpet", "mod-ale"]


@pytest.mark.parametrize("facts", [_as_the_pack_lists_them, _ale_first])
def test_the_engine_goes_in_before_the_script_that_needs_it(tmp_path: Path, facts: object) -> None:
    """Mutation this catches: the plan's modules left in the package's order."""
    mv = Move(tmp_path, facts=facts)  # type: ignore[arg-type]
    assert [p.manifest.id for p in mv.plan.modules] == ["mod-ale", "lootpet"]
    assert "Modules installed again: AzerothCore Lua Engine (ALE), Loot Pet." in mv.plan.text()
    _requiring_applier(mv)
    lines = mv.run()
    assert _installed(mv) == ["mod-ale", "lootpet"]
    assert not any("Loot Pet" in line and "left out" in line for line in lines)
    assert move_server.read_marker(mv.target.server_dir) is None  # the move finished


def test_a_press_stuck_on_the_old_order_carries_on_now(tmp_path: Path) -> None:
    """The player's own folder: the modules step stopped on Loot Pet with nothing done.

    The record a stuck press left (every step before the modules done, no module done) is
    carried on by the next press: mod-ale first, then Loot Pet, and the move ends.
    """
    mv = Move(tmp_path, facts=_as_the_pack_lists_them)
    digest = move_server.package_digest(mv.plan.manifest)  # type: ignore[arg-type]
    mv.target.server_dir.mkdir()
    (mv.target.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
    move_server.write_marker(mv.target.server_dir, digest, ["revs", "answers"], modules_done=[])
    _requiring_applier(mv)
    mv.run()
    assert _installed(mv) == ["mod-ale", "lootpet"]
    assert mv.rebuilds == 1


def test_a_script_whose_engine_the_package_lacks_is_left_out_and_the_move_ends(
    tmp_path: Path,
) -> None:
    """The requirement no press can meet: the rest comes over and the sentence says what to do.

    Mutation this catches: the requirement asked only of the applier (the move then stops
    with "could not be installed again", the same on every press).
    """
    mv = Move(tmp_path, facts=_without_ale)
    _requiring_applier(mv)
    lines = mv.run()
    assert _installed(mv) == []
    said = [line for line in lines if "Loot Pet" in line and "left out" in line]
    # Said when the step passes it, and again at the end, where the player reads what it did.
    assert said == [LEFT_OUT, LEFT_OUT]
    assert lines[-1] == LEFT_OUT
    assert any(e.startswith("load:") for e in mv.events)  # the data still went in
    assert move_server.read_marker(mv.target.server_dir) is None


def test_a_left_out_module_is_still_named_at_the_end_of_a_later_press(tmp_path: Path) -> None:
    """The modules step finished on an earlier press; the closing still names what it left out."""
    mv = Move(tmp_path, facts=_without_ale)
    _requiring_applier(mv)
    # One press ends after the modules (the record says so), the next one finishes.
    digest = move_server.package_digest(mv.plan.manifest)  # type: ignore[arg-type]
    mv.target.server_dir.mkdir()
    (mv.target.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
    move_server.write_marker(
        mv.target.server_dir,
        digest,
        ["revs", "answers", "modules"],
        modules_done=[],
        modules_left_out=[LEFT_OUT],
    )
    lines = mv.run()
    assert _installed(mv) == []
    assert lines[-1].startswith("!! Loot Pet was left out")
