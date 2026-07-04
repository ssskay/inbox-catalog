"""Synthetic demo profile: a board-game collection.

This is the example someone sees first when they clone the repo. It catalogues
**board games and expansions** bought from (entirely fictional) online shops, and
ships with offline ``.eml`` fixtures so the whole pipeline runs with no mailbox
and no network.

Nothing here is real: the shops (``meeplemarket.example`` …) and the data are
invented purely to demonstrate the engine. To catalogue your own domain, copy
this file, swap the allowlist/keywords/templates, and register it.
"""
from __future__ import annotations

import re
from typing import Optional

from .. import images, logutil
from ..parse import EmailCtx
from ..profile import CollectionProfile
from ..templates import GenericOrderTemplate, Template, clean, to_float, to_qty
from . import register

log = logutil.get("profile.demo")


# --- a seller-specific template: itemized shipment from "Meeple Market" -----

class MeepleMarketShipmentTemplate(Template):
    """Parses the fictional Meeple Market 'your games shipped' email, which lists
    one line per game: ``1x Wingspan — $65.00``. Demonstrates a multi-item
    template that emits several rows from one email."""
    name = "meeple_shipment"

    _LINE_RE = re.compile(
        r"^\s*(?P<qty>\d+)\s*[x×]\s*(?P<title>.+?)\s*[—\-:]\s*"
        r"(?P<sym>[$£€])?(?P<amt>\d[\d,]*(?:\.\d{2})?)",
        re.M)

    def matches(self, ctx: "EmailCtx") -> bool:
        return "meeplemarket.example" in ctx.from_addr

    def parse(self, ctx: "EmailCtx") -> list[dict]:
        order_id = None
        mo = re.search(r"order\s*#?\s*([A-Za-z0-9-]{4,})", ctx.text, re.I)
        if mo:
            order_id = mo.group(1)
        rows: list[dict] = []
        for m in self._LINE_RE.finditer(ctx.text):
            title = clean(m.group("title"))
            if not title:
                continue
            price = to_float(m.group("amt"))
            cur = {"$": "USD", "£": "GBP", "€": "EUR"}.get(m.group("sym") or "$", "USD")
            rows.append({
                "name": title,
                "maker": "Meeple Market",
                "price": price,
                "currency": cur,
                "quantity": to_qty(m.group("qty")),
                "seller": "Meeple Market",
                "order_id": order_id,
                "purchased_at": ctx.date_iso,
                "image_url": images.first_product_image_url(ctx.html),
                "source": "template",
            })
        return rows


# --- optional taxonomy: bucket a game by a few keyword signals --------------

_CATEGORY_SIGNALS = {
    "strategy": ("strategy", "engine builder", "worker placement", "wingspan",
                 "terraforming", "scythe", "gloomhaven"),
    "party": ("party", "codenames", "dixit", "telestrations", "wavelength"),
    "family": ("family", "ticket to ride", "carcassonne", "azul", "splendor"),
    "expansion": ("expansion", "expacks", " exp.", "europe expansion"),
}


def _classify_game(item: dict) -> Optional[str]:
    hay = f"{item.get('name') or ''} {item.get('maker') or ''}".lower()
    for label, signals in _CATEGORY_SIGNALS.items():
        if any(sig in hay for sig in signals):
            return label
    return "uncategorized"


# --- item-vs-noise: drop obvious non-game accessories -----------------------

_NON_GAME_TYPES = (
    "card sleeve", "sleeves", "playmat", "play mat", "dice tray", "insert",
    "organizer", "gift card", "shipping protection",
)


def _is_game(item: dict) -> bool:
    hay = (item.get("name") or "").lower()
    return not any(t in hay for t in _NON_GAME_TYPES)


# --- assemble + register ----------------------------------------------------

DEMO_PROFILE = register(CollectionProfile(
    name="demo",
    description="Synthetic board-game collection (fictional shops, offline fixtures).",
    # Fictional shops, plus generic marketplaces an order email might come from.
    # `.example` is the reserved-for-docs TLD (RFC 2606) — guaranteed non-routable.
    sender_allowlist=[
        "meeplemarket.example",
        "tabletoptrove.example",
        "boardgamebazaar.example",
        "shop.app",          # generic Shopify notifications a game shop might use
    ],
    keyword_gate=[
        "your order", "order confirmation", "order confirmed",
        "thank you for your order", "thank you for your purchase",
        "receipt", "your purchase", "order #", "order number",
        "your games shipped", "has shipped", "board game", "tabletop",
    ],
    subject_blocklist=[
        "% off", "flash sale", "coupon", "coming soon", "price drop",
        "back in stock", "abandoned cart", "still in your cart", "wishlist",
        "newsletter", "leave a review", "rate your", "how was your",
        "refund", "return label", "we received your return",
        "verify your", "reset your", "sign in", "log in",
    ],
    # Domain-neutral infra/SaaS that are real purchases but never a board game.
    merchant_denylist=[
        "cloudflare", "github", "vercel", "namecheap", "godaddy", "openai",
        "anthropic", "aws", "google cloud", "spotify", "netflix", "uber",
        "doordash", "stamps.com", "pirate ship", "pitney bowes",
    ],
    templates=[
        MeepleMarketShipmentTemplate(),
        GenericOrderTemplate(),   # catch-all, must stay last
    ],
    item_predicate=_is_game,
    classifier=_classify_game,
    llm_noun="board-game purchase",
))
