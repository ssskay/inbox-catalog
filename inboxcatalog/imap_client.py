"""READ-ONLY IMAP access.

Mailboxes are opened with readonly=True (EXAMINE, not SELECT). We never delete,
move, flag, or modify mail. Candidate emails are found by per-sender SINCE
searches (polite, narrow) using the active profile's sender allowlist, then
fetched in batches.
"""
from __future__ import annotations

import email
import imaplib
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import Message
from typing import Iterator

from . import config, logutil

log = logutil.get("imap")


@contextmanager
def connection(password: str) -> Iterator[imaplib.IMAP4_SSL]:
    """Yield a logged-in, read-only IMAP connection; always logs out."""
    log.info("connecting to %s:%s as %s (READ-ONLY)",
             config.IMAP_HOST, config.IMAP_PORT, config.IMAP_ACCOUNT)
    conn = imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT)
    try:
        conn.login(config.IMAP_ACCOUNT, password)
        # readonly=True => EXAMINE, not SELECT. No flag/state changes.
        # Mailbox names with spaces/brackets (e.g. "[Gmail]/All Mail") must be
        # quoted for the IMAP command; imaplib does not auto-quote them.
        typ = "NO"
        try:
            typ, _ = conn.select(f'"{config.IMAP_MAILBOX}"', readonly=True)
        except Exception as exc:
            log.warning("EXAMINE %s failed (%s)", config.IMAP_MAILBOX, exc)
        if typ != "OK":
            log.warning("could not EXAMINE %s, falling back to INBOX",
                        config.IMAP_MAILBOX)
            conn.select("INBOX", readonly=True)
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _imap_date(days: int) -> str:
    since = datetime.now() - timedelta(days=days)
    return since.strftime("%d-%b-%Y")  # e.g. 01-Jun-2026


def find_candidate_uids(conn: imaplib.IMAP4_SSL, lookback_days: int,
                        sender_allowlist: list[str]) -> list[str]:
    """Return de-duplicated UIDs of messages from allowlisted senders since the
    lookback window. We search per-sender and union the results."""
    since = _imap_date(lookback_days)
    seen: list[str] = []
    seen_set: set[str] = set()
    for sender in sender_allowlist:
        if not sender:
            continue  # never let an empty/None criterion reach the IMAP search
        # No CHARSET prefix: SINCE/FROM args are ASCII, and omitting it keeps a
        # None out of conn.uid() entirely (imaplib would skip a None arg anyway,
        # so the on-the-wire command is unchanged).
        typ, data = conn.uid("search", "SINCE", since, "FROM", sender)
        if typ != "OK":
            log.debug("search failed for sender=%s", sender)
            continue
        uids = data[0].split() if data and data[0] else []
        new = [u.decode() for u in uids if u.decode() not in seen_set]
        for u in new:
            seen_set.add(u)
            seen.append(u)
        if uids:
            log.debug("sender %-22s -> %d msgs", sender, len(uids))
    log.info("found %d candidate emails since %s across %d senders",
             len(seen), since, len(sender_allowlist))
    return seen[: config.MAX_MESSAGES]


def fetch_messages(conn: imaplib.IMAP4_SSL, uids: list[str]
                   ) -> Iterator[tuple[str, Message]]:
    """Yield (uid, parsed email.Message) in polite batches."""
    for i in range(0, len(uids), config.FETCH_BATCH):
        batch = uids[i:i + config.FETCH_BATCH]
        uid_set = ",".join(batch)
        typ, data = conn.uid("fetch", uid_set, "(RFC822)")
        if typ != "OK" or not data:
            log.warning("fetch failed for batch starting at index %d", i)
            continue
        idx = 0
        for part in data:
            if not isinstance(part, tuple):
                continue
            raw = part[1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw)
            uid = batch[idx] if idx < len(batch) else "?"
            idx += 1
            yield uid, msg
        log.debug("fetched batch %d-%d", i, i + len(batch))
