"""Command-line entry point.

  python3 -m inboxcatalog --stats
  python3 -m inboxcatalog connect                        # Gmail sign-in (read-only)
  python3 -m inboxcatalog --ingest --fixtures            # offline DRY RUN
  python3 -m inboxcatalog --ingest --fixtures --apply    # commit fixture rows
  python3 -m inboxcatalog --ingest --apply               # live, read-only mailbox
  python3 -m inboxcatalog --reindex                      # build local CLIP index
  python3 -m inboxcatalog lookup path/to/photo.jpg       # nearest catalogued item
  python3 -m inboxcatalog --profile demo --stats         # pick a profile

Ingest is a DRY RUN unless --apply is given. The IMAP password (live mode only)
is read from $INBOX_IMAP_PASSWORD or the macOS Keychain and is never written to
disk or logged. The bundled --fixtures need no mailbox, no network, no secrets.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import config, db, google_auth, ingest, logutil, profiles, returns
from .sources import (FixtureSource, GmailApiSource, ImapSource, MboxSource,
                      default_fixture_dir)

# NOTE: ``embed`` and ``lookup`` are imported lazily inside their command
# handlers below — they pull in numpy (and optionally torch/CLIP), and importing
# them here would make every command (including the zero-dependency offline demo
# and --stats) fail on a machine without those heavy deps installed.


def _resolve_profile(args):
    return profiles.load(args.profile or config.DEFAULT_PROFILE)


def _resolve_source(args, profile):
    """Pick the message source and a human label.

    Priority: explicit --fixtures / --gmail / --imap win; otherwise default to
    Gmail when the user has connected (a token exists), else fall back to the
    IMAP app-password path for backwards compatibility."""
    if args.fixtures is not None:
        directory = (Path(args.fixtures) if args.fixtures
                     else default_fixture_dir(profile.name))
        return FixtureSource(directory), "fixtures"
    if args.mbox:
        return MboxSource(Path(args.mbox)), "Takeout .mbox (read-only)"
    if args.gmail:
        return GmailApiSource(profile), "Gmail API (read-only)"
    if args.imap:
        return ImapSource(profile), "live IMAP (read-only)"
    if google_auth.has_token():
        return GmailApiSource(profile), "Gmail API (read-only)"
    return ImapSource(profile), "live IMAP (read-only)"


def _cmd_connect(args, log) -> int:
    return google_auth.connect()


def _cmd_disconnect(args, log) -> int:
    return google_auth.disconnect()


def _cmd_ingest(args, log) -> int:
    profile = _resolve_profile(args)
    source, src_label = _resolve_source(args, profile)
    conn = db.connect()
    db.init(conn)
    print(f"Profile: {profile.name} — {profile.description}")
    print(f"Source : {src_label}")
    if not args.apply:
        print("DRY RUN — no DB writes, no downloads, no API spend. "
              "Re-run with --apply to commit.\n")
    summary = ingest.run(conn, source, profile, lookback_days=args.lookback,
                         apply=args.apply, use_llm=args.llm)
    print(summary.render(args.apply))
    if args.apply and summary.added:
        print("\nTip: run `python3 -m inboxcatalog --reindex` to embed the new "
              "images for photo lookup (requires the optional CLIP deps).")
    conn.close()
    return 0


def _cmd_reindex(args, log) -> int:
    try:
        from . import embed  # lazy: pulls in numpy + CLIP, only needed here
    except ImportError:
        log.error("photo indexing needs the optional image deps. Install them "
                  "with:  pip3 install --break-system-packages 'inbox-catalog[embed]'"
                  "  (or: pip3 install numpy sentence-transformers torch Pillow)")
        return 1
    conn = db.connect()
    db.init(conn)
    try:
        stats = embed.reindex(conn, force=args.force)
    except RuntimeError as exc:
        log.error(str(exc))
        conn.close()
        return 1
    print(f"Reindex: {stats}")
    conn.close()
    return 0


def _cmd_lookup(args, log) -> int:
    try:
        from . import lookup  # lazy: pulls in numpy, only needed here
    except ImportError:
        log.error("photo lookup needs the optional image deps. Install them "
                  "with:  pip3 install --break-system-packages 'inbox-catalog[embed]'"
                  "  (or: pip3 install numpy sentence-transformers torch Pillow)")
        return 1
    conn = db.connect()
    db.init(conn)
    try:
        matches = lookup.lookup_by_image(conn, args.image, top_k=args.top_k)
    except (ValueError, RuntimeError) as exc:
        log.error(str(exc))
        conn.close()
        return 1
    if not matches:
        print("No matches — is the index built? Run `python3 -m inboxcatalog --reindex`.")
        conn.close()
        return 0
    print(f"\nTop {len(matches)} match(es) for {args.image}:\n")
    for i, m in enumerate(matches, 1):
        price = f"{m.price} {m.currency or ''}".strip() if m.price is not None else "?"
        print(f"  {i}. {m.name or '(unnamed)'}")
        print(f"     maker={m.maker or '-'}  price={price}  seller={m.seller or '-'}")
        print(f"     bought={m.purchased_at or '-'}  "
              f"similarity={m.score:.3f} ({m.confidence})")
        print(f"     image={m.image_path or '-'}")
    conn.close()
    return 0


def _resolve_since(args) -> Optional[str]:
    """Turn --since / --months into an ISO cutoff date (None = all time).
    --since wins if both are given."""
    if getattr(args, "since", None):
        return args.since
    months = getattr(args, "months", None)
    if months:
        from datetime import date
        today = date.today()
        # subtract N whole months without dateutil
        m = today.month - months
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return date(y, m, min(today.day, 28)).isoformat()
    return None


def _cmd_returns(args, log) -> int:
    profile = _resolve_profile(args)
    conn = db.connect()
    db.init(conn)
    items = db.items_for_profile(conn, profile.name, since=_resolve_since(args))
    if not items:
        print(f"No items catalogued for profile {profile.name!r} yet. "
              f"Ingest first (e.g. --ingest --fixtures --apply).")
        conn.close()
        return 0
    print(returns.render_returns(items))
    conn.close()
    return 0


def _cmd_triage(args, log) -> int:
    from .profiles.life_zones import render_triage
    profile = _resolve_profile(args)
    conn = db.connect()
    db.init(conn)
    items = db.items_for_profile(conn, profile.name, since=_resolve_since(args))
    if not items:
        print(f"No items catalogued for profile {profile.name!r} yet. "
              f"Ingest first (e.g. --ingest --fixtures --apply).")
        conn.close()
        return 0
    print(render_triage(items))
    conn.close()
    return 0


def _cmd_mark(args, log) -> int:
    key, state = args.mark
    if state not in returns.STATES:
        log.error("invalid state %r — must be one of: %s",
                  state, ", ".join(returns.STATES))
        return 1
    profile = _resolve_profile(args)
    conn = db.connect()
    db.init(conn)
    rows = db.find_items(conn, profile.name, key)
    if not rows:
        log.error("no item matches %r (tried item id, order id, name substring)", key)
        conn.close()
        return 1
    for row in rows:
        old = row["return_state"] or "(none)"
        db.set_return_state(conn, row["id"], state)
        print(f"  [{row['id']:>3}] {row['name']}: {old} -> {state}")
    conn.commit()
    conn.close()
    return 0


def _cmd_export(args, log) -> int:
    """Machine-readable catalog dump: one JSON object per item with every
    stored field plus the computed return window. This is the agent-facing
    surface — anything that wants to reason about the user's purchases
    (inventory, project momentum, reorder buttons) reads this, not the mail."""
    import json
    profile = _resolve_profile(args)
    conn = db.connect()
    db.init(conn)
    out = []
    for row in db.items_for_profile(conn, profile.name, since=_resolve_since(args)):
        item = dict(row)
        win = returns.window_for(row)
        item["return_window"] = {
            "return_by": win.return_by.isoformat() if win.return_by else None,
            "days_left": win.days_left,
            "status": ("returned" if row["return_state"] == "returned"
                       else win.status),
            "basis": win.basis,
        }
        out.append(item)
    print(json.dumps(out, indent=2, default=str))
    conn.close()
    return 0


def _cmd_stats(args, log) -> int:
    profile = _resolve_profile(args)
    conn = db.connect()
    db.init(conn)
    c = db.counts(conn)
    print(f"Active profile: {profile.name} — {profile.description}")
    print(f"Available profiles: {', '.join(profiles.available())}")
    print(f"Catalog: {c['items']} items | {c['embeddings']} embedded | "
          f"{c['emails_logged']} emails logged")
    print(f"DB: {config.DB_PATH}")
    print(f"Images: {config.IMAGES_DIR}")
    conn.close()
    return 0


def _has(module: str) -> bool:
    """True if a module is importable, without importing it (no heavy load)."""
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _cmd_doctor(args, log) -> int:
    """A friendly readiness check: what's installed, what's catalogued, and the
    ONE next step to get from the offline demo to real mail. Reads only — never
    retrieves a secret or touches a mailbox."""
    import os
    import platform
    from . import auth, google_auth

    color = sys.stdout.isatty()   # plain glyphs when piped or read by a tool
    OK = "\033[32m✓\033[0m" if color else "✓"
    NO = "\033[31m✗\033[0m" if color else "✗"
    OPT = "\033[90m○\033[0m" if color else "○"
    def line(sym, label, note=""):
        print(f"    {sym}  {label:<26} {note}")

    print("\n  Inbox Catalog — setup check")
    print("  " + "═" * 40)

    pyver = platform.python_version()
    py_ok = sys.version_info >= (3, 10)
    print("\n  Engine")
    line(OK if py_ok else NO, f"Python {pyver}", "(needs ≥ 3.10)")
    core = _has("numpy") and _has("requests")
    line(OK if core else OPT, "core deps",
         "installed" if core else "missing · only needed for real-mail --apply "
         "(pip install -r requirements.txt)")
    img = _has("sentence_transformers") and _has("torch") and _has("PIL")
    line(OK if img else OPT, "image search (CLIP)",
         "installed" if img else "not installed · optional, for `lookup` only")
    llm = _has("anthropic")
    line(OK if llm else OPT, "LLM fallback",
         "installed" if llm else "not installed · optional, for `--llm` only")

    print("\n  Your catalog")
    try:
        conn = db.connect()
        db.init(conn)
        c = db.counts(conn)
        line(OK, f"{c['items']} items", f"· {c['emails_logged']} emails logged")
        conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        line(NO, "catalog unreadable", str(exc))
    print(f"       data dir: {config.DATA_DIR}")

    print("\n  Read real mail — the engine needs ONE of these (all read-only)")
    line(OK, "Offline demo", "always works, no setup  →  --ingest --fixtures")
    src = auth.imap_password_source()
    line(OK if src else NO, "IMAP app password",
         f"found (from {src})" if src else "not set · see docs/connect-gmail.md")
    acct = os.environ.get("INBOX_IMAP_ACCOUNT", "").strip()
    line(OK if acct else NO, "IMAP account",
         acct if acct else "INBOX_IMAP_ACCOUNT not set")
    line(OK if google_auth.has_token() else OPT, "Gmail OAuth token",
         "present" if google_auth.has_token() else "none · dormant path, not needed")

    print("\n  Taxonomy")
    from .profiles import life_zones as lz
    if lz.TAXONOMY.source.endswith(".json"):
        line(OK, "local override", f"{lz.TAXONOMY.source} ({len(lz.TAXONOMY.signals)} zones)")
    else:
        line(OK, "generic defaults", f"{len(lz.TAXONOMY.signals)} shipped zones")

    print("\n  " + "─" * 56)
    imap_ready = bool(src and acct)
    if imap_ready:
        print("  Both set — pull your real Amazon mail (read-only DRY RUN first):")
        print(f"    INBOX_IMAP_ACCOUNT='{acct}' python3 -m inboxcatalog --profile amazon --ingest --imap")
    else:
        print("  Next → try the demo (zero setup):")
        print("    python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply")
        print("    python3 -m inboxcatalog --profile amazon --returns")
        print("  Then → real Amazon mail: set an app password (docs/connect-gmail.md),")
        print("         export INBOX_IMAP_ACCOUNT='you@gmail.com', and re-run this check.")
    print()
    return 0


def _cmd_welcome() -> int:
    """Short, friendly landing when the CLI is run with no action."""
    print("""
  📦 Inbox Catalog — turn your order emails into a catalog of what you own.

  Try it right now, no setup, no mail, no credentials:
    python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply
    python3 -m inboxcatalog --profile amazon --returns

  See where you stand / how to connect real mail:
    python3 -m inboxcatalog doctor

  Full option list:
    python3 -m inboxcatalog --help
