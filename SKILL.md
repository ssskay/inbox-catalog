---
name: inbox-catalog
description: >-
  Use when the user wants to inventory or catalog things they bought from their
  email — e.g. "catalog the demo purchases", "catalog my purchases", "catalog my
  orders", "check my email for orders", "what did I buy", "build a catalog from
  my inbox", "did it ship yet", or "did I already buy this". Turns order,
  shipment, and receipt emails into a structured catalog (name, maker, price,
  date, quantity, and product photo when present). Read-only; runs a zero-setup
  offline demo, or reads real mail via a Gmail connector, IMAP, or a Takeout
  .mbox.
---

# Inbox Catalog

Inbox Catalog turns a mailbox full of order, shipment, and receipt emails into a
structured, searchable catalog of what the user owns — name, maker, price,
currency, quantity, date, order id, and the product photo when the email has one.
It runs on the user's machine; there is no server and nothing is uploaded.

**Read-only, always.** Live mailboxes are opened with IMAP `EXAMINE` (never
`SELECT`); a connector is only ever used to *search and read*. The tool cannot
send, delete, move, or flag a message. Every ingest is a **dry run** that writes
nothing until the user adds `--apply`. Say this to the user before touching real
mail.

## Running the commands (do this first)

Everything runs through the module invocation `python3 -m inboxcatalog …` — the
engine behaves identically however it was installed. The only question is whether
that module is already importable from where you are:

- **Installed via pip / pipx** (`pip install inbox-catalog`): the module is on the
  Python path, so `python3 -m inboxcatalog …` works from **any** directory. No
  `cd` needed. The catalog lives in a per-user data dir (see below), not in a
  checkout.
- **Installed as a Claude Code plugin, or a clone / copy into `~/.claude/skills/`:**
  the package isn't pip-installed, so run from the **engine root** — the folder
  that contains the `inboxcatalog/` package (and this `SKILL.md`). For a plugin,
  that's `$CLAUDE_PLUGIN_ROOT`; for a clone, it's this skill's own folder. `cd`
  there first:

      cd "${CLAUDE_PLUGIN_ROOT:-.}"    # plugin → engine root; clone → already there

`$CLAUDE_PLUGIN_ROOT` locates **skill assets** (this doc, fixtures, references) —
it is not how a pip install finds the engine. If a bare `python3 -m inboxcatalog
--stats` fails with `No module named inboxcatalog`, you're not pip-installed and
not in the engine root — `cd` there (see above) and retry. No `pip install` is
needed for the offline demo, `--stats`, or any dry run; those run on the Python
standard library alone.

**Where the catalog is stored.** `$INBOX_DATA_DIR` overrides it if set. Otherwise
a repo/clone checkout writes to its own `data/` dir, while a pip install (no
checkout) uses a per-user state dir — `~/Library/Application Support/inbox-catalog`
on macOS, `$XDG_STATE_HOME/inbox-catalog` (default `~/.local/state/inbox-catalog`)
on Linux. Each run logs the chosen data dir at startup.

## When to use

Activate on requests like "catalog the demo purchases", "catalog my purchases",
"catalog my orders", "check my email for orders", "what did I buy last month",
"build me a catalog from my inbox", or any ask to inventory things the user
bought from their email.

## Routing — the skill chooses, not the user

The user never picks a "method." The **only** thing they ever decide is whether to
grant access to their mail — unavoidable on every real-mail path. You detect the
situation and choose, in this order:

1. **Demo request** — "catalog the demo purchases", or the user just wants to see
   what the tool does and has no inbox to point at yet. Run the bundled synthetic
   fixtures: no mailbox, no network, no credentials.

   **Isolate the demo from any real catalog.** The synthetic fixtures are fake
   `@example.com` data — they must never land in (or migrate) the user's real
   catalog. Point `INBOX_DATA_DIR` at a fresh scratch dir and reuse it for every
   demo command in the session:

       export INBOX_DATA_DIR="$(mktemp -d)"                   # isolated, throwaway demo catalog
       python3 -m inboxcatalog --ingest --fixtures            # DRY RUN — shows 4 items
       python3 -m inboxcatalog --ingest --fixtures --apply    # commit to the demo catalog
       python3 -m inboxcatalog --stats                        # demo catalog counts

   (If your shell doesn't keep env vars between commands, prefix each line with
   `INBOX_DATA_DIR=/tmp/inbox-catalog-demo` instead.)

   Then **summarize the catalogued items back conversationally** — the demo yields
   4 board games (Wingspan $65, Azul $40, Codenames $19.95, Ticket to Ride Europe
   Expansion $34.50) with maker and category, and correctly ignores 2 non-order
   emails. This proves the whole pipeline end-to-end, offline.

2. **Real mail + a Gmail connector/MCP is available this session** — prefer it.
   Use the connector to search the user's mail for order / shipment / receipt
   messages and extract name, maker, price, and date yourself. This is the best
   option for an **arbitrary** inbox: it needs no app password and no profile, so
   it works for whatever the user actually buys, not just pre-configured shops.
   Read-only: search and read only, never send or modify. Summarize what you find;
   offer to record it.

3. **Real mail + an app password already configured** (macOS Keychain
   `inbox-catalog-imap`, or `INBOX_IMAP_PASSWORD` in the environment) — use the
   local IMAP engine. Deterministic and photo-capable, but it only recognizes
   **profiled** shops (see the ~0-items note below).

       INBOX_IMAP_ACCOUNT='them@gmail.com' python3 -m inboxcatalog --ingest --imap   # DRY RUN
       # report counts, then on the user's OK:
       INBOX_IMAP_ACCOUNT='them@gmail.com' python3 -m inboxcatalog --ingest --imap --apply

