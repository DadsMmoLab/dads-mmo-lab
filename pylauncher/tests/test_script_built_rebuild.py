"""T804: Rebuild for a WotLK server the old Dad's MMO Lab bash script built inside WSL.

The folder carries no `.yulon-install.json`, so the ordinary Rebuild refuses it. The owner chose
the narrow route (option B): rebuild it the way it was built, after one question, with no marker
written, no file touched in the folder, the server not taken over, and never a `compose down`
or a `-v`. Every rule `script_built.check()` applies has a test below that goes red when the
rule is removed. Nothing here reaches real docker, git or WSL: every answer is a seam.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.conftest import wait_for_panel
from tests.support_native import Recorder, engine
from tests.test_controller_view import WOTLK, _Ps, _services
from yulon import docker, platform, runner, wsl
from yulon.catalog import composegen, native, script_built
from yulon.catalog.installer import InstallerError, InstallOptions, InstallStopped
from yulon.controller import Controller
from yulon.install_wiring import ScriptBuiltRebuild
from yulon.ownership import Ownership
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

PROJECT = "wow-server-playerbots"
LINUX = "/home/u/wow-server-playerbots"
CORE = "https://github.com/mod-playerbots/azerothcore-wotlk.git"
MODULE = "https://github.com/mod-playerbots/mod-playerbots.git"

# What the script's `cat > docker-compose.override.yml << 'OVERRIDE'` writes (install-wow-wotlk.sh,
# and the same text in the -ubuntu variant).
OVERRIDE = """services:
  ac-worldserver:
    build:
      context: .
      target: worldserver
    volumes:
      - ./modules:/azerothcore/modules
    environment:
      AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES: "1"
      AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"
      AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "1600"
      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "2000"
  ac-authserver:
    build:
      context: .
      target: authserver
  ac-db-import:
    build:
      context: .
      target: db-import
  ac-client-data-init:
    build:
      context: .
      target: client-data
"""
# install-wow-wotlk-fedora.sh: the same file with SELinux's label on the mount.
FEDORA_OVERRIDE = OVERRIDE.replace("/azerothcore/modules\n", "/azerothcore/modules:Z\n")
UPSTREAM_COMPOSE = "services:\n  ac-database:\n    image: mysql:8.4\n"
FIXTURE = Path(__file__).parent / "data" / "wotlk-compose-config-script.json"


def config() -> dict[str, Any]:
    """The real `docker compose config` of a script-built server, with the project name it lost."""
    loaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {"name": PROJECT, **copy.deepcopy(loaded)}


def folder(tmp_path: Path, override: str = OVERRIDE) -> Path:
    server = tmp_path / "wow-server-playerbots"
    server.mkdir(parents=True)
    (server / composegen.BASE_FILE).write_text(UPSTREAM_COMPOSE, encoding="utf-8")
    (server / composegen.OVERRIDE_FILE).write_text(override, encoding="utf-8")
    (server / "modules" / "mod-playerbots").mkdir(parents=True)
    return server


class Log:
    """What the fake daemon was asked, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.build_run = docker.AttachedRun(0)
        self.up_run = docker.AttachedRun(0)

    def of(self, kind: str) -> list[Any]:
        return [what for k, what in self.events if k == kind]


def seams(log: Log | None = None, **over: Any) -> script_built.ScriptBuiltSeams:
    log = log if log is not None else Log()

    def remote_url(path: Path) -> str | None:
        return MODULE if path.name == "mod-playerbots" else CORE

    def build(server_dir: Path, files: Sequence[str], sink: Any, cancel: Any) -> docker.AttachedRun:
        log.events.append(("build", (server_dir, tuple(files))))
        sink("compiling")
        return log.build_run

    def images_built(refs: Sequence[str]) -> bool | None:
        log.events.append(("images", tuple(refs)))
        return True

    def run_attached(argv: list[str], cwd: Path, sink: Any, cancel: Any) -> docker.AttachedRun:
        log.events.append(("up", (list(argv), cwd)))
        sink("starting")
        return log.up_run

    fields: dict[str, Any] = {
        "linux_path": lambda path, distro: LINUX,
        "claim": lambda path: Ownership.UNCLAIMED,
        "remote_url": remote_url,
        "file_unmodified": lambda path, name: True,
        "compose_config": lambda path: config(),
        "container_exists": lambda name: True,
        "container_project": lambda name: PROJECT,
        "container_working_dir": lambda name: LINUX,
        "world_running": lambda name: False,
        "daemon_ready": lambda: True,
        "build": build,
        "images_built": images_built,
        "run_attached": run_attached,
    }
    fields.update(over)
    return script_built.ScriptBuiltSeams(**fields)