""")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inboxcatalog",
        description="Privacy-first engine that turns your purchase/shipment "
                    "emails into an image-searchable catalog of things you own.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--profile", default=None,
                   help="collection profile name (default: $INBOX_PROFILE or 'demo')")
    p.add_argument("--ingest", action="store_true",
                   help="ingest purchase/shipment emails (dry-run unless --apply)")
    p.add_argument("--fixtures", nargs="?", const="", default=None, metavar="DIR",
                   help="ingest offline .eml fixtures instead of a live mailbox "
                        "(no DIR = the bundled synthetic demo fixtures)")
    p.add_argument("--mbox", default=None, metavar="FILE",
                   help="ingest a single Google Takeout .mbox file (zero-credential "
                        "Tier-2 path); read-only, dry-run unless --apply")
    p.add_argument("--gmail", action="store_true",
                   help="force the read-only Gmail API source (default once you "
                        "have run `connect`)")
    p.add_argument("--imap", action="store_true",
                   help="force the read-only IMAP app-password source")
    p.add_argument("--apply", action="store_true",
                   help="commit changes (default is dry-run)")
    p.add_argument("--lookback", type=int, default=config.DEFAULT_LOOKBACK_DAYS,
                   metavar="N", help="how many days back to scan (default 365)")
    p.add_argument("--llm", action="store_true",
                   help="opt-in: use the LLM fallback for no-template emails "
                        "(cached; needs ANTHROPIC_API_KEY and the optional dep)")
    p.add_argument("--reindex", action="store_true",
                   help="(re)build the local CLIP image index (needs CLIP deps)")
    p.add_argument("--force", action="store_true",
                   help="with --reindex: re-embed every image, not just new ones")
    p.add_argument("--returns", action="store_true",
                   help="returns report: items still inside their return window "
                        "(most urgent first), expired, and already returned")
    p.add_argument("--triage", action="store_true",
                   help="group catalogued items by life zone with return-window "
                        "status, plus unrouted + spend-flag sections")
    p.add_argument("--mark", nargs=2, metavar=("ITEM", "STATE"), default=None,
                   help="set an item's return state (keep|return|evaluate|returned); "
                        "ITEM is an item id, order id, or name substring")
    p.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                   help="only include items ordered on/after this date "
                        "(applies to --returns/--triage/--export)")
    p.add_argument("--months", type=int, default=None, metavar="N",
                   help="convenience: only include the last N months of orders "
                        "(--since wins if both are given)")
    p.add_argument("--export", action="store_true",
                   help="dump the catalog as JSON (all fields + computed "
                        "return window) for agents/other tools to consume")
    p.add_argument("--stats", action="store_true", help="show catalog counts")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument("--top-k", type=int, default=5, metavar="K",
                   help="lookup: number of nearest items to return")
    p.add_argument("command", nargs="?",
                   choices=["lookup", "connect", "disconnect", "doctor"],
                   help="doctor (setup check) | lookup <image> | connect "
                        "(Gmail sign-in) | disconnect")
    p.add_argument("image", nargs="?", help="path to the query photo (for lookup)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = logutil.setup(debug=args.debug)
    log.info("data dir: %s (%s)", config.DATA_DIR, config.DATA_DIR_SOURCE)

    if args.command == "doctor":
        return _cmd_doctor(args, log)
    if args.command == "connect":
        return _cmd_connect(args, log)
    if args.command == "disconnect":
        return _cmd_disconnect(args, log)
    if args.command == "lookup":
        if not args.image:
            log.error("usage: python3 -m inboxcatalog lookup <image_path>")
            return 1
        return _cmd_lookup(args, log)
    if args.reindex:
        return _cmd_reindex(args, log)
    if args.ingest:
        return _cmd_ingest(args, log)
    if args.mark:
        return _cmd_mark(args, log)
    if args.returns:
        return _cmd_returns(args, log)
    if args.export:
        return _cmd_export(args, log)
    if args.triage:
        return _cmd_triage(args, log)
    if args.stats:
        return _cmd_stats(args, log)

    return _cmd_welcome()


if __name__ == "__main__":
    sys.exit(main())
