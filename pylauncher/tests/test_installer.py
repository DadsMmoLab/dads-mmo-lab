"""Tests for the Phase 3a installer (`yulon.catalog.installer`, roadmap 3.2/3.3).

`runner.interact` is exercised for real against a tiny bash script that
behaves like the installers (colour codes, `read -r` prompts with and without
trailing newlines); the `Installer` control flow is tested through its seams
so no Docker, network, or two-hour build is involved.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import platform, runner
from yulon.catalog import installer as installer_module
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import (
    PROMPT_RULES,
    DockerUnavailableError,
    Installer,
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    bash_available,
    make_responder,
)

# Not just `which bash`: on Windows that finds the Store alias for WSL, which fails
# with execvpe(/bin/bash) when no distro is installed (Windows test VM, 2026-08-21).
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
echo "Remove it and start fresh? (y/n): "
read -r fresh
echo "fresh=[$fresh]"
echo "building..." >&2
exit ${EXIT_CODE:-0}
"""


@needs_bash
def test_interact_answers_prompts_with_and_without_newlines(tmp_path: Path) -> None:
    """Partial-line prompts (`echo -ne`) and full-line prompts both get answered."""
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    seen: list[str] = []
    respond = make_responder(InstallOptions(server_dir=Path("/srv/wow")))
    lines = list(runner.interact(["bash", str(script)], respond=respond))
    stripped = [runner.strip_ansi(line) for line in lines]
    seen.extend(stripped)
    assert "dir=[/srv/wow]" in stripped
    assert "answer=[y]" in stripped
    assert "fresh=[n]" in stripped  # destructive offer declined by default
    assert "building..." in stripped  # stderr is merged


@needs_bash
def test_interact_raises_on_nonzero_exit_after_yielding_output(tmp_path: Path) -> None:
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    got: list[str] = []
    with pytest.raises(subprocess.CalledProcessError):
        for line in runner.interact(
            ["bash", str(script)], respond=make_responder(InstallOptions()), env={"EXIT_CODE": "3"}
        ):
            got.append(runner.strip_ansi(line))
    assert "dir=[]" in got  # blank = script default dir


def test_prompt_rules_are_ordered_and_decline_optional_offers() -> None:
    respond = make_responder(InstallOptions(reinstall=True, client_dir=Path("/c/wow")))
    assert respond("Enter path to your WoW TBC client folder:") == "/c/wow"
    assert respond("Remove it and start fresh? (y/n): ") == "y"
    assert respond("Open the GitHub README in your browser now? (y/n): ") == "n"
    assert respond("Download wow-manage.sh to your home folder now? (y/n): ") == "n"
    assert respond("Continue anyway? (NOT recommended) (y/n): ") == "n"
    assert respond("Type yes to reset the keyring, or anything else to cancel: ") == "yes"
    assert respond("Ready to build your Playerbots server? (y/n): ") == "y"
    assert respond("Building worldserver 42%") is None
    # Hints that merely MENTION the key are not prompts (the live gate caught this one).
    assert respond("  Leave blank and press ENTER to use the default location.") is None
    assert respond("Press ENTER to continue...") == ""
    assert respond("Press ENTER when done creating accounts...") == ""
    assert respond("  ── or press ENTER to shut down manually ──") == ""
    assert respond("  WoW not detected — press ENTER to shut down.") == ""
    assert respond("Install path: ") == ""  # blank = default when no server_dir was chosen
    assert (
        make_responder(InstallOptions(server_dir=Path("/srv/wow")))("  Install path: ")
        == "/srv/wow"
    )
    assert len(PROMPT_RULES) >= 8


def _installer(entry: object, **kwargs: object) -> Installer:
    """An `Installer` with every host-dependent seam pinned.

    These tests assert on control flow, not on the machine they run on: the
    package manager decides which script variant is picked, `bash_check`
    whether a shell exists, and `platform_id` whether the entry is installable
    here at all (roadmap 6.1). Any test that cares overrides the one it cares
    about.
    """
    kwargs.setdefault("package_manager", lambda: None)
    kwargs.setdefault("bash_check", lambda: True)
    kwargs.setdefault("platform_id", lambda: "linux")
    return Installer(entry, **kwargs)  # type: ignore[arg-type]


def _fake_interact(calls: list[dict[str, object]]) -> object:
    def interact(command: list[str], **kwargs: object) -> Iterator[str]:
        calls.append({"command": command, **kwargs})
        yield "hello"
        yield "done"

    return interact


def test_installer_runs_the_entry_script_through_interact(tmp_path: Path) -> None:
    """`run()` resolves `<installers_root>/<install.script>`, streams lines, answers via rules."""
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    calls: list[dict[str, object]] = []
    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: True,
        interact=_fake_interact(calls),  # type: ignore[arg-type]
    )
    # Not a bare "/srv": that is now one of the reserved trees preflight
    # refuses, and this test is about the script running, not about the
    # folder rule.
    assert list(installer.run(InstallOptions(server_dir=Path("/srv/wow-server")))) == [
        "hello",
        "done",
    ]
    assert calls[0]["command"] == ["bash", str(script)]
    assert calls[0]["cwd"] == script.parent
    assert callable(calls[0]["respond"])


def test_a_cancelled_run_is_not_logged_as_a_finished_install(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`interact()` returns on cancel instead of raising, so `run()` fell through to success.

    The app log is the file a user pastes into a bug report, and it said
    "install of wow-wotlk finished" for an install stopped 2.3 GB into the
    source clone (install gate, 2026-08-23).
    """
    import threading

    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    cancel = threading.Event()

    def interact(command: list[str], **kwargs: object) -> Iterator[str]:
        yield "cloning"
        cancel.set()  # the Stop button, mid-stream

    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: True,
        interact=interact,  # type: ignore[arg-type]
    )
    with caplog.at_level("INFO", logger="yulon.catalog.installer"):
        assert list(installer.run(InstallOptions(), cancel=cancel)) == ["cloning"]
    said = [record.message for record in caplog.records]
    assert any("was cancelled" in message for message in said), said
    assert not any("finished" in message for message in said), said


def test_the_cancel_log_survives_a_line_arriving_after_stop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The other cancel shape, and the one that left the log with no ending at all.

    `_StreamWorker.run()` breaks its loop on the first line received AFTER Stop,
    and breaking drops the last reference to this generator — CPython closes it
    and raises `GeneratorExit` at the `yield`, which `except CalledProcessError`
    does not catch. The app log is the file a user pastes into a bug report and
    it held "installing wow-wotlk via ..." and then nothing (review,
    2026-08-23).
    """
    import threading

    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    cancel = threading.Event()

    def interact(command: list[str], **kwargs: object) -> Iterator[str]:
        yield "cloning"
        cancel.set()  # Stop, mid-stream
        yield "one more line the worker will never take"

    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: True,
        interact=interact,  # type: ignore[arg-type]
    )
    with caplog.at_level("INFO", logger="yulon.catalog.installer"):
        lines = installer.run(InstallOptions(), cancel=cancel)
        for _ in lines:
            if cancel.is_set():
                break  # exactly what `_StreamWorker.run()` does
        lines.close()
    said = [record.message for record in caplog.records]
    assert any("was cancelled" in message for message in said), said
    assert not any("finished" in message for message in said), said


