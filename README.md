# Inbox Catalog

[![PyPI](https://img.shields.io/pypi/v/inbox-catalog)](https://pypi.org/project/inbox-catalog/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/inbox-catalog/)

Your inbox already knows everything you've bought. It's just trapped in a pile of
order confirmations you'll never scroll back through.

Inbox Catalog reads those emails and turns them into a live, structured catalog of
what you own — item, maker, price, date, and the product photo — so the stuff you
bought stops being out of sight, out of mind.

It runs on your machine. Nothing is uploaded. Mail is opened strictly
**read-only** — it can't send, delete, move, or flag anything.

## Why this exists

I buy a lot of small things from a lot of small shops. After a good sale, half of
it vanishes into "I'll deal with that later," and I forget what's even coming.
Pre-orders are the worst — you pay in March, it ships in September, and by then it's
gone from your brain.

Budget apps don't help, because they only see *who you paid* (some cryptic Shopify
or PayPal string), not *what you bought*. So they file everything under "shopping"
and shrug.

Your order emails have the actual item. This reads them back to you — no manual
logging, no spreadsheet, no receipts to file. Just: here's what you have.

## What you can point it at

- **A visual shelf** — a browsable grid of everything you own, with photos, sorted
  by category. The screenshot-able "here's my collection" thing.
- **Pre-order radar** — what's paid for and hasn't arrived yet, so nothing slips
  through.
- **Did it ship?** — orders confirmed but with no shipping email yet, pulled
  straight from your inbox.
- **Spending in *your own* categories** — not a bank's merchant guess. If your
  brain sorts things into "hobby / home / work," so does this — because it reads
  the item, not the charge.
- **A receipt drawer** — searchable proof of purchase for when something breaks,
  without ever filing a thing.
- **Did I already buy this?** — a quick check before you re-buy from that artist for
  the third time.

Same catalog, one tool. Point it at whichever question you have.

## How it works

Three ways in — and you never pick. The skill figures out which one fits and just
does it. The only thing *you* decide is letting it read your mail (which every path
needs, and which always stays read-only):

- **Already have Gmail connected in Claude Code?** It reads your orders directly.
  Works on any shop, no setup.
- **Personal Gmail?** A ~3-click app password ([guide included](docs/connect-gmail.md))
  gets you the live, photo-powered version.
- **Locked-down work account, or just privacy-minded?** Point it at a Google Takeout
  `.mbox` export — no credentials at all.

Want to see it work before connecting anything? There's a zero-setup demo built
in. 👇

## Install

**Claude Code** (the full experience — you just talk to it). Two slash commands:

```
/plugin marketplace add ssskay/inbox-catalog
/plugin install inbox-catalog@inbox-catalog
```

Then open a fresh session and say **"catalog the demo purchases"**. Claude runs a
bundled set of demo orders and hands you back a catalog — four board games with
makers, prices, and categories. No mailbox, no credentials, no risk. That's the
whole experience, minus your actual stuff.

**Just the engine** (any terminal, no Claude required):

```bash
pip install inbox-catalog
python3 -m inboxcatalog --ingest --fixtures --apply   # same zero-credential demo
python3 -m inboxcatalog --stats
```

Not on the plugin system but want the Claude skills? Copy the folder in instead:

```bash
git clone https://github.com/ssskay/inbox-catalog ~/.claude/skills/inbox-catalog
```

**New here?** [`GETTING-STARTED.md`](GETTING-STARTED.md) walks you from the demo to
your first real-mail run, step by step. Not sure what's set up? Run
`python3 -m inboxcatalog doctor`.

*(Shipping your own fork? The full walkthrough — local testing, publishing,
versioning — is in [`PUBLISHING.md`](PUBLISHING.md).)*

## Bonus: an Amazon returns tracker

A second bundled profile (`--profile amazon`) turns Amazon order / shipment /
delivery / return-window emails into a returns dashboard: a **keep / return /
evaluate / returned** state per item, a **return-window clock** (days left,
expired), and a **life-zone triage** that groups purchases by which part of your
life they serve. The demo data here is **synthetic too** — obviously-fake
`@example.com` mail — and it exercises every feature in one run, so you can try
all of it before connecting a real inbox:

```
$ python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply
$ python3 -m inboxcatalog --profile amazon --returns

==== Returns — policy window 30d ====

Still returnable (8) — most urgent first:
  [3] Anime Collectible Figure 4 inch      evaluate    22.99 USD   ⏳ 5 days left   <-- decide!
  [1] Craft Vinyl Roll 12x5ft Matte        evaluate    25.98 USD   ⏳ 21 days left
  ...
Expired (1):
  [9] Yoga Mat Extra Thick Non-Slip        evaluate    26.00 USD   ⏳ expired 24d ago

$ python3 -m inboxcatalog --profile amazon --triage

🔨 Crafting (2 · 2 returnable)   🌟 Fandom & Fun (2)   🏷️ Resale (1)   🎁 Gifts (1)
unrouted (1) — needs your call:
  • Bamboo Cutting Board Medium            evaluate    ⏳ 25 days left
💰 Spend flags: 1 item over $75 · 5 orders within 7 days
```

Zones are generic out of the box and fully user-configurable — drop your own
taxonomy in a local, gitignored config file (see
[`references/life-zone-routing.md`](references/life-zone-routing.md)).

## Make it recognize your shops

Out of the box it only knows one category (board games — the demo). Pointed at a
real inbox, it'll connect and find your order emails but catalog close to nothing,
because it doesn't yet know *your* shops. That's not a bug — it's waiting for a
**profile**.

A profile is a short file that tells the engine which senders and keywords count as
"a purchase" for you, plus optional templates for your favorite shops. Write one for
pins, sneakers, manga, whatever you're into, and the catalog fills up. See
[`references/writing-a-profile.md`](references/writing-a-profile.md).

Your profile is *your* data — keep your personal shop lists out of any public fork.

## The origin story (a case study in weird)

This started with a genuinely cursed shopping category — small-artist collectibles
from random indie shops, resale marketplaces, and one-off drops, with names that
mean nothing to a merchant categorizer. No app could make sense of it. So I built a
little thing that read the order emails and turned them into a real, searchable
database. One profile, and suddenly the mess had shape.

Then it clicked: that niche was never the point. The framework underneath — email
in, catalog out — doesn't care what you collect. So I pulled the niche out into a
profile and made the framework generic. The weirdest possible use case turned out to
be the best proof it works for anyone.

---

Built by [Sara](https://sarakay.me) — *Data Gremlin Go Brr*. Read-only,
local-first, forkable.
