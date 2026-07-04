"""Look an item up by photo.

Embed the query image with the same local CLIP model, compare against every
stored item vector (cosine == dot product, since vectors are unit-normalized),
and return the top-K nearest items with price/maker/context/confidence. $0 —
fully local, no network, no API.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import db, embed, logutil

log = logutil.get("lookup")


@dataclass
class Match:
    item_id: int
    score: float          # cosine similarity, 0..1
    confidence: str       # high | medium | low
    name: Optional[str]
    maker: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    seller: Optional[str]
    purchased_at: Optional[str]
    image_path: Optional[str]


def _confidence(score: float) -> str:
    # Thresholds are model-dependent. Defaults are tuned for clip-ViT-B-32, whose
    # cosine scores for true matches typically land ~0.80-0.95 while unrelated
    # look-alikes rarely clear ~0.80. Re-tune if INBOX_CLIP_MODEL changes
    # (e.g. ViT-L-14 runs ~0.10-0.15 lower across the board).
    if score >= 0.85:
        return "high"
    if score >= 0.75:
        return "medium"
    return "low"


def lookup_by_image(conn: sqlite3.Connection, image_path: str, top_k: int = 5
                    ) -> list[Match]:
    """Return up to top_k nearest catalogued items for the query photo."""
    qvec = embed.embed_image_file(image_path)
    if qvec is None:
        raise ValueError(f"could not read/embed query image: {image_path}")

    rows = db.all_embeddings(conn)
    if not rows:
        log.warning("no embeddings in the index yet — run --reindex after ingest")
        return []

    mat = np.vstack([embed.from_bytes(r["vector"]) for r in rows])
    sims = mat @ qvec  # both unit-normalized -> cosine similarity
    order = np.argsort(-sims)[:top_k]

    matches: list[Match] = []
    for idx in order:
        r = rows[int(idx)]
        score = float(sims[int(idx)])
        matches.append(Match(
            item_id=r["item_id"], score=score, confidence=_confidence(score),
            name=r["name"], maker=r["maker"], price=r["price"],
            currency=r["currency"], seller=r["seller"],
            purchased_at=r["purchased_at"], image_path=r["image_path"],
        ))
    return matches
