"""Tests for `yulon.client_names` -- a client's files as the disk spells them (T227).

Real folders on this machine's own (case-sensitive) disk. On the Centurion proof
(yulon-ubuntu2, 2026-10-04) a valid 3.3.5a client was refused because it held
`Data/lichking.mpq` and the check asked for `Data/lichking.MPQ` exactly.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from yulon import client_names


def test_a_lowercase_archive_is_found_under_its_own_name(tmp_path: Path) -> None:
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "lichking.mpq").write_bytes(b"MPQ")

    found = client_names.find(tmp_path, "Data/lichking.MPQ")

    assert found == tmp_path / "Data" / "lichking.mpq"
    assert sorted(p.name for p in (tmp_path / "Data").iterdir()) == ["lichking.mpq"]


def test_every_folder_on_the_way_is_matched_too(tmp_path: Path) -> None:
    (tmp_path / "Data" / "enus").mkdir(parents=True)
    (tmp_path / "Data" / "enus" / "locale-enus.mpq").write_bytes(b"MPQ")

    assert client_names.on_disk(tmp_path, "Data/enUS/locale-enUS.MPQ") == PurePosixPath(
        "Data/enus/locale-enus.mpq"
    )


def test_the_exact_spelling_wins_over_a_case_variant(tmp_path: Path) -> None:
    """Two names that differ only in case are two files here; the one asked for is the one."""
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "lichking.mpq").write_bytes(b"lower")
    (tmp_path / "Data" / "lichking.MPQ").write_bytes(b"canonical")

    assert client_names.find(tmp_path, "Data/lichking.MPQ") == tmp_path / "Data" / "lichking.MPQ"
    assert client_names.find(tmp_path, "Data/lichking.mpq") == tmp_path / "Data" / "lichking.mpq"


def test_what_is_not_there_keeps_the_spelling_it_was_asked_for(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()

    assert client_names.on_disk(tmp_path, "Data/enUS/patch-X.MPQ") == PurePosixPath(
        "data/enUS/patch-X.MPQ"
    )
    assert client_names.find(tmp_path, "Data/enUS/patch-X.MPQ") is None


def test_a_folder_that_cannot_be_listed_is_not_there_and_nothing_raises(tmp_path: Path) -> None:
    assert client_names.find(tmp_path / "gone", "Data/lichking.MPQ") is None
