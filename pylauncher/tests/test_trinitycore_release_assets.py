"""The `fetch-release` stage and the `release_assets` model (T850-B).

MoP's databases do not come from its git checkout: they come from a GitHub release
asset, a zip with four `.sql` files at its ROOT and no folder (GAP §0). This file
proves the mechanism on a fixture entry that carries such an asset, and proves that
an entry WITHOUT one -- Centurion, and every shipped entry today -- is untouched:
the same stage tuple, the same step count, and an existing install's record read
back as finished.

Nothing here touches a network or Docker: the download is the engine's own seam
(`release_download`), handed a zip built here, so what the stage does around that
fetch is all that is under test. The zip's checksum is real (its bytes are), which
is what lets a tampered download be refused by the very code the app runs.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.support_trinitycore import (
    MOP_DATABASE_URL,
    MOP_MEMBERS,
    centurion_data,
    centurion_like,
    database_zip,
    release_asset,
)
from yulon.catalog import native, verified_download
from yulon.catalog.catalog import CatalogEntry, parse_catalog
from yulon.catalog.families.trinitycore import (
    RELEASE_PARTIAL_SUFFIX,
    TrinityCoreInstaller,
)
from yulon.catalog.installer import InstallerError, InstallStopped

CENTURION_STAGES = (
    "clone-sources",
    "db-password",
    "write-dockerfile",
    "generate-compose",
    "build",
    "client-data",
    "conf",
    "start-db",
    "import",
    "up",
    "ready",
)
"""Centurion's stages, word for word, pinned here so a new stage cannot slip into them."""

FETCH_RELEASE = "fetch-release"

DEST = "release/v1.0.4/db"
"""Where the fixture asset's members land, relative to the server dir."""

DATABASE_ZIP = database_zip()
"""The fixture asset's bytes: the four `.sql` files at the zip's root, and nothing else."""

DATABASE_ASSET = release_asset(DATABASE_ZIP)

UNPACKED_NAMES = sorted([*MOP_MEMBERS, verified_download.UNPACKED])
"""What the unpacked folder holds, as `unpacked()` sorts it: the four members and the note."""


# -- fixtures ---------------------------------------------------------------------------------


