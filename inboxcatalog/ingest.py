"""Ingest orchestrator: fetch -> parse -> (optional LLM) -> download -> write.

Default is a DRY RUN: it prints what it WOULD add and writes nothing (no DB rows,
no API spend, no downloads). ``--apply`` commits.

Idempotent: emails already in ingest_log are skipped; items are de-duped on
(profile, seller, order_id, name).

The orchestrator is domain-neutral. It takes a :class:`MessageSource` (live IMAP
or offline fixtures) and a :class:`CollectionProfile` (which senders/keywords/
templates/taxonomy to use) — it contains no knowledge of what is being catalogued.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import db, images, llm, logutil, parse
from .profile import CollectionProfile
from .sources import MessageSource

log = logutil.get("ingest")


@dataclass
class Summary:
    candidates: int = 0
    already_done: int = 0
    added: int = 0
    enriched: int = 0
    returned: int = 0
    duplicate_items: int = 0
    non_item: int = 0
    no_image: int = 0
    no_parse: int = 0
    errors: int = 0
    via_counts: dict = field(default_factory=dict)

    def bump_via(self, via: str) -> None:
        self.via_counts[via] = self.via_counts.get(via, 0) + 1

    def render(self, apply: bool) -> str:
        mode = "APPLIED" if apply else "DRY RUN (nothing written)"
        lines = [
            "",
            f"==== Ingest summary — {mode} ====",
            f"  candidate emails  : {self.candidates}",
            f"  already ingested  : {self.already_done}",
            f"  items added       : {self.added}",
            f"  items enriched    : {self.enriched}",
            f"  items returned    : {self.returned}",
            f"  duplicate items   : {self.duplicate_items}",
            f"  non-item (filtered): {self.non_item}",
            f"  emails no parse   : {self.no_parse}",
            f"  items w/o image   : {self.no_image}",
            f"  errors            : {self.errors}",
        ]
        if self.via_counts:
            lines.append("  parsed via:")
            for via, n in sorted(self.via_counts.items()):
                lines.append(f"    {via:24s} {n}")
        return "\n".join(lines)


def run(conn: sqlite3.Connection, source: MessageSource,
        profile: CollectionProfile, lookback_days: int, apply: bool,
        use_llm: bool) -> Summary:
    s = Summary()
    for uid, msg in source.iter_messages(lookback_days):
        s.candidates += 1
        if db.already_ingested(conn, uid) is not None:
            s.already_done += 1
            continue
        try:
            _process_one(conn, uid, msg, profile, apply, use_llm, s)
        except Exception as exc:
            s.errors += 1
            log.exception("uid=%s failed: %s", uid, exc)
            if apply:
                db.log_ingest(conn, uid, "error")
    if apply:
        conn.commit()
    return s


def _process_one(conn, uid, msg, profile: CollectionProfile, apply, use_llm,
                 s: Summary) -> None:
    ctx = parse.build_ctx(uid, msg)

    if not profile.passes_keyword_gate(ctx):
        log.debug("uid=%s from=%s skipped keyword gate", uid, ctx.from_addr)
        if apply:
            db.log_ingest(conn, uid, "skipped", "gate")
        return

    result = parse.dispatch(ctx, profile)
    items = result.items
    via = result.via

    # LLM fallback only when templates produced nothing.
    if not items and use_llm:
        llm_items = llm.parse_email(conn, ctx, profile, dry_run=not apply)
        if llm_items:
            items = llm_items
            via = "llm"

    if not items:
        s.no_parse += 1
        log.info("uid=%s NO PARSE (from=%s subj=%r)", uid, ctx.from_addr, ctx.subject[:60])
        if apply:
            db.log_ingest(conn, uid, "no_parse", via)
        return

    # De-dupe within a single email. A multi-line template can match the same
    # line in both the text/plain and text/html parts (both live in ctx.text);
    # collapse on the natural key so one shipment line yields one row.
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for item in items:
        key = (item.get("seller"), item.get("order_id"), item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped

    # Domain judgement: drop rows the profile says aren't the thing you collect.
    kept = []
    for item in items:
        if profile.is_item(item):
            kept.append(item)
        else:
            s.non_item += 1
            log.debug("uid=%s dropped non-item: %s", uid, item.get("seller"))
    items = kept
    if not items:
        if apply:
            db.log_ingest(conn, uid, "non_item", via)
        return

    s.bump_via(via)
    added_here = 0
    for item in items:
        # A refund / return-received sighting isn't a new catalog row — it's a
        # state transition ('returned') on rows an earlier email already
        # catalogued. Apply it and move on; never insert a row for it.
        if item.get("_return_event"):
            oid, nm = item.get("order_id"), item.get("name")
            if apply:
                n = db.mark_returned(conn, profile.name, oid, nm)
            else:
                n = db.count_to_mark_returned(conn, profile.name, oid, nm)
                if n:
                    print(f"  ✓ [{via}] would mark {n} item(s) returned "
                          f"(order={oid or '-'}{f', {nm!r}' if nm else ''})")
            s.returned += n
            continue
        item["profile"] = profile.name
        item["category"] = profile.classify(item)
        item = profile.enrich(item)
        # Always try to attach the email's product image (LLM items have none).
        image_url = item.get("image_url") or images.first_product_image_url(ctx.html)
        if apply:
            if db.item_exists(conn, profile.name, item.get("seller"),
                              item.get("order_id"), item.get("name")):
                # A later email about a known order line (shipment / delivery /
                # return-window) may carry lifecycle dates the original row
                # lacked — merge those instead of discarding the sighting.
                if db.enrich_item_lifecycle(conn, profile.name, item.get("seller"),
                                            item.get("order_id"), item.get("name"),
                                            item):
                    s.enriched += 1
                else:
                    s.duplicate_items += 1
                continue
            path, sha = images.download(image_url) if image_url else (None, None)
            item["image_path"], item["image_sha"] = path, sha
            if not path:
                s.no_image += 1
            new_id = db.insert_item(conn, item)
            if new_id is None:
                s.duplicate_items += 1
            else:
                s.added += 1
                added_here += 1
        else:
            # Dry run: show the row, don't download or write.
            if db.item_exists(conn, profile.name, item.get("seller"),
                              item.get("order_id"), item.get("name")):
                if any(item.get(k) for k in ("delivered_at", "return_by")):
                    s.enriched += 1
                    print(f"  ~ [{via}] would enrich {item.get('name')!r} "
                          f"(order={item.get('order_id') or '-'}) with "
                          f"delivered_at={item.get('delivered_at') or '-'} "
                          f"return_by={item.get('return_by') or '-'}")
                else:
                    s.duplicate_items += 1
                continue
            if not image_url:
                s.no_image += 1
            s.added += 1
            added_here += 1
            _print_dry(via, item, image_url)

    if apply:
        db.log_ingest(conn, uid, "added" if added_here else "skipped", via, added_here)


def _print_dry(via: str, item: dict, image_url) -> None:
    price = item.get("price")
    cur = item.get("currency") or ""
    price_str = f"{price} {cur}".strip() if price is not None else "?"
    cat = item.get("category")
    qty = int(item.get("quantity") or 1)
    qty_str = f" x{qty}" if qty != 1 else ""
    print(f"  + [{via}] {item.get('name') or '?'!r:36s}{qty_str} "
          f"maker={item.get('maker') or '-'} "
          f"price={price_str} seller={item.get('seller') or '-'} "
          f"cat={cat or '-'} "
          f"order={item.get('order_id') or '-'} "
          f"img={'yes' if image_url else 'NO'}")
