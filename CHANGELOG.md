# Changelog

Every release of Yu'lon, newest first.

<!--
How to add a line: under "## Unreleased" use only the headings "### New", "### Fixed" and "### Changed".
One plain line per change, at most about 120 characters, saying what the player gets. Bold button names are fine;
ticket ids, proof, test notes, numbers as evidence, backticks and commands are not: they go in the pull request.
New: Set a server's time zone on the Tuning tab with **Server time zone**.
Fixed: Sending gold to a character no longer crashes the app.
Changed: Opening Yu'lon while it is already running brings the open window to the front.
-->

## Unreleased

### New
- Centurion can be installed from the Catalog on Windows and Linux: level-60 PvP WoW on the 3.3.5a client, with bots.
- Each server has its own game launcher: press **▶** on its tab or **Play** to see who is online and press **PLAY**.
- **Make a ready-to-play client…** makes a copy of your client for one server, sharing the big game files to save space.
- A ready-to-play client can carry a server's patches and addons, and optional packs such as HD packs.
- Set a server's time zone on the Tuning tab with **Server time zone**.
- Centurion's **Characters** tab offers **Set level**, **Teleport**, **Send gold**, **Send everything worn** and more.
- Centurion's pathfinding data continues where it stopped instead of starting again from 0 %.
- On a Steam Deck, **Reinstall Docker after a SteamOS update…** brings back the Docker a SteamOS update removed.
- On Linux and the Steam Deck, folder pickers list your SD cards and USB drives.
- WoW TBC, Vanilla and Tortoise servers keep their log files in a logs folder inside the server folder.
- A server left broken by a failed update or rebuild is mended with **Repair server files…**, without a reinstall.
- A finished build that could not be started is kept, and the next rebuild uses it instead of compiling again.

### Fixed
- A module that only changes settings, such as Experience Rates, now shows as installed and can be removed.
- **Bigger Stacks** on TBC, Vanilla and Tortoise now shows as installed and can be removed.
- After you remove Experience Rates, setting its rates back by hand no longer makes it read as installed again.
- Removing a settings module whose file Yu'lon cannot read now says so instead of claiming nothing changes.
- The Server tab no longer says "restart loop" after Docker itself was stopped and started.
- Stop takes effect at once and never kills a loading world, and a clean put-back after Stop reads Stopped.
- On Windows, an Xbox or Xbox-style gamepad now moves around Yu'lon; before, its buttons were not read.
- Long questions such as **Rebuild the server…** scroll inside a dialog that fits the screen; the buttons always show.
- A fresh Centurion server no longer restarts over and over at the end of its install.
- A fresh Centurion server no longer crashes on its first start while it makes its bots.
- On Windows, Centurion no longer stalls at start-up; older installs need **Repair server files…**, then **Recreate containers…**.
- WoW TBC and Vanilla installs and rebuilds work again, with the bots version Yu'lon has tested.
- A Tortoise server no longer crashes after its weekly honor day; older installs press **Apply database corrections…**.
- Stopping a TBC, Vanilla or Tortoise server while its world loads no longer kills it and loses progress.
- A failed **Update the server to latest…** now also puts your databases back as they were.
- On WotLK, **Update the server to latest…** applies the new code's database updates, so the new build starts.
- When a failed update cannot put the old build back, the message says honestly which build the server is on.
- A rebuild waits for a slow Docker instead of throwing away the build it just finished.
- On WotLK, a kept build is no longer thrown away by a backup, a module update check or a settings save.
- **Stop** during a download or build now ends it at once instead of letting it run on.
- When **Stop** cannot put everything back, the log says what state the server is in and what to press.
- **Restore** on the Maintenance tab works with the server stopped, and leaves it stopped.
- **Re-extract map data** on Centurion works again, and a failed run keeps your old map data.
- Centurion's pathfinding line says how far a stopped run got and what the next press will do.
- On Linux and the Steam Deck, lower-case game file names are accepted on every game (a lower-case Data folder not yet).
- A fresh WoW TBC or Vanilla install gets the latest dungeon and raid updates.
- WoW WotLK's bots read a real settings file, so My Party, the Bots tab and Tuning see your bot settings.
- **Install** again after a failed build is no longer refused for space its own build cache already uses.
- **Install** again after an install whose world server kept restarting now finishes.
- **Install** no longer stops a running world server because its last rebuild failed.
- Opening Yu'lon no longer starts a stopped WSL distro, or the server you stopped inside it.
- Closing Yu'lon while the command channel is being set up no longer breaks the channel.
- A server command that worked but printed nothing is reported as done, not as a channel that is off.
- Typing a letter in a list, such as **Characters** or **Accounts**, picks that name instead of switching tabs.
- The **Level** box starts at the character's own level, and lowering a level asks first.
- The realm badge says **STOPPING**, **PARTLY UP** or **STATUS UNKNOWN** instead of a wrong OFFLINE.
- Installing again into a removed server's folder finds its ready-to-play client and offers **Play**.
- A module or server source whose update failed half-way can be updated again.
- **Check for updates** no longer offers to move a module back to an older release.
- On WotLK, the mod-playerbots update chip updates it, through **Update the server to latest…**.
- On TBC and Vanilla, updating the server keeps lines you added by hand to its Docker settings files.
- On Tortoise, **Revert** on a Tuning card no longer brings back a bot rebuild that would wipe restored bots.
- On Tortoise, a bot dashboard that could not be rebuilt after an update is reported instead of left running old.
- Installing or removing a database mod with the server stopped ends by naming the one press left: **Start**.
- On Linux with firewalld, the Networking tab no longer suggests removing a port your firewall already allows.
- The install's disk check measures the drive you moved Docker Desktop's disk to.
- Your client's read-only files stay read-only when Yu'lon removes a server's client, or you are told which one.
- The Modules list shows its modules on a small window instead of only its header.
- Questions with long button labels show the whole label, also on a Steam Deck screen.
- Removing a server while its **Modules** tab is still loading no longer prints an error.
- An "&" in a label or a server folder's name shows as written.

