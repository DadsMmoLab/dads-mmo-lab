# Yu'lon Style Guide

> **Audience:** this document is written to be read by both **humans** and **LLM coding agents**
> working on `pylauncher/`. Every rule here is a hard constraint, not a preference, unless
> explicitly marked "recommended". If a change conflicts with this guide, fix the change — don't
> update the guide to match the change, unless the guide itself is the thing under review.
>
> This guide is downstream of `pyplan/README.md`. If the two conflict, `README.md` (the design
> doc) wins on *architecture*, and this document wins on *code style*. Flag the conflict rather
> than silently picking one.

---

## 1. Non-negotiable rules (read this section first)

1. **Strict separation of concerns.** Every module has exactly one job. See §3.
2. **DRY — Don't Repeat Yourself.** Shared behavior lives in one place (`runner.py`, `platform.py`,
   base controller classes), never copy-pasted per game. See §4.
3. **Call down / signal up.** Parents talk to children by calling their methods directly.
   Children never reach up into a parent or hold a parent reference — they emit a signal and let
   whoever is listening decide what to do. See §5.
4. **Never refer to a game by its full/trademarked name — acronyms only.** Never "Warcraft",
   always "WoW". Never "RuneScape", always "RS". See §6.
5. **All filenames are lowercase.** No exceptions.
6. **Python is fully typed.** Despite Python being dynamically typed by default, this codebase
   treats it as statically typed: every function signature, variable where it isn't obvious, and
   class attribute is annotated, and CI enforces it with a type checker. Untyped/`Any`-typed code
   is treated as a bug, not a style nit. See §2.
7. **`pyplan/roadmap.md` stays a clean plan — no notes, decision logs, or commentary.** See §9.

---

## 2. Modern Python Standards

Baseline: **Python 3.11+** (per `pyplan/README.md` §2). Beyond PEP 8:

- **This is a typed codebase, full stop.** Python doesn't enforce types at runtime by default —
  we do it anyway, deliberately, via annotations + a static checker. Nothing in `pylauncher/`
  should ship unannotated just because "Python doesn't require it."