class Downloads:
    """The engine's download seam: serves one local file, and remembers what was asked for."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self.calls: list[str] = []

    def __call__(self, url: str, dest: Path) -> Path:
        self.calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, dest)
        return dest


def a_served_file(tmp_path: Path, name: str, body: bytes) -> Path:
    """`body` on disk, as a download would leave it."""
    path = tmp_path / name
    path.write_bytes(body)
    return path


def engine(
    entry: CatalogEntry, *, download: Callable[[str, Path], Path] | None = None
) -> TrinityCoreInstaller:
    """A real engine for `entry`, its download seam faked so nothing leaves this machine."""
    return TrinityCoreInstaller(
        entry,
        seams=native.Seams(platform_id=lambda: "linux"),
        release_download=download,
    )


def mop_like(*, assets: list[dict[str, Any]] | None = None) -> CatalogEntry:
    """The fixture entry with one release asset (T850-B's shape), unless `assets` says otherwise."""
    return centurion_like(release_assets=[DATABASE_ASSET] if assets is None else assets)


def fetch_lines(
    entry: CatalogEntry,
    server_dir: Path,
    download: Callable[[str, Path], Path],
    *,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """The `fetch-release` stage of `entry`, run alone on `server_dir`, as it yields."""
    made = engine(entry, download=download)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(game_id=entry.id, install_id="test", family="trinitycore"),
        cancel=cancel,
        secrets=native.Secrets(db_password="tc-0123456789abcdef"),
    )
    return made.stage_named(FETCH_RELEASE).run(ctx)


def fetch(
    entry: CatalogEntry,
    server_dir: Path,
    download: Callable[[str, Path], Path],
    *,
    cancel: threading.Event | None = None,
) -> list[str]:
    """`fetch_lines()`, run to the end: the stage finished."""
    return list(fetch_lines(entry, server_dir, download, cancel=cancel))


def dest_of(server_dir: Path) -> Path:
    return server_dir / DEST


def unpacked(server_dir: Path) -> list[str]:
    """Every name in the unpacked folder, sorted: the members, and this app's note."""
    return sorted(path.name for path in dest_of(server_dir).iterdir())


# -- the model ---------------------------------------------------------------------------------


def _document(**fields: Any) -> dict[str, Any]:
    """The entry document with one release asset whose named fields are replaced."""
    return centurion_data(release_assets=[{**DATABASE_ASSET, **fields}])


REFUSALS: dict[str, tuple[dict[str, Any], str]] = {
    "a plain http url": (_document(url="http://github.com/x/db.zip"), "https URL"),
    "a url with credentials": (
        _document(url="https://user:pw@github.com/x/db.zip"),
        "credentials",
    ),
    "a sha256 that is not 64 hex": (
        _document(sha256="abc123"),
        "String should match pattern",
    ),
    "a size of zero": (_document(size=0), "greater than 0"),
    "a dest that climbs out": (_document(dest="../db"), "inside its folder"),
    "a dest that is absolute": (_document(dest="/db"), "inside its folder"),
    "no members at all": (_document(members=[]), "at least 1 item"),
    "a member in a folder": (_document(members=["sql/auth.sql"]), "plain file name"),
    "a member that climbs out": (_document(members=[".."]), "plain file name"),
    "a member with a backslash": (_document(members=["a\\b.sql"]), "plain file name"),
    "a member with a colon": (_document(members=["C:a.sql"]), "plain file name"),
    "a member put twice": (_document(members=["auth.sql", "auth.sql"]), "repeat a name"),
}


def test_the_fixture_asset_is_valid_on_its_own() -> None:
    """So a refusal below can never be the base fixture's fault."""
    (asset,) = mop_like().install.native.trinitycore.release_assets
    assert asset.url == MOP_DATABASE_URL
    assert asset.dest == DEST
    assert asset.members == MOP_MEMBERS
    assert asset.size == len(DATABASE_ZIP)


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_each_rule_refuses_its_one_bad_input(name: str) -> None:
    document, fragment = REFUSALS[name]
    with pytest.raises(ValidationError, match=re.escape(fragment)):
        parse_catalog({"schema_version": 1, "games": [document]})


def test_the_hosts_a_release_asset_may_be_fetched_from_are_the_entrys_own() -> None:
    """The asset's own host, plus GitHub's two release-asset hosts, and nothing else.

    Each url's host is what the catalog names and GitHub's two are what it redirects
    to -- the fence `Client.hosts()` is for the client's downloads, over the server
    side (T850-B): never a wildcard, and never a host nobody named.
    """
    assert mop_like().install.native.trinitycore.release_hosts() == frozenset(
        {
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
    )
    assert centurion_like().install.native.trinitycore.release_hosts() == frozenset(
        {"objects.githubusercontent.com", "release-assets.githubusercontent.com"}
    )


# -- the stage is the entry's, and Centurion's tuple does not move -----------------------------


def test_an_entry_without_assets_runs_the_old_tuple_word_for_word() -> None:
    """Centurion's stages and its step count do not move because another entry needs a stage."""
    names = engine(centurion_like()).stage_names()
    assert names == CENTURION_STAGES
    assert FETCH_RELEASE not in names
    assert len(names) == 11


def test_an_entry_with_assets_has_fetch_release_second() -> None:
    """Second, so a bad download is refused before the ~75-minute compile."""
    names = engine(mop_like()).stage_names()
    assert names == (CENTURION_STAGES[0], FETCH_RELEASE, *CENTURION_STAGES[1:])
    assert names.index(FETCH_RELEASE) == 1
    assert engine(mop_like()).stage_named(FETCH_RELEASE).recorded is True


def test_the_class_tuple_accepts_fetch_release_so_a_mop_record_reads_back() -> None:
    """`STAGE_NAMES` is what a reader with the family but no entry validates a record against.

    `installer.unfinished_import()` is that reader, and a name missing from the class
    tuple is a name it drops on the way in, so the record it hands the rest of the
    app would disagree with the one on disk.
    """
    assert FETCH_RELEASE in TrinityCoreInstaller.STAGE_NAMES
    assert TrinityCoreInstaller.STAGE_NAMES == (
        CENTURION_STAGES[0],
        FETCH_RELEASE,
        *CENTURION_STAGES[1:],
    )
    assert len(set(TrinityCoreInstaller.STAGE_NAMES)) == len(TrinityCoreInstaller.STAGE_NAMES)


# -- the record --------------------------------------------------------------------------------


def write_record(server_dir: Path, completed: tuple[str, ...], game_id: str) -> None:
    server_dir.mkdir(parents=True, exist_ok=True)
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=game_id, install_id="test", family="trinitycore", completed=completed
        ),
    )


