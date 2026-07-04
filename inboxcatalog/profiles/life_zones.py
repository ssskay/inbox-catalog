"""Life-zone routing for the Amazon tracker.

Taxonomy source: ``references/life-zone-routing.md``. Each catalogued item gets
one primary zone (optional secondary) answering "which part of life is this
purchase for?", on top of its keep/return/evaluate state. The routing rules that
matter:

  * No positive signal -> ``unrouted``. Never guess.
  * ``fandom-fun`` is NOT a catch-all — positive signal only.
  * ``gifts`` overrides category whenever a gift signal is present.
  * The **spend flag** (finance) is a cross-cutting *flag*, not a zone:
    high-spend items (default > $75) and order clusters get flagged at view time.
  * Every decision is logged: matched signal, primary/secondary, confidence,
    and any override.

The default taxonomy below ships generic zones. Users can replace the whole
taxonomy with a local, untracked config file (see :func:`load_taxonomy` and
``references/life-zone-routing.md`` for the format).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .. import logutil

log = logutil.get("zones")

UNROUTED = "unrouted"

# Spend threshold (USD-ish, currency-naive on purpose) for the spend flag.
SPEND_FLAG_THRESHOLD = float(os.environ.get("INBOX_SPEND_FLAG", "75"))
# A "cluster" is >= CLUSTER_MIN_ORDERS distinct orders within CLUSTER_DAYS days.
CLUSTER_MIN_ORDERS = int(os.environ.get("INBOX_CLUSTER_MIN_ORDERS", "3"))
CLUSTER_DAYS = int(os.environ.get("INBOX_CLUSTER_DAYS", "7"))

# Where an optional taxonomy override is read from. Same "local, untracked"
# spirit as private profiles (see .gitignore). Absent file -> built-in defaults.
_HERE = Path(__file__).resolve().parent
ZONES_CONFIG_PATH = Path(os.environ.get(
    "INBOX_ZONES_CONFIG", _HERE / "zones.local.json"))


class ZonesConfigError(ValueError):
    """A taxonomy override file exists but is malformed. Raised loudly — the
    engine never silently falls back to defaults when the user meant to override."""


# --- the default (generic, shipped) taxonomy --------------------------------
# A plain-dict spec, validated through the exact same path as a user override,
# so the shipped defaults can't drift from what an override is allowed to say.
DEFAULT_SPEC = {
    "zones": {
        "crafting": {
            "label": "🔨 Crafting — maker & craft supplies",
            "signals": [
                r"craft vinyl", r"\bvinyl\b", r"craft paint", r"acrylic paint",
                r"acrylic sheet", r"epoxy", r"\bresin\b", r"basswood", r"plywood",
                r"\bblanks?\b", r"\byarn\b", r"knitting", r"crochet", r"\bbeads?\b",
                r"button[- ]?maker", r"pin backs?", r"keychain hardware", r"lanyard",
                r"blank tumbler", r"blank mug",
            ],
        },
        "home-org": {
            "label": "🧺 Home & Org — storage, cleaning & mailing supplies",
            "signals": [
                r"storage", r"organizer", r"\bbins?\b", r"shelv", r"\bshelf\b",
                r"label maker", r"thermal label", r"\blabels?\b", r"poly mailer",
                r"bubble mailer", r"\bmailers?\b", r"packing tape", r"kraft box",
                r"command hook", r"cleaning", r"trash bag", r"drawer",
            ],
        },
        "fandom-fun": {
            "label": "🌟 Fandom & Fun — collecting, hobbies & self-care",
            "signals": [
                r"\banime\b", r"\bfigures?\b", r"plush", r"\bpins?\b", r"blind box",
                r"\bmanga\b", r"collectible", r"wall scroll", r"trading cards?",
                r"\bkeycap\b", r"skincare", r"candle",
            ],
        },
        "resale": {
            "label": "🏷️ Resale — inventory & reselling logistics",
            "signals": [
                r"\bbulk\b", r"lot of \d+", r"wholesale", r"display board",
                r"shipping scale", r"price label", r"barcode",
            ],
        },
        "language-learning": {
            "label": "📖 Language Learning — study materials",
            "signals": [
                r"\bspanish\b", r"\bfrench\b", r"\bjapanese\b", r"\bgerman\b",
                r"\bitalian\b", r"phrasebook", r"grammar workbook", r"graded reader",
                r"\bvocabulary\b", r"language learning", r"flash ?cards?",
            ],
        },
        "fitness": {
            "label": "🏃 Fitness — movement & nutrition",
            "signals": [
                r"protein", r"creatine", r"resistance band", r"dumbbell", r"yoga mat",
                r"foam roller", r"workout", r"water bottle",
            ],
        },
        "dev-hardware": {
            "label": "🔌 Dev Hardware — electronics & tinkering",
            "signals": [
                r"raspberry pi", r"arduino", r"\bsensors?\b", r"breadboard",
                r"microcontroller", r"\bgpio\b", r"jumper wire", r"\besp32\b",
            ],
        },
        "work-office": {
            "label": "💼 Work & Office — desk & job equipment",
            "signals": [
                r"mechanical keyboard", r"\bkeyboard\b", r"monitor arm", r"\bmonitor\b",
                r"usb-c dock", r"\bdock\b", r"ergonomic", r"standing desk",
                r"laptop stand", r"webcam", r"desk mat",
            ],
        },
        "trips-events": {
            "label": "🧳 Trips & Events — travel & event logistics",
            "signals": [
                r"\btravel\b", r"luggage", r"packing cube", r"travel adapter",
                r"neck pillow", r"passport", r"\btsa\b", r"portable charger",
            ],
        },
        "gifts": {
            "label": "🎁 Gifts — presents for other people",
            "signals": [],   # gift zone is override-only (detected by gift signal)
        },
        "content-gear": {
            "label": "🎥 Content Gear — creator equipment",
            "signals": [
                r"ring light", r"softbox", r"tripod", r"backdrop", r"microphone",
                r"flatlay",
            ],
        },
        "academic": {
            "label": "🎓 Academic — school & conference supplies",
            "signals": [
                r"poster tube", r"presentation clicker", r"badge holder",
                r"lab notebook",
            ],
        },
    },
    # Priority order for keyword matching. Specific/raw-material zones come before
    # fandom-fun so "pin backs" is Crafting (supply) while a bare "pin" stays
    # fandom-fun. ``gifts`` is override-only and is intentionally absent here.
    "priority": [
        "crafting", "language-learning", "dev-hardware", "content-gear",
        "academic", "fitness", "trips-events", "work-office", "home-org",
        "fandom-fun",
    ],
    # Zones whose goods are plausibly resale inventory when bought in quantity —
    # the resale tiebreaker only steals from these (a bulk order of trash bags is
    # still Home & Org).
    "resalable": ["crafting", "home-org", "fandom-fun"],
    "resale_zone": "resale",
    "gift_zone": "gifts",
    "bulk_quantity": 10,
    "gift_regex": (r"gift receipt|this (?:order|item) is a gift|gift options?"
                   r" (?:were|was) (?:selected|included)"),
}


class Taxonomy:
    """A validated, ready-to-route taxonomy: zone labels, compiled signal
    regexes, priority order, and the gift/resale override wiring."""

    __slots__ = ("labels", "signals", "priority", "resalable", "resale_zone",
                 "gift_zone", "gift_re", "bulk_qty", "source")

    def __init__(self, labels, signals, priority, resalable, resale_zone,
                 gift_zone, gift_re, bulk_qty, source):
        self.labels = labels
        self.signals = signals
        self.priority = priority
        self.resalable = resalable
        self.resale_zone = resale_zone
        self.gift_zone = gift_zone
        self.gift_re = gift_re
        self.bulk_qty = bulk_qty
        self.source = source


def _fail(source: str, msg: str) -> "ZonesConfigError":
    return ZonesConfigError(
        f"taxonomy config ({source}) is invalid: {msg}\n"
        f"See references/life-zone-routing.md for the config-file format.")


def _compile(source: str, where: str, pattern) -> "re.Pattern[str]":
    if not isinstance(pattern, str):
        raise _fail(source, f"{where}: signal {pattern!r} is not a string")
    try:
        return re.compile(pattern, re.I)
    except re.error as exc:
        raise _fail(source, f"{where}: signal {pattern!r} is not a valid regex ({exc})")


def _build(spec: dict, source: str) -> Taxonomy:
    """Validate a taxonomy spec (dict) and compile it into a :class:`Taxonomy`.

    Shared by the shipped defaults and any user override so both are held to the
    exact same rules. Raises :class:`ZonesConfigError` with a pointed message on
    anything malformed — it never quietly repairs or drops a bad field."""
    zones = spec.get("zones")
    if not isinstance(zones, dict) or not zones:
        raise _fail(source, "'zones' must be a non-empty object of zone-id -> {label, signals}")
    if UNROUTED in zones:
        raise _fail(source, f"{UNROUTED!r} is reserved and cannot be redefined as a zone")

    labels: dict[str, str] = {}
    signals: dict[str, list] = {}
    for zid, zdef in zones.items():
        if not isinstance(zid, str) or not zid:
            raise _fail(source, f"zone id {zid!r} must be a non-empty string")
        if not isinstance(zdef, dict):
            raise _fail(source, f"zone {zid!r} must be an object with 'label' and 'signals'")
        label = zdef.get("label")
        if not isinstance(label, str) or not label:
            raise _fail(source, f"zone {zid!r} needs a non-empty 'label' string")
        raw_signals = zdef.get("signals", [])
        if not isinstance(raw_signals, list):
            raise _fail(source, f"zone {zid!r}: 'signals' must be a list of regex strings")
        labels[zid] = label
        signals[zid] = [_compile(source, f"zone {zid!r}", p) for p in raw_signals]
    labels.setdefault(UNROUTED, "unrouted — needs your call")

    def _zone_list(key, default):
        val = spec.get(key, default)
        if not isinstance(val, list) or not all(isinstance(z, str) for z in val):
            raise _fail(source, f"'{key}' must be a list of zone ids")
        for z in val:
            if z not in zones:
                raise _fail(source, f"'{key}' names {z!r}, which is not a defined zone")
        return val

    priority = tuple(_zone_list("priority", list(zones)))
    resalable = frozenset(_zone_list("resalable", []))

    def _zone_id(key, default):
        # Explicitly set -> must name a real zone. Left unset -> use the generic
        # default only if that zone exists in this taxonomy, else disable the
        # feature (None) rather than force the user to define a zone they may
        # not want.
        if key in spec:
            z = spec[key]
            if z is not None and z not in zones:
                raise _fail(source, f"'{key}' is {z!r}, which is not a defined zone "
                                    f"(set '{key}' to one of: {', '.join(zones)})")
            return z
        return default if default in zones else None

    resale_zone = _zone_id("resale_zone", "resale")
    gift_zone = _zone_id("gift_zone", "gifts")

    bulk_qty = spec.get("bulk_quantity", 10)
    if not isinstance(bulk_qty, int) or isinstance(bulk_qty, bool) or bulk_qty < 1:
        raise _fail(source, f"'bulk_quantity' must be a positive integer, got {bulk_qty!r}")

    gift_re = _compile(source, "gift_regex", spec.get("gift_regex", DEFAULT_SPEC["gift_regex"]))

    return Taxonomy(labels=labels, signals=signals, priority=priority,
                    resalable=resalable, resale_zone=resale_zone,
                    gift_zone=gift_zone, gift_re=gift_re, bulk_qty=bulk_qty,
                    source=source)


def load_taxonomy(path: Optional[Path] = None) -> Taxonomy:
    """Return the active taxonomy. With no override file present, the built-in
    generic defaults. When ``path`` (default: ``INBOX_ZONES_CONFIG`` or
    ``profiles/zones.local.json``) exists, its JSON overrides the defaults
    key-by-key at the top level; providing ``zones`` replaces the zone set
    wholesale. A malformed file raises :class:`ZonesConfigError` — never a
    silent fallback."""
    path = ZONES_CONFIG_PATH if path is None else path
    if not path.exists():
        return _build(DEFAULT_SPEC, source="built-in defaults")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _fail(str(path), f"could not read the file ({exc})")
    try:
        override = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _fail(str(path), f"not valid JSON ({exc})")
    if not isinstance(override, dict):
        raise _fail(str(path), "top level must be a JSON object")
    if "zones" in override:
        # Replacing the zone set wholesale: don't inherit the generic defaults'
        # zone-dependent fields (they'd name zones this taxonomy no longer has).
        # Only the zone-independent knobs fall back to defaults when unset; the
        # rest are re-derived from the new zones inside _build.
        spec = dict(override)
        for k in ("bulk_quantity", "gift_regex"):
            spec.setdefault(k, DEFAULT_SPEC[k])
    else:
        # Tweaking the shipped taxonomy in place: merge over the defaults.
        spec = dict(DEFAULT_SPEC)
        spec.update(override)
    tax = _build(spec, source=str(path))
    log.info("loaded taxonomy override from %s (%d zones)", path, len(tax.signals))
    return tax


# The taxonomy in force for this process. Import-time load means a malformed
# override fails the run loudly and immediately, which is the intended contract.
TAXONOMY = load_taxonomy()

# Back-compat: some callers/tests read the default label map directly.
ZONE_LABELS = TAXONOMY.labels


def _tax(taxonomy: Optional[Taxonomy]) -> Taxonomy:
    return TAXONOMY if taxonomy is None else taxonomy


def detect_gift(text: str, taxonomy: Optional[Taxonomy] = None) -> Optional[str]:
    """Gift signal from the email body (gift receipt / explicit gift option).
    Returns the matched phrase, or None."""
    m = _tax(taxonomy).gift_re.search(text or "")
    return m.group(0) if m else None


def _match(taxonomy: Taxonomy, zone: str, hay: str) -> Optional[str]:
    for pat in taxonomy.signals.get(zone, ()):  # gift zone has none
        m = pat.search(hay)
        if m:
            return m.group(0)
    return None


def route(item: dict, taxonomy: Optional[Taxonomy] = None) -> dict:
    """Assign zone fields to one item dict (mutates and returns it).

    Sets: zone, zone_secondary, zone_signal, zone_confidence.
    """
    tax = _tax(taxonomy)
    name = (item.get("name") or "")
    qty = int(item.get("quantity") or 1)

    # Rule: gift override — a gift signal beats the item's own category.
    if item.get("is_gift") and tax.gift_zone:
        item.update(zone=tax.gift_zone, zone_secondary=None,
                    zone_signal=f"gift override: {item['is_gift']}",
                    zone_confidence="high")
        log.info("route: %r -> %s (OVERRIDE, signal=%r)", name, tax.gift_zone,
                 item["is_gift"])
        return item

    hits: list[tuple[str, str]] = []
    for zone in tax.priority:
        sig = _match(tax, zone, name)
        if sig:
            hits.append((zone, sig))

    # Rule: resale tiebreaker — bulk quantities / explicit resale signals turn a
    # resalable category into inventory; the category zone drops to secondary.
    resale_sig = _match(tax, tax.resale_zone, name) if tax.resale_zone else None
    bulk = tax.resale_zone and (
        resale_sig or (qty >= tax.bulk_qty and (not hits or hits[0][0] in tax.resalable)))
    if bulk and (resale_sig or hits):
        secondary = hits[0][0] if hits else None
        sig = resale_sig or f"quantity {qty} >= {tax.bulk_qty}"
        item.update(zone=tax.resale_zone, zone_secondary=secondary,
                    zone_signal=str(sig), zone_confidence="high" if resale_sig else "medium")
        log.info("route: %r -> %s (bulk/resale, signal=%r, secondary=%s)",
                 name, tax.resale_zone, sig, secondary)
        return item

    if not hits:
        # Rule: no positive signal -> unrouted. Never guess, and never let
        # fandom-fun absorb the uncertain ones.
        item.update(zone=UNROUTED, zone_secondary=None, zone_signal=None,
                    zone_confidence=None)
        log.info("route: %r -> unrouted (no signal)", name)
        return item

    primary, sig = hits[0]
    secondary = hits[1][0] if len(hits) > 1 else None
    confidence = "high" if (len(sig) > 6 or len(hits) > 1) else "medium"
    item.update(zone=primary, zone_secondary=secondary, zone_signal=sig,
                zone_confidence=confidence)
    log.info("route: %r -> %s (signal=%r, secondary=%s, confidence=%s)",
             name, primary, sig, secondary, confidence)
    return item


# --- spend flags: cross-cutting finance flags (view-time, not a zone) -------

def spend_flags(items, threshold: Optional[float] = None) -> dict:
    """Return {'high_spend': [items], 'clusters': [(start_date, n_orders)]}.

    High spend = line total (price x quantity) over the threshold.
    Cluster = >= CLUSTER_MIN_ORDERS distinct orders inside CLUSTER_DAYS days.
    """
    threshold = SPEND_FLAG_THRESHOLD if threshold is None else threshold
    get = lambda it, k: (it[k] if k in it.keys() else None) if hasattr(it, "keys") \
        else it.get(k)
    high = []
    order_dates: dict[str, str] = {}
    for it in items:
        price, qty = get(it, "price"), int(get(it, "quantity") or 1)
        if price is not None and price * qty > threshold:
            high.append(it)
            log.info("spend-flag: %r flagged high-spend (%.2f x %d > %.2f)",
                     get(it, "name"), price, qty, threshold)
        oid, day = get(it, "order_id"), get(it, "purchased_at")
        if oid and day:
            order_dates.setdefault(oid, day)

    clusters = []
    days = []
    for oid, day in order_dates.items():
        try:
            days.append(datetime.fromisoformat(day).date())
        except ValueError:
            continue
    days.sort()
    i = 0
    for j in range(len(days)):
        while days[j] - days[i] > timedelta(days=CLUSTER_DAYS):
            i += 1
        n = j - i + 1
        if n >= CLUSTER_MIN_ORDERS:
            clusters.append((days[i].isoformat(), n))
            log.info("spend-flag: cluster flagged — %d orders within %dd of %s",
                     n, CLUSTER_DAYS, days[i])
    # keep only the largest window per start date
    dedup = {}
    for start, n in clusters:
        dedup[start] = max(n, dedup.get(start, 0))
    return {"high_spend": high, "clusters": sorted(dedup.items())}


# --- the --triage view -------------------------------------------------------

def render_triage(items, today=None, policy_days=None,
                  spend_threshold: Optional[float] = None,
                  taxonomy: Optional[Taxonomy] = None) -> str:
    """Group catalogued items by life zone, each zone sorted by return-window
    days-left ascending, with dedicated `unrouted` and spend-flag sections."""
    from ..returns import window_for  # core layer; safe one-way import

    tax = _tax(taxonomy)
    labels = tax.labels
    get = lambda it, k: (it[k] if k in it.keys() else None) if hasattr(it, "keys") \
        else it.get(k)
    by_zone: dict[str, list] = {}
    for it in items:
        win = window_for(it, today, policy_days)
        by_zone.setdefault(get(it, "zone") or UNROUTED, []).append((it, win))

    def sort_key(pair):
        win = pair[1]
        return win.days_left if win.days_left is not None else 10**6
    for pairs in by_zone.values():
        pairs.sort(key=sort_key)

    def clock(win) -> str:
        if win.days_left is None:
            return "window unknown"
        if win.days_left < 0:
            return f"(expired {win.return_by})"
        return f"⏳ {win.days_left} days left"

    out = []
    # Known zones first (in taxonomy order), then any stored zone the current
    # taxonomy no longer defines (e.g. a legacy value), so nothing is dropped.
    ordered = [z for z in labels if z != UNROUTED and z in by_zone]
    ordered += [z for z in by_zone if z != UNROUTED and z not in labels]
    for zone in ordered:
        pairs = by_zone[zone]
        n_ret = sum(1 for it, w in pairs
                    if w.days_left is not None and w.days_left >= 0
                    and get(it, "return_state") != "returned")
        out.append(f"{labels.get(zone, zone)} ({len(pairs)} item(s) · "
                   f"{n_ret} still returnable)")
        for it, win in pairs:
            state = get(it, "return_state") or "evaluate"
            marker = "   ← evaluate!" if state == "evaluate" else ""
            out.append(f"  • {get(it, 'name') or '(unnamed)':42.42s} "
                       f"{state:9s} {clock(win)}{marker}")
    if UNROUTED in by_zone:
        pairs = by_zone[UNROUTED]
        out.append(f"\n{labels[UNROUTED].split(' — ')[0]} "
                   f"({len(pairs)} item(s)) — needs your call:")
        for it, win in pairs:
            state = get(it, "return_state") or "evaluate"
            out.append(f"  • {get(it, 'name') or '(unnamed)':42.42s} "
                       f"{state:9s} {clock(win)}")
    flags = spend_flags(items, spend_threshold)
    out.append("")
    thr = SPEND_FLAG_THRESHOLD if spend_threshold is None else spend_threshold
    if flags["high_spend"] or flags["clusters"]:
        parts = []
        if flags["high_spend"]:
            names = ", ".join(f"{get(it, 'name')} "
                              f"(${(get(it, 'price') or 0) * int(get(it, 'quantity') or 1):.2f})"
                              for it in flags["high_spend"])
            parts.append(f"{len(flags['high_spend'])} item(s) over ${thr:.0f}: {names}")
        for start, n in flags["clusters"]:
            parts.append(f"{n} orders within {CLUSTER_DAYS} days of {start}")
        out.append("💰 Spend flags: " + " · ".join(parts))
    else:
        out.append(f"💰 Spend flags: none (threshold ${thr:.0f})")
    return "\n".join(out)
