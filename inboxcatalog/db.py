"""SQLite spine for the catalog.

Tables:
  items            one row per catalogued thing (multi-item orders -> N rows)
  item_embeddings  one CLIP vector per item image
  ingest_log       one row per processed email uid (idempotency + audit)
  llm_cache        cached LLM parse per email (never re-parsed)

Dedupe key for an item: (profile, seller, order_id, name). A second sighting of
the same order line is skipped rather than duplicated. The ``profile`` column
lets one DB hold multiple collection domains without collisions.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, logutil

log = logutil.get("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile      TEXT,
    name         TEXT,
    maker        TEXT,            -- shop / brand / maker
    price        REAL,            -- per-line price as printed (NULL if absent)
    currency     TEXT,
    quantity     INTEGER DEFAULT 1,  -- units on this order line (defaults to 1)
    seller       TEXT,
    order_id     TEXT,
    purchased_at TEXT,            -- ISO-8601 date
    category     TEXT,            -- optional taxonomy label from the profile
    image_path   TEXT,
    image_sha    TEXT,
    source       TEXT,            -- template | llm | manual
    delivered_at TEXT,            -- ISO-8601 date the item arrived (from a delivery email)
    return_by    TEXT,            -- ISO-8601 last day the item can be returned
    return_state TEXT,            -- keep | return | evaluate | returned (NULL = profile has no return layer)
    zone         TEXT,            -- primary life-zone label (profile-defined taxonomy)
    zone_secondary TEXT,          -- optional secondary life-zone
    zone_signal  TEXT,            -- which signal matched (audit trail for routing)
    zone_confidence TEXT,         -- high | medium | low
    created_at   TEXT NOT NULL
);

-- Natural dedupe key. COALESCE keeps NULLs from colliding into one another.
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_natural
    ON items (
        COALESCE(profile,''),
        COALESCE(seller,''),
        COALESCE(order_id,''),
        COALESCE(name,'')
    );

CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    vector  BLOB NOT NULL,        -- float32 little-endian
    dim     INTEGER NOT NULL,
    model   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_log (
    email_uid  TEXT PRIMARY KEY,
    status     TEXT NOT NULL,     -- added | skipped | no_item | no_image | error
    parsed_via TEXT,              -- template:<name> | llm | none
    items_found INTEGER DEFAULT 0,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    email_uid    TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    model        TEXT NOT NULL,
    ts           TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    config.ensure_dirs()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


# Legacy life-zone ids (the original character-named "hamster" taxonomy) mapped
# to the generic zone ids the engine ships today. A pure value translation:
# catalogs built before the rename stored these ids in the zone columns, and
# this remaps them so an existing catalog keeps displaying under a zone the
# current taxonomy still knows. Only the two zone columns are touched.
LEGACY_ZONE_MAP = {
    "panda": "crafting",
    "pashmina": "home-org",
    "oxnard": "fandom-fun",
    "howdy": "resale",
    "dexter": "language-learning",
    "auntie-viv": "fitness",
    "hamtaro": "dev-hardware",
    "boss": "work-office",
    "cappy": "trips-events",
    "sandy": "gifts",
    "bijou": "content-gear",
    "maxwell": "academic",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive migrations for DBs created before a column existed.

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a catalog
    built before ``quantity`` shipped needs the column added in place. Existing
    rows default to 1, which is the correct historical assumption (one unit)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    if "quantity" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN quantity INTEGER DEFAULT 1")
        log.info("migrated items: added quantity column (existing rows -> 1)")
    # Return-decision + life-zone layers (all nullable; profiles that don't use
    # them leave the columns NULL, so old catalogs migrate losslessly).
    for col in ("delivered_at", "return_by", "return_state", "zone",
                "zone_secondary", "zone_signal", "zone_confidence"):
        if col not in cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
            log.info("migrated items: added %s column", col)
    # Versioned, run-once migration for the zone rename. Gated by
    # PRAGMA user_version so it fires exactly once when an old catalog is first
    # opened by the new engine — and never again. Re-running on every init would
    # clobber the zones a user's own taxonomy override legitimately writes (a
    # custom taxonomy may reuse a legacy id like "panda" as an active zone).
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        _migrate_legacy_zones(conn)
        conn.execute("PRAGMA user_version = 1")


def _migrate_legacy_zones(conn: sqlite3.Connection) -> None:
    """Rewrite legacy character-named zone ids to the generic taxonomy ids.

    Value-only: it updates ``zone``/``zone_secondary`` in place and never touches
    any other (non-NULL) lifecycle field. One UPDATE per legacy id per column,
    each guarded by an exact-match WHERE so unrelated rows are left alone. Called
    once via the ``user_version`` gate in :func:`_migrate` (safe to call directly
    in tests — it is itself idempotent)."""
    for col in ("zone", "zone_secondary"):
        for old, new in LEGACY_ZONE_MAP.items():
            cur = conn.execute(
                f"UPDATE items SET {col}=? WHERE {col}=?", (new, old))
            if cur.rowcount:
                log.info("migrated items: %s %r -> %r (%d row(s))",
                         col, old, new, cur.rowcount)


# --- items -----------------------------------------------------------------

def item_exists(conn: sqlite3.Connection, profile: Optional[str],
                seller: Optional[str], order_id: Optional[str],
                name: Optional[str]) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM items WHERE COALESCE(profile,'')=? AND COALESCE(seller,'')=? "
        "AND COALESCE(order_id,'')=? AND COALESCE(name,'')=? LIMIT 1",
        (profile or "", seller or "", order_id or "", name or ""),
    )
    return cur.fetchone() is not None


def insert_item(conn: sqlite3.Connection, item: dict) -> Optional[int]:
    """Insert one item. Returns new id, or None if it was a duplicate."""
    try:
        cur = conn.execute(
            """INSERT INTO items
               (profile, name, maker, price, currency, quantity, seller, order_id,
                purchased_at, category, image_path, image_sha, source,
                delivered_at, return_by, return_state,
                zone, zone_secondary, zone_signal, zone_confidence, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item.get("profile"), item.get("name"), item.get("maker"),
                item.get("price"), item.get("currency"),
                int(item.get("quantity") or 1), item.get("seller"),
                item.get("order_id"), item.get("purchased_at"),
                item.get("category"), item.get("image_path"),
                item.get("image_sha"), item.get("source", "template"),
                item.get("delivered_at"), item.get("return_by"),
                item.get("return_state"),
                item.get("zone"), item.get("zone_secondary"),
                item.get("zone_signal"), item.get("zone_confidence"), _now(),
            ),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        log.debug("duplicate item skipped: %s / %s / %s",
                  item.get("seller"), item.get("order_id"), item.get("name"))
        return None


