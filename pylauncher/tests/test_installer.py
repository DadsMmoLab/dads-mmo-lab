"""Tests for `yulon.catalog.installer` after 7.2: options, errors, copy, dispatch.

The bash lineage this module used to drive is gone (`pyplan/phase7-decisions.md`,
"Deleted in 7.2"). What is left is the small surface every engine and the view
share — the options, the error types, `compose_file()`, the platform and cancel
copy — and the one decision `installer_for()` still makes.

`runner.interact()` is kept (contract 7.2) and its two transport tests here run
for real against a tiny bash script that prompts the way the installers did,
with and without a trailing newline. Nothing in this file starts an install.
"""

from __future__ import annotations

import subprocess
from dataclasses import fields
from pathlib import Path

import pytest

from tests.support_bash import bash_available
from yulon import platform, runner
from yulon.catalog import installer as installer_module
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    cancelled_install_message,
    installer_for,
)

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")


# `installer.bash_available()` answered this until 7.2 deleted it with the engine
# that needed it. F.1 copied the probe to `tests/support_bash.py` for exactly this
# moment; the two transport tests below still need a real shell.
needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)

FAKE_INSTALLER = r"""
W='\033[1;37m'; NC='\033[0m'
echo -e "${W}Where should the server files be installed?${NC}"
echo -ne "  ${W}Install path: ${NC}"
read -r user_input
echo "dir=[$user_input]"
echo -e "${W}Ready to begin? (y/n): ${NC}"
read -r answer
echo "answer=[$answer]"
echo -e "${W}Press ENTER to continue...${NC}"
read -r
echo "building..." >&2
exit ${EXIT_CODE:-0}
"""


def _canned_responder(install_path: str) -> object:
    """The three answers `FAKE_INSTALLER` waits on, as a plain `runner.Responder`.

    Spelled inline since 7.2 rather than built by `installer.make_responder()`:
    the rule table went with the engine that owned it, and what these two tests
    are about is the TRANSPORT — that a prompt printed without a trailing
    newline is answered at all — not how an answer was chosen.
    """

    def respond(line: str) -> str | None:
        if "Install path:" in line:
            return install_path
        if "(y/n)" in line:
            return "y"
        if line.lstrip().startswith("Press ENTER"):
            return ""
        return None

    return respond


@needs_bash
def test_interact_answers_prompts_with_and_without_newlines(tmp_path: Path) -> None:
    """Partial-line prompts (`echo -ne`) and full-line prompts both get answered."""
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    lines = list(
        runner.interact(["bash", str(script)], respond=_canned_responder("/srv/wow"))  # type: ignore[arg-type]
    )
    stripped = [runner.strip_ansi(line) for line in lines]
    assert "dir=[/srv/wow]" in stripped  # answered a prompt with no trailing newline
    assert "answer=[y]" in stripped  # answered a whole-line prompt
    assert "building..." in stripped  # stderr is merged


@needs_bash
def test_interact_raises_on_nonzero_exit_after_yielding_output(tmp_path: Path) -> None:
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    got: list[str] = []
    with pytest.raises(subprocess.CalledProcessError):
        for line in runner.interact(
            ["bash", str(script)],
            respond=_canned_responder(""),  # type: ignore[arg-type]
            env={"EXIT_CODE": "3"},
        ):
            got.append(runner.strip_ansi(line))
    assert "dir=[]" in got  # everything printed before the failure was still yielded


# ------------------------------------------------------------------ the deletion

# What the module holds after F.3, so the assertion below is a diff against a
# state rather than a wish. What went: `Installer`, `AskTheUser`,
# `ASK_THE_USER`, `PromptRule`, `PROMPT_RULES`, `make_responder`,
# `bash_available`, `host_package_manager`, `NO_BASH_HELP`, `DEFAULT_TERM`,
# `_ERROR_TAIL_LINES` and `InstallOptions.reinstall`, with the imports only they
# needed.
MODULE_SURFACE_AFTER_7_2 = {
    # this module's own names, private ones included: `_ERROR_TAIL_LINES`
    # was private and is on the deleted list, so a `_`-prefixed filter would
    # have let it back.
    "_PLATFORM_NAMES",
    "COMPOSE_FILENAMES",
    "DEFAULT_INSTALLERS_ROOT",
    "DockerNeedsReLoginError",
    "DockerUnavailableError",
    "InstallEngine",
    "InstallOptions",
    "InstallerError",
    "SUDO_PROMPT_PREFIX",
    "UnsupportedPlatformError",
    "cancelled_install_message",
    "compose_file",
    "docker_unavailable",
    "installer_for",
    "logger",
    "platform_names",
    "provision_lines",
    "unsupported_platform_message",
    # what it imports, which is the other half of the evidence: the bash engine
    # needed `os`, `re`, `secrets`, `shutil`, `subprocess`, `sys`, `deque` and
    # `Mapping`, and none of them may come back unnoticed either.
    "annotations",
    "threading",
    "Callable",
    "Iterable",
    "Iterator",
    "dataclass",
    "Path",
    "Protocol",
    "docker",
    "platform",
    "resources",
    "runner",
    "CatalogEntry",
    "get_logger",
}


