# The test-suite segfault reproduces locally at ~1 in 4 — 2026-08-28

Observed on yulon-ubuntu (Ubuntu 24.04.4, Python 3.12.3, 8 vCPU / 23 GB), branch
`test/full-vm-run-2026-08-28` @ 66d4824e:

    pytest -q --ignore=tests/integration
    → 3 of 4 runs: 970 passed, 2 skipped in ~26s
    → 1 of 4 runs: Fatal Python error: Segmentation fault, inside pluggy/_callers.py

## Why this matters
This is a known, open, unfixed issue with a spike branch dedicated to it:
`spike/segfault-repro`, top commit **"spike: run the GUI suite 20x on CI to catch the segfault"**,
on top of `e8a845f8` "Yulon does not parse, and the GUI tests crash on window count (#112)".

So the project's current best reproduction is *twenty CI runs of the GUI suite*. A ~25% hit rate on
a 26-second local suite is a dramatically cheaper repro, and it is what would let someone actually
fix this rather than keep chasing it on CI.

## What is still needed to make it actionable
1. The rate as a measured fraction over 10-20 runs, not an adjective.
2. The **full** faulthandler traceback. `pluggy/_callers.py` is only the caller frame; the
   interesting frame is the test underneath it.
3. Whether the crash follows the GUI subset. The spike assumes GUI tests. If the crash also appears
   with the GUI tests excluded, the spike is aimed at the wrong target — which is itself the finding.
4. Environment: PySide6 version, whether `QT_QPA_PLATFORM=offscreen` is set, pytest/pluggy versions.

Diagnosis only — no fix should be attempted from here. A precise reproduction handed to an existing
spike branch is worth more than a guessed patch.

Note this is a **third** independent local-vs-CI divergence in this run, alongside
`mypy .` vs CI's `mypy yulon main.py`, and the SELinux test that only fails where SELinux enforces.
