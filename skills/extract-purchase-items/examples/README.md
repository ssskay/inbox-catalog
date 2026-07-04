# Golden examples

Each pair is an input email (`*.eml`) and the expected extraction
(`*.expected.json`) the `extract-purchase-items` skill should produce. All emails
are synthetic, on the reserved `.example` domain (RFC 2606); nothing here is real.

| Input                              | Expected                                   | Edge case demonstrated                                            |
|------------------------------------|--------------------------------------------|------------------------------------------------------------------|
| `01-single-item-order.eml`         | `01-single-item-order.expected.json`       | Single item; subtotal/shipping/total lines excluded (rule 5)     |
| `02-multi-item-shipment.eml`       | `02-multi-item-shipment.expected.json`     | One email → two line-items split, shared order id (rule 4)       |
| `03-recommendation-carousel.eml`   | `03-recommendation-carousel.expected.json` | "Recommended for you" carousel excluded (rules 1, 2)             |
| `04-infra-receipt.eml`             | `04-infra-receipt.expected.json`           | Non-product subscription receipt → empty items (rules 3, 5)      |
| `05-multi-quantity-order.eml`      | `05-multi-quantity-order.expected.json`    | Per-unit price stated ("each"), `quantity` > 1 (rule 4)         |
| `06-line-total-per-unit.eml`       | `06-line-total-per-unit.expected.json`     | Line total ÷ quantity → per-unit price; `Free` → 0.00 (rule 4)  |

The expected JSON matches the engine schema in `inboxcatalog/profile.py`
(`_DEFAULT_LLM_PROMPT`): `{"items": [{"name", "maker", "price", "currency",
"quantity", "order_id"}]}`.