def test_the_script_lineage_is_gone() -> None:
    """The module's whole surface, so a deleted symbol cannot return renamed.

    A list of forbidden NAMES would pass the day one of them came back as
    `ScriptInstaller` or `_PROMPT_RULES`. This enumerates instead: every
    attribute the module has, against every one it is supposed to have. The
    imports are in the set on purpose — `subprocess`, `secrets`, `re` and `os`
    were there for the engine that ran the scripts, and a diff that ignored
    them would let the machinery back in one import at a time.

    Adding something to this module is meant to fail here once; the fix is to
    add the name above, deliberately.
    """
    assert {name for name in vars(installer_module) if not name.startswith("__")} == (
        MODULE_SURFACE_AFTER_7_2
    )
    assert {f.name for f in fields(InstallOptions)} == {"server_dir", "client_dir"}


# ------------------------------------------------------------------ the dispatch


def test_installer_for_does_not_consult_the_platform_but_does_pass_it_on(
    tmp_path: Path,
) -> None:
    """One rule, read from `install.native.family`; the OS decides nothing here.

    Which class each shipped entry gets is `test_spine.py`'s
    `test_every_shipped_native_entry_reaches_the_class_its_family_id_names`,
    enumerated over the catalog. What is asserted here is the other half, which
    that test cannot see because it passes one platform: the answer does not
    move when the platform does, and `platform_id` is nevertheless WIRED —
    reaching the engine's seams, where the 6.1 refusal reads it. A factory that
    accepted the argument and dropped it would pass an isinstance check and
    refuse nothing.
    """
    engines = {
        here: installer_for(WOTLK, platform_id=lambda h=here: h)  # type: ignore[misc]
        for here in ("linux", "macos", "windows")
    }
    assert {type(engine) for engine in engines.values()} == {AzerothCoreInstaller}

    # `_preflight_lines()` tests `install.supports(here)` before anything else,
    # so the value arrives without a daemon being asked for anything.
    off_platform = installer_for(WOTLK, platform_id=lambda: "haiku")
    with pytest.raises(UnsupportedPlatformError, match="cannot be installed on haiku"):
        off_platform.preflight(InstallOptions(server_dir=tmp_path / "srv"))


