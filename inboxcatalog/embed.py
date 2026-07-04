"""Local CLIP image embeddings. No API cost — runs on a local model (CPU ok).

Heavy deps (sentence-transformers / torch / Pillow) are imported lazily so the
email-ingest path works before they're installed. Vectors are stored as raw
float32 bytes in item_embeddings, normalized to unit length so similarity is a
plain dot product.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import numpy as np

from . import config, db, logutil

log = logutil.get("embed")

_MODEL = None  # cached SentenceTransformer


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer  # lazy/heavy
    except ImportError as exc:
        raise RuntimeError(
            "CLIP deps missing. Install:\n"
            "  pip3 install sentence-transformers torch Pillow"
        ) from exc
    log.info("loading CLIP model %s (first run downloads weights)", config.CLIP_MODEL)
    _MODEL = SentenceTransformer(config.CLIP_MODEL)
    return _MODEL


def embed_image_file(path: str) -> Optional[np.ndarray]:
    """Return a unit-normalized float32 embedding for an image file, or None."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow missing: pip3 install Pillow") from exc
    try:
        img = Image.open(path).convert("RGB")
    except Exception as exc:
        log.warning("cannot open image %s: %s", path, exc)
        return None
    model = _load_model()
    # CLIP SentenceTransformers accept a list of PIL images at runtime, but the
    # stub types `encode(sentences)` as text-only — ignore just that mismatch.
    vec = model.encode([img], convert_to_numpy=True, normalize_embeddings=True)[0]  # pyright: ignore[reportCallIssue, reportArgumentType]
    return vec.astype(np.float32)


def to_bytes(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def reindex(conn: sqlite3.Connection, force: bool = False) -> dict:
    """Embed every item image that lacks an embedding (or all, if force).
    Returns {embedded, skipped, failed}."""
    if force:
        rows = [r for r in db.all_items(conn) if r["image_path"]]
    else:
        rows = db.items_with_image_missing_embedding(conn)
    stats = {"embedded": 0, "skipped": 0, "failed": 0}
    if not rows:
        log.info("nothing to embed (all images already indexed)")
        return stats
    log.info("embedding %d image(s)%s", len(rows), " [force]" if force else "")
    for r in rows:
        path = r["image_path"]
        if not path:
            stats["skipped"] += 1
            continue
        vec = embed_image_file(path)
        if vec is None:
            stats["failed"] += 1
            continue
        db.upsert_embedding(conn, r["id"], to_bytes(vec), int(vec.shape[0]),
                            config.CLIP_MODEL)
        stats["embedded"] += 1
    conn.commit()
    log.info("reindex done: %s", stats)
    return stats
