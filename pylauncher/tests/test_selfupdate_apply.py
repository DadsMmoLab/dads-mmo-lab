"""The whole sequence, and what every refusal leaves behind (T90 plan 3).

Each step is a seam on `ApplyIO`, so what this file asserts is the ORDER, what
is handed to each step, and — for every way the sequence can stop — that the
running install is byte for byte what it was and nothing is left staged beside
it. The install is hashed before and after, every time.

Nothing here touches the network, and nothing runs a process.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.apply import (
    ApplyIO,
    ReadyToRestart,
    SavedForManualInstall,
    apply_update,
)
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import Cancelled, UpdateError
from yulon.selfupdate.swap import SwapPlan
from yulon.update import CHECKSUMS_NAME, ReleaseAsset, UpdateCheck

TAG = "v0.8.70-Public"
ARTIFACT = f"Yulon-{TAG}-x86_64.tar.gz"
DIGEST = "a" * 64
SIZE = 1234


def _offer(
    *,
    available: bool = True,
    checksums: bool = True,
    assets: tuple[str, ...] = (ARTIFACT, CHECKSUMS_NAME),
) -> UpdateCheck:
    return UpdateCheck(
        current="0.8.66-Public",
        latest=TAG,
        available=available,
        url=f"https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/{TAG}",
        assets=tuple(ReleaseAsset(n, f"https://example.invalid/{n}", SIZE) for n in assets),
        has_checksums=checksums,
    )


def _install(root: Path, *, writable: bool = True) -> Install:
    target = root / "yulon"
    target.mkdir()
    (target / "yulon").write_text("the running build", encoding="utf-8")
    (target / "_internal").mkdir()
    (target / "_internal" / "data").write_text("payload", encoding="utf-8")
    return Install(InstallKind.TARBALL, target, "yulon", writable)


def _hash_tree(root: Path) -> str:
    """Every path and byte under `root` that is not one of this app's work dirs.

    The work directories are checked separately by `_nothing_is_staged`: they
    are Yu'lon's own and exist only while an update is in flight, so folding
    them into "the install is untouched" would make that assertion about the
    update rather than about the player's folder.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.relative_to(root).parts[0] in layout.WORK_NAMES:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class _IO:
    """Every seam, recording the order it was called in. Fails whichever step is named."""

    def __init__(self, *, fail: str | None = None, cancel_after: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail
        self.cancel_after = cancel_after
        self.cancelled = False
        self.urls: list[str] = []
        self.downloaded: list[tuple[str, Path, int]] = []
        self.verified: list[tuple[Path, str]] = []
        self.staged: list[Path] = []
        self.smoked: list[Path] = []
        self.planned: list[tuple[Path, int, Path]] = []
        self.prepared: list[tuple[str, int]] = []

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise UpdateError(f"{name} refused")
        if self.cancel_after == name:
            self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled

    def fetch_text(self, url: str) -> str:
        self._step("fetch_text")
        self.urls.append(url)
        # A line for each artifact this project builds, the way a real
        # `SHA256SUMS` carries every file of the release.
        return "".join(
            f"{DIGEST}  Yulon-{TAG}{suffix}\n"
            for suffix in ("-x86_64.tar.gz", "-x86_64.AppImage", "-windows-x64.zip", "-macos.dmg")
        )

    def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int,
        progress: object,
        cancelled: object,
    ) -> Path:
        self._step("download")
        self.downloaded.append((url, dest, expected_size))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"the new build archive")
        return dest

    def verify(self, path: Path, digest: str) -> None:
        self._step("verify")
        self.verified.append((path, digest))

    def prepare(self, install: Install, version: str, *, pid: int) -> Path:
        """Both marked work dirs, exactly as the real `prepare` makes them.

        A fake that made only the staging one left an unmarked
        `.yulon-download` behind — which is the shape of the defect this
        package exists to avoid, produced by the test's own double.
        """
        self._step("prepare")
        self.prepared.append((version, pid))
        for name in (layout.NEW_NAME, layout.DOWNLOAD_NAME):
            layout.work_dir(install, name).mkdir(parents=True, exist_ok=True)
            layout.write_marker(
                install,
                name,
                layout.Marker(name, "0.8.66-Public", version, pid, layout.now()),
            )
        return layout.work_dir(install, layout.NEW_NAME)

    def stage(self, install: Install, archive: Path) -> Path:
        self._step("stage")
        assert install.target is not None
        staged = layout.work_dir(install, layout.NEW_NAME)
        (staged / install.executable).write_text("the new build", encoding="utf-8")
        self.staged.append(staged)
        return staged

    def entries_to_swap(self, install: Install, staged: Path) -> tuple[str, ...]:
        self._step("entries_to_swap")
        del staged
        return (install.executable, "_internal")

    def smoke_test(self, exe: Path) -> None:
        self._step("smoke_test")
        self.smoked.append(exe)

    def plan_swap(
        self,
        install: Install,
        staged: Path,
        *,
        pid: int,
        script_dir: Path,
        entries: tuple[str, ...] = (),
    ) -> SwapPlan:
        self._step("plan_swap")
        self.planned.append((staged, pid, script_dir))
        self.entries = entries
        del install
        script = script_dir / f"yulon-update-{pid}.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        return SwapPlan(script, ["/bin/sh", str(script)], entries)

    def as_apply_io(self, script_dir: Path) -> ApplyIO:
        return ApplyIO(
            prepare=self.prepare,
            fetch_text=self.fetch_text,
            download=self.download,
            verify=self.verify,
            stage=self.stage,
            smoke_test=self.smoke_test,
            entries_to_swap=self.entries_to_swap,
            plan_swap=self.plan_swap,
            script_dir=lambda: script_dir,
        )


