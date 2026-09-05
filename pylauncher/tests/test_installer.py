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

import ast
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path

import pytest

from tests.support_bash import bash_available

# The machine double the family tests drive. Imported here so the two engine
# refusals this file pins can be reached the way an install reaches them —
# through `run()` — rather than by calling the method that raises them.
from tests.support_native import Recorder
from tests.support_native import install as run_install
from yulon import platform, runner
from yulon.catalog import composegen, native
from yulon.catalog import installer as installer_module
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


def _expiring_cancel(after: float = 20.0) -> threading.Event:
    """A deadline for the two tests below, so a wedged transport fails instead of hanging.

    `runner.interact()` has no wall-clock bound of its own and this repo has no
    `pytest-timeout`, so a child that stops answering blocks the reader loop
    forever. A review mutation of the responder arrived as a permanent block
    rather than a red test (m4, 2026-09-02), and a test that wedges CI is worse
    than no test: the run never reports, so nobody learns anything from it.
    `cancel` is the bound `interact()` does have — it is checked every loop turn
    and terminates the child — which is why every `interact()` test in
    `test_prompt.py` already passes one.

    Generous on purpose. These two drive a real bash script that answers three
    prompts and exits, so 20 s is nowhere near the honest running time; the
    deadline exists to end a hang, never to measure one, and a machine slow
    enough to trip it would have been reported by the whole suite first.
    """
    event = threading.Event()
    timer = threading.Timer(after, event.set)
    timer.daemon = True
    timer.start()
    return event


@needs_bash
def test_interact_answers_prompts_with_and_without_newlines(tmp_path: Path) -> None:
    """Partial-line prompts (`echo -ne`) and full-line prompts both get answered."""
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    lines = list(
        runner.interact(
            ["bash", str(script)],
            respond=_canned_responder("/srv/wow"),  # type: ignore[arg-type]
            cancel=_expiring_cancel(),
        )
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
            cancel=_expiring_cancel(),
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
    "generated_compose_files",
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
    "composegen",
    "docker",
    "platform",
    "resources",
    "runner",
    "CatalogEntry",
    "get_logger",
}


def test_the_script_lineage_is_gone() -> None:
    """This module's namespace, so a deleted symbol cannot return renamed.

    A list of forbidden NAMES would pass the day one of them came back as
    `ScriptInstaller` or `_PROMPT_RULES`. This enumerates instead: every
    attribute the module namespace has, against every one it is supposed to
    have. The imports are in the set on purpose — `subprocess`, `secrets`, `re`
    and `os` were there for the engine that ran the scripts, and a diff that
    ignored them would let the machinery back in one import at a time.

    `vars(module)` is the NAMESPACE and nothing deeper, which is narrower than
    it reads: a review put `PROMPT_RULES` and `make_responder` back as members
    of `InstallOptions` and this assertion stayed green, because a class's own
    attributes are not the module's (mutation m7, reproduced 2026-09-02: the
    rule table and a working `make_responder` were back, and this test passed).
    What closes that is
    `test_no_module_under_yulon_binds_a_script_lineage_name` below, which reads
    every binding in the package rather than one namespace's top layer.

    Adding something to this module is meant to fail here once; the fix is to
    add the name above, deliberately.
    """
    assert {name for name in vars(installer_module) if not name.startswith("__")} == (
        MODULE_SURFACE_AFTER_7_2
    )
    assert {f.name for f in fields(InstallOptions)} == {"server_dir", "client_dir"}


# The names the bash engine owned, every one of them deleted by F.3 or by the
# commits it depended on. Not a wish list: every one of them was measured absent
# from every module under `yulon/` on the day this test was written (2026-09-02).
SCRIPT_LINEAGE_NAMES = frozenset(
    {
        "Installer",
        "AskTheUser",
        "ASK_THE_USER",
        "PromptRule",
        "PROMPT_RULES",
        "make_responder",
        "host_package_manager",
        "bash_available",
        "NO_BASH_HELP",
        "DEFAULT_TERM",
        "_ERROR_TAIL_LINES",
    }
)


