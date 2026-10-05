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
    client_dir.mkdir(exist_ok=True)
    for rel, blob in files.items():
        _write(client_dir / rel, blob)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)
    applier.install(manifest)
    return applier


def _names(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


ASIDE = ".yulon-module-old"
"""The suffix the player's own file is set aside under while a module's file has its name."""


@pytest.mark.parametrize(
    "theirs",
    [
        pytest.param("Data/Patch-A.MPQ", id="same case"),
        pytest.param("Data/patch-a.mpq", id="another case", marks=needs_case_sensitive_disk),
    ],
)
def test_a_players_own_file_under_the_patchs_name_is_set_aside_and_put_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, theirs: str
) -> None:
    """Owner's decision on the cold review of T262: "set aside, put back".

    The player's `Data/patch-a.mpq` (from another mod) used to be overwritten, and
    Remove then deleted it, its hash being the one Yu'lon wrote: data loss. Now it
    is moved aside under a Yu'lon-named sibling, the receipt records that, and
    Remove puts it back byte for byte with its read-only flag.
    """
    manifest = _manifest(ARAC)
    client = tmp_path / "client"
    _write(client / theirs, b"another mod's patch-a")
    os.chmod(client / theirs, 0o444)  # note 3: a read-only file no longer stops Install
    before = (client / theirs).stat()
    applier = _arac_over(monkeypatch, tmp_path, {})
    landed = client / theirs
    aside = landed.with_name(landed.name + ASIDE)

    assert _names(client) == sorted(["Data", theirs, theirs + ASIDE])
    assert landed.read_bytes() == MPQ, "the patch landed on the name already there"
    assert aside.read_bytes() == b"another mod's patch-a"
    copies = read_client_copies(_clone_of(applier, manifest), item_id="mod-arac")
    assert copies == (
        ClientCopy(
            step="Patch-A.MPQ", path=str(landed), sha256=sha256_of(landed), aside=str(aside)
        ),
    )

    report = applier.remove(manifest)

    assert _names(client) == ["Data", theirs]
    assert landed.read_bytes() == b"another mod's patch-a"
    after = landed.stat()
    assert (after.st_mode, after.st_mtime_ns) == (before.st_mode, before.st_mtime_ns)
    assert f"took back {landed.name} from {client / 'Data'}" in report.done
    assert f"put your own {landed.name} back in {client / 'Data'}" in report.done


def test_a_players_file_with_the_same_bytes_is_not_set_aside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a file whose bytes differ is the player's to keep; the same bytes are the patch."""
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": MPQ})

    assert _names(tmp_path / "client") == ["Data", "Data/Patch-A.MPQ"]
    applier.remove(_manifest(ARAC))
    assert _names(tmp_path / "client") == ["Data"]


def _update(applier: Any, manifest: Manifest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """`Applier.update()` with only its git questions answered (a clean checkout, same origin).

    The rest is the real route: `update()` runs `install()` again over the clone, and
    `install()` hands the claim's receipts to `_client()` (`log.previous_copies`).
    """
    monkeypatch.setattr(applier, "_update_refusal", lambda _manifest: None)
    monkeypatch.setattr(applier, "_reset_cost", lambda _manifest, _clone: None)
    return applier.update(manifest)


def test_a_reinstall_keeps_the_players_file_aside_rather_than_its_own_first_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The second install finds ITS OWN file at the name: that is not the player's to keep,
    and setting it aside would put Yu'lon's patch back where the player's belongs.

    Through the real `install()` and then `update()`, so the hand-off of the claim's
    receipts to `_client()` (`log.previous_copies`) is what is tested.
    """
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    data = tmp_path / "client" / "Data"

    _update(applier, manifest, monkeypatch)

    assert _names(data) == ["Patch-A.MPQ", "Patch-A.MPQ" + ASIDE]
    assert (data / ("Patch-A.MPQ" + ASIDE)).read_bytes() == b"the player's own"
    (copy,) = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert copy.aside == str(data / ("Patch-A.MPQ" + ASIDE)), "the first aside is carried"
    report = applier.remove(manifest)
    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert f"put your own Patch-A.MPQ back in {data}" in report.done


def test_a_put_back_that_fails_keeps_the_aside_file_and_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yulon import apply as apply_module

    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    data = tmp_path / "client" / "Data"
    aside = data / ("Patch-A.MPQ" + ASIDE)
    real_rename = os.rename

    def refuse(src: object, dst: object) -> None:
        if Path(str(src)) == aside:
            raise PermissionError("the file is held open")
        real_rename(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(apply_module.os, "rename", refuse)
    report = applier.remove(manifest)

    assert aside.read_bytes() == b"the player's own", "the player's file was not kept"
    assert any(str(aside) in line and "held open" in line for line in report.left_behind)
    assert not any("put your own" in line for line in report.done)


def test_a_changed_patch_keeps_the_players_file_aside_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Our file is left (it changed since), so the name is not free: the aside stays, named."""
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    data = tmp_path / "client" / "Data"
    (data / "Patch-A.MPQ").write_bytes(MPQ + b" edited")

    report = applier.remove(manifest)

    assert (data / ("Patch-A.MPQ" + ASIDE)).read_bytes() == b"the player's own"
    assert any(str(data / ("Patch-A.MPQ" + ASIDE)) in line for line in report.left_behind)


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
        "data/PATCH-4.MPQ" + ASIDE,
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
        "data/PATCH-4.MPQ",
        "data/enus",
        "data/enus/locale-enus.mpq",
    ]
    assert (client / "data" / "PATCH-4.MPQ").read_bytes() == b"an older keg patch"
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