def _apply(
    io: _IO,
    install: Install,
    root: Path,
    *,
    result: UpdateCheck | None = None,
    stages: list[str] | None = None,
) -> ReadyToRestart | SavedForManualInstall:
    downloads = root / "Downloads"
    downloads.mkdir(exist_ok=True)
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    return apply_update(
        result if result is not None else _offer(),
        install,
        downloads_dir=downloads,
        pid=4242,
        progress=lambda _done, _total: None,
        stage_changed=(stages.append if stages is not None else (lambda _s: None)),
        cancelled=io.is_cancelled,
        io=io.as_apply_io(scripts),
    )


def _nothing_is_staged(install: Install) -> None:
    """No working directory of this app's is left beside or inside the install."""
    assert install.target is not None
    for name in layout.WORK_NAMES:
        assert not layout.work_dir(install, name).exists(), f"{name} was left behind"


# -- the happy path ----------------------------------------------------------


def test_the_steps_happen_in_exactly_this_order(tmp_path: Path) -> None:
    io = _IO()
    install = _install(tmp_path)
    stages: list[str] = []

    outcome = _apply(io, install, tmp_path, stages=stages)

    assert io.calls == STEPS
    assert isinstance(outcome, ReadyToRestart)
    assert outcome.version == TAG
    assert outcome.plan.script.exists()
    assert stages[-1] == "Ready"
    assert stages[:4] == ["Checking…", "Downloading…", "Unpacking…", "Testing the new version…"]


def test_the_checksums_are_fetched_from_an_address_the_app_built_itself(tmp_path: Path) -> None:
    """Never the feed's `browser_download_url`, which is remote text.

    The fixture's assets carry `https://example.invalid/…`; what must be asked
    for is `github.com/<this repo>/releases/download/<tag>/SHA256SUMS`.
    """
    io = _IO()
    _apply(io, _install(tmp_path), tmp_path)
    assert io.urls == [
        "https://github.com/DadsMmoLab/dads-mmo-lab/releases/download/" f"{TAG}/{CHECKSUMS_NAME}"
    ]
    assert "example.invalid" not in io.downloaded[0][0]
    assert io.downloaded[0][0].endswith(f"/releases/download/{TAG}/{ARTIFACT}")


def test_the_download_lands_in_its_own_marked_folder_and_not_in_the_staging_one(
    tmp_path: Path,
) -> None:
    """**Never `.yulon-new`** (cold review 2).

    Everything in the staging directory is an entry of the new build by
    definition — that is what `stage.staged_entries()` reads — so an archive
    left there was handed to the helper as part of the program, and a 90 MB
    tarball was installed into the player's folder. It is still a marked
    directory beside it, on the same filesystem, so the unpack is a rename and
    a `.part` from a killed app is inside something this app can prove is its
    own.
    """
    io = _IO()
    install = _install(tmp_path)
    assert install.target is not None
    _apply(io, install, tmp_path)
    _url, dest, size = io.downloaded[0]
    assert dest == layout.work_dir(install, layout.DOWNLOAD_NAME) / ARTIFACT
    assert dest.parent != layout.work_dir(install, layout.NEW_NAME)
    assert size == SIZE, "the size the RELEASE declares is the bound, not a header"
    assert io.verified == [(dest, DIGEST)]
    assert not layout.work_dir(
        install, layout.DOWNLOAD_NAME
    ).exists(), "the archive was still there when the helper was planned"


def test_the_staged_executable_is_what_the_smoke_test_runs(tmp_path: Path) -> None:
    io = _IO()
    install = _install(tmp_path)
    assert install.target is not None
    _apply(io, install, tmp_path)
    assert io.smoked == [layout.work_dir(install, layout.NEW_NAME) / "yulon"]


def test_the_plan_is_made_for_this_process_and_this_staged_tree(tmp_path: Path) -> None:
    io = _IO()
    install = _install(tmp_path)
    assert install.target is not None
    _apply(io, install, tmp_path)
    staged, pid, script_dir = io.planned[0]
    assert staged == layout.work_dir(install, layout.NEW_NAME)
    assert pid == 4242
    assert script_dir == tmp_path / "scripts"
    assert io.entries == ("yulon", "_internal"), "the helper was not told what to replace"


# -- the refusals that never reach the network -------------------------------


