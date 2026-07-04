"""Offline tests for the Amazon profile: template extraction, lifecycle
enrichment (delivery / return-by dates merged onto existing rows), the
return-window clock, state transitions, and the fixtures path end to end.

No network, no mailbox:

    python3 -m unittest tests.test_amazon -v
    # or:  python3 tests/test_amazon.py
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog import db, ingest, returns  # noqa: E402
from inboxcatalog.parse import EmailCtx  # noqa: E402
from inboxcatalog.profiles import load as load_profile  # noqa: E402
from inboxcatalog.profiles import life_zones as _lz  # noqa: E402

# Determinism: pin the shipped generic taxonomy so the end-to-end zone
# assertions hold regardless of any local zones.local.json override on disk.
_lz.TAXONOMY = _lz._build(_lz.DEFAULT_SPEC, source="test-default")
from inboxcatalog.profiles.amazon import (AmazonOrderTemplate,  # noqa: E402
                                          AmazonRefundTemplate,
                                          AmazonReturnWindowTemplate,
                                          AmazonShipmentTemplate)
from inboxcatalog.sources import FixtureSource, default_fixture_dir  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "inboxcatalog" / "profiles" / "fixtures_amazon"
TODAY = date(2026, 7, 1)


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init(conn)
    return conn


def _ctx(from_addr="auto-confirm@amazon.com", subject="Your Amazon.com order #113-1111111-2222222",
         text="", html="", date_iso="2026-06-20") -> EmailCtx:
    return EmailCtx(uid="t", from_addr=from_addr, subject=subject,
                    date_iso=date_iso, text=text, html=html)


class TestAmazonTemplates(unittest.TestCase):
    def test_order_confirmation_extracts_line_items(self):
        body = ("Order Confirmation\n"
                "Order #113-1111111-2222222\n"
                "2 x Craft Vinyl Roll 12x5ft - $11.99 each\n"
                "1 x Spanish Grammar Workbook Third Edition - $52.80\n"
                "Order Total: $76.78\n")
        rows = AmazonOrderTemplate().parse(_ctx(text=body))
        self.assertEqual(len(rows), 2)
        by_name = {r["name"]: r for r in rows}
        vinyl = by_name["Craft Vinyl Roll 12x5ft"]
        self.assertEqual(vinyl["quantity"], 2)
        self.assertEqual(vinyl["price"], 11.99)
        self.assertEqual(vinyl["currency"], "USD")
        self.assertEqual(vinyl["order_id"], "113-1111111-2222222")
        self.assertEqual(vinyl["purchased_at"], "2026-06-20")
        book = by_name["Spanish Grammar Workbook Third Edition"]
        self.assertEqual(book["quantity"], 1)
        self.assertEqual(book["price"], 52.80)

    def test_order_confirmation_detects_gift(self):
        body = ("Order #113-1111111-2222222\n"
                "A gift receipt was included with this order.\n"
                "1 x Baby Bath Toy Set - $19.99\n")
        rows = AmazonOrderTemplate().parse(_ctx(text=body))
        self.assertTrue(rows[0].get("is_gift"))

    def test_delivered_email_carries_delivery_date(self):
        body = ("Order #113-1111111-2222222\n"
                "Delivered on June 24, 2026. It was handed to a resident.\n"
                "- Craft Vinyl Roll 12x5ft\n")
        tpl = AmazonShipmentTemplate()
        ctx = _ctx(subject="Delivered: your Amazon.com order", text=body)
        self.assertTrue(tpl.matches(ctx))
        rows = tpl.parse(ctx)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delivered_at"], "2026-06-24")
        self.assertIsNone(rows[0]["purchased_at"])  # not the purchase date

    def test_return_window_email_carries_return_by(self):
        body = ("Order #113-1111111-2222222\n"
                "The following item is return eligible through July 8, 2026:\n"
                "- Craft Vinyl Roll 12x5ft\n")
        tpl = AmazonReturnWindowTemplate()
        ctx = _ctx(subject="Your return window closes soon", text=body)
        self.assertTrue(tpl.matches(ctx))
        rows = tpl.parse(ctx)
        self.assertEqual(rows[0]["return_by"], "2026-07-08")

    def test_marketing_noise_fails_the_gate(self):
        profile = load_profile("amazon")
        ctx = _ctx(from_addr="marketing@amazon.com",
                   subject="Deals for you: 25% off home & kitchen",
                   text="Save 25% on storage bins and more. Shop your order history.")
        self.assertFalse(profile.passes_keyword_gate(ctx))

    def test_shipment_subject_wins_over_order_template(self):
        # 'Shipped:' subjects also contain the word 'order' — the shipment
        # template must claim them so bare re-sightings don't become new rows
        # with bogus purchase dates.
        profile = load_profile("amazon")
        ctx = _ctx(subject="Shipped: your Amazon.com order #113-1111111-2222222",
                   text="- Craft Vinyl Roll 12x5ft\nOrder #113-1111111-2222222")
        first = next(t for t in profile.templates if t.matches(ctx))
        self.assertEqual(first.name, "amazon_shipment")


class TestReturnWindow(unittest.TestCase):
    def test_explicit_return_by_wins(self):
        win = returns.window_for({"name": "x", "return_by": "2026-07-08",
                                  "delivered_at": "2026-06-01",
                                  "purchased_at": "2026-05-01"}, TODAY)
        self.assertEqual(win.basis, "explicit")
        self.assertEqual(win.days_left, 7)
        self.assertTrue(win.returnable)

    def test_delivered_plus_policy(self):
        win = returns.window_for({"name": "x", "return_by": None,
                                  "delivered_at": "2026-06-24",
                                  "purchased_at": "2026-06-18"}, TODAY,
                                 policy_days=30)
        self.assertEqual(win.basis, "delivered+policy")
        self.assertEqual(win.return_by, date(2026, 7, 24))
        self.assertEqual(win.days_left, 23)

    def test_order_date_fallback(self):
        win = returns.window_for({"name": "x", "purchased_at": "2026-06-26"},
                                 TODAY, policy_days=30)
        self.assertEqual(win.basis, "ordered+policy")
        self.assertEqual(win.days_left, 25)

    def test_expired(self):
        win = returns.window_for({"name": "x", "return_by": "2026-06-15"}, TODAY)
        self.assertEqual(win.status, "expired")
        self.assertEqual(win.days_left, -16)
        self.assertFalse(win.returnable)

    def test_no_dates_is_unknown(self):
        win = returns.window_for({"name": "x"}, TODAY)
        self.assertEqual(win.status, "unknown")
        self.assertIsNone(win.days_left)

    def test_report_orders_by_urgency_and_flags_evaluate(self):
        items = [
            {"id": 1, "name": "Late", "return_state": "evaluate",
             "purchased_at": "2026-06-28", "price": 5.0, "currency": "USD",
             "quantity": 1, "return_by": None, "delivered_at": None},
            {"id": 2, "name": "Urgent", "return_state": "keep",
             "purchased_at": None, "return_by": "2026-07-03",
             "delivered_at": None, "price": 9.0, "currency": "USD", "quantity": 1},
            {"id": 3, "name": "Gone", "return_state": "returned",
             "purchased_at": "2026-01-01", "return_by": None,
             "delivered_at": None, "price": 1.0, "currency": "USD", "quantity": 1},
            {"id": 4, "name": "Old", "return_state": "evaluate",
             "purchased_at": "2026-04-01", "return_by": None,
             "delivered_at": None, "price": 2.0, "currency": "USD", "quantity": 1},
        ]
        out = returns.render_returns(items, TODAY)
        self.assertLess(out.index("Urgent"), out.index("Late"))   # most urgent first
        self.assertIn("EVALUATE", out)                            # flagged
        self.assertIn("Expired (1)", out)                         # Old separated out
        self.assertIn("Already returned (1)", out)                # Gone separated out


class TestLifecycleEnrichment(unittest.TestCase):
    def test_later_sighting_fills_dates_without_clobbering(self):
        conn = _mem()
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Widget",
                              "purchased_at": "2026-06-18"})
        changed = db.enrich_item_lifecycle(
            conn, "amazon", "amazon.com", "O1", "Widget",
            {"delivered_at": "2026-06-24", "return_by": None})
        self.assertTrue(changed)
        row = conn.execute("SELECT * FROM items").fetchone()
        self.assertEqual(row["delivered_at"], "2026-06-24")
        # a second, different delivery date must NOT overwrite the first
        db.enrich_item_lifecycle(conn, "amazon", "amazon.com", "O1", "Widget",
                                 {"delivered_at": "2026-06-30"})
        row = conn.execute("SELECT * FROM items").fetchone()
        self.assertEqual(row["delivered_at"], "2026-06-24")

    def test_sighting_with_no_dates_is_not_an_enrichment(self):
        conn = _mem()
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Widget"})
        self.assertFalse(db.enrich_item_lifecycle(
            conn, "amazon", "amazon.com", "O1", "Widget", {}))


class _OneMessageSource:
    """Minimal MessageSource yielding a single raw email (for ingest tests)."""
    def __init__(self, raw: str):
        self._raw = raw

    def iter_messages(self, lookback_days):
        import email as _email
        yield ("refund-1", _email.message_from_string(self._raw))


class TestAmazonRefund(unittest.TestCase):
    def test_refund_template_matches_and_targets_the_order(self):
        tpl = AmazonRefundTemplate()
        ctx = _ctx(from_addr="return@amazon.com",
                   subject="Refund issued for your Amazon.com order #113-1111111-2222222",
                   text="We've issued a refund of $28.99 for order #113-1111111-2222222.")
        self.assertTrue(tpl.matches(ctx))
        rows = tpl.parse(ctx)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "113-1111111-2222222")
        self.assertEqual(rows[0]["_return_event"], "returned")
        self.assertIsNone(rows[0]["name"])          # order-level

    def test_refund_passes_the_gate_and_wins_dispatch(self):
        # Refund mail used to be blocked by the subject blocklist; now it must
        # pass the gate AND be claimed by the refund template (not shipment/order).
        profile = load_profile("amazon")
        ctx = _ctx(from_addr="return@amazon.com",
                   subject="Refund issued for your Amazon.com order #113-1111111-2222222",
                   text="Your refund is on the way. Order #113-1111111-2222222")
        self.assertTrue(profile.passes_keyword_gate(ctx))
        first = next(t for t in profile.templates if t.matches(ctx))
        self.assertEqual(first.name, "amazon_refund")

    def test_mark_returned_scopes_to_order_idempotent_no_clobber(self):
        conn = _mem()
        for nm in ("A", "B"):
            db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                                  "order_id": "O1", "name": nm,
                                  "return_state": "evaluate",
                                  "delivered_at": "2026-06-01"})
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O2", "name": "C",
                              "return_state": "evaluate"})
        self.assertEqual(db.mark_returned(conn, "amazon", "O1"), 2)
        self.assertEqual(db.mark_returned(conn, "amazon", "O1"), 0)   # idempotent
        rows = {r["name"]: r for r in conn.execute("SELECT * FROM items")}
        self.assertEqual(rows["A"]["return_state"], "returned")
        self.assertEqual(rows["B"]["return_state"], "returned")
        self.assertEqual(rows["C"]["return_state"], "evaluate")       # other order safe
        self.assertEqual(rows["A"]["delivered_at"], "2026-06-01")     # no clobber

    def test_end_to_end_refund_marks_order_returned(self):
        conn = _mem()
        profile = load_profile("amazon")
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "114-8812733-1054420", "name": "Widget",
                              "return_state": "evaluate"})
        raw = ("From: return@amazon.com\r\n"
               "Subject: Refund issued for your Amazon.com order #114-8812733-1054420\r\n"
               "Date: Wed, 02 Jul 2026 09:00:00 -0400\r\n\r\n"
               "We've issued your refund for order #114-8812733-1054420.\r\n")
        with mock.patch("inboxcatalog.images.download", return_value=(None, None)):
            s = ingest.run(conn, _OneMessageSource(raw), profile,
                           lookback_days=365, apply=True, use_llm=False)
        self.assertEqual(s.returned, 1)
        self.assertEqual(s.added, 0)
        row = conn.execute("SELECT return_state FROM items").fetchone()
        self.assertEqual(row["return_state"], "returned")


class TestZoneMigration(unittest.TestCase):
    def test_legacy_zone_ids_remap_to_generic(self):
        conn = _mem()
        # Seed rows carrying the old character-named zone ids, then re-run the
        # (idempotent) migration to remap them.
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Blanks",
                              "zone": "panda", "zone_secondary": "howdy"})
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O2", "name": "Plush",
                              "zone": "oxnard", "zone_secondary": None,
                              "return_state": "keep"})
        db._migrate_legacy_zones(conn)
        rows = {r["name"]: r for r in conn.execute("SELECT * FROM items")}
        self.assertEqual(rows["Blanks"]["zone"], "crafting")
        self.assertEqual(rows["Blanks"]["zone_secondary"], "resale")
        self.assertEqual(rows["Plush"]["zone"], "fandom-fun")
        # value-only: other lifecycle fields are untouched
        self.assertEqual(rows["Plush"]["return_state"], "keep")

    def test_migration_is_idempotent_and_leaves_generic_ids_alone(self):
        conn = _mem()
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Keeb", "zone": "work-office"})
        db._migrate_legacy_zones(conn)   # already generic — no change
        db._migrate_legacy_zones(conn)   # second pass — still no change
        row = conn.execute("SELECT zone FROM items").fetchone()
        self.assertEqual(row["zone"], "work-office")

    def test_migration_runs_once_and_never_clobbers_later_writes(self):
        # Simulate an old catalog (user_version 0) holding legacy zone ids.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        conn.execute("PRAGMA user_version = 0")
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Blanks", "zone": "panda"})
        db._migrate(conn)                # first open -> remap + bump version
        self.assertEqual(conn.execute("SELECT zone FROM items").fetchone()["zone"],
                         "crafting")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        # A later run under a taxonomy override legitimately writes 'panda' again.
        conn.execute("UPDATE items SET zone='panda'")
        db._migrate(conn)                # version already 1 -> must NOT clobber
        self.assertEqual(conn.execute("SELECT zone FROM items").fetchone()["zone"],
                         "panda")


class TestStateTransitions(unittest.TestCase):
    def test_mark_by_id_order_and_name(self):
        conn = _mem()
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Alpha Widget",
                              "return_state": "evaluate"})
        db.insert_item(conn, {"profile": "amazon", "seller": "amazon.com",
                              "order_id": "O1", "name": "Beta Widget",
                              "return_state": "evaluate"})
        by_order = db.find_items(conn, "amazon", "O1")
        self.assertEqual(len(by_order), 2)
        by_name = db.find_items(conn, "amazon", "Alpha")
        self.assertEqual(len(by_name), 1)
        db.set_return_state(conn, by_name[0]["id"], "keep")
        row = conn.execute("SELECT return_state FROM items WHERE name='Alpha Widget'").fetchone()
        self.assertEqual(row["return_state"], "keep")
        by_id = db.find_items(conn, "amazon", str(by_order[1]["id"]))
        self.assertEqual(by_id[0]["name"], "Beta Widget")


class TestFixturesEndToEnd(unittest.TestCase):
    def _run(self, conn):
        profile = load_profile("amazon")
        source = FixtureSource(FIXTURES)
        with mock.patch("inboxcatalog.images.download", return_value=(None, None)):
            return ingest.run(conn, source, profile, lookback_days=365,
                              apply=True, use_llm=False)

    def test_full_pipeline(self):
        conn = _mem()
        s = self._run(conn)
        self.assertEqual(s.candidates, 11)
        self.assertEqual(s.added, 9)       # 9 distinct items across 7 orders
        self.assertEqual(s.enriched, 3)    # 2 delivered dates + 1 return-by
        self.assertEqual(s.errors, 0)

        rows = {r["name"]: r for r in db.items_for_profile(conn, "amazon")}
        self.assertEqual(len(rows), 9)

        # lifecycle: the delivered email filled delivered_at on order 113 items
        vinyl = rows["Craft Vinyl Roll 12x5ft Matte"]
        self.assertEqual(vinyl["delivered_at"], "2026-06-24")
        self.assertEqual(vinyl["quantity"], 2)
        self.assertEqual(vinyl["price"], 12.99)
        # return-window email filled return_by on the fandom figure
        figure = rows["Anime Collectible Figure 4 inch"]
        self.assertEqual(figure["return_by"], "2026-07-08")
        # every item defaults to `evaluate`
        self.assertTrue(all(r["return_state"] == "evaluate" for r in rows.values()))

        # zone routing (taxonomy: references/life-zone-routing.md)
        self.assertEqual(vinyl["zone"], "crafting")
        self.assertEqual(figure["zone"], "fandom-fun")
        gift = rows["Baby Milestone Blanket and Sticker Set"]
        self.assertEqual(gift["zone"], "gifts")          # gift overrides category
        self.assertIn("gift", gift["zone_signal"])
        self.assertEqual(rows["Bamboo Cutting Board Medium"]["zone"], "unrouted")
        self.assertEqual(rows["Mechanical Keyboard 75 Percent Hot-Swappable RGB"]["zone"], "work-office")
        self.assertEqual(rows["Sticker Sheets Bulk Lot of 100 Assorted"]["zone"], "resale")

        # idempotent: a second run adds nothing
        s2 = self._run(conn)
        self.assertEqual(s2.added, 0)
        self.assertEqual(s2.already_done, 11)

    def test_default_fixture_dir_is_profile_aware(self):
        self.assertEqual(default_fixture_dir("amazon").name, "fixtures_amazon")
        self.assertEqual(default_fixture_dir("demo").name, "fixtures")
        self.assertEqual(default_fixture_dir("nope").name, "fixtures")


if __name__ == "__main__":
    unittest.main(verbosity=2)