def verdict(server: Path, **over: Any) -> script_built.ScriptBuiltCheck:
    return script_built.check(server, "Ubuntu", seams=seams(**over))


def rebuilt(server: Path, log: Log, **over: Any) -> list[str]:
    return list(script_built.rebuild_as_built(server, "Ubuntu", seams=seams(log, **over)))


def refuses(server: Path, rule: str, **over: Any) -> str:
    answer = verdict(server, **over)
    assert answer.refusal is not None, f"rule {rule} did not refuse"
    assert f"rule {rule}" in answer.refusal, answer.refusal
    assert str(server) in answer.refusal, "the refusal must name the folder"
    return answer.refusal


# -- the happy path ------------------------------------------------------------------------


def test_a_folder_matching_every_rule_is_rebuilt_as_built(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    answer = script_built.check(server, "Ubuntu", seams=seams(log))
    assert answer == script_built.ScriptBuiltCheck(PROJECT, LINUX, None)

    said = rebuilt(server, log)

    kinds = [kind for kind, _ in log.events]
    assert kinds == ["build", "images", "up"], "compile, then check the tags, then start"
    assert log.of("build") == [(server, (composegen.BASE_FILE, composegen.OVERRIDE_FILE))]
    assert log.of("images") == [script_built.IMAGE_REFS]
    assert any("Done" in line for line in said)


@pytest.mark.parametrize("override", [OVERRIDE, FEDORA_OVERRIDE], ids=("bash", "fedora"))
def test_both_known_script_overrides_are_accepted(tmp_path: Path, override: str) -> None:
    server = folder(tmp_path, override)
    assert script_built.override_problem(override) is None
    assert verdict(server).refusal is None
    assert script_built.looks_script_built(server) is True


def test_the_refs_are_the_four_stock_names_the_script_tags() -> None:
    assert script_built.IMAGE_REFS == (
        "acore/ac-wotlk-worldserver:master",
        "acore/ac-wotlk-authserver:master",
        "acore/ac-wotlk-db-import:master",
        "acore/ac-wotlk-client-data:master",
    )


# -- the rules -----------------------------------------------------------------------------


def test_a_folder_that_is_not_in_the_distro_refuses(tmp_path: Path) -> None:
    server = folder(tmp_path)
    assert script_built.check(server, None, seams=seams()).refusal is not None
    refuses(server, "R1", linux_path=lambda path, distro: None)

    def other(path: Path, distro: str) -> str:
        raise platform.WslDistroMismatch("lives in another distro")

    assert "another distro" in refuses(server, "R1", linux_path=other)


def test_a_folder_with_an_install_record_never_takes_this_route(tmp_path: Path) -> None:
    server = folder(tmp_path)
    owned = refuses(server, "R2", claim=lambda path: Ownership.OWNED)
    assert "Yu'lon's own install record" in owned, "an owned folder takes the normal Rebuild"
    for old in (".dml-install.json", ".dml-migrate.json"):
        marked = folder(tmp_path / old.strip("."))
        (marked / old).write_text("{}", encoding="utf-8")
        assert old in refuses(marked, "R2")
        assert script_built.looks_script_built(marked) is False
    # A real record on disk keeps the question from being asked at all.
    (server / ".yulon-install.json").write_text("{}", encoding="utf-8")
    assert script_built.looks_script_built(server) is False


def test_an_unreadable_install_record_keeps_the_old_refusal(tmp_path: Path) -> None:
    server = folder(tmp_path)
    (server / native.STATE_FILE).write_text("{ not json", encoding="utf-8")
    real = script_built.ScriptBuiltSeams.in_wsl("Ubuntu")
    assert real.claim(server) is Ownership.UNKNOWN
    refuses(server, "R2", claim=real.claim)
    assert script_built.looks_script_built(server) is False
    # ... and the engine still says what it always said about the missing record.
    rec = Recorder()
    with pytest.raises(InstallerError, match="so Yu'lon has no record of building"):
        list(engine(rec).rebuild(InstallOptions(server_dir=server)))


@pytest.mark.parametrize(
    "extra",
    [composegen.BUILD_FILE, "compose.yml", "compose.yaml", "docker-compose.yaml"],
)
def test_a_build_overlay_or_a_yulon_marked_compose_file_refuses(tmp_path: Path, extra: str) -> None:
    server = folder(tmp_path)
    (server / extra).write_text("services: {}\n", encoding="utf-8")
    assert extra in refuses(server, "R3")
    assert script_built.looks_script_built(server) is False


@pytest.mark.parametrize("name", [composegen.BASE_FILE, composegen.OVERRIDE_FILE])
def test_a_yulon_marked_compose_file_refuses(tmp_path: Path, name: str) -> None:
    server = folder(tmp_path)
    path = server / name
    path.write_text(f"{composegen.GENERATED_MARKER}\n{path.read_text()}", encoding="utf-8")
    assert name in refuses(server, "R3")
    assert script_built.looks_script_built(server) is False


def test_a_missing_compose_file_refuses(tmp_path: Path) -> None:
    server = folder(tmp_path)
    (server / composegen.OVERRIDE_FILE).unlink()
    refuses(server, "R3")
    assert script_built.looks_script_built(server) is False


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(
            lambda t: t.replace("    build:", "    image: evil:1\n    build:", 1), id="image"
        ),
        pytest.param(
            lambda t: t.replace("    build:", '    ports:\n      - "1:1"\n    build:', 1),
            id="ports",
        ),
        pytest.param(
            lambda t: t + "  ac-extra:\n    build:\n      context: .\n      target: x\n",
            id="service",
        ),
        pytest.param(
            lambda t: t.replace("      - ./modules:", "      - /etc:/x\n      - ./modules:"),
            id="volume",
        ),
        pytest.param(
            lambda t: t.replace("AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN", "MYSQL_ROOT_PASSWORD"),
            id="env",
        ),
        pytest.param(
            lambda t: t.replace("    build:", "    command: sh\n    build:", 1), id="command"
        ),
        pytest.param(lambda t: t.replace("target: authserver", "target: worldserver"), id="target"),
        pytest.param(
            lambda t: t.replace(
                "context: .\n      target: authserver", "context: ..\n      target: authserver"
            ),
            id="context",
        ),
        pytest.param(lambda t: "version: '3'\n" + t, id="top-level"),
        pytest.param(lambda t: t.replace('"1"', '"1" # note', 1), id="inline-comment"),
    ],
)
def test_an_override_with_any_unknown_key_refuses(
    tmp_path: Path, edit: Callable[[str], str]
) -> None:
    text = edit(OVERRIDE)
    assert text != OVERRIDE
    server = folder(tmp_path, text)
    assert composegen.OVERRIDE_FILE in refuses(server, "R4")


