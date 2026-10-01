"""The `slow` marker is registered, and CI still runs the tests that carry it.

`slow` exists for the local inner loop only: `yt --fast` deselects it, and the
full `yt` and CI do not. Two ways that could rot without a red test:

* the marker drops out of pyproject.toml's `markers` list, and every
  `@pytest.mark.slow` turns into an "Unknown pytest.mark.slow" warning - still
  deselectable by name, so a fast run LOOKS right;
* someone adds `and not slow` to a pytest step in ci.yml, and the slowest tests
  - the real-window navigation sweeps among them - are never run anywhere.

Text pins on ci.yml, as in test_release_workflow_t90.py: the repository has no
YAML parser among its dependencies. Comment lines are stripped first, because
ci.yml's own comments use the word "slow".
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pylauncher" / "pyproject.toml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _pytest_marker_expressions() -> list[str]:
    """The `-m` expression of every pytest command in ci.yml's code lines."""
    code = [
        ln for ln in CI.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")
    ]
    runs = [ln for ln in code if re.search(r"\bpython -m pytest\b", ln)]
    assert runs, "ci.yml runs no pytest at all - this guard is reading the wrong file"
    found = []
    for ln in runs:
        m = re.search(r"""\s-m\s+(["'])(.*?)\1""", ln.split("python -m pytest", 1)[1])
        found.append(m.group(2) if m else "")
    return found


def test_pyproject_registers_the_slow_marker() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.split(":", 1)[0].strip() == "slow" for m in markers), markers


def test_ci_runs_the_slow_tests() -> None:
    expressions = _pytest_marker_expressions()
    assert "not integration" in expressions, expressions
    for expr in expressions:
        assert "slow" not in expr, f"ci.yml deselects slow tests: -m {expr!r}"
