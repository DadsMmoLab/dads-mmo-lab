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
