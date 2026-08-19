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
pytest              # tests
mypy yulon main.py  # static types
ruff check .        # lint
black --check .     # formatting
```

Run `black .` (without `--check`) to auto-format.

## Notes

- Python 3.11+ required (see `pyplan/README.md` §2).
- All four checks above (`pytest`, `mypy`, `ruff`, `black --check`) are expected to run in CI —
  see `pyplan/roadmap.md` Phase 0.2.
- See `pyplan/style-guide.md` before writing any code — it is a hard constraint, not a preference.