def _bindings(tree: ast.Module) -> Iterator[tuple[str, int]]:
    """Every name this file BINDS, at any depth, with the line that binds it.

    Bindings rather than occurrences, so a docstring or an error message that
    happens to spell one of these is not a finding — and so the resurrection
    that matters is caught wherever it is written: a def or a class at any
    nesting, a plain or annotated assignment (module scope, class body, or
    inside a function), an attribute assigned onto an existing object, a
    parameter, and an import alias.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield node.name, node.lineno
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id, node.lineno
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            yield node.attr, node.lineno
        elif isinstance(node, ast.arg):
            yield node.arg, node.lineno
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                yield alias.asname or alias.name.split(".")[0], node.lineno


def test_no_module_under_yulon_binds_a_script_lineage_name() -> None:
    """The claim the namespace diff above only looks like it makes.

    The module-surface test watches one dict. This walks the whole package and
    every binding in it, which is where m7 put the rule table back: as a class
    attribute of `InstallOptions`, in the very module the surface test guards,
    and green. Reading files rather than importing them is deliberate — an
    import gives back a namespace, and a namespace is exactly the layer that
    missed it.

    The positive control matters more than the assertion. A walk that found no
    files, or a `_bindings()` that yielded nothing, would pass this silently
    and would be indistinguishable from a clean tree, so it is asked for a name
    it must find first.
    """
    package = Path(installer_module.__file__ or "").parents[1]
    bound: dict[str, list[str]] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_bytes().decode("utf-8"), filename=str(path))
        for name, lineno in _bindings(tree):
            bound.setdefault(name, []).append(f"{path.relative_to(package).as_posix()}:{lineno}")

    assert package.name == "yulon", f"the walk started somewhere else: {package}"
    assert "StagedInstaller" in bound, "the walk read no bindings, so its silence proves nothing"
    assert "InstallOptions" in bound, "the walk never reached the module the surface test guards"

    resurrected = {name: where for name, where in bound.items() if name in SCRIPT_LINEAGE_NAMES}
    assert resurrected == {}


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

    class FamilyForWasReached(Exception):
        """Named so a kill here reads as what it is, rather than as a stray TypeError."""

    def _record(entry: object) -> None:
        reached.append(entry)
        raise FamilyForWasReached(entry)

    reached: list[object] = []
    monkeypatch.setattr(families, "family_for", _record)

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
    """Three folders, three different things Install does next, and the copy says which.

    Until 7.2 both halves had to warn that Install would NOT resume, because
    the bash installer found the folder, offered to wipe it, the app declined,
    and it exited 0 having done nothing. 7.2's engine records every finished
    stage in `native.STATE_FILE` and re-checks the disk before skipping one --
    measured on yulon-ubuntu 2026-09-05, "Using /home/pk/gate72-cycle2
    (resuming)" followed by "Already finished: clone-core, clone-modules,
    generate-compose"
    (`pyplan/gates/7.2-ubuntu-2026-09-05/cycle2-pressB.log:26`) -- so the copy
    promised the resume from then on.

    It promised it on every folder, and that is what this now pins. The record
    is what buys the resume, and a folder can hold source without holding one:
    `native.STATE_FILE` is written before stage one, and `git.py`'s clone
    `shutil.rmtree`s the destination it is about to clone into, so a Stop
    during `clone-core` leaves the checkout and takes the record with it. On
    that folder the engine refuses the next press rather than carrying on
    (`test_the_engine_refuses_the_folder_a_cancelled_clone_leaves`), so the
    copy must not send the user back to the Install button.
    """
    nothing_there = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" not in nothing_there
    assert "start over" in nothing_there
    assert "carries on" not in nothing_there and "refuse" not in nothing_there
    assert "will not pick up" not in nothing_there and "nothing to resume" not in nothing_there

    (tmp_path / "src").mkdir()
    no_record = cancelled_install_message("WoW WotLK", tmp_path)
    assert "the app will refuse it" in no_record
    assert f"Delete {tmp_path}" in no_record
    assert "carries on" not in no_record, "the copy promised a resume the engine refuses"

    native.write_state(tmp_path, native.InstallState(game_id="wow-wotlk", install_id="cafef00d"))
    resumable = cancelled_install_message("WoW WotLK", tmp_path)
    assert str(tmp_path / native.STATE_FILE) in resumable
    assert "Press Install again" in resumable and "carries on" in resumable
    assert "refuse" not in resumable and "will NOT carry on" not in resumable
    assert "If it had not" not in resumable, "a conditional with no sentence to refer back to"

    # The one folder that gets both halves, and they have to read as one choice.
    for name in composegen.COMPOSE_FILES:
        (tmp_path / name).write_text(f"{composegen.GENERATED_MARKER}\n", encoding="utf-8")
    both = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" in both
    assert "If it had not, press Install again" in both


def test_the_cancel_copy_does_not_read_upstreams_compose_file_as_its_own(
    tmp_path: Path,
) -> None:
    """The clone brings a `docker-compose.yml` down; it is not evidence of a build.

    This test used to be `..._reads_the_folder_the_way_compose_does` and
    asserted the opposite: that the split answers on `compose_file()`, so on
    any of the four names Compose itself accepts. That reading was measured
    wrong on yulon-ubuntu 2026-09-05. A real WotLK install was started from the
    tile's Install and stopped 20 s into `clone-core`; the folder it left held
    a 2.2 GB checkout whose only compose file was upstream's own -- git-tracked
    and unmodified, first line `# docker-compose.yml for AzerothCore.` -- and
    the modal told the user the source was there and to press "Use existing..."
    (`pyplan/gates/7.2-ubuntu-2026-09-05/widget-cancel.log`, with
    `widget-cancel-folder-after.txt` for the folder). Pressing "Use existing..."
    there adopts a folder holding no server, because `attach_existing()` asks
    that same `compose_file()` question.

    `COMPOSE_FILENAMES` is not wrong; it answers a different question. It
    exists so a FINISHED pre-7.2 install called `compose.yml` stays adoptable,
    and this message is about a folder this run has just written into, where
    nothing is called that. What this split needs is whether the engine's own
    compose stage ran, and the engine marks every file it writes.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "# docker-compose.yml for AzerothCore.\nservices: {}\n", encoding="utf-8"
    )
    upstreams = cancelled_install_message("WoW WotLK", tmp_path)
    assert "nothing is lost" not in upstreams, "upstream's own compose file was read as ours"
    assert "this app did not write" in upstreams, (
        "the copy said nothing at all about the compose file the user can see in the folder: "
        + upstreams
    )

    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert "nothing is lost" not in cancelled_install_message("WoW WotLK", tmp_path)

    (tmp_path / composegen.BASE_FILE).write_text(
        f"{composegen.GENERATED_MARKER}\nservices: {{}}\n", encoding="utf-8"
    )
    ours = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" in ours and "nothing is lost" in ours
    assert composegen.BASE_FILE in ours, "the copy names no file the user can go and look for"


