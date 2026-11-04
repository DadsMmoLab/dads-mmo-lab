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
import urllib.request
from collections.abc import Callable, Iterator, Mapping
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
from yulon import client_packs, platform
from yulon.catalog import native, verified_download
from yulon.catalog.catalog import RELEASE_HOSTS, CatalogEntry, parse_catalog
from yulon.catalog.families.trinitycore import (
    RELEASE_PARTIAL_SUFFIX,
    TrinityCoreInstaller,
)
from yulon.catalog.installer import InstallerError, InstallStopped
from yulon.selfupdate import fetch as selfupdate_fetch
from yulon.update import _Deadline

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
    entry: CatalogEntry,
    *,
    download: Callable[[str, Path], Path] | None = None,
    opener: client_packs.Opener | None = None,
) -> TrinityCoreInstaller:
    """A real engine for `entry`, its download seam faked so nothing leaves this machine.

    `download` replaces the whole fetch; `opener` leaves the stage's real fetcher
    (`verified_download.fetch_bounded`) in place and fakes only its `Opener`, so the
    bounded fetch itself is exercised with no network.
    """
    return TrinityCoreInstaller(
        entry,
        seams=native.Seams(platform_id=lambda: "linux"),
        release_download=download,
        release_opener=opener,
    )


def mop_like(*, assets: list[dict[str, Any]] | None = None) -> CatalogEntry:
    """The fixture entry with one release asset (T850-B's shape), unless `assets` says otherwise."""
    return centurion_like(release_assets=[DATABASE_ASSET] if assets is None else assets)


