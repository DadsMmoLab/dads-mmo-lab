# Yu'lon Checklist

> Companion to `pyplan/roadmap.md`. This file is where **checkable progress** is tracked, while `roadmap.md` itself stays a clean plan with no commentary (style-guide §9).
>
> **How to use this file:**
> - Check items off as they're completed. Leave unchecked items as-is; don't delete steps even if scope changes — note the change instead.
> - This file is expected to grow throughout the project. That's fine; it's a checklist, not a plan.

---

## Phase 0 — Tooling & hygiene

- [x] 0.1 Pin the toolchain (`requirements-dev.txt`: pytest, mypy, black, ruff)
- [x] 0.2 Add a CI lint/type/test job (`ci.yml`, separate from `release.yml`)
- [x] 0.3 Resolve remaining naming debt (lowercase filenames, valid `snake_case` package dirs)
- [x] 0.4 Pin an AzerothCore compose fixture for integration tests
- [x] 0.5 Write a minimal contributor/dev-setup doc (`development.md`)
- [x] 0.6 Establish a shared logging convention

---

## Phase 1 — Foundation (testable Python core, no UI)

- [x] 1.1 `runner.py` — subprocess streaming (`stream()`, `run()`)
- [x] 1.2 `platform.py` — OS detection + `config_dir()` + provisioning stubs
- [x] 1.3 `docker.py` — shared Docker lifecycle logic + port-conflict check
- [x] 1.4 Base controller abstraction
- [ ] 1.5 Tests (mocked unit tests + real-Docker integration suite)
- [ ] **Phase 1 exit criteria met**

---

## Phase 2 — Manifest schema & data port

- [ ] 2.1 Finalize the manifest schema (+ `repo` allow-list validation)
- [ ] 2.2 Port WotLK modules from `wow-manage.sh` into `manifests/wow-wotlk/`
- [ ] 2.3 `modules.py` — load/validate/fetch
- [ ] **Phase 2 exit criteria met**

---

## Phase 3 — Catalog (catalog + installer)

- [ ] 3.1 `catalog.json` — game list
- [ ] 3.2 `installer.py` — orchestration (Phase 3a: shells out to existing scripts)
- [ ] 3.3 Silent Docker/WSL provisioning stubs wired in (graceful failure until Phase 5)
- [ ] 3.4 Networking auto-setup (LAN + internet play; firewall helpers, realmlist updater, router-step prompts) — README §13
- [ ] **Phase 3 exit criteria met** (verified via CLI/test harness — no UI yet)

---

## Phase 4 — Controller UI (PySide6)

- [ ] 4.1 `log_panel.py` — streaming output widget
- [ ] 4.2 `catalog_view.py` — browsable catalog
- [ ] 4.3 `controller_view.py` — per-install management (+ LAN/internet networking auto-setup control)
- [ ] **Phase 4 exit criteria met**

---

## Phase 5 — Windows/macOS provisioning + packaging

- [ ] 5.1 Silent Docker Desktop / WSL2 provisioning + doc update
- [ ] 5.2 PyInstaller specs finalized
- [ ] 5.3 GitHub Actions release matrix complete
- [ ] 5.4 Application self-update check (README §10)
- [ ] **Phase 5 exit criteria met**

---

## Cross-cutting

> Anything that doesn't cleanly belong to one phase — style-guide amendments, cross-document
> corrections, tooling gotchas, etc.
