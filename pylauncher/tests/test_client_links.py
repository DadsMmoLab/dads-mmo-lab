"""T300: a module's client copy never writes through a link it did not choose.

The rule is `yulon.links`': a link is a symlink or a Windows junction (any
name-surrogate reparse point). A ready-to-play client is Yu'lon's own and holds
no link (`play_client.plan()` reproduces none), so a link anywhere on the way
into it is refused before a single file is copied. In the player's own client a
linked folder is the player's choice (a `Data/` on another drive) and is
followed, but a linked file is refused: copying onto it would change the file it
points to.

Every test drives the REAL `Applier.install()` over a SHIPPED manifest into real
folders under `tmp_path`, with only git, SQL and the DBC copier faked (the
`test_client_takeback` harness). Each link points into a second folder, and the
assertion is that this folder is byte for byte what it was.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.support_case import needs_case_sensitive_disk
from tests.test_client_takeback import MPQ, _make_play_client, _play_applier, _SodClone
from tests.test_server_dbc import (
    ARAC,
    ARAC_DBCS,
    SOD,
    _compose_run_double,
    _manifest,
    _the_app_s_applier,
    _volume,
    _write,
)
from yulon import apply as apply_module
from yulon import links, play_client
from yulon.apply import ApplyError, ApplyRefusal
from yulon.manifest import ClientFile, Manifest

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")


def _symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this account may not make symlinks")


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    """Every name under `folder` and every file's bytes, never following a link."""
    found: dict[str, bytes | None] = {}
    for here, dirs, files in os.walk(folder):
        for name in dirs:
            found[os.path.relpath(os.path.join(here, name), folder)] = None
        for name in files:
            path = Path(here) / name
            found[os.path.relpath(path, folder)] = path.read_bytes()
    return found


def _elsewhere(tmp_path: Path) -> Path:
    """The second folder a link points into, with a file of its own under the patch's name."""
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "Patch-A.MPQ", b"another program's Patch-A.MPQ")
    _write(elsewhere / "common.MPQ", b"another client's common.MPQ")
    return elsewhere


def _own_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, manifest: Manifest, git: Any = None
) -> tuple[Any, Path]:
    """The applier for the player's own client at `tmp_path/client` (no ready-to-play one)."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client = tmp_path / "client"
    client.mkdir(exist_ok=True)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client, manifest)
    if git is not None:
        applier.git = git
    return applier, client


def _ready_to_play(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, manifest: Manifest, git: Any = None
) -> tuple[Any, Path]:
    """The applier for a server WITH a ready-to-play client, and that client's folder."""
    server_dir, original = tmp_path / "server", tmp_path / "client"
    server_dir.mkdir()
    _write(original / "Data" / "common.MPQ", b"MPQ")  # a client has archives
    _compose_run_double(monkeypatch, _volume(tmp_path))
    play = _make_play_client(original, server_dir)
    _the_app_s_applier(monkeypatch, server_dir, original, manifest)  # the fakes
    return _play_applier(monkeypatch, server_dir, original, play, manifest, git), play


def _refused(applier: Any, manifest: Manifest, *named: Path) -> str:
    with pytest.raises(ApplyRefusal) as refused:
        applier.install(manifest)
    said = str(refused.value)
    for path in named:
        assert str(path) in said, said
    assert f"Nothing of {manifest.id} was put into your game client or deployed." in said
    return said


# ------------------------------------------------------- a ready-to-play client


@pytest.mark.parametrize(
    "name",
    [pytest.param("Data", id="same case"), pytest.param("data", marks=needs_case_sensitive_disk)],
)
def test_a_linked_data_folder_in_a_ready_to_play_client_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    manifest = _manifest(ARAC)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    for child in (play / "Data").iterdir():
        child.unlink()
    (play / "Data").rmdir()
    _symlink(elsewhere, play / name)

    _refused(applier, manifest, play / name)

    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("Patch-A.MPQ", id="same case"),
        pytest.param("patch-a.mpq", marks=needs_case_sensitive_disk),
    ],
)
def test_a_linked_file_in_a_ready_to_play_client_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    manifest = _manifest(ARAC)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    _symlink(elsewhere / "Patch-A.MPQ", play / "Data" / name)

    _refused(applier, manifest, play / "Data" / name)

    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize(
    "name",
    [pytest.param("enUS", id="same case"), pytest.param("enus", marks=needs_case_sensitive_disk)],
)
def test_a_linked_folder_deeper_in_is_refused_before_anything_is_copied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """A keg's `enUS/` lands on a linked folder; its top-level patch must not land first."""
    manifest = _manifest(SOD)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest, _SodClone(manifest, ARAC_DBCS))
    _symlink(elsewhere, play / "Data" / name)
    data_before = sorted(os.listdir(play / "Data"))

    _refused(applier, manifest, play / "Data" / name)

    assert _snapshot(elsewhere) == before
    assert sorted(os.listdir(play / "Data")) == data_before, "a file was copied before refusing"


