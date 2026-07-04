"""The CollectionProfile abstraction — the one seam that makes the engine
domain-pluggable.

The core engine (ingest, parse, db, embed, lookup) contains *no* knowledge of
what is being catalogued. Everything domain-specific is supplied by a profile:

    * ``sender_allowlist``  — which senders are worth fetching at all
    * ``keyword_gate``      — subject/body terms that mark an email as a purchase
    * ``subject_blocklist`` — order-ish subjects that are NOT purchases (noise)
    * ``merchant_denylist`` — real purchases that aren't the thing you collect
    * ``templates``         — the concrete parse templates (first match wins)
    * ``is_item``           — item-vs-noise signal for a parsed row
    * ``classify``          — optional taxonomy label for a row
    * ``llm_prompt``        — the enrichment prompt for the LLM fallback

Profiles are registered by name in ``profiles/`` and selected at runtime via the
``INBOX_PROFILE`` env var or the ``--profile`` CLI flag. To support a new domain,
write one profile module — you never touch the engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .parse import EmailCtx
    from .templates import Template


@dataclass
class CollectionProfile:
    """A pluggable description of one collection domain.

    The required fields define which emails matter and how to read them; the
    optional callables (``is_item``, ``classify``, ``build_llm_prompt``) let a
    profile add domain judgement without the engine knowing the domain.
    """
    name: str
    description: str
    sender_allowlist: list[str]
    keyword_gate: list[str]
    subject_blocklist: list[str]
    merchant_denylist: list[str]
    templates: list["Template"]

    # Optional domain judgement. Defaults are permissive/no-op so a minimal
    # profile only needs the fields above.
    item_predicate: Optional[Callable[[dict], bool]] = None
    classifier: Optional[Callable[[dict], Optional[str]]] = None
    item_enricher: Optional[Callable[[dict], dict]] = None
    llm_prompt_builder: Optional[Callable[[str, str, str], str]] = None
    llm_noun: str = "purchase"  # human label used in the default LLM prompt

    # --- engine-facing helpers ------------------------------------------

    def passes_subject_blocklist(self, subject: str) -> bool:
        """True if the subject is NOT on the noise blocklist."""
        low = subject.lower()
        return not any(b in low for b in self.subject_blocklist)

    def passes_keyword_gate(self, ctx: "EmailCtx") -> bool:
        """True if the email looks like a purchase: passes the negative subject
        gate first, then trips at least one positive keyword."""
        if not self.passes_subject_blocklist(ctx.subject):
            return False
        hay = f"{ctx.subject}\n{ctx.text}".lower()
        return any(k in hay for k in self.keyword_gate)

    def is_denied_merchant(self, item: dict) -> bool:
        """True if this row's seller/maker is a known non-collectible merchant."""
        hay = f"{item.get('seller') or ''} {item.get('maker') or ''}".lower()
        return any(d in hay for d in self.merchant_denylist)

    def is_item(self, item: dict) -> bool:
        """Item-vs-noise signal. Default: keep anything not on the merchant
        denylist; a profile may supply a stricter predicate."""
        if self.is_denied_merchant(item):
            return False
        if self.item_predicate is not None:
            return self.item_predicate(item)
        return True

    def classify(self, item: dict) -> Optional[str]:
        """Optional taxonomy label for a row (e.g. genre/category). None if the
        profile supplies no classifier."""
        if self.classifier is not None:
            return self.classifier(item)
        return None

    def enrich(self, item: dict) -> dict:
        """Optional last-touch enrichment before a row is written (e.g. default
        decision state, life-zone routing). Identity if the profile supplies no
        enricher."""
        if self.item_enricher is not None:
            return self.item_enricher(item)
        return item

    def build_llm_prompt(self, sender: str, subject: str, body: str) -> str:
        """Prompt for the LLM fallback parser. A profile may override entirely;
        otherwise a domain-neutral default is generated from ``llm_noun``."""
        if self.llm_prompt_builder is not None:
            return self.llm_prompt_builder(sender, subject, body)
        return _DEFAULT_LLM_PROMPT.format(
            noun=self.llm_noun, sender=sender, subject=subject, body=body[:6000])


_DEFAULT_LLM_PROMPT = """You extract {noun} details from a shopping email.
Return ONLY a JSON object, no prose, of this shape:

{{"items": [
  {{"name": str|null, "maker": str|null, "price": number|null,
    "currency": str|null, "quantity": int, "order_id": str|null}}
]}}

Rules:
- One object per distinct item purchased (split multi-item orders).
- "maker" is the shop / brand / maker.
- "currency" is a 3-letter ISO code (USD, GBP, EUR, JPY...).
- "quantity" is the units of that line (e.g. "2x" -> 2). Default to 1 if not shown.
- "price" is the per-UNIT price. If only a line total for quantity N is shown,
  divide by N (e.g. "2x - $34.00" -> price 17.00). A "Free" line is price 0.
- If this email is NOT a {noun}, return {{"items": []}}.

Email sender: {sender}
Subject: {subject}
Body:
{body}
"""
