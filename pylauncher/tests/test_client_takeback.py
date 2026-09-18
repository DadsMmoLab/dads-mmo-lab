"""T67: Remove takes the client patch back out of the game client — when it can prove it is ours.

The owner's rule (2026-09-16): a module's client MPQ is deleted from the client's
`Data/` only if it is still byte-for-byte the file this app copied — a checksum
recorded at install — and an addon folder is never deleted at all, only named,
because the game has a checkbox for it. What is left is named in
`ApplyReport.left_behind` with the reason it was left.

Every test here drives the REAL `Applier.install()` and then the REAL
`Applier.remove()` over a SHIPPED manifest — `mod-arac` for the MPQ, the
`tortoise-bots-manager` addon for the folder — into a real directory under
`tmp_path` that stands in for the game client. Only git, SQL and the DBC copier
are replaced, because none of those is what is under test: the assertion is
always about the bytes in the client folder afterwards, never about a sentence
the code told the test about itself.

The ARAC half reuses `test_server_dbc`'s harness — the applier `for_wotlk()`
itself builds, with its docker double — so the route from the Modules tab's
Install press to the file in `Data/` is the app's own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_server_dbc import (
    ARAC,
    ARAC_DBCS,
    SOD,
    _CloneFromManifest,
    _compose_run_double,
    _manifest,
    _the_app_s_applier,
    _volume,
    _write,
)
from yulon.apply import CLAIM_FILE, Applier, ClientCopy, read_client_copies, sha256_of
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest
from yulon.ui.controller_view import _format_report

BOTS_MANAGER = Path("wow-tortoise") / "mods" / "tortoise-bots-manager.json"

MPQ = "MPQ\x1aPatch-A.MPQ".encode("latin-1")
"""What `_CloneFromManifest` puts at `Patch-A.MPQ` in the clone, and therefore the
exact bytes an untouched install leaves in the client's `Data/`."""


def _arac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Install `mod-arac` through the app's own applier; hand back the applier."""
    manifest = _manifest(ARAC)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    (client_dir / "Data").mkdir(parents=True)
    # A patch of the user's own, next to ours, that nothing here may touch.
    (client_dir / "Data" / "Patch-Y.MPQ").write_bytes(b"the user's own patch")
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)
    applier.install(manifest)
    return applier


def _clone_of(applier: Any, manifest: Manifest) -> Path:
    return applier.clone_dir(manifest)


def _bots_manager() -> Manifest:
    return wotlk_modules.load_module(wotlk_modules.BUNDLED_MANIFESTS_DIR / BOTS_MANAGER)


# ------------------------------------------------------------ the MPQ arms


def test_an_unchanged_client_patch_is_taken_back_out_of_the_data_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect T67 was filed for: after Remove, the ARAC patch is gone from the client.

    A left-behind `Patch-A.MPQ` is loaded by every 3.3.5a start, so the character
    screen goes on offering race/class pairs whose DBCs the server no longer has.
    """
    manifest = _manifest(ARAC)
    applier = _arac(monkeypatch, tmp_path)
    data = tmp_path / "client" / "Data"
    assert (data / "Patch-A.MPQ").read_bytes() == MPQ, "the install did not happen"

    report = applier.remove(manifest)

    assert not (data / "Patch-A.MPQ").exists()
    assert f"took back Patch-A.MPQ from {data}" in report.done
    assert not any("Patch-A.MPQ" in line for line in report.left_behind), report.left_behind
    assert (data / "Patch-Y.MPQ").read_bytes() == b"the user's own patch", "took a stranger's file"


def test_a_client_patch_edited_since_install_is_kept_and_named_as_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One byte appended by the user, and the delete is off. The checksum is the only gate.

    The fixture violates exactly one rule: the record IS there, the path IS
    right, the file IS ours by every other test — only the bytes differ. So a
    build whose checksum comparison went away deletes the user's edited file
    here and nothing else in the suite notices.
    """
    manifest = _manifest(ARAC)
    applier = _arac(monkeypatch, tmp_path)
    data = tmp_path / "client" / "Data"
    edited = MPQ + b" and the user's own change"
    (data / "Patch-A.MPQ").write_bytes(edited)

    report = applier.remove(manifest)

    assert (data / "Patch-A.MPQ").read_bytes() == edited, "deleted a file the user had changed"
    assert (
        "Patch-A.MPQ in your game client's Data folder (it has changed since Yu'lon copied it, "
        "so it left it alone)"
    ) in report.left_behind
    # And it was left for THAT reason, not by the neighbouring "no record" arm.
    assert not any("no record" in line for line in report.left_behind), report.left_behind
    assert not any("took back" in line for line in report.done), report.done
    assert "it has changed since Yu'lon copied it" in _format_report(report)


