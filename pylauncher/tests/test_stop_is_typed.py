"""A Stop that takes effect says so by its type, and anything else after a Stop is a failure (T250).

Until T250 `LogPanel` read every exception that arrived after Stop as the Stop
taking effect, by timing alone, so a real failure that landed in the same moment
was shown as "cancelled". Now a stopped job is a clean cancel only when what
ended it carries `yulon.after_stop.StopTookEffect`, on itself or on an exception
it was raised `from`. These tests hold the two halves: every sentence in the tree
that says a Stop happened is raised as such a type, and a real failure racing a
Stop is shown as the failure.
"""

from __future__ import annotations

import ast
import importlib
import re
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND
from tests.support_native import Recorder, engine
from tests.support_stop import stop_when
from yulon import docker, git, platform, runner
from yulon.after_stop import StopTookEffect, TrueAfterStop, stop_took_effect
from yulon.catalog import native
from yulon.catalog.installer import InstallOptions, InstallStopped, ReadyWaitStopped
from yulon.controller_wow_tortoise import botdash
from yulon.ui.widgets.log_panel import STOPPED_THEN_FAILED

YULON = Path(native.__file__).resolve().parents[1]

SAYS_A_STOP = re.compile(
    r"(?<![Nn]othing )was stopped|(?:^|\. )Stopped before|was cancelled"
    r"|Stop was pressed|Stop now anyway\" was pressed"
)
"""How a sentence in this tree says a Stop happened. "Nothing was stopped" says the opposite."""

NOT_THE_STOP = {
    "client_packs.py::_download": (
        "a client-pack download: its Stop and its stall watchdog are said in its own dialog, "
        "never in a log panel"
    ),
    "selfupdate/apply.py::_stop_if_cancelled": "the app's own update, said in its own dialog",
    "selfupdate/fetch.py::download": "the app's own update, said in its own dialog",
    "selfupdate/fetch.py::_refuse_if_stalled": "the app stopped a stalled download; no Stop",
}
"""Sentences that mention a stop and are not the player's Stop taking effect, with why."""


CONSTANT = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _said(call: ast.Call, module: object) -> str:
    """Every word a raise hands its type: literals, and constants named or reached by attribute.

    A sentence kept in a module constant (`ReadyWaitStopped(READY_WAIT_STOPPED)`)
    is resolved through the imported module, so a stop sentence cannot hide
    behind a name (cold review of 4cc5f731).
    """
    parts: list[str] = []
    for sub in ast.walk(call):
        if sub is call.func:
            continue
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts.append(sub.value)
        named = sub.attr if isinstance(sub, ast.Attribute) else getattr(sub, "id", None)
        if isinstance(sub, ast.Name | ast.Attribute) and named and CONSTANT.match(named):
            try:
                value = eval(ast.unparse(sub), vars(module))  # noqa: S307 - this tree's names
            except Exception:  # noqa: BLE001 - a local, not a module constant
                continue
            if isinstance(value, str):
                parts.append(value)
    return " ".join(parts)


def _module(rel: str) -> object:
    return importlib.import_module("yulon." + rel[:-3].replace("/", "."))


