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
- **Restart** on the Server tab and in the tray menu stops a server, saving every character, and starts it again.
- Closing Yu'lon keeps it in the system tray: see which servers are up, **Start** them or **Play**, from the tray icon.

### Fixed
- My Party and the bot counts read a hand-edited conf as the server does: indented settings count, the first copy wins.
- A server setting file over 2 MB is no longer loaded into the Tuning tab's editor, which could freeze Yu'lon.
- On macOS the Tuning tab no longer lets you edit the server's own conf under a different spelling of its name.
- **Save file** on the Tuning tab keeps the file's byte-order mark and the line endings of every line you did not edit.
- A server setting file that is a link to a file outside the server folder is no longer opened, saved or backed up by the Tuning tab.
- **Add to Steam** on Steam Deck now makes a Server entry that starts from Gaming Mode; press it again to mend an old one.
- The **Tuning** tab lists every module config file in the server's modules folder, with a scroll bar when there are many.
- A file of a server's client pack deleted from the ready-to-play client is put back at the next **Play**.
- A world update the database refused can be run again from **Apply database corrections…**, after it asks first.
- A module update that breaks the build is now put back automatically; **Put back the last update…** undoes one by hand.
- Removing NPC Teleporter on WotLK now takes its teleporters out of the world again.
- Removing NPC Teleporter on WotLK also clears its menus and scripts, and restores the gate menus it replaced.
- Updating a TBC or Vanilla server now brings in its new world content fixes once, and names any it could not apply.
- Updating a TBC or Vanilla server stops before building when the new code needs database changes it cannot apply.
- Updating a TBC or Vanilla server reloads changed bot tables, replacing what the bots had generated in them.
- TBC and Vanilla servers no longer risk a crash when bots fight Netherspite in Karazhan or pick certain mounts.
- Two Yu'lons pressing **Re-extract map data** on one server folder at the same moment: only one goes ahead.
- When another Yu'lon is extracting into the folder, **Re-extract map data** says so and never offers to remove its run.
- Two Yu'lons sharing a server folder, as Windows and WSL or via a link, no longer extract into it once one has started.
- On Windows, **Stop** ends a build's leftover helpers even when the build's own docker process had already exited.
- **Stop** during **Re-extract map data** ends the map extractor at once and puts the old map data back.
- The pathfinding line quotes the generator up to a whole word, and **Refresh** keeps what **Stop** said there.
- **Stop** during a clone or map tool removes its container, even as it starts; one Docker would not start goes too.
- **Re-extract map data** waits for a map tool an earlier Yu'lon left running instead of writing under it.
- A tool container Yu'lon could not remove is named with the command that removes it; host git never clones under one.
- Installing a module never writes into another folder through a link in your game client; it names the link instead.
- On Windows, a link inside the server folder no longer stops a finished build from being kept for the next rebuild.
- **Stop** during a quiet build step ends the build at once, not at its next line.
- **Stop** during a clone with your own git also ends the helpers git started.
- A module whose files hold a link is refused, naming the link, so it never copies files from elsewhere on your computer.
- Adding a module from a folder again keeps the working copy if the new copy fails part way, as the message says.
- **Stop** during a database import ends the importer too; **Install** again never clears databases it still writes.
- **Stop** asks Docker once to end a build, instead of again every tenth of a second until it has.
- **Re-extract map data** stops, and says why, if its reservation of the folder ends part way.

### Changed
- Each WotLK-style server now has its own container names and ports, so a second one can run beside WotLK.
- On Linux, **Stop** during a build is said to end it at once, as it does; the line after a Stop says so on Windows too.
- A stopped install or rebuild says what the Stop left, after **Stopped:**.

## v0.9.14-Public — 2026-10-07

### Fixed
- On Windows with Docker Desktop, a WotLK build no longer fails within a second of starting.
- A Tortoise server installed before the dbc.MPQ check learns that archive on its next Install, so a later swap is noticed.

### Changed
- A WotLK install keeps one copy of the shared server source in Docker's build cache instead of two, saving about 1.5 GB.
- New TBC, Vanilla and Centurion servers build from their projects' newest code of October 2026.
- TBC and Vanilla bots fight Netherspite better, and a Centurion battleground win restores a depleted mark.

## v0.9.13-Public — 2026-10-06

