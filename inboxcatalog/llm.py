"""Optional LLM fallback parser. Opt-in (--llm) and cached once per email.

Used ONLY for emails no template could parse. The raw JSON response is cached in
llm_cache keyed by email uid, so a message is never sent to the API twice. The
prompt comes from the active CollectionProfile, so the engine stays
domain-neutral. A small/cheap model is the right tool here — never a flagship
model in an automated pipeline.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import TYPE_CHECKING, Optional

from . import config, db, logutil

if TYPE_CHECKING:
    from .profile import CollectionProfile

log = logutil.get("llm")


def available() -> bool:
    return bool(os.environ.get(config.ANTHROPIC_ENV_VAR))


def parse_email(conn: sqlite3.Connection, ctx, profile: "CollectionProfile",
                dry_run: bool) -> list[dict]:
    """Return item dicts from the LLM for this email. Uses cache; on a miss makes
    exactly one API call (unless dry_run, which won't spend money)."""
    cached = db.get_llm_cache(conn, ctx.uid)
    if cached is not None:
        log.debug("uid=%s LLM cache hit", ctx.uid)
        return _normalize(json.loads(cached), ctx)

    if dry_run:
        log.info("uid=%s would call the LLM (dry-run: not spending)", ctx.uid)
        return []

    if not available():
        log.warning("uid=%s no %s set — skipping LLM fallback",
                    ctx.uid, config.ANTHROPIC_ENV_VAR)
        return []

    try:
        import anthropic  # lazy/optional
    except ImportError:
        log.warning("`anthropic` not installed — skipping LLM (pip3 install anthropic)")
        return []

    key = os.environ[config.ANTHROPIC_ENV_VAR]
    logutil.register_secret(key)
    client = anthropic.Anthropic(api_key=key)
    prompt = profile.build_llm_prompt(ctx.from_addr, ctx.subject, ctx.text)
    log.info("uid=%s calling LLM (%s)", ctx.uid, config.LLM_MODEL)
    try:
        resp = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", "") == "text")
    except Exception as exc:
        log.warning("uid=%s LLM call failed: %s", ctx.uid, exc)
        return []

    data = _extract_json(raw)
    if data is None:
        log.warning("uid=%s LLM returned non-JSON; caching empty", ctx.uid)
        data = {"items": []}
    db.put_llm_cache(conn, ctx.uid, json.dumps(data), config.LLM_MODEL)
    conn.commit()
    return _normalize(data, ctx)


def _extract_json(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    try:
        return json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                return None
    return None


def _normalize(data: dict, ctx) -> list[dict]:
    out: list[dict] = []
    rows = (data or {}).get("items") or []
    for p in rows:
        if not isinstance(p, dict):
            continue
        out.append({
            "name": p.get("name") or ctx.subject,
            "maker": p.get("maker"),
            "price": _num(p.get("price")),
            "currency": (p.get("currency") or None),
            "quantity": _qty(p.get("quantity")),
            "seller": p.get("maker") or ctx.from_addr.split("@")[-1],
            "order_id": p.get("order_id"),
            "purchased_at": ctx.date_iso,
            "image_url": None,  # image still extracted from HTML by orchestrator
            "source": "llm",
        })
    return out


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _qty(v) -> int:
    """Coerce a quantity to a positive int, defaulting to 1 (conservative)."""
    try:
        n = int(v)
        return n if n > 0 else 1
    except (TypeError, ValueError):
        return 1