def test_the_cancel_copy_points_at_use_existing_when_the_source_is_on_disk(
    tmp_path: Path,
) -> None:
    """Stop after the build finished throws away hours; the recovery is one button.

    `interact()` breaks on `cancel.is_set()` at the top of its loop, before it
    ever looks at the child's return code, so a Stop pressed while the script
    sits in `wait_for_server` (up to 1800 s of bare dots, with the containers
    already up) discards a completed install. The app cannot prove that from
    here — see `cancelled_install_message()` — but it can name the evidence it
    does have and the button that adopts the folder without pinning it.
    """
    from yulon.catalog.installer import cancelled_install_message

    nothing_there = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" not in nothing_there
    assert "nothing to resume" in nothing_there

    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    source_there = cancelled_install_message("WoW WotLK", tmp_path)
    assert "Use existing" in source_there
    assert str(tmp_path) in source_there


def test_neither_new_rule_answers_a_line_that_is_not_the_question() -> None:
    """`respond()` sees every line, and on a quiet partial line the whole buffer.

    An unanchored `Remove snap Docker` writes "n" into a child that is not
    reading, which desynchronises the next real prompt; an unanchored
    rpm-ostree rule opens a modal dialog over ordinary build output. Both new
    rules now carry the `(y/n)` suffix `ask_yes_no` prints, the way the
    docker-group rule already did (review, 2026-08-23).
    """
    asked: list[str] = []
    respond = make_responder(InstallOptions(), ask=lambda prompt: (asked.append(prompt), "y")[1])
    assert respond("  Remove snap Docker first if you want to keep your own containers.") is None
    assert respond("Step 4/9: Install Docker via rpm-ostree and reboot now was skipped") is None
    assert asked == []


@pytest.mark.parametrize(
    ("package_manager", "expected"),
    [
        (None, "wow-wotlk/install-wow-wotlk.sh"),
        ("pacman", "wow-wotlk/install-wow-wotlk.sh"),
        ("apt", "wow-wotlk/install-wow-wotlk-ubuntu.sh"),
        ("dnf", "wow-wotlk/install-wow-wotlk-fedora.sh"),
        ("zypper", "wow-wotlk/install-wow-wotlk.sh"),
    ],
)
def test_installer_picks_the_script_variant_for_the_host_package_manager(
    tmp_path: Path, package_manager: str | None, expected: str
) -> None:
    """Ubuntu/Fedora hosts get the Debian/Fedora ports; everything else the default script."""
    entry = load_catalog().get("wow-wotlk")
    installer = _installer(entry, installers_root=tmp_path, package_manager=lambda: package_manager)
    assert installer.script == tmp_path / expected


def test_script_env_inherits_ours_and_defaults_term(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop-launched app has no TERM; the scripts' `clear` needs one. Overrides win."""
    entry = load_catalog().get("wow-wotlk")
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("YULON_MARKER", "1")
    env = _installer(entry, env={"EXIT_CODE": "3"}).script_env()
    assert env["TERM"] == installer_module.DEFAULT_TERM
    assert env["YULON_MARKER"] == "1" and env["EXIT_CODE"] == "3"
    monkeypatch.setenv("TERM", "")
    assert Installer(entry).script_env()["TERM"] == installer_module.DEFAULT_TERM
    monkeypatch.setenv("TERM", "screen")
    assert _installer(entry, env={"TERM": "dumb"}).script_env()["TERM"] == "dumb"
    assert Installer(entry).script_env()["TERM"] == "screen"


def test_host_package_manager_is_none_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_module.sys, "platform", "win32")
    assert installer_module.host_package_manager() is None
    monkeypatch.setattr(installer_module.sys, "platform", "linux")
    monkeypatch.setattr(installer_module.platform, "linux_package_manager", lambda: "apt")
    assert installer_module.host_package_manager() == "apt"


def test_installer_fails_gracefully_without_docker(tmp_path: Path) -> None:
    """Roadmap 3.3: no daemon + provisioning not implemented → a clear, catchable error."""
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    calls: list[dict[str, object]] = []
    from yulon.platform import ProvisionReport

    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: False,
        ensure_docker=lambda **_: ProvisionReport(
            "linux", manual_steps=("Install Docker Engine by hand: https://docs.docker.com/",)
        ),
        interact=_fake_interact(calls),  # type: ignore[arg-type]
    )
    with pytest.raises(DockerUnavailableError, match="could not be set up automatically"):
        list(installer.run())
    assert calls == []  # the script never started

    rebooter = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: False,
        ensure_docker=lambda **_: ProvisionReport(
            "windows", done=("wsl --install",), reboot_required=True, manual_steps=("Reboot.",)
        ),
    )
    with pytest.raises(DockerUnavailableError, match="reboot is needed"):
        rebooter.preflight(InstallOptions())


def test_the_prompter_reaches_provisioning_and_not_only_the_script(tmp_path: Path) -> None:
    """The consent question is asked by the thing that escalates, which runs first.

    `run()` has held the live prompter all along and dropped it one line before
    `preflight()` — where `ensure_docker()` is called. So on a passwordless-sudo
    box the launcher joined the docker group before the script started, and the
    script's own `docker_group_consent()` then found the user already a member
    and never asked. Nobody was asked anything, by either half.

    This pins the delivery rather than the wording: whatever the dialog says,
    the seam that can escalate has to be able to reach it.
    """
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    seen: list[object] = []
    from yulon.platform import ProvisionReport

    def _provision(**kwargs: object) -> ProvisionReport:
        seen.append(kwargs.get("ask"))
        return ProvisionReport("linux", docker_ready=True)

    def _prompter(_question: str) -> str:
        return "n"

    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: False,
        ensure_docker=_provision,
        interact=_fake_interact([]),  # type: ignore[arg-type]
    )
    list(installer.run(InstallOptions(server_dir=tmp_path / "srv"), ask=_prompter))
    assert seen == [_prompter]

    # And with no prompter it arrives as None rather than being omitted, so the
    # decline is a decision the provisioning path makes, not an accident of
    # keyword defaults it never sees.
    seen.clear()
    list(installer.run(InstallOptions(server_dir=tmp_path / "srv")))
    assert seen == [None]


def test_installer_requires_the_client_dir_when_the_script_asks_for_it(tmp_path: Path) -> None:
    """README §3a: the app never fetches a client; games that need one refuse to start without."""
    entry = load_catalog().get("wow-tbc")
    assert entry.install.requires_client_dir is True
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    installer = _installer(entry, installers_root=tmp_path, docker_check=lambda: True)
    with pytest.raises(InstallerError, match="never downloads game clients"):
        list(installer.run())
    with pytest.raises(InstallerError, match="does not exist"):
        list(installer.run(InstallOptions(client_dir=tmp_path / "nope")))


def test_installer_reports_a_missing_script(tmp_path: Path) -> None:
    entry = load_catalog().get("wow-wotlk")
    with pytest.raises(InstallerError, match="not found"):
        _installer(entry, installers_root=tmp_path, docker_check=lambda: True).preflight(
            InstallOptions()
        )


def test_installer_wraps_script_failure(tmp_path: Path) -> None:
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    def failing(command: list[str], **kwargs: object) -> Iterator[str]:
        yield "step 1"
        raise subprocess.CalledProcessError(7, command)

    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: True,
        interact=failing,  # type: ignore[arg-type]
    )
    got: list[str] = []
    with pytest.raises(InstallerError, match="status 7"):
        for line in installer.run():
            got.append(line)
    assert got == ["step 1"]


def test_preflight_refuses_when_bash_cannot_run(tmp_path: Path) -> None:
    """A `bash` that is only the WSL alias must produce advice, not an execvpe error."""
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    installer = _installer(
        entry, installers_root=tmp_path, docker_check=lambda: True, bash_check=lambda: False
    )
    with pytest.raises(InstallerError, match="no working `bash`"):
        list(installer.run())