@needs_case_sensitive_disk
def test_two_source_names_that_differ_only_in_case_land_on_one_name(tmp_path: Path) -> None:
    """T262: the copy never makes the twin it exists to avoid, even from its own source."""
    from yulon.apply import _copy_onto

    src = tmp_path / "src"
    _write(src / "Patch.MPQ", b"first")
    _write(src / "patch.mpq", b"second")
    target = tmp_path / "client" / "Data"

    written = _copy_onto(src, target)

    assert sorted(p.name for p in target.iterdir()) == ["Patch.MPQ"]
    assert written == [target / "Patch.MPQ"], "one receipt for the one file"


def test_a_folder_copy_never_writes_through_a_file_shared_by_hard_link(tmp_path: Path) -> None:
    """T181's rule in the folder copy too: a ready-to-play client's archive shares the
    player's inode, so it is replaced, never opened for writing."""
    from yulon.apply import _copy_onto

    players = tmp_path / "players" / "patch-4.MPQ"
    _write(players, b"the player's own patch-4")
    target = tmp_path / "play" / "Data"
    target.mkdir(parents=True)
    os.link(players, target / "patch-4.MPQ")
    src = tmp_path / "src"
    _write(src / "patch-4.MPQ", b"the keg's patch-4")

    _copy_onto(src, target)

    assert (target / "patch-4.MPQ").read_bytes() == b"the keg's patch-4"
    assert players.read_bytes() == b"the player's own patch-4"


def test_a_hard_linked_archive_is_replaced_as_before_and_not_set_aside(tmp_path: Path) -> None:
    """The owner's decision keeps T181's rule: a shared archive (a ready-to-play client's
    and the player's) is replaced by a file of its own; the player's inode is untouched."""
    from yulon import apply as apply_module

    manifest = _manifest(ARAC)
    clone = tmp_path / "clone"
    _write(clone / "Patch-A.MPQ", MPQ)
    players = tmp_path / "original" / "Data" / "Patch-A.MPQ"
    _write(players, b"the player's own")
    data = tmp_path / "play" / "Data"
    data.mkdir(parents=True)
    os.link(players, data / "Patch-A.MPQ")
    log = apply_module._Log()

    Applier(tmp_path / "server", client_dir=tmp_path / "play")._client(manifest, clone, log)

    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == MPQ
    assert players.read_bytes() == b"the player's own"
    assert [copy.aside for copy in log.client_copies] == [""]