4. **An `.mbox` file was supplied** (a Google Takeout export) — use the mbox engine.

       python3 -m inboxcatalog --ingest --mbox "path/to/export.mbox"          # DRY RUN
       python3 -m inboxcatalog --ingest --mbox "path/to/export.mbox" --apply  # commit

5. **Real mail, nothing configured** — this is the *one* moment you involve the
   user, and you frame it as **granting access, not choosing a method**: "To read
   your mail I need you to grant access — here's the easiest way for your account."
   Then pick the easiest grant for them via `docs/connect-gmail.md`: a personal
   Gmail → app password (Tier 1); a Workspace/school or Advanced-Protection account,
   or someone who'd rather not create a credential → Takeout `.mbox` (Tier 2).

   **Run `python3 -m inboxcatalog doctor` to see exactly what's set up** — it
   reports which deps and mail paths are configured (without touching a secret or
   mailbox) and prints the single next step. Use it to tell the user precisely
   what's missing (e.g. "your app password is set, you just need to export
   `INBOX_IMAP_ACCOUNT`") instead of guessing.

**Every path:** read-only / dry run first, report the counts, and only `--apply`
after the user confirms.

## When a real inbox catalogs ~0 items — this is NOT a failure

The local engine (paths 3 and 4) ships with only the **`demo` board-game
profile**, whose sender allowlist is fictional board-game shops. Pointed at a real
inbox it will **connect and find candidate emails but catalog ~0 items** — because
it is looking for board-game shops, not whatever the user buys. This is a profile
limitation, not a bug, and you must **never let a 0-item result read as failure.**

When it happens, say what actually happened ("I connected and found N order-looking
emails, but the shipped profile only recognizes board-game shops, so it matched
none of yours"), then automatically offer the two real fixes:

- **Switch to the connector path** (routing option 2) if a Gmail connector/MCP is
  available this session — it reads any inbox with no profile at all.
- **Write a profile** for the user's shops (sender allowlist + keyword gate, plus
  optional templates). Walk them through `references/writing-a-profile.md`. A
  profile the user writes is *their* local data — never commit personal shop lists
  into this public repo.

## The Amazon returns tracker (`--profile amazon`)

A second bundled profile turns Amazon order/shipment/delivery/return-window
emails into a **returns tracker**: every item gets a state — `keep` / `return` /
`evaluate` (the default: "not sure yet") / `returned` — plus a **return-window
clock** (days left, expired) computed from an explicit return-by date in the
mail, or delivery date + a policy window (default 30 days, override with
`INBOX_RETURN_WINDOW_DAYS`). Activate on "what did I buy on Amazon", "what can
I still return", "track my Amazon returns", or "which Amazon items should I
evaluate".

Zero-setup demo (bundled synthetic Amazon fixtures, no mailbox). Isolate it in a
scratch catalog so the fake data never touches the user's real one:

    export INBOX_DATA_DIR="$(mktemp -d)"                                    # throwaway demo catalog
    python3 -m inboxcatalog --profile amazon --ingest --fixtures            # DRY RUN
    python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply    # commit to the demo catalog
    python3 -m inboxcatalog --profile amazon --returns                      # the payoff

The `--returns` report leads with items **still inside their window, most
urgent first**, flags everything still `evaluate`, and separates expired /
already-returned. Record decisions with:

    python3 -m inboxcatalog --profile amazon --mark <item-id|order-id|name> keep
    python3 -m inboxcatalog --profile amazon --mark "Yoga Mat" return

**Refund emails auto-close the loop.** When an Amazon refund email is ingested,
the tracker marks that order's item(s) `returned` automatically (order-level —
correct a partial refund with `--mark`). So a normal re-ingest keeps the
returned pile current without any manual marking.

Each item is also routed to a **life zone** (see
`references/life-zone-routing.md`); `--triage` groups the catalog by zone with
the return clock inline, plus `unrouted` (no confident signal — never guessed)
and high-spend/cluster flag sections:

    python3 -m inboxcatalog --profile amazon --triage

Real mail works the same as every other path (read-only, dry-run first):
`--imap`, `--mbox`, or the Gmail connector, with `--profile amazon`. Later
shipment/delivery/return-window emails **enrich** already-catalogued rows with
delivery and return-by dates instead of duplicating them; every window
computation, zone decision, and state change is logged so you can always trace
*why* an item is flagged the way it is.

## Optional: photo search (after `--apply`)

If the optional CLIP dependencies are installed, catalogued photos become
reverse-image searchable:

    python3 -m inboxcatalog --reindex                       # embed new images
    python3 -m inboxcatalog lookup path/to/photo.jpg        # find the closest items

(The offline demo has no real photos, so this is only meaningful on a real inbox
with product images.)

## Reference files

- `docs/connect-gmail.md` — grant a mailbox read access: Tier 1 app password
  (live), Tier 2 Google Takeout `.mbox` (no credential). Also explains why a
  "Sign in with Google" OAuth client isn't shipped.
- `references/writing-a-profile.md` — teach the engine to recognize the user's own
  shops so a real inbox catalogs their purchases, not ~0 items.
- `references/life-zone-routing.md` — the life-zone taxonomy and routing rules the
  Amazon profile's `--triage` view uses (which zone a purchase serves, the
  gift/bulk overrides, the spend-flag rules, and how to write your own taxonomy).
