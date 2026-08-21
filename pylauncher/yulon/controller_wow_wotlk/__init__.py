"""WotLK controller — siloed server-management logic for AzerothCore WotLK.

Each server gets its own controller/ package so game-specific behavior
(container names, DB layout, module quirks) stays isolated.

`controller.py` holds `WotlkController`, the base `yulon.controller.Controller`
bound to this game's `docker_ctl.SPEC`; `docker_ctl.py` keeps the function-style
re-exports for callers that don't want an object.
"""
