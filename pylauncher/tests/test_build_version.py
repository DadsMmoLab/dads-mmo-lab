"""A packaged build knows its version from the tag it was built from."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

import stamp_version  # noqa: E402


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("v0.8.70-Public", "0.8.70-Public"),
        ("v0.8.68-fixtest", "0.8.68-fixtest"),
        ("v0.6.59", "0.6.59"),
        ("Yulon", None),
        ("release/0.8.66-fixtest", None),
        ("", None),
    ],
)
def test_only_a_version_tag_is_a_version(ref: str, expected: str | None) -> None:
    assert stamp_version.version_from_ref(ref) == expected


def test_a_tag_writes_the_stamp(tmp_path: Path) -> None:
    target = tmp_path / "_build_version.py"
    assert stamp_version.main(["v0.8.70-Public", "--out", str(target)]) == 0
    namespace: dict[str, object] = {}
    exec(target.read_text(encoding="utf-8"), namespace)
    assert namespace["VERSION"] == "0.8.70-Public"


def test_a_branch_build_writes_nothing_and_still_exits_zero(tmp_path: Path) -> None:
    target = tmp_path / "_build_version.py"
    assert stamp_version.main(["Yulon", "--out", str(target)]) == 0
    assert not target.exists()


def test_the_package_prefers_the_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    import yulon

    fake = types.ModuleType("yulon._build_version")
    fake.VERSION = "9.9.9-Public"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yulon._build_version", fake)
    try:
        assert importlib.reload(yulon).__version__ == "9.9.9-Public"
    finally:
        monkeypatch.delitem(sys.modules, "yulon._build_version")
        importlib.reload(yulon)


def test_a_checkout_has_no_stamp_and_uses_the_written_version() -> None:
    import yulon

    assert not (Path(yulon.__file__).parent / "_build_version.py").exists()
    assert yulon.__version__ == yulon._FALLBACK_VERSION


def test_deriving_a_version_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--derive` is what the release job compares the built bundle against."""
    target = tmp_path / "_build_version.py"
    assert stamp_version.main(["v0.8.70-Public", "--out", str(target), "--derive"]) == 0
    assert capsys.readouterr().out == "0.8.70-Public\n"
    assert not target.exists()

    assert stamp_version.main(["Yulon", "--out", str(target), "--derive"]) == 0
    assert capsys.readouterr().out == "\n", "a branch derives an empty line, not the word None"
    assert not target.exists()


PYLAUNCHER = Path(__file__).resolve().parents[1]
SPEC = (PYLAUNCHER / "build" / "pylauncher.spec").read_text(encoding="utf-8")
SPEC_CODE = "\n".join(line for line in SPEC.splitlines() if not line.lstrip().startswith("#"))
"""The spec with its comments stripped: a pin must read code, not prose."""


def test_the_spec_names_the_stamp_module_where_pyinstaller_will_see_it() -> None:
    """The bundle carries the stamp because the spec NAMES it, not by inference.

    What this can prove: the three files agree on one module name and one path -
    the stamper writes `yulon/_build_version.py`, the spec hides
    `yulon._build_version` when that file is there, and `yulon/__init__.py`
    imports exactly that name. A rename in any one of them fails here.

    WHAT IT CANNOT PROVE, and what shipped wrong because nobody measured it: that
    PyInstaller then puts the module in the bundle. v0.8.69-fixtest was stamped
    on all three runners, and `collect_submodules("yulon")` - which the spec
    relied on - returned an empty list on every one of them, silently, because
    PyInstaller runs a spec with `ROOT` off `sys.path`. Only a real build shows
    that, which is why the release job now asks the built bundle for its version
    and warns when it disagrees with the tag.
    """
    out = stamp_version._DEFAULT_OUT
    relative = out.relative_to(PYLAUNCHER)
    dotted = ".".join(relative.with_suffix("").parts)
    assert dotted == "yulon._build_version"

    assert f'hiddenimports.append("{dotted}")' in SPEC_CODE, (
        "the spec does not name the stamp module; `collect_submodules` alone has "
        "never carried it"
    )
    assert f'os.path.join(ROOT, "{relative.parts[0]}", "{relative.name}")' in SPEC_CODE
    assert "os.path.exists(_stamp)" in SPEC_CODE, "a checkout has no stamp and must still build"

    init = (PYLAUNCHER / "yulon" / "__init__.py").read_text(encoding="utf-8")
    assert f'importlib.import_module("{dotted}")' in init


def test_the_spec_puts_the_package_on_the_path_before_collecting_it() -> None:
    """Measured 2026-09-21: without this, `collect_submodules` returns 0 modules.

    It does not raise and does not warn - not even with `on_error="raise"`, which
    returned an empty list too. The call has been in the spec since packaging
    began and has never contributed anything.
    """
    assert SPEC_CODE.index("sys.path.insert(0, ROOT)") < SPEC_CODE.index(
        'collect_submodules("yulon")'
    )


def test_the_app_can_say_its_version_without_a_window() -> None:
    """`--version` is how CI asks a built bundle what it thinks it is.

    IN A SUBPROCESS, WITH A TIMEOUT, and deliberately so: calling `main()` in
    this process is what this test did first, and when a mutation took the flag
    away the call fell through to the real thing - it configured logging, went
    looking for the docker group and built a window, and the suite hung for ten
    minutes until it was killed by hand. A test of an entry point must not be
    able to start the app it is testing.
    """
    import yulon

    done = subprocess.run(
        [sys.executable, "main.py", "--version"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == yulon.__version__
    assert "PySide6" not in done.stderr
