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
3. **Personal use focus.** Guides are for offline, single-player setups. No public server setup guides.
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

## Installing a server from the CLI (Phase 3a harness)

```bash
python -m yulon.catalog.installer wow-wotlk --server-dir ~/wow-server-playerbots
python -m yulon.catalog.installer wow-tbc --client-dir ~/Games/WoWTBC   # games that need a client folder
```

Needs a reachable Docker daemon (otherwise it stops with the Phase 3.3 message), and cached or
passwordless sudo — the wrapped scripts still start with `sudo -v`. The **app** no longer needs
that (roadmap 6.1.5 runs the script on a pty and answers the password through a dialog), but this
CLI harness has no dialog to answer with, so the old requirement stands here.

The harness also **declines** the installers' docker-group question, because there is nobody to
ask: joining that group is a root-equivalent privilege change, and the app never makes one
silently. Add yourself with `sudo usermod -aG docker "$USER"` if you want it.

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
# optional: YULON_WOTLK_REALM_ADDRESS=127.0.0.1 (dml-start.sh's DML_REALM_ADDRESS)
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

