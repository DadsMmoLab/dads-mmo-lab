"""T842: a build that fails on damaged Docker data or a crashing compiler says so in plain words.

The player's own Docker Desktop tail (29.9.2) ends on an un-prefixed
`failed to solve: failed to extract layer ... crc32 mismatch`. The `#25 ERROR:`
step line above it says the same thing, but like T202 the detector reads only
Docker's own lines: a step line starting `#<n>` is skipped. A compiler that
dies (exit code 139, or `internal compiler error: Segmentation fault`) is the
opposite: those words ARE the step's own output (`#18 110.6 ...`), so that
matcher reads step lines too (with the `#<n> <secs>` prefix stripped).

Both driven through the real engine with only docker doubled, the way
test_build_stall.py drives the T202 tail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import Recorder, install
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError

DAMAGED_TAIL = (
    "#25 naming to docker.io/library/ac-worldserver:0.0.1-centurion done",
    "#25 unpacking docker.io/library/ac-worldserver:0.0.1-centurion (sha256:1ea9abc) done",
    "#25 ERROR: failed to extract layer sha256:1ea9abc: exit status 1: unpigz: skipping: "
    "<stdin>: corrupted -- crc32 mismatch",
    "failed to solve: failed to extract layer sha256:1ea9abc: exit status 1: unpigz: skipping: "
    "<stdin>: corrupted -- crc32 mismatch",
)


def test_damaged_data_matches_the_measured_tail() -> None:
    assert docker.build_data_damaged(DAMAGED_TAIL)


def test_damaged_data_skips_step_lines() -> None:
    """The `#25 ERROR:` step line alone is not Docker's word on its storage (T202's rule)."""
    assert not docker.build_data_damaged(DAMAGED_TAIL[:3])


def test_damaged_data_needs_the_extract_layer_words() -> None:
    """A registry `unexpected EOF` on a pull is the network, not Docker's storage."""
    tail = (
        "#3 [internal] load metadata for docker.io/library/mysql:8.4",
        "failed to solve: failed to copy: httpReadSeeker: failed open: failed to do "
        "request: unexpected EOF",
    )
    assert not docker.build_data_damaged(tail)


def test_damaged_data_is_not_the_lost_builder() -> None:
    from tests.test_build_stall import A7_TAIL, M910Q_TAIL

    assert not docker.build_data_damaged(A7_TAIL)
    assert not docker.build_data_damaged(M910Q_TAIL)
    assert not docker.builder_connection_lost(DAMAGED_TAIL)


COMPILER_TAIL = (
    "#18 110.6 /bin/sh: 1: clang++: not found",
    "#18 110.6 clang++: fatal error: clang frontend command failed with exit code 139 "
    "(use -v to see invocation)",
    '#18 ERROR: process "/bin/sh -c make -j4" did not complete successfully: exit code: 1',
    'failed to solve: process "/bin/sh -c make -j4" did not complete successfully: exit code: 1',
)

GCC_TAIL = (
    "#18 95.2 g++: internal compiler error: Segmentation fault (program cc1plus)",
    '#18 ERROR: process "/bin/sh -c make -j4" did not complete successfully: exit code: 1',
    'failed to solve: process "/bin/sh -c make -j4" did not complete successfully: exit code: 1',
)


def test_compiler_crash_matches_exit_code_139() -> None:
    assert docker.compiler_crashed(COMPILER_TAIL)


def test_compiler_crash_matches_segfault() -> None:
    assert docker.compiler_crashed(GCC_TAIL)


def test_compiler_crash_accepts_plain_cc_too() -> None:
    tail = ("#18 95.2 c++: internal compiler error: Segmentation fault (program cc1plus)",)
    assert docker.compiler_crashed(tail)


def test_compiler_crash_ignores_exit_code_1() -> None:
    """A frontend failure with exit code 1 is a compile error, not a crash."""
    tail = (
        "#18 110.6 clang++: fatal error: clang frontend command failed with exit code 1 "
        "(use -v to see invocation)",
    )
    assert not docker.compiler_crashed(tail)


def test_an_ordinary_compile_error_is_not_a_crash() -> None:
    tail = (
        "#17 12.3 src/Foo.cpp:3:1: error: no member named 'foo' in 'Bar'",
        '#17 ERROR: process "/bin/sh -c make" did not complete successfully: exit code: 2',
        "failed to solve: process did not complete successfully",
    )
    assert not docker.compiler_crashed(tail)


def test_damaged_data_is_said_through_the_engine(tmp_path: Path) -> None:
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, DAMAGED_TAIL))
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "wow")
    said = str(caught.value)
    assert said.startswith("the build failed (exit 1)"), said
    assert "checksum did not match" in said, said
    assert "docker builder prune -a" in said, said
    assert "crc32 mismatch" in said, "Docker's own words stay, for a search"


def test_compiler_crash_is_said_through_the_engine(tmp_path: Path) -> None:
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, COMPILER_TAIL))
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "wow")
    said = str(caught.value)
    assert said.startswith("the build failed (exit 1)"), said
    assert "the compiler itself crashed" in said, said
    assert "exit code 139" in said, "Docker's own words stay, for a search"


def test_the_lost_builder_sentence_wins_over_damaged_data(tmp_path: Path) -> None:
    """Branch order pinned: a tail with both T202's EOF and the damaged layer names the builder."""
    from tests.test_build_stall import A7_TAIL

    tail = (*A7_TAIL[:-1], DAMAGED_TAIL[-1], "")
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, tail))
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "wow")
    said = str(caught.value)
    assert "lost its connection to Docker's builder" in said, said
    assert "checksum did not match" not in said, said


def test_damaged_data_beats_the_generic_tail(tmp_path: Path) -> None:
    from tests.test_build_stall import A7_TAIL

    tail = (*A7_TAIL[:2], *DAMAGED_TAIL)
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, tail))
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "wow")
    assert "checksum did not match" in str(caught.value)


def test_the_build_sentence_constants_say_what_to_do() -> None:
    assert "docker builder prune -a" in native.BUILD_DATA_DAMAGED
    assert "memory" in native.COMPILER_CRASHED
