# Yu'lon Checklist & Notes

> Companion to `pyplan/roadmap.md`. This file is where **checkable progress** and **all notes,
> decisions, and discoveries** live — `roadmap.md` itself stays a clean plan with no commentary
> (style-guide §9).
>
> **How to use this file:**
> - Check items off as they're completed. Leave unchecked items as-is; don't delete steps even if
>   scope changes — note the change instead.
> - Every phase has a **Notes** subsection. Log anything discovered, decided, or flagged while
>   doing the work there — bugs found, conflicts resolved, deviations from the plan, links to
>   relevant commits/PRs. Dated, short entries are better than long prose.
> - This file is expected to grow throughout the project. That's fine — it's a log, not a plan.

---

## Phase 0 — Tooling & hygiene

- [x] 0.1 Pin the toolchain (`requirements-dev.txt`: pytest, mypy, black, ruff)
- [x] 0.2 Add a CI lint/type/test job (`ci.yml`, separate from `release.yml`)
- [x] 0.3 Resolve remaining naming debt (lowercase filenames, valid `snake_case` package dirs)
- [x] 0.4 Pin an AzerothCore compose fixture for integration tests
- [x] 0.5 Write a minimal contributor/dev-setup doc (`development.md`)
- [x] 0.6 Establish a shared logging convention

### Phase 0 Notes

- 2026-08-19 — Package `py/` renamed to `yulon/`: a package literally named `py` shadows the
  third-party `py` PyPI package that `pytest` depends on internally, crashing pytest with
  `AttributeError: module 'py' has no attribute 'path'`. Not a style nit — a real, reproducible
  bug caught by actually running the test suite during setup.
- 2026-08-19 — Package `controller-wow-wotlk/` renamed to `controller_wow_wotlk/`: a hyphenated
  directory containing `__init__.py` is not a valid, importable Python package name (`mypy`
  rejected it outright: "contains `__init__.py` but is not a valid Python package name").
  style-guide §6a's kebab-case-for-directories guidance was corrected to carve out an exception
  for real Python packages, which must be `snake_case`.
- 2026-08-19 — Fixed several `mypy --strict` violations in the Phase-scaffolded placeholder stubs
  (missing generic type args on `dict`/`CompletedProcess`; `str` used for filesystem paths instead
  of `pathlib.Path`) while validating the initial setup. All four checks (`ruff`, `black --check`,
  `mypy`, `pytest`) pass cleanly as of this entry.
- 2026-08-19 — 0.4 done: fixture pinned in `pylauncher/tests/fixture.md` (source=
  `mod-playerbots/azerothcore-wotlk.git`, branch=`Playerbot`; containers `ac-database`/
  `ac-authserver`/`ac-worldserver`). Intentionally pins source+branch, not an exact SHA (branch
  moves faster than the installers); a breaking change would warrant a SHA, logged here.
- 2026-08-19 — 0.6 done: shared logging convention in `pylauncher/yulon/log.py` (`get_logger()` +
  idempotent `configure()` writing a rotating `yulon.log` to the per-OS config dir + stderr).
  `runner.py` is the first consumer (`logger = get_logger(__name__)`); `main.py` calls `configure()`.
  Covered by `pylauncher/tests/test_log.py`.
- 2026-08-19 — Removed a stray duplicate `.venv` at the repo root (a partial PySide6-only env from
  an early setup step). The only venv is `pylauncher/.venv` (full dev toolchain). Also added
  `.venv/` to the root `.gitignore`, which previously didn't exclude it.