### Changed
- Failure messages say in plain words what went wrong, with the technical detail under **Details**.
- A command you must type yourself stands on its own line, so it is easy to copy.
- When Docker is not answering, the Server tab shows one box that says what to do on your computer.
- The Server tab is split into Realm, Play, Client, Command channel and a red-bordered Danger zone.
- On small windows, including the Steam Deck, every server tab scrolls instead of squeezing its buttons.
- A greyed button says why on its own tab, since a Steam Deck has no hover tooltips.
- The sidebar shows each server's full name and a dot for its realm, with **Catalog** and **Logs** pinned on top.
- Checkboxes, radio buttons, number boxes and drop-downs look like what they are, and are big enough for a Deck.
- The Tuning tab fits every window size, with a **Settings | Edit file** switch on small screens.
- The Modules tab's three server-build buttons are one **Server build ▾** menu, so the toolbar fits a Steam Deck.
- With a controller or the arrow keys, each direction goes to the nearest thing that way, and into a new tab.
- **Ctrl+Tab** moves between the Catalog, Logs and servers like the controller bumpers.
- On first run the Catalog says to choose a game, marks WotLK as Recommended, and changes nothing until **Install**.
- New installs suggest a folder named after the game, such as yulon-wotlk; existing servers stay where they are.
- On Windows, server folders and Yu'lon's saved passwords can be read only by your own account.
- Opening Yu'lon while it is already running brings the open window to the front instead of a second copy.
- **Make a ready-to-play client…** picks a folder outside OneDrive by itself when your client is inside it.
- **Check for updates** on the Modules tab gives a true count of commits behind, or none when it cannot know.
- Messages that send you to a press name it as labelled, say where it is, and pick one that can fix the problem.
- A build that loses Docker, often from too little memory, says so in plain words and what to try.
- After **PLAY**, the launcher says when the game was started instead of a lasting "is starting".
- The **Logs** tab colours warnings amber and errors red.
- Fresh Tortoise installs build TortoiseBots v2026-09-28: bots respawn nearby and travel to zones that fit them.
- Removing **Bigger Stacks** asks first and says every item's stack size goes back.

## v0.8.90-Public — 2026-09-26

### New
- Yu'lon tells you when a new public release is out and shows what is in it before you update.
- Yu'lon installs the update it offers: it downloads, checks and starts the new version for you.
- A **Logs** tab under Catalog shows Yu'lon's logs, and **Save logs for support…** writes one file to send.
- A server can be removed from Yu'lon without deleting anything, and brought back with **Use existing…**.
- The number of random bots can be changed on the Bots tab, on all four games.
- **Reset to default ▾** on the Tuning tab puts a server's settings back to how Yu'lon installed them.
- Every module question is asked when you install, and Yu'lon remembers your answers.
- The Server tab says when upstream has new code for your server.
- A server inside a WSL distro can be rebuilt and updated from Yu'lon on Windows.
- Tortoise: turn on the **Bot dashboard** to see a live map of every bot, their health and their gear.
- Tortoise: **Rebuild random bots…** gives the random bots the newest gear, skills and professions.
- On Tortoise, updating the server takes the newest TortoiseBots release, and the TortoiseBots Manager addon updates.
- Older Tortoise, Vanilla and TBC servers are offered **Repair server files…** to take the newest server file fixes.