@pytest.mark.parametrize("known_by", ["its marker alone", "its origins alone"])
def test_a_ready_to_play_client_is_known_by_its_marker_or_by_its_origins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, known_by: str
) -> None:
    """Either fact alone makes the folder Yu'lon's, where no link belongs."""
    manifest = _manifest(SOD)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    git = _SodClone(manifest, ARAC_DBCS)
    if known_by == "its marker alone":
        applier, client = _own_client(monkeypatch, tmp_path, manifest, git)
        _write(client / play_client.MARKER, b"{}")
    else:
        applier, client = _ready_to_play(monkeypatch, tmp_path, manifest, git)
        (client / play_client.MARKER).unlink()
    _symlink(elsewhere, client / "Data" / "enUS")

    _refused(applier, manifest, client / "Data" / "enUS")

    assert _snapshot(elsewhere) == before


def test_a_junction_in_a_ready_to_play_client_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows' answer for a junction: a folder with a name-surrogate reparse tag, no S_IFLNK."""
    manifest = _manifest(ARAC)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    junction = play / "Data"
    real_lstat = os.lstat

    def as_windows_sees(path: str | os.PathLike[str]) -> Any:
        found = real_lstat(path)
        if Path(path) != junction:
            return found

        class _Look:
            st_mode = found.st_mode
            st_file_attributes = 0x10 | links.FILE_ATTRIBUTE_REPARSE_POINT
            st_reparse_tag = links.IO_REPARSE_TAG_MOUNT_POINT

        return _Look()

    monkeypatch.setattr(links, "_lstat", as_windows_sees)
    before = sorted(os.listdir(junction))

    _refused(applier, manifest, junction)

    assert sorted(os.listdir(junction)) == before


# ------------------------------------------------------- the player's own client


@pytest.mark.parametrize(
    "name",
    [pytest.param("Data", id="same case"), pytest.param("data", marks=needs_case_sensitive_disk)],
)
def test_a_linked_data_folder_in_the_players_own_client_is_their_choice_and_followed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """A `Data/` on another drive is a setup a player makes on purpose; refusing it breaks them."""
    manifest = _manifest(ARAC)
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "common.MPQ", b"the player's common.MPQ")
    applier, client = _own_client(monkeypatch, tmp_path, manifest)
    _symlink(elsewhere, client / name)

    applier.install(manifest)

    assert (elsewhere / "Patch-A.MPQ").read_bytes() == MPQ
    assert (elsewhere / "common.MPQ").read_bytes() == b"the player's common.MPQ"
    assert (client / name).is_symlink(), "the player's link was replaced"


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("Patch-A.MPQ", id="same case"),
        pytest.param("patch-a.mpq", marks=needs_case_sensitive_disk),
    ],
)
def test_a_linked_file_in_the_players_own_client_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    manifest = _manifest(ARAC)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, client = _own_client(monkeypatch, tmp_path, manifest)
    _symlink(elsewhere / "Patch-A.MPQ", client / "Data" / name)

    _refused(applier, manifest, client / "Data" / name)

    assert _snapshot(elsewhere) == before
    assert (client / "Data" / name).is_symlink()


# ------------------------------------------------------- the copy itself (belt)


def test_the_copy_never_writes_through_a_linked_file(tmp_path: Path) -> None:
    """Should a link appear after the check, the copy stops at it rather than write through."""
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    source = tmp_path / "clone" / "Patch-A.MPQ"
    _write(source, MPQ)
    dest = tmp_path / "client" / "Data" / "Patch-A.MPQ"
    _symlink(elsewhere / "Patch-A.MPQ", dest)

    with pytest.raises(ApplyError, match="link"):
        apply_module._copy_unshared(source, dest)

    assert _snapshot(elsewhere) == before


class _LinkAppearsDuringSql:
    """The SQL step, run after the check and before the copy: a link appears meanwhile."""

    def __init__(self, folder: Path, target: Path) -> None:
        self.folder, self.target = folder, target

    def run_file(self, db: str, path: Path) -> None:
        if not self.folder.is_symlink():
            if self.folder.exists():
                shutil.rmtree(self.folder)
            _symlink(self.target, self.folder)

    def run_statement(self, db: str, statement: str) -> None:
        raise AssertionError("no manifest here runs an inline statement")