def test_a_file_set_aside_in_the_original_is_put_back_in_the_ready_to_play_client_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The aside is rebased like the receipt's path: installed before the switch, it is in
    the original and the ready-to-play client holds a copy of it. Remove after the switch
    puts that copy back there; the original's own files are never moved out of it."""
    manifest = _manifest(ARAC)
    _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    server_dir, original = tmp_path / "server", tmp_path / "client"
    play = _make_play_client(original, server_dir)
    applier = _play_applier(monkeypatch, server_dir, original, play, manifest)

    applier.remove(manifest)

    assert (play / "Data" / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert not (play / "Data" / ("Patch-A.MPQ" + ASIDE)).exists()
    assert (original / "Data" / ("Patch-A.MPQ" + ASIDE)).read_bytes() == b"the player's own"
    assert (original / "Data" / "Patch-A.MPQ").read_bytes() == MPQ, "reached into the original"


@needs_case_sensitive_disk
def test_two_source_names_of_one_name_set_the_players_file_aside_once(tmp_path: Path) -> None:
    """The step's own first copy is not the player's: the twin overwrites it, no second aside."""
    from yulon import apply as apply_module

    manifest = _manifest(SOD)
    clone = tmp_path / "clone"
    for step in manifest.client:
        (clone / step.src).mkdir(parents=True)
    folder = clone / manifest.client[0].src
    _write(folder / "Patch-4.MPQ", b"first")
    _write(folder / "patch-4.mpq", b"second")
    data = tmp_path / "client" / "Data"
    _write(data / "Patch-4.MPQ", b"the player's own")
    log = apply_module._Log(persist=lambda _files: None)

    Applier(tmp_path / "server", client_dir=tmp_path / "client")._client(manifest, clone, log)

    assert _names(data) == ["Patch-4.MPQ", "Patch-4.MPQ" + ASIDE]
    assert (data / ("Patch-4.MPQ" + ASIDE)).read_bytes() == b"the player's own"


# ------------- T262 scoped re-review: the aside is durable, and every path puts it back


def _player_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """The player's own `Data/Patch-A.MPQ` (read-only) and the server folder, made first."""
    players = tmp_path / "client" / "Data" / "Patch-A.MPQ"
    _write(players, b"the player's own")
    os.chmod(players, 0o444)
    (tmp_path / "server").mkdir()
    return players, tmp_path / "server"


def _install_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: str
) -> tuple[Any, pytest.ExceptionInfo[BaseException]]:
    """Install mod-arac over the player's file, failing at `fail` after the file was moved."""
    from yulon import apply as apply_module

    manifest = _manifest(ARAC)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(
        monkeypatch, tmp_path / "server", tmp_path / "client", manifest
    )
    if fail == "the copy":

        def broken(src: object, dst: object) -> object:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(apply_module, "_copy_unshared", broken)
    elif fail == "the DBC step":

        def dbc(*_a: object, **_k: object) -> None:
            raise apply_module.ApplyError("the DBC copy failed")

        monkeypatch.setattr(applier, "_dbc", dbc)
    with pytest.raises(BaseException) as failed:
        applier.install(manifest)
    return applier, failed


@pytest.mark.parametrize("fail", ["the copy", "the DBC step"])
def test_an_install_that_fails_after_the_move_puts_the_players_file_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: str
) -> None:
    """Scoped re-review, must-fix 1: a failure after the rename leaves nothing hidden."""
    players, _server = _player_and_clone(tmp_path)
    before = players.stat()

    applier, _failed = _install_failing(monkeypatch, tmp_path, fail)

    assert _names(players.parent) == ["Patch-A.MPQ"]
    assert players.read_bytes() == b"the player's own"
    assert players.stat().st_mode == before.st_mode, "still read-only"
    copies = read_client_copies(_clone_of(applier, _manifest(ARAC)), item_id="mod-arac")
    assert not [copy for copy in copies if copy.aside], "no record of an aside that is gone"


