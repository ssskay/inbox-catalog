# CLAUDE.md — orientation for agent sessions

Inbox Catalog: a local-first, **read-only** engine that turns order/shipment
emails into a structured catalog of what the user owns. This file exists to keep
sessions cheap — read it plus `SKILL.md` and you are oriented. Do not re-derive
the repo's story from scratch.

## Context discipline (read this first)

- **Do NOT bulk-read fixtures.** `*.eml` files under `inboxcatalog/profiles/`
  and `skills/*/examples/` are synthetic test mail. Only open one when debugging
  a specific parser failure.
- **Do NOT read** `data/` (runtime DB + images), `.cx/` (session-tool cache),
  `.pytest_cache/`, or `.DS_Store` files. Nothing there informs a change.
- **Session/local state lives in `HANDOFF.md`** (untracked). Read it at session
  start; keep it short and current at session end. Do not paste its contents
  into committed files.
- Enough orientation for most tasks: this file + `SKILL.md` + the one module
  you're changing.

## Mail access — the four paths (canonical glossary)

Confusing these is the #1 recurring mistake. Full detail: `docs/connect-gmail.md`
(the **single source of truth** for access setup — other docs must point there,
never fork the content).

1. **Claude Gmail connector/MCP** — Claude reads mail in-session; no engine
   credential exists. Preferred when available. Works on any inbox, no profile.
2. **IMAP app password** — the engine reads live mail (`--imap`). Keychain
   service `inbox-catalog-imap` or `$INBOX_IMAP_PASSWORD`. Recommended engine path.
3. **Takeout `.mbox`** — the engine reads an export file (`--mbox`). Zero
   credential, snapshot only. Fallback.
4. **OAuth `connect` / `--gmail`** — shipped but **dormant** (hosted-future
   only; see "Why not OAuth?" in the connect doc). Gotcha: if a token exists,
   `cli._resolve_source` prefers it automatically; `--imap` overrides.

All paths are read-only (IMAP `EXAMINE`, never `SELECT`); every ingest is a dry
run until `--apply`.

## Layout & doc ownership

- `inboxcatalog/` — the domain-neutral engine (incl. `returns.py`). Domain
  logic lives only in `inboxcatalog/profiles/` (`demo`, `amazon`).
- `inboxcatalog/profiles/life_zones.py` — the generic, shipped life-zone
  taxonomy + router. Users override it with an untracked
  `inboxcatalog/profiles/zones.local.json` (gitignored, like private profiles);
  a malformed override fails loudly, never silently. Format: the routing doc.
- `SKILL.md` (root) — the Inbox Catalog skill: routing order demo → connector →
  IMAP → mbox. The skill chooses the path, not the user.
- `amazon-tracker/` — standalone drop-in skill, a **thin wrapper** over the same
  engine. No private engine copies. Its `docs/connect-gmail.md` is a pointer to
  the engine's doc, not a fork.
- `references/life-zone-routing.md` **is duplicated by design** at
  `amazon-tracker/references/life-zone-routing.md` (drop-in folders must be
  self-contained). Edit both or neither — keep them byte-identical.
- `skills/extract-purchase-items/` — reusable extraction skill with
  example-locked schema tests.
- `.claude-plugin/{plugin.json,marketplace.json}` — packages the repo as a
  Claude Code plugin + one-plugin marketplace. `plugin.json` `skills` lists
  `"./"` (root skill) and `"./amazon-tracker"`; `skills/extract-purchase-items`
  is auto-discovered. Skills locate the engine root via `$CLAUDE_PLUGIN_ROOT`
  (falls back to the skill's own folder for a copy/clone install). Publishing +
  local-test walkthrough: `PUBLISHING.md`; skill-behavior scenarios (not code):
  `tests/skill-scenarios.md`.
- `docs/inbox-as-a-dataset.md` — essay; `references/writing-a-profile.md` — how
  users teach the engine their shops.

## Conventions

- Python: `python3` / `pip3`, installs with `--break-system-packages`, **no
  venvs**. Heavy deps are lazy-imported; offline paths run on stdlib.
- Tests (all offline, stdlib + pytest, ~0.3s — run them, they're free):
  `python3 -m pytest tests/ skills/extract-purchase-items/test_examples.py -q`
- Quick end-to-end sanity (use a scratch `INBOX_DATA_DIR`):
  `python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply && python3 -m inboxcatalog --profile amazon --returns`
- DB migrations are **additive only** (`db._migrate`); never clobber non-NULL
  lifecycle fields (enrich, don't dupe).
- Privacy: personal shop lists / profiles / real order data never go into
  committed files. Keep fixture data obviously synthetic (`@example.com`).
- Git: coordinate before rewriting history; verify `git log` after committing.