### New
- **Where to get help…** on the Server tab links each game's server, bots and community, and Yu'lon's issues page.
- If Docker lost a server's database, **Start** and **Rebuild** say so and offer **Repair the database…**.
- A Server rates card on the Tuning tab sets XP, gold, item drops, reputation and honor on every game.
- Delete an account from the Accounts tab; all-digit names are refused on TBC, Vanilla and Tortoise.
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
- Tortoise builds TortoiseBots v10, whose faster travel and auction-house planning cuts lag on servers with hundreds of bots.
- A Tortoise client folder missing its dbc.MPQ archive is refused before the build, with how to fix it.
- A missing bots table is now reported once, with how to fix it, instead of a quiet note every few seconds.
- Stopping a Tortoise server with a hired companion online no longer crashes it or asks you to check your characters.
- On Centurion, **Stop** logs players and bots out first and waits while their saves are written, so no change is lost.
- Installing a module that asks a question about your characters starts a stopped database to check the answer.
- A module install refused after Yu'lon started the database to check your answers now says the database is still running.
- Removing **All Stackables to 200** on WotLK puts every item's stack size and limit back exactly as it was.
- **Stop** still ends what it was pressed for when your computer is too short of resources to start a helper.
- On Windows, **Stop** also ends the helper programs a build started on your PC.
- The worldserver log saved when you stop a server has passwords masked, as the support file does.
- A failed install is listed once in the support file, however its folder is spelled.
- After a module install on a stopped server, the report says to press **Start** rather than **Stop** then **Start**.
- Refusing a client folder now reads as two plain sentences instead of one run-on line.
- A module's right-click **Remove** is offered only when the module is installed.
- **Forget Yu'lon's record…** asks in a box that fits small screens and defaults to No.
- The header says STARTING, not REALM ONLINE, until the world server reports ready, including while Docker restarts it.
- A database that is a moment slow to answer after a start no longer logs a warning.
- A brand-new database that is still doing its first setup no longer logs "Access denied", "Can't connect" or a missing-table warning.
- **Refresh** keeps the sentence beside **Repair the database…** and **Restore a backup…** instead of clearing it.
- The corrections dialog names each step by what it does instead of "statement 1".
- After **Stop**, the missing-database sentence stays beside **Repair the database…** and **Restore a backup…**.
- An empty server database now says it is empty, not that Docker's copy was removed.
- **Repair the database…** and the adopt button appear even when the database was still starting at the first look.
- **Restart server…** and **Recreate containers…** say done only once the world is up, and say so plainly if it crash-loops.
- Once a crash loop is fixed, the Server tab reads up a minute after the world says ready, not ten minutes later.
- The header reads CRASH LOOP, not REALM ONLINE, while the world server keeps crashing.
- A rebuild whose rollback did not come up says its containers are still running and to press Stop.
- A long failure under the rebuild log is shown whole at every window size; its last lines were cut off.
- The AH bot's GUID, account and items-per-cycle accept 0 to 4294967295 and refuse a negative number.
- The raw file editor on the Tuning tab warns before saving a whole-number setting the server would read differently.
- A decimal module question, such as the XP rates, takes only the digits 0 to 9 and one point, and refuses "1_0".
- **Save logs for support…** also reads the servers of an install that failed, even though it was never added to the list.
- **Save logs for support…** keeps what database and Tortoise login containers wrote as errors, not only their normal output.
- The main window fits a Steam Deck or a small screen instead of hanging off its edges, and scrolls when it is cramped.
- Long error and warning messages scroll inside their box, so its OK button always stays on screen.
- After a reinstall or a new data folder, **Repair the command channel** gives Yu'lon's own server account a new password.
- After **Repair the database…**, the command channel offers **Repair** for its lost account, or turning on if it is off.
- After the command channel gave up, **Refresh**, **Start** or turning it on checks it again without restarting Yu'lon.
- The **Apply database corrections…** offer appears even when the database was still starting when Yu'lon looked.
- A server shutdown typed on the Console tab is not sent: Docker would start the world again, so it points to **Stop**.
- On Centurion and Tortoise, **Stop** has the world server save every character, bots included, before it closes.
- On Windows, Centurion and Tortoise's **Stop** asks for that save through the command channel and says when it could not.
- **Stop** lets the world server finish saving every character before it closes, even on a slow disk.
- A failed install now shows its servers' last log lines, and **Save logs for support…** keeps them.
- **Bigger Stacks** on TBC, Vanilla and Tortoise now shows as installed and can be removed.
- A module that only changes settings, such as Experience Rates, now shows as installed and can be removed.
- Number boxes in Tuning and module questions take only the digits 0 to 9, so the server reads exactly what you typed.
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
- On Linux and the Steam Deck, lower-case game file names are accepted on every game, a lower-case Data folder too.
- On Linux and the Steam Deck, a lower-case Interface folder is your client's Interface folder for addons.
- A module's client file replaces your file of that name in any case, and **Remove** puts your file back.
- An install that fails part way puts back any file of yours it had moved aside.
- A failed **Re-extract map data** says when part-made pathfinding tiles were kept and will be continued.
- Progress-bar marks from the map tools no longer clutter the install log.
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
- A WotLK rebuild with nothing changed no longer re-sends gigabytes to Docker, and still uses a Docker builder you chose.

### Changed
- **Uninstall** now takes every module's files out of your client and puts your own files back.
- A module whose client file has the same name as another module's is refused until the other is removed.
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
- Fresh Tortoise installs build TortoiseBots v2026-10-06: hired companions are deleted when dismissed or on restart.
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
