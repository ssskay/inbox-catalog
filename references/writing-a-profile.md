# Writing a profile — make the engine recognize *your* shops

The shipped `demo` profile only matches fictional board-game shops, so a real
inbox catalogs ~0 items until a profile describes the senders and purchase
patterns you actually have. A profile is one small Python module. This is the
honest path from "0 items" to a real catalog.

> **Privacy:** a profile you write is *your* data (it names shops you buy from).
> Keep it local — do **not** commit a personal shop list into this public repo.

## What a profile is

A `CollectionProfile` (see `inboxcatalog/profile.py`) is a plain description of one
collection domain. Required fields:

| Field | What it does |
|---|---|
| `name` | the profile id you pass to `--profile` / `INBOX_PROFILE` |
| `description` | one-line human label |
| `sender_allowlist` | domains/addresses worth fetching at all (server-side IMAP/Gmail search) |
| `keyword_gate` | positive phrases that mark an email as a purchase ("order confirmation", "has shipped", …) |
| `subject_blocklist` | noise to reject up front (sales, cart, review, refund, login) |
| `merchant_denylist` | real charges that are never your item (hosting, SaaS, postage) |
| `templates` | parsers that pull line-items out of an email (see below) |

Optional domain judgement (all default to permissive no-ops):

| Field | What it does |
|---|---|
| `item_predicate` | `(item) -> bool`: keep vs. drop a parsed row (e.g. filter accessories) |
| `classifier` | `(item) -> str`: a taxonomy label (genre, fandom, category) |
| `llm_noun` | the noun used in the LLM fallback prompt, e.g. `"sneaker purchase"` |

## The fastest way: copy the demo

`inboxcatalog/profiles/demo.py` is a complete, commented example. To make your own:

1. **Copy it** to a new module, e.g. `inboxcatalog/profiles/mine.py`.
2. **Change `name`** (e.g. `"mine"`) and `description`.
3. **Replace `sender_allowlist`** with the domains your order emails come from.
   This is the single most important field — if a shop's sender isn't here, its
   emails are never fetched. Tip: search your mail for "order confirmation" /
   "has shipped" and note the `From:` domains.
4. **Tune `keyword_gate` / `subject_blocklist`** if your emails use different
   wording. The demo's defaults are already broad and a good starting point.
5. **Adjust `item_predicate` / `classifier`** for your domain (what counts as an
   item, and how to label it), or delete them to keep everything.
6. **Register it** so the engine can find it: add one import line to
   `_ensure_loaded()` in `inboxcatalog/profiles/__init__.py`, next to the existing
   `from . import demo`:
   ```python
   from . import mine   # importing the module runs its module-level register(...)
   ```
   (Each profile module calls `register(CollectionProfile(...))` at import time, so
   importing it is all that's needed.)

Then run it:

```bash
python3 -m inboxcatalog --profile mine --stats                 # confirm it loaded
INBOX_IMAP_ACCOUNT='you@gmail.com' \
  python3 -m inboxcatalog --profile mine --ingest --imap       # DRY RUN, read-only
```

## Templates: how a shop's email is parsed

`sender_allowlist` decides *which* emails are fetched; **templates** decide *what*
gets pulled out of them. Two kinds:

- **`GenericOrderTemplate()`** — a catch-all that handles common
  "product … price" order layouts. Keep it **last** in the `templates` list so
  shop-specific templates win first. For many shops this alone is enough.
- **A seller-specific `Template` subclass** — for a shop whose layout the generic
  parser misreads. See `MeepleMarketShipmentTemplate` in `demo.py`: implement
  `matches(ctx)` (usually `"theirshop.com" in ctx.from_addr`) and `parse(ctx)`
  returning a list of item dicts (`name`, `maker`, `price`, `currency`,
  `quantity`, `seller`, `order_id`, `purchased_at`, `image_url`, `source`).

Start with only `GenericOrderTemplate()` and add a custom template *only* for a
shop that comes out wrong.

## No template? Use the LLM fallback

If templates miss an email, the optional LLM fallback can extract line-items from
its text (cached, opt-in, needs `ANTHROPIC_API_KEY` and the optional dep):

```bash
python3 -m inboxcatalog --profile mine --ingest --imap --llm
```

Only the receipt *text* is sent to the model; product images are analyzed locally.
The extraction contract it follows is documented in
`skills/extract-purchase-items/SKILL.md`.

## Verify

- `--profile mine --stats` shows your profile is loaded and lists item counts.
- A dry-run ingest reports `candidate emails` (senders matched) and `items added`
  (rows parsed). If candidates are found but items stay 0, your `sender_allowlist`
  is right but a **template** isn't matching that shop's layout — add a
  shop-specific template or try `--llm`.
