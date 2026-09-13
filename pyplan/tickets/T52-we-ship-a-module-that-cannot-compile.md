# T52 — we ship a module that cannot compile, and we pin nothing

**Status:** FILED — diagnosed from a user's build, fix is an owner decision.
**Filed:** 2026-09-13 by the lead, from Boatmurdered's report (read on the owner's instruction).
**Branch:** `report/t52-challenge-modes-unbuildable`, from `upstream/Yulon`. No code change yet:
which way to fix it is a judgement about gameplay, not about correctness.

## The failure, named exactly

```
/azerothcore/modules/mod-challenge-modes/src/ChallengeModes.cpp:448:95:
  fatal error: non-virtual member function marked 'override' hides virtual member function
448 |  void OnPlayerResurrect(Player* player, float /*restore_percent*/, bool /*applySickness*/) override

/azerothcore/src/server/game/Scripting/ScriptDefines/PlayerScript.h:644:18:
  note: hidden overloaded virtual function 'PlayerScript::OnPlayerResurrect' declared here:
        type mismatch at 3rd parameter ('bool &' vs 'bool')
```

AzerothCore takes `bool& applySickness`; the module declares `bool` and marks it `override`.
The build runs with `-Wfatal-errors`, so it stops there. **Every rebuild, at whatever object
index the module lands on.** This user hit it three times across two versions.

## It is our entry, and it is unpinned

`manifests/wow-wotlk/modules/mod-challenge-modes.json`:

```json
"source": { "repo": "nl-saw/mod-challenge-modes" }
```

No `branch`, no `rev`. That repository's `master` carries the stale signature **today**, at
lines 448 and 641 — verified against `raw.githubusercontent.com`. So the module we ship cannot
build against the core we build, and has not been able to since AzerothCore changed the hook.

Nothing the user did caused it and nothing they can do about it is obvious: the Modules tab
offered them the module, installed it, and the rebuild then failed with an error that (before
T38/T50) did not even name it.

## The wider defect, which outlives this one module

**Not one manifest pins anything.** Every entry is `{"repo": "..."}`, so Yu'lon clones whatever
that repository's default branch is at the moment of install, and compiles it against whatever
AzerothCore revision the entry builds. Two moving targets. A build that worked last month can
fail today with no change by the user, and a user who installs the same module on two machines
a week apart can get two different results.

That is the same shape as this defect and will produce the next one.

## The options, for the owner

1. **Drop the entry.** Honest, and it stops offering something that cannot work.
2. **Point it at `ExWoW/mod-challenge-modes`.** 14 commits ahead of `nl-saw`, and its lead
   commit is literally *"feat: update module for latest AzerothCore API changes"*; `bool&` is
   correct there at lines 452 and 633. **But it is not a like-for-like swap** — the same 14
   commits also carry *"Remove permadeath logic for hardcore mode"*, reward changes and config
   changes. Repointing the entry silently changes what the module DOES for everyone who
   installs it.
3. **Pin it and keep it broken**, with the row marked unavailable, until upstream fixes it.

Recommendation: (1) now, and open the pinning question as its own piece of work. (2) is a
gameplay decision that belongs to the owner, not to a bug fix.

## What this ticket owes if it becomes a fix

- Whichever route: a test that the catalog cannot offer a module whose entry names a
  repository revision this app has never built. Today nothing would catch this before a user's
  40-minute rebuild does.
- The user's own evidence is the acceptance test: the same module set, building past object
  1270 of 1922.

## Provenance

Three of the user's screenshots, read on the owner's instruction: the removal receipt showing
`1972 → 1922` objects after removing an unrelated module (which refuted the lead's first,
wrong diagnosis), and the Error tab naming `ChallengeModes.cpp:448`. See
`pyplan/gates/t52-challenge-modes-2026-09-13/`.
