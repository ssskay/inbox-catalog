"""Offline tests for the life-zone router, the taxonomy config loader, and the
spend flags. Taxonomy + rules under test: ``references/life-zone-routing.md``.

    python3 -m unittest tests.test_zones -v
    # or:  python3 tests/test_zones.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog.profiles import life_zones as lz  # noqa: E402

# Determinism: pin the engine to the shipped generic defaults so these tests
# pass identically whether or not the developer has a local taxonomy override
# (zones.local.json) on disk. Tests assert the public, default behavior.
lz.TAXONOMY = lz._build(lz.DEFAULT_SPEC, source="test-default")
lz.ZONE_LABELS = lz.TAXONOMY.labels


def _route(name: str, qty: int = 1, is_gift=None) -> dict:
    item = {"name": name, "quantity": qty}
    if is_gift:
        item["is_gift"] = is_gift
    return lz.route(item)


class TestRouting(unittest.TestCase):
    def test_primary_buckets(self):
        cases = {
            "Craft Vinyl Roll 12x5ft Matte": "crafting",
            "Acrylic Sheets 3mm Clear 10 Pack": "crafting",
            "Poly Mailers 10x13 100 Pack": "home-org",
            "Anime Collectible Figure 4 inch": "fandom-fun",
            "Manga Volume 1 Paperback": "fandom-fun",
            "Spanish Grammar Workbook": "language-learning",
            "Creatine Monohydrate 500g": "fitness",
            "Raspberry Pi 5 8GB": "dev-hardware",
            "Ergonomic Mouse Wireless": "work-office",
            "Packing Cubes Set of 6": "trips-events",
            "Ring Light 10 inch with Stand": "content-gear",
            "Poster Tube with Strap": "academic",
        }
        for name, zone in cases.items():
            self.assertEqual(_route(name)["zone"], zone, name)

    def test_no_signal_is_unrouted_never_guessed(self):
        item = _route("Bamboo Cutting Board Medium")
        self.assertEqual(item["zone"], lz.UNROUTED)
        self.assertIsNone(item["zone_signal"])

    def test_fandom_is_not_a_dump_bucket(self):
        # A generic personal-ish item with no fandom/self-care signal must go
        # unrouted, not fandom-fun.
        self.assertEqual(_route("Ceramic Flower Vase White")["zone"], lz.UNROUTED)

    def test_gift_overrides_category(self):
        # A gifted craft kit is a gift, not crafting — the override beats keywords.
        item = _route("Craft Vinyl Starter Kit", is_gift="gift receipt")
        self.assertEqual(item["zone"], "gifts")
        self.assertIn("override", item["zone_signal"])
        self.assertEqual(item["zone_confidence"], "high")

    def test_resale_bulk_tiebreaker_for_resalable_goods(self):
        # Fandom goods in wholesale quantity -> resale inventory, category
        # kept as the secondary zone.
        item = _route("Anime Pins Wholesale Lot Assorted", qty=50)
        self.assertEqual(item["zone"], "resale")
        self.assertEqual(item["zone_secondary"], "fandom-fun")
        # Plain quantity signal (no explicit resale keyword) also flips it.
        item = _route("Anime Pin Set Figures", qty=12)
        self.assertEqual(item["zone"], "resale")

    def test_bulk_non_resalable_stays_put(self):
        # A bulk order of fitness supplies is not resale inventory.
        item = _route("Protein Bars Variety Pack", qty=24)
        self.assertEqual(item["zone"], "fitness")

    def test_pin_backs_are_crafting_supply_not_fandom(self):
        self.assertEqual(_route("Pin Backs Rubber Clutch 200pc")["zone"], "crafting")

    def test_items_route_by_subject(self):
        self.assertEqual(_route("Spanish Phrasebook Pocket Edition")["zone"],
                         "language-learning")
        self.assertEqual(_route("Ergonomic Standing Desk Converter")["zone"],
                         "work-office")

    def test_every_decision_carries_an_audit_trail(self):
        item = _route("Raspberry Pi 5 8GB")
        self.assertIsNotNone(item["zone_signal"])
        self.assertIn(item["zone_confidence"], ("high", "medium"))


class TestTaxonomyConfig(unittest.TestCase):
    def test_default_taxonomy_when_no_override_file(self):
        tax = lz.load_taxonomy(Path(tempfile.gettempdir()) / "definitely-absent-zones.json")
        self.assertEqual(tax.source, "built-in defaults")
        self.assertIn("crafting", tax.labels)
        self.assertEqual(tax.gift_zone, "gifts")
        self.assertEqual(tax.resale_zone, "resale")
        # Routing against the default taxonomy explicitly.
        item = lz.route({"name": "Craft Vinyl Roll", "quantity": 1}, taxonomy=tax)
        self.assertEqual(item["zone"], "crafting")

    def test_valid_override_replaces_the_taxonomy(self):
        spec = {
            "zones": {
                "garden": {"label": "🌱 Garden", "signals": [r"seeds?", r"trowel"]},
                "kitchen": {"label": "🍳 Kitchen", "signals": [r"skillet", r"whisk"]},
                "presents": {"label": "🎁 Presents", "signals": []},
            },
            "priority": ["garden", "kitchen"],
            "gift_zone": "presents",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(spec, fh)
            path = Path(fh.name)
        tax = lz.load_taxonomy(path)
        self.assertEqual(set(tax.signals), {"garden", "kitchen", "presents"})
        self.assertEqual(lz.route({"name": "Tomato Seeds Pack"}, taxonomy=tax)["zone"], "garden")
        # gift override targets the configured gift zone id
        gift = lz.route({"name": "Whisk", "is_gift": "gift receipt"}, taxonomy=tax)
        self.assertEqual(gift["zone"], "presents")
        # a "crafting" item is unknown to this taxonomy -> unrouted, never guessed
        self.assertEqual(lz.route({"name": "Acrylic Sheets"}, taxonomy=tax)["zone"], lz.UNROUTED)

    def test_malformed_override_fails_loudly(self):
        bad_cases = [
            "{ not json",                                     # invalid JSON
            json.dumps({"zones": {}}),                        # empty zones
            json.dumps({"zones": {"x": {"signals": []}}}),    # missing label
            json.dumps({"zones": {"x": {"label": "X", "signals": ["("]}}}),  # bad regex
            json.dumps({"zones": {"x": {"label": "X", "signals": []}},
                        "priority": ["nope"]}),               # priority names unknown zone
            json.dumps({"zones": {"x": {"label": "X", "signals": []}},
                        "gift_zone": "missing"}),             # gift zone not defined
            json.dumps({"zones": {"unrouted": {"label": "U", "signals": []}}}),  # reserved id
        ]
        for raw in bad_cases:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write(raw)
                path = Path(fh.name)
            with self.assertRaises(lz.ZonesConfigError, msg=raw):
                lz.load_taxonomy(path)


class TestSpendFlags(unittest.TestCase):
    def test_high_spend_uses_line_total(self):
        items = [
            {"name": "Cheap", "price": 10.0, "quantity": 1,
             "order_id": "A", "purchased_at": "2026-06-01"},
            {"name": "Single big", "price": 129.99, "quantity": 1,
             "order_id": "B", "purchased_at": "2026-06-02"},
            {"name": "Adds up", "price": 30.0, "quantity": 3,
             "order_id": "C", "purchased_at": "2026-06-20"},
        ]
        flags = lz.spend_flags(items, threshold=75)
        names = {i["name"] for i in flags["high_spend"]}
        self.assertEqual(names, {"Single big", "Adds up"})

    def test_order_cluster_detection(self):
        items = [{"name": f"i{n}", "price": 5.0, "quantity": 1,
                  "order_id": f"O{n}", "purchased_at": d}
                 for n, d in enumerate(["2026-06-26", "2026-06-27",
                                        "2026-06-28", "2026-05-01"])]
        flags = lz.spend_flags(items, threshold=75)
        self.assertTrue(flags["clusters"])
        start, n = flags["clusters"][0]
        self.assertEqual(n, 3)

    def test_no_flags_when_quiet(self):
        items = [{"name": "a", "price": 5.0, "quantity": 1,
                  "order_id": "O1", "purchased_at": "2026-06-01"}]
        flags = lz.spend_flags(items, threshold=75)
        self.assertFalse(flags["high_spend"])
        self.assertFalse(flags["clusters"])


class TestTriageView(unittest.TestCase):
    def test_render_groups_and_sections(self):
        from datetime import date
        items = [
            {"id": 1, "name": "Craft Vinyl Roll", "zone": "crafting",
             "return_state": "evaluate", "purchased_at": "2026-06-18",
             "delivered_at": "2026-06-24", "return_by": None,
             "price": 28.99, "quantity": 2, "order_id": "A"},
            {"id": 2, "name": "Anime Figure", "zone": "fandom-fun",
             "return_state": "keep", "purchased_at": "2026-06-26",
             "delivered_at": None, "return_by": None,
             "price": 22.99, "quantity": 1, "order_id": "B"},
            {"id": 3, "name": "Mystery Thing", "zone": "unrouted",
             "return_state": "evaluate", "purchased_at": "2026-06-28",
             "delivered_at": None, "return_by": None,
             "price": 129.99, "quantity": 1, "order_id": "C"},
        ]
        out = lz.render_triage(items, today=date(2026, 7, 1))
        self.assertIn("🔨 Crafting", out)
        self.assertIn("🌟 Fandom & Fun", out)
        self.assertIn("unrouted", out)
        self.assertIn("Spend flags", out)
        self.assertIn("Mystery Thing", out)
        self.assertIn("days left", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