@pytest.mark.parametrize(
    ("result", "why"),
    [
        (_offer(available=False), "nothing newer"),
        (_offer(checksums=False), "no checksums"),
        (_offer(assets=(CHECKSUMS_NAME,)), "no artifact for this machine"),
        (_offer(assets=("Yulon-v0.8.70-Public-windows-x64.zip", CHECKSUMS_NAME)), "wrong artifact"),
    ],
)
def test_a_release_this_app_cannot_prove_is_refused_before_anything_is_fetched(
    result: UpdateCheck, why: str, tmp_path: Path
) -> None:
    io = _IO()
    install = _install(tmp_path)
    assert install.target is not None
    before = _hash_tree(install.target)

    with pytest.raises(UpdateError):
        _apply(io, install, tmp_path, result=result)

    assert io.calls == [], f"{why}: the network was reached anyway"
    assert _hash_tree(install.target) == before
    _nothing_is_staged(install)


def test_an_artifact_the_checksum_file_does_not_list_is_refused_before_the_download(
    tmp_path: Path,
) -> None:
    io = _IO()
    io.fetch_text = lambda _url: f"{DIGEST}  something-else.tar.gz\n"  # type: ignore[method-assign]
    install = _install(tmp_path)
    assert install.target is not None
    before = _hash_tree(install.target)

    with pytest.raises(UpdateError, match="does not list"):
        _apply(io, install, tmp_path)

    assert "download" not in io.calls
    assert _hash_tree(install.target) == before
    _nothing_is_staged(install)


# -- every step failing in turn ----------------------------------------------

STEPS = [
    "prepare",
    "fetch_text",
    "download",
    "verify",
    "stage",
    "smoke_test",
    "entries_to_swap",
    "plan_swap",
]


@pytest.mark.parametrize("failing", STEPS)
def test_a_step_that_refuses_stops_the_sequence_and_leaves_the_install_alone(
    failing: str, tmp_path: Path
) -> None:
    io = _IO(fail=failing)
    install = _install(tmp_path)
    assert install.target is not None
    before = _hash_tree(install.target)

    with pytest.raises(UpdateError, match=f"{failing} refused"):
        _apply(io, install, tmp_path)

    assert io.calls == STEPS[: STEPS.index(failing) + 1], "a later step ran anyway"
    assert _hash_tree(install.target) == before, "the running install was changed"
    _nothing_is_staged(install)


@pytest.mark.parametrize("after", ["download", "verify", "stage", "smoke_test"])
def test_cancelling_stops_the_sequence_and_leaves_nothing_behind(
    after: str, tmp_path: Path
) -> None:
    io = _IO(cancel_after=after)
    install = _install(tmp_path)
    assert install.target is not None
    before = _hash_tree(install.target)

    with pytest.raises(Cancelled):
        _apply(io, install, tmp_path)

    assert "plan_swap" not in io.calls
    assert _hash_tree(install.target) == before
    _nothing_is_staged(install)


# -- the installs that download but do not swap ------------------------------


def test_a_read_only_install_saves_the_verified_file_and_never_stages(tmp_path: Path) -> None:
    io = _IO()
    install = _install(tmp_path, writable=False)
    assert install.target is not None
    before = _hash_tree(install.target)

    outcome = _apply(io, install, tmp_path)

    assert isinstance(outcome, SavedForManualInstall)
    assert outcome.path == tmp_path / "Downloads" / ARTIFACT
    assert outcome.path.exists()
    assert outcome.version == TAG
    assert io.calls == ["fetch_text", "download", "verify"], "it went on to stage"
    assert io.verified == [(outcome.path, DIGEST)], "the saved file was not proved"
    assert _hash_tree(install.target) == before


def test_macos_downloads_its_dmg_and_stops_there(tmp_path: Path) -> None:
    io = _IO()
    install = Install(InstallKind.MACOS_APP, None, "", False)
    dmg = f"Yulon-{TAG}-macos.dmg"
    outcome = _apply(io, install, tmp_path, result=_offer(assets=(dmg, CHECKSUMS_NAME)))

    assert isinstance(outcome, SavedForManualInstall)
    assert outcome.path == tmp_path / "Downloads" / dmg
    assert io.calls == ["fetch_text", "download", "verify"]


def test_a_checkout_is_refused_because_it_has_no_artifact_at_all(tmp_path: Path) -> None:
    io = _IO()
    with pytest.raises(UpdateError):
        _apply(io, Install(InstallKind.SOURCE, None, "", False), tmp_path)
    assert io.calls == []


# -- the size the feed declares ----------------------------------------------


@pytest.mark.parametrize("size", [0, -1, 10 * 1024 * 1024 * 1024])
def test_an_artifact_size_the_feed_made_up_is_refused_before_the_download(
    size: int, tmp_path: Path
) -> None:
    """The size comes off the network too, and it is what bounds the read.

    Zero would make every byte "too long"; a negative one would make the read
    bound negative; ten gigabytes is not an artifact this project builds and is
    a disk somebody else chose to fill.
    """
    io = _IO()
    install = _install(tmp_path)
    offer = _offer()
    offer = replace(
        offer,
        assets=tuple(replace(a, size=size) if a.name == ARTIFACT else a for a in offer.assets),
    )
    with pytest.raises(UpdateError, match="size"):
        _apply(io, install, tmp_path, result=offer)
    assert io.calls == []
    _nothing_is_staged(install)
