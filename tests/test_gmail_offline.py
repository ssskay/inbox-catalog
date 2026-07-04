"""Offline tests for the read-only Gmail path.

No network, no Google libraries, no credentials — everything is exercised with
pure helpers and a hand-rolled fake Gmail service, so this runs in CI on the
standard library alone:

    python3 -m unittest tests.test_gmail_offline -v
    # or:  python3 tests/test_gmail_offline.py
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog import cli, config, gmail_client, google_auth  # noqa: E402


# --- a minimal fake Gmail API client (duck-typed) --------------------------

class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _Messages:
    def __init__(self, pages, raw_by_id):
        self._pages = pages          # list of list response dicts
        self._raw_by_id = raw_by_id  # id -> {"raw": b64url}
        self.list_calls = []

    def list(self, userId, q, maxResults, pageToken):
        self.list_calls.append({"q": q, "pageToken": pageToken})
        # page index is encoded in the token: None -> 0, "p1" -> 1, ...
        idx = 0 if pageToken is None else int(pageToken[1:])
        return _Exec(self._pages[idx])

    def get(self, userId, id, format):
        return _Exec(self._raw_by_id[id])


class _Users:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeGmail:
    def __init__(self, pages, raw_by_id):
        self._users = _Users(_Messages(pages, raw_by_id))

    def users(self):
        return self._users


def _raw(headers_subject: str) -> str:
    msg = (f"From: shop@example.com\r\nSubject: {headers_subject}\r\n"
           f"\r\nbody\r\n").encode()
    return base64.urlsafe_b64encode(msg).decode("ascii")


# --- pure helpers ----------------------------------------------------------

class TestBuildQuery(unittest.TestCase):
    def test_senders_and_lookback(self):
        q = gmail_client.build_query(["a.example", "b.example"], 365)
        self.assertEqual(q, "from:(a.example OR b.example) newer_than:365d")

    def test_blank_senders_dropped(self):
        q = gmail_client.build_query(["", None, "  ", "real.example"], 30)
        self.assertEqual(q, "from:(real.example) newer_than:30d")

    def test_no_lookback(self):
        self.assertEqual(gmail_client.build_query(["a"], 0), "from:(a)")

    def test_empty_allowlist_has_no_from(self):
        # Must never produce a bare query that would match the whole mailbox via
        # an empty from:() group.
        self.assertEqual(gmail_client.build_query([], 365), "newer_than:365d")


class TestDecodeRaw(unittest.TestCase):
    def test_roundtrip(self):
        msg = gmail_client.decode_raw(_raw("Order #X-1"))
        self.assertEqual(msg["Subject"], "Order #X-1")
        self.assertEqual(msg["From"], "shop@example.com")


# --- paging + fetch with the fake service ----------------------------------

class TestIterMessages(unittest.TestCase):
    def test_pagination_and_decode(self):
        pages = [
            {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p1"},
            {"messages": [{"id": "m3"}]},  # no nextPageToken -> last page
        ]
        raw_by_id = {
            "m1": {"raw": _raw("one")},
            "m2": {"raw": _raw("two")},
            "m3": {"raw": _raw("three")},
        }
        svc = FakeGmail(pages, raw_by_id)
        out = list(gmail_client.iter_messages(svc, "from:(a)", max_messages=100))
        self.assertEqual([mid for mid, _ in out], ["m1", "m2", "m3"])
        self.assertEqual([m["Subject"] for _, m in out], ["one", "two", "three"])

    def test_respects_max_messages_cap(self):
        pages = [{"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}]
        raw_by_id = {i: {"raw": _raw(i)} for i in ("m1", "m2", "m3")}
        svc = FakeGmail(pages, raw_by_id)
        out = list(gmail_client.iter_messages(svc, "q", max_messages=2))
        self.assertEqual([mid for mid, _ in out], ["m1", "m2"])


# --- token presence + source selection -------------------------------------

class TestTokenAndSourceSelection(unittest.TestCase):
    def setUp(self):
        self._orig_token = config.GOOGLE_TOKEN_PATH
        self._tmp = tempfile.TemporaryDirectory()
        config.GOOGLE_TOKEN_PATH = Path(self._tmp.name) / "token.json"

    def tearDown(self):
        config.GOOGLE_TOKEN_PATH = self._orig_token
        self._tmp.cleanup()

    def test_has_token_false_then_true(self):
        self.assertFalse(google_auth.has_token())
        config.GOOGLE_TOKEN_PATH.write_text("{}")
        self.assertTrue(google_auth.has_token())

    def test_load_credentials_none_without_token(self):
        self.assertIsNone(google_auth.load_credentials())

    def _args(self, **over):
        ns = cli.build_parser().parse_args([])
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def test_source_selection(self):
        from inboxcatalog.sources import (FixtureSource, GmailApiSource,
                                          ImapSource)
        from inboxcatalog import profiles
        profile = profiles.load("demo")

        # explicit --fixtures wins
        src, label = cli._resolve_source(self._args(fixtures=""), profile)
        self.assertIsInstance(src, FixtureSource)

        # explicit --gmail
        src, label = cli._resolve_source(self._args(gmail=True), profile)
        self.assertIsInstance(src, GmailApiSource)

        # explicit --imap
        src, label = cli._resolve_source(self._args(imap=True), profile)
        self.assertIsInstance(src, ImapSource)

        # auto: token present -> Gmail
        config.GOOGLE_TOKEN_PATH.write_text("{}")
        src, label = cli._resolve_source(self._args(), profile)
        self.assertIsInstance(src, GmailApiSource)

        # auto: no token -> IMAP (back-compat)
        config.GOOGLE_TOKEN_PATH.unlink()
        src, label = cli._resolve_source(self._args(), profile)
        self.assertIsInstance(src, ImapSource)


if __name__ == "__main__":
    unittest.main(verbosity=2)
