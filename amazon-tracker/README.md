# Amazon Tracker

You know that feeling: something arrives, you're not sure about it, and by the
time you decide it should go back the return window died a week ago.

Amazon Tracker reads the Amazon emails already in your inbox — order
confirmations, shipping and delivery notices, return-window reminders — and
turns them into the dashboard Amazon never gives you:

- **A return-window clock per item.** Days left, most urgent first, expired
  separated out.
- **A decision state per item.** `keep` / `return` / `returned` — and
  `evaluate`, the default, for the maybe pile. The report nags you about those
  until you decide.
- **Life-zone triage.** Every purchase grouped by which part of your life it
  serves (crafting, fandom, work, gifts…), with an `unrouted` bucket for the
  ones it won't guess at, and 💰 flags for big or bursty spending.

It runs on your machine. Nothing is uploaded. Mail is opened strictly
**read-only** — it can't send, delete, move, or flag anything — and nothing is
written until you confirm.

## How to use

1. Get the engine (this skill is a thin wrapper over
   [inbox-catalog](https://github.com/ssskay/inbox-catalog), the generic
   email→catalog core — one engine, many collections):

       git clone https://github.com/ssskay/inbox-catalog ~/Code/inbox-catalog

2. Copy this `amazon-tracker/` folder into `~/.claude/skills/`
3. Open Claude Code
4. Say: **"what can I still return?"** or **"track my Amazon returns"**

Want to see it work before connecting anything? Say **"demo the Amazon
tracker"** — a bundled set of synthetic Amazon emails runs the whole pipeline
offline: no mailbox, no credentials, no risk.

## What you get

The bundled demo is **synthetic** — obviously-fake `@example.com` mail — so you
can see the whole thing before connecting a real inbox:

```
Still returnable (8) — most urgent first:
  Anime Collectible Figure 4 inch   evaluate   ⏳ 5 days left    <-- decide!
  Craft Vinyl Roll 12x5ft Matte     evaluate   ⏳ 21 days left
  ...
Expired (1):
  Yoga Mat Extra Thick Non-Slip     evaluate   ⏳ expired 24d ago

💰 Spend flags: 1 item over $75 · 5 orders within 7 days
```

## The pieces

- `SKILL.md` — the skill itself (what Claude follows)
- `references/life-zone-routing.md` — the life-zone taxonomy, routing rules, and
  how to write your own zone config
- `docs/connect-gmail.md` — granting read-only mail access (app password or
  Takeout export; points at the engine's full guide)

---

Built by [Sara](https://sarakay.me) — *Data Gremlin Go Brr*. Read-only,
local-first, forkable.
