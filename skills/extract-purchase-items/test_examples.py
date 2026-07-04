"""Conformance test for the extract-purchase-items golden examples.

Asserts every example's expected JSON (a) parses and (b) matches the engine's
LLM schema in ``inboxcatalog/profile.py`` (``_DEFAULT_LLM_PROMPT``), so the skill
and the engine can never silently drift apart. Pure stdlib — no pytest, no deps:

    python3 skills/extract-purchase-items/test_examples.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "examples")

# The engine's per-item schema. Keep in lockstep with _DEFAULT_LLM_PROMPT.
ITEM_KEYS = {"name", "maker", "price", "currency", "quantity", "order_id"}

# Personal-content guard: the shipped examples must stay generic and synthetic.
# The maintainer's private taxonomy names and signal terms may never leak into
# tracked example fixtures (they live only in an untracked local override).
PERSONAL_TERMS = (
    "hamster", "panda", "oxnard", "sandy", "howdy", "dexter", "auntie",
    "hamtaro", "cappy", "bijou", "maxwell", "pashmina", "elder ham",
    "chiikawa", "glowforge", "sublimation", "nendoroid", "love and deepspace",
    "honkai", "enamel pin", "htv", "cricut",
)


def _scan_personal_content() -> list[str]:
    """Fail if any tracked example leaks the maintainer's private taxonomy /
    signal terms — the examples must read as generic synthetic mail."""
    errs: list[str] = []
    for path in sorted(glob.glob(os.path.join(EXAMPLES, "*"))):
        with open(path, encoding="utf-8", errors="ignore") as fh:
            hay = fh.read().lower()
        hits = [t for t in PERSONAL_TERMS if t in hay]
        if hits:
            errs.append(f"{os.path.basename(path)} contains personal terms: {hits}")
    return errs


def _check(path: str) -> list[str]:
    errs: list[str] = []
    with open(path) as fh:
        data = json.load(fh)  # raises on invalid JSON
    if set(data) != {"items"}:
        errs.append(f"top-level keys {set(data)} != {{'items'}}")
    for i, item in enumerate(data.get("items", [])):
        if set(item) != ITEM_KEYS:
            errs.append(f"item[{i}] keys {set(item)} != {ITEM_KEYS}")
        if item.get("price") is not None and not isinstance(item["price"], (int, float)):
            errs.append(f"item[{i}] price is not a number: {item['price']!r}")
        if not (item.get("name") is None or isinstance(item["name"], str)):
            errs.append(f"item[{i}] name is not str|null")
        q = item.get("quantity")
        if not isinstance(q, int) or isinstance(q, bool) or q < 1:
            errs.append(f"item[{i}] quantity is not a positive int: {q!r}")
    return errs


def main() -> int:
    files = sorted(glob.glob(os.path.join(EXAMPLES, "*.expected.json")))
    if not files:
        print("FAIL: no example outputs found")
        return 1
    # Every example .eml should have a matching .expected.json and vice versa.
    emls = {os.path.basename(p)[:-4] for p in glob.glob(os.path.join(EXAMPLES, "*.eml"))}
    jsons = {os.path.basename(p)[: -len(".expected.json")] for p in files}
    if emls != jsons:
        print(f"FAIL: example pairing mismatch: only-eml={emls - jsons} only-json={jsons - emls}")
        return 1

    failed = False
    personal = _scan_personal_content()
    if personal:
        failed = True
        print("FAIL: personal content found in examples")
        for e in personal:
            print(f"     - {e}")

    for path in files:
        errs = _check(path)
        name = os.path.basename(path)
        if errs:
            failed = True
            print(f"FAIL {name}")
            for e in errs:
                print(f"     - {e}")
        else:
            with open(path) as fh:
                n = len(json.load(fh)["items"])
            print(f"PASS {name}  ({n} items)")
    print("\n" + ("FAILED" if failed else "OK — all examples conform to the engine schema"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
