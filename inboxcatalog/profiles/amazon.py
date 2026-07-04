"""Amazon returns-tracker profile.

Recognizes Amazon **order-confirmation, shipment, delivery, and return-window**
emails and catalogs each purchased item with the standard schema (name, price,
currency, quantity, order id, order date, image) plus the lifecycle dates the
return-decision layer needs (delivery date, explicit return-by date when mail
states one).

On top of cataloguing, every item gets:
  * a return state (default ``evaluate`` — the "not sure yet" pile), and
  * a life-zone tag routed via :mod:`.life_zones`
    (taxonomy: ``references/life-zone-routing.md``).

Ships with offline fixtures (``fixtures_amazon/``) so the whole flow runs with
no mailbox: ``python3 -m inboxcatalog --profile amazon --ingest --fixtures
inboxcatalog/profiles/fixtures_amazon``.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

from .. import images, logutil
from ..parse import EmailCtx
from ..profile import CollectionProfile
from ..returns import DEFAULT_STATE
from ..templates import Template, clean, to_float, to_qty
from . import register
from .life_zones import detect_gift, route

log = logutil.get("profile.amazon")

# Amazon order ids look like 113-2591024-7364219.
_ORDER_RE = re.compile(r"#?\s*(\d{3}-\d{7}-\d{7})")
# "June 25, 2026" / "Jun 25, 2026" — the date shapes Amazon mail prints.
_DATE_RE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
_DELIVERED_RE = re.compile(r"delivered(?:\s+on)?[:\s]+" + _DATE_RE, re.I)
_RETURN_BY_RE = re.compile(
    r"(?:return(?:able| eligible)?(?: is)?(?:\s+\w+){0,3}?\s+"
    r"(?:through|until|by|closes? on)|return window closes? on)\s+" + _DATE_RE,
    re.I)
# "2 x Item Name - $28.99" line items (synthetic-fixture / plain-text shape).
_LINE_RE = re.compile(
    r"^\s*(?P<qty>\d+)\s*[x×]\s*(?P<title>.+?)\s*[—\-:]\s*"
    r"(?P<sym>[$£€])?(?P<amt>\d[\d,]*(?:\.\d{2})?)(?:\s*(?:each|/ea))?\s*$",
    re.M)
# Bare item lines in shipment/delivery mail: "- Item Name" (no price).
_BARE_LINE_RE = re.compile(r"^\s*[-•]\s+(?P<title>[^\n]{3,100})\s*$", re.M)
# Real Amazon mail (2024+ layout, after html_to_text): an item is a block of
#   * Full Product Name Possibly Very Long
#     Quantity: 1
#     8.98 USD          <- present in order/shipped mail, absent in delivered
_BLOCK_RE = re.compile(
    r"^\s*\*\s*(?P<title>[^\n]{4,300}?)\s*\n"
    r"\s*Quantity:\s*(?P<qty>\d+)\s*\n?"
    r"(?:\s*(?P<amt>\d[\d,]*(?:\.\d{2})?)\s*(?P<cur>USD|EUR|GBP|CAD|AUD|JPY)\b)?",
    re.M)
# Subject fallback: Ordered/Shipped/Delivered: "Item name..." [and N more items]
_SUBJ_ITEM_RE = re.compile(
    r'(?:ordered|shipped|delivered|out for delivery|arriving[^:]*):\s*'
    r'["“](?P<title>.+?)(?:\.\.\.|…)?["”]', re.I)
# Ship-to line: "Alex - BRIGHTON, MA". If INBOX_OWNER_NAME is set and the name
# doesn't match, that's the alternate-ship-to gift signal from the taxonomy.
_SHIP_TO_RE = re.compile(r"^\s*(?P<who>[A-Z][\w.'’-]{1,40}) - [A-Z][A-Z ,.]{2,40}$",
                         re.M)


def _to_iso(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _order_id(text: str) -> Optional[str]:
    m = _ORDER_RE.search(text)
    return m.group(1) if m else None


_CUR = {"$": "USD", "£": "GBP", "€": "EUR"}


def _extract_items(ctx: EmailCtx, oid: Optional[str]) -> list[dict]:
    """All item rows an Amazon email carries, trying (in order): the real
    '* name / Quantity / price' block layout, the fixture '2 x name - $p'
    lines, and finally the quoted item name in the subject (which Amazon
    truncates — better a partial name than a lost item)."""
    rows: list[dict] = []
    for m in _BLOCK_RE.finditer(ctx.text):
        title = clean(m.group("title"))
        if not title or _ORDER_RE.search(title):
            continue
        row = _base_row(ctx, title, oid)
        row.update(quantity=to_qty(m.group("qty")),
                   price=to_float(m.group("amt")) if m.group("amt") else None,
                   currency=m.group("cur"))
        rows.append(row)
    if rows:
        return rows
    for m in _LINE_RE.finditer(ctx.text):
        title = clean(m.group("title"))
        if not title:
            continue
        row = _base_row(ctx, title, oid)
        row.update(price=to_float(m.group("amt")),
                   currency=_CUR.get(m.group("sym") or "$", "USD"),
                   quantity=to_qty(m.group("qty")))
        rows.append(row)
    if rows:
        return rows
    m = _SUBJ_ITEM_RE.search(ctx.subject)
    if m:
        title = clean(m.group("title"))
        if title:
            log.info("uid=%s: no body items; falling back to subject name %r "
                     "(may be truncated)", ctx.uid, title)
            return [_base_row(ctx, title, oid)]
    return rows


def _gift_signal(ctx: EmailCtx) -> Optional[str]:
    """Gift receipt text, or an alternate ship-to name when INBOX_OWNER_NAME
    is set (the account owner's first name): a different name on the ship-to
    line (e.g. 'Jessica - PORTLAND, OR') is a gift shipping somewhere else."""
    sig = detect_gift(ctx.text)
    if sig:
        return sig
    owner = os.environ.get("INBOX_OWNER_NAME", "").strip().lower()
    if owner:
        m = _SHIP_TO_RE.search(ctx.text)
        if m and owner not in m.group("who").lower():
            return f"ships to {m.group('who')}"
    return None


def _base_row(ctx: EmailCtx, name: str, order_id: Optional[str]) -> dict:
    return {
        "name": name,
        "maker": "Amazon",
        "price": None,
        "currency": None,
        "quantity": 1,
        "seller": "amazon.com",
        "order_id": order_id,
        "purchased_at": ctx.date_iso,
        "image_url": images.first_product_image_url(ctx.html),
        "source": "template",
    }


class AmazonOrderTemplate(Template):
    """Order-confirmation email: the authoritative itemized sighting — one row
    per line item, with quantity, per-unit price, order id, order date, and any
    gift signal (which later drives the gift-zone override)."""
    name = "amazon_order"

    def matches(self, ctx: EmailCtx) -> bool:
        return ("amazon." in ctx.from_addr
                and ("order" in ctx.subject.lower()
                     or "order confirmation" in ctx.text.lower())
                and not AmazonShipmentTemplate._KIND_RE.search(ctx.subject))

    def parse(self, ctx: EmailCtx) -> list[dict]:
        oid = _order_id(ctx.subject) or _order_id(ctx.text)
        gift = _gift_signal(ctx)
        return_by = _RETURN_BY_RE.search(ctx.text)
        rows = _extract_items(ctx, oid)
        for row in rows:
            if return_by:
                row["return_by"] = _to_iso(return_by.group(1))
            if gift:
                row["is_gift"] = gift
            log.info("order %s: extracted %r qty=%s price=%s gift=%s",
                     oid, row["name"], row["quantity"], row["price"], bool(gift))
        return rows


class AmazonShipmentTemplate(Template):
    """Shipment / delivery email: re-mentions of order lines. A 'Delivered'
    email carries the delivery date, which starts the return-window clock; the
    ingest layer merges it onto the already-catalogued row."""
    name = "amazon_shipment"

    _KIND_RE = re.compile(r"\b(shipped|delivered|out for delivery|arriving)\b", re.I)

    def matches(self, ctx: EmailCtx) -> bool:
        return "amazon." in ctx.from_addr and bool(self._KIND_RE.search(ctx.subject))

    def parse(self, ctx: EmailCtx) -> list[dict]:
        oid = _order_id(ctx.subject) or _order_id(ctx.text)
        delivered = _DELIVERED_RE.search(ctx.text)
        delivered_iso = _to_iso(delivered.group(1)) if delivered else None
        # Real 'Delivered:' mail says "Delivered today" with no date in the
        # body — the email's own date IS the delivery date.
        if not delivered_iso and re.match(r"\s*delivered", ctx.subject, re.I):
            delivered_iso = ctx.date_iso
        rows = _extract_items(ctx, oid)
        legacy = []
        if not rows:
            for m in _BARE_LINE_RE.finditer(ctx.text):
                title = clean(m.group("title"))
                if title and not _ORDER_RE.search(title):
                    legacy.append(_base_row(ctx, title, oid))
            rows = legacy
        for row in rows:
            row["delivered_at"] = delivered_iso
            # A shipment sighting is not the purchase date; keep the original.
            row["purchased_at"] = None
        log.info("shipment/delivery for order %s: %d line(s), delivered_at=%s",
                 oid, len(rows), delivered_iso)
        return rows


_REFUND_SUBJECT_RE = re.compile(r"\brefund", re.I)


class AmazonRefundTemplate(Template):
    """Refund email: not a purchase but a **state** signal — a refund means the
    return completed, so the matching order's catalogued item(s) are now
    ``returned``. Emits an order-level 'return event' (the automated equivalent of
    ``--mark <order> returned``) that the ingest layer applies to existing rows
    instead of inserting anything. Order-level on purpose: a partial refund marks
    the whole order (rare; correctable with ``--mark``), which is predictable and
    never guesses the wrong item.

    Must be tried FIRST — a refund subject like "Refund issued for your return"
    also trips the return-window and order templates."""
    name = "amazon_refund"

    def matches(self, ctx: EmailCtx) -> bool:
        return "amazon." in ctx.from_addr and bool(_REFUND_SUBJECT_RE.search(ctx.subject))

    def parse(self, ctx: EmailCtx) -> list[dict]:
        oid = _order_id(ctx.subject) or _order_id(ctx.text)
        if not oid:
            log.info("refund email with no parseable order id (subj=%r) — skipped",
                     ctx.subject[:60])
            return []
        log.info("refund for order %s -> mark returned", oid)
        return [{"seller": "amazon.com", "maker": "Amazon", "order_id": oid,
                 "name": None, "_return_event": "returned", "source": "template"}]


class AmazonReturnWindowTemplate(Template):
    """Return-window reminder: carries the authoritative return-by date."""
    name = "amazon_return_window"

    def matches(self, ctx: EmailCtx) -> bool:
        return "amazon." in ctx.from_addr and "return" in ctx.subject.lower()

    def parse(self, ctx: EmailCtx) -> list[dict]:
        oid = _order_id(ctx.subject) or _order_id(ctx.text)
        m = _RETURN_BY_RE.search(ctx.text)
        return_by = _to_iso(m.group(1)) if m else None
        rows = _extract_items(ctx, oid)
        if not rows:
            for bm in _BARE_LINE_RE.finditer(ctx.text):
                title = clean(bm.group("title"))
                if title and not _ORDER_RE.search(title):
                    rows.append(_base_row(ctx, title, oid))
        for row in rows:
            row["return_by"] = return_by
            row["purchased_at"] = None
        log.info("return-window for order %s: %d line(s), return_by=%s",
                 oid, len(rows), return_by)
        return rows


# --- coarse product category (separate axis from the life zone) -------------

_CATEGORY_SIGNALS = {
    "craft-supply": ("craft vinyl", "vinyl", "resin", "acrylic", "blanks",
                     "craft paint", "yarn"),
    "book": ("book", "workbook", "manga", "novel", "textbook", "paperback"),
    "electronics": ("keyboard", "monitor", "usb", "charger", "raspberry",
                    "arduino", "speaker", "webcam", "cable"),
    "home": ("storage", "organizer", "bin", "shelf", "hook", "mailer", "tape"),
    "toy-collectible": ("plush", "figure", "pin", "blind box", "toy"),
    "apparel": ("shirt", "hoodie", "sock", "blazer", "legging"),
    "health-fitness": ("protein", "creatine", "yoga", "dumbbell", "skincare"),
}


def _classify(item: dict) -> Optional[str]:
    hay = (item.get("name") or "").lower()
    for label, signals in _CATEGORY_SIGNALS.items():
        if any(sig in hay for sig in signals):
            return label
    return None


def _enrich(item: dict) -> dict:
    """Default the return state and route the life zone. Runs last, once the
    row is otherwise complete (so routing sees quantity + gift signal)."""
    item.setdefault("return_state", DEFAULT_STATE)
    return route(item)


AMAZON_PROFILE = register(CollectionProfile(
    name="amazon",
    description="Amazon orders with a returns tracker (keep/return/evaluate + "
                "return-window clock) and life-zone triage.",
    sender_allowlist=[
        "amazon.com",        # auto-confirm@, shipment-tracking@, order-update@,
                             # return@, no-reply@ — all end in amazon.com
    ],
    keyword_gate=[
        "your order", "order confirmation", "order #", "has shipped",
        "shipped:", "delivered", "out for delivery", "arriving",
        "return window", "your package", "your amazon.com order", "refund",
    ],
    subject_blocklist=[
        "% off", "deal", "deals", "sponsored", "recommended for you",
        "coupon", "lightning", "prime day", "kindle unlimited",
        "rate your", "review your purchase", "how was your",
        "verify your", "reset your", "sign-in", "sign in",
        # NOTE: "refund" is intentionally NOT blocked — a refund email is the
        # signal that auto-marks an order `returned` (AmazonRefundTemplate).
    ],
    merchant_denylist=[],    # seller is always Amazon; nothing to deny
    templates=[
        AmazonRefundTemplate(),         # FIRST — refund subjects can also trip
        AmazonShipmentTemplate(),       # 'Shipped:/Delivered:' next — those and
        AmazonReturnWindowTemplate(),   # return-window subjects contain 'order'
        AmazonOrderTemplate(),
    ],
    classifier=_classify,
    item_enricher=_enrich,
    llm_noun="Amazon purchase",
))