def test_a_patch_installed_before_this_app_recorded_them_is_kept_and_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No receipt, unchanged bytes: still kept. Absence of evidence is not evidence.

    The claim file is rewritten to the shape an older build wrote — everything
    except `client_files` — so this is the real upgrade path, not a synthetic
    one. The bytes in the client are UNTOUCHED, which is what separates this arm
    from the one above: a build that deleted on "the manifest says this item
    copied it" passes every other test here and fails this one.
    """
    manifest = _manifest(ARAC)
    applier = _arac(monkeypatch, tmp_path)
    data = tmp_path / "client" / "Data"
    claim = _clone_of(applier, manifest) / CLAIM_FILE
    payload = json.loads(claim.read_text(encoding="utf-8"))
    assert payload.pop("client_files"), "the install recorded nothing to take away"
    claim.write_text(json.dumps(payload), encoding="utf-8")

    report = applier.remove(manifest)

    assert (data / "Patch-A.MPQ").read_bytes() == MPQ
    assert (
        "Patch-A.MPQ (in your game client's Data folder — Yu'lon has no record of copying it, "
        "so it left it alone)"
    ) in report.left_behind
    assert not any("has changed since" in line for line in report.left_behind), report.left_behind
    assert not any("took back" in line for line in report.done), report.done


def test_the_receipt_the_install_wrote_is_of_the_file_that_landed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The record is a hash of the bytes in the CLIENT, under the destination path.

    Read back through `read_client_copies()`, and compared against the file on
    disk rather than against the constant the test wrote — a receipt of the
    source in the clone would agree with itself and disagree with the client.
    """
    manifest = _manifest(ARAC)
    applier = _arac(monkeypatch, tmp_path)
    landed = tmp_path / "client" / "Data" / "Patch-A.MPQ"

    copies = read_client_copies(_clone_of(applier, manifest), item_id="mod-arac")

    assert copies == (ClientCopy(step="Patch-A.MPQ", path=str(landed), sha256=sha256_of(landed)),)


def test_a_claim_of_another_item_is_not_a_licence_to_delete(tmp_path: Path) -> None:
    """The receipts are read only out of a claim that names the item being removed.

    Defence in depth, and said plainly so nobody reads it as the live guard:
    `remove()` calls `_require_own_clone()` first, and a claim naming another item
    reads `UNKNOWN` there, so this clone is refused before `_unclient()` runs at
    all. This pins the function's own rule, because `read_client_copies()` is
    what authorises a delete inside the USER'S GAME and a later caller reaching
    it by another route must not inherit somebody else's receipts.
    """
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / CLAIM_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "item_id": "some-other-module",
                "clone_id": "whatever",
                "url": "",
                "client_files": [{"step": "Patch-A.MPQ", "path": "/x", "sha256": "0" * 64}],
            }
        ),
        encoding="utf-8",
    )

    assert read_client_copies(clone, item_id="mod-arac") == ()


# ------------------------------------------- a whole folder of client files


class _SodClone(_CloneFromManifest):
    """`_CloneFromManifest` plus the shape a keg's `Client Files/data` really has.

    A patch at the top and one under a locale folder, so the mapping from the
    source tree onto `<client>/Data` has to carry a subdirectory.
    """

    def clone(self, spec: Any) -> None:
        super().clone(spec)
        data = spec.dest / self.manifest.client[0].src
        _write(data / "patch-4.MPQ", b"the keg's patch-4")
        _write(data / "enUS" / "patch-enUS-4.MPQ", b"the keg's locale patch")


