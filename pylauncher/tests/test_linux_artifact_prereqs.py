"""The FUSE-less Linux route: what `release.yml` ships, and what the README promises about it.

Measured on `yulon-arch` (kernel 7.1.8-arch1-3, neither `fuse2` nor `fuse3`
installed), 2026-09-04 — evidence in `pyplan/gates/7.1-arch/71-arch-appimage.log`.
The release AppImage, sha256 `cb7c1b7e75…`, is refused by its own runtime before
the interpreter exists:

    Error: No suitable fusermount binary found on the $PATH
    Cannot mount AppImage, please check your FUSE setup.
    ... See https://github.com/AppImage/AppImageKit/wiki/FUSE for more information

The same file with `--appimage-extract-and-run` started and reached its own
update check, so the bundle is sound and the blocker is one package outside it.
Nothing in `yulon/` can catch this — the failure happens above our code — so the
only thing that can be guarded is the pair the user is left with: the release job
must keep emitting a Linux artifact that needs no FUSE, and the README must name
it. Those two live in different files with no owner between them, which is the
shape that rots.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = REPO / "pylauncher" / "README.md"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"

_STEP = re.compile(r"^      - name: (.+)$", re.M)
"""A step header in `release.yml`, at the indentation `steps:` entries sit on."""


def _packaging_step(name: str) -> str:
    """The text of one named step of the release job, header excluded.

    Sliced on step headers rather than parsed as YAML because the interesting
    part is a shell body: PyYAML would hand back the same string with the header
    stripped, at the cost of a dependency the test suite does not otherwise need
    here.
    """
    headers = list(_STEP.finditer(RELEASE_WORKFLOW.read_text(encoding="utf-8")))
    assert headers, f"no steps found in {RELEASE_WORKFLOW}; the slicing is broken"
    for index, header in enumerate(headers):
        if header.group(1).strip() == name:
            end = headers[index + 1].start() if index + 1 < len(headers) else None
            return header.string[header.end() : end]
    raise AssertionError(f"{RELEASE_WORKFLOW} has no step named {name!r}")


_ARTIFACT = re.compile(r"Yulon-\$\{YULON_REF\}-x86_64(\.[A-Za-z0-9.]+?)[\"' ]")
"""A Linux release filename as the packaging step spells it, capturing the suffix."""


def linux_release_suffixes() -> set[str]:
    """Every distinct suffix the Linux packaging step writes a release file with."""
    return set(_ARTIFACT.findall(_packaging_step("Package AppImage")))


def test_the_linux_job_ships_a_second_artifact_for_boxes_without_fuse() -> None:
    """One of the two Linux artifacts has to be openable with no FUSE at all.

    The AppImage cannot be that one. A statically linked runtime was tried and
    measured not to fix it (2026-08-25, recorded in the workflow): static linking
    drops the libfuse *library* dependency, and mounting still shells out to the
    `fusermount3` setuid helper. So this asserts the tarball is still being
    produced — it is the thing every remedy below points at, and a deletion of
    that one `tar -czf` line would turn the README into a lie silently.
    """
    assert linux_release_suffixes() == {".AppImage", ".tar.gz"}


def test_the_readme_names_every_linux_artifact_the_release_job_writes() -> None:
    """A user choosing between two files on the Releases page gets no note from GitHub.

    Written after the 2026-09-04 Arch run: the `.tar.gz` had existed since #96
    precisely so a FUSE-less box has something to run, and `pylauncher/README.md`
    — the page that says what works where — did not mention it once. Driven off
    the workflow rather than a list here so a third artifact cannot be added
    without the page acquiring a row for it.
    """
    readme = README.read_text(encoding="utf-8")
    suffixes = linux_release_suffixes()
    assert suffixes, "the scan found no Linux artifacts; this guard has gone vacuous"
    unnamed = sorted(s for s in suffixes if s.lstrip(".") not in readme)
    assert not unnamed, f"the README never names these shipped Linux artifacts: {unnamed}"


def test_the_readme_says_the_sentence_the_appimage_runtime_only_links() -> None:
    """The error a stuck user pastes into a search box has to land on our own page.

    The runtime's own message names no package and no file — it links a wiki. The
    README carries the first line verbatim so that searching it inside the repo,
    or on the web alongside the project name, reaches the two routes out; and it
    names both FUSE packages, because which one a distro is missing decides which
    one to install.
    """
    readme = README.read_text(encoding="utf-8")
    assert "No suitable fusermount binary found on the $PATH" in readme
    for package in ("fuse2", "fuse3"):
        assert package in readme, f"the README does not name the {package} package"