def _stop_sentences() -> list[tuple[str, str]]:
    """(site, raised type expression) for every `raise` whose sentence says a Stop happened."""
    found: list[tuple[str, str]] = []
    for path in sorted(YULON.rglob("*.py")):
        rel = path.relative_to(YULON).as_posix()
        if rel.endswith("__main__.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module(rel)

        def visit(node: ast.AST, where: str, rel: str = rel, module: object = module) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    visit(child, child.name)
                    continue
                if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                    names = {
                        sub.func.id
                        for sub in ast.walk(child.exc)
                        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    }
                    if (
                        SAYS_A_STOP.search(_said(child.exc, module))
                        or "_cancelled_message" in names
                    ):
                        found.append((f"{rel}::{where}", ast.unparse(child.exc.func)))
                visit(child, where)

        visit(tree, "<module>")
    return found


def _resolve(site: str, expression: str) -> object:
    return eval(expression, vars(_module(site.split("::")[0])))  # noqa: S307 - this tree's names


def test_every_sentence_that_says_a_stop_happened_is_raised_as_a_stop() -> None:
    """The enumerating half: a new "was stopped" raised as a plain error fails here, not on screen.

    Each one is either a type carrying `StopTookEffect` (the Stop itself), one
    carrying `TrueAfterStop` (a sentence about what the Stop left, shown after
    it), or a site in `NOT_THE_STOP` saying why it is not the player's Stop.
    """
    sites = _stop_sentences()
    assert len(sites) >= 15, f"the walk found too few sites to be the real one: {sites}"
    unmarked = [
        (site, expression)
        for site, expression in sites
        if site not in NOT_THE_STOP
        and not issubclass(
            _resolve(site, expression), StopTookEffect | TrueAfterStop  # type: ignore[arg-type]
        )
    ]
    assert unmarked == [], unmarked
    assert (
        "catalog/native.py::wait_for_ready",
        "ReadyWaitStopped",
    ) in sites, "a sentence kept in a module constant was not seen"
    stale = set(NOT_THE_STOP) - {site for site, _ in sites}
    assert stale == set(), f"exemptions that no longer name a site: {stale}"


def test_the_stop_types_carry_the_mark_and_none_says_it_left_something() -> None:
    """Each kind of Stop has its own type, and none is also a failure kept after a Stop."""
    for kind in (
        runner.StreamEnded,
        git.GitStopped,
        docker.StopAbandoned,
        InstallStopped,
        ReadyWaitStopped,
        botdash.SwitchStopped,
    ):
        assert issubclass(kind, StopTookEffect), kind
        assert not issubclass(kind, TrueAfterStop), kind


def test_the_mark_is_followed_through_from_and_not_through_context() -> None:
    stop = runner.StreamEnded(143, ["make"])
    wrapped = RuntimeError("the build failed")
    wrapped.__cause__ = stop
    assert stop_took_effect(wrapped) is True
    only_context = RuntimeError("the cleanup failed")
    only_context.__context__ = stop
    assert stop_took_effect(only_context) is False
    assert stop_took_effect(OSError("disk full")) is False


def test_a_crash_loop_seen_as_stop_lands_in_an_installs_ready_wait_is_the_crash_loop(
    qapp: object, tmp_path: Path
) -> None:
    """T250 at its sharpest: the verdict is a failure of the server's own, and the Stop did not
    cause it. The install says the crash loop, under "Stopped", and not "cancelled"."""
    rec = Recorder()
    reached = threading.Event()
    looks = iter(range(10_000))

    def held(spec: object, ready: docker.ReadySpec) -> bool:
        reached.set()
        assert ready.cancel is not None and ready.cancel.wait(HANG_BOUND)
        return False

    def world(spec: object) -> native.WorldOutput:
        restarts = 0 if next(looks) == 0 else 9
        return native.WorldOutput(text="loading", restarts=restarts, status="running")

    made = engine(rec, wait_ready=held, world_output=world)
    options = InstallOptions(server_dir=tmp_path / "server")
    panel, finished = stop_when(
        lambda cancel: made.run(options, cancel=cancel), reached, "the install's ready wait"
    )

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert "crash loop" in header, header
    assert native.READY_WAIT_STOPPED not in header, header
    assert finished and finished[0][0] is False, finished


def test_stop_while_docker_is_being_set_up_is_a_clean_cancel(qapp: object, tmp_path: Path) -> None:
    """A Stop during provisioning comes back as a report that is not ready, never as an error.

    Read as a refusal it said "Docker is not available" -- or, Stop pressed on
    the docker-group question, that the player had declined it. The install
    asks the Stop first.
    """
    rec = Recorder()
    reached = threading.Event()

    def provisioning(
        *, cancel: threading.Event | None = None, ask: object = None
    ) -> platform.ProvisionReport:
        reached.set()
        assert cancel is not None and cancel.wait(HANG_BOUND), "Stop never reached provisioning"
        return platform.ProvisionReport(platform="linux", docker_ready=False)

    made = engine(rec, docker_ready=lambda: False, ensure_docker=provisioning)
    options = InstallOptions(server_dir=tmp_path / "server")

    def press(cancel: threading.Event) -> Iterator[str]:
        return made.run(options, cancel=cancel)

    panel, finished = stop_when(press, reached, "provisioning")

    assert panel.status_text() == "cancelled", panel.status_text()
    assert finished == [(True, "stopped")], finished


def test_a_build_that_exited_on_its_own_under_a_stop_reads_as_the_stop(tmp_path: Path) -> None:
    """`_check_run()` asks the cancel before the exit status, as `extract._conclude()` does.

    A compile another route killed while Stop was pressed exits with a code of
    its own, not `CANCELLED_RETURNCODE`; read as "the build failed (exit 143)"
    it would be a refusal for a button the player pressed.
    """
    rec = Recorder()
    cancel = threading.Event()

    def killed(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        cancel.set()
        return docker.AttachedRun(143, ("compiling", "Terminated"))

    with pytest.raises(InstallStopped) as stopped:
        list(
            engine(rec, build=killed).run(InstallOptions(server_dir=tmp_path / "s"), cancel=cancel)
        )
    assert str(stopped.value).startswith("the build was stopped."), stopped.value
