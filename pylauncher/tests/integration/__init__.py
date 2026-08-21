"""Integration tests that drive a real Docker daemon (roadmap Phase 1.5).

Everything in this package is marked `integration` and skips itself when no
Docker daemon is reachable, so the default `pytest` run stays green on a
machine (or CI runner) without Docker. See `conftest.py` for the gates.
"""
