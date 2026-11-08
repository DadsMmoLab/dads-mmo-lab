---
type: How-to
title: "My Party: build a bot party"
description: "Use My Party on the Bots tab to add bots of a chosen class to your character's group, and dismiss them again."
tags: [my-party, bots, party, wotlk, unbound]
resource: pylauncher/yulon/party.py
sources:
  - id: party
    resource: pylauncher/yulon/party.py
    title: "The My Party bridge and its preconditions"
  - id: panel
    resource: pylauncher/yulon/ui/widgets/party_panel.py
    title: "The My Party panel and its texts"
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: "Where the panel sits on the Bots tab"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# My Party: build a bot party

My Party lets you build a group of bots for your character from the launcher. You pick a class, press **Add a bot**, and the bot joins your party in the game.[^panel] It is on the **Bots** tab, beside the list of bots.

It works on [WoW WotLK](/games-and-bots/wow-wotlk.md) and [WoW Unbound](/games-and-bots/wow-unbound.md) only. On the other servers the group says it is not available there, because it needs two modules that only these servers have.[^party][^view]

## Before you start

- The server must be running, and the character you play must be logged in to the game. A bot is added to a live session.[^panel]
- My Party needs a small bridge script set in the server folder. If the panel shows **Set up My Party**, press it. The scripts load when the world server restarts, or when you type `.reload ale` in the game.[^panel]

## Steps

1. Go to the **Bots** tab and find the **My Party** group.
2. Type your character's name in the Character box ("the character you are playing, logged in").
3. Press **Show this character's party** to see who is in it.
4. Choose a class. Optionally pick a spec (the default is "let the server pick") and a level (the default is "leave it as the server made it").
5. Press **Add a bot**.

### What you'll see

The panel says "Working -- the server is being asked. This can take a few seconds." and then the bot appears in the party list, and in the Browse list on the left.[^panel]

## More things the panel does

- **Dismiss** removes the chosen bot. With a bot chosen the button carries its name.
- **Dismiss every bot...** takes two presses: the first arms it and shows the count, the second sends it. If you changed the character or the party between the presses, nothing is sent and you are told to press again.[^panel]
- **Show characters I can add** lists characters the server would let you add. **Add this character...** adds the one you choose.
- **Link an account...** lets you add characters from another player's account: type that player's account name (the name they log in with) and press it twice to confirm.[^panel]

## If something goes wrong

- "Type the name of the character you are playing first": the Character box is empty.
- "Choose a bot in the party list first": press **Show this character's party**, then pick a bot.
- If the panel says My Party is not ready, it shows which check is unmet. The usual cause is the bridge scripts missing, or the world server not restarted since they were copied.[^party]
- No level box appears if the server's own maximum level cannot be read from its configuration.[^panel]

See also [Playerbots: what they are](/games-and-bots/playerbots-overview.md).

[^party]: The My Party check: only wow-wotlk and wow-unbound qualify, and the bridge is proven by the server answering.
[^panel]: The My Party panel: button labels, refusals and the two-press gestures.
[^view]: The message shown on other servers.