- **Type hints everywhere.** All function signatures are fully annotated, including return types
  (including `-> None` where a function returns nothing — don't omit it). Annotate module-level
  and class-level variables whose type isn't obvious from an inline literal. Use
  `from __future__ import annotations` at the top of every module so annotations don't pay a
  runtime cost and forward references just work.
- **Static type checking is enforced in CI**, not just recommended. Use **`mypy`** or **`pyright`**
  (either is fine to start with; pick one and be consistent — `pyright` is the natural pairing if
  editors/agents are already using Pylance, since it's the same engine). Add it to CI alongside
  `pytest` from Phase 1 onward (`pyplan/README.md` §7) so type errors fail the build, the same as
  a failing test.
- **No `Any` as an escape hatch.** `typing.Any` is only acceptable at a genuine untyped boundary
  (e.g. immediately after `json.load()`, before converting into a dataclass/Pydantic model — see
  below) and should be narrowed away as close to that boundary as possible. Reaching for `Any`
  because a real type is inconvenient to write is not allowed — use `TypeAlias`, `Protocol`,
  generics, or a union instead.
- **No untyped `**kwargs`/`*args` without a `ParamSpec`/explicit types** unless wrapping a
  genuinely dynamic third-party API (e.g. some Qt signal plumbing) — and even then, type what you
  can.
- **`pathlib.Path`, never raw string paths.** No `os.path.join`; no manual `/` or `\` string
  concatenation. This matters doubly here since the app is cross-platform (macOS/Windows/Linux).
- **f-strings only** for string interpolation. No `%`-formatting, no `.format()`.
- **Dataclasses (or Pydantic, for manifest validation) over bare dicts** for any structured data
  that has a known shape — e.g. a loaded manifest, a catalog entry, a container status result.
  Bare `dict[str, Any]` is acceptable only at the JSON parse boundary, immediately converted.
- **No bare `except:`.** Catch specific exceptions. If you must catch broadly at a boundary
  (e.g. top-level UI error handling), catch `Exception` and log/report it — never silently swallow.
- **Context managers for resources.** Subprocess handles, file handles, DB connections — always
  `with`.
- **No mutable default arguments.** `def f(x: list | None = None)`, never `def f(x: list = [])`.
- **Prefer composition over inheritance**, except where inheritance expresses genuine
  "is-a" hierarchy (e.g. a base `Controller` class that per-game controllers subclass — see §3).
- **Docstrings on every public function/class** — one-line summary is the minimum; explain
  *why*, not just *what*, when the "why" isn't obvious from the name.
- **Logging, not `print`.** Use the standard `logging` module (or a thin wrapper) everywhere
  except throwaway local debugging that never gets committed.
- **Formatting/linting:** `black` for formatting, `ruff` for linting (recommended tools — pin
  versions in `requirements-dev.txt` once that file exists). Line length 100.

---

## 3. Strict Separation of Concerns

Every file in `pylauncher/yulon/` has exactly one job. This is the most important structural rule
in the codebase because the whole point of Yu'lon is to be more maintainable than the shell
scripts it replaces.

| Layer | Owns | Must never |
|---|---|---|
| `runner.py` | Running a subprocess and streaming its output | Know anything about Docker, games, or manifests |
| `docker.py` | Shared, game-agnostic Docker lifecycle (start/stop/status/health/polling/port-conflict check) via `runner.py` | Know anything about a specific game's containers/ports — those are passed in by callers via `ContainerSpec` |
| `controller.py` | The base `Controller`: composes one `ContainerSpec` + server dir, exposes start/stop/status/polling by delegating to `docker.py`, and applies the README §12 single-instance guard (refuse `start()` while a *foreign* container binds our ports) once for every game | Know anything about a specific game; reimplement `docker.py` behavior; read manifests (Phase 2.3 layers that on top) |
| `manifest.py` | The manifest schema: pydantic models for every family, the `repo` allow-list, the JSON Schema export | Know how to *apply* a manifest (that is each controller's `modules.py`); hardcode any per-item data |
| `manifest_store.py` | Reading one game's manifest tree (indexes + items) and mirroring it from GitHub with ETag revalidation | Know what any item does; apply anything |
| `apply.py` | The shared apply engine: turns manifest primitives into install/configure/remove steps through small seams (`Git`, `SqlRunner`, `DbcCopier`); reports every skipped step | Contain per-item conditionals (that is manifest data); rebuild/restart/touch the Docker lifecycle itself (the controller decides) |
| `platform.py` | OS detection, config dir paths, Docker/WSL provisioning, network auto-setup (LAN/internet-play firewall, IP detection, WSL2 portproxy — README §13) | Know anything about a specific game or module |
| `networking.py` | README §13 orchestration: `plan()` computes firewall/portproxy commands, the realmlist UPDATE and the router steps the app cannot do; `apply()` executes the automatable part (`sudo -n`, never a blocking prompt) and reports every skip with the command to paste | Hardcode ports or DB/table names (catalog data); contain UI; restart the server itself |
| `catalog/catalog.py` | Typed models for `catalog.json` (emulator sources, install script, containers, port table, DB names, client) + `load_catalog()` | Hardcode per-game values (they are `catalog.json` data); drive an install |
| `catalog/installer.py` | Orchestrating an install (deps → clone → build → config) for *one* catalog entry | Contain UI code, or hardcode per-game logic that belongs in a manifest |
| `controller_<acronym>/docker_ctl.py` | Holding *that one game's* `ContainerSpec` and re-exporting `docker.py`'s shared operations | Reimplement Docker lifecycle logic itself, reach into another game's controller, or contain UI code |
| `controller_<acronym>/controller.py` | That game's `Controller` subclass — passes its `ContainerSpec` to the base and nothing else | Override the inherited lifecycle methods (if a game needs different behavior, extend `docker.py`/the base instead) |
| `controller_<acronym>/modules.py` | Binding the shared store/fetcher/applier to that game (game id, bundled manifest dir, refresh URL, DB container) | Hardcode module data that should live in `manifests/` JSON; reimplement loading or applying |
| `ui/*_view.py` | Rendering widgets and wiring signals | Contain business logic, subprocess calls, or Docker calls directly — delegate to controller/catalog objects |

**Rule of thumb:** if you're writing Docker-related code inside a `ui/` file, or UI-related code
inside a `controller_*/` file, stop — that logic belongs in the other layer, connected by a
signal or a plain method call (see §5).

**Manifests hold data, code holds behavior.** If a piece of information could be different for a
different mod/module/game (a repo URL, a config key name, a SQL glob), it belongs in a JSON
manifest under `manifests/`, not in a Python conditional. See `pyplan/README.md` §6.

---

## 4. DRY — Don't Repeat Yourself

- **Shared subprocess/Docker logic lives in `runner.py` and `docker.py`.** Per-game controllers
  (`controller_wow_wotlk/`, `controller_wow_tbc/`, etc.) hold only their own `ContainerSpec` and
  re-export the shared operations (or use the `*_for(spec, ...)` convenience wrappers) — they do
  not each reimplement "wait for container healthy" or "check port conflict" from scratch. (Phase
  1.4's base `Controller` class layers a shared, subclassable surface on top of this for behavior
  that genuinely needs inheritance — the module-level sharing in `docker.py` doesn't require it.)
- **The single-instance/port-conflict check (`pyplan/README.md` §12) is implemented once**, in
  `docker.py`'s `port_conflicts()`/`port_conflicts_for()`, and used by every controller.
- **Manifests are the DRY mechanism for game/module data.** Adding a new mod is a new JSON file,
  not new Python code, specifically so behavior isn't duplicated per mod.
- **If you find yourself copy-pasting a function between two `controller_*/` packages, stop.**
  Promote it to the shared `docker.py` module (or another shared utility module) instead.
- **Constants defined once.** Container name prefixes, default ports, config dir names — one
  source of truth, imported everywhere else.

---

## 5. Call Down / Signal Up

This is the governing UI architecture pattern and applies to **all** parent/child relationships
in the codebase, not just Qt widgets.

- **Calling down:** a parent (a view, a controller, an orchestrator) calls methods on its children
  directly. A `controller_view.py` calls `docker_ctl.start()` directly. A `CatalogView`'s parent
  window calls `catalog_view.refresh()` directly.
- **Signaling up:** a child never calls back into its parent by holding a reference to it, and
  never mutates parent state directly. Instead, a child **emits a signal** (PySide6 `Signal`) that
  the parent (or whoever composed the child) connects to. The child has no idea who's listening,
  or whether anyone is.

```python
# GOOD — child signals up, has no reference to its parent
class InstallPanel(QWidget):
    install_finished = Signal(bool)  # emits success/failure, doesn't know who's listening

    def _on_process_done(self, ok: bool) -> None:
        self.install_finished.emit(ok)


# GOOD — parent calls down directly
class CatalogView(QWidget):
    def __init__(self) -> None:
        self.panel = InstallPanel()
        self.panel.install_finished.connect(self._handle_install_finished)

    def start_install(self, entry: CatalogEntry) -> None:
        self.panel.begin(entry)  # calling down


# BAD — child reaches up into a parent it was handed a reference to
class InstallPanel(QWidget):
    def __init__(self, parent_window) -> None:
        self._parent_window = parent_window  # ✗ never do this

    def _on_process_done(self, ok: bool) -> None:
        self._parent_window.refresh_catalog()  # ✗ child mutating parent directly
```

This applies equally outside the UI layer: a `docker_ctl.py` function that starts a container
should not call back into `installer.py` or the UI — it returns a result (or emits an event, if
async) and lets the caller decide what happens next.

---

## 6. Game Naming — Acronyms Only

**Never refer to a game by its full or trademarked name anywhere in code, filenames, manifest
`id`/`game` fields, log messages, or UI copy shown to developers.** Use the acronym.

| Never write | Always write |
|---|---|
| Warcraft, World of Warcraft | **WoW** |
| RuneScape | **RS** |
| MapleStory | **MS** |
| Mu Online | **MU** |
| Wrath of the Lich King | **WotLK** (append to the game acronym: `wow-wotlk`) |
| The Burning Crusade | **TBC** (`wow-tbc`) |
| Tortoise WoW (Turtle-WoW solo fork) | **WoW Tortoise** (`wow-tortoise`, or **Tortoise** for short) |

- This applies to Python identifiers, JSON `game`/`id` values, directory names
  (`controller_wow_wotlk/`, not `controller_world_of_warcraft_wrath_of_the_lich_king/`), and log
  output.
- **User-facing UI copy is the one exception where clarity matters more than the rule** — a
  first-run screen may need to spell out "World of Warcraft (WoW) — Wrath of the Lich King" once
  for a new user's benefit. Even then, default to the acronym after first mention on a given
  screen.
- When adding a new game to the catalog, pick its acronym convention (check for existing community
  convention first — e.g. RS for RuneScape is already used in this repo's `guides/runescape/`) and
  use it consistently across manifest `game` values, directory names, and container name prefixes.
- Rationale: keeps the codebase, logs, and filenames neutral of trademarked terms, and keeps
  naming short and consistent with how the community already refers to these servers.

### 6a. Filenames — always lowercase

**Every filename in this repository, without exception, is lowercase.** This includes Python
modules, JSON manifests, markdown docs, and directories.

- `catalog_view.py`, not `Catalog_view.py`
- `controller_wow_wotlk/`, not `Controller-WoW-WotLK/`
- Use `snake_case` for Python module files (`docker_ctl.py`), and `kebab-case` for
  non-Python files and directories where multiple words are needed (`pylauncher/`,
  `release.yml` stays as-is since it's a tool-mandated name).
- **Hard override: any directory that is an importable Python package (i.e. contains an
  `__init__.py`) MUST use `snake_case`, never `kebab-case`, even though it's "a directory."**
  A hyphen is not a legal character in a Python identifier/import path — `import
  controller-wow-wotlk` is a syntax error, and tools like `mypy` reject such a directory outright
  ("contains `__init__.py` but is not a valid Python package name"). This isn't a style
  preference; a hyphenated package directory is broken, not just inconsistently named. `kebab-case`
  is reserved for directories that are *not* Python packages (e.g. `pylauncher/` itself, the repo
  root, is fine since nothing imports it as `import pylauncher`).
- **Also avoid a top-level package literally named `py`.** It shadows the third-party `py` PyPI
  package that `pytest` depends on internally, which breaks `pytest` at import time
  (`AttributeError: module 'py' has no attribute 'path'`). This project's top-level package is
  named `yulon`, not `py`, for this reason.
- Class names inside a file still use `PascalCase` as normal Python convention — this rule is
  about the **filename on disk**, not identifiers inside the file.

---

## 7. Extrapolated Conventions (from `pyplan/README.md`)

These aren't new rules — they're existing decisions from the design doc, restated here so this
guide is a complete reference on its own:

- **UI toolkit is PySide6 (Qt).** No Tkinter, no web views, no JS of any kind (`README.md` §2).
- **Docker is driven via the `docker` CLI through `subprocess`**, not the Docker SDK, to keep
  parity with `docker compose` semantics (`README.md` §2).
- **Game/module/mod data lives in JSON manifests** under `manifests/<game>/`, not hardcoded in
  Python (`README.md` §6). Manifest `repo` fields must point at legitimate open-source projects
  only — never a piracy source (`README.md` §3a).
- **The app never bundles or fetches copyrighted client assets.** Users always supply their own
  legally obtained client (`README.md` §3a).
- **The app installs everything, including Docker, the WSL/VM virtualization layer, and Python.**
  Docker Engine (Linux) / Docker Desktop + WSL2 (Windows) / Docker Desktop + Linux VM (macOS),
  the emulator, modules/mods, and the app's own runtime are all provisioned silently — Python via
  a bundled PyInstaller interpreter so end users never install it themselves (`README.md` §1 goal 5
  and §3b).
- **Testing uses `pytest`**, with `subprocess` calls mocked in unit tests and a smaller
  integration suite that exercises real Docker (`README.md` §7, Phase 1).
- **Per-OS app config/state lives under a dedicated directory**, separate from server data:
  `~/.local/share/yulon/` (Linux), `%APPDATA%\yulon\` (Windows),
  `~/Library/Application Support/yulon/` (macOS) (`README.md` §11).
- **Only one server runs at a time**; the shared controller layer enforces this centrally rather
  than leaving it to each per-game controller (`README.md` §12).
- **Networking auto-setup (LAN & internet play)** is automated by the app, not left to shell
  commands (`README.md` §13). LAN is fully automatable; internet play additionally requires
  router steps (DHCP reservation, TCP port forwarding) the app **detects and prompts** for rather
  than silently failing. The per-OS firewall commands and the auth/world/db port table are shared
  `platform.py`-owned behavior + manifest data, never per-game copy-pasted.
- **Packaging targets:** `.AppImage` (Linux, all distros), `.exe`/MSI (Windows), `.dmg` (macOS),
  built via a GitHub Actions matrix since PyInstaller cannot cross-compile (`README.md` §4).
- **Code signing/notarization is out of scope for v1** — unsigned-binary OS warnings are an
  accepted, documented tradeoff for now (`README.md` §8–9).

---

## 8. For LLM Agents Specifically

If you are an LLM making changes to this codebase:

- **Check this file and `pyplan/README.md` before writing code**, not after. Both are short
  enough to read in full.
- **If a requested change would violate §1's non-negotiable rules, say so explicitly** and propose
  a compliant alternative rather than silently applying the requested change as-is.
- **When adding a new game, mod, or module, prefer adding/editing a JSON manifest over writing new
  Python**, per §3 and §4. Only write Python if the manifest schema genuinely can't express the
  new behavior — and if so, consider extending the schema instead.
- **When naming anything — files, directories, identifiers, manifest fields — apply §6 (acronyms)
  and §6a (lowercase filenames) before finalizing.** Don't wait to be asked to fix casing or naming
  after the fact.
- **Do not invent new architectural patterns.** Use call-down/signal-up (§5) for any new
  parent/child relationship, even outside the UI layer, unless the design doc explicitly says
  otherwise for that specific case.
- **Never emit unannotated function signatures or `Any`-typed code to "get it working first."**
  Per §2, treat missing types as a defect at write-time, not something to add in a follow-up pass.
- **Never add notes, decision-log commentary, "flagged conflict" callouts, or discovery write-ups
  into `pyplan/roadmap.md`.** Per §9, that file is a clean, checklist-style execution plan only.

---

## 9. Document Hygiene — `roadmap.md` Stays a Clean Checklist

`pyplan/roadmap.md` is the execution plan. It is **not** a scratchpad, decision log, or running
commentary. Keep it terse and checklist-shaped:

- **`roadmap.md` may only contain:** phase/step headers, numbered action items, and
  "Definition of done" lines. Short one-line clarifications of scope (e.g. "out of scope for this
  step: ...") are fine when they change what the step actually requires.
- **`roadmap.md` may NOT contain:** "flagged conflict" essays, historical narration of bugs found
  during implementation, justifications for why an ordering choice was made, or any other prose
  that reads as a note-to-self rather than an instruction. If you catch yourself writing more than
  2-3 sentences of explanation for *why* a step is what it is, trim it back to the instruction.
- **Rationale:** `roadmap.md` needs to stay skimmable as a plan. Mixing in retrospective narration
  makes it harder to tell "what to do next" from "what already happened," especially for an LLM
  agent re-reading the file cold in a future session.