def all_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM items ORDER BY id"))


def items_for_profile(conn: sqlite3.Connection, profile: str,
                      since: Optional[str] = None) -> list[sqlite3.Row]:
    """All catalogued rows for a profile. ``since`` (ISO date, e.g.
    ``2026-04-01``) keeps only items ordered on/after that day; rows with no
    ``purchased_at`` are kept (a shipment-only sighting shouldn't vanish from a
    window just because its confirmation email wasn't parsed)."""
    if since:
        # Window by the best available date: order date, else delivery date.
        # A row with neither is undateable and is excluded from a dated window
        # (keeping it would silently leak old/ambiguous items into "last N mo").
        return list(conn.execute(
            "SELECT * FROM items WHERE COALESCE(profile,'')=? "
            "AND COALESCE(purchased_at, delivered_at) >= ? "
            "ORDER BY COALESCE(purchased_at, delivered_at)",
            (profile or "", since)))
    return list(conn.execute(
        "SELECT * FROM items WHERE COALESCE(profile,'')=? ORDER BY id",
        (profile or "",)))


def enrich_item_lifecycle(conn: sqlite3.Connection, profile: Optional[str],
                          seller: Optional[str], order_id: Optional[str],
                          name: Optional[str], item: dict) -> bool:
    """Fill lifecycle fields on an existing row from a later email sighting.

    A shipment/delivery/return-window email re-mentions an order line we already
    catalogued; instead of discarding it as a duplicate, copy any lifecycle data
    it carries (delivered_at, return_by) onto the row — but only into NULL
    columns, so an explicit earlier value is never clobbered. Returns True if a
    row was updated."""
    updates = {k: item[k] for k in ("delivered_at", "return_by")
               if item.get(k)}
    if not updates:
        return False
    sets = ", ".join(f"{k}=COALESCE({k}, ?)" for k in updates)
    cur = conn.execute(
        f"""UPDATE items SET {sets}
            WHERE COALESCE(profile,'')=? AND COALESCE(seller,'')=?
              AND COALESCE(order_id,'')=? AND COALESCE(name,'')=?
              AND ({' OR '.join(f'{k} IS NULL' for k in updates)})""",
        (*updates.values(), profile or "", seller or "", order_id or "", name or ""),
    )
    if cur.rowcount:
        log.info("lifecycle enrich: %r (order %s) <- %s",
                 name, order_id, updates)
    return bool(cur.rowcount)


def set_return_state(conn: sqlite3.Connection, item_id: int, state: str) -> None:
    row = conn.execute(
        "SELECT name, order_id, return_state FROM items WHERE id=?",
        (item_id,)).fetchone()
    conn.execute("UPDATE items SET return_state=? WHERE id=?", (state, item_id))
    if row:
        log.info("state transition: item #%d %r (order %s): %s -> %s",
                 item_id, row["name"], row["order_id"],
                 row["return_state"] or "(none)", state)


