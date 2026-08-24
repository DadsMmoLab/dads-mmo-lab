"""Tests for the launcher entry point's headless provisioning mode.

`main.py` had no tests at all before this file. The GUI path is covered by the
packaging smoke test (`YULON_SMOKE_TEST`, which builds the window and exits);
what needed pinning is `--provision`, because its EXIT CODES are control flow
for something else. The clean-Windows harness reboots on 3 and stops on 2, so a
code that drifts does not produce a wrong message, it produces a harness that
reboots forever or gives up on a box that was fine.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import main
from yulon import platform


def _report(**kwargs: Any) -> platform.ProvisionReport:
    base: dict[str, Any] = {"platform": "windows"}
    base.update(kwargs)
    return platform.ProvisionReport(**base)


@pytest.fixture(autouse=True)
def _no_real_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here may install Docker on the machine running the tests."""

    def _refuse(*_args: Any, **_kwargs: Any) -> platform.ProvisionReport:
        raise AssertionError("ensure_docker() was called for real")

    monkeypatch.setattr(main.platform, "ensure_docker", _refuse)
    monkeypatch.setattr(main.platform, "docker_program", lambda: "docker")


def test_a_ready_daemon_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.platform, "ensure_docker", lambda **_k: _report(docker_ready=True))
    assert main.provision_headless() == main.PROVISION_READY == 0


def test_a_required_reboot_is_its_own_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wsl --install` forces a reboot on a box with no WSL, which this checkpoint is.

    It must not share an exit code with "needs a human": the harness reboots and
    runs another pass for one and stops for the other. Note `docker_ready` is
    True here as well — a reboot outranks it, because nothing after the reboot
    has been judged yet.
    """
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(docker_ready=True, reboot_required=True),
    )
    assert main.provision_headless() == main.PROVISION_REBOOT == 3


def test_a_daemon_that_never_came_up_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(manual_steps=("start Docker Desktop yourself",)),
    )
    assert main.provision_headless() == main.PROVISION_MANUAL == 2


def test_the_three_exit_codes_are_distinct() -> None:
    """They are a protocol. Two of them colliding is silent and total."""
    codes = {main.PROVISION_READY, main.PROVISION_MANUAL, main.PROVISION_REBOOT}
    assert len(codes) == 3


def test_the_report_is_one_parseable_line_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The harness greps one line out of a log that also carries human logging.

    A step whose text contains a newline is the case that breaks a naive
    emitter, and installers produce those — so it is the case tested.
    """
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(
            done=("downloaded the installer\nto C:\\x",),
            skipped=("start Docker Desktop: no exe",),
            docker_ready=False,
        ),
    )
    main.provision_headless()
    marked = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("YULON_PROVISION_JSON ")
    ]
    assert len(marked) == 1, "the harness needs exactly one marked line"
    payload = json.loads(marked[0][len("YULON_PROVISION_JSON ") :])
    assert payload["done"] == ["downloaded the installer\nto C:\\x"]
    assert payload["ok"] is False
    assert payload["docker_cli"] == "docker"
    assert set(payload) == {
        "platform",
        "done",
        "skipped",
        "manual_steps",
        "reboot_required",
        "docker_ready",
        "ok",
        "docker_cli",
        "docker_group",
    }
    # The consent outcome is part of the support payload: it is what tells
    # "the user declined root-equivalent access" apart from "provisioning
    # broke", and headless can only ever report the former.
    assert payload["docker_group"] == "not-applicable"


def test_an_unresolvable_docker_cli_is_reported_as_null(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The field that distinguishes "installed it" from "can now use it".

    That gap is Cross-cutting defect 3 and it is invisible from anywhere else in
    the report: every step can read as done while the process that ran them
    still cannot spell `docker`.
    """
    monkeypatch.setattr(main.platform, "docker_program", lambda: None)
    monkeypatch.setattr(main.platform, "ensure_docker", lambda **_k: _report(docker_ready=False))
    main.provision_headless()
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("YULON_PROVISION_JSON ")
    )
    assert json.loads(line[len("YULON_PROVISION_JSON ") :])["docker_cli"] is None


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["yulon", "--provision"], {}),
        (["yulon"], {"YULON_PROVISION": "1"}),
    ],
    ids=["flag", "environment"],
)
def test_main_takes_the_headless_path_without_building_a_window(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], env: dict[str, str]
) -> None:
    """Both spellings, and neither may import Qt.

    The environment variable exists because a scheduled task is a clumsy place
    to pass arguments; the flag exists because a support request should be one
    thing to type. Qt not being imported is the load-bearing half: this runs on
    a box that may have no display at all.
    """
    monkeypatch.setattr(main, "configure", lambda **_k: None)
    monkeypatch.setattr(main.sys, "argv", argv)
    # Cleared first: a developer with YULON_PROVISION exported would otherwise
    # make the flag case pass without the flag doing anything.
    monkeypatch.delenv("YULON_PROVISION", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def _boom() -> object:
        raise AssertionError("build_window() was called in headless provisioning mode")

    monkeypatch.setattr(main, "build_window", _boom)
    monkeypatch.setattr(main.platform, "ensure_docker", lambda **_k: _report(docker_ready=True))
    assert main.main() == 0


def test_the_report_line_survives_a_console_that_cannot_spell_the_step_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """This crashed a real clean-Windows run, at the moment it reported success.

    `platform`'s own step text contains an arrow, and the harness runs the frozen
    app as `yulon.exe --provision > log 2>&1`, which gives a cp1252 stdout. The
    first version of this function passed `ensure_ascii=False` for prettier
    output and died with UnicodeEncodeError right here -- after the run had
    already spent a 659 MB download (clean-box run, 2026-08-23).

    So the marked line has to be encodable by the narrowest console encoding it
    can plausibly meet, and the escaping has to be lossless: a harness that reads
    a mangled path is no better off than one that reads nothing.
    """
    step = r"downloaded the installer → C:\Users\pk\x.exe"
    monkeypatch.setattr(
        main.platform,
        "ensure_docker",
        lambda **_k: _report(done=(step,), docker_ready=True),
    )
    main.provision_headless()
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("YULON_PROVISION_JSON ")
    )
    line.encode("cp1252")  # the whole assertion: this is what raised
    payload = json.loads(line[len("YULON_PROVISION_JSON ") :])
    assert payload["done"] == [step], "the escaping lost or changed the step text"
