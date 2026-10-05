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
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.support_case import needs_case_sensitive_disk
from tests.test_server_dbc import (
    ARAC,
    ARAC_DBCS,
    SOD,
    WOTLK,
    _CloneFromManifest,
    _compose_run_double,
    _FakeSql,
    _manifest,
    _the_app_s_applier,
    _volume,
    _write,
)
from yulon import play_client
from yulon.apply import CLAIM_FILE, Applier, ClientCopy, read_client_copies, sha256_of
from yulon.catalog import upstream
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest
from yulon.ui.controller_view import (
    ControllerServices,
    _format_report,
    archives_left_out,
    module_kept_files,
)

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
    # T126: this addon follows its releases; the stand-in answers for GitHub.
    applier = Applier(
        server_dir,
        client_dir=client_dir,
        newest_release=lambda slug: upstream.Release("v2026-09-25", "a" * 40),
    )
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


# ------------------------------------- T181a: after the switch to a ready-to-play client


def _play_applier(
    monkeypatch: pytest.MonkeyPatch,
    server_dir: Path,
    original: Path,
    play: Path,
    manifest: Manifest,
    git: Any = None,
) -> Any:
    """The Modules tab's applier for a server WITH a ready-to-play client, git and SQL faked.

    Built by `for_entry(..., play_client_dir=)`, the route the tab takes, so the
    rebase under test is the one the factory wires rather than one set by hand.
    """
    services = ControllerServices.for_entry(
        WOTLK, server_dir, client_dir=original, play_client_dir=play
    )
    applier = services.applier
    assert applier is not None
    applier.sql = _FakeSql()
    applier.git = git if git is not None else _CloneFromManifest(manifest, ARAC_DBCS)
    return applier


def _make_play_client(original: Path, server_dir: Path) -> Path:
    play = original.with_name(original.name + " (Yu'lon)")
    play_client.create(
        original,
        play,
        game="wow-wotlk",
        server_dir=server_dir,
        allow_full_copy=False,
        reflink=lambda src, dst: False,
    )
    return play


def test_a_patch_installed_into_the_original_is_removed_from_the_ready_to_play_client_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review Focus 5. Receipts are ABSOLUTE paths into the folder they were written to.

    Installed before the switch, the receipt names the ORIGINAL's `Data/Patch-A.MPQ`;
    the ready-to-play client got the same file as a hard link. Removed after the
    switch, the ready-to-play client's link goes and the original's file stays:
    the receipt is moved onto the ready-to-play client, never acted on where it points.
    """
    manifest = _manifest(ARAC)
    _arac(monkeypatch, tmp_path)
    server_dir, original = tmp_path / "server", tmp_path / "client"
    receipts = read_client_copies(_the_clone(server_dir, manifest), item_id=manifest.id)
    assert [Path(c.path) for c in receipts] == [
        original / "Data" / "Patch-A.MPQ"
    ], "the fixture no longer records an absolute path into the original"
    play = _make_play_client(original, server_dir)
    assert os.path.samefile(original / "Data/Patch-A.MPQ", play / "Data/Patch-A.MPQ")
    applier = _play_applier(monkeypatch, server_dir, original, play, manifest)

    report = applier.remove(manifest)

    assert not (play / "Data" / "Patch-A.MPQ").exists(), report.left_behind
    assert (original / "Data" / "Patch-A.MPQ").read_bytes() == MPQ, "reached into the original"
    assert f"took back Patch-A.MPQ from {play / 'Data'}" in report.done
    assert (original / "Data" / "Patch-Y.MPQ").read_bytes() == b"the user's own patch"


def test_a_patch_removed_after_the_switch_is_not_named_as_left_out_of_the_ready_to_play_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Controller ruling, T181a: the original's copy of a removed module's patch is not "left out".

    Installed into the player's own client before the switch, the ready-to-play
    client made with "Also remove them from your original client" unticked (no
    `take_back_files()`), then removed after the switch: the patch goes from the
    ready-to-play client only and stays in the original. Play and Refresh must
    not then name it at every press, nor advise making the client again, which
    would bring the removed module's patch back. An archive of the original's
    that no module of this server put there is still named.
    """
    manifest = _manifest(ARAC)
    _arac(monkeypatch, tmp_path)
    server_dir, original = tmp_path / "server", tmp_path / "client"
    play = _make_play_client(original, server_dir)
    (original / "Data" / "patch-Z.MPQ").write_bytes(b"MPQ another server's patch")
    applier = _play_applier(monkeypatch, server_dir, original, play, manifest)

    applier.remove(manifest)

    assert (original / "Data" / "Patch-A.MPQ").read_bytes() == MPQ, "the scenario changed"
    assert not (play / "Data" / "Patch-A.MPQ").exists(), "the scenario changed"
    assert archives_left_out(server_dir, play, original, original) == (
        Path("Data") / "patch-Z.MPQ",
    )
    assert not (original / play_client.TAKEN_BACK).exists(), "the original gained a record"
    assert (play / play_client.TAKEN_BACK).exists()

    play_client.delete(play, game="wow-wotlk", server_dir=server_dir)

    assert not play.exists(), "Delete left the record (or anything) behind"
    assert not (original / play_client.TAKEN_BACK).exists()