def test_a_later_set_aside_blocked_puts_the_first_one_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A keg's second file of the player's cannot be moved (WoW holds it on Windows): the
    install fails, and the first file it had already moved goes back."""
    from yulon import apply as apply_module

    manifest = _manifest(SOD)
    client = tmp_path / "client"
    _write(client / "Data" / "patch-4.MPQ", b"the player's patch-4")
    _write(client / "Data" / "patch-Z.MPQ", b"the player's patch-Z")
    (tmp_path / "server").mkdir()
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, tmp_path / "server", client, manifest)
    applier.git = _SodClone(manifest, ARAC_DBCS)
    real_rename = os.rename

    def held(src: object, dst: object) -> None:
        if Path(str(src)).name == "patch-Z.MPQ":
            raise PermissionError(13, "The process cannot access the file")
        real_rename(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(apply_module.os, "rename", held)
    with pytest.raises(PermissionError):
        applier.install(manifest)

    assert sorted(p.name for p in (client / "Data").iterdir() if p.is_file()) == [
        "patch-4.MPQ",
        "patch-Z.MPQ",
    ]
    assert (client / "Data" / "patch-4.MPQ").read_bytes() == b"the player's patch-4"
    assert (client / "Data" / "patch-Z.MPQ").read_bytes() == b"the player's patch-Z"


def test_a_put_back_after_a_failure_that_fails_is_recorded_named_and_restored_later(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yulon import apply as apply_module

    players, _server = _player_and_clone(tmp_path)
    aside = players.with_name("Patch-A.MPQ" + ASIDE)
    real_rename = os.rename

    def no_way_back(src: object, dst: object) -> None:
        if Path(str(src)) == aside:
            raise PermissionError(13, "held open")
        real_rename(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(apply_module.os, "rename", no_way_back)
    applier, failed = _install_failing(monkeypatch, tmp_path, "the DBC step")

    assert aside.read_bytes() == b"the player's own"
    assert str(aside) in str(failed.value), "the failure names where the player's file is"
    (copy,) = read_client_copies(_clone_of(applier, _manifest(ARAC)), item_id="mod-arac")
    assert copy.aside == str(aside), "and the claim records it"
    monkeypatch.setattr(apply_module.os, "rename", real_rename)

    applier.remove(_manifest(ARAC))

    assert _names(players.parent) == ["Patch-A.MPQ"]
    assert players.read_bytes() == b"the player's own"


@pytest.mark.parametrize("dies", ["after the move", "before the move"])
def test_a_crash_during_the_install_leaves_a_record_remove_acts_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dies: str
) -> None:
    """The process dies (no rollback runs): the claim was written BEFORE the rename, so
    the next Remove finds the player's file and puts it back, or finds it never moved."""
    from yulon import apply as apply_module

    players, server = _player_and_clone(tmp_path)
    manifest = _manifest(ARAC)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server, tmp_path / "client", manifest)
    real_rename = os.rename

    class Died(BaseException):
        pass

    def rename(src: object, dst: object) -> None:
        if dies == "before the move" and str(dst).endswith(ASIDE):
            raise Died()
        real_rename(src, dst)  # type: ignore[arg-type]
        if dies == "after the move" and str(dst).endswith(ASIDE):
            raise Died()

    monkeypatch.setattr(apply_module.os, "rename", rename)
    monkeypatch.setattr(Applier, "_put_asides_back", lambda self, log: [])  # no process left
    monkeypatch.setattr(Applier, "_unplan", lambda self, log, key, prior: None)
    with pytest.raises(Died):
        applier.install(manifest)
    monkeypatch.setattr(apply_module.os, "rename", real_rename)
    (copy,) = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert copy.aside == str(players.with_name("Patch-A.MPQ" + ASIDE)), "written BEFORE the move"

    report = applier.remove(manifest)

    assert _names(players.parent) == ["Patch-A.MPQ"]
    assert players.read_bytes() == b"the player's own"
    if dies == "after the move":
        assert f"put your own Patch-A.MPQ back in {players.parent}" in report.done


def test_without_a_claim_the_players_file_is_not_moved(tmp_path: Path) -> None:
    """No record to write the aside into: refused, the player's file untouched."""
    from yulon import apply as apply_module

    clone = tmp_path / "clone"
    _write(clone / "Patch-A.MPQ", MPQ)
    data = tmp_path / "client" / "Data"
    _write(data / "Patch-A.MPQ", b"the player's own")

    with pytest.raises(apply_module.ApplyError, match="was not moved"):
        Applier(tmp_path / "server", client_dir=tmp_path / "client")._client(
            _manifest(ARAC), clone, apply_module._Log()
        )

    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"


def test_an_aside_no_receipt_records_is_put_back_by_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Belt: `<name>.yulon-module-old` beside a module's file with no record is put back."""
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {})
    data = tmp_path / "client" / "Data"
    _write(data / ("Patch-A.MPQ" + ASIDE), b"the player's own")

    report = applier.remove(manifest)

    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert f"put your own Patch-A.MPQ back in {data}" in report.done


