---
type: How-to
title: "Updating Yu'lon"
description: "How Yu'lon tells you about a new version, and what Update now does on each system."
tags: [update, self-update, release, checksum, appimage]
resource: pylauncher/yulon/selfupdate/
sources:
  - id: pyreadme
    resource: pylauncher/README.md
    title: "Launcher README: update behaviour per platform"
  - id: readme
    resource: README.md
    title: "Project README: Updates"
  - id: detect
    resource: pylauncher/yulon/selfupdate/detect.py
    title: "Install kinds and button labels"
  - id: updatebar
    resource: pylauncher/yulon/ui/widgets/update_bar.py
    title: "The update line under the header"
  - id: updatedialog
    resource: pylauncher/yulon/ui/widgets/update_dialog.py
    title: "The update window and its buttons"
  - id: main
    resource: pylauncher/main.py
    title: "Check for Yu'lon updates button"
  - id: dialog
    resource: pylauncher/yulon/ui/widgets/update_dialog.py
    title: "The update window"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:25:00Z
status: draft
---

# Updating Yu'lon

Yu'lon checks for a newer public version when it starts, and can replace itself when you say so. Your game servers keep running while it updates.[^dialog]

## How it finds a new version

- It asks GitHub Releases at most once a day, and only looks at public releases (tags ending `-Public`). Test builds are never offered.[^pyreadme]
- If there is one, a line under the header says "Yu'lon *new* is available (you have *old*)." with a **See what's new** button.[^updatebar]
- **Check for Yu'lon updates** in [Settings](/getting-started/settings-and-themes.md) asks right away and tells you either way.[^main]

## Steps

1. Press **See what's new**. A window titled "Update to Yu'lon *version*" shows the release notes.
2. Choose a button:
   - **Update now** - downloads, checks, replaces Yu'lon and restarts it.
   - **Later** - asks again next launch.
   - **Skip this version** - hides that version until a newer one comes out.[^pyreadme]

## What the main button does

Yu'lon only installs a release it can prove. It checks the download against the release's `SHA256SUMS` before anything is unpacked, and it starts the new build once as a test before touching yours. If any of that fails, it says why and your install stays as it was.[^pyreadme]

| You run | The button | What happens |
|---|---|---|
| Linux AppImage, Linux tar.gz folder, Windows zip folder | **Update now** | Downloads, checks, replaces itself and restarts. |
| macOS | **Download** | Downloads and checks the `.dmg`, then opens it for you to install. |
| A folder you cannot write to | **Download** | Saves the checked file into your Downloads folder. |
| A release without a checksum list | **Open download page** | Opens the release page instead.[^detect] |

The project describes these update paths as written and unit-tested; the live proof on a real machine of each kind is still owed.[^pyreadme]

## Your own files are safe

Yu'lon replaces only the files it shipped. Notes or saves you keep beside the program stay where they are. If the update would have to replace something Yu'lon did not put there, it stops and offers the download instead.[^pyreadme]

## If something goes wrong

- **Two copies of Yu'lon are open.** The one where you pressed Update says so and stops. Close the other and try again.[^pyreadme]
- **An update stopped part-way.** Yu'lon touches nothing more and tells you what to do on its message bar. The old version is in a folder called `.yulon-old` next to the app. Hidden folders `.yulon-new`, `.yulon-download` and `.yulon-old` are removed the next time the new version starts. On Windows they are ordinary visible folders.[^pyreadme]
- **Putting the old version back by hand:** first delete or move away every file and folder in the Yu'lon folder that also exists inside `.yulon-old`. Then move everything in `.yulon-old` except `.yulon-marker`, `helper-started`, `helper.lock`, `helper.log` and `stand-down` into the Yu'lon folder, and delete `.yulon-old` and `.yulon-new`. Do step one first, or Yu'lon will not start.[^pyreadme]
- With the AppImage, the file inside `.yulon-old` is the version you had.

[^pyreadme]: pylauncher/README.md, "Odds and ends": the daily check, the per-platform table, the SHA256SUMS rule, and the put-it-back steps. README.md "Updates" says the same.
[^updatebar]: update_bar.py builds the "is available" line and the See what's new button.
[^dialog]: update_dialog.py: the title, "Your game servers keep running while Yu'lon updates.", and Later and Skip this version.
[^main]: main.py creates the Check for Yu'lon updates button inside Settings.
[^detect]: selfupdate/detect.py chooses Update now, Download or Open download page.