def test_bash_available_probes_that_bash_actually_runs() -> None:
    """`which bash` is not enough — the probe must execute something."""
    import subprocess as sp

    calls: list[list[str]] = []

    def ok(argv: list[str], *a: object, **k: object) -> sp.CompletedProcess[str]:
        calls.append(argv)
        return sp.CompletedProcess(argv, 0, "", "")

    def broken(argv: list[str], *a: object, **k: object) -> sp.CompletedProcess[str]:
        calls.append(argv)
        return sp.CompletedProcess(argv, 1, "", "execvpe(/bin/bash) failed")

    if shutil.which("bash") is None:
        assert bash_available(ok) is False  # nothing to probe
        return
    assert bash_available(ok) is True
    assert calls[-1] == ["bash", "-c", "exit 0"]
    assert bash_available(broken) is False


_REUSABLE_PROBE = """
set -u
%s
if dir_is_reusable "$1"; then echo REUSABLE; else echo PROTECTED; fi
"""


def _dir_is_reusable(script: Path, target: Path) -> str:
    """Run the installer's own `dir_is_reusable` against `target`.

    The function is lifted out of the shipped script rather than restated here,
    so this test cannot pass against a copy that has drifted from the file the
    users actually run.
    """
    body = script.read_text(encoding="utf-8")
    start = body.index("dir_is_reusable() {")
    end = body.index("\n}", start) + 2
    out = subprocess.run(
        ["bash", "-c", _REUSABLE_PROBE % body[start:end], "probe", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return out.stdout.strip()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permission semantics")
@pytest.mark.parametrize(
    "script_name",
    ["install-wow-wotlk.sh", "install-wow-wotlk-fedora.sh", "install-wow-wotlk-ubuntu.sh"],
)
def test_the_installers_reusable_check_fails_closed(tmp_path: Path, script_name: str) -> None:
    """An empty folder may be cloned into; anything we cannot verify may not.

    This is the property a six-agent adversarial pass killed the first spelling
    over, and no Python test can cover it because it lives in bash. The obvious
    version, `[ -n "$(ls -A "$D")" ]`, fails OPEN: `ls -A` prints nothing for a
    directory it cannot READ exactly as it does for an empty one, so a folder
    holding someone's server reads as empty and gets cloned over. `find
    -maxdepth 0 -empty` prints nothing when it cannot look, which is the safe
    way round.

    All three WotLK scripts are checked because they are separate files that
    have already diverged elsewhere.
    """
    script = (
        Path(__file__).resolve().parents[1] / "catalog" / "installers" / "wow-wotlk" / script_name
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _dir_is_reusable(script, empty) == "REUSABLE"

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "docker-compose.yml").write_text("x", encoding="utf-8")
    assert _dir_is_reusable(script, occupied) == "PROTECTED"

    # A lone dotfile is content: `.db_password` is exactly what a half-finished
    # install leaves behind, and `ls -A` and `find -empty` agree here.
    dotted = tmp_path / "dotted"
    dotted.mkdir()
    (dotted / ".db_password").write_text("x", encoding="utf-8")
    assert _dir_is_reusable(script, dotted) == "PROTECTED"

    # The one that matters: unreadable AND non-empty. This is where `ls -A`
    # returned "empty" and the old guard would have cloned over a real install.
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    (unreadable / "server.sql").write_text("x", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        assert _dir_is_reusable(script, unreadable) == "PROTECTED"
    finally:
        unreadable.chmod(0o755)

    assert _dir_is_reusable(script, tmp_path / "does-not-exist") == "PROTECTED"


_SELINUX_PROBE = """
set -u
print_info() { :; }
print_success() { :; }
print_warning() { :; }
%(stub)s
chcon() { echo "chcon $*" >> "$CALLS"; }
%(body)s
selinux_label_for_containers "$1"
"""


def _selinux_free_path(tmp_path: Path) -> str:
    """A PATH with the tools the function needs and no SELinux ones.

    Omitting the `getenforce` stub is NOT how you express "this distro has no
    SELinux": on Fedora - and on any CI image with the tools installed -
    `command -v getenforce` then finds the real binary and the case asserts the
    opposite of what it says. Verified on Fedora 44, where exactly that made
    three "absent" cases fire the relabel.
    """
    bin_dir = tmp_path / "nosel-bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("bash", "sh", "mkdir", "cat", "rm"):
        found = shutil.which(tool)
        if found and not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(found)
    return str(bin_dir)


# Every shell function the probe must carry, not just the entry point.
#
# `selinux_label_for_containers` calls the other three. An unlifted callee is
# not an absent behaviour - it is `command not found`, which exits 127, which
# `if ! selinux_labels_supported ...` reads as "this filesystem cannot hold
# labels", so the function returns having relabelled nothing and the test sees
# an empty list. That is exactly how this broke: two of the four were lifted,
# every positive case asserted [], and it merged because the whole test is
# skipped on the Windows dev box.
_SELINUX_FUNCTIONS = (
    "selinux_is_enforcing",
    "selinux_labels_supported",
    "selinux_drop_z_from_override",
    "selinux_label_for_containers",
)


def _selinux_body(script: Path) -> str:
    """Every function in the call graph, lifted from the shipped script.

    Lifted rather than restated so the test cannot pass against a copy of the
    logic that the product no longer has.
    """
    text = script.read_text(encoding="utf-8")
    out = []
    for func in _SELINUX_FUNCTIONS:
        name = f"{func}() {{"
        assert text.count(name) == 1, f"{script.name}: {name} appears {text.count(name)} times"
        begin = text.index(name)
        out.append(text[begin : text.index("\n}", begin) + 2])
    return "\n".join(out)


def _selinux_calls(
    script: Path,
    target: Path,
    calls: Path,
    enforce: str | None,
    *,
    sysfs: Path | None = None,
    path: str | None = None,
) -> list[str]:
    """Run the installer's own relabel helper and report the argv it emitted.

    Asserting on the emitted command rather than on the message it prints: a
    rule spelled one way in prose and another in argv is how this project has
    been caught before.

    `sysfs` points the kernel-interface check at a file under the test's
    control. Without it the real /sys/fs/selinux/enforce answers on any box that
    has one, the `getenforce` stub is never consulted, and the not-enforcing
    cases assert nothing at all.
    """
    stub = "" if enforce is None else f'getenforce() {{ echo "{enforce}"; }}'
    probe = _SELINUX_PROBE % {"stub": stub, "body": _selinux_body(script)}
    calls.write_text("", encoding="utf-8")
    env = {
        "PATH": path if path is not None else os.environ.get("PATH", ""),
        "CALLS": str(calls),
        # A path that does not exist takes the getenforce branch, which is what
        # "this kernel exposes no selinuxfs" looks like.
        "YULON_SELINUX_ENFORCE_PATH": str(sysfs) if sysfs else str(target / "no-selinuxfs"),
    }
    result = subprocess.run(
        ["bash", "-c", probe, "probe", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    # A helper the probe forgot to lift is `command not found`, and bash's 127
    # is indistinguishable from a real "no" to every caller here - it silently
    # turns "did not relabel" into the answer. Failing on the shell's own
    # complaint names the missing function; without it the next unlifted callee
    # arrives as an empty list that reads like a behaviour change.
    assert "command not found" not in result.stderr, (
        f"{script.name}: the probe is missing a function the shipped code calls, "
        f"so its result means nothing:\n{result.stderr.strip()}"
    )
    return [line for line in calls.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="needs a POSIX shell")
@pytest.mark.parametrize(
    "script_name",
    ["install-wow-wotlk.sh", "install-wow-wotlk-fedora.sh", "install-wow-wotlk-ubuntu.sh"],
)
def test_the_installers_label_the_server_folder_only_where_selinux_enforces(
    tmp_path: Path, script_name: str
) -> None:
    """Fedora could not finish an install until this ran; Ubuntu must not see it.

    Measured on clean Fedora 44 (2026-08-25), with the first-install dead-end
    already fixed: `ac-db-import` exited 1 with "cp: cannot create regular file
    ...: Permission denied" on files owned by the user. AzerothCore's compose
    bind-mounts env/dist without `:z`, so the directory keeps `user_home_t` and
    the container is refused. Relabelling made the identical import exit 0 and
    the whole stack came up.

    Both sources of truth are covered. selinuxfs is checked first because it
    exists whenever SELinux is loaded, while `getenforce` ships in an optional
    package - gating only on the tool fails OPEN, which is the direction of the
    bug. The negative cases carry the weight: a relabel firing on Debian would
    be a change nobody asked for on a system with no SELinux to satisfy.
    """
    script = (
        Path(__file__).resolve().parents[1] / "catalog" / "installers" / "wow-wotlk" / script_name
    )
    target = tmp_path / "server"
    target.mkdir()
    calls = tmp_path / "calls.txt"
    expected = [f"chcon -Rt container_file_t {target}/env"]

    # selinuxfs present - the kernel's own answer wins, and `getenforce` is not
    # consulted even when it disagrees.
    enforcing = tmp_path / "enforce-1"
    enforcing.write_text("1\n", encoding="utf-8")
    assert _selinux_calls(script, target, calls, "Disabled", sysfs=enforcing) == expected

    permissive = tmp_path / "enforce-0"
    permissive.write_text("0\n", encoding="utf-8")
    assert _selinux_calls(script, target, calls, "Enforcing", sysfs=permissive) == []

    # No selinuxfs - fall back to the tool.
    assert _selinux_calls(script, target, calls, "Enforcing") == expected
    assert _selinux_calls(script, target, calls, "Permissive") == []
    assert _selinux_calls(script, target, calls, "Disabled") == []

    # Neither source: no selinuxfs and no tools on PATH - Debian, Ubuntu, Arch.
    assert _selinux_calls(script, target, calls, None, path=_selinux_free_path(tmp_path)) == []

    # The directories are made first, so whatever compose creates inside them
    # inherits the label rather than needing a second pass.
    assert (target / "env" / "dist" / "etc").is_dir()
    assert (target / "env" / "dist" / "logs").is_dir()


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


# The sudo banner, checked against the argv the script actually runs.
#
# A banner shown immediately before `sudo -v` is the whole of the user's
# informed consent, and it is checked in BOTH directions off this one table:
# every bullet must be backed by a command, and every privileged command must
# have a bullet. The second is the one with teeth - the first version of this
# check was a closed world of hand-written keys, so `sudo systemctl reboot`
# (five sites in the Fedora installer, four of them with no confirmation at all)
# sat behind a test whose docstring claimed a real use of sudo must be declared.
#
# Keyed on a phrase in the bullet so wording stays free to improve, and valued
# by the VERBS that justify it, taken from the same extraction the reverse
# direction uses - so the two can never disagree about what a script does.
SUDO_BANNER_CLAIMS: dict[str, frozenset[str]] = {
    "Installing Docker": frozenset(
        {
            "dnf install",
            "apt-get install",
            "apt-get update",
            "pacman",
            "rpm-ostree install",
        }
    ),
    # Taking software OFF the machine is a different consent from putting it on.
    "Removing conflicting packages": frozenset({"dnf remove", "apt-get remove"}),
    "Docker service": frozenset(
        {
            "systemctl enable",
            "systemctl start",
            "systemctl restart",
            "systemctl daemon-reload",
            "systemctl reset-failed",
        }
    ),
    "docker group": frozenset({"usermod"}),
    "Running docker": frozenset({"docker"}),
    "old server folder": frozenset({"rm $SERVER_DIR"}),
    "repository": frozenset({"tee", "install", "curl", "chmod", "bash -c curl", "bash -c echo"}),
    "Restarting your computer": frozenset({"systemctl reboot"}),
    "Docker snap": frozenset({"snap remove"}),
    "SteamOS read-only root": frozenset({"steamos-readonly enable", "steamos-readonly disable"}),
    "developer mode": frozenset({"steamos-devmode"}),
    "pacman keyring": frozenset(
        {
            "pacman-key --init",
            "pacman-key --populate",
            "pacman-key --list-keys",
            "pacman-key --refresh-keys",
            "rm /etc/pacman.d/gnupg",
        }
    ),
}

# Privileged calls that need no bullet, each with the reason it needs none.
# Anything NOT here and not in a claim above fails the suite by name.
SUDO_EXEMPT: dict[str, str] = {
    "-v": "the credential prompt itself - this IS the thing the banner introduces",
    "true": "`sudo -n true`, the keepalive that refreshes the cache already granted",
    "systemctl status": "read-only diagnostics",
    "systemctl is-active": "read-only diagnostics",
    "journalctl": "read-only diagnostics",
    "dnf list": "read-only query - asks what is installed, changes nothing",
    "steamos-readonly status": "read-only diagnostics - reports the mode, does not change it",
}

# `sudo` inside an echo/print_* line is usually advice being PRINTED - the
# installers tell the user what to type when something fails - and counting that
# as an action would make every one of those messages a consent obligation. But
# only usually: a `$(...)` on the same line RUNS, and `$(sudo systemctl reboot)`
# dropped into one of those echoes passed this check until the fragments below
# existed.
_SUDO_CALL = re.compile(r"(?<![\w-])sudo\s+((?:-\w+\s+)*)([\w./-]+)((?:\s+[^\s;&|]+)*)")
_NOT_A_CALL = re.compile(r"^\s*#|^\s*(echo|print_\w+|printf)\b")
_PRINTED = re.compile(r"(echo|print_\w+|printf)\b[^\n]*sudo")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)")
# `sudo bash -c '<body>'` runs <body> AS ROOT. Collapsing it to the verb `bash`
# discarded everything that actually ran, and `bash` was already approved for
# repository setup - so any command could be hidden inside those quotes.
_SHELL_C = re.compile(r"""(?<![\w-])sudo\s+(?:-\w+\s+)*(?:bash|sh)\s+-c\s+(['"])(.*?)\1""")

# Programs whose SUBCOMMAND changes what the user is consenting to. Enabling a
# service and rebooting the machine are both `systemctl`, and collapsing them
# would hide the reboot behind the service bullet. `rm` is here because the
# generic script wipes two very different things - the server folder the user
# chose to reinstall, and the system's pacman keyring.
_SUBCOMMAND_MATTERS = frozenset(
    {"systemctl", "rpm-ostree", "steamos-readonly", "snap", "pacman-key"}
)
# Package managers put flags before the subcommand (`dnf -y install`), while
# pacman-key's subcommand IS a flag (`--populate`). Reading them the same way
# filed `pacman-key --populate archlinux holo` under the verb "archlinux".
_FLAGS_BEFORE_SUBCOMMAND = frozenset({"dnf", "apt-get"})
_TARGET_MATTERS = frozenset({"rm"})


def _executable_parts(line: str) -> list[str]:
    """The parts of `line` that actually run, printed text removed.

    A command substitution runs wherever it appears, including inside the echo
    that prints its output - which is why it is pulled out before the advisory
    check rather than after.
    """
    parts: list[str] = list(_SUBSTITUTION.findall(line))
    if not (_NOT_A_CALL.match(line) or _PRINTED.search(line)):
        parts.append(line)
    return parts


def _normalise(program: str, rest: str) -> str:
    """One `sudo` call as the verb a user would have to consent to."""
    words = rest.split()
    if program in _SUBCOMMAND_MATTERS and words:
        return f"{program} {words[0]}"
    if program in _FLAGS_BEFORE_SUBCOMMAND:
        subcommand = next((w for w in words if not w.startswith("-")), "")
        return f"{program} {subcommand}" if subcommand else program
    if program in _TARGET_MATTERS:
        target = next((w for w in words if not w.startswith("-")), "")
        return f"{program} {target.strip(chr(34) + chr(39))}" if target else program
    return program


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Physical lines joined across backslash continuations, keyed by first line.

    The real `sudo bash -c '...'` in the Fedora installer wraps its body onto a
    second physical line, so a per-line regex saw an unterminated quote and the
    body - which runs as root - was never inspected at all.
    """
    joined: list[tuple[int, str]] = []
    number = 0
    buffer = ""
    for index, line in enumerate(text.splitlines(), 1):
        if not buffer:
            number = index
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined.append((number, buffer + line))
        buffer = ""
    if buffer:
        joined.append((number, buffer))
    return joined


def _sudo_verbs(text: str) -> dict[str, list[int]]:
    """Every executable `sudo` call in `text`, normalised to a verb, with its lines.

    Every line is kept, not just the first: a verb recorded once could be
    supplied by an unrelated call elsewhere in the file, so deleting the call a
    bullet actually describes still passed.
    """
    found: dict[str, list[int]] = {}
    for number, line in _logical_lines(text):
        # `sudo bash -c '<body>'` runs <body> as root. Name the verb after what
        # the body actually starts with, attributed to the wrapper that gave it
        # root, so replacing the body changes the verb and fails the check.
        wrapped = _SHELL_C.findall(line)
        for _quote, body in wrapped:
            words = body.strip().split()
            if words:
                found.setdefault(f"bash -c {words[0]}", []).append(number)
        for part in _executable_parts(line):
            for _flags, program, rest in _SUDO_CALL.findall(part):
                # Already accounted for, with its body, just above. When the
                # body could NOT be read the wrapper is left to fail as an
                # unknown verb rather than vanish.
                if program in {"bash", "sh"} and wrapped:
                    continue
                found.setdefault(_normalise(program, rest), []).append(number)
    return found


def _sudo_banner_bullets(text: str) -> list[str]:
    """The bullet lines between the banner header and the password prompt.

    Anchored on the `echo` that prints the header rather than on the text
    appearing anywhere, so a comment quoting the banner cannot shadow it.
    """
    header = re.search(r"^echo .*This installer needs sudo access for", text, re.MULTILINE)
    assert header, "no sudo banner header found"
    end = text.index("Please enter your password", header.start())
    return [line for line in text[header.start() : end].splitlines() if "\u2022" in line]


def _wotlk_script(script_name: str) -> Path:
    return (
        Path(__file__).resolve().parents[1] / "catalog" / "installers" / "wow-wotlk" / script_name
    )


WOTLK_SCRIPTS = [
    "install-wow-wotlk.sh",
    "install-wow-wotlk-fedora.sh",
    "install-wow-wotlk-ubuntu.sh",
]


@pytest.mark.parametrize("script_name", WOTLK_SCRIPTS)
def test_the_sudo_banner_declares_every_privileged_thing_the_script_does(script_name: str) -> None:
    """No privileged action may be absent from the banner - and none may be unknown.

    This is the direction that failed before. It used to iterate the claims
    table, so a command outside it could never be required; `sudo systemctl
    reboot` was exactly that, and the Fedora installer reboots the machine after
    a 10-second sleep with no confirmation on four of its five paths. Now the
    iteration is over what the SCRIPT does, and a verb this table has never
    heard of fails by name rather than passing by omission.
    """
    text = _wotlk_script(script_name).read_text(encoding="utf-8")
    bullets = " ".join(_sudo_banner_bullets(text))
    for verb, lines in sorted(_sudo_verbs(text).items()):
        line = lines[0]
        if verb in SUDO_EXEMPT:
            continue
        claims = [phrase for phrase, verbs in SUDO_BANNER_CLAIMS.items() if verb in verbs]
        assert claims, (
            f"{script_name}:{line}: `sudo {verb}` is a privileged action this table has never "
            f"heard of. Add it to SUDO_BANNER_CLAIMS with the bullet that discloses it, or to "
            f"SUDO_EXEMPT with the reason it needs none. Do not leave it unlisted: that is how "
            f"a reboot shipped undisclosed."
        )
        assert any(phrase in bullets for phrase in claims), (
            f"{script_name}:{line}: the script runs `sudo {verb}` but no banner bullet mentions "
            f"{' or '.join(repr(c) for c in claims)}, so the user consents to less than they get."
        )


@pytest.mark.parametrize("script_name", WOTLK_SCRIPTS)
def test_the_sudo_banner_claims_nothing_the_script_does_not_do(script_name: str) -> None:
    """And the other way: a promise with nothing behind it.

    All three scripts used to promise "Fixing file ownership after build" and
    none of them contains a `chown` - the only match in each file is a comment,
    which is why this now reads extracted argv rather than the file's text.
    """
    text = _wotlk_script(script_name).read_text(encoding="utf-8")
    present = set(_sudo_verbs(text))
    bullets = _sudo_banner_bullets(text)
    assert bullets, f"{script_name}: no bullets found under the sudo banner"

    for bullet in bullets:
        phrases = [phrase for phrase in SUDO_BANNER_CLAIMS if phrase in bullet]
        assert phrases, (
            f"{script_name}: the banner claims something with no known backing command:\n"
            f"  {bullet.strip()}\n"
            f"Either drop the claim, or add it to SUDO_BANNER_CLAIMS with the verbs that make "
            f"it true."
        )
        for phrase in phrases:
            assert SUDO_BANNER_CLAIMS[phrase] & present, (
                f"{script_name}: the banner promises {phrase!r} but the script runs none of "
                f"{sorted(SUDO_BANNER_CLAIMS[phrase])} - a password asked for work that never "
                f"happens."
            )


def test_the_verb_extractor_ignores_advice_and_comments() -> None:
    """The extractor's own trap, pinned.

    The installers print `sudo` commands as advice when something fails, and
    they carry comments naming commands they no longer run. Counting either as
    an action would make the banner grow obligations the script does not have -
    and counting a comment as BACKING is how the first version of this check
    could be satisfied without any real command at all.
    """
    text = (
        "# sudo rm -rf /etc/pacman.d/gnupg\n"
        'print_info "  sudo chcon -Rt container_file_t /srv"\n'
        'echo "run: sudo usermod -aG docker $USER"\n'
        "sudo systemctl reboot\n"
        "sudo systemctl enable docker\n"
    )
    assert set(_sudo_verbs(text)) == {"systemctl reboot", "systemctl enable"}


def _override_volumes(script: Path) -> dict[str, list[str]]:
    """Volumes per service from the override heredoc, by structure not substring.

    A two-space key is a service; a four-space `volumes:` belongs to that
    service; six-space `- ` entries under it are its mounts. Anything deeper -
    such as a `volumes:` that has drifted INSIDE `build:` - is deliberately not
    collected, because that is the shape that makes `docker compose config`
    reject the file outright while every substring the old assertions looked for
    is still present.
    """
    text = script.read_text(encoding="utf-8")
    body = text[text.index("docker-compose.override.yml") : text.index("\nOVERRIDE")]
    services: dict[str, list[str]] = {}
    service = None
    in_volumes = False
    for raw in body.split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if indent == 2 and line.endswith(":"):
            service = line[:-1]
            services.setdefault(service, [])
            in_volumes = False
        elif indent == 4 and service is not None:
            in_volumes = line == "volumes:"
        elif indent == 6 and in_volumes and line.startswith("- ") and service is not None:
            services[service].append(line[2:])
        elif indent >= 6 and not (in_volumes and line.startswith("- ")):
            in_volumes = False
    return services


@pytest.mark.parametrize(
    "script_name",
    ["install-wow-wotlk.sh", "install-wow-wotlk-fedora.sh", "install-wow-wotlk-ubuntu.sh"],
)
def test_the_generated_override_labels_every_bind_mount_it_shares(script_name: str) -> None:
    """`:z` is the half that survives a relabel; chcon is the half that runs once.

    A review of the first version made the case: `chcon` is applied before ONE of
    the several `docker compose up` sites in this product, and any policy relabel
    (`restorecon -R ~`, a selinux-policy update, `touch /.autorelabel`) puts the
    tree back with nothing to re-apply it. `:z` is applied by Docker on every
    start, so it is self-healing.

    Asserted per SERVICE, because the mount only helps the container that has
    it, and because a `volumes:` block that drifts inside `build:` is rejected by
    `docker compose config` while leaving every substring intact.
    """
    script = (
        Path(__file__).resolve().parents[1] / "catalog" / "installers" / "wow-wotlk" / script_name
    )
    services = _override_volumes(script)
    for service in ("ac-worldserver", "ac-authserver", "ac-db-import"):
        assert service in services, f"{script_name}: {service} missing from the override"
        mounts = services[service]
        assert (
            "${DOCKER_VOL_ETC:-./env/dist/etc}:/azerothcore/env/dist/etc:z" in mounts
        ), f"{script_name}: {service} has no labelled etc mount, only {mounts}"
        assert (
            "${DOCKER_VOL_LOGS:-./env/dist/logs}:/azerothcore/env/dist/logs:z" in mounts
        ), f"{script_name}: {service} has no labelled logs mount, only {mounts}"
    # Spelled with the base file's own default so Compose merges by target path
    # rather than adding a second mount of the same directory.
    assert all(
        m.startswith("${DOCKER_VOL_")
        for svc in ("ac-authserver", "ac-db-import")
        for m in services[svc]
    )


@pytest.mark.parametrize(
    "script_name",
    ["install-wow-wotlk.sh", "install-wow-wotlk-fedora.sh", "install-wow-wotlk-ubuntu.sh"],
)
def test_the_relabel_also_runs_on_the_branch_that_reuses_an_existing_build(
    script_name: str,
) -> None:
    """The recovery path needs it most, and had it least.

    Found by review: the relabel was called only where a fresh build is made. The
    branch that finds compiled images already present - which is exactly how a
    user recovers an install whose labels were reset - ran `docker compose up`
    with no relabel and no override rewrite, so the only recovery left was the
    2-4 hour rebuild the change exists to avoid. An override written by an older
    build carries no `:z` either, so on that path the relabel is the only thing
    that can help.
    """
    script = (
        Path(__file__).resolve().parents[1] / "catalog" / "installers" / "wow-wotlk" / script_name
    )
    text = script.read_text(encoding="utf-8")
    reuse = text[text.index("Skipping compile") : text.index("Skipping compile") + 1200]
    reuse = reuse[: reuse.index("return 0")]
    assert (
        'selinux_label_for_containers "$SERVER_DIR"' in reuse
    ), f"{script_name}: the reuse branch brings the stack up without relabelling"
    assert reuse.index('selinux_label_for_containers "$SERVER_DIR"') < reuse.index(
        "docker compose up"
    ), f"{script_name}: the relabel must run BEFORE the stack comes up"


def test_installer_refuses_a_reserved_folder_before_asking_for_a_password(
    tmp_path: Path,
) -> None:
    """The refusal has to land BEFORE Docker provisioning, not after it.

    The scripts refuse this set themselves - `case "$SERVER_DIR" in
    /|"$HOME"|/home|/root|/tmp|...` - but they do it after their own sudo prompt
    and after Docker discovery. On a clean Fedora 44 box (2026-08-25) picking
    the home folder cost a sudo password typed into Yu'lon's own dialog and a
    wait, and only then said "Cannot use '/home/pk' as the install location".

    Adding the mirror to `platform.server_dir_problem()` was not enough on its
    own: `Installer.preflight()` never consults `preflight.gather()` - that
    belongs to the native engine - so the mirror was dead code for every Linux
    install until this call site existed. `calls == []` is the assertion that
    matters; a refusal that still shelled out has not saved the user anything.
    """
    entry = load_catalog().get("wow-wotlk")
    calls: list[dict[str, object]] = []
    # A real script on disk, so the refusal under test is reached rather than
    # the "install script not found" one standing in for it.
    script = tmp_path / "wow-wotlk" / "install-wow-wotlk.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        "#!/usr/bin/env bash" + chr(10) + "exit 0" + chr(10),
        encoding="utf-8",
    )
    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: True,
        interact=_fake_interact(calls),  # type: ignore[arg-type]
        platform_id=lambda: "linux",
    )
    with pytest.raises(InstallerError, match="home folder itself"):
        list(installer.run(InstallOptions(server_dir=Path.home())))
    assert calls == []


def test_installer_refuses_a_platform_its_script_cannot_run(tmp_path: Path) -> None:
    """Roadmap 6.1: an off-Linux click is refused BEFORE any subprocess starts.

    Asked of TBC rather than WotLK since 6.2: WotLK is now installable on macOS
    through the native engine, so it is no longer an example of an entry that
    is not installable off Linux. The refusal itself is unchanged.
    """
    entry = load_catalog().get("wow-tbc")
    assert entry.install.platforms == ("linux",)
    assert entry.install.supports("linux") is True
    assert entry.install.supports("macos") is False
    calls: list[dict[str, object]] = []
    installer = _installer(
        entry,
        installers_root=tmp_path,
        docker_check=lambda: True,
        interact=_fake_interact(calls),  # type: ignore[arg-type]
        platform_id=lambda: "macos",
    )
    with pytest.raises(UnsupportedPlatformError, match="cannot be installed on macOS"):
        list(installer.run())
    assert calls == []  # nothing ran — not even a script-exists check reached bash
    # It is an InstallerError too, so existing handlers keep working.
    assert issubclass(UnsupportedPlatformError, InstallerError)


def test_unsupported_message_names_the_platform_and_the_requirement() -> None:
    entry = load_catalog().get("wow-tbc")
    message = installer_module.unsupported_platform_message(entry, "windows")
    assert "WoW TBC" in message and "Windows" in message and "Linux" in message
    assert "Nothing was started" in message


def test_platform_names_reads_as_english() -> None:
    """6.2 widens `platforms` to two entries — the copy must not become "linux, macos"."""
    assert installer_module.platform_names(["linux"]) == "Linux"
    assert installer_module.platform_names(["linux", "macos"]) == "Linux or macOS"
    assert (
        installer_module.platform_names(["linux", "macos", "windows"]) == "Linux, macOS or Windows"
    )
    assert installer_module.platform_names([]) == "another platform"
    assert installer_module.platform_names(["haiku"]) == "haiku"  # unknown id passes through


@needs_bash
def test_failure_message_carries_the_scripts_own_last_words(tmp_path: Path) -> None:
    """Roadmap 6.1: a failed install must not be reported as a bare exit status."""
    script = tmp_path / "wow-wotlk" / "install-wow-wotlk.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        "#!/bin/bash\necho 'Checking system...'\necho 'ERROR: needs 20GB free, found 2GB' >&2\n"
        "exit 4\n",
        encoding="utf-8",
    )
    installer = _installer(
        entry=load_catalog().get("wow-wotlk"), installers_root=tmp_path, docker_check=lambda: True
    )
    lines: list[str] = []
    with pytest.raises(InstallerError) as caught:
        for line in installer.run():
            lines.append(runner.strip_ansi(line).strip())
    message = str(caught.value)
    assert "exited with status 4" in message
    assert "It last said:" in message
    assert "ERROR: needs 20GB free, found 2GB" in message  # the script's real error
    assert "ERROR: needs 20GB free, found 2GB" in lines  # and it was streamed live too


# -- upstream 1.4.4: the docker-group consent prompt --------------------------

DOCKER_GROUP_PROMPT = "Add 'pk' to the docker group (grants root-equivalent access)? (y/n): "


def test_the_docker_group_question_is_put_to_the_user_not_answered_by_a_rule() -> None:
    """Upstream 1.4.4 removed the NOPASSWD sudoers rule and made group membership consented.

    Our `(y/n)` catch-all would have answered "y" and granted root-equivalent
    access with nobody asked — defeating the security change while syncing it.
    Neither canned answer is defensible: "y" grants silently, and "n" leaves the
    user outside the docker group so the launcher's own `docker` calls fail.
    """
    asked: list[str] = []
    respond = installer_module.make_responder(
        installer_module.InstallOptions(),
        ask=lambda prompt: (asked.append(prompt), "y")[1],
    )
    assert respond(DOCKER_GROUP_PROMPT) == "y"
    assert asked == [DOCKER_GROUP_PROMPT.strip()], "the user was not asked"


def test_a_dismissed_dialog_is_not_consent() -> None:
    """Cancelling the question must not read as yes."""
    respond = installer_module.make_responder(
        installer_module.InstallOptions(), ask=lambda _prompt: None
    )
    assert respond(DOCKER_GROUP_PROMPT) == "n"


def test_without_a_prompter_the_privilege_change_is_declined() -> None:
    """The CLI harness has no dialog. Refusing is recoverable and visible; granting is not."""
    respond = installer_module.make_responder(installer_module.InstallOptions())
    assert respond(DOCKER_GROUP_PROMPT) == "n"


def test_the_warning_paragraph_above_the_question_does_not_trigger_a_dialog() -> None:
    """The script prints several lines about the docker group before asking.

    The rule requires the `(y/n)` suffix so it matches the question and not the
    explanation — otherwise the dialog opens on prose nobody is waiting on.
    """
    asked: list[str] = []
    respond = installer_module.make_responder(
        installer_module.InstallOptions(),
        ask=lambda prompt: asked.append(prompt) or "y",  # type: ignore[func-returns-value]
    )
    assert respond("  the installer can add your user to the docker group.") is None
    assert respond("  To let you run docker without typing sudo each time,") is None
    assert asked == []


def test_the_consent_rule_wins_over_the_yes_no_catch_all() -> None:
    """Order matters: `(y/n)` sits below it and would otherwise answer first."""
    patterns = [rule.pattern for rule in installer_module.PROMPT_RULES]
    consent = next(i for i, p in enumerate(patterns) if "docker group" in p)
    catch_all = next(i for i, p in enumerate(patterns) if p == r"\(y/n\)")
    assert consent < catch_all


def _shipped_question(script_name: str, needle: str) -> str:
    """The exact line `ask_yes_no` prints for a question in a SHIPPED script.

    Read out of the file rather than transcribed here. A rule that is only ever
    matched against a copy of the wording keeps passing on the day the script
    is reworded — which is the day the catch-all quietly takes the question
    back.
    """
    from yulon import resources

    text = (resources.installers_dir() / "wow-wotlk" / script_name).read_text(encoding="utf-8")
    found = re.search(rf'ask_yes_no "([^"]*{needle}[^"]*)"', text)
    assert found, f"{script_name} no longer asks anything containing {needle!r}"
    return f"{found.group(1)} (y/n): "  # exactly what ask_yes_no() echoes


def test_removing_the_users_own_docker_is_declined_rather_than_assumed() -> None:
    """The Ubuntu script offers to `snap remove docker`; the catch-all said yes.

    That takes away a Docker the user installed themselves, and every container
    and volume on it, because the installer cannot drive snap Docker. Declining
    costs an exit carrying the script's own instruction to remove it by hand.
    """
    prompt = _shipped_question("install-wow-wotlk-ubuntu.sh", "snap Docker")
    assert make_responder(InstallOptions())(prompt) == "n"


def test_rebooting_the_machine_is_put_to_the_user_not_answered_by_the_catch_all() -> None:
    """Immutable Fedora's rpm-ostree path reboots 10 s after a yes.

    The catch-all answered "y" — so a Bazzite user pressing Install had their
    machine rebooted from inside the launcher, unasked. "n" is not the answer
    either: the script then exits 0 having installed nothing.
    """
    prompt = _shipped_question("install-wow-wotlk-fedora.sh", "rpm-ostree")
    asked: list[str] = []
    respond = make_responder(InstallOptions(), ask=lambda prompt: (asked.append(prompt), "y")[1])
    assert respond(prompt) == "y"
    assert asked == [prompt.strip()], "the reboot was decided without asking"
    # And with no dialog to ask through (the CLI harness) it is declined.
    assert make_responder(InstallOptions())(prompt) == "n"


def test_the_shipped_installers_no_longer_write_a_passwordless_sudo_rule() -> None:
    """Upstream 1.4.4's actual security change, asserted against the files we ship.

    The docker group already grants root-equivalent access, so a
    `/etc/sudoers.d/docker-nopasswd` rule only added attack surface. We shipped
    it until this sync.
    """
    from yulon import resources

    scripts = sorted((resources.installers_dir() / "wow-wotlk").glob("install-*.sh"))
    assert scripts, "no installers found"
    for script in scripts:
        text = script.read_text(encoding="utf-8", errors="replace")
        assert "sudo tee /etc/sudoers.d/docker-nopasswd" not in text, script.name
        assert "docker_group_consent" in text, f"{script.name} is behind upstream 1.4.4"


# --------------------------------------------------------- one docker probe, not two
# `installer.docker_available()` was `runner.run(["docker", "info"])` — a second
# copy of `platform.docker_ready()` (style-guide §4) that never learned about
# `docker_programs()`. So `preflight()` could refuse an install with "Docker
# isn't available" on the exact Windows box where `ensure_docker()` had, seconds
# earlier, proved that it was.


def test_the_install_gate_and_the_provisioner_ask_the_same_question() -> None:
    """One function, so the two can no longer disagree — the duplicate is gone."""
    import inspect

    assert not hasattr(installer_module, "docker_available")
    default = inspect.signature(Installer.__init__).parameters["docker_check"].default
    assert default is platform.docker_ready


def test_the_gate_sees_a_docker_that_is_only_on_the_registry_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point of collapsing them: the gate now tries the off-PATH exe too.

    Modelled as the real Windows failure — the plain name cannot be started at
    all, only the absolute path answers — with no `docker_check` override, so
    the default the constructor picked is what is under test.
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
    gate = Installer(load_catalog().get("wow-wotlk"), installers_root=tmp_path)._docker_check
    assert gate() is True
    assert tried == ["docker", exe]


def test_no_installer_escalates_privileges_without_asking() -> None:
    """Roadmap Phase 6 preamble: no silent escalation of host privileges.

    Adding a user to the `docker` group is a root-equivalent grant — `docker run
    -v /:/mnt --rm -it alpine chroot /mnt sh` edits any file on the host — so
    every script has to ask first. An audit on 2026-08-24 found three that did
    not: TBC, Vanilla and Tortoise each ran `usermod -aG docker` with no consent
    and no warning, having been written before the rule existed.

    A grep rather than a run, because these scripts install operating-system
    packages and cannot be executed in a test. It is worth having anyway: the
    failure it catches is a line silently reappearing in a 2000-line shell
    script, which is exactly what nobody re-reads.
    """
    from yulon import resources

    scripts = sorted(resources.installers_dir().rglob("install-*.sh"))
    assert len(scripts) >= 5, f"expected the catalog's installers, found {scripts}"

    ungated: list[str] = []
    sudoers: list[str] = []
    for script in scripts:
        for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
            bare = line.strip()
            if bare.startswith("#"):
                continue
            if "usermod -aG docker" in bare and "docker_group_consent &&" not in bare:
                ungated.append(f"{script.name}:{number}")
            if "sudoers" in bare or "NOPASSWD" in bare:
                sudoers.append(f"{script.name}:{number}")

    assert not ungated, f"the docker group is joined without consent at: {ungated}"
    # Forbidden outright, not merely gated: membership already is root, so the
    # rule buys nothing and is pure attack surface.
    assert not sudoers, f"a passwordless sudo rule is written at: {sudoers}"


def test_every_installer_asks_where_to_install() -> None:
    """The folder the user picked only reaches a script that asks for it.

    `InstallOptions.server_dir` has exactly one channel into a script: the
    `Install path:` rule in `PROMPT_RULES` types it in when the script asks.
    `script_env()` does not export it, and `run()` passes no arguments — so a
    script that never prints that prompt silently installs into its own
    hardcoded `$HOME/<default>` and the picker is decoration.

    Reported from a Steam Deck (2026-08-28): a tester made
    `~/wow-server-tortoise`, chose it, and watched Tortoise install into
    `~/tortoise-wow-server`. TBC and Vanilla had the same hole; only the WotLK
    scripts had ever grown `choose_install_dir()`. Worse than a wrong folder:
    `catalog_view._on_run_finished()` then looks for a compose file in the
    folder the user chose, finds none, and calls a good install failed.

    A grep rather than a run, for the reason the docker-group audit above gives:
    these scripts install OS packages and cannot be executed in a test.
    """
    from yulon import resources

    rule = next(r for r in PROMPT_RULES if "Install path" in r.pattern)
    prompt = re.compile(rule.pattern, re.IGNORECASE)

    scripts = sorted(resources.installers_dir().rglob("install-*.sh"))
    assert len(scripts) >= 5, f"expected the catalog's installers, found {scripts}"

    silent = [
        script.name
        for script in scripts
        if not any(prompt.search(line) for line in script.read_text(encoding="utf-8").splitlines())
    ]
    assert not silent, (
        f"these installers never print the prompt {rule.pattern!r}, so the folder "
        f"the user picked is discarded: {silent}"
    )


def test_every_installer_calls_choose_install_dir_before_it_uses_the_folder() -> None:
    """Printing the prompt is half of it; the answer has to reach the install.

    The test above is satisfied by a script that defines `choose_install_dir()`
    and never runs it — the prompt would be in the file, the app would never see
    it, and `SERVER_DIR` would keep its hardcoded default. So this asserts the
    call happens in the MAIN block, and that it comes before every other
    top-level call whose function reads `$SERVER_DIR`. Order is the half with
    teeth: placed after `show_summary` the summary names the wrong folder, and
    placed after the existing-install check the script offers to `rm -rf` it.

    Only top-level call order can be checked this way — a `$SERVER_DIR` inside a
    function definition runs when the function is CALLED, not where it is
    written, which is what made the first version of this test inert (its
    ordering branch passed a script whose call had been moved to the end).
    """
    from yulon import resources

    scripts = sorted(resources.installers_dir().rglob("install-*.sh"))
    assert len(scripts) >= 5, f"expected the catalog's installers, found {scripts}"

    problems: list[str] = []
    for script in scripts:
        lines = script.read_text(encoding="utf-8").splitlines()
        bodies = _shell_function_bodies(lines)
        # The MAIN block: bare top-level calls, in the order bash runs them.
        in_a_body = {n for span in bodies.values() for n in span}
        calls = [
            (n, line.strip())
            for n, line in enumerate(lines, 1)
            if re.fullmatch(r"[a-z_][a-z0-9_]*", line.strip()) and n not in in_a_body
        ]
        chosen = next((n for n, name in calls if name == "choose_install_dir"), None)
        if chosen is None:
            problems.append(f"{script.name}: never calls choose_install_dir")
            continue
        for number, name in calls:
            if number >= chosen or name not in bodies:
                continue
            if any("$SERVER_DIR" in lines[n - 1] for n in bodies[name]):
                problems.append(
                    f"{script.name}: {name}() uses $SERVER_DIR at line {number}, "
                    f"before it is chosen at line {chosen}"
                )

    assert not problems, f"the chosen folder is not in effect where it is used: {problems}"


def _shell_function_bodies(lines: list[str]) -> dict[str, range]:
    """`name -> the 1-based line numbers of its body`, for `name() {` at column 0.

    Good enough for these scripts and no more: they are written in one style,
    every definition opens with `name() {` unindented and closes with a `}`
    unindented. A parser would be the wrong trade for a shape that has held
    across six installers.
    """
    bodies: dict[str, range] = {}
    opened: tuple[str, int] | None = None
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([a-z_][a-z0-9_]*)\(\) \{", line)
        if match and opened is None:
            opened = (match.group(1), number)
        elif line == "}" and opened is not None:
            bodies[opened[0]] = range(opened[1], number + 1)
            opened = None
    return bodies


def test_no_installer_refuses_to_run_over_free_space_on_the_wrong_disk() -> None:
    """`check_system()` probes $HOME; the server files may not go there.

    `choose_install_dir()` exists so the server files can live on an SD card or
    an external drive — it says so, in those words, and then probes free space
    at the folder that was actually chosen. But `check_system()` runs first and
    hard-exits when $HOME has less than 15-20 GB, so on a 64 GB Steam Deck the
    installer offers the SD card and then refuses the install because the
    internal disk is full. The prompt and the gate contradict each other inside
    one run.

    $HOME is still worth a word — Docker's images go there whatever the user
    picks — so the check stays and becomes a warning. The authority on whether
    there is room for the server files is `choose_install_dir()`, which is the
    only one of the two that knows where they are going.
    """
    from yulon import resources

    scripts = sorted(resources.installers_dir().rglob("install-*.sh"))
    assert len(scripts) >= 5, f"expected the catalog's installers, found {scripts}"

    refusing: list[str] = []
    for script in scripts:
        lines = script.read_text(encoding="utf-8").splitlines()
        probe = next(
            (n for n, line in enumerate(lines) if 'df -BG "$HOME"' in line),
            None,
        )
        if probe is None:
            continue
        # From the probe to the `fi` that closes the branch testing it.
        for line in lines[probe:]:
            if line.strip() == "fi":
                break
            if re.fullmatch(r"\s*exit\s+\d+", line):
                refusing.append(f"{script.name}:{probe + 1}")
                break

    assert not refusing, (
        "these installers abort on free space in $HOME, which is not where the "
        f"server files necessarily go: {refusing}"
    )