def test_installer_for_refuses_an_entry_with_no_native_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `native` means no family to name — a catalog-authoring error, raised AT ONCE.

    The entry is a copy of a shipped one with exactly `native` removed, so the
    only rule it breaks is the one under test: it keeps its platforms, its
    script field and everything else that could otherwise raise first.

    `family_for()` refuses the same state, in a sentence that shares the phrase
    "`install.native` section" with this one — so a test that matched only that
    phrase passed with this guard DELETED, `family_for()` having raised in its
    place (measured: mutation m1, 2026-09-02). Two things separate them: the
    wording that is this function's alone, and the fact that `family_for()` is
    never reached, which is what "raised here rather than deferred" means.
    """
    from yulon.catalog import families

    reached: list[object] = []
    monkeypatch.setattr(
        families, "family_for", lambda entry: reached.append(entry)  # type: ignore[arg-type]
    )

    no_native = TBC.model_copy(update={"install": TBC.install.model_copy(update={"native": None})})
    assert no_native.install.native is None
    with pytest.raises(InstallerError, match="cannot be installed yet") as caught:
        installer_for(no_native)
    assert "no `install.native` section" in str(caught.value)
    assert reached == [], "the refusal was deferred to family_for() instead of raised here"


def test_installer_for_hands_the_probe_and_reset_to_the_engine() -> None:
    """`catalog/` may not import a controller package, so the caller supplies both.

    Identity, not truthiness: a factory that built its own probe would also
    produce an engine with a callable in that slot.
    """

    def probe() -> object:
        return "absent"

    def reset(*_a: object, **_k: object) -> None:
        return None

    engine = installer_for(WOTLK, import_probe=probe, reset_unfinished=reset)  # type: ignore[arg-type]
    assert isinstance(engine, AzerothCoreInstaller)
    assert engine._probe is probe  # noqa: SLF001 - no public getter, on purpose
    assert engine._reset is reset  # noqa: SLF001


# ------------------------------------------------------------------ the copy


def test_the_cancel_copy_tells_the_truth_about_resuming(tmp_path: Path) -> None:
    """Stop leaves a folder the engine can carry on from; the copy must say so.

    Until 7.2 both halves had to warn that Install would NOT resume, because
    the bash installer found the folder, offered to wipe it, the app declined,
    and it exited 0 having done nothing. The engine records every finished
    stage in `native.STATE_FILE` and re-checks the disk before skipping one, so
    the honest advice is now the opposite. "Use existing…" keeps its half
    because it answers a different question — adopting a build that had in fact
    finished — which is why it is still absent from the other.
    """
    nothing_there = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" not in nothing_there
    assert "carry on" in nothing_there
    assert "clean start" in nothing_there
    assert "will not pick up" not in nothing_there and "nothing to resume" not in nothing_there

    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    source_there = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" in source_there
    assert str(tmp_path / native.STATE_FILE) in source_there
    assert "carries on" in source_there
    assert "will NOT carry on" not in source_there


def test_the_cancel_copy_reads_the_folder_the_way_compose_does(tmp_path: Path) -> None:
    """`compose.yml` is an install too, and it is what two of the four games write.

    The advice differs on whether there is source on disk, so a check stricter
    than Compose's own sends a finished TBC or Vanilla install down the "there
    is nothing there" branch — the same class of bug `COMPOSE_FILENAMES` exists
    for. Asserted by naming the branch's own sentence, not by string equality
    with the other call, so the two halves may be reworded independently.
    """
    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert "Use existing" in cancelled_install_message("WoW TBC", tmp_path)


def test_compose_file_answers_in_composes_own_order(tmp_path: Path) -> None:
    """The order is Compose's, and it is not the obvious one.

    Measured against Docker Compose v5.3.1: with all four names present it
    reports its own search list - compose.yaml, compose.yml, docker-compose.yml,
    docker-compose.yaml - so `.yml` beats `.yaml` in the second pair and loses in
    the first. An earlier version of COMPOSE_FILENAMES had that pair swapped, and
    the test that claimed to check the order never exercised it.
    """
    assert installer_module.compose_file(tmp_path) is None

    (tmp_path / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    assert installer_module.compose_file(tmp_path) == tmp_path / "docker-compose.yaml"

    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert installer_module.compose_file(tmp_path) == tmp_path / "docker-compose.yml"

    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert installer_module.compose_file(tmp_path) == tmp_path / "compose.yml"

    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    assert installer_module.compose_file(tmp_path) == tmp_path / "compose.yaml"


def test_compose_file_ignores_a_directory_of_that_name(tmp_path: Path) -> None:
    """`is_file()`, not `exists()` - a directory called `compose.yml` is not an install."""
    (tmp_path / "compose.yml").mkdir()
    assert installer_module.compose_file(tmp_path) is None


def test_unsupported_message_names_the_platform_and_the_requirement() -> None:
    message = installer_module.unsupported_platform_message(TBC, "windows")
    assert "WoW TBC" in message and "Windows" in message and "Linux" in message
    assert "Nothing was started" in message


def test_platform_names_reads_as_english() -> None:
    """6.2 widened `platforms` to two entries — the copy must not become "linux, macos"."""
    assert installer_module.platform_names(["linux"]) == "Linux"
    assert installer_module.platform_names(["linux", "macos"]) == "Linux or macOS"
    assert (
        installer_module.platform_names(["linux", "macos", "windows"]) == "Linux, macOS or Windows"
    )
    assert installer_module.platform_names([]) == "another platform"
    assert installer_module.platform_names(["haiku"]) == "haiku"  # unknown id passes through


# --------------------------------------------------------- one docker probe, not two
# `installer.docker_available()` was `runner.run(["docker", "info"])` — a second
# copy of `platform.docker_ready()` (style-guide §4) that never learned about
# `docker_programs()`. So a preflight could refuse an install with "Docker
# isn't available" on the exact Windows box where `ensure_docker()` had, seconds
# earlier, proved that it was. The engine that held the duplicate went in 7.2;
# `native.Seams.docker_ready` is now the only gate, and it is the real function.


def test_the_install_gate_and_the_provisioner_ask_the_same_question() -> None:
    """One function, so the two can no longer disagree."""
    assert native.Seams().docker_ready is platform.docker_ready


def test_the_gate_sees_a_docker_that_is_only_on_the_registry_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of collapsing them: the gate tries the off-PATH exe too.

    Modelled as the real Windows failure — the plain name cannot be started at
    all, only the absolute path answers. Called as `platform.docker_ready`
    directly since 7.2: it used to be read off a constructor default, and the
    class it was read off is gone.
    """
    exe = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_windows_docker_programs", lambda: (exe,))
    tried: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        tried.append(argv[0])
        if argv[0] == "docker":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    assert platform.docker_ready() is True
    assert tried == ["docker", exe]