def fetch_lines(
    entry: CatalogEntry,
    server_dir: Path,
    download: Callable[[str, Path], Path] | None = None,
    *,
    cancel: threading.Event | None = None,
    opener: client_packs.Opener | None = None,
) -> Iterator[str]:
    """The `fetch-release` stage of `entry`, run alone on `server_dir`, as it yields."""
    made = engine(entry, download=download, opener=opener)
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
    download: Callable[[str, Path], Path] | None = None,
    *,
    cancel: threading.Event | None = None,
    opener: client_packs.Opener | None = None,
) -> list[str]:
    """`fetch_lines()`, run to the end: the stage finished."""
    return list(fetch_lines(entry, server_dir, download, cancel=cancel, opener=opener))


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
    "a url on another host": (
        _document(url="https://example.com/x/db.zip"),
        "must be on github.com",
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


def test_the_release_fence_is_fixed_data_github_and_the_two_hosts_it_redirects_to() -> None:
    """`RELEASE_HOSTS` is the same for every entry: github.com and GitHub's two asset hosts.

    Fixed, never derived from an asset's own url (review round 1): a fence built from
    the addresses it is meant to bound can refuse nothing. `ReleaseAsset`'s validator
    keeps each url on github.com, and the download's redirect may land only on the two.
    """
    assert RELEASE_HOSTS == frozenset(
        {
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
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


# -- the bounded fetch, through the stage's real opener seam (T850-E) ----------------------------


class ServingOpener:
    """A fake `client_packs.Opener`: serves one body in small pieces, counting what it reads."""

    def __init__(
        self,
        body: bytes,
        *,
        chunk: int = 64,
        after_first_read: Callable[[], None] | None = None,
    ) -> None:
        self.body = body
        self.chunk = chunk
        self.after_first_read = after_first_read
        self.reads = 0
        """Body bytes handed out, across every response this opener has served."""
        self.requests: list[tuple[str, frozenset[str]]] = []
        self.closed = False

    def __call__(
        self,
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Served:
        self.requests.append((url, hosts))
        return _Served(self)


class _Served:
    """One response: `read1` answers at most `chunk` bytes, the way a socket does."""

    def __init__(self, opener: ServingOpener) -> None:
        self.status = 200
        self._opener = opener
        self._pos = 0
        self._served = False

    def read1(self, amount: int, /) -> bytes:
        opener = self._opener
        data = opener.body[self._pos : self._pos + min(amount, opener.chunk)]
        self._pos += len(data)
        opener.reads += len(data)
        if not self._served and data:
            self._served = True
            if opener.after_first_read is not None:
                opener.after_first_read()
        return data

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return None

    def close(self) -> None:
        self._opener.closed = True


class RedirectingOpener:
    """A fake opener that refuses a redirect off the fence before a body byte exists.

    Models `client_packs._open` exactly: the redirect is judged inside `urlopen`, so
    no response is returned and the body (the `ServingOpener` here) is never read.
    """

    def __init__(self, sentence: str, body: bytes) -> None:
        self.sentence = sentence
        self.inner = ServingOpener(body)

    def __call__(
        self,
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Served:
        raise selfupdate_fetch.UpdateError(self.sentence)


def test_the_real_fetcher_downloads_through_a_fake_opener_and_the_stage_unpacks(
    tmp_path: Path,
) -> None:
    """The stage's default path: `fetch_bounded` through the entry's opener, then the unpack."""
    server_dir = tmp_path / "srv"
    opener = ServingOpener(DATABASE_ZIP, chunk=1 << 16)
    said = fetch(mop_like(), server_dir, opener=opener)
    assert opener.requests == [(MOP_DATABASE_URL, RELEASE_HOSTS)]
    assert unpacked(server_dir) == UNPACKED_NAMES
    assert "Unpacked 4 files" in said[-1]


def test_a_redirect_off_the_release_hosts_is_refused_before_any_body_byte(tmp_path: Path) -> None:
    """The opener refuses the redirect, so the fetcher surfaces it with nothing on disk."""
    opener = RedirectingOpener(
        "The download was redirected to 'evil.example', which this server's catalog entry "
        "does not name as a plain https address (no port, no login), so Yu'lon did not follow it.",
        DATABASE_ZIP,
    )
    path = tmp_path / "db.download.zip"
    with pytest.raises(InstallerError, match=re.escape("does not name as a plain https address")):
        verified_download.fetch_bounded(
            MOP_DATABASE_URL,
            path,
            size=1 << 30,
            hosts=RELEASE_HOSTS,
            opener=opener,
            cancelled=lambda: False,
        )
    assert opener.inner.reads == 0, "the body was never read"
    assert not path.exists()
    assert not (tmp_path / "db.download.zip.part").exists()


def test_the_real_opener_refuses_a_release_redirect_to_a_host_the_fence_does_not_name() -> None:
    """The other half: `_https_only_opener` itself, held to `RELEASE_HOSTS`."""
    opener = selfupdate_fetch._https_only_opener(
        _Deadline(1.0), platform.verify_context(), hosts=RELEASE_HOSTS
    )
    handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    request = urllib.request.Request(MOP_DATABASE_URL, method="GET")

    with pytest.raises(selfupdate_fetch.UpdateError, match="does not name"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/x.zip")

    followed = handler.redirect_request(
        request, None, 302, "Found", {}, "https://objects.githubusercontent.com/x.zip"
    )
    assert followed is not None, "a redirect to a host the fence names IS followed"


def test_a_body_larger_than_the_pin_stops_at_size_plus_one_and_deletes_the_part(
    tmp_path: Path,
) -> None:
    opener = ServingOpener(DATABASE_ZIP + b"more bytes than the pin allows\n")
    path = tmp_path / "db.download.zip"
    with pytest.raises(
        InstallerError, match=re.escape(f"sent more than the {len(DATABASE_ZIP)} bytes")
    ):
        verified_download.fetch_bounded(
            MOP_DATABASE_URL,
            path,
            size=len(DATABASE_ZIP),
            hosts=RELEASE_HOSTS,
            opener=opener,
            cancelled=lambda: False,
        )
    assert opener.reads == len(DATABASE_ZIP) + 1, "stopped the moment size + 1 had arrived"
    assert not path.exists()
    assert not (tmp_path / "db.download.zip.part").exists()


def test_a_stop_after_the_first_chunk_ends_the_fetch_and_deletes_the_part(tmp_path: Path) -> None:
    """The stage's cancel, read between chunks by the fetcher, reported as a stop."""
    server_dir = tmp_path / "srv"
    stop = threading.Event()
    opener = ServingOpener(DATABASE_ZIP, after_first_read=stop.set)
    with pytest.raises(InstallStopped, match="the install was stopped"):
        fetch(mop_like(), server_dir, opener=opener, cancel=stop)
    # `opener.reads == 64` below is what proves the fetch's own Stop check: the unpack's
    # later check would raise the same InstallStopped.
    assert opener.reads == 64, "the first chunk arrived, then the cancel was seen"
    assert not (server_dir / f"{DEST}.download.zip").exists()
    assert not (server_dir / f"{DEST}.download.zip.part").exists()
    assert not dest_of(server_dir).exists()


def test_a_zip_that_finished_before_a_stop_is_proved_and_reused_not_fetched_again(
    tmp_path: Path,
) -> None:
    """Codex round 2: the bounded fetcher hands back a finished `.download.zip`, so
    `download_verified()` proves it by sha256 and the opener is never asked."""
    server_dir = tmp_path / "srv"
    zip_path = server_dir / f"{DEST}.download.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(DATABASE_ZIP)
    opener = ServingOpener(DATABASE_ZIP)
    fetch(mop_like(), server_dir, opener=opener)
    assert opener.requests == [], "a proved zip already on disk is not downloaded again"
    assert (dest_of(server_dir) / "world.sql").is_file()
    assert not zip_path.exists(), "deleted once unpacked"


def test_a_stale_zip_on_disk_is_deleted_and_fetched_once_through_the_fence(
    tmp_path: Path,
) -> None:
    """The reuse never trusts the file: a wrong one fails the sha256, is deleted, and the
    second call fetches it through the bounded opener."""
    server_dir = tmp_path / "srv"
    zip_path = server_dir / f"{DEST}.download.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(b"not the pinned zip")
    opener = ServingOpener(DATABASE_ZIP)
    fetch(mop_like(), server_dir, opener=opener)
    assert len(opener.requests) == 1
    assert (dest_of(server_dir) / "auth.sql").is_file()


def test_a_stop_before_the_first_byte_never_reaches_the_opener(tmp_path: Path) -> None:
    """A Stop pressed before the request is honoured without connecting (T850-M3)."""
    opener = ServingOpener(DATABASE_ZIP)
    path = tmp_path / "db.download.zip"
    with pytest.raises(InstallStopped, match="the install was stopped"):
        verified_download.fetch_bounded(
            MOP_DATABASE_URL,
            path,
            size=len(DATABASE_ZIP),
            hosts=RELEASE_HOSTS,
            opener=opener,
            cancelled=lambda: True,
        )
    assert opener.requests == [], "the opener was never asked"
    assert not path.exists()
    assert not (tmp_path / "db.download.zip.part").exists()


def test_a_failed_rename_deletes_the_part_and_is_refused_as_unwritable(
    tmp_path: Path,
) -> None:
    """A folder at `path` breaks the rename, so the part is deleted, not left (T850-M3)."""
    opener = ServingOpener(DATABASE_ZIP)
    path = tmp_path / "db.download.zip"
    path.mkdir()
    with pytest.raises(InstallerError, match=re.escape("could not be written")):
        verified_download.fetch_bounded(
            MOP_DATABASE_URL,
            path,
            size=len(DATABASE_ZIP),
            hosts=RELEASE_HOSTS,
            opener=opener,
            cancelled=lambda: False,
        )
    assert path.is_dir(), "the folder that broke the rename is left as it is"
    assert not (tmp_path / "db.download.zip.part").exists()


def test_the_progress_callback_logs_about_every_tenth_and_the_full_size(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A small `size` over 64-byte chunks: lines arrive and the last says the full size."""
    from yulon.catalog.families import trinitycore as trinitycore_family

    body = b"x" * (1 << 20)
    opener = ServingOpener(body, chunk=64)
    path = tmp_path / "db.download.zip"
    zip_name = "LoA-Pandaria-Playerbots-v1.0.4-win64-database.zip"
    with caplog.at_level("INFO", logger="yulon.catalog.families.trinitycore"):
        verified_download.fetch_bounded(
            MOP_DATABASE_URL,
            path,
            size=len(body),
            hosts=RELEASE_HOSTS,
            opener=opener,
            cancelled=lambda: False,
            progress=trinitycore_family._release_progress(zip_name, len(body)),
        )
    said = [
        record.getMessage()
        for record in caplog.records
        if record.name == "yulon.catalog.families.trinitycore"
        and record.getMessage().startswith("Downloading")
    ]
    assert said, "progress lines are emitted"
    assert said[-1] == f"Downloading {zip_name}: 1 of 1 MB", "the last says the full size"


def test_the_progress_callback_logs_every_sixteen_megabytes_for_a_large_asset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The 142 MB asset's 10% is rarer than 16 MB never: the floor wins (T850-M3)."""
    from yulon.catalog.families import trinitycore as trinitycore_family

    zip_name = "LoA-Pandaria-Playerbots-v1.0.4-win64-database.zip"
    size = 142 << 20
    note = trinitycore_family._release_progress(zip_name, size)
    with caplog.at_level("INFO", logger="yulon.catalog.families.trinitycore"):
        note(1 << 20, size)
        note(16 << 20, size)
        note(size, size)
    said = [
        record.getMessage()
        for record in caplog.records
        if record.name == "yulon.catalog.families.trinitycore"
        and record.getMessage().startswith("Downloading")
    ]
    assert said == [
        f"Downloading {zip_name}: 16 of 142 MB",
        f"Downloading {zip_name}: 142 of 142 MB",
    ]
