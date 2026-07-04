"""READ-ONLY Gmail API fetch — the Gmail counterpart to ``imap_client``.

Yields ``(message_id, email.message.Message)`` pairs, exactly like the IMAP and
fixture sources, so the ingest orchestrator is unchanged. We use the Gmail API
with the ``gmail.readonly`` scope and ``format=raw`` so each message comes back as
the original RFC822 bytes — the same thing the IMAP path already parses, which
means every downstream template works untouched.

The query-building and raw-decoding helpers are pure functions with no network or
Google dependency, so they are unit-tested offline.
"""
from __future__ import annotations

import base64
import email
from email.message import Message
from typing import Iterator

from . import config, logutil

log = logutil.get("gmail")


def build_query(sender_allowlist: list[str], lookback_days: int) -> str:
    """Translate the profile allowlist + lookback window into a Gmail search query.

    Mirrors the IMAP ``SINCE``/``FROM`` search: only messages from allowlisted
    senders, within the time window. Empty/None senders are dropped so a stray
    blank never widens the search to the whole mailbox.
    """
    senders = [s.strip() for s in sender_allowlist if s and s.strip()]
    parts: list[str] = []
    if senders:
        parts.append("from:(" + " OR ".join(senders) + ")")
    if lookback_days and lookback_days > 0:
        parts.append(f"newer_than:{int(lookback_days)}d")
    return " ".join(parts)


def decode_raw(raw_b64url: str) -> Message:
    """Decode a Gmail ``format=raw`` payload (base64url) into an email Message."""
    data = base64.urlsafe_b64decode(raw_b64url.encode("ascii"))
    return email.message_from_bytes(data)


def _list_ids(service, query: str, max_messages: int) -> Iterator[str]:
    """Yield message ids matching the query, paging politely until the cap."""
    page_token = None
    yielded = 0
    while True:
        resp = (service.users().messages()
                .list(userId="me", q=query, maxResults=config.GMAIL_PAGE_SIZE,
                      pageToken=page_token)
                .execute())
        for m in resp.get("messages", []):
            yield m["id"]
            yielded += 1
            if yielded >= max_messages:
                log.info("reached message cap (%d)", max_messages)
                return
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def iter_messages(service, query: str, max_messages: int
                  ) -> Iterator[tuple[str, Message]]:
    """Yield ``(message_id, Message)`` for every message matching the query."""
    count = 0
    for mid in _list_ids(service, query, max_messages):
        try:
            raw = (service.users().messages()
                   .get(userId="me", id=mid, format="raw")
                   .execute())
            msg = decode_raw(raw["raw"])
        except Exception as exc:
            log.warning("could not fetch/parse message %s: %s", mid, exc)
            continue
        count += 1
        yield mid, msg
    log.info("fetched %d message(s) from Gmail (query=%r)", count, query)
