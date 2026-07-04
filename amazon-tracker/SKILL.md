---
name: amazon-tracker
description: >-
  Use when the user asks about their Amazon purchases and returns — "what did I
  buy on Amazon", "what can I still return", "track my Amazon returns", "catalog
  my Amazon orders", "which Amazon items should I evaluate", or how many days are
  left to return something. Read-only; runs a zero-setup synthetic demo, or reads
  the user's own Amazon order, shipment, delivery, and return-window emails.
---

# Amazon Tracker

Amazon Tracker reads the Amazon emails already sitting in the user's inbox —
order confirmations, shipment and delivery notices, return-window reminders —
and turns them into a returns dashboard: every item with its price, order,
delivery date, a **keep / return / evaluate / returned** state, and a
**return-window clock** (days left before the return option expires).

**The payoff is the clock, not the list.** "6 items still returnable, 2 expire
in 3 days, 3 are flagged `evaluate`" is the answer that makes this worth
running. Lead with what's still returnable and what needs a decision.

**Read-only, always.** Mailboxes are opened read-only (IMAP `EXAMINE`); the
tool cannot send, delete, move, or flag mail. Every ingest is a **dry run**
that writes nothing until the user adds `--apply`. Say this before touching
real mail.

## The shared engine (setup)

This skill is a thin wrapper over the **inbox-catalog** engine — the same
generic email→catalog core the Inbox Catalog skill uses, selected here with
`--profile amazon`. The skill keeps no private copy of the engine. Locate the
engine root (the folder containing the `inboxcatalog/` package) in this order:

1. `$CLAUDE_PLUGIN_ROOT` if set — this skill is installed as part of the
   inbox-catalog **plugin**, and the engine sits at the plugin root, else
2. `$INBOX_CATALOG_ROOT` if set, else
3. an `inbox-catalog/` folder next to this skill folder, else
4. `~/inbox-catalog`, else `~/.claude/skills/inbox-catalog`.

If none exists, have the user get it first:

    git clone https://github.com/ssskay/inbox-catalog ~/inbox-catalog

`cd` into that engine root before running any command below (e.g.
`cd "$CLAUDE_PLUGIN_ROOT"` for a plugin install). The offline demo, `--stats`,
and every dry run need **no** `pip install` — they run on the Python standard
library. Only live mail (`--imap`/`--mbox` with `--apply`) and photo features
need deps: `pip3 install --break-system-packages -r requirements.txt` (no venvs).

## Zero-setup demo first

Before connecting any mail, prove the whole flow offline with the bundled
synthetic Amazon fixtures (no mailbox, no network, no credentials). Run it in a
scratch catalog so the fake `@example.com` data never mixes with a real one:

    export INBOX_DATA_DIR="$(mktemp -d)"                                    # throwaway demo catalog
    python3 -m inboxcatalog --profile amazon --ingest --fixtures            # DRY RUN
    python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply    # commit to the demo catalog
    python3 -m inboxcatalog --profile amazon --returns                      # the payoff

Then summarize conversationally: the demo yields **9 items across 7 orders** and
exercises every feature in one run — a craft-supply order that goes
order→shipped→delivered (its return window now ticking), a fandom order with an
explicit return-by date pulled from a reminder email (only days left), an
expired item, a gift (routed by gift receipt, not category), a bulk resale lot,
a $129.99 keyboard that trips the spend flag (plus an order cluster), one
`unrouted` item the router won't guess at, and a marketing email correctly
ignored. Every item starts in the `evaluate` pile.

## Real mail (read-only ingest)

Same routing as the engine: prefer a Gmail connector/MCP when one is connected
this session (search for Amazon order emails and extract directly); else an
IMAP app password (macOS Keychain `inbox-catalog-imap` or
`$INBOX_IMAP_PASSWORD`); else a Google Takeout `.mbox`. Setup guide:
`docs/connect-gmail.md` (points at the engine's full doc).

    INBOX_IMAP_ACCOUNT='them@gmail.com' python3 -m inboxcatalog --profile amazon --ingest --imap          # DRY RUN
    INBOX_IMAP_ACCOUNT='them@gmail.com' python3 -m inboxcatalog --profile amazon --ingest --imap --apply  # after the user confirms
    python3 -m inboxcatalog --profile amazon --ingest --mbox "takeout.mbox" --apply

Later shipment/delivery/return-window emails **enrich** already-catalogued
rows (delivery date, return-by date) instead of duplicating them — re-running
ingest is always safe and idempotent.

## The returns view — lead with this

    python3 -m inboxcatalog --profile amazon --returns

Items **still inside their return window come first, most urgent first**
(fewest days left at the top), anything still `evaluate` is flagged loudly,
and expired / already-returned items are separated out. The window is an
explicit return-by date from mail when one was seen; otherwise delivery date +
a policy window (default 30 days; `INBOX_RETURN_WINDOW_DAYS` overrides);
otherwise order date + policy as a conservative fallback.

Every new item starts as **`evaluate`** — the "not sure yet" pile is the whole
point. Record decisions the moment the user makes them:

    python3 -m inboxcatalog --profile amazon --mark <item-id|order-id|name-substring> keep
    python3 -m inboxcatalog --profile amazon --mark "Yoga Mat" return
    python3 -m inboxcatalog --profile amazon --mark 114-8812733-1054420 returned

**Refunds close the loop automatically.** Ingesting an Amazon **refund** email
marks that order's item(s) `returned` on its own (order-level; a re-ingest is
safe and idempotent). `--mark … returned` is still there to correct a partial
refund or record a return the mail hasn't reflected yet.

## Life-zone triage

    python3 -m inboxcatalog --profile amazon --triage

Groups the catalog by **life zone** — which part of the user's life each
purchase serves — with the return clock inline, plus an `unrouted` section
(no confident signal; the router never guesses) and 💰 spend flags (items over
$75 by default, and bursts of many orders in a week). The taxonomy, signals,
override rules, and how to swap in your own zones live in
`references/life-zone-routing.md`.

## Observability

Every routing decision, window computation (inputs → days left), and state
transition is logged. If the user asks "why is this expired?" or "why did
this land in that zone?", run with `--debug` and read the log lines — the
answer is always traceable.
