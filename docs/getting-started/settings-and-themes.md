---
type: How-to
title: "Settings and themes"
description: "Change the colour theme, keep Yu'lon in the system tray, start it when you sign in, and open the logs."
tags: [settings, themes, tray, sign-in, appearance]
resource: pylauncher/yulon/ui/tray_settings.py
sources:
  - id: settings
    resource: pylauncher/yulon/ui/tray_settings.py
    title: "The Settings page"
  - id: themespec
    resource: pylauncher/yulon/ui/theme_spec.py
    title: "Theme loading and the themes folder"
  - id: themes
    resource: pylauncher/yulon/ui/themes/arthas.json
    title: "One of the bundled theme files"
  - id: sidebar
    resource: pylauncher/yulon/ui/sidebar.py
    title: "The Settings button"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:25:00Z
status: draft
---

# Settings and themes

The Settings page changes how Yu'lon looks and whether it stays in the system tray. Open it with **Settings** in the top bar.[^sidebar]

## Change the colour theme

1. Open **Settings**.
2. Next to **Color theme:** open the dropdown and pick a theme. The app changes at once. A strip of small colour swatches under the dropdown previews the main colours.[^settings]

The default theme is Arthas.[^themespec] The bundled themes are:[^themes]

- Arthas, Floral, Jungle, Ocean (and dark versions of Floral, Jungle and Ocean)
- Organic Dark and Organic Light
- Dracula Dark and Dracula Light
- 98x Dark and 98x Light
- Gruvbox Dark and Gruvbox Light, each in Hard, Medium and Soft

Every theme colours the cards, tooltips and icons, not only the background.

## Make your own theme

1. Press **Open themes folder...**. It opens the folder where theme `.json` files live, so you can add or edit custom themes.[^settings]
2. Copy one of the files, give the copy a new `id` made of lowercase letters, digits, `-` or `_`, and change its colours.
3. Come back to Settings. Custom themes appear in the same dropdown.[^themespec]

Yu'lon fills the themes folder with the bundled themes the first time you open it, and it does not overwrite files you changed.[^themespec]

## Keep Yu'lon in the system tray

Two checkboxes control this:[^settings]

- **Keep running when Yu'lon is closed** - closing the window leaves Yu'lon in the tray.
- **Start the tray when I sign in** - starts it when you log in. It stays greyed out until the first box is on, with the note "Turn on keeping Yu'lon in the tray first."

Your servers keep running either way.[^settings]

If your desktop has no system tray, Yu'lon says so: "This desktop has no system tray, so closing Yu'lon quits it, as it always has."[^settings]

## Other buttons

- **Check for Yu'lon updates** - see [Updating Yu'lon](/getting-started/updating-yulon.md).
- **Diagnostic logs...** - opens Yu'lon's logs and lets you save a support file when something goes wrong.[^settings]

## If something goes wrong

If a setting cannot be saved, Yu'lon tells you it "lasts only until Yu'lon closes". If starting at sign-in cannot be turned on or off, it shows the reason.[^settings]

[^sidebar]: The tooltip on Settings reads "Change the color theme, keep Yu'lon in the system tray, and start it at sign-in".
[^settings]: Labels and messages are in tray_settings.py (SettingsView).
[^themespec]: theme_spec.py: DEFAULT_THEME is arthas; the themes folder is created and seeded by sync_user_themes.
[^themes]: The bundled themes are the .json files in ui/themes.