def test_a_remove_keeps_no_record_in_a_folder_without_this_servers_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A module's Remove is not marker-gated, so the record it keeps is (T181a fix round).

    The marker goes after the applier is built: the Remove still takes the patch
    back by its receipt, and writes no file of Yu'lon's into an unmarked folder.
    """
    manifest = _manifest(ARAC)
    _arac(monkeypatch, tmp_path)
    server_dir, original = tmp_path / "server", tmp_path / "client"
    play = _make_play_client(original, server_dir)
    applier = _play_applier(monkeypatch, server_dir, original, play, manifest)
    (play / play_client.MARKER).unlink()

    applier.remove(manifest)

    assert not (play / "Data" / "Patch-A.MPQ").exists(), "the take-back itself stopped"
    assert not (play / play_client.TAKEN_BACK).exists()
    assert not (original / play_client.TAKEN_BACK).exists()


class _NewerClone(_CloneFromManifest):
    """The same module a release later: its client patch has other bytes."""

    def clone(self, spec: Any) -> None:
        super().clone(spec)
        for client in self.manifest.client:
            _write(spec.dest / client.src, NEWER_MPQ)


NEWER_MPQ = b"MPQ\x1a a newer Patch-A.MPQ"


def test_reinstalling_after_the_switch_never_writes_through_a_shared_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A copy onto the ready-to-play client's hard link would write the original's file.

    `shutil.copy2` opens an existing destination and truncates it, and a hard link
    is the same file in both folders. The new patch must land as the ready-to-play
    client's own file.
    """
    manifest = _manifest(ARAC)
    _arac(monkeypatch, tmp_path)
    server_dir, original = tmp_path / "server", tmp_path / "client"
    play = _make_play_client(original, server_dir)
    assert os.path.samefile(original / "Data/Patch-A.MPQ", play / "Data/Patch-A.MPQ")
    # The clone goes, so the install is a fresh clone and asks nothing of a real git
    # about the old one; what is under test is only the copy into the client.
    shutil.rmtree(_the_clone(server_dir, manifest))
    applier = _play_applier(
        monkeypatch, server_dir, original, play, manifest, _NewerClone(manifest, ARAC_DBCS)
    )

    applier.install(manifest)

    assert (play / "Data" / "Patch-A.MPQ").read_bytes() == NEWER_MPQ
    assert (original / "Data" / "Patch-A.MPQ").read_bytes() == MPQ, "wrote through the link"
    assert not os.path.samefile(original / "Data/Patch-A.MPQ", play / "Data/Patch-A.MPQ")


@pytest.mark.parametrize("installed", ["before the switch", "after the switch"])
def test_a_receipted_patch_in_the_ready_to_play_client_is_kept_from_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, installed: str
) -> None:
    """`module_kept_files()` is what Play and Refresh pass to `play_client` as `keep`."""
    manifest = _manifest(ARAC)
    server_dir, original = tmp_path / "server", tmp_path / "client"
    if installed == "before the switch":
        _arac(monkeypatch, tmp_path)
        play = _make_play_client(original, server_dir)
    else:
        server_dir.mkdir()
        (original / "Data").mkdir(parents=True)
        (original / "Data" / "common.MPQ").write_bytes(b"MPQ")  # a client has archives
        _compose_run_double(monkeypatch, _volume(tmp_path))
        play = _make_play_client(original, server_dir)
        _the_app_s_applier(monkeypatch, server_dir, original, manifest)  # the fakes
        _play_applier(monkeypatch, server_dir, original, play, manifest).install(manifest)
        assert not (original / "Data" / "Patch-A.MPQ").exists(), "installed into the original"

    assert module_kept_files(server_dir, play) == (Path("Data") / "Patch-A.MPQ",)


def test_no_receipt_means_nothing_is_kept(tmp_path: Path) -> None:
    """A server with no module clones keeps nothing: Refresh works as it did."""
    server_dir, original = tmp_path / "server", tmp_path / "client"
    server_dir.mkdir()
    (original / "Data").mkdir(parents=True)
    (original / "Data" / "common.MPQ").write_bytes(b"MPQ")  # a client has archives
    play = _make_play_client(original, server_dir)

    assert module_kept_files(server_dir, play) == ()