def test_an_unreadable_override_refuses(tmp_path: Path) -> None:
    server = folder(tmp_path)
    (server / composegen.OVERRIDE_FILE).write_bytes(b"\xff\xfe not utf-8")
    refuses(server, "R4")


@pytest.mark.parametrize("answer", [False, None], ids=("edited", "unreadable"))
def test_an_edited_or_unreadable_upstream_compose_file_refuses(
    tmp_path: Path, answer: object
) -> None:
    server = folder(tmp_path)
    assert composegen.BASE_FILE in refuses(server, "R5", file_unmodified=lambda path, name: answer)


@pytest.mark.parametrize("which", ["core", "module", "core-unreadable", "module-unreadable"])
def test_a_checkout_of_another_repository_refuses(tmp_path: Path, which: str) -> None:
    server = folder(tmp_path)
    other = "https://github.com/someone/azerothcore-wotlk.git"

    def remote_url(path: Path) -> str | None:
        module = path.name == "mod-playerbots"
        if which == "core":
            return MODULE if module else other
        if which == "module":
            return other if module else CORE
        if which == "core-unreadable":
            return MODULE if module else None
        return None if module else CORE

    refuses(server, "R5", remote_url=remote_url)


def test_a_yulon_project_name_refuses(tmp_path: Path) -> None:
    server = folder(tmp_path)
    named = config()
    named["name"] = "yulon-wow-wotlk-0123456789ab"
    assert "yulon-wow-wotlk" in refuses(server, "R6", compose_config=lambda path: named)


def _changed(edit: Callable[[dict[str, Any]], None]) -> Callable[[Path], dict[str, Any]]:
    def read(path: Path) -> dict[str, Any]:
        loaded = config()
        edit(loaded)
        return loaded

    return read


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(
            lambda c: c["services"]["ac-worldserver"].update(image="acore/x:1"), id="image"
        ),
        pytest.param(lambda c: c["services"]["ac-database"].update(container_name="db"), id="name"),
        pytest.param(lambda c: c["volumes"].pop("ac-database"), id="volume"),
        pytest.param(lambda c: c.pop("name"), id="no-project"),
        pytest.param(lambda c: c.pop("services"), id="no-services"),
    ],
)
def test_a_compose_config_that_does_not_read_as_the_scripts_refuses(
    tmp_path: Path, edit: Callable[[dict[str, Any]], None]
) -> None:
    refuses(folder(tmp_path), "R6", compose_config=_changed(edit))
    refuses(folder(tmp_path / "again"), "R6", compose_config=lambda path: None)


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"container_project": lambda name: "other"}, id="project"),
        pytest.param({"container_project": lambda name: None}, id="no-project"),
        pytest.param({"container_project": lambda name: docker.UNREADABLE}, id="unreadable"),
        pytest.param({"container_working_dir": lambda name: "/srv/elsewhere"}, id="folder"),
        pytest.param(
            {"container_working_dir": lambda name: docker.UNREADABLE}, id="folder-unreadable"
        ),
    ],
)
def test_ac_containers_of_another_project_refuse(tmp_path: Path, over: dict[str, Any]) -> None:
    refuses(folder(tmp_path), "R7", **over)


