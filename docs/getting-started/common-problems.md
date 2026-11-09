---
type: Troubleshooting
title: "Common problems"
description: "Fixes for the problems people hit while installing Yu'lon and their first server."
tags: [troubleshooting, docker, install, disk-space, ports]
resource: pylauncher/yulon/catalog/preflight.py
sources:
  - id: preflight
    resource: pylauncher/yulon/catalog/preflight.py
    title: "The refusal and warning texts"
  - id: platform
    resource: pylauncher/yulon/platform.py
    title: "Docker setup, folder rules and SteamOS repair texts"
  - id: clientdir
    resource: pylauncher/yulon/catalog/families/clientdir.py
    title: "Client folder refusals"
  - id: pyreadme
    resource: pylauncher/README.md
    title: "AppImage and FUSE note"
  - id: dockerbanner
    resource: pylauncher/yulon/ui/widgets/docker_banner.py
    title: "Open Docker Desktop and Try again"
  - id: catalogview
    resource: pylauncher/yulon/ui/catalog_view.py
    title: "Install dialogs"
  - id: changelog
    resource: CHANGELOG.md
    title: "Release notes, where Free Docker space... is announced"
  - id: banner
    resource: pylauncher/yulon/ui/widgets/docker_banner.py
    title: "The Docker banner and its buttons"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Common problems

Most install problems are caught by the [Pre-install checks](/getting-started/pre-install-checks.md) before anything is built, and the box that appears says what to change. Find your message below.

## "Cannot install yet"

This box lists each refused check. Fix the line it names, then press **Install** again. Nothing was written.[^catalogview]

### Docker did not answer

On Linux the usual causes are that Docker is stopped, or your account is not in the `docker` group. On Windows and macOS, start Docker Desktop and wait until it says "Engine running".[^platform] On the Server tab an amber banner offers **Open Docker Desktop** and **Try again**.[^banner] If Yu'lon asked to add you to the docker group and you said yes, log out and back in once, then press **Install** again.[^platform]

### Docker Compose is missing or too old

Yu'lon needs Docker Compose version 2 (`docker compose version`, with a space). If yours is too old, Yu'lon offers to download the current one into your own plugin folder. It checks the download first and needs no administrator password.[^platform]

### Not enough memory

The build is killed part-way if Docker has too little memory. On Windows, Docker Desktop takes its memory from Windows: set the `memory=` line in `%UserProfile%\.wslconfig`, run `wsl --shutdown`, start Docker Desktop again, and retry. On macOS, raise the memory in Docker Desktop's Resources settings. On Linux, close other programs or add swap.[^preflight]

### Not enough disk space

Two drives matter: the server folder, and Docker's own disk. If the message is about Docker's disk, moving your server folder will not help. Free space on the drive Docker stores on, or move Docker's storage. Otherwise free space or pick another folder.[^preflight] Once a server exists, **Free Docker space...** on its Backups & updates tab removes Docker's build cache and old builds.[^changelog]

### That folder will not work

Yu'lon refuses a network path, and a folder inside WSL on Windows, because Docker Desktop would see an empty folder. Pick a folder on your computer's own disk.[^platform] If it says a container cannot see the folder, share it with Docker (on Docker Desktop, its file-sharing settings).[^preflight]

### The server's ports are in use

Another server's containers already use the ports. Stop that server or remove its containers, then try again.[^preflight] A plain "something is listening" line is only a warning.

### The client folder was refused

Pick the folder the game itself is installed in, the one that holds a `Data` folder. Not a launcher, an installer or a backup. If a required file is missing, the client is the wrong one or incomplete. If the version is wrong, pick a client of the version the tile names, and press **Install** again.[^clientdir]

## Install stopped part-way

Press **Install** again. It continues where it stopped.[^catalogview] If the box says "Another job is still running.", wait for the other job or stop it from its log.

## Windows asks for a restart

After WSL2 is enabled, restart Windows once, then open Yu'lon and press **Install** again. If Yu'lon could not enable WSL2 itself, open an Administrator PowerShell, run `wsl --install --no-distribution`, and restart.[^platform]

## Steam Deck: Docker disappeared after an update

A SteamOS update removes Docker. Press **Reinstall Docker after a SteamOS update...** on the server's tab. It asks first, may ask for your password, resets the package keyring (it says what that changes), and does not touch your server folders.[^platform]

## The AppImage will not open on Linux

Your system may lack FUSE. Use the `.tar.gz` download instead. See [Download and start the app](/getting-started/download-and-start-the-app.md).[^pyreadme]

## Windows or macOS warns about the app

That is the unsigned-app warning. Choose "Run anyway" (Windows) or right-click and Open (macOS).

## Still stuck

Open **Log** in the top bar and press **Save logs for support...**. The zip has passwords taken out. Send it when you ask for help. See [Where Yu'lon keeps its files](/getting-started/where-files-live.md).

[^catalogview]: Titles and messages on the Catalog page: "Cannot install yet", "Install stopped", "Another job is still running."
[^platform]: Docker, Compose, WSL2, folder and SteamOS texts come from the system checks.
[^banner]: The Docker banner defines the two buttons.
[^preflight]: The pre-install checks give remedies for memory, space, folder sharing and ports.
[^clientdir]: The client folder checks give the refusal sentences.
[^pyreadme]: pylauncher/README.md, FUSE paragraph.
[^changelog]: The release notes: Free Docker space... on Backups & updates.