def unfinished(entry: CatalogEntry, server_dir: Path) -> list[str]:
    """The stages THIS ENTRY's install still owes: its own `stages()`, not the class tuple."""
    made = engine(entry)
    state = native.read_state(server_dir, valid=made.stage_names())
    assert state is not None
    return [name for name in made.stage_names() if not state.has(name)]


def test_a_finished_centurion_record_still_reads_as_finished(tmp_path: Path) -> None:
    """The record an existing install on disk holds: every old stage, and nothing else.

    Read back against the entry's own stage tuple -- what a resume and "Use
    existing…" ask. With `fetch-release` in Centurion's `stages()` an install that
    has finished could never read as finished, and every press would offer to carry
    it on.
    """
    server_dir = tmp_path / "srv"
    write_record(server_dir, CENTURION_STAGES, "wow-centurion")
    entry = centurion_like()
    assert unfinished(entry, server_dir) == []
    state = native.read_state(server_dir, valid=engine(entry).stage_names())
    assert state is not None and state.completed == CENTURION_STAGES
    assert native.read_claim(server_dir, valid=TrinityCoreInstaller.STAGE_NAMES).state == state


def test_a_record_without_fetch_release_reads_unfinished_for_an_entry_that_has_it(
    tmp_path: Path,
) -> None:
    """The same folder, for the entry that needs the stage: one stage is missing."""
    server_dir = tmp_path / "srv"
    write_record(server_dir, CENTURION_STAGES, "wow-mop")
    assert unfinished(mop_like(), server_dir) == [FETCH_RELEASE]


def test_a_record_naming_fetch_release_reads_finished_and_keeps_it(tmp_path: Path) -> None:
    """A MoP install's own record: the stage survives `read_claim` with the class tuple."""
    server_dir = tmp_path / "srv"
    written = (CENTURION_STAGES[0], FETCH_RELEASE, *CENTURION_STAGES[1:])
    write_record(server_dir, written, "wow-mop")
    claim = native.read_claim(server_dir, valid=TrinityCoreInstaller.STAGE_NAMES)
    assert claim.state is not None and claim.state.completed == written
    assert unfinished(mop_like(), server_dir) == []
    # The same folder read against an entry that has no such stage: nothing of ITS
    # stages is missing, and the name it does not know is KEPT rather than dropped
    # (`InstallState.unknown`'s promise to a folder another entry's install wrote).
    centurion = native.read_state(server_dir, valid=engine(centurion_like()).stage_names())
    assert centurion is not None and centurion.unknown == (FETCH_RELEASE,)


# -- the fetch ---------------------------------------------------------------------------------