def test_a_linked_folder_that_appears_after_the_check_stops_the_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: the check runs before deploy and SQL; a link made since is met at the copy.

    The copy checks each file's way into a ready-to-play client again as it writes
    it, so `Data/` turned into a link during the SQL step stops the copy at the link
    and nothing lands in the folder it points to, not even an empty folder.
    """
    manifest = _manifest(ARAC)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    applier.sql = _LinkAppearsDuringSql(play / "Data", elsewhere)

    with pytest.raises(ApplyError) as failed:
        applier.install(manifest)

    assert (play / "Data").is_symlink(), "the test's link was not made"
    assert str(play / "Data") in str(failed.value), str(failed.value)
    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize(
    "linked",
    [
        pytest.param("Data", id="the folder it copies into"),
        pytest.param("Data/enUS", id="a folder under it"),
    ],
)
def test_a_linked_folder_that_appears_after_the_check_stops_a_folder_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, linked: str
) -> None:
    """The same for a step that copies a folder (`_copy_onto()`): a keg's `data/` with `enUS/`."""
    manifest = _manifest(SOD)
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest, _SodClone(manifest, ARAC_DBCS))
    conf = applier._conf
    link = play.joinpath(*linked.split("/"))

    def link_appears(*args: Any, **kwargs: Any) -> None:  # after the check, before the copy
        conf(*args, **kwargs)
        if link.exists():
            shutil.rmtree(link)
        _symlink(elsewhere, link)

    monkeypatch.setattr(applier, "_conf", link_appears)

    with pytest.raises(ApplyError) as failed:
        applier.install(manifest)

    assert str(link) in str(failed.value), str(failed.value)
    assert _snapshot(elsewhere) == before


def test_a_link_above_a_folder_the_copy_must_make_stops_it_before_making_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An addon's `Interface/AddOns/<name>` is made by the copy; through a linked `Interface/` it
    would be made in the folder the link points to, empty folders and all."""
    manifest = _manifest("kegs/bmah.json")
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    applier.sql = _LinkAppearsDuringSql(play / "Interface", elsewhere)

    with pytest.raises(ApplyError) as failed:
        applier.install(manifest)

    assert str(play / "Interface") in str(failed.value), str(failed.value)
    assert _snapshot(elsewhere) == before


def test_a_single_file_step_under_a_linked_folder_makes_nothing_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cold review: a step that copies one file asks about its folder before making it.

    An addon step whose source is one file lands in `Interface/AddOns/<name>/`, which
    the copy makes. With `Interface/` turned into a link after the check, making that
    folder first would make it in the place the link points to.
    """
    shipped = _manifest("kegs/bmah.json")
    one_file = ClientFile(src="BlackMarketUI.toc", dest="addons", name="BlackMarketUI")
    manifest = shipped.model_copy(update={"client": [one_file]})
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    applier.sql = _LinkAppearsDuringSql(play / "Interface", elsewhere)

    with pytest.raises(ApplyError) as failed:
        applier.install(manifest)

    assert str(play / "Interface") in str(failed.value), str(failed.value)
    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize("linked", ["the client folder itself", "a folder above it"])
def test_a_ready_to_play_client_reached_through_a_link_is_installed_into(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, linked: str
) -> None:
    """Cold review: the client folder is where the player keeps it, link or not.

    Only links from the client folder down are Yu'lon's business; one that leads to
    the folder (moved to another drive and linked back) is followed, both by the check
    before the install and by the one at the copy.
    """
    manifest = _manifest(ARAC)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    if linked == "the client folder itself":
        real = tmp_path / "other drive" / play.name
        real.parent.mkdir()
        play.rename(real)
        _symlink(real, play)
    else:
        real_parent = tmp_path / "other drive"
        real_parent.mkdir()
        real = real_parent / play.name
        play.rename(real)
        _symlink(real_parent, tmp_path / "linked parent")
        applier.client_dir = tmp_path / "linked parent" / play.name

    applier.install(manifest)

    landed = real / "Data" / "Patch-A.MPQ"
    assert landed.is_file() and not landed.is_symlink()


def test_the_copy_names_the_outermost_link_on_the_way(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With a link inside a linked folder, the one to replace is the folder nearest the client."""
    manifest = _manifest(ARAC)
    applier, play = _ready_to_play(monkeypatch, tmp_path, manifest)
    elsewhere, further = tmp_path / "elsewhere", tmp_path / "further"
    elsewhere.mkdir()
    further.mkdir()
    shutil.rmtree(play / "Data")
    _symlink(elsewhere, play / "Data")
    _symlink(further, elsewhere / "enUS")

    found = applier._link_on_the_way(play / "Data" / "enUS" / "x.MPQ", True, file=True)

    assert found == play / "Data"
