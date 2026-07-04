"""Offline tests for the Takeout .mbox source (Tier-2, zero-credential path).

No network, no credentials, standard library only:

    python3 -m unittest tests.test_mbox -v
    # or:  python3 tests/test_mbox.py
"""
from __future__ import annotations

import mailbox
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog.sources import MboxSource  # noqa: E402


def _make_mbox(dirpath: Path, messages: list[str]) -> Path:
    """Write a real .mbox (via stdlib mailbox) containing the given raw emails."""
    path = dirpath / "export.mbox"
    box = mailbox.mbox(str(path))
    box.lock()
    try:
        for raw in messages:
            box.add(mailbox.mboxMessage(raw))
    finally:
        box.flush()
        box.unlock()
    return path


_MSG_1 = (
    "From: Tabletop Trove <orders@tabletoptrove.example>\n"
    "To: collector@example.com\n"
    "Subject: Order confirmation: Wingspan\n"
    "Message-ID: <order-1@tabletoptrove.example>\n"
    "Date: Mon, 03 Feb 2026 10:14:00 -0500\n"
    "\n"
    "Thanks for your order! Wingspan $60.00\n"
)
_MSG_2_NO_ID = (
    "From: Meeple Market <ship@meeplemarket.example>\n"
    "To: collector@example.com\n"
    "Subject: Your shipment is on the way\n"
    "Date: Tue, 04 Feb 2026 09:00:00 -0500\n"
    "\n"
    "Azul has shipped.\n"
)


class MboxSourceTests(unittest.TestCase):
    def test_yields_all_messages_with_expected_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = _make_mbox(Path(d), [_MSG_1, _MSG_2_NO_ID])
            pairs = list(MboxSource(path).iter_messages(lookback_days=365))

        self.assertEqual(len(pairs), 2)
        (uid1, msg1), (uid2, msg2) = pairs

        # uid comes from Message-ID when present ...
        self.assertEqual(uid1, "<order-1@tabletoptrove.example>")
        # ... and falls back to a positional id when absent.
        self.assertTrue(uid2.startswith("mbox-export-"), uid2)

        # Headers survive the mbox round-trip intact.
        self.assertIn("tabletoptrove.example", msg1.get("From"))
        self.assertEqual(msg1.get("Subject"), "Order confirmation: Wingspan")
        self.assertEqual(msg2.get("Subject"), "Your shipment is on the way")
        # Body content preserved (the 'From ' envelope line is stripped).
        self.assertIn("Wingspan", msg1.get_payload())

    def test_uids_are_unique(self):
        with tempfile.TemporaryDirectory() as d:
            path = _make_mbox(Path(d), [_MSG_1, _MSG_2_NO_ID])
            uids = [uid for uid, _ in MboxSource(path).iter_messages(365)]
        self.assertEqual(len(uids), len(set(uids)))

    def test_missing_file_exits_cleanly(self):
        with self.assertRaises(SystemExit):
            list(MboxSource(Path("/nonexistent/nope.mbox")).iter_messages(365))


if __name__ == "__main__":
    unittest.main(verbosity=2)
