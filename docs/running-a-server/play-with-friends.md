---
type: How-to
title: "Play with friends: LAN, internet and router port forwarding"
description: Choose who can reach your server, review the plan Yu'lon shows, apply it, and finish the router steps for friends outside your home.
tags: [networking, lan, internet, port-forwarding, firewall]
resource: pylauncher/yulon/networking.py
sources:
  - id: networking
    resource: pylauncher/yulon/networking.py
    title: Builds and applies the networking plan
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Networking tab, its radios and plan text
  - id: single
    resource: pylauncher/yulon/single_network.py
    title: Address handling for single-container servers
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Play with friends: LAN, internet and router port forwarding

By default your server is set up for the same Wi-Fi. The **Networking** tab lets you change that, and tells you the router steps Yu'lon cannot do for you.[^view]

## Choose who plays

On the **Networking** tab pick one:[^view]

- **LAN (same Wi-Fi)**: people on your home network. This is the starting choice.
- **Internet play (friends elsewhere)**: friends outside your home. Needs router port forwarding, below.
- **Only this computer (127.0.0.1)**: only you, on this machine. Other computers that log in are told the server is on themselves, so they cannot play.[^networking]

## Steps

1. Pick a choice.
2. Press **Show plan**. Nothing changes yet; the tab reads "Choose who plays with you, then press Show plan to see what Yu'lon would change. Nothing changes until you press Apply."[^view]
3. Read the plan. **Apply** stays greyed until you have shown it ("Press Show plan first, so you can read what Apply will change.").
4. Press **Apply**.
5. Restart the server so it hands out the new address. The report says "Restart the server so it hands out the new address."[^view]

## Reading the plan

The plan lists the mode, the address of this computer on your network, your public address (Internet mode), the game ports and the firewall Yu'lon found (ufw, firewalld, Windows Firewall, the macOS firewall, or none). It then gives **Players set realmlist to:** followed by the address your players type in their game client's realmlist. Under **Details** are the exact commands Apply will run.[^view]

Apply opens the game ports in your firewall, sets the address the realm list gives players, and, on Windows with the server inside WSL, passes the ports on to it.[^view] On Linux the firewall commands run without asking for a password; if your computer would ask for one, that step is skipped and the exact command is shown for you to run yourself.[^networking]

## Router steps for Internet play

Yu'lon cannot change your router. For Internet play the plan ends with "You need to do these yourself":[^networking]

1. Give this computer a DHCP reservation on the router so its address stays the same.
2. Forward TCP (not UDP) for the two game ports to this computer. On the router look for "Port Forwarding", "Virtual Server" or "NAT".
3. Your public address can change. If friends suddenly cannot connect, run Show plan and Apply again. A free dynamic DNS name (for example from duckdns.org) gives you one that stays put.
4. People on your own network keep using this computer's LAN address; only remote players use the public address.

Friends who use Yu'lon too can join with the friend launcher: see [The friend launcher](/playing/friend-launcher.md).

If your internet provider uses carrier-grade NAT, the plan warns that you cannot accept port forwards and suggests asking for a public address or using a VPN.[^networking]

## What you'll see

After Apply: "Apply finished N of M steps." If some steps could not be done, **Details** lists them with what to run by hand.[^view]

## If something goes wrong

- "could not determine this machine's LAN IP": connect to a network and press Show plan again.
- A plan that says "Not ready to apply" lists why under Warnings.
- On Windows, set the network profile to Private and remove any Block rule Windows made for Docker; the plan names both when they apply.[^networking]
- Friends still cannot connect: check the router step, your firewall, and that the server is started. See [Troubleshooting](/running-a-server/troubleshooting.md).
- To change the name players see in the realm list, see [Realm name](/running-a-server/realm-name.md).

Single-container servers (the DDON and DFO ones) keep their address in a file, which Yu'lon rewrites for you.[^single]

[^networking]: The plan, warnings and manual steps are built by the networking module.
[^view]: Radio labels, buttons and the plan text are in the server page code.
[^single]: Single-container servers read their advertised address from a file Yu'lon changes.
