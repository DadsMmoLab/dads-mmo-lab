---
type: Concept
title: "What Yu'lon installs on your computer"
description: "What the app sets up for you on Linux, Steam Deck, Windows and macOS, and what it will ask you for."
tags: [docker, wsl2, steamos, windows, macos]
resource: pylauncher/yulon/platform.py
sources:
  - id: readme
    resource: README.md
    title: "Project README with the per-OS table"
  - id: platform
    resource: pylauncher/yulon/platform.py
    title: "OS detection and Docker/WSL2 setup and their questions"
  - id: catalogjson
    resource: pylauncher/yulon/catalog/catalog.json
    title: "Which platforms each game installs on"
  - id: native
    resource: pylauncher/yulon/catalog/native.py
    title: "The install engine and its Docker setup messages"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# What Yu'lon installs on your computer

Game servers run inside **Docker**, and Docker needs a Linux kernel. Yu'lon sets this up for you, but it is still there. Here is what happens on each system.[^readme]

| You are on | What Yu'lon sets up | What that really is |
|---|---|---|
| Linux (Ubuntu, Fedora, Arch, and so on) | Docker Engine, through your system's package manager | Ordinary containers, no virtual machine |
| Steam Deck (SteamOS) | Docker Engine; SteamOS is briefly unlocked and locked again | Ordinary containers |
| Windows 10 or 11 | WSL2, then Docker Desktop | A small Linux machine managed by Windows |
| macOS | Docker Desktop | Docker Desktop runs the containers in its own Linux machine |

## When does this happen?

When you press **Install** and Docker is not answering, Yu'lon checks what is installed and sets up what is missing. On Windows and macOS it tells you this can mean downloading Docker Desktop and waiting for its engine, "up to a few minutes with no output", and that you can stop at any time.[^platform]

## What it will ask you for

Yu'lon cannot do everything silently. It asks in plain words for:[^readme]

- **Your computer's password**, when your system needs one to install software (sudo on Linux, the Windows administrator prompt, the Mac password). Yu'lon says the password goes to sudo and is never saved. Leaving it empty skips the steps that need it.[^platform]
- **Permission to join the docker group** (Linux). The question warns that this is effectively full root access on the machine. If you say yes, you log out and back in once, then press **Install** again.[^platform]
- **A restart on Windows.** After WSL2 is turned on, Windows must restart once. Yu'lon tells you, and you start the app again afterwards.[^platform]
- **Router settings for internet play.** Yu'lon can detect and explain them, but it cannot log in to your router.[^readme]
- **Your own game client.**

## On Windows, WSL is hidden, not gone

You do not need to use WSL yourself. It is simply what Docker Desktop runs on.[^readme]

## On a Steam Deck

SteamOS keeps its system files read-only. Yu'lon unlocks them to install Docker and locks them again.[^readme] A SteamOS update removes Docker again. If that happens, the Server tab offers **Reinstall Docker after a SteamOS update…**. It asks first, explains what it will change, and does not touch your server folders.[^platform] See [Common problems](/getting-started/common-problems.md).

## Which games work where

Every game installs on Linux and Windows except where a tile says otherwise. On macOS only WotLK and WoW Unbound can be installed.[^readme] On a platform a game does not support, the tile says "Installer needs ..." and the **Install** button is disabled.

[^readme]: README.md, the table "What the app installs for you" and the list of things it cannot do silently. The macOS limit is in pylauncher/README.md and the game catalog (install platforms).
[^catalogjson]: Install platforms per game.
[^native]: The Docker Desktop wait message.
[^platform]: Question and message texts come from the system checks (sudo password, docker group, Docker start, SteamOS repair) and the install steps (the Docker Desktop wait message).
