# Morning report ("Phase 8 Closing Night") — how it is built, what to add tonight

Artifact URL (redeploy to the SAME url): https://claude.ai/code/artifact/85d5b01e-a039-4160-8fcb-7c845cf8778f

Old scratchpad: C:\Users\perzi\AppData\Local\Temp\claude\C--Users-perzi-dads-mmo-lab\e6db1900-f1cd-4099-995e-e1ef6bec1004\scratchpad\
- `closing-night.tmpl.html` — the page with `{{IMG:<name>}}` placeholders (title "Phase 8 Closing Night").
- `shots-morning/<name>.png` — the images; build = replace each `{{IMG:x}}` with `data:image/png;base64,<shots-morning/x.png>`; write `closing-night.html`; publish with the Artifact tool (file path `closing-night.html`, `url` above). Keep under 16 MB (downscale PNGs with Pillow if needed).
- Rules from the Artifact tool: no `<html>/<head>/<body>` wrappers, `<title>` first, favicon stays, theme tokens, no external assets.

## Sections to add for 2026-09-09 daytime (the exit pass), in this order
1. "What was pressed today" table = the ticket table from PR #146's body (`lead/pr146-new-body.md`), plus 27/29 and the 8.6/8.8 state.
2. Screenshots, one per feature, from the gate folders on `yulon-phase8b`:
   - T5 (8.6): `pyplan/gates/8.6-spec-level-dismiss-yulon-ubuntu2-2026-09-09/` — `panel-5-*.png` (the seam's refusal, three rows), `panel-6-*.png` ("3 bots left"), `panel-9-*.png` (levels 42/1/37), `client-4-*.png` (`Total 0 specs found`).
   - T7 (8.7a): `pyplan/gates/8.7a-guard-wired-yulon-ubuntu2-2026-09-09/` — the three PNG renderings (refusal, success report with "started the database alone", remove report); say they are renderings of the saved strings.
   - T3 (7.10): `pyplan/gates/7.10-clause35-ubuntu2-2026-09-09/shots/` — one panel frame; the `failrestore/restore.log` lines 14/17/21 as text.
   - T14 (Tortoise updates button): `pyplan/gates/tortoise-updates-button-m910q-2026-09-09/` when it lands — button, confirmation, report frames.
   - T13 (uninvite contract) when it lands.
3. "Open questions" = the tail of `morning-questions-2026-09-09.md` (old scratchpad): budget rules, Codex cooldown, clock note, 8.6 call, 8.8 ask (Steam login / shortcuts.vdf), the m910q index, release version/branch, DML VM.
4. Keep the existing night sections below, unchanged.

## Budget rules in force (owner 13:00-13:05 CEST)
One reviewer (Codex; Opus while Codex is on cooldown; Fable only for live-box or >300-line tickets); two-round cap then the lead fixes by hand; Sonnet hands for small tickets.