def test_the_cancel_copy_never_sends_you_to_delete_a_folder_the_app_would_adopt(
    tmp_path: Path,
) -> None:
    """The one sentence in this modal that can destroy a server, on the folder that earns it.

    `generated_compose_files()` closed the half that adopted a folder holding
    nothing built. It opened the opposite one, and this is the folder that
    falls into it: `.git`, `src/`, and a `docker-compose.yml` carrying no
    `GENERATED_MARKER` — a bash-era install, a compose file somebody wrote by
    hand, anything at all from before 7.2, since nothing before 7.2 marked what
    it wrote. Rendered from the real function on 2026-09-05: no "Use existing…"
    half at all, and "Do not press Install again on this folder … Delete
    <dir>".

    `attach_existing()` would have taken that folder. It gates on
    `compose_file()`, which answers on any of the four names Compose itself
    accepts and asks nothing about who wrote the file — so the app was telling
    someone to delete a server it can adopt and manage from a tab. That
    adoptability is asserted here through `compose_file()` itself rather than
    described, because it is the entire reason the sentence is wrong.

    The copy cannot tell this folder from the one the previous finding was
    about: a cancelled `clone-core` leaves `.git`, `src/` and upstream's own
    unmarked `docker-compose.yml` too, and no filesystem read separates them.
    So it says both readings and lets the user look — which is the rule the
    rest of this message already follows — and the delete it names is
    conditional on the user's own answer, never an instruction.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert (
        installer_module.compose_file(tmp_path) is not None
    ), "the premise is gone: this is no longer a folder `attach_existing()` would take"

    note = cancelled_install_message("WoW WotLK", tmp_path)
    assert f"Delete {tmp_path}" not in note, (
        'the app told the user to delete a folder its own "Use existing…" would adopt: ' + note
    )
    assert "Use existing" in note, (
        "the folder `attach_existing()` accepts got no offer to attach it: " + note
    )
    assert "there is no server behind it" in note, (
        "the other reading — that this is the compose file the clone brought down — is gone, so "
        "the copy now pushes a user at a tab for a server that does not exist: " + note
    )


def test_the_refusal_gives_the_reason_the_engine_actually_refuses_on(tmp_path: Path) -> None:
    """Two folders, two different checks, and the copy was giving both of them the git one.

    The refusal used to explain itself with "the app cannot tell its own
    half-finished download from a checkout you made yourself, so it stops
    rather than run `git fetch` and `git reset --hard` over your work". That is
    `refuse_unowned_checkout()`'s reason, and it is true of a folder holding a
    `.git`. The branch fires on any leftovers at all, so a folder holding only
    upstream's `docker-compose.yml` and no checkout got the same sentence:
    there is no checkout in it, nothing will be fetched, and nothing will be
    reset. What stops that folder is `_claim_folder()`, one step earlier and on
    a different question — "is not empty and was not created by this app" —
    and preflight raises it before Docker is mentioned.

    Both are pure filesystem reads, which is why the copy can be right about
    which one it is; the engine is driven for both shapes in
    `test_the_engine_refuses_the_folder_a_cancelled_clone_leaves`.
    """
    plain = tmp_path / "plain"
    (plain / "src").mkdir(parents=True)
    note = cancelled_install_message("WoW WotLK", plain)
    assert "the app will refuse it" in note
    assert "git fetch" not in note and "git reset" not in note, (
        "a folder with no checkout in it was told about a fetch and a reset that cannot happen: "
        + note
    )
    assert "checkout" not in note, (
        "the stated cause is git-specific on a folder that holds no git checkout: " + note
    )
    assert "was not created by this app" in note, (
        "the refusal the engine actually raises for this folder is not the one the copy names: "
        + note
    )

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src").mkdir()
    git_note = cancelled_install_message("WoW WotLK", checkout)
    assert "git fetch" in git_note and "git reset --hard" in git_note, git_note
    assert "no record here of an install this app made" in git_note, git_note


def test_the_engine_refuses_the_folder_a_cancelled_clone_leaves(tmp_path: Path) -> None:
    """What "press Install again" actually does on a checkout with no record of ours.

    The copy's promise is worth exactly what the engine does, so the engine is
    asked here rather than described. Both refusals are pure filesystem reads
    and neither needs a daemon, which is why the promise can be pinned in a
    unit test at all.

    **Driven through `run()`, not by calling the refusal.** Until 2026-09-05
    the `.git` half of this test called `engine.refuse_unowned_checkout()`
    directly, which proves the function and says nothing about whether an
    install reaches it — the failure shape that let six reviewers approve a fix
    on a path nothing ran. Both halves now go in at a call site: `run()` for
    the checkout, `preflight()` for the folder with no `.git`.

    Driven for real first: `python -m yulon.install_wiring wow-wotlk
    --server-dir /home/pk/gate72-cancel-install` against the folder the
    cancelled widget run left, yulon-ubuntu 2026-09-05, exited 1 with "is
    already a git checkout of ... and there is no record here of an install
    this app made"
    (`pyplan/gates/7.2-ubuntu-2026-09-05/cycle2-pressA2-refused-existing-checkout.log`).
    """
    url = "https://github.com/mod-playerbots/azerothcore-wotlk.git"
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src").mkdir()
    rec = Recorder()
    with pytest.raises(InstallerError) as caught:
        run_install(rec, checkout, remote_url=lambda _dest: url)
    assert "no record here of an install this app made" in str(caught.value), str(caught.value)

    # ...and the same shape without the `.git`, which never reaches a stage:
    # `_claim_folder()` refuses it before preflight says a word about Docker.
    # The probe and reset are what `install_wiring` passes; an engine built
    # without them refuses in preflight before it looks at the folder at all.
    engine = installer_for(
        WOTLK,
        platform_id=lambda: "linux",
        import_probe=lambda: None,  # type: ignore[arg-type,return-value]
        reset_unfinished=lambda *_a, **_k: (),  # type: ignore[arg-type]
    )
    plain = tmp_path / "plain"
    (plain / "src").mkdir(parents=True)
    with pytest.raises(InstallerError) as refused:
        engine.preflight(InstallOptions(server_dir=plain))
    assert "not empty and was not created by this app" in str(refused.value)

    # Which is what the copy for each folder tells the user, in their words.
    assert "no record here of an install this app made" in cancelled_install_message(
        "WoW WotLK", checkout
    )
    note = cancelled_install_message("WoW WotLK", plain)
    assert "the app will refuse it" in note and "was not created by this app" in note


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
