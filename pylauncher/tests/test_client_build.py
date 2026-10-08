"""Which build a client's Wow.exe is, read from its PE version resource (T576).

A WotLK realm accepts a login from any build its `build_info` lists, and the world
server then drops every one that is not 3.3.5a (12340): the player sees the password
accepted and a disconnect. `yulon.client_build` reads the build from the exe so Yu'lon
says so before the game starts. The fixtures here are minimal PE files made on the
spot, with the version resource where Windows tools put it.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from tests.pe_fixture import RSRC_RAW
from tests.pe_fixture import exe as _exe
from tests.pe_fixture import pe as _pe
from tests.pe_fixture import resource_section as _resource_section
from tests.pe_fixture import version_info as _version_info
from tests.pe_fixture import versioned as _versioned
from yulon import client_build


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    client_build.forget_cached_builds()


def test_a_stock_client_reads_as_3_3_5_12340(tmp_path: Path) -> None:
    version = client_build.read_version(_versioned(tmp_path, 3, 3, 5, 12340))

    assert version == client_build.ExeVersion(3, 3, 5, 12340)
    assert str(version) == "3.3.5 (12340)"


def test_an_older_wotlk_client_reads_its_own_build(tmp_path: Path) -> None:
    version = client_build.read_version(_versioned(tmp_path, 3, 3, 3, 11723))

    assert version is not None and version.build == 11723
    assert str(version) == "3.3.3 (11723)"


def test_a_64_bit_optional_header_is_read_too(tmp_path: Path) -> None:
    version = client_build.read_version(_versioned(tmp_path, 3, 3, 5, 12340, pe32_plus=True))

    assert version is not None and version.build == 12340


def test_an_exe_with_no_version_resource_has_no_version(tmp_path: Path) -> None:
    assert client_build.read_version(_exe(tmp_path, "Wow.exe", _pe(None))) is None


def test_a_resource_that_is_not_a_version_has_no_version(tmp_path: Path) -> None:
    data = _pe(_resource_section(_version_info(3, 3, 3, 11723), rtype=3))

    assert client_build.read_version(_exe(tmp_path, "Wow.exe", data)) is None


@pytest.mark.parametrize(
    "data",
    [b"", b"MZ", b"not an exe at all" * 50, _pe(None)[:0x90], _pe(_resource_section(b"x"))[:-3]],
)
def test_a_file_that_is_not_a_readable_pe_has_no_version(tmp_path: Path, data: bytes) -> None:
    assert client_build.read_version(_exe(tmp_path, "Wow.exe", data)) is None


def test_a_missing_exe_has_no_version(tmp_path: Path) -> None:
    assert client_build.read_version(tmp_path / "Wow.exe") is None


def test_a_version_block_without_the_fixed_info_signature_has_no_version(tmp_path: Path) -> None:
    block = bytearray(_version_info(3, 3, 3, 11723))
    at = block.index(struct.pack("<I", 0xFEEF04BD))
    block[at : at + 4] = b"\0\0\0\0"
    data = _pe(_resource_section(bytes(block)))

    assert client_build.read_version(_exe(tmp_path, "Wow.exe", data)) is None


# --- the refusal -------------------------------------------------------------------------


def test_an_11723_client_is_refused_naming_both_builds(tmp_path: Path) -> None:
    exe = _versioned(tmp_path, 3, 3, 3, 11723)

    said = client_build.refusal(exe, version="3.3.5a", build=12340)

    assert said is not None
    assert "3.3.3 (11723)" in said
    assert "3.3.5a" in said and "12340" in said


def test_a_12340_client_is_accepted(tmp_path: Path) -> None:
    exe = _versioned(tmp_path, 3, 3, 5, 12340)

    assert client_build.refusal(exe, version="3.3.5a", build=12340) is None


def test_an_exe_without_a_version_resource_is_accepted_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    exe = _exe(tmp_path, "Wow.exe", _pe(None))

    with caplog.at_level("INFO"):
        said = client_build.refusal(exe, version="3.3.5a", build=12340)

    assert said is None
    assert any("no version resource" in r.getMessage() for r in caplog.records)


def test_a_server_that_names_no_build_asks_nothing_of_the_exe(tmp_path: Path) -> None:
    exe = _versioned(tmp_path, 3, 3, 3, 11723)

    assert client_build.refusal(exe, version="1.12.1", build=None) is None


def test_a_client_with_no_exe_yet_is_not_refused_here(tmp_path: Path) -> None:
    """The folder checks say a missing Wow.exe; this one only judges a build it can read."""
    assert client_build.refusal(tmp_path / "Wow.exe", version="3.3.5a", build=12340) is None


# --- the cache, so Play stays fast -------------------------------------------------------


def test_the_same_exe_is_parsed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _versioned(tmp_path, 3, 3, 3, 11723)
    parsed: list[Path] = []
    real = client_build._parse
    monkeypatch.setattr(client_build, "_parse", lambda path: parsed.append(path) or real(path))

    for _ in range(3):
        assert client_build.refusal(exe, version="3.3.5a", build=12340) is not None

    assert parsed == [exe]


def test_a_replaced_exe_is_read_again(tmp_path: Path) -> None:
    exe = _versioned(tmp_path, 3, 3, 3, 11723)
    assert client_build.refusal(exe, version="3.3.5a", build=12340) is not None

    exe.write_bytes(_pe(_resource_section(_version_info(3, 3, 5, 12340))) + b"\0")

    assert client_build.refusal(exe, version="3.3.5a", build=12340) is None


def test_the_build_asked_for_is_the_catalogs_and_not_12340(tmp_path: Path) -> None:
    exe = _versioned(tmp_path, 3, 3, 5, 12340)

    said = client_build.refusal(exe, version="2.4.3", build=8606)

    assert said is not None
    assert "3.3.5 (12340)" in said and "2.4.3" in said and "8606" in said


# --- a resource that only looks like a version (hostile or odd files never invent a build) ---


def test_bytes_that_merely_contain_the_signature_are_not_a_version(tmp_path: Path) -> None:
    """No VS_VERSIONINFO header around the fixed info: not a version, so nothing is refused."""
    real = _version_info(3, 3, 3, 11723)
    fake = b"\x01\x02\x03\x04" * 2 + real[40:] + b"\0" * 40
    data = _pe(_resource_section(fake))

    exe = _exe(tmp_path, "Wow.exe", data)

    assert client_build.read_version(exe) is None
    assert client_build.refusal(exe, version="3.3.5a", build=12340) is None


def test_a_version_block_outside_the_resource_directory_is_not_read(tmp_path: Path) -> None:
    """The data entry points into `.text`, where a perfect block sits: not the resource section."""
    image = bytearray(_pe(_resource_section(_version_info(3, 3, 5, 12340))))
    block = _version_info(3, 3, 3, 11723)
    image[0x200 : 0x200 + len(block)] = block
    entry = RSRC_RAW + 0x48
    struct.pack_into("<I", image, entry, 0x1000)

    assert client_build.read_version(_exe(tmp_path, "Wow.exe", bytes(image))) is None


def test_a_directory_offset_past_the_resource_directory_is_not_followed(tmp_path: Path) -> None:
    image = bytearray(_pe(_resource_section(_version_info(3, 3, 3, 11723))))
    struct.pack_into("<I", image, RSRC_RAW + 0x14, 0x80000000 | 0x7000)

    assert client_build.read_version(_exe(tmp_path, "Wow.exe", bytes(image))) is None
