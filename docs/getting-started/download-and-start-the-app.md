---
type: How-to
title: "Download and start the app"
description: "Get the Yu'lon download for your system, get past the unsigned-app warning, and open it."
tags: [download, appimage, windows, macos, unsigned]
resource: README.md
sources:
  - id: readme
    resource: README.md
    title: "Project README: unsigned builds"
  - id: pyreadme
    resource: pylauncher/README.md
    title: "Launcher README: downloads and the AppImage note"
  - id: release
    resource: .github/workflows/release.yml
    title: "Which files each release carries"
  - id: detect
    resource: pylauncher/yulon/selfupdate/detect.py
    title: "Artifact names per install kind"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:25:00Z
status: draft
---

# Download and start the app

Yu'lon comes as one download per system. You get it from the project's GitHub Releases page and open it. No installer is needed.[^readme]

## Steps

1. Open the Yu'lon releases page on GitHub and pick the newest release.
2. Download the file for your system:
   - **Linux or Steam Deck:** `Yulon-<version>-x86_64.AppImage`, or `Yulon-<version>-x86_64.tar.gz`.
   - **Windows:** `Yulon-<version>-windows-x64.zip`. Unzip it and run `yulon.exe`.
   - **macOS:** `Yulon-<version>-macos.dmg`. Open it and install the app.[^release]
3. Start it.
   - AppImage: make the file executable, then open it.
   - tar.gz: run `tar -xzf Yulon-*-x86_64.tar.gz && ./yulon/yulon`.[^pyreadme]

## The unsigned-app warning

The downloads are not code-signed yet, so your system will warn you the first time.[^readme]

- **Windows:** SmartScreen warns. Choose "Run anyway".
- **macOS:** Gatekeeper warns. Right-click the app and choose Open.

This is a known trade-off of the current releases.

## Which Linux file should I take?

An AppImage needs a helper called FUSE, and not every Linux ships it. On Arch it is missing by default. There the AppImage is refused with "No suitable fusermount binary found" before Yu'lon even starts.[^pyreadme]

If that happens, take the **.tar.gz** instead. It is the same app and needs no FUSE.[^pyreadme] Another way out is to start the AppImage with `--appimage-extract-and-run`, which works but unpacks the whole image on every launch.[^pyreadme]

## What you'll see

A window with a top bar (Launcher, Log, Catalog, Servers, Settings) and the Catalog. See [First run and the Catalog](/getting-started/first-run-and-the-catalog.md).

## If something goes wrong

- **Mac:** only Apple-silicon Macs get a download. The project builds the `.dmg` on an Apple-silicon runner and has no Intel build.[^pyreadme]
- More in [Common problems](/getting-started/common-problems.md).

Once you are running, Yu'lon can update itself: see [Updating Yu'lon](/getting-started/updating-yulon.md).

[^readme]: README.md, "Unsigned builds".
[^release]: File names come from the release workflow and the self-updater's naming rule (Yulon-<tag><suffix>).
[^pyreadme]: pylauncher/README.md, "Odds and ends" and the platform table (footnotes on the dmg and the AppImage).
