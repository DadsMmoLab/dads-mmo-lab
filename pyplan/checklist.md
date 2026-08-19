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
- [ ] 0.4 Pin an AzerothCore compose fixture for integration tests
- [x] 0.5 Write a minimal contributor/dev-setup doc (`DEVELOPMENT.md`)
- [ ] 0.6 Establish a shared logging convention

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

---

## Phase 1 — Foundation (testable Python core, no UI)

- [ ] 1.1 `runner.py` — subprocess streaming (`stream()`, `run()`)
- [ ] 1.2 `platform.py` — OS detection + `config_dir()` + provisioning stubs
- [ ] 1.3 `docker_ctl.py` — shared Docker lifecycle logic + port-conflict check
- [ ] 1.4 Base controller abstraction
- [ ] 1.5 Tests (mocked unit tests + real-Docker integration suite)
- [ ] **Phase 1 exit criteria met**

### Phase 1 Notes

-

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
