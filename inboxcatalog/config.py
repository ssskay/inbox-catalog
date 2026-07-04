"""Core, domain-neutral configuration.

This module holds only engine-level settings: filesystem paths, the IMAP target,
model ids, and politeness limits. It deliberately contains **no domain logic** —
the list of senders to watch, the keyword gate, parse templates, and the
enrichment prompt all live in a :class:`~inboxcatalog.profile.CollectionProfile`
selected at runtime (see ``profiles/``).

No secrets live here. The IMAP password is read at runtime by
``auth.get_imap_password`` from an environment variable or the macOS Keychain and
is never written to disk or logged.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Paths -----------------------------------------------------------------
# Everything runtime-generated lives under data/ (gitignored). Images are kept
# on disk next to their sha256 so the DB stays small and the files are portable.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("INBOX_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("INBOX_DB", DATA_DIR / "catalog.db"))
IMAGES_DIR = DATA_DIR / "images"

# --- Active profile --------------------------------------------------------
# Which CollectionProfile drives sender/keyword/template/taxonomy logic. The CLI
# --profile flag overrides this. The engine never hardcodes a domain.
DEFAULT_PROFILE = os.environ.get("INBOX_PROFILE", "demo")

# --- IMAP (READ-ONLY) ------------------------------------------------------
# All connection details are environment-driven so no personal account is ever
# baked into the source. Mailboxes are opened with EXAMINE (read-only); the tool
# never deletes, moves, flags, or otherwise modifies mail.
IMAP_HOST = os.environ.get("INBOX_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("INBOX_IMAP_PORT", "993"))
IMAP_ACCOUNT = os.environ.get("INBOX_IMAP_ACCOUNT", "")  # required only for live runs
IMAP_MAILBOX = os.environ.get("INBOX_IMAP_MAILBOX", "INBOX")

# Where the read-only IMAP password is sourced from (never persisted by us).
ENV_VAR = "INBOX_IMAP_PASSWORD"
KEYCHAIN_SERVICE = os.environ.get("INBOX_KEYCHAIN_SERVICE", "inbox-catalog-imap")

# --- Gmail "Sign in with Google" (read-only OAuth) -------------------------
# The friendly alternative to an IMAP app password: a one-click browser sign-in
# that grants ONLY the read-only Gmail scope. Unlike Gmail's IMAP (which needs
# the full mail.google.com read/write scope), the Gmail API honours a strictly
# read-only scope, so this path cannot modify mail at the permission layer.
#
# Setup lives under a per-user config dir (NOT in the repo, NOT in data/). The
# OAuth token is the one credential we must persist (refresh tokens are how
# OAuth works) — it is written local-only, 0600, and is revocable in two clicks
# at https://myaccount.google.com/permissions. See docs/connect-gmail.md.
CONFIG_DIR = Path(os.environ.get("INBOX_CONFIG_DIR", Path.home() / ".inbox-catalog"))
GOOGLE_CLIENT_SECRET = Path(os.environ.get(
    "INBOX_GOOGLE_CLIENT_SECRET", CONFIG_DIR / "client_secret.json"))
GOOGLE_TOKEN_PATH = Path(os.environ.get(
    "INBOX_GOOGLE_TOKEN", CONFIG_DIR / "token.json"))
# Strictly read-only. Changing this list would change what the consent screen
# asks for — keep it read-only by construction.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_PAGE_SIZE = 100     # message ids per Gmail API list page

# --- Models ----------------------------------------------------------------
# Local CLIP model for image embeddings (sentence-transformers id). CPU-friendly.
# Heavy deps are lazy-imported, so non-embedding paths run without torch.
CLIP_MODEL = os.environ.get("INBOX_CLIP_MODEL", "clip-ViT-B-32")
# Optional LLM fallback for emails no template can parse. A small/cheap model is
# the right tool here — never a flagship model in an automated pipeline.
LLM_MODEL = os.environ.get("INBOX_LLM_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_ENV_VAR = "ANTHROPIC_API_KEY"

# --- Politeness limits -----------------------------------------------------
FETCH_BATCH = 25          # UIDs fetched per IMAP round-trip
MAX_MESSAGES = 2500       # hard cap per run, paged through in FETCH_BATCH chunks
HTTP_TIMEOUT = 20         # seconds, per image download
DEFAULT_LOOKBACK_DAYS = 365


def ensure_dirs() -> None:
    """Create the data + images directories if missing (idempotent)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
