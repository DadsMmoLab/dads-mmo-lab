# Clause 9 of the 7.1 gate line, from the `clean-ssh` run of 2026-09-04

`docker compose config`, taken from the install that press 2 of the `clean-ssh` run left at
`/home/pk/wowserver` (press 2 log beside this file: `gate71-press2.log`, `ready` at line 6045,
exit 0 at 20:13:05), **diffed against `tests/data/wotlk-compose-config.json` the way
`tests/support_compose.py` does**. Captured 2026-09-04 23:54:35 CEST by lane gate-71-72,
before that box was restored for the 7.2 run.

## How it was captured, and why that way

Run **in the install directory with no `-f`**, so compose auto-loads `docker-compose.yml` and
`docker-compose.override.yml` and not the build overlay — the trap the Arch record on the 7.1
line describes (`-f` disables override discovery and reports two false differences).

```
cd ~/wowserver && docker compose config --format json > compose-config.raw.json   # 9,322 bytes
cd ~/wowserver && docker compose config               > compose-config.yml        # 219 lines
```

Docker 29.1.3, Docker Compose 2.40.3, core rev `413bea61a` (a different clone from the
2026-08-31 run the fixture was minted from, whose core was `47960183b`). md5 of the JSON:
`5ec739cc005cbbbf72e28c01cc2de2ee`.

## The diff — `compose-diff.txt`

Run on m910q in a private lane copy carrying the `2f39a6d9` test files, 2026-09-04 23:56:10.

* **Both variables set, the route the test documents**
  (`YULON_COMPOSE_CONFIG=<raw json>` and `YULON_COMPOSE_ROOT=/home/pk/wowserver`):
  `tests/test_compose_fixture.py` → **59 passed**;
  `test_a_captured_compose_config_matches_the_fixture PASSED`.
* **The seam by hand**, exactly as the test calls it — `compare(shape_from_config(raw, root),
  shape_from_config(fixture))` → **0 service differences**; `compare_stack(...)` → **0 stack
  differences**. The raw capture keeps its top-level `name:`, which is why `compare_stack`
  reports nothing here where the 2026-09-04 non-clean record (whose transform stripped it)
  reported one line.
* **The trap, reproduced on purpose**: with only `YULON_COMPOSE_ROOT` set the same test prints
  `SKIPPED [1] ... set YULON_COMPOSE_CONFIG=<compose config json> to diff a live install` —
  and without `-rs` a skip reads as a pass. `checklist.md` records this under the Fedora entry.
* **Control**: the same capture with no root → 3 service differences (the absolute bind paths),
  so the comparison is not vacuous; the root is doing work and the fixture is being read.

## What this settles, and what it does not

The clause asks that the config "matches a fixture minted from a DIFFERENT run". The fixture
was minted from the 2026-08-31 install; this capture is from a fresh clone on 2026-09-04 (core
`413bea61a` against `47960183b`) compiled on a box restored from `clean-ssh`. So the fixture and
the subject are different runs, and the comparison passes at every field the vocabulary
compares. **Met.**

It does not distinguish the runs by install id: both installed into `/home/pk/wowserver`, and
the id is derived from the path, so both read `243c46e3`. The 2026-09-04 non-clean record
already noted that.

## The 2026-09-05 re-run produced the same bytes

The 7.2 re-run from the same checkpoint (`pyplan/gates/7.2-ubuntu-2026-09-05/`) captured its own
`docker compose config --format json` from its own `/home/pk/wowserver` at 01:58:59 and got
**md5 `5ec739cc005cbbbf72e28c01cc2de2ee` — byte-identical to this file**. Same path, same
compose version, same catalog, same core revision (`413bea61a` again, the clone happened three
hours later and upstream had not moved). Identical bytes means the diff above is that run's
result too; it was not run a second time.
