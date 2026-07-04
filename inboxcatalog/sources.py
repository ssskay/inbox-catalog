"""Message sources — the seam between a live mailbox and offline fixtures.

Both sources yield ``(uid, email.message.Message)`` pairs, so the ingest
orchestrator is identical whether it reads a real read-only IMAP account or a
directory of ``.eml`` files. The fixture source is what makes the engine runnable
with no mailbox and no network — the first thing someone cloning the repo tries.
"""
from __future__ import annotations

import email
import mailbox
from email.message import Message
from pathlib import Path
from typing import Iterator, Protocol

from . import auth, config, gmail_client, google_auth, imap_client, logutil
from .profile import CollectionProfile

log = logutil.get("sources")


class MessageSource(Protocol):
    """Anything that can yield candidate emails for ingest."""

    def iter_messages(self, lookback_days: int) -> Iterator[tuple[str, Message]]:
        ...


class ImapSource:
    """Live, READ-ONLY mailbox source. Uses the profile's sender allowlist to
    find candidates server-side, then fetches them in polite batches."""

    def __init__(self, profile: CollectionProfile):
        self.profile = profile

    def iter_messages(self, lookback_days: int) -> Iterator[tuple[str, Message]]:
        password = auth.require_imap_password()  # exits cleanly if unset
        if not config.IMAP_ACCOUNT:
            raise SystemExit(
                "INBOX_IMAP_ACCOUNT is not set. Export it (e.g. "
                "export INBOX_IMAP_ACCOUNT='you@example.com') or try the bundled "
                "fixtures with --fixtures.")
        with imap_client.connection(password) as conn:
            uids = imap_client.find_candidate_uids(
                conn, lookback_days, self.profile.sender_allowlist)
            yield from imap_client.fetch_messages(conn, uids)


class GmailApiSource:
    """Live, READ-ONLY Gmail source via the Gmail API (``gmail.readonly`` scope).

    The friendly "Sign in with Google" alternative to an IMAP app password. Uses
    the profile's sender allowlist to build a server-side Gmail query, then
    fetches matching messages as raw RFC822 — identical bytes to the IMAP path,
    so every downstream template is unchanged."""

    def __init__(self, profile: CollectionProfile):
        self.profile = profile

    def iter_messages(self, lookback_days: int) -> Iterator[tuple[str, Message]]:
        creds = google_auth.load_credentials()
        if creds is None:
            raise SystemExit(google_auth.not_connected_msg())
        service = google_auth.build_gmail(creds)
        query = gmail_client.build_query(
            self.profile.sender_allowlist, lookback_days)
        log.info("Gmail (READ-ONLY) query: %s", query)
        yield from gmail_client.iter_messages(
            service, query, config.MAX_MESSAGES)


class FixtureSource:
    """Offline source: every ``*.eml`` in a directory, sorted by filename.

    No network, no credentials, no mailbox. The synthetic ``uid`` is the file
    name so the ingest log stays idempotent across runs."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def iter_messages(self, lookback_days: int) -> Iterator[tuple[str, Message]]:
        # `lookback_days` is part of the MessageSource contract but meaningless
        # offline — fixtures are read in full regardless of any time window.
        del lookback_days
        if not self.directory.is_dir():
            raise SystemExit(f"fixture directory not found: {self.directory}")
        files = sorted(self.directory.glob("*.eml"))
        log.info("loading %d fixture email(s) from %s", len(files), self.directory)
        for path in files:
            try:
                msg = email.message_from_bytes(path.read_bytes())
            except Exception as exc:
                log.warning("could not parse fixture %s: %s", path.name, exc)
                continue
            yield path.stem, msg


class MboxSource:
    """Offline source: every message in a single ``.mbox`` file (Google Takeout).

    This is the zero-credential Tier-2 path. Takeout exports a mailbox as one
    concatenated ``.mbox`` file; we iterate it with the standard library
    (``mailbox.mbox``, no third-party dep) and re-parse each message through the
    same ``email.message_from_bytes`` used by the IMAP/Gmail/fixtures paths, so the
    bytes handed downstream are identical and every template is unchanged.

    Like the fixture source, a ``.mbox`` is a static snapshot: ``lookback_days`` is
    part of the contract but meaningless here — the file is read in full. Each
    message's ``uid`` is its RFC822 ``Message-ID`` when present (stable across
    re-runs for idempotent ingest), else a positional fallback."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def iter_messages(self, lookback_days: int) -> Iterator[tuple[str, Message]]:
        del lookback_days  # snapshot: no time window
        if not self.path.is_file():
            raise SystemExit(
                f"mbox file not found: {self.path}\n"
                "Export your mail as .mbox from https://takeout.google.com "
                "(select only Mail), or try the bundled fixtures with --fixtures.")
        box = mailbox.mbox(str(self.path))
        log.info("loading messages from mbox %s", self.path)
        count = 0
        try:
            for i, raw in enumerate(box):
                try:
                    # Re-parse so the message is a plain email.Message with no mbox
                    # 'From ' envelope — byte-identical to the .eml/IMAP path.
                    msg = email.message_from_bytes(raw.as_bytes())
                except Exception as exc:
                    log.warning("could not parse mbox message #%d: %s", i, exc)
                    continue
                uid = ((msg.get("Message-ID") or "").strip()
                       or f"mbox-{self.path.stem}-{i:06d}")
                count += 1
                yield uid, msg
            log.info("read %d message(s) from %s", count, self.path)
        finally:
            box.close()  # release the file handle even if iteration is abandoned


def default_fixture_dir(profile_name: str = "demo") -> Path:
    """The bundled synthetic fixtures for a profile. Profiles other than the
    demo keep theirs in ``profiles/fixtures_<name>/``; fall back to the demo
    fixtures when a profile ships none."""
    base = Path(__file__).resolve().parent / "profiles"
    if profile_name and profile_name != "demo":
        candidate = base / f"fixtures_{profile_name}"
        if candidate.is_dir():
            return candidate
    return base / "fixtures"
