# Connect your mailbox (read-only)

Amazon Tracker uses the **inbox-catalog engine's** mail access — there is no
separate Amazon credential or setup. Everything is read-only: mailboxes are
opened with IMAP `EXAMINE`, and the tool cannot send, delete, move, or flag a
message.

**Full guide:** see `docs/connect-gmail.md` in the engine repo
([inbox-catalog](https://github.com/ssskay/inbox-catalog)) for the two-tier
setup with screenshots-level detail. The short version:

| | Tier 1 — **App password (recommended)** | Tier 2 — **Takeout export** |
|---|---|---|
| Speed | Instant, ~3 clicks | Request + download an export |
| Credential? | One 16-char app password | **None** |
| Live? | Yes — re-runs pick up new mail | Point-in-time snapshot |

- **Tier 1:** personal Gmail with 2-Step Verification → create an app password
  → store it in the macOS Keychain as `inbox-catalog-imap` (or export
  `INBOX_IMAP_PASSWORD`). Then:

      INBOX_IMAP_ACCOUNT='you@gmail.com' python3 -m inboxcatalog --profile amazon --ingest --imap

- **Tier 2:** export Mail from https://takeout.google.com as `.mbox`, then:

      python3 -m inboxcatalog --profile amazon --ingest --mbox "path/to/export.mbox"

Both are **dry runs** until you add `--apply`. If a Gmail connector/MCP is
already available in your Claude session, no credential is needed at all —
Claude searches and reads your Amazon order mail directly (still read-only).

One Amazon-specific note: the profile's sender allowlist is just `amazon.com`,
so the server-side search already narrows to Amazon mail — a full-inbox scan
is never needed.