def test_an_aside_no_receipt_records_is_adopted_by_the_next_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Belt: the install that finds one names it and records it, so Remove puts it back."""
    manifest = _manifest(ARAC)
    data = tmp_path / "client" / "Data"
    _write(data / ("Patch-A.MPQ" + ASIDE), b"the player's own")

    applier = _arac_over(monkeypatch, tmp_path, {})

    (copy,) = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert copy.aside == str(data / ("Patch-A.MPQ" + ASIDE))
    applier.remove(manifest)
    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"


def test_a_second_module_with_a_file_of_the_same_name_is_refused_naming_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lead decision: no aside chains between two modules. The first module's file stays."""
    from yulon.apply import ApplyRefusal

    arac = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {})
    other = arac.model_copy(update={"id": "mod-arac-copy"})
    applier.git = _CloneFromManifest(other, ARAC_DBCS)
    data = tmp_path / "client" / "Data"
    (data / "Patch-A.MPQ").chmod(0o644)
    (data / "Patch-A.MPQ").write_bytes(MPQ)

    with pytest.raises(ApplyRefusal) as refused:
        applier.install(other)

    assert "mod-arac-copy" in str(refused.value) and "mod-arac put there" in str(refused.value)
    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == MPQ


class _SodWithoutTheLocalePatch(_SodClone):
    """The keg's next version: its `enUS/patch-enUS-4.MPQ` is gone from the source."""

    def clone(self, spec: Any) -> None:
        super().clone(spec)
        (spec.dest / self.manifest.client[0].src / "enUS" / "patch-enUS-4.MPQ").unlink()


def test_an_update_that_no_longer_ships_a_file_takes_it_back_and_puts_the_players_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _manifest(SOD)
    client = tmp_path / "client"
    locale = client / "Data" / "enUS" / "patch-enUS-4.MPQ"
    _write(locale, b"the player's own locale patch")
    (tmp_path / "server").mkdir()
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, tmp_path / "server", client, manifest)
    applier.git = _SodClone(manifest, ARAC_DBCS)
    applier.install(manifest)
    assert locale.read_bytes() == b"the keg's locale patch"
    applier.git = _SodWithoutTheLocalePatch(manifest, ARAC_DBCS)

    _update(applier, manifest, monkeypatch)

    assert locale.read_bytes() == b"the player's own locale patch"
    assert _names(locale.parent) == ["patch-enUS-4.MPQ"]
    copies = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert str(locale) not in {copy.path for copy in copies}


def test_a_reinstall_over_a_file_the_player_changed_records_it_and_remove_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    data = tmp_path / "client" / "Data"
    (data / "Patch-A.MPQ").write_bytes(b"the player's change to the patch")

    _update(applier, manifest, monkeypatch)

    changed = data / ("Patch-A.MPQ" + ASIDE + ".1")
    assert changed.read_bytes() == b"the player's change to the patch"
    (copy,) = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert copy.kept == (str(changed),)
    report = applier.remove(manifest)
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert changed.read_bytes() == b"the player's change to the patch"
    assert any(str(changed) in line for line in report.left_behind)


def test_an_aside_the_claim_cannot_read_keeps_the_receipt_and_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    clone = _clone_of(applier, manifest)
    claim = json.loads((clone / CLAIM_FILE).read_text("utf-8"))
    claim["client_files"][0]["aside"] = 5
    (clone / CLAIM_FILE).write_text(json.dumps(claim), "utf-8")

    (copy,) = read_client_copies(clone, item_id=manifest.id)
    assert copy.aside == "" and copy.aside_unknown is True
    report = applier.remove(manifest)

    data = tmp_path / "client" / "Data"
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own", "found beside it"
    assert any("could not be read" in line for line in report.left_behind)


def test_uninstall_takes_every_modules_files_back_and_puts_the_players_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Uninstall seam `ControllerServices.for_entry()` wires: the applier's take-back."""
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    services = ControllerServices.for_entry(WOTLK, tmp_path / "server", tmp_path / "client")
    assert services.uninstall is not None
    took, left = services.uninstall.take_back_client_files()  # type: ignore[attr-defined]

    data = tmp_path / "client" / "Data"
    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert f"put your own Patch-A.MPQ back in {data}" in took
    assert left == []
    assert applier is not None


def test_an_update_that_ships_no_client_file_any_more_leaves_no_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Its receipts are replaced by this run's, even by none: nothing of it is left to take."""
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    without = manifest.model_copy(update={"client": []})

    _update(applier, without, monkeypatch)

    data = tmp_path / "client" / "Data"
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert read_client_copies(_clone_of(applier, manifest), item_id=manifest.id) == ()


def test_uninstall_puts_back_an_aside_no_receipt_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    applier = _arac_over(monkeypatch, tmp_path, {})
    data = tmp_path / "client" / "Data"
    _write(data / ("Patch-A.MPQ" + ASIDE), b"the player's own")

    took, left = applier.take_back_everything()

    assert _names(data) == ["Patch-A.MPQ"]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert left == []


