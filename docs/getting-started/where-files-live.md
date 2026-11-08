---
type: Reference
title: "Where Yu'lon keeps its files"
description: "Where the app's own settings and logs live, and where your server files go."
tags: [files, folders, logs, config, state]
resource: pylauncher/yulon/platform.py
sources:
  - id: readme
    resource: README.md
    title: "Project README: where things live"
  - id: platform
    resource: pylauncher/yulon/platform.py
    title: "config_dir: the per-OS app folder"
  - id: state
    resource: pylauncher/yulon/state.py
    title: "state.json, the remembered servers"
  - id: themespec
    resource: pylauncher/yulon/ui/theme_spec.py
    title: "The themes folder"
  - id: logsview
    resource: pylauncher/yulon/ui/logs_view.py
    title: "Save logs for support"
  - id: catalogjson
    resource: pylauncher/yulon/catalog/catalog.json
    title: "Default server folder names"
  - id: pyreadme
    resource: pylauncher/README.md
    title: "Launcher README, odds and ends"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Where Yu'lon keeps its files

Yu'lon keeps two kinds of files apart: its own settings and logs, and your server files.[^readme]

## The app's own folder

This holds Yu'lon's settings, remembered servers and log. It is not your server data.[^platform]

| System | Folder |
|---|---|
| Linux and Steam Deck | `~/.local/share/yulon/` (or under `XDG_DATA_HOME` if you set it) |
| Windows | `%APPDATA%\yulon\` |
| macOS | `~/Library/Application Support/yulon/` |

Inside it you will find:

- `state.json` - the servers Yu'lon remembers.[^state]
- `yulon.log` - Yu'lon's own log.[^platform]
- `ui.json` - what the windows remember: sizes, positions and the server addresses you typed into a client window.
- `update.json` - what Yu'lon remembers about update checks. Deleting it only costs one more check.[^pyreadme]
- `themes/` - theme files you can edit. See [Settings and themes](/getting-started/settings-and-themes.md).[^themespec]
- `downloads/` - installers Yu'lon downloads for itself, such as Docker Desktop's on Windows.[^platform]

## Your server files

Your server files go wherever you chose at install time. The suggestion is a folder in your home folder named `yulon-` plus the game, for example `yulon-wotlk`, `yulon-tbc` or `yulon-coa`.[^catalogjson] A server installed by an earlier version keeps its own folder.[^readme] Server files are never inside the app's folder.

## Logs and support files

You do not have to dig through folders for logs. Open **Log** in the top bar, or **Diagnostic logs...** in Settings. Press **Save logs for support...** and you get one zip of every log and settings file, with passwords taken out, to send when something goes wrong.[^logsview]

## If something goes wrong

If an update stops part-way, Yu'lon may leave hidden folders named `.yulon-new`, `.yulon-download` and `.yulon-old` next to the app. They are removed the next time the new version starts. See [Updating Yu'lon](/getting-started/updating-yulon.md).[^pyreadme]

[^readme]: README.md, "Where things live".
[^platform]: The settings folder, the log folder and the downloads folder names are set by the app for each system.
[^state]: The app keeps its remembered state in one file named state.json.
[^pyreadme]: pylauncher/README.md, "Odds and ends".
[^themespec]: Custom themes go in themes in `<config folder>/themes`.
[^logsview]: The Logs page: the button text and its tooltip.
[^catalogjson]: default_server_dir in the game catalog.