def test_containers_that_do_not_exist_yet_are_not_foreign(tmp_path: Path) -> None:
    server = folder(tmp_path)
    assert verdict(server, container_exists=lambda name: False).refusal is None


@pytest.mark.parametrize("running", [True, None], ids=("running", "unknown"))
def test_a_running_or_unknown_world_refuses(tmp_path: Path, running: bool | None) -> None:
    refuses(folder(tmp_path), "R8", world_running=lambda name: running)


def test_a_docker_that_does_not_answer_refuses(tmp_path: Path) -> None:
    refuses(folder(tmp_path), "R9", daemon_ready=lambda: False)


def test_the_first_failing_rule_is_the_one_named(tmp_path: Path) -> None:
    """R2 and R4 both fail here; the answer is R2, and no docker was asked yet."""
    server = folder(tmp_path, "services: {}\n")
    asked: list[str] = []

    def never(*_a: object) -> bool:
        asked.append("docker")
        return True

    refuses(server, "R2", claim=lambda path: Ownership.OWNED, container_exists=never)
    assert asked == []


# -- the rebuild ---------------------------------------------------------------------------


def test_a_refused_folder_compiles_and_starts_nothing(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    with pytest.raises(InstallerError, match="rule R8"):
        rebuilt(server, log, world_running=lambda name: True)
    assert log.events == []


def test_a_failed_build_never_reaches_up(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    log.build_run = docker.AttachedRun(2, ("ERROR: compile failed",))
    with pytest.raises(InstallerError, match="left on its old build"):
        rebuilt(server, log)
    assert log.of("up") == [] and log.of("images") == []


def test_a_stopped_build_never_reaches_up(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    log.build_run = docker.AttachedRun(docker.CANCELLED_RETURNCODE)
    with pytest.raises(InstallStopped, match="old build"):
        rebuilt(server, log)
    assert log.of("up") == []


@pytest.mark.parametrize("built", [False, None], ids=("missing", "unknown"))
def test_missing_images_after_the_build_never_reach_up(tmp_path: Path, built: bool | None) -> None:
    server = folder(tmp_path)
    log = Log()
    with pytest.raises(InstallerError, match="stock image"):
        rebuilt(server, log, images_built=lambda refs: built)
    assert log.of("up") == []


def test_a_failed_start_says_the_build_is_kept(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    log.up_run = docker.AttachedRun(1, ("port is already allocated",))
    with pytest.raises(InstallerError, match="is compiled, but starting the server failed"):
        rebuilt(server, log)


def test_the_up_argv_never_pulls_builds_or_removes_volumes(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    rebuilt(server, log)
    ((argv, cwd),) = log.of("up")
    assert argv == ["compose", "up", "-d", "--pull", "never", "--no-build"]
    assert cwd == server
    every = [*argv, *log.of("build")[0][1]]
    for forbidden in ("down", "-v", "--volumes", "--build", "rm", "prune", "--remove-orphans"):
        assert forbidden not in every


def _snapshot(root: Path) -> dict[str, bytes | None]:
    found: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        found[str(path.relative_to(root))] = path.read_bytes() if path.is_file() else None
    return found


def test_nothing_in_the_folder_is_written_or_removed(tmp_path: Path) -> None:
    server = folder(tmp_path)
    before = _snapshot(server)
    rebuilt(server, Log())
    assert _snapshot(server) == before
    assert not (server / native.STATE_FILE).exists()
    assert not (server / ".yulon-install.json").exists()


def test_a_stop_during_the_start_is_a_stop(tmp_path: Path) -> None:
    server = folder(tmp_path)
    log = Log()
    log.up_run = docker.AttachedRun(docker.CANCELLED_RETURNCODE)
    with pytest.raises(InstallStopped, match="press Rebuild again"):
        rebuilt(server, log)


# -- the press -----------------------------------------------------------------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _wsl_view(
    ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, applies: bool = True
) -> tuple[ControllerView, dict[str, list[object]]]:
    monkeypatch.setattr(wsl, "distro_state", lambda distro: "running")
    ran: dict[str, list[object]] = {"ordinary": [], "as_built": []}
    services = _services(ps, tmp_path, [])
    services.controller = Controller(WOTLK.container_spec(), tmp_path, wsl_distro="Ubuntu")

    def ordinary(cancel: object = None) -> Iterator[str]:
        ran["ordinary"].append(cancel)
        yield "ordinary"

    def as_built(cancel: object = None) -> Iterator[str]:
        ran["as_built"].append(cancel)
        yield "as built"

    services.rebuild = ordinary
    services.script_built_rebuild = ScriptBuiltRebuild(lambda: applies, as_built)
    return ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline), ran


def _answer(monkeypatch: pytest.MonkeyPatch, answer: object) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> object:
        asked.append((title, text))
        return answer

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


@pytest.mark.parametrize(
    "answer",
    [QMessageBox.StandardButton.No, QMessageBox.StandardButton.NoButton],
    ids=("no", "closed"),
)
def test_declining_or_closing_the_question_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    view, ran = _wsl_view(ps, tmp_path, monkeypatch)
    asked = _answer(monkeypatch, answer)

    assert view.rebuild_server() is False

    assert [title for title, _ in asked] == [script_built.QUESTION_TITLE]
    assert ran == {"ordinary": [], "as_built": []}
    assert view.rebuild_log.running is False


def test_rebuild_press_on_a_script_built_wsl_server_asks_the_script_built_question(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, ran = _wsl_view(ps, tmp_path, monkeypatch)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)

    assert view.rebuild_server() is True
    wait_for_panel(view.rebuild_log)

    ((title, text),) = asked
    assert title == "Rebuild the way it was built?"
    assert text == script_built.question(tmp_path, "Ubuntu")
    assert "built by the old Dad's MMO Lab script, not by Yu'lon" in text
    assert "will not change, add or remove any file in that folder" in text
    assert "cannot be undone without a backup" in text
    assert str(tmp_path) in text and "Ubuntu" in text
    assert len(ran["as_built"]) == 1 and ran["ordinary"] == []


def test_a_folder_that_does_not_look_script_built_gets_the_ordinary_question(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, ran = _wsl_view(ps, tmp_path, monkeypatch, applies=False)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)

    assert view.rebuild_server() is True
    wait_for_panel(view.rebuild_log)

    ((title, _text),) = asked
    assert title != script_built.QUESTION_TITLE
    assert len(ran["ordinary"]) == 1 and ran["as_built"] == []


def test_the_route_is_wired_only_for_wotlk_inside_a_distro(tmp_path: Path) -> None:
    from yulon import install_wiring
    from yulon.catalog.catalog import load_catalog

    entry = load_catalog().get("wow-wotlk")
    assert install_wiring.script_built_rebuild_for_app(entry, tmp_path) is None
    others = [e for e in load_catalog().games if e.id != "wow-wotlk"]
    assert install_wiring.script_built_rebuild_for_app(others[0], tmp_path, wsl_distro="U") is None
    route = install_wiring.script_built_rebuild_for_app(entry, tmp_path, wsl_distro="U")
    assert route is not None
    # A folder that is not inside that distro never applies (and reads nothing).
    assert route.applies() is False


# -- the old refusal ----------------------------------------------------------------------


def test_the_old_refusal_names_the_rule_that_did_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = folder(tmp_path)
    rec = Recorder()
    # Inside a distro: the real check stops at R1 for a folder that is not a WSL path.
    with pytest.raises(InstallerError) as outside:
        list(engine(rec, distro="Ubuntu").rebuild(InstallOptions(server_dir=server)))
    assert "so Yu'lon has no record of building" in str(outside.value)
    assert "did not match a server built by the old Dad's MMO Lab script" in str(outside.value)
    assert "rule R1" in str(outside.value)

    # And whichever rule failed is the one it names.
    monkeypatch.setattr(
        script_built,
        "check",
        lambda *a, **k: script_built.ScriptBuiltCheck(
            "", "", f"{server} did not match (rule R4: x)."
        ),
    )
    with pytest.raises(InstallerError, match=r"rule R4: x"):
        list(engine(rec, distro="Ubuntu").rebuild(InstallOptions(server_dir=server)))

    # A server that is not in a distro gets the refusal it always got, with no clause.
    with pytest.raises(InstallerError) as plain:
        list(engine(rec).rebuild(InstallOptions(server_dir=server)))
    assert "rule R" not in str(plain.value)
