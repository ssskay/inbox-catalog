"""Read-only IMAP password resolver. Domain-agnostic and reusable.

Resolution order:
    1. environment variable (``config.ENV_VAR``)
    2. macOS Keychain: ``security find-generic-password -a "$USER" -s <service> -w``

The secret is NEVER hardcoded, written to disk, or printed. If neither source
returns a value, ``require_imap_password`` prints setup instructions and the
caller exits cleanly. The value is registered with the log redactor so it can
never leak into a log line.
"""
from __future__ import annotations

import os
import subprocess
import sys

from . import config, logutil

log = logutil.get("auth")


def get_imap_password() -> str | None:
    """Return the IMAP password from env or Keychain, or None."""
    pw = os.environ.get(config.ENV_VAR)
    if pw and pw.strip():
        log.debug("imap password sourced from env var %s", config.ENV_VAR)
        val = pw.strip()
        logutil.register_secret(val)
        return val

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", user,
             "-s", config.KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            log.debug("imap password sourced from Keychain (service=%s)",
                      config.KEYCHAIN_SERVICE)
            val = out.stdout.strip()
            logutil.register_secret(val)
            return val
    except FileNotFoundError:
        log.debug("`security` not found — not on macOS? skipping Keychain")
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Keychain lookup failed: %s", exc)
    return None


def imap_password_source() -> str | None:
    """Where an IMAP password *would* come from — ``"env"`` or ``"keychain"`` —
    or None if neither is set. Existence check only: it never retrieves or prints
    the secret, and it reads Keychain **metadata** (no ``-w``) so it can't trigger
    a macOS access prompt. Safe for a read-only setup/doctor check."""
    pw = os.environ.get(config.ENV_VAR)
    if pw and pw.strip():
        return "env"
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", user,
             "-s", config.KEYCHAIN_SERVICE],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return "keychain"
    except FileNotFoundError:
        pass
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def _setup_msg() -> str:
    account = config.IMAP_ACCOUNT or "<your-imap-account>"
    return f"""
No IMAP password found. Set it ONE of these two ways, then re-run:

  # Option A — environment variable (this shell only):
  export {config.ENV_VAR}='your-app-password'

  # Option B — macOS Keychain (persistent, recommended):
  security add-generic-password -a "$USER" -s {config.KEYCHAIN_SERVICE} -w 'your-app-password'

Use an app-specific password from your mail provider (account: {account}).
Access is READ-ONLY; the secret is never written to disk or logged by this tool.

Tip: you don't need a mailbox at all to try the engine — run against the bundled
synthetic fixtures with `--fixtures` (see the README quickstart).
""".rstrip()


def require_imap_password() -> str:
    """Return the password or print setup instructions and exit(2)."""
    pw = get_imap_password()
    if not pw:
        print(_setup_msg(), file=sys.stderr)
        sys.exit(2)
    return pw
