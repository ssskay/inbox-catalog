# Life-Zone Routing

*Written for a Claude Code instance with no prior context. A "life zone" is a
category label the Amazon tracker tags each catalogued item with, on top of the
keep / return / evaluate state it already computes. It answers: **"which part of
life is this purchase for?"***

The engine ships a **generic default taxonomy** (below). It is fully
**user-configurable** — anyone can replace the whole zone set with their own via
a local, untracked config file (see [Writing your own taxonomy](#writing-your-own-taxonomy)).

---

## The two triage axes

1. **Return state** (already built): `keep` / `return` / `evaluate` / `returned`
   + a return-window clock (days left, expired).
2. **Life-zone tag** (this doc): which zone the item serves.

Every catalogued item gets **one primary zone** (optional secondary), plus its
return state.

---

## The default taxonomy

Each zone matches an item by **signals** — regexes tested (case-insensitively)
against the item name. Short/ambiguous tokens use word boundaries so `pin`
doesn't fire on `pineapple`.

| Zone id | Label | Example signals |
|---|---|---|
| `crafting` | 🔨 Crafting — maker & craft supplies | `craft vinyl`, `acrylic sheet`, `resin`, `blanks`, `yarn`, `pin backs` |
| `home-org` | 🧺 Home & Org — storage, cleaning & mailing supplies | `storage`, `organizer`, `bin`, `shelf`, `poly mailer`, `packing tape`, `label` |
| `fandom-fun` | 🌟 Fandom & Fun — collecting, hobbies & self-care | `anime`, `figure`, `plush`, `pin`, `blind box`, `manga`, `collectible` |
| `resale` | 🏷️ Resale — inventory & reselling logistics | `bulk`, `lot of N`, `wholesale`, `display board`, `shipping scale`, `barcode` |
| `language-learning` | 📖 Language Learning — study materials | `spanish`, `french`, `japanese`, `phrasebook`, `grammar workbook`, `flashcards` |
| `fitness` | 🏃 Fitness — movement & nutrition | `protein`, `creatine`, `resistance band`, `dumbbell`, `yoga mat`, `workout` |
| `dev-hardware` | 🔌 Dev Hardware — electronics & tinkering | `raspberry pi`, `arduino`, `sensor`, `breadboard`, `microcontroller`, `gpio` |
| `work-office` | 💼 Work & Office — desk & job equipment | `mechanical keyboard`, `monitor`, `usb-c dock`, `ergonomic`, `standing desk`, `webcam` |
| `trips-events` | 🧳 Trips & Events — travel & event logistics | `travel`, `luggage`, `packing cube`, `travel adapter`, `passport`, `tsa` |
| `gifts` | 🎁 Gifts — presents for other people | *(override-only — see rules; detected by gift signal, not category)* |
| `content-gear` | 🎥 Content Gear — creator equipment | `ring light`, `softbox`, `tripod`, `backdrop`, `microphone` |
| `academic` | 🎓 Academic — school & conference supplies | `poster tube`, `presentation clicker`, `badge holder`, `lab notebook` |
| `unrouted` | *(reserved)* — needs your call | *(no positive signal matched — never guessed)* |

Two cross-cutting mechanisms sit on top of the zones:

- **`gifts` is override-only.** A gift signal (a gift receipt in the email, or,
  when `INBOX_OWNER_NAME` is set, a ship-to line with a different name) routes the
  item to `gifts` regardless of what it is. A gifted craft kit is a gift, not
  crafting.
- **The spend flag** (finance) is **not a zone**. On top of whatever zone an item
  lands in, the tracker flags items over a spend threshold (default `$75`,
  `INBOX_SPEND_FLAG`) *and* clusters of many orders in a short window
  (default ≥3 orders within 7 days; `INBOX_CLUSTER_MIN_ORDERS`,
  `INBOX_CLUSTER_DAYS`). This applies the finance lens without pretending money is
  a "thing you bought."

---

## Routing rules for the classifier

1. **Assign one primary zone + optional secondary.** Primary drives the
   `--triage` grouping; both are stored.
2. **No positive signal → `unrouted`.** Surface these for the user to decide.
   **Do not guess.**
3. **`fandom-fun` is not a catch-all.** Only route there on a genuine
   fun/fandom/self-care signal. Uncertain personal items go to `unrouted`.
4. **`gifts` overrides category** whenever a gift signal is present.
5. **Resale tiebreaker.** An explicit resale signal (`wholesale`, `lot of N`, …)
   or a **bulk quantity** (default ≥10) of a *resalable-category* item
   (`crafting`, `home-org`, `fandom-fun`) routes it to `resale`, with the original
   category kept as the secondary zone. A bulk order of trash bags is still
   `home-org` — only resalable categories flip.
6. **The spend flag is a flag, not a zone** (see above).
7. **Log every decision** — matched signal, primary/secondary, confidence, and any
   override — so the user can always trace *why* an item landed where it did.

---

## The `--triage` view

Groups the catalog **by zone**, each zone's items sorted by return-window
days-left ascending (most urgent first), with a dedicated `unrouted` section and
a spend-flag summary. Example (from the bundled synthetic demo):

```
🔨 Crafting — maker & craft supplies (2 item(s) · 2 still returnable)
  • Craft Vinyl Roll 12x5ft Matte              evaluate  ⏳ 21 days left   ← evaluate!
  • Acrylic Sheets 3mm Clear 10 Pack 12x12 inc evaluate  ⏳ 21 days left   ← evaluate!
🌟 Fandom & Fun — collecting, hobbies & self-care (2 item(s) · 2 still returnable)
  • Anime Collectible Figure 4 inch            evaluate  ⏳ 5 days left   ← evaluate!
  • Manga Volume 1 Paperback                   evaluate  ⏳ 23 days left   ← evaluate!
🏃 Fitness — movement & nutrition (1 item(s) · 0 still returnable)
  • Yoga Mat Extra Thick Non-Slip              evaluate  (expired 2026-06-09)

unrouted (1 item(s)) — needs your call:
  • Bamboo Cutting Board Medium                evaluate  ⏳ 25 days left

💰 Spend flags: 1 item(s) over $75: Mechanical Keyboard ($129.99) · 5 orders within 7 days of 2026-06-26
```

---

## Writing your own taxonomy

The default taxonomy is generic on purpose. To route into **your own** zones,
drop a JSON file at `inboxcatalog/profiles/zones.local.json` (override the path
with `INBOX_ZONES_CONFIG`). It is **gitignored** — your taxonomy is your data and
never lands in the public repo. When the file is present it **replaces** the
shipped taxonomy; when it is absent the generic defaults apply.

A malformed override **fails loudly** with a pointed message — the engine never
silently falls back to defaults when you meant to override.

### Config format

```json
{
  "zones": {
    "garden": {
      "label": "🌱 Garden — seeds, soil & tools",
      "signals": ["seeds?", "\\btrowel\\b", "potting soil", "\\bfertilizer\\b"]
    },
    "kitchen": {
      "label": "🍳 Kitchen — cookware & gadgets",
      "signals": ["skillet", "\\bwhisk\\b", "baking sheet", "\\bknife\\b"]
    },
    "presents": {
      "label": "🎁 Presents — for other people",
      "signals": []
    }
  },
  "priority": ["garden", "kitchen"],
  "resalable": ["garden"],
  "resale_zone": null,
  "gift_zone": "presents",
  "bulk_quantity": 10,
  "gift_regex": "gift receipt|this (?:order|item) is a gift"
}
```

| Key | Meaning | Default if omitted |
|---|---|---|
| `zones` | **Required.** Map of `zone-id` → `{label, signals}`. `signals` are regex strings (matched case-insensitively against the item name). `unrouted` is reserved and may not be defined. | — |
| `priority` | Order zones are tested in. Earlier wins on a tie. | zone insertion order |
| `resalable` | Zone ids the bulk-quantity resale tiebreaker may steal from. | `[]` |
| `resale_zone` | Zone id bulk/resale items route to. `null` disables the tiebreaker. | `resale` if defined, else disabled |
| `gift_zone` | Zone id gift-flagged items route to. `null` disables the gift override. | `gifts` if defined, else disabled |
| `bulk_quantity` | Quantity threshold for the resale tiebreaker. | `10` |
| `gift_regex` | Regex for the in-body gift signal. | the shipped default |

### Walkthrough

1. Copy the format above into `inboxcatalog/profiles/zones.local.json`.
2. Replace `zones` with your own ids, labels, and signal regexes. Keep signals
   specific (word-boundaried short tokens) so they don't over-match.
3. List your zones in `priority` (specific/raw-material zones before broad
   "fun" zones so, e.g., a supply beats a collectible).
4. Point `gift_zone` / `resale_zone` at the zones you want the overrides to use
   (or `null` to switch an override off).
5. Run `python3 -m inboxcatalog --profile amazon --triage` — a malformed file
   errors immediately with what to fix; a valid one routes into your zones.

Spend-flag thresholds stay environment-driven (`INBOX_SPEND_FLAG`,
`INBOX_CLUSTER_MIN_ORDERS`, `INBOX_CLUSTER_DAYS`) — they are not part of the
taxonomy file.