def test_the_members_land_the_note_is_written_and_the_zip_is_deleted(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    downloads = Downloads(a_served_file(tmp_path, "served.zip", DATABASE_ZIP))
    said = fetch(mop_like(), server_dir, downloads)
    assert downloads.calls == [MOP_DATABASE_URL]
    assert unpacked(server_dir) == UNPACKED_NAMES
    note = json.loads(
        (dest_of(server_dir) / verified_download.UNPACKED).read_text(encoding="utf-8")
    )
    assert note == {"url": MOP_DATABASE_URL, "sha256": DATABASE_ASSET["sha256"]}
    assert not (server_dir / f"{DEST}.download.zip").exists()
    assert not (server_dir / f"{DEST}{RELEASE_PARTIAL_SUFFIX}").exists()
    assert said == [
        f"Downloading {MOP_DATABASE_URL}",
        "db.download.zip matches its pinned size and sha256.",
        "Unpacked 4 files into release/v1.0.4/db; "
        "LoA-Pandaria-Playerbots-v1.0.4-win64-database.zip was deleted.",
    ]


def test_a_second_run_skips_the_download_entirely(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    served = a_served_file(tmp_path, "served.zip", DATABASE_ZIP)
    fetch(mop_like(), server_dir, Downloads(served))
    again = Downloads(served)
    said = fetch(mop_like(), server_dir, again)
    assert again.calls == [], "the download seam was not reached at all"
    assert said == [
        "release/v1.0.4/db already holds LoA-Pandaria-Playerbots-v1.0.4-win64-database.zip; "
        "not downloaded again."
    ]


def test_a_member_deleted_from_the_folder_means_the_asset_is_fetched_again(
    tmp_path: Path,
) -> None:
    """The note alone is not enough: the folder is a person's disk, and it can lose a file."""
    server_dir = tmp_path / "srv"
    served = a_served_file(tmp_path, "served.zip", DATABASE_ZIP)
    fetch(mop_like(), server_dir, Downloads(served))
    (dest_of(server_dir) / "world.sql").unlink()
    again = Downloads(served)
    said = fetch(mop_like(), server_dir, again)
    assert again.calls == [MOP_DATABASE_URL]
    assert "Unpacked 4 files" in said[-1]
    assert unpacked(server_dir) == UNPACKED_NAMES


def test_a_download_that_does_not_match_its_sha256_is_fetched_once_more_then_refused(
    tmp_path: Path,
) -> None:
    """One byte different and the same size: only the sha256 can catch this one."""
    server_dir = tmp_path / "srv"
    tampered = bytearray(DATABASE_ZIP)
    tampered[10] ^= 0xFF
    downloads = Downloads(a_served_file(tmp_path, "served.zip", bytes(tampered)))
    said: list[str] = []
    with pytest.raises(InstallerError, match=re.escape("did not match the size and sha256")):
        for line in fetch_lines(mop_like(), server_dir, downloads):
            said.append(line)
    assert said == [
        f"Downloading {MOP_DATABASE_URL}",
        "The download did not match its pinned sha256; fetching it again.",
        f"Downloading {MOP_DATABASE_URL}",
    ], "the failure is said, then the fetch is made once more"
    assert downloads.calls == [MOP_DATABASE_URL, MOP_DATABASE_URL], "retried exactly once"
    assert not dest_of(server_dir).exists()
    assert not (server_dir / f"{DEST}.download.zip").exists()


def test_a_download_of_the_wrong_length_is_refused_and_nothing_is_unpacked(
    tmp_path: Path,
) -> None:
    """The other half of the pin: a transfer cut off early is not complete by its own lights."""
    server_dir = tmp_path / "srv"
    downloads = Downloads(a_served_file(tmp_path, "short.zip", DATABASE_ZIP[:100]))
    with pytest.raises(InstallerError, match=re.escape("did not match the size and sha256")):
        fetch(mop_like(), server_dir, downloads)
    assert downloads.calls == [MOP_DATABASE_URL, MOP_DATABASE_URL]
    assert not dest_of(server_dir).exists()
    assert not (server_dir / f"{DEST}.download.zip").exists()
    assert not (server_dir / f"{DEST}{RELEASE_PARTIAL_SUFFIX}").exists()


def test_a_folder_of_somebody_elses_is_refused_and_left_exactly_as_it_is(tmp_path: Path) -> None:
    """No note, so the folder is not this app's, and it is never replaced."""
    server_dir = tmp_path / "srv"
    foreign = dest_of(server_dir)
    foreign.mkdir(parents=True)
    (foreign / "mine.sql").write_bytes(b"a person's dump\n")
    downloads = Downloads(a_served_file(tmp_path, "served.zip", DATABASE_ZIP))
    with pytest.raises(InstallerError, match=re.escape("did not unpack from")):
        fetch(mop_like(), server_dir, downloads)
    assert downloads.calls == [], "nothing was fetched for a folder that cannot be replaced"
    assert sorted(path.name for path in foreign.iterdir()) == ["mine.sql"]
    assert (foreign / "mine.sql").read_bytes() == b"a person's dump\n"


def test_a_folder_this_app_unpacked_from_an_older_pin_is_replaced_whole(tmp_path: Path) -> None:
    """Its own note is what says so; nothing of the old unpack is left behind."""
    server_dir = tmp_path / "srv"
    old = dest_of(server_dir)
    old.mkdir(parents=True)
    (old / "old.sql").write_bytes(b"-- an older asset\n")
    verified_download.write_unpacked(old, url=MOP_DATABASE_URL, sha256="0" * 64)
    said = fetch(mop_like(), server_dir, Downloads(a_served_file(tmp_path, "s.zip", DATABASE_ZIP)))
    assert unpacked(server_dir) == UNPACKED_NAMES
    assert "Unpacked 4 files" in said[-1]


# -- the zip's members -------------------------------------------------------------------------


def test_a_zip_missing_a_listed_member_is_refused_by_name_before_anything_lands(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "srv"
    short = database_zip(("auth.sql", "characters.sql"))
    served = a_served_file(tmp_path, "short.zip", short)
    with pytest.raises(InstallerError, match=re.escape("does not hold playerbots.sql at its root")):
        fetch(mop_like(assets=[release_asset(short)]), server_dir, Downloads(served))
    assert not dest_of(server_dir).exists()
    assert not (server_dir / f"{DEST}{RELEASE_PARTIAL_SUFFIX}").exists()


def test_a_member_nobody_listed_is_never_extracted(tmp_path: Path) -> None:
    """Another server's dump in the same zip must not reach the disk."""
    server_dir = tmp_path / "srv"
    body = database_zip(extra={"someone-elses.sql": b"-- not ours\n"})
    served = a_served_file(tmp_path, "extra.zip", body)
    said = fetch(mop_like(assets=[release_asset(body)]), server_dir, Downloads(served))
    assert unpacked(server_dir) == UNPACKED_NAMES
    assert "Unpacked 4 files" in said[-1]


def test_a_zip_member_that_climbs_out_is_not_the_listed_name(tmp_path: Path) -> None:
    """`../x.sql` in the zip against `x.sql` in the list: no match, so `x.sql` is missing."""
    server_dir = tmp_path / "srv"
    climbing = database_zip(extra={"../x.sql": b"-- from outside\n"})
    served = a_served_file(tmp_path, "climbing.zip", climbing)
    entry = mop_like(assets=[release_asset(climbing, members=[*MOP_MEMBERS[1:], "x.sql"])])
    with pytest.raises(InstallerError, match=re.escape("does not hold x.sql at its root")):
        fetch(entry, server_dir, Downloads(served))
    assert not dest_of(server_dir).exists()
    assert not (server_dir / "release" / "x.sql").exists()
    assert not (tmp_path / "x.sql").exists()


def test_unzip_members_refuses_a_listed_name_that_would_leave_the_folder(tmp_path: Path) -> None:
    """The model refuses these names too; this is the second fence, where the path is built."""
    zip_path = a_served_file(tmp_path, "db.zip", DATABASE_ZIP)
    for name in ("../auth.sql", "sub/auth.sql", "/auth.sql", "C:auth.sql", "auth\\sql"):
        with pytest.raises(InstallerError, match="not a plain file name"):
            verified_download.unzip_members(
                zip_path, tmp_path / "partial", [name], cancel_check=lambda: None
            )
    assert not (tmp_path / "partial").exists()


def test_a_stop_between_two_members_leaves_the_partial_folder(tmp_path: Path) -> None:
    """`unzip_members`'s own half of the promise: a Stop is checked before each member."""
    partial = tmp_path / "db.yulon-partial"
    zip_path = a_served_file(tmp_path, "db.zip", DATABASE_ZIP)
    checked = 0

    def cancel_check() -> None:
        nonlocal checked
        checked += 1
        if checked > 1:
            raise InstallStopped("the install was stopped")

    with pytest.raises(InstallStopped):
        verified_download.unzip_members(zip_path, partial, MOP_MEMBERS, cancel_check=cancel_check)
    assert checked == 2, "checked before the first member, then before the second"
    assert [path.name for path in partial.iterdir()] == ["auth.sql"]
    assert not (partial / verified_download.UNPACKED).exists(), "the note is written last"


def test_the_next_run_removes_a_left_over_partial_and_unpacks_afresh(tmp_path: Path) -> None:
    """The stage's other half: a partial from a Stop is never read as finished."""
    server_dir = tmp_path / "srv"
    partial = server_dir / f"{DEST}{RELEASE_PARTIAL_SUFFIX}"
    partial.mkdir(parents=True)
    (partial / "auth.sql").write_bytes(b"-- half a folder, from a Stop\n")
    served = a_served_file(tmp_path, "served.zip", DATABASE_ZIP)
    said = fetch(mop_like(), server_dir, Downloads(served))
    assert "Unpacked 4 files" in said[-1]
    assert unpacked(server_dir) == UNPACKED_NAMES
    assert not partial.exists()
    assert (dest_of(server_dir) / "auth.sql").read_bytes() == b"-- auth.sql\n"


def test_a_stop_after_the_download_renames_nothing_into_place(tmp_path: Path) -> None:
    """The press's cancel event, read inside the unpack: no member lands and nothing is renamed."""
    server_dir = tmp_path / "srv"
    stop = threading.Event()
    downloads = Downloads(a_served_file(tmp_path, "served.zip", DATABASE_ZIP))

    def stopping_download(url: str, dest: Path) -> Path:
        result = downloads(url, dest)
        stop.set()
        return result

    with pytest.raises(InstallStopped):
        fetch(mop_like(), server_dir, stopping_download, cancel=stop)
    assert not dest_of(server_dir).exists()
    partial = server_dir / f"{DEST}{RELEASE_PARTIAL_SUFFIX}"
    assert (
        partial.exists() and list(partial.iterdir()) == []
    ), "the folder the unpack had just made, with no member in it"
    assert (server_dir / f"{DEST}.download.zip").is_file(), "the zip a resume reuses is kept"
