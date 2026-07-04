"""Reusable parse-template machinery.

A *template* knows how to read one family of order/shipment emails and emit
structured item rows. The base :class:`Template` protocol and the shared parsing
helpers (price, currency, image picking) live here because they are completely
domain-neutral — they are about the *shape* of a receipt, not what was bought.

A :class:`CollectionProfile` supplies the concrete list of templates the engine
runs. This module ships one fully generic template, :class:`GenericOrderTemplate`,
that extracts a price + product image + name from almost any order email; profiles
compose it with their own seller-specific templates.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import images, logutil

if TYPE_CHECKING:
    from .parse import EmailCtx

log = logutil.get("templates")

# --- shared helpers --------------------------------------------------------

_CUR_SYMBOL = {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₩": "KRW"}
_CUR_CODE = {"USD", "GBP", "EUR", "JPY", "KRW", "CAD", "AUD"}

# $12.00 / £8.50 / 1,200 JPY / USD 12.00 / ¥1200
_PRICE_RE = re.compile(
    r"(?:(?P<sym>[$£€¥₩])\s?(?P<amt1>\d[\d,]*(?:\.\d{2})?))"
    r"|(?:(?P<code>USD|GBP|EUR|JPY|KRW|CAD|AUD)\s?(?P<amt2>\d[\d,]*(?:\.\d{2})?))"
    r"|(?:(?P<amt3>\d[\d,]*(?:\.\d{2})?)\s?(?P<code2>USD|GBP|EUR|JPY|KRW|CAD|AUD))",
    re.I,
)


def normalize_currency(token: str | None) -> str | None:
    if not token:
        return None
    token = token.strip()
    if token in _CUR_SYMBOL:
        return _CUR_SYMBOL[token]
    up = token.upper()
    return up if up in _CUR_CODE else None


def to_float(amt: str) -> float | None:
    try:
        return float(amt.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def to_qty(v) -> int:
    """Coerce a captured quantity to a positive int; default 1 (conservative)."""
    try:
        n = int(str(v).strip())
        return n if n > 0 else 1
    except (ValueError, TypeError, AttributeError):
        return 1


def find_first_price(text: str) -> tuple[float | None, str | None]:
    """Return (amount, currency) for the first plausible money mention."""
    for m in _PRICE_RE.finditer(text or ""):
        if m.group("sym"):
            return to_float(m.group("amt1")), normalize_currency(m.group("sym"))
        if m.group("code"):
            return to_float(m.group("amt2")), normalize_currency(m.group("code"))
        if m.group("code2"):
            return to_float(m.group("amt3")), normalize_currency(m.group("code2"))
    return None, None


def seller_domain(from_addr: str) -> str:
    m = re.search(r"@([\w.-]+)", from_addr or "")
    return m.group(1).lower() if m else (from_addr or "")


def clean(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# --- base protocol ---------------------------------------------------------

class Template:
    """Implement ``matches`` and ``parse`` to teach the engine a receipt layout."""
    name = "base"

    def matches(self, ctx: "EmailCtx") -> bool:  # pragma: no cover
        raise NotImplementedError

    def parse(self, ctx: "EmailCtx") -> list[dict]:  # pragma: no cover
        raise NotImplementedError


# --- a fully generic, domain-neutral order template ------------------------

class GenericOrderTemplate(Template):
    """Catch-all for any allowlisted shop's order/receipt email.

    Extracts a price, the first product-looking image, and uses the (cleaned)
    subject as the item name. Deliberately matches everything, so profiles place
    it LAST in their template list — specific seller templates get first refusal.
    """
    name = "generic"

    _NAME_STRIP = re.compile(
        r"(?i)^(order confirmation|your order is confirmed|your order|"
        r"order confirmed|order is confirmed|receipt|confirmed|"
        r"thank you for your (?:order|purchase))[:\-\s]*")
    _ORDER_RE = re.compile(
        r"order\s*(?:#|number|no\.?|id)?\s*[:#]?\s*([A-Za-z0-9-]{4,})", re.I)

    def matches(self, ctx: "EmailCtx") -> bool:
        return True  # only reached after specific templates decline

    def _item_name(self, subject: str) -> str:
        # Most order subjects read "<prefix>: <item>" or "<prefix> - <item>".
        # Prefer the text after the last delimiter when it looks substantial,
        # otherwise strip the known prefix from the front.
        for delim in (": ", " - ", " — "):
            if delim in subject:
                tail = subject.rsplit(delim, 1)[-1].strip()
                if len(tail) >= 3:
                    return clean(tail) or subject
        return clean(self._NAME_STRIP.sub("", subject)) or clean(subject) or "Item"

    def parse(self, ctx: "EmailCtx") -> list[dict]:
        text = ctx.text
        price, cur = find_first_price(text)
        order_id = None
        mo = self._ORDER_RE.search(text)
        if mo:
            order_id = mo.group(1)
        name = self._item_name(ctx.subject)
        img = images.first_product_image_url(ctx.html)
        domain = seller_domain(ctx.from_addr)
        # Only emit a row if we got *something* useful (a price or an image),
        # otherwise let the LLM path decide.
        if price is None and not img:
            return []
        return [{
            "name": name,
            "maker": None,
            "price": price,
            "currency": cur,
            "quantity": 1,   # generic order subject carries no per-line count
            "seller": domain,
            "order_id": order_id,
            "purchased_at": ctx.date_iso,
            "image_url": img,
            "source": "template",
        }]
