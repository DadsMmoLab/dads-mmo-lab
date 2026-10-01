"""The `slow` marker is registered, and CI still runs the tests that carry it.

`slow` exists for a quick local run only (`-m 'not integration and not slow'`);
CI and the full local run do not deselect it. Two ways that could rot without a
red test:

* the marker drops out of pyproject.toml's `markers` list, and every
  `@pytest.mark.slow` turns into an "Unknown pytest.mark.slow" warning - still
  deselectable by name, so a quick run LOOKS right;
* something in CI deselects it, and the slowest tests - the real-window
  navigation sweeps among them - are never run anywhere.

The second has many spellings: a second `-m`, a `-k`, a bare `pytest` rather
than `python -m pytest`, an argument on a continuation line, `PYTEST_ADDOPTS`,
pyproject's `addopts`. So the rule is not "parse the -m expression" but the
blunter one: no CI step that runs pytest says the word `slow` at all, nothing
in ci.yml sets PYTEST_ADDOPTS to anything that does, and neither does
`addopts`. Every rule is checked on made-up text too, one violation each, so a
checker that reads too little goes red here instead of passing quietly.

Text, not YAML: the repository has no YAML parser among its dependencies (as in
test_release_workflow_t90.py). A "step" is a `run:` key plus every line indented
deeper than the key, which covers block scalars (`|`, `>`), plain multi-line
values and shell `\\` continuations alike. Whole-line comments are dropped first,
because ci.yml's own comments use the word "slow".
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pylauncher" / "pyproject.toml"
CI = ROOT / ".github" / "workflows" / "ci.yml"

SLOW = re.compile(r"\bslow\b")
PYTEST = re.compile(r"\bpytest\b")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block(lines: list[str], i: int, column: int) -> list[str]:
    """Line `i` plus every following line indented past `column`, or carried by a `\\`."""
    out = [lines[i]]
    for line in lines[i + 1 :]:
        if line.strip() and _indent(line) <= column and not out[-1].rstrip().endswith("\\"):
            break
        out.append(line)
    return out


def ci_violations(text: str) -> list[str]:
    """What in a workflow's text deselects, or could deselect, the `slow` tests."""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    found: list[str] = []
    covered: set[int] = set()
    for i, line in enumerate(lines):
        key = re.match(r"^(\s*(?:-\s+)?)run:", line)
        if key is None:
            continue
        block = _block(lines, i, len(key.group(1)))
        covered.update(range(i, i + len(block)))
        step = "\n".join(block)
        if PYTEST.search(step) and SLOW.search(step):
            found.append(f"a pytest step says 'slow': {step.strip()!r}")
    for i, line in enumerate(lines):
        named = re.match(r"^\s*(?:-\s+)?name:", line)
        if PYTEST.search(line) and i not in covered and not named:
            found.append(f"pytest outside any run: step, unchecked: {line.strip()!r}")
        at = line.find("PYTEST_ADDOPTS")
        if at >= 0:
            setting = "\n".join(_block(lines, i, at))
            if SLOW.search(setting):
                found.append(f"PYTEST_ADDOPTS says 'slow': {setting.strip()!r}")
    return found


def pyproject_violations(text: str) -> list[str]:
    """`slow` not registered, or deselected for every run by `addopts`."""
    options = tomllib.loads(text).get("tool", {}).get("pytest", {}).get("ini_options", {})
    found: list[str] = []
    markers = options.get("markers", [])
    if not any(m.split(":", 1)[0].strip() == "slow" for m in markers):
        found.append(f"'slow' is not registered: {markers!r}")
    addopts = options.get("addopts", "")
    if isinstance(addopts, list):
        addopts = " ".join(addopts)
    if SLOW.search(addopts):
        found.append(f"addopts says 'slow': {addopts!r}")
    return found


def test_the_real_ci_runs_the_slow_tests() -> None:
    text = CI.read_text(encoding="utf-8")
    assert 'python -m pytest -q -m "not integration"' in text, "reading the wrong file?"
    assert ci_violations(text) == []


def test_the_real_pyproject_registers_slow_and_does_not_deselect_it() -> None:
    assert pyproject_violations(PYPROJECT.read_text(encoding="utf-8")) == []


_STEPS = """\
jobs:
  test:
    steps:
      - name: ruff
        run: python -m ruff check .
{step}
      - name: after
        run: echo done
"""


# A step is "      - name: <id>" plus these lines; MORE is one level deeper than `run:`.
RUN = "        run: "
MORE = "\n          "
_DESELECTING = {
    "second-m": RUN + 'python -m pytest -q -m "not integration" -m "not slow"',
    "k": RUN + 'python -m pytest -q -m "not integration" -k "not slow"',
    "bare": RUN + "pytest -m 'not slow'",
    "unquoted": RUN + "python -m pytest -m not\\ slow",
    "block": RUN + "|" + MORE + "python -m pytest -q \\" + MORE + '-m "not slow"',
    "folded": RUN + ">" + MORE + "python -m pytest -q" + MORE + '-m "not slow"',
    "plain": RUN + "python -m pytest -q" + MORE + '-m "not slow"',
    "env": "        env:" + MORE + 'PYTEST_ADDOPTS: -m "not slow"\n' + RUN + "python -m pytest",
    "env-folded": "        env:"
    + MORE
    + "PYTEST_ADDOPTS: >-"
    + MORE
    + '  -m "not slow"\n'
    + RUN
    + "python -m pytest",
    "exported": RUN + 'echo "PYTEST_ADDOPTS=-m \'not slow\'" >> "$GITHUB_ENV"',
}


@pytest.mark.parametrize(
    "step",
    [f"      - name: {k}\n{v}" for k, v in _DESELECTING.items()],
    ids=list(_DESELECTING),
)
def test_each_way_of_deselecting_slow_in_ci_is_caught(step: str) -> None:
    assert ci_violations(_STEPS.format(step=step)) != []
    # ...and for that word alone: the same text deselecting something else is clean.
    assert ci_violations(_STEPS.format(step=step.replace("slow", "quick"))) == []


@pytest.mark.parametrize(
    "step",
    [
        '      - name: tests\n        run: python -m pytest -q -m "not integration"',
        "      - name: a slow step that is not pytest\n        run: echo slow",
        "      # pytest is slow, says a comment\n"
        '      - name: tests\n        run: python -m pytest -q -m "not integration"',
    ],
    ids=["plain", "slow-but-not-pytest", "comment"],
)
def test_a_ci_that_runs_slow_is_not_flagged(step: str) -> None:
    assert ci_violations(_STEPS.format(step=step)) == []


_MARKERS = 'markers = ["integration: docker", "slow: over ~2 s"]\n'


@pytest.mark.parametrize(
    "toml",
    [
        '[tool.pytest.ini_options]\nmarkers = ["integration: docker"]\n',
        f"[tool.pytest.ini_options]\n{_MARKERS}addopts = \"-m 'not slow'\"\n",
        f'[tool.pytest.ini_options]\n{_MARKERS}addopts = ["-q", "-k", "not slow"]\n',
    ],
    ids=["unregistered", "addopts", "addopts-list"],
)
def test_each_way_of_breaking_slow_in_pyproject_is_caught(toml: str) -> None:
    assert pyproject_violations(toml) != []


def test_a_pyproject_that_registers_slow_is_not_flagged() -> None:
    assert pyproject_violations(f'[tool.pytest.ini_options]\n{_MARKERS}addopts = "-q"\n') == []
