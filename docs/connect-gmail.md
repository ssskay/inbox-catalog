# Connect your mailbox (read-only)

Inbox Catalog runs **on your own machine, against your own inbox**, and only ever
**reads** (mailboxes are opened with `EXAMINE`, never `SELECT` — it cannot delete,
move, or flag a message). Nothing is uploaded to a server; there is no server.

> **Using this through Claude with a Gmail connector/MCP already hooked up?**
> Then you may not need this page at all — Claude can search and read your order
> mail directly through the connector (still read-only, no credential to create).
> This page is for the **local engine's own access**, used when no connector is
> available or you want the deterministic, photo-capable engine paths.

Because you run it yourself, there's no "sign in to our app" step — there is no app
to sign in to. You just give the tool read access to your own mail. Two ways:

| | Tier 1 — **App password (recommended)** | Tier 2 — **Takeout export (fallback)** |
|---|---|---|
| Speed | Instant, ~3 clicks | Slower — request + download an export |
| Credential? | One 16-char app password | **None** |
| Live / incremental? | **Yes** — re-runs pick up new mail | No — point-in-time snapshot |
| Requires | Personal Google account **with 2-Step Verification on** | Any Google account |
| Use it when | You can turn on 2FA and want ongoing use | App passwords don't work for your account |

**Start with Tier 1.** It's the fast path and keeps your catalog current on its
own. Only drop to Tier 2 if your account can't use app passwords (see the list
below) or you'd rather not create any credential at all.

---

## Tier 1 — App password over IMAP (recommended)

An app password is a single 16-character code, scoped to this one tool, that lets
it read your mailbox live over IMAP. Each re-run picks up new mail — no re-export,
nothing to refresh manually.

**Requirement:** a **personal Google account with 2-Step Verification turned on.**
App passwords don't exist until 2FA is enabled.

1. **Turn on 2-Step Verification** if it isn't already:
   <https://myaccount.google.com/security> → "2-Step Verification".
2. Go to **<https://myaccount.google.com/apppasswords>**.
3. Type a name like `inbox catalog` → **Create** → **copy the 16-character code**.
4. Hand the code to the tool — macOS Keychain (persistent, recommended):
   ```bash
   security add-generic-password -a "$USER" -s inbox-catalog-imap -w 'the-16-char-code'
   export INBOX_IMAP_ACCOUNT='you@gmail.com'
   ```
   or an environment variable for this shell only:
   ```bash
   export INBOX_IMAP_ACCOUNT='you@gmail.com'
   export INBOX_IMAP_PASSWORD='the-16-char-code'
   ```
5. Run it — dry run first (writes nothing), then commit:
   ```bash
   python3 -m inboxcatalog --ingest --imap            # DRY RUN
   python3 -m inboxcatalog --ingest --imap --apply    # catalog it
   python3 -m inboxcatalog --stats
   ```

Other providers work too — set `INBOX_IMAP_HOST` / `INBOX_IMAP_PORT` and use that
provider's app-password equivalent.

> **Heads-up — regenerating:** an app password is tied to your account password.
> **Changing your Google password revokes all app passwords**, so if you reset it
> you'll need to generate a fresh one (steps 2–4) and re-store it.

### Who can't use app passwords?

If any of these describe you, skip to Tier 2 — app passwords either don't exist or
are blocked for your account:

1. **No 2-Step Verification** — app passwords require 2FA; without it the
   `apppasswords` page won't offer one.
2. **Google Workspace / school or work accounts** where the **admin has disabled
   app passwords** (common on managed domains).
3. **Advanced Protection Program** accounts — Google blocks app passwords entirely
   for these.

For all three, use the Takeout fallback below, which needs no credential at all.

---

## Tier 2 — Takeout export (fallback: no credential)

Use this if you **can't** use an app password (see the list above) or simply
**won't** create a credential. Google Takeout hands you a copy of your own mail as
a standard `.mbox` file; you point the tool at it. No password, no Google Cloud
project, nothing that can be breached later — it's a static file on your disk.

1. Go to **<https://takeout.google.com>**.
2. **Deselect all**, then select **only Mail**. (You can limit it to a single
   label — e.g. a Gmail filter that tags order/receipt emails, then export just
   that label. Smaller export, tighter privacy.)
3. Choose **.mbox** as the format, create the export, then download + unzip it. You
   now have a file like `All mail Including Spam and Trash.mbox`.
4. Point the tool straight at the file — dry run first (writes nothing), then
   commit:
   ```bash
   python3 -m inboxcatalog --ingest --mbox "path/to/export.mbox"            # DRY RUN
   python3 -m inboxcatalog --ingest --mbox "path/to/export.mbox" --apply    # catalog it
   python3 -m inboxcatalog --stats
   ```
   `--mbox` reads the export in place (read-only, standard library — no extra
   install) and honours the same dry-run / `--apply` rules as every other source.

**Tradeoff, stated honestly:** a Takeout export is a *snapshot*. To catalog orders
that arrive later, re-export and re-run. For a fallback you reach for when app
passwords aren't available, that's the accepted cost.

<details>
<summary>Alternative: split the mbox into <code>.eml</code> files first</summary>

If you'd rather not use `--mbox`, you can split the export into per-message `.eml`
files (pure standard library, no install) and use the `--fixtures` directory reader
instead — same result:

```bash
python3 - "path/to/export.mbox" ./mail-eml <<'PY'
import mailbox, pathlib, sys
src, out = sys.argv[1], pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for i, msg in enumerate(mailbox.mbox(src)):
    (out / f"{i:06d}.eml").write_bytes(msg.as_bytes())
print("wrote", i + 1, "messages to", out)
PY

python3 -m inboxcatalog --ingest --fixtures ./mail-eml --apply    # catalog it
```
The `.eml` folder is just a scratch copy of your own mail; delete it when done.
</details>

---

## Why not "Sign in with Google" (OAuth)?

Reading Gmail is a Google **restricted** scope (`gmail.readonly`). For a tool that
strangers download and run themselves, that makes OAuth the *worst* option, not the
friendly one: to ship a "Sign in with Google" client we'd own a public consent
screen, and a restricted scope forces Google's **CASA security assessment** — a
third-party audit that must be **re-done every 12 months** and can cost from a few
hundred dollars into five figures. Nobody's going to fund a recurring annual audit
so strangers can read *their own* mail on *their own* laptop, and the free
"Testing" alternative expires each user's sign-in after 7 days. The engine does
ship an isolated, opt-in read-only OAuth path for a possible *hosted* future
(`pip install "inbox-catalog[gmail]"`, then `python3 -m inboxcatalog connect`), but
it is not a path you want for self-run use.

> **If you did run `connect`:** once a token exists, ingest prefers the Gmail API
> automatically. Pass `--imap` to force the app-password path, or
> `python3 -m inboxcatalog disconnect` to remove the token and go back to the
> tiers above.

---

## Glossary — the four ways mail can be read (don't mix them up)

| Path | Credential | Who reads the mail | Status |
|---|---|---|---|
| **Claude Gmail connector/MCP** | none (connector's own grant) | Claude, in-session | Preferred when available |
| **IMAP app password** (Tier 1) | 16-char app password | the engine | Recommended engine path |
| **Takeout `.mbox`** (Tier 2) | none | the engine, from a file | Fallback / snapshot |
| **OAuth `connect`** (`--gmail`) | local OAuth token | the engine | Shipped but dormant — hosted-future only |

"App password" and "OAuth" are different credentials; the Claude connector is not
a credential of this tool at all. Every path is read-only.
