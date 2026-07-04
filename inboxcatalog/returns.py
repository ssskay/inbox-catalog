"""Return-decision layer: per-item keep/return/evaluate state + a return-window
clock computed from the dates the catalog already stores.

This module is domain-neutral: any profile that stores ``return_state`` and one
of (``return_by``, ``delivered_at``, ``purchased_at``) gets the layer for free.
The window is resolved in priority order:

  1. an explicit ``return_by`` date extracted from mail (authoritative), else
  2. ``delivered_at`` + the policy window, else
  3. ``purchased_at`` + the policy window (conservative fallback — windows
     usually start at delivery, so this only ever *under*-promises days left).

Every computation is logged with its inputs so a "why is this expired?" question
is answerable from the log alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from . import logutil

log = logutil.get("returns")

STATES = ("keep", "return", "evaluate", "returned")
DEFAULT_STATE = "evaluate"

# Policy window (days) applied when mail gave no explicit return-by date.
# Amazon's baseline is 30 days from delivery for most categories.
POLICY_WINDOW_DAYS = int(os.environ.get("INBOX_RETURN_WINDOW_DAYS", "30"))


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


@dataclass
class Window:
    """The resolved return-window for one item."""
    return_by: Optional[date]   # None = not computable (no usable date at all)
    days_left: Optional[int]    # negative = expired N days ago
    basis: str                  # explicit | delivered+policy | ordered+policy | unknown

    @property
    def returnable(self) -> bool:
        return self.days_left is not None and self.days_left >= 0

    @property
    def status(self) -> str:
        if self.days_left is None:
            return "unknown"
        return "returnable" if self.days_left >= 0 else "expired"


def window_for(item, today: Optional[date] = None,
               policy_days: Optional[int] = None) -> Window:
    """Compute the return window for one item row (dict or sqlite3.Row)."""
    today = today or date.today()
    policy = POLICY_WINDOW_DAYS if policy_days is None else policy_days

    get = (lambda k: item[k] if k in item.keys() else None) if hasattr(item, "keys") \
        else item.get
    explicit = _parse_date(get("return_by"))
    delivered = _parse_date(get("delivered_at"))
    ordered = _parse_date(get("purchased_at"))

    if explicit:
        rb, basis = explicit, "explicit"
    elif delivered:
        rb, basis = delivered + timedelta(days=policy), "delivered+policy"
    elif ordered:
        rb, basis = ordered + timedelta(days=policy), "ordered+policy"
    else:
        log.info("window: %r has no return_by/delivered_at/purchased_at -> unknown",
                 get("name"))
        return Window(None, None, "unknown")

    days_left = (rb - today).days
    log.info("window: %r basis=%s (return_by=%s delivered=%s ordered=%s "
             "policy=%dd today=%s) -> return_by=%s days_left=%d",
             get("name"), basis, explicit, delivered, ordered, policy, today,
             rb, days_left)
    return Window(rb, days_left, basis)


def _state(item) -> str:
    get = (lambda k: item[k] if k in item.keys() else None) if hasattr(item, "keys") \
        else item.get
    return get("return_state") or DEFAULT_STATE


def _fmt_price(item) -> str:
    get = (lambda k: item[k] if k in item.keys() else None) if hasattr(item, "keys") \
        else item.get
    price, cur, qty = get("price"), get("currency") or "", int(get("quantity") or 1)
    if price is None:
        return "?"
    total = price * qty
    qty_str = f" (x{qty})" if qty != 1 else ""
    return f"{total:.2f} {cur}".strip() + qty_str


def _clock(win: Window) -> str:
    if win.days_left is None:
        return "no dates — window unknown"
    if win.days_left < 0:
        return f"expired {-win.days_left}d ago ({win.return_by})"
    if win.days_left == 0:
        return f"LAST DAY today ({win.return_by})"
    return f"{win.days_left} days left (until {win.return_by})"


def render_returns(items, today: Optional[date] = None,
                   policy_days: Optional[int] = None) -> str:
    """The --returns report: items still inside their window sorted most-urgent
    first, `evaluate` clearly flagged, expired / already-returned separated out."""
    today = today or date.today()
    open_items, expired, returned, unknown = [], [], [], []
    for it in items:
        win = window_for(it, today, policy_days)
        st = _state(it)
        if st == "returned":
            returned.append((it, win))
        elif win.status == "returnable":
            open_items.append((it, win))
        elif win.status == "expired":
            expired.append((it, win))
        else:
            unknown.append((it, win))

    open_items.sort(key=lambda p: p[1].days_left)
    expired.sort(key=lambda p: p[1].days_left, reverse=True)  # freshest expiry first

    def line(it, win, show_clock=True) -> str:
        st = _state(it)
        flag = "  <-- EVALUATE: decide before the window closes!" if st == "evaluate" else ""
        clock = f"   ⏳ {_clock(win)}" if show_clock else ""
        get = (lambda k: it[k] if k in it.keys() else None) if hasattr(it, "keys") else it.get
        return (f"  [{get('id') or '-':>3}] {get('name') or '(unnamed)':40.40s} "
                f"{st:9s} {_fmt_price(it):>14s}{clock}{flag}")

    out = [f"==== Returns — {today} (policy window "
           f"{POLICY_WINDOW_DAYS if policy_days is None else policy_days}d) ===="]
    out.append(f"\nStill returnable ({len(open_items)}) — most urgent first:")
    if open_items:
        out += [line(it, w) for it, w in open_items]
        n_eval = sum(1 for it, _ in open_items if _state(it) == "evaluate")
        if n_eval:
            out.append(f"  ({n_eval} item(s) flagged `evaluate` — these need a decision)")
    else:
        out.append("  (none)")
    if unknown:
        out.append(f"\nWindow unknown ({len(unknown)}) — no usable dates:")
        out += [line(it, w, show_clock=False) for it, w in unknown]
    out.append(f"\nExpired ({len(expired)}):")
    out += [line(it, w) for it, w in expired] or ["  (none)"]
    out.append(f"\nAlready returned ({len(returned)}):")
    out += [line(it, w, show_clock=False) for it, w in returned] or ["  (none)"]
    return "\n".join(out)