def _the_clone(server_dir: Path, manifest: Manifest) -> Path:
    return server_dir / "modules" / manifest.id


# ------------------------------- T262: a client file already there in another case


def _arac_over(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, files: dict[str, bytes]) -> Any:
    """Install `mod-arac` into a client holding `files` (relative paths); the applier."""
    manifest = _manifest(ARAC)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    for rel, blob in files.items():
        _write(client_dir / rel, blob)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)
    applier.install(manifest)
    return applier


def _names(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


@needs_case_sensitive_disk
def test_a_patch_lands_on_the_name_already_in_the_client_in_another_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T262: `Data/patch-a.mpq` beside a new `Data/Patch-A.MPQ` is two archives of one name
    to the game (Wine is case-blind), and the receipt named the new one only."""
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/patch-a.mpq": b"an older ARAC patch"})
    client = tmp_path / "client"

    assert _names(client) == ["Data", "Data/patch-a.mpq"]
    assert (client / "Data" / "patch-a.mpq").read_bytes() == MPQ
    landed = client / "Data" / "patch-a.mpq"
    copies = read_client_copies(_clone_of(applier, manifest), item_id="mod-arac")
    assert copies == (ClientCopy(step="Patch-A.MPQ", path=str(landed), sha256=sha256_of(landed)),)

    report = applier.remove(manifest)

    assert _names(client) == ["Data"], "the patch was not taken back from the name it landed on"
    assert f"took back patch-a.mpq from {client / 'Data'}" in report.done


@needs_case_sensitive_disk
def test_a_patch_lands_in_a_lowercase_data_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T261 with T262: a client whose `Data/` is `data/` gets no second, empty `Data/`."""
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"data/common.MPQ": b"the user's common"})
    client = tmp_path / "client"

    assert _names(client) == ["data", "data/Patch-A.MPQ", "data/common.MPQ"]

    applier.remove(manifest)

    assert _names(client) == ["data", "data/common.MPQ"]


@needs_case_sensitive_disk
def test_a_folder_of_client_files_lands_on_the_folders_and_files_already_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T262: a keg's `Client Files/data` tree, copied onto `data/enus/` and `PATCH-4.MPQ`.

    `copytree` made the source's `enUS/` beside the client's `enus/`, and its
    `patch-4.MPQ` beside `PATCH-4.MPQ`; each now lands on the name on disk, and
    Remove takes back exactly what landed. The client's own archives stay.
    """
    manifest = _manifest(SOD)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client = tmp_path / "client"
    _write(client / "data" / "PATCH-4.MPQ", b"an older keg patch")
    _write(client / "data" / "enus" / "locale-enus.mpq", b"the user's locale")
    os.chmod(client / "data" / "enus" / "locale-enus.mpq", 0o444)  # T196/T198: kept
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client, manifest)
    applier.git = _SodClone(manifest, ARAC_DBCS)

    applier.install(manifest)

    assert _names(client) == [
        "Interface",
        "Interface/patch-Z.MPQ",  # the keg's second step, `dest: interface`
        "data",
        "data/PATCH-4.MPQ",
        "data/enus",
        "data/enus/locale-enus.mpq",
        "data/enus/patch-enUS-4.MPQ",
        "data/patch-Z.MPQ",
    ]
    assert (client / "data" / "PATCH-4.MPQ").read_bytes() == b"the keg's patch-4"
    assert (client / "data" / "enus" / "locale-enus.mpq").stat().st_mode & 0o777 == 0o444

    report = applier.remove(manifest)

    assert _names(client) == [
        "Interface",
        "Interface/patch-Z.MPQ",
        "data",
        "data/enus",
        "data/enus/locale-enus.mpq",
    ]
    assert f"took back PATCH-4.MPQ from {client / 'data'}" in report.done


@needs_case_sensitive_disk
def test_an_addon_lands_in_the_interface_folder_already_there_in_another_case(
    tmp_path: Path,
) -> None:
    """T262: `interface/addons/` is the client's AddOns folder too; no second tree beside it."""
    manifest = _bots_manager()
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client = tmp_path / "client"
    (client / "interface" / "addons").mkdir(parents=True)
    applier = Applier(
        server_dir,
        client_dir=client,
        newest_release=lambda slug: upstream.Release("v2026-09-25", "a" * 40),
    )
    applier.git = _CloneFromManifest(manifest, ())  # type: ignore[assignment]

    applier.install(manifest)

    assert sorted(p.name for p in client.iterdir()) == ["interface"]
    assert (client / "interface" / "addons" / "TortoiseBotsManager" / "patch-Z.MPQ").is_file()