def mark_returned(conn: sqlite3.Connection, profile: Optional[str],
                  order_id: Optional[str], name: Optional[str] = None) -> int:
    """Transition catalogued rows to ``returned`` from a refund / return-received
    sighting. Matches by (profile, order_id) and, when ``name`` is given, that
    item too; ``name=None`` marks the whole order. Only flips rows that aren't
    already ``returned`` and touches **only** the ``return_state`` column — never
    any other (non-NULL) lifecycle field. Returns the number of rows transitioned.

    This is the automated equivalent of ``--mark <order> returned``; a deliberate
    state transition, not an accidental clobber."""
    if not order_id:
        return 0
    clauses = ["COALESCE(profile,'')=?", "COALESCE(order_id,'')=?",
               "COALESCE(return_state,'')!='returned'"]
    params: list = [profile or "", order_id]
    if name:
        clauses.append("COALESCE(name,'')=?")
        params.append(name)
    where = " AND ".join(clauses)
    cur = conn.execute(f"UPDATE items SET return_state='returned' WHERE {where}", params)
    if cur.rowcount:
        log.info("refund: marked %d row(s) returned (order %s%s)", cur.rowcount,
                 order_id, f", name={name!r}" if name else "")
    return cur.rowcount


def count_to_mark_returned(conn: sqlite3.Connection, profile: Optional[str],
                           order_id: Optional[str], name: Optional[str] = None) -> int:
    """How many rows :func:`mark_returned` would transition (for dry-run reporting)."""
    if not order_id:
        return 0
    clauses = ["COALESCE(profile,'')=?", "COALESCE(order_id,'')=?",
               "COALESCE(return_state,'')!='returned'"]
    params: list = [profile or "", order_id]
    if name:
        clauses.append("COALESCE(name,'')=?")
        params.append(name)
    where = " AND ".join(clauses)
    return conn.execute(f"SELECT COUNT(*) FROM items WHERE {where}", params).fetchone()[0]


def find_items(conn: sqlite3.Connection, profile: str, key: str) -> list[sqlite3.Row]:
    """Resolve a user-supplied key to items: exact id, exact order_id, or a
    case-insensitive name substring — in that order of preference."""
    if key.isdigit():
        rows = list(conn.execute(
            "SELECT * FROM items WHERE id=? AND COALESCE(profile,'')=?",
            (int(key), profile or "")))
        if rows:
            return rows
    rows = list(conn.execute(
        "SELECT * FROM items WHERE order_id=? AND COALESCE(profile,'')=? ORDER BY id",
        (key, profile or "")))
    if rows:
        return rows
    return list(conn.execute(
        "SELECT * FROM items WHERE name LIKE ? AND COALESCE(profile,'')=? ORDER BY id",
        (f"%{key}%", profile or "")))


def items_with_image_missing_embedding(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT i.* FROM items i
           LEFT JOIN item_embeddings e ON e.item_id = i.id
           WHERE i.image_path IS NOT NULL AND e.item_id IS NULL
           ORDER BY i.id"""
    ))


# --- embeddings ------------------------------------------------------------

def upsert_embedding(conn: sqlite3.Connection, item_id: int, vector_bytes: bytes,
                     dim: int, model: str) -> None:
    conn.execute(
        """INSERT INTO item_embeddings (item_id, vector, dim, model, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(item_id) DO UPDATE SET
             vector=excluded.vector, dim=excluded.dim,
             model=excluded.model, created_at=excluded.created_at""",
        (item_id, vector_bytes, dim, model, _now()),
    )


def all_embeddings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT e.item_id, e.vector, e.dim, e.model, i.*
           FROM item_embeddings e JOIN items i ON i.id = e.item_id
           ORDER BY e.item_id"""
    ))


# --- ingest log ------------------------------------------------------------

def already_ingested(conn: sqlite3.Connection, uid: str) -> Optional[str]:
    row = conn.execute(
        "SELECT status FROM ingest_log WHERE email_uid=?", (uid,)
    ).fetchone()
    return row["status"] if row else None


def log_ingest(conn: sqlite3.Connection, uid: str, status: str,
               parsed_via: str = "", items_found: int = 0) -> None:
    conn.execute(
        """INSERT INTO ingest_log (email_uid, status, parsed_via, items_found, ts)
           VALUES (?,?,?,?,?)
           ON CONFLICT(email_uid) DO UPDATE SET
             status=excluded.status, parsed_via=excluded.parsed_via,
             items_found=excluded.items_found, ts=excluded.ts""",
        (uid, status, parsed_via, items_found, _now()),
    )


# --- llm cache -------------------------------------------------------------

def get_llm_cache(conn: sqlite3.Connection, uid: str) -> Optional[str]:
    row = conn.execute(
        "SELECT response_json FROM llm_cache WHERE email_uid=?", (uid,)
    ).fetchone()
    return row["response_json"] if row else None


def put_llm_cache(conn: sqlite3.Connection, uid: str, response_json: str,
                  model: str) -> None:
    conn.execute(
        """INSERT INTO llm_cache (email_uid, response_json, model, ts)
           VALUES (?,?,?,?)
           ON CONFLICT(email_uid) DO UPDATE SET
             response_json=excluded.response_json, model=excluded.model, ts=excluded.ts""",
        (uid, response_json, model, _now()),
    )


def counts(conn: sqlite3.Connection) -> dict:
    def one(q: str) -> int:
        return conn.execute(q).fetchone()[0]
    return {
        "items": one("SELECT COUNT(*) FROM items"),
        "embeddings": one("SELECT COUNT(*) FROM item_embeddings"),
        "emails_logged": one("SELECT COUNT(*) FROM ingest_log"),
    }