# ------------- T262 second scoped re-review: kept moves and a failed record write


def test_a_failed_update_puts_back_the_players_changed_file_it_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hole 1: the move into `kept` (a reinstall over a file the player changed) is this
    run's too, so a failure after it puts the player's changed file back at its name."""
    from yulon import apply as apply_module

    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    data = tmp_path / "client" / "Data"
    (data / "Patch-A.MPQ").write_bytes(b"the player's change")

    def dbc(*_a: object, **_k: object) -> None:
        raise apply_module.ApplyError("the DBC copy failed")

    monkeypatch.setattr(applier, "_dbc", dbc)
    with pytest.raises(apply_module.ApplyError):
        _update(applier, manifest, monkeypatch)

    assert _names(data) == ["Patch-A.MPQ", "Patch-A.MPQ" + ASIDE]
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's change"
    assert (data / ("Patch-A.MPQ" + ASIDE)).read_bytes() == b"the player's own"
    (copy,) = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert copy.kept == () and copy.aside == str(data / ("Patch-A.MPQ" + ASIDE))


def test_a_record_write_that_fails_moves_nothing_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hole 2: the first aside write raises (disk full). Nothing was moved, so the claim
    must not hold an aside that does not exist; Remove then leaves the player's file."""
    from yulon import apply as apply_module

    players, server = _player_and_clone(tmp_path)
    manifest = _manifest(ARAC)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server, tmp_path / "client", manifest)
    real = apply_module.write_clone_claim
    failed_once: list[bool] = []

    def full(clone: Path, **kw: Any) -> None:
        if not failed_once and any(c.aside for c in kw.get("client_files", ())):
            failed_once.append(True)
            raise OSError(28, "No space left on device")
        real(clone, **kw)

    monkeypatch.setattr(apply_module, "write_clone_claim", full)
    with pytest.raises(OSError):
        applier.install(manifest)

    assert _names(players.parent) == ["Patch-A.MPQ"]
    assert players.read_bytes() == b"the player's own"
    copies = read_client_copies(_clone_of(applier, manifest), item_id=manifest.id)
    assert not [copy for copy in copies if copy.aside], copies
    report = applier.remove(manifest)
    assert players.read_bytes() == b"the player's own"
    assert not any("has changed since" in line for line in report.left_behind)


def test_a_disk_full_copy_whose_put_back_fails_names_the_players_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Should 5: the failure is an OSError, not Yu'lon's own; it still names the file."""
    from yulon import apply as apply_module

    players, _server = _player_and_clone(tmp_path)
    aside = players.with_name("Patch-A.MPQ" + ASIDE)
    real_rename = os.rename

    def no_way_back(src: object, dst: object) -> None:
        if Path(str(src)) == aside:
            raise PermissionError(13, "held open")
        real_rename(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(apply_module.os, "rename", no_way_back)
    _applier, failed = _install_failing(monkeypatch, tmp_path, "the copy")

    assert isinstance(failed.value, OSError) and failed.value.errno == 28
    assert str(aside) in str(failed.value)
    assert aside.read_bytes() == b"the player's own"


def test_the_live_sequence_puts_the_players_original_back_after_a_second_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """m910q live check D2: install, the player edits, update (their edit kept as `.1`),
    Remove, Install, Remove. The kept `.1` was adopted as the main aside by the second
    Install, so the last Remove put the EDIT on the live name and called the player's
    real file "your changed" copy. The file there when an install began must come back."""
    manifest = _manifest(ARAC)
    applier = _arac_over(monkeypatch, tmp_path, {"Data/Patch-A.MPQ": b"the player's own"})
    data = tmp_path / "client" / "Data"
    (data / "Patch-A.MPQ").write_bytes(b"the player's edit")
    _update(applier, manifest, monkeypatch)
    applier.remove(manifest)
    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    kept = data / ("Patch-A.MPQ" + ASIDE + ".1")
    assert kept.read_bytes() == b"the player's edit"

    applier.install(manifest)
    report = applier.remove(manifest)

    assert (data / "Patch-A.MPQ").read_bytes() == b"the player's own"
    assert kept.read_bytes() == b"the player's edit", "the kept copy stays a kept copy"
    assert _names(data) == ["Patch-A.MPQ", "Patch-A.MPQ" + ASIDE + ".1"]
    assert any(str(kept) in line for line in report.left_behind), "the kept copy is named"