def test_removing_a_keg_takes_back_its_own_files_and_leaves_the_clients_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The round 1 data-loss bug: a `dest: data` step whose `src` is a DIRECTORY.

    `_client()` copies such a step's CONTENTS into `<client>/Data`, so the
    destination of the copy is the user's own archive folder. Recording a receipt
    for everything found THERE made every stock `.MPQ` this app's own, and
    removing the shipped Season of Discovery keg emptied `Data/`.

    The fixture is a stock WotLK `Data/` — `common.MPQ`, `lichking.MPQ`,
    `enUS/locale-enUS.MPQ` — with the keg installed over it. Exactly one rule can
    tell the two apart: whether the file is named by the SOURCE tree in the
    clone. Sizes and names are on the same footing for both, and the stock files
    are never modified, so no checksum, path or ownership rule fires instead.
    """
    manifest = _manifest(SOD)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    data = client_dir / "Data"
    stock = {
        data / "common.MPQ": b"the user's common.MPQ",
        data / "lichking.MPQ": b"the user's lichking.MPQ",
        data / "enUS" / "locale-enUS.MPQ": b"the user's locale-enUS.MPQ",
    }
    for path, blob in stock.items():
        _write(path, blob)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)
    applier.git = _SodClone(manifest, ARAC_DBCS)
    applier.install(manifest)
    assert (data / "patch-4.MPQ").is_file() and (data / "enUS" / "patch-enUS-4.MPQ").is_file()

    report = applier.remove(manifest)

    for path, blob in stock.items():
        assert path.read_bytes() == blob, f"the keg's remove took the user's {path.name}"
    assert not any(
        name in line for line in report.done for name in ("common", "lichking", "locale")
    )
    assert not (data / "patch-4.MPQ").exists()
    assert not (data / "enUS" / "patch-enUS-4.MPQ").exists()
    assert f"took back patch-4.MPQ from {data}" in report.done
    assert f"took back patch-enUS-4.MPQ from {data / 'enUS'}" in report.done


# ----------------------------------------------------------- the addon arm


def test_an_addon_folder_is_never_deleted_and_the_report_says_how_to_turn_it_off(
    tmp_path: Path,
) -> None:
    """The owner's decision: `Interface/AddOns/<name>` stays, and the player disables it.

    Over the shipped `tortoise-bots-manager` manifest, whose `client` step copies
    the whole checkout to `dest: addons`.
    """
    manifest = _bots_manager()
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    applier = Applier(server_dir, client_dir=client_dir)
    applier.git = _CloneFromManifest(manifest, ())  # type: ignore[assignment]
    addon = client_dir / "Interface" / "AddOns" / "TortoiseBotsManager"
    applier.install(manifest)
    assert (addon / "patch-Z.MPQ").is_file(), "the install did not happen"

    report = applier.remove(manifest)

    assert (addon / "patch-Z.MPQ").is_file(), "deleted an addon folder"
    assert (
        "the TortoiseBotsManager addon folder in your game client's Interface/AddOns "
        "(Yu'lon does not delete addons — disable it in the game's AddOns menu)"
    ) in report.left_behind
    assert not any("took back" in line for line in report.done), report.done
    assert "disable it in the game's AddOns menu" in _format_report(report)


def test_with_no_game_client_folder_the_remove_names_what_it_could_not_reach(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Installed with a client folder, removed on a record that no longer has one.

    The file is in a client this app can no longer see, so the one honest answer
    is to name it — and not to claim it was taken back.
    """
    manifest = _manifest(ARAC)
    applier = _arac(monkeypatch, tmp_path)
    kept = (tmp_path / "client" / "Data" / "Patch-A.MPQ").read_bytes()
    applier.client_dir = None

    report = applier.remove(manifest)

    assert (tmp_path / "client" / "Data" / "Patch-A.MPQ").read_bytes() == kept
    assert (
        "Patch-A.MPQ (in whatever game client you installed it into — no game client folder "
        "is set here now, so Yu'lon could not reach it)"
    ) in report.left_behind
    assert not any("took back" in line for line in report.done), report.done
