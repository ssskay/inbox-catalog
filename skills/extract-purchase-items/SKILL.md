---
name: extract-purchase-items
description: Extract clean, structured line-items from the text/HTML of a single order confirmation, shipment-confirmation, or receipt email. Use this when you have one purchase email and need JSON of what was actually bought (name, maker/brand, price, currency, quantity, order id) — for any retailer, with no domain assumptions. Returns empty items for marketing, payment-only, or non-purchase emails rather than guessing.
---

# Extract purchase line-items from an email

Given the raw text (and/or HTML) of **one** order/shipment/receipt email, return a
JSON object listing only the products that were actually purchased. This skill is
domain-neutral: it works for books, board games, electronics, clothing, anything.

It is the human-readable companion to the engine prompt in
`inboxcatalog/profile.py` (`_DEFAULT_LLM_PROMPT`, used by `inboxcatalog/llm.py`).
The output schema below is **identical** to that prompt so the skill and the
engine agree byte-for-byte. Do not invent extra keys.

## Output schema

Return **only** a JSON object (no prose, no Markdown fence) of exactly this shape:

```json
{
  "items": [
    {
      "name": "string | null",
      "maker": "string | null",
      "price": 0.0,
      "currency": "string | null",
      "quantity": 1,
      "order_id": "string | null"
    }
  ]
}
```

Field rules:

- `name` — the product title as shown. Trim shipping/marketing cruft, keep the
  real title (including an edition/expansion suffix that is part of the name).
- `maker` — the shop / brand / storefront the email is from. This is the
  **storefront**, not the legal merchant or payment descriptor (see rule 2).
- `price` — a JSON number, no currency symbol, no thousands separators
  (`1299.00`, not `"$1,299.00"`). The **per-unit** price. If the email shows a
  per-unit price, use it. If it shows only a **line total** for a quantity of N,
  divide by N to get the per-unit price (e.g. `2x Badge — $34.00` → `price:
  17.00`). `null` if no price is shown.
- `currency` — a 3-letter ISO code (`USD`, `GBP`, `EUR`, `JPY`, ...), inferred
  from the symbol or code. `null` if you cannot tell (see rule 6).
- `quantity` — the number of units on that line, as an integer (`"2x" → 2`).
  **Default to `1`** when no count is shown. Always an int, never `null`.
- `order_id` — the order / receipt number for the purchase, repeated on every
  item from that order. `null` if none is shown.

There is **no** top-level `order_id`/`seller`/`currency` — everything the engine
stores lives on each item object. `price` is **per-unit**, so `price × quantity`
should reconcile to that line's total. Two common layouts:

- `2x Widget — $9.00 each` → `price: 9.00, quantity: 2` (per-unit shown; use it).
- `2x Widget — $18.00` (a **line total**, no "each") → `price: 9.00, quantity: 2`
  (divide the total by the quantity).

When a line total does not divide evenly into the quantity, round the per-unit
price to the currency's minor unit (cents). A `Free` line is `price: 0.00`, not
`null`.

A non-purchase email returns `{"items": []}`. An empty array is a correct,
expected answer — never pad it.

## The seven rules (this is the actual value)

These are hard-won extraction lessons. Apply every one:

1. **Recommendation carousels poison extraction.** Blocks titled "You might also
   like", "Recommended for you", "Customers also bought", "Complete your
   collection", "Trending now", etc. list products that were **not** purchased.
   Never emit them as items. The purchased items are the ones in the order/line
   table, not the suggestion strip.

2. **Shop/brand name ≠ merchant string.** The storefront shown in the email (e.g.
   "Tabletop Trove") usually differs from the legal merchant or payment-processor
   descriptor on the charge (e.g. "TT RETAIL LLC" or a card statement string).
   Capture the human-facing **storefront** as `maker`. Do not use a payment
   descriptor, gateway name, or `noreply@` domain as the brand when a real
   storefront name is present.

3. **Payment-processor emails often have no products.** PayPal / Stripe / "you
   sent a payment" / generic charge confirmations confirm money moved, not a
   catalog of goods. They frequently have no itemized product and no image.
   Extract only what is genuinely present; if there is no named product line,
   return `{"items": []}`. Do **not** turn the payee name or the total into a
   fake product. (The engine's schema has nowhere to park a bare order id without
   a product, so a pure payment notice with no product is correctly empty.)

