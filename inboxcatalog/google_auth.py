"""Read-only Gmail OAuth ("Sign in with Google") — shipped but dormant.

This is NOT the recommended path for self-run use — that's an IMAP app password
or a Takeout ``.mbox`` (see ``docs/connect-gmail.md``, including its "Why not
OAuth?" section). It is kept as an isolated, opt-in path for a possible *hosted*
future. When used, the user clicks a real Google consent page (no password typed,
nothing pasted) and grants ONLY ``gmail.readonly``. The resulting token is stored
**local-only** and 0600; it is the single credential we persist, because that is
how OAuth refresh works, and it is revocable in two clicks at
https://myaccount.google.com/permissions. Heads-up: once a token exists,
``cli._resolve_source`` prefers it automatically — ``--imap`` forces the
app-password path; ``python3 -m inboxcatalog disconnect`` removes the token.

Design choices that keep the project's posture intact:
- **Read-only at the permission layer.** We request only the read-only scope, so
  the app cannot send, delete, or modify mail even in principle.
- **Local-first.** The token never leaves the user's machine; there is no server.
- **Optional + lazy.** The Google libraries are an opt-in extra
  (``pip install inbox-catalog[gmail]``) and are imported lazily, so every other
  code path runs without them — exactly like the CLIP and ``anthropic`` deps.
- **No token in logs.** The access/refresh tokens are registered with the log
  redactor, mirroring ``auth.register_secret`` for the IMAP password.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from . import config, logutil

log = logutil.get("google_auth")

_MISSING_DEPS_MSG = (
    "Gmail sign-in needs the optional Google libraries. Install them with:\n"
    "  pip3 install inbox-catalog[gmail]\n"
    "or:\n"
    "  pip3 install google-auth google-auth-oauthlib google-api-python-client"
)


def available() -> bool:
    """True if the optional Google OAuth/Gmail libraries are importable."""
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        return True
    except ImportError:
        return False


def has_token() -> bool:
    """True if a saved OAuth token exists on this machine (does not validate it)."""
    return config.GOOGLE_TOKEN_PATH.exists()


def not_connected_msg() -> str:
    return (
        "Not connected to Gmail. Run `python3 -m inboxcatalog connect` first "
        "(one-time browser sign-in, read-only). Setup guide: docs/connect-gmail.md.\n"
        "Or use the bundled offline demo with `--ingest --fixtures` — no mailbox "
        "needed."
    )


def _register_token_secrets(creds) -> None:
    """Keep tokens out of any log line, like the IMAP password redactor."""
    for attr in ("token", "refresh_token"):
        val = getattr(creds, attr, None)
        if val:
            logutil.register_secret(val)


def _save_token(creds) -> None:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.GOOGLE_TOKEN_PATH
    path.write_text(creds.to_json())
    try:
        path.chmod(0o600)  # local-only, owner read/write
    except OSError:  # pragma: no cover - non-POSIX
        pass
    log.debug("saved OAuth token to %s (0600, local only)", path)


def load_credentials():
    """Return valid, refreshed credentials, or ``None`` if not connected.

    Refreshes a stale access token in place (and re-saves) when a refresh token
    is present. Never prompts; the interactive browser flow is ``connect`` only.
    """
    if not has_token():
        return None
    if not available():
        log.warning("token exists but Google libraries are missing")
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(
            str(config.GOOGLE_TOKEN_PATH), config.GMAIL_SCOPES)
    except Exception as exc:
        log.warning("could not read saved token (%s); re-run `connect`", exc)
        return None
    _register_token_secrets(creds)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _register_token_secrets(creds)
            _save_token(creds)
            log.debug("refreshed expired access token")
        except Exception as exc:
            log.warning("token refresh failed (%s); re-run `connect`", exc)
            return None
    return creds if (creds and creds.valid) else None


def build_gmail(creds):
    """Build a read-only Gmail API client from credentials."""
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _whoami(creds) -> Optional[str]:
    """Return the connected mailbox address via the read-only getProfile call."""
    try:
        service = build_gmail(creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception as exc:  # pragma: no cover - network
        log.debug("getProfile failed: %s", exc)
        return None


def connect() -> int:
    """Interactive, one-time browser sign-in. Returns a process exit code."""
    if not available():
        print(_MISSING_DEPS_MSG, file=sys.stderr)
        return 2
    secret = config.GOOGLE_CLIENT_SECRET
    if not secret.exists():
        print(
            f"client_secret.json not found at {secret}.\n"
            "Do the one-time Google setup first (Part 1 of docs/connect-gmail.md), "
            "then save the downloaded file there.",
            file=sys.stderr)
        return 2

    from google_auth_oauthlib.flow import InstalledAppFlow

    log.info("opening your browser to sign in to Google (read-only Gmail)…")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secret), config.GMAIL_SCOPES)
        # Desktop-app client → loopback redirect on a random free port. No hosted
        # redirect URI, no server: a pure-CLI browser handshake.
        creds = flow.run_local_server(port=0, prompt="consent",
                                      authorization_prompt_message="")
    except Exception as exc:
        print(f"Sign-in did not complete: {exc}", file=sys.stderr)
        return 1

    _register_token_secrets(creds)
    _save_token(creds)
    who = _whoami(creds) or "your account"
    print(f"\n  Connected as {who} — read-only.")
    print(f"  Token saved to {config.GOOGLE_TOKEN_PATH} (local only, revoke at")
    print("  https://myaccount.google.com/permissions).\n")
    print("Next: `python3 -m inboxcatalog --ingest` for a dry run "
          "(writes nothing), then add `--apply` to catalog.")
    return 0


def disconnect() -> int:
    """Delete the local OAuth token. Returns a process exit code."""
    path = config.GOOGLE_TOKEN_PATH
    if path.exists():
        path.unlink()
        print(f"Disconnected — removed local token {path}.")
        print("To fully revoke access, also remove it at "
              "https://myaccount.google.com/permissions.")
    else:
        print("Already disconnected (no local token found).")
    return 0