### Fixed
- Stopping Docker with a server running no longer loses the characters the world was saving.
- A server inside a WSL distro keeps running after Yu'lon closes.
- Sending gold to a character no longer crashes the app.
- Walking the Characters list no longer freezes the window.
- With a controller connected, as on every Steam Deck, the app no longer crashes when it closes.
- Closing Yu'lon while something is stuck no longer ends in a crash report.
- R, L, Space and Backspace type into a text field again.
- A Tortoise server updated onto the new bots module keeps its bots.
- The random-bot count you set survives a Repair, an update and the command channel being switched.
- A Repair keeps the command channel on.
- Turning the command channel on keeps a Fedora server's SELinux labels.
- Installing into the folder that already holds the server is no longer refused for space its build already used.
- Baby, Nerf, Buff and Extreme Buff Mobs show as installed, cannot be stacked, and refuse extreme multipliers.
- Hearthstone Cooldown Tweaks asks which cooldown you want.
- Accountwide Systems installs again.
- Saving a Tuning card no longer makes a private settings file readable by everyone.
- Internet or LAN play on Linux with firewalld, such as a Steam Deck, no longer refuses a reload it does not need.
- Test builds are no longer offered as updates, and every newer public release is.

### Changed
- Each release lists what is new in it, and the app reports the exact version it was built from.
- Fresh WoW WotLK installs build AzerothCore and mod-playerbots as of 20 September 2026.
- Fresh WoW TBC and Vanilla installs build this week's CMaNGOS code and bots, with several bot crash fixes.
- Fresh Tortoise installs build TortoiseBots v2026-09-26: new bot gear, and bots fill dungeon and battleground queues.

## v0.8.7-Public — 2026-09-18

### New
- **Update the server to latest…** builds a server from its upstream's newest code, with a backup offered first.

### Fixed
- The Modules tab says which modules are installed.
- A build that fails says what the compiler said.
- On WotLK, the level you choose for a bot in My Party holds after it joins.
- On Linux, the install check no longer tells you to open Docker Desktop.
- A refusal over Docker's disk space says where that disk really is and how to move it.

### Changed
- Tortoise, Vanilla and TBC come out of a fresh install with the command channel already on.
- Tortoise talks to its world through the command channel again, like the other games.

## v0.8.4-Public — 2026-09-12

### New
- A server's client folder can be set, changed or forgotten on the Server tab after the install.
- The install log shows which step it is on with a progress bar, and colours warnings and refusals.
- The command channel lets Yu'lon talk to a running world, and sets itself up and repairs itself.

### Fixed
- Pressing Yes on a question no longer counts as No.
- A server tab whose folder is gone can be dropped with **Forget this install…**.
- A clash with another install's database names that install's folder, and a failure line can be copied.

### Changed
- Tortoise runs the Penqle core with TortoiseBots: 500 bots online at install, and its two addons from the Modules tab.
- The **Adopt as imported…** and **Apply pending database updates…** buttons are gone; Tortoise updates itself.

## v0.8.0-Public — 2026-09-11

### New
- The Server tab shows whether the world is up, restarting or stuck restarting.
- The **Accounts** tab creates accounts, sets passwords and grants or takes away GM rank.
- Launch the game at a server, revive a character and rename one, on all four games and on Windows.
- Browse the online bots the way the in-game who-list shows them.
- My Party on WotLK adds a bot, or one of your own, a guild mate's or a linked account's characters, to your party.
- The **Modules** tab shows how far behind each module is, updates it and turns on its settings.
- A module can be installed from a repository link or a folder on this computer.
- **Uninstall…** removes a server, with **Keep my characters** to keep them for later.
- **Rebuild the server…** keeps the old build and puts it back by itself if the new one does not start.

### Fixed
- My Party only dismisses a bot that is still in your party, and says when it cannot confirm the bot left.
- A log panel's **Stop** stops a job that was waiting quietly.
- A paused world server counts as running, so no database change is sent under it.
- A rebuild no longer overwrites a build recipe you changed yourself.
- A rebuild of a server whose address changed no longer waits hours and puts the old build back.
- Upgrading an existing Tortoise install no longer stops on a database update that runs twice.

### Changed
- Before you stop a database job, Yu'lon says what stays applied.
- Turning on a module's settings says when the world must restart to read them.
- The Catalog's two columns are equal at every window size.

## v0.6.59Public — 2026-08-29

The last release before this log existed. Cut from `Yulon` at `57c74600`; its contents are the commit history up to that tag.