4. **Multi-item orders split; capture per-unit price and quantity.** One email can
   contain several line-items. Emit one object per distinct purchased product,
   each carrying the shared `order_id`, its own `quantity` (`"2x" → quantity: 2`),
   and its own **per-unit** `price`. When the email prints a line *total* for a
   quantity, divide by the quantity so `price` is per-unit. Do not merge two lines
   of the same product if they carry different prices (e.g. one is bundle-
   discounted) — they are distinct line-items.

5. **Subtotal / shipping / tax / discount / total are NOT items.** Lines like
   "Subtotal", "Shipping", "Tax", "Discount", "Gift card", "Store credit",
   "Total" are accounting rows, not products. Never emit them as items, and never
   use the order Total as a product price.

6. **Currency: infer, don't guess.** Map the symbol or code you see
   (`$`→`USD` unless context says `CA$`/`A$`, `£`→`GBP`, `€`→`EUR`, `¥`→`JPY`).
   If no symbol or code is present, set `currency` to `null` rather than assuming.

7. **Be conservative — null over hallucination.** If a price, name, brand, or
   order id is not actually in the email, the value is `null`. Never invent,
   complete, or "remember" a value that is not on the page. Missing is a valid,
   honest answer.

## How to read an email

1. Prefer the **plain-text** part for line extraction; use the **HTML** part to
   disambiguate product names, prices, and to tell the order table apart from a
   recommendation carousel (carousels are usually a separate styled block).
2. Locate the order/line table — the rows with a product and a price next to it.
3. Drop any row matched by rule 5 (subtotal/shipping/tax/discount/total).
4. Drop any block matched by rule 1 (recommendation/cross-sell carousel).
5. For each surviving purchased line, build one item object.
6. Set `maker` to the storefront (rule 2); set `order_id` from the order/receipt
   number; infer `currency` (rule 6).
7. If nothing survives, return `{"items": []}` (rules 3, 5).

## Worked example

Input (excerpt):

```
From: Meeple Market <ship@meeplemarket.example>
Subject: Your games shipped! Order #MM-7781

Order #MM-7781
1x Azul - $40.00
1x Codenames - $19.95
Subtotal: $59.95
Shipping: $0.00
Total: $59.95
```

Output:

```json
{
  "items": [
    {"name": "Azul", "maker": "Meeple Market", "price": 40.00, "currency": "USD", "quantity": 1, "order_id": "MM-7781"},
    {"name": "Codenames", "maker": "Meeple Market", "price": 19.95, "currency": "USD", "quantity": 1, "order_id": "MM-7781"}
  ]
}
```

Two products split into two rows (rule 4), each with `quantity: 1`, the shared
order id repeated, and the Subtotal/Shipping/Total lines excluded (rule 5). A line
reading `2x Codenames` would instead be `"quantity": 2` with the same per-unit
`price`.

## Edge-case guidance

- **Marketing / sale / "back in stock" emails** — not purchases. `{"items": []}`.
- **Infrastructure / SaaS subscription receipts** (hosting, domains, API plans) —
  these are real charges but they are services, not catalogable goods, and a
  consumer profile will filter them anyway. With no physical product line, return
  `{"items": []}` unless the caller's domain explicitly wants services.
- **Refund / return / cancellation** notices — not purchases. `{"items": []}`.
- **Accessory vs. main item** — extract what was bought as printed; deciding
  whether an accessory "counts" is the engine's `is_item` job, not this skill's.
  Emit the accessory as a normal item.
- **Prices with thousands separators or trailing currency code** (`$1,299.00 USD`)
  — parse to the number `1299.00` and the code `USD`.
- **No order id** — `order_id: null`, still emit the items.

## Examples

See `examples/` for golden input→output pairs, one per edge case:

| Example                          | Demonstrates                                      |
|----------------------------------|---------------------------------------------------|
| `01-single-item-order`           | single item; subtotal/shipping/total excluded (5) |
| `02-multi-item-shipment`         | one email → several line-items split (4)          |
| `03-recommendation-carousel`     | "Recommended for you" block excluded (1, 2)       |
| `04-infra-receipt`               | non-product receipt → empty items (3, 5)          |
| `05-multi-quantity-order`        | per-unit price stated as "each", `quantity` > 1 (4) |
| `06-line-total-per-unit`         | line total ÷ quantity → per-unit price; `Free` → 0 (4) |
