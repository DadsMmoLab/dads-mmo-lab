# Developing `pylauncher`

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

Needs a reachable Docker daemon (otherwise it stops with the Phase 3.3 message) and, because
the wrapped scripts start with `sudo -v`, cached or passwordless sudo.

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
