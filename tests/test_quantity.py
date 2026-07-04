"""Offline tests for the `quantity` field end to end (DB + migration + template).

No network, no Google libs. Runs on the standard library:

    python3 -m unittest tests.test_quantity -v
    # or:  python3 tests/test_quantity.py
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog import db  # noqa: E402


class TestQuantityColumn(unittest.TestCase):
    def _mem(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_init_creates_quantity_column(self):
        conn = self._mem()
        db.init(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        self.assertIn("quantity", cols)

    def test_insert_roundtrip(self):
        conn = self._mem()
        db.init(conn)
        db.insert_item(conn, {"name": "Widget", "seller": "Shop",
                              "order_id": "O1", "quantity": 3})
        row = conn.execute("SELECT quantity FROM items WHERE name='Widget'").fetchone()
        self.assertEqual(row["quantity"], 3)

    def test_default_quantity_is_one(self):
        conn = self._mem()
        db.init(conn)
        db.insert_item(conn, {"name": "NoQty", "seller": "Shop", "order_id": "O2"})
        row = conn.execute("SELECT quantity FROM items WHERE name='NoQty'").fetchone()
        self.assertEqual(row["quantity"], 1)

    def test_migration_adds_column_to_old_db(self):
        # Simulate a catalog created before `quantity` existed: an items table
        # with no quantity column. The migration must add it without data loss.
        conn = self._mem()
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, "
                     "seller TEXT, order_id TEXT)")
        conn.execute("INSERT INTO items (name) VALUES ('legacy')")
        conn.commit()
        db._migrate(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        self.assertIn("quantity", cols)
        row = conn.execute("SELECT name, quantity FROM items").fetchone()
        self.assertEqual(row["name"], "legacy")
        self.assertEqual(row["quantity"], 1)  # existing rows default to 1
        # idempotent: a second run is a no-op, not an error
        db._migrate(conn)


class TestTemplateQuantity(unittest.TestCase):
    def test_meeple_template_captures_quantity(self):
        from inboxcatalog.profiles.demo import MeepleMarketShipmentTemplate
        from inboxcatalog.parse import EmailCtx

        body = ("Order #MM-9001\n\n"
                "2x Carcassonne - $32.00\n"
                "1x Dixit - $30.00\n")
        ctx = EmailCtx(uid="x", from_addr="ship@meeplemarket.example",
                       subject="Your games shipped", text=body, html="",
                       date_iso="2026-03-14")
        rows = MeepleMarketShipmentTemplate().parse(ctx)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Carcassonne"]["quantity"], 2)
        self.assertEqual(by_name["Carcassonne"]["price"], 32.00)
        self.assertEqual(by_name["Dixit"]["quantity"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
