---
type: Concept
title: Keep Yu'lon running and start it at sign-in
description: Your servers keep running when you close the Yu'lon window, and Yu'lon can open in the tray when you sign in.
tags: [tray, autostart, sign-in, background]
resource: pylauncher/yulon/autostart.py
sources:
  - id: autostart
    resource: pylauncher/yulon/autostart.py
    title: The sign-in entry for each system
  - id: tray
    resource: pylauncher/yulon/ui/tray_settings.py
    title: The two tray switches and their labels
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Keep Yu'lon running and start it at sign-in

Yu'lon has no setting that starts a particular server by itself. What it does have are two switches about the Yu'lon app: stay in the tray when you close the window, and open in the tray when you sign in to your computer.[^tray]

## Your servers keep running either way

Closing or hiding the Yu'lon window does not stop your servers. The Settings note says so directly: "Your servers keep running either way."[^tray] To stop a server, use **Stop** on its Server tab ([Start, stop and restart](/running-a-server/start-stop-and-restart.md)).

## The two switches

Open **Settings** and find:[^tray]

1. **Keep running when Yu'lon is closed**: closing the window leaves Yu'lon in the system tray.
2. **Start the tray when I sign in**: Yu'lon opens hidden in the tray each time you sign in. It needs the first switch on ("Turn on keeping Yu'lon in the tray first.").

Both are off until you turn them on.[^autostart]

### What you'll see

If your desktop has no system tray, closing Yu'lon quits it, and the Settings note says so.[^tray]

## Which system does what

- **Windows**: Yu'lon adds an entry that runs at sign-in.
- **Linux**: it writes a desktop autostart entry, which most desktops read.
- **macOS**: it writes a launch agent. This is written but has not been proved on a Mac.[^autostart]

The sign-in switch only works for an installed Yu'lon. When you run Yu'lon from a source checkout, it is greyed with the line "Only an installed Yu'lon can start when you sign in."[^autostart]

## If something goes wrong

If a switch will not turn on, the line under the switches says why. If you moved or deleted the Yu'lon program after turning the sign-in switch on, the old entry starts nothing; Yu'lon treats that as off, so turn it on again.[^autostart]

If your servers do not come back after a restart of the computer, press **Start** on each one. See [Start, stop and restart](/running-a-server/start-stop-and-restart.md).

[^autostart]: The sign-in entry is per system and is "on" only if the program it names exists.
[^tray]: Switch labels and the note are in the tray settings code.
