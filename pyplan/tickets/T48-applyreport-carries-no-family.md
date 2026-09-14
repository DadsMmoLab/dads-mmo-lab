# T48 — `ApplyReport` names an id but not the family it belongs to

**Status:** FIXED on the branch; gate and review below.
**Filed:** 2026-09-13 by the lead.
**Branch:** `fix/applyreport-carries-its-family`, from `upstream/Yulon` 936beda5. (Filed on
`fix/t44-open-findings`, which was never pushed; the ticket travels with the fix.)

## The gap

`ApplyReport` (`yulon/apply.py:818`) carries:

```python
action: When
item_id: str
```

T42 round 2 established that an id alone does not identify a row — two families can ship the
same id — and keyed the view's own state by `(family, id)`:

```python
# yulon/ui/controller_view.py
self._manifests[(manifest.type, manifest.id)] = manifest
```

The report did not follow. So a report cannot be attributed back to the row that produced
it when two rows share an id: the session facts are keyed one way and the report is keyed
the other.

## Why it has not bitten yet

`apply_module()` is called with a `Manifest`, which carries its own `type`, so every
*producer* of a report knows the family at the moment it builds one. Only the report itself
forgets. That makes this cheap — the value is in hand at every construction site — and it
also means nothing today is wrong on screen; it is a latent mis-attribution waiting for two
same-named rows.

## Definition of done

1. **`ApplyReport` carries `family: ManifestType`**, set at every construction site from the
   manifest already in scope.
2. **Every consumer that matches a report to a row matches on `(family, item_id)`** — found
   by grep, not by memory, and the list of sites goes on the ticket.
3. A test builds two manifests with the same id in different families, runs both, and asserts
   each report lands on its own row.

## Evidence the ticket owes

The gate's last line and a named mutation per test. The mutation that matters: drop `family`
from the match key and see the test fail — if it still passes, the fixture is not two rows
that share an id.

## The fix — 2026-09-14

`ApplyReport.family: ManifestType` — required and keyword-only. No default: a default is a guess
at the family, and a chip on the wrong family's row is a lie where a missing chip is a gap.

**Construction sites** (`grep -rn "ApplyReport(" yulon`):

- `apply.py::Applier._report` — `family=manifest.type`.
- `controller_wow_tortoise/autoupdate.py::_with_note` — carries `report.family` through.

**Consumers that match a report to a row** (`grep -rn "\.item_id" yulon`):

- `controller_view.py::_module_done` — the record `forget()` now matches `(type, id)`.
- `controller_view.py::_note_session_facts` — refuses a report whose `(family, id)` is not the
  press on record, and keys the facts by the REPORT's family.
- `controller_view.py::_format_report` — displays the id; no match, unchanged.
- `modules_panel.py::VersionCache.forget` — kept by bare id on purpose: an update check's key
  still carries no family, and forgetting a same-id twin costs one re-read.

**Tests.** A view test with two `twin` manifests (`module` and `mod`) whose third press delivers
a `mod` report while the `module` twin is on record; an applier test driven with an `ale`; the
Tortoise guard test asserts `family == "mod"` through `_with_note`.