- 2026-08-19 — Three-subagent review of all changes since last commit found and fixed real bugs
  in `yulon/log.py`/`tests/test_log.py`, plus a lingering naming violation:
  - **Idempotency bug (real, confirmed by two independent reviews):** the original `configure()`
    used a single `_configured` flag checked before anything else ran, so a later
    `configure(config_dir=...)` call was a **silent no-op** if `get_logger()` (or a bare
    `configure()`) had already run once — meaning file logging could never actually turn on once
    anything imported `yulon.runner` (which calls `get_logger(__name__)` at module scope). Fixed
    by splitting into two independent flags, `_stderr_configured` and `_file_configured`, each
    guarding only its own handler — a later call with a real `config_dir` now correctly adds the
    file handler even if stderr was already configured. Added a regression test
    (`test_configure_can_add_file_handler_after_earlier_stderr_only_call`) that fails against the
    old implementation and passes now.
  - **Thread-safety:** added a `threading.Lock()` around the check-then-act handler setup in
    `configure()` (previously a bare module-level boolean with no lock).
  - **Test isolation/global-state leakage:** the original tests mutated the real root logger and
    `log._configured` with no teardown, leaking handlers across the whole pytest session and
    creating an undocumented test-order dependency (`test_configure_is_idempotent` only passed if
    it ran immediately after another specific test — confirmed to fail when run in isolation).
    Added `log._reset_for_tests()` (test-only helper, not public API) and an `autouse` pytest
    fixture that resets state before/after every test. Verified the previously-order-dependent
    test now passes both alone and in arbitrary combinations.
  - **Missing content-level test:** the original suite only counted handler objects, never
    verified a logged message actually reached the file. Added
    `test_a_logged_message_actually_reaches_the_log_file`.
  - **`main.py` behavior clarified, not changed:** confirmed `main.py`'s `configure()` call is
    stderr-only today because `platform.config_dir()` (Phase 1) doesn't exist yet — this was
    already correct, just under-documented. Added an explicit code comment in `main.py` instead
    of leaving the gap implicit.
  - **`runner.py` didn't actually exercise the logger** (it was assigned but never called),
    undercutting roadmap 0.6's "used by at least one Phase 1 module as a working example"
    definition of done. Added `logger.debug(...)` calls (f-string formatted, per style-guide §2)
    in both `stream()`/`run()` stubs so the convention has a real, working example, not just an
    unused import.
  - **`DEVELOPMENT.md` → `development.md`:** an existing uppercase filename (predating this
    session's changes) that violates style-guide §6a's exceptionless lowercase rule, flagged
    independently by two of the three review agents. Renamed; fixed the two references to it in
    `pyplan/README.md` §5 and this file's own 0.5 entry above.
  - **`pyplan/README.md` §5's structure tree was stale**: didn't list `yulon/log.py` or `tests/`'s
    actual contents (`fixture.md`, `test_log.py`, `test_setup_sanity.py`, `__init__.py`). Updated.
  - All four checks (`black`, `ruff`, `mypy --strict`, `pytest`) pass after these fixes; 7 tests
    total (up from 4), including the two new regression/content tests.

---

## Phase 1 — Foundation (testable Python core, no UI)

- [x] 1.1 `runner.py` — subprocess streaming (`stream()`, `run()`)
- [ ] 1.2 `platform.py` — OS detection + `config_dir()` + provisioning stubs
- [ ] 1.3 `docker_ctl.py` — shared Docker lifecycle logic + port-conflict check
- [ ] 1.4 Base controller abstraction
- [ ] 1.5 Tests (mocked unit tests + real-Docker integration suite)
- [ ] **Phase 1 exit criteria met**

### Phase 1 Notes

- 2026-08-19 — 1.1 done: implemented `yulon/runner.py` (`stream()` + `run()`).
  - `stream()` runs `subprocess.Popen` with `text=True`/`encoding="utf-8"`/
    `errors="replace"`, drains **stderr on a background thread** (to avoid
    pipe-buffer deadlock when a child writes a lot to stderr), yields stdout
    lines stripped of their trailing newline, then yields the buffered stderr
    lines, and raises `subprocess.CalledProcessError` on non-zero exit.
  - `run()` is the fire-and-collect sibling: `subprocess.run(..., capture_output=True,
    text=True, check=False)` — returns the process, does **not** raise on
    non-zero exit (callers inspect `returncode`).
  - `_cwd_arg()` coerces `pathlib.Path`→`str` for the `cwd=` param (style-guide
    §2: `Path` in the public API, `str` only at the `subprocess` boundary).
  - Covered by `tests/test_runner.py` (6 tests: stdout capture, stderr separation,
    non-zero returncode, line streaming, stderr ordering, `cwd` handling). Note:
    corrected below (2026-08-19 review entry) — stderr is **not** interleaved in
    real time, it's appended as a block after stdout finishes; the original
    wording here was imprecise.
  - All four checks (`black`, `ruff`, `mypy --strict`, `pytest`) green; 14 tests
    total.
- 2026-08-19 — Three-subagent review of all changes since last commit (two of
  the three review calls hit a model rate limit on first attempt and were
  retried successfully). Findings and fixes:
  - **Two undocumented, suspicious regressions reverted.** An external edit had
    changed `yulon/__init__.py`'s `__version__` from `"0.1.0"` to `"0.0.2"` (a
    decrease, unexplained anywhere) and `build/pylauncher.spec`'s PyInstaller
    `name=` from `"yulon"` to `"Dads Yulon Launcher"` (uppercase + spaces,
    diverging from the `yulon` package name, `yulon.log`, and the
    `~/.local/share/yulon/`-style config dirs used everywhere else, and
    violating style-guide §6a). Both reverted to their prior, documented values.
  - **Real bug in `stream()`: resource leak on early generator abandonment.**
    If a caller `break`s out of `for line in stream(...):` (or the generator is
    garbage-collected unexhausted), the original `with subprocess.Popen(...)`
    block received `GeneratorExit` at the suspended `yield`, which skipped
    `reader.join()`/`proc.wait()` entirely — leaving the child process running
    and the stderr-reader thread unjoined. For a long-running child (e.g. a
    `docker compose up`), this could also **hang the calling thread** if the
    `with` block's implicit `wait()` were ever reached on that path, since it
    blocks until the child exits on its own. Fixed by wrapping the body in
    `try`/`finally`: the `finally` now unconditionally terminates the process
    (escalating to `kill()` after a 5s timeout), joins the reader thread (with
    a timeout), and closes both pipes — on both the normal-completion and
    early-abandonment paths. Added a regression test
    (`test_stream_terminates_child_on_early_generator_abandonment`) using a
    child that sleeps 60s, asserting `gen.close()` returns in well under 10s.
  - **Misleading docstrings/comments about real-time stderr interleaving.**
    The module docstring and `stream()`'s one-line summary implied stdout and
    stderr are merged live ("as it arrives"). In reality stderr is only
    drained on a background thread to avoid pipe-buffer deadlock — it is
    yielded as one block *after* stdout is exhausted and the process exits, not
    interleaved chronologically. Rewrote both docstrings to state this
    precisely, and renamed/fixed the test that previously only asserted
    membership (`test_stream_yields_stderr_lines_too`) to assert exact order
    (`test_stream_yields_stderr_lines_after_stdout`, `assert lines == ["o1",
    "o2", "e1"]`).
  - **Unverified deadlock-avoidance code path.** The original test suite only
    used short (1-2 line) stderr output, so the entire reason the background
    thread exists (avoiding a full OS pipe buffer, typically ~64KB, deadlocking
    the child) was never actually exercised. Added
    `test_stream_does_not_deadlock_on_large_stderr_payload` (200,000 stderr
    lines, asserts completion in well under 30s).
  - **Missing-executable behavior was undocumented and untested.** `Popen`
    raises `OSError`/`FileNotFoundError` when the target executable doesn't
    exist, but neither `stream()`'s nor `run()`'s docstring mentioned this, and
    no test covered it. Added `Raises: OSError` to `stream()`'s docstring and
    added `test_stream_raises_oserror_on_missing_executable` /
    `test_run_raises_oserror_on_missing_executable`.
  - **`roadmap.md` §3.2 numbering bug + commentary creep.** An earlier edit
    inserted a new item as "3." directly before the existing "3. *Definition of
    done*" line, leaving two items both numbered "3." in the raw markdown, and
    the inserted text read as an explanatory paragraph rather than a checklist
    item (bordering on style-guide §9's "no commentary in roadmap.md" rule).
    Renumbered (`3`/`4`) and trimmed to a single terse action-item sentence.
  - **`roadmap.md` §5.2 stale reference.** Still said "bundle `manifests/` and
    `py/`" — `py/` was renamed to `yulon/` back in Phase 0; fixed.
  - **`README.md` §3a/§3b redundancy.** §3a's bullet list had grown to fully
    re-describe Docker/WSL/VM provisioning that §3b (added the same session)
    already owns in detail. Trimmed §3a back to a short cross-reference to §3b
    instead of duplicating the content.
  - **`style-guide.md` §7 loose cross-reference.** A bullet cited both `§3a`
    and `§3b` for the Python-bundling claim, but that specific detail only
    lives in `§3b`. Removed the inaccurate `§3a` citation.
  - All four checks (`black`, `ruff`, `mypy --strict`, `pytest`) green after
    fixes; 18 tests total (up from 14).

---

## Phase 2 — Manifest schema & data port

- [ ] 2.1 Finalize the manifest schema (+ `repo` allow-list validation)
- [ ] 2.2 Port WotLK modules from `wow-manage.sh` into `manifests/wow-wotlk/`
- [ ] 2.3 `modules.py` — load/validate/fetch
- [ ] **Phase 2 exit criteria met**

### Phase 2 Notes

- 2026-08-19 — README §6's example manifest originally listed `"requires": ["playerbots"]` for
  `ah-bot`. Verified against `wow-manage.sh`: AH Bot has no such dependency. The module that
  actually requires Playerbots is `mod-player-bot-level-brackets`. README §6 corrected; keep this
  in mind when porting the real module list in 2.2 — don't copy the old example's dependency
  claim.

---

## Phase 3 — Catalog (catalog + installer)

- [ ] 3.1 `catalog.json` — game list
- [ ] 3.2 `installer.py` — orchestration (Phase 3a: shells out to existing scripts)
- [ ] 3.3 Silent Docker/WSL provisioning stubs wired in (graceful failure until Phase 5)
- [ ] **Phase 3 exit criteria met** (verified via CLI/test harness — no UI yet)

### Phase 3 Notes

-

---

## Phase 4 — Controller UI (PySide6)

- [ ] 4.1 `log_panel.py` — streaming output widget
- [ ] 4.2 `catalog_view.py` — browsable catalog
- [ ] 4.3 `controller_view.py` — per-install management
- [ ] **Phase 4 exit criteria met**

### Phase 4 Notes

-

---

## Phase 5 — Windows/macOS provisioning + packaging

- [ ] 5.1 Silent Docker Desktop / WSL2 provisioning + doc update
- [ ] 5.2 PyInstaller specs finalized
- [ ] 5.3 GitHub Actions release matrix complete
- [ ] 5.4 Application self-update check (README §10)
- [ ] **Phase 5 exit criteria met**

### Phase 5 Notes

-

---

## Cross-cutting / general notes

> Anything that doesn't cleanly belong to one phase — style-guide amendments, cross-document
> corrections, tooling gotchas, etc.

-
