# 👤 How to Create Accounts

> Creating accounts takes about 60 seconds.
> Copy paste the commands below — that's all!

---

## Before You Start

Make sure your server is running. You should see
**AZEROTH IS READY** in your launcher, or run:

```bash
docker ps
```

If you see containers listed — you're ready!

---

## Step 1 — Open the GM Console

Copy and paste this into Konsole:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

You will see the server output. That means it worked!

---

## Step 2 — Create Your Account

Type this exactly — replace USERNAME and PASSWORD
with whatever you want:

```
account create USERNAME PASSWORD PASSWORD
```

Wait 2 seconds then type:

```
account set gmlevel USERNAME 3 -1
```

**Example** — creating an account called "dad":
```
account create dad mypassword mypassword
account set gmlevel dad 3 -1
```

---

## Step 3 — Exit the Console Safely

Press **Ctrl+P** then immediately **Ctrl+Q**

> Never press Ctrl+C — that stops the server!

---

## Step 4 — Done!

Log into WoW with your new username and password.
Set realmlist to `127.0.0.1` if you haven't already.

---

## Creating More Accounts

Just repeat Steps 1-3 for each account. You can
create as many as you need — one per family member,
one for testing, whatever you like.

---

## Quick Reference — Copy Paste Ready

**Open console:**
```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

**Create account:**
```
account create USERNAME PASSWORD PASSWORD
account set gmlevel USERNAME 3 -1
```

**Exit console:**
Ctrl+P then Ctrl+Q

---

## Troubleshooting

**Console shows nothing after attaching?**
Press Enter once to get a fresh line then type.

**"Account already exists" error?**
Good news — the account is already there! Just
try logging in with that username and password.

**Login says information not valid?**
Make sure you typed the password the same both
times when creating. Try creating the account
again with a simpler password.

**Server not found?**
Start your server first:
```bash
# Base WoW
cd ~/wow-server && docker compose up -d

# NPCBots
cd ~/wow-server-npcbots && docker compose up -d

# Playerbots
cd ~/wow-server-playerbots && docker compose up -d
```

---

*Part of the Dad's MMO Lab project — free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
**ko-fi.com/dadsmmolab**
