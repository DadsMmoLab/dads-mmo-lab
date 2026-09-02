# Contributing to Dad's MMO Lab

First off — thank you. This project exists because of community knowledge and community effort. Every contribution matters.

## How to Contribute

### 🐛 Found a Bug in a Guide?
Open an issue with:
- Which guide you were following
- Which step failed
- What error message you got
- Your Steam Deck model / SteamOS version

### ✅ Got a Game Working That's Not Listed?
Amazing. Open an issue or PR with:
- The game name and version
- The emulator you used
- A rough outline of the steps
- Whether it's fully working or partially working

We'll help write it up into a proper guide.

### 📝 Want to Improve an Existing Guide?
PRs welcome. Keep the language plain and dad-friendly. Assume the reader loves games but isn't a developer.

### 💡 Have a Game Suggestion?
Open an issue tagged `game-suggestion`. Include:
- Game name
- Why you think it's possible (emulator exists? Linux compatible?)
- Link to the emulator project if you have it

## Ground Rules

1. **Open source emulators only.** No links to or instructions for obtaining copyrighted server binaries.
2. **No game assets.** We never distribute client files or game data.
3. **Private use focus.** Guides are for servers you run for yourself and the people you invite — LAN and internet play with friends are shipped features (`pyplan/README.md` §13, `yulon/networking.py`). No guides for running a publicly advertised server.
4. **Be kind.** This is a community for people who love old games. Keep it welcoming.

## Style Guide for Guides

- Write steps as if explaining to a friend who's smart but not technical
- Every command block should be copy-pasteable
- Always include a troubleshooting section
- Always credit the emulator project you're using

---

# Developing `pylauncher / yulon`

Minimal contributor setup for Yu'lon (see `pyplan/roadmap.md` Phase 0.5).

## Setup

```bash
cd pylauncher
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Running the app

```bash
python main.py
```

## Installing a server from the CLI (the harness)

```bash
python -m yulon.install_wiring wow-wotlk --server-dir ~/wow-server-playerbots
python -m yulon.install_wiring wow-tbc --client-dir ~/Games/WoWTBC   # games that need a client folder
```

The harness builds the same engine the Catalog's Install button builds
(`install_wiring.installer_for_app()` → `installer_for()` → the entry's family engine on
`native.StagedInstaller`) and streams its stages to stdout, so what it proves, the button proves.

Needs Docker — but not a reachable one to begin with: when no daemon answers, preflight runs the
real provisioner (`platform.ensure_docker()`) and refuses only if what comes back still cannot be
used, needing a reboot or a manual install. It needs no `bash`; 7.2 deleted the shell installers
and the engine that drove them, and nothing left on this path is a script.

It does not need cached sudo either, because it can ask. `main()` hands the engine
`_terminal_prompter` as its `ask` seam, and `ask` reaches Docker provisioning and nothing else: the
sudo password, read through `getpass` so it is never echoed back, and the docker-group consent,
asked in words a person can see themselves agreeing to. Off a terminal there is nobody to ask, so
the prompter says so on stderr and answers empty — which sudo and a y/n question both take as no —
and provisioning reports the group join as a manual step (`sudo usermod -aG docker "$USER"`, then
log out and back in) rather than making a root-equivalent change with no one watching. On a box
where Docker already answers, nothing is asked at all.

The harness refuses what the app refuses, in the same words. An entry that does not list this OS
in `install.platforms` is stopped by the engine's preflight with
`installer.unsupported_platform_message()` — the sentence the Catalog tile hangs on its disabled
Install button. An entry with no `install.native` block is refused by `installer_for()` itself,
printed as that sentence and exited 1;
`test_catalog.py::test_every_shipped_entry_is_installable_somewhere_and_names_its_family` is what
keeps a shipped entry from reaching it.

## Building the desktop binary

```bash
pip install pyinstaller
pyinstaller build/pylauncher.spec --noconfirm --distpath build/dist --workpath build/work
YULON_SMOKE_TEST=1 QT_QPA_PLATFORM=offscreen build/dist/yulon/yulon   # builds the window and exits 0
```

The release workflow (`.github/workflows/release.yml`) runs the same spec on each OS runner and
wraps the output (AppImage / zip / dmg). PyInstaller cannot cross-compile — never try locally.

## Running checks

```bash
pytest              # tests (unit + live-Docker integration; the latter self-skips without a daemon)
pytest -m "not integration"   # quick loop: mocked unit tests only
pytest -m integration tests/integration   # live suite only (needs a reachable Docker daemon)
mypy yulon main.py  # static types
ruff check .        # lint
black --check .     # formatting
```

Run `black .` (without `--check`) to auto-format.

### Integration tests

`tests/integration/` drives a real Docker daemon and is skipped automatically when none is
reachable, so the default `pytest` stays green without Docker. `test_docker_live.py` needs only
Docker (it brings up a throwaway `busybox` compose project shaped like an install).
`test_wotlk_live.py` additionally needs the AzerothCore fixture from `tests/fixture.md`, already
built, stopped, and pointed at via an env var — it will start it, wait for `ready...`, and stop it:

```bash
YULON_WOTLK_SERVER_DIR=~/wow-server-playerbots pytest -m integration tests/integration
# optional: YULON_WOTLK_REALM_ADDRESS=127.0.0.1 (the realm host the auth log must show)
```

`test_accounts_live.py` writes real accounts into `acore_auth`, so it needs the WotLK database
container **running**, named through its own env var:

```bash
YULON_WOTLK_DB_CONTAINER=ac-database pytest -m integration tests/integration
# optional: YULON_WOTLK_DB_PASSWORD (default `password`) and YULON_WOTLK_WORLD_CONTAINER (default
# `ac-worldserver`) — only the byte-exactness test needs a ready worldserver, and a pty
```

## Notes

- Python 3.11+ required (see `pyplan/README.md` §2).
- All four checks above (`pytest`, `mypy`, `ruff`, `black --check`) are expected to run in CI —
  see `pyplan/roadmap.md` Phase 0.2.
- See `pyplan/style-guide.md` before writing any code — it is a hard constraint, not a preference.


## Building the UI in Qt Designer

The views are hand-written widgets today, but nothing in `yulon/` depends on that: every view is
handed its dependencies as a seam (`ControllerServices`, `JobRunner`, `LogPanel`) and talks back
with signals, so the widget layer can be replaced by Designer-built forms without touching the
core.

```bash
pyside6-designer                  # ships with PySide6; save forms as yulon/ui/forms/<name>.ui
pyside6-uic yulon/ui/forms/x.ui -o yulon/ui/forms/ui_x.py   # or load the .ui at runtime
```

Two rules keep a Designer form working with the rest of the app:

1. **Long calls stay off the GUI thread.** Route every service call through `self._run(...)`
   (`yulon/ui/widgets/job.py`), never straight from a slot — `docker compose up`, a module
   install and a networking plan all take seconds to minutes.
2. **Callbacks must be bound methods of the view** (`@Slot(object)` on the widget class). A plain
   function or lambda connected to a worker's signal is delivered ON THE WORKER THREAD in PySide6,
   even with an explicit `QueuedConnection`.

