"""Product-image extraction + download.

Many email CDN links expire, so images are downloaded immediately during ingest,
stored on disk under ``data/images/<sha256>.<ext>``, and the DB keeps the path +
sha256. Failures are flagged (return None), never fatal. Domain-neutral.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urlparse

from . import config, logutil

log = logutil.get("images")

# <img src="..."> and bare CDN urls. Skip tracking pixels / spacers / icons.
_IMG_SRC_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
_SKIP_HINTS = ("spacer", "pixel", "tracking", "1x1", "logo", "icon", "facebook",
               "twitter", "instagram", "footer", "header", "divider", ".gif",
               "brand", "sprite", "badge", "stars", "rating", "email-assets",
               "/assets/", "banner", "social", "pstmrk", "/open?", "open?m=",
               "theme_images", "/theme/", "storefront", "/avatars/",
               "shopifycloud", "notifications")
_EXT_BY_CT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
}


def first_product_image_url(html: str, prefer_hosts: Optional[list[str]] = None
                            ) -> Optional[str]:
    """Pick the most product-looking image URL from email HTML.

    Heuristic: drop obvious chrome/tracking images; prefer URLs whose host
    matches a seller-CDN hint; otherwise take the first surviving candidate."""
    if not html:
        return None
    candidates: list[str] = []
    for m in _IMG_SRC_RE.finditer(html):
        url = m.group(1).strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        low = url.lower()
        if any(h in low for h in _SKIP_HINTS):
            continue
        candidates.append(url)
    if not candidates:
        return None
    if prefer_hosts:
        for url in candidates:
            if any(h in url.lower() for h in prefer_hosts):
                return url
    return candidates[0]


_GENERIC_IMG_RE = re.compile(
    r"https?://[^\s\"'<>)]+\.(?:jpe?g|png|webp)", re.I)


def best_product_image_url(html: str, prefer_hosts: Optional[list[str]] = None
                           ) -> Optional[str]:
    """Find the best real product photo in an order email's HTML.

    More thorough than ``first_product_image_url``: it also scans the raw HTML
    for product-looking image URLs (which appear in srcset/data-src/CSS as well
    as ``<img src>``). Returns None if nothing product-looking is found."""
    if not html:
        return None
    pick = first_product_image_url(html, prefer_hosts=prefer_hosts)
    if pick:
        return pick
    for m in _GENERIC_IMG_RE.finditer(html):
        url = m.group(0)
        if not any(h in url.lower() for h in _SKIP_HINTS):
            return url
    return None


# Shopify-style email images use size-transform suffixes (…_compact_cropped.jpg)
# that often 404 when fetched directly; the un-suffixed original resolves fine.
_SHOPIFY_XF = re.compile(
    r"_(?:compact_cropped|compact|grande|large|medium|small|pico|icon|thumb|"
    r"master|\d+x\d*|\d*x\d+)(?=\.(?:jpe?g|png|webp|gif)(?:\?|$))", re.I)


def _alt_url(url: str) -> Optional[str]:
    if "cdn.shopify.com" not in url:
        return None
    stripped = _SHOPIFY_XF.sub("", url)
    return stripped if stripped != url else None


def _looks_like_image(content: bytes) -> bool:
    """Sniff common image magic numbers (JPEG/PNG/GIF/WebP)."""
    head = content[:12]
    return (
        head.startswith(b"\xff\xd8\xff")              # JPEG
        or head.startswith(b"\x89PNG\r\n\x1a\n")      # PNG
        or head[:6] in (b"GIF87a", b"GIF89a")          # GIF
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")  # WebP
    )


def _ext_from(content_type: str, url: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _EXT_BY_CT:
        return _EXT_BY_CT[ct]
    m = re.search(r"\.(jpg|jpeg|png|webp|gif)(?:\?|$)", url, re.I)
    return f".{m.group(1).lower()}" if m else ".jpg"


def download(url: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Download an image URL now. Returns (path, sha256) or (None, None).

    Idempotent on content: identical bytes -> same sha -> same file reused.
    ``requests`` is imported lazily so a dry-run never requires it."""
    if not url:
        return None, None
    import html as _html
    url = _html.unescape(url)  # raw email HTML leaves &amp; in query strings
    # RFC 2606/6761 reserved hosts (.example/.invalid/.test) never resolve — the
    # offline demo fixtures use them, so skip quietly instead of emitting a
    # scary connection-failure WARNING during the zero-setup demo.
    host = (urlparse(url).hostname or "").lower()
    if host.endswith((".example", ".invalid", ".test", ".localhost")):
        log.debug("skipping reserved-host image (offline demo fixture): %s", url)
        return None, None
    try:
        import requests  # lazy
    except ImportError:
        log.warning("`requests` not installed — cannot download images "
                    "(pip3 install requests)")
        return None, None

    config.ensure_dirs()
    # Try the given URL, then a fallback with the Shopify size-transform stripped.
    candidates = [url] + ([alt] if (alt := _alt_url(url)) else [])
    for cand in candidates:
        try:
            resp = requests.get(cand, timeout=config.HTTP_TIMEOUT,
                                headers={"User-Agent": "inbox-catalog/1.0 (+image-archive)"})
            resp.raise_for_status()
            content = resp.content
            if not content:
                continue
            # Reject non-images (e.g. captcha/HTML served at an image URL).
            ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].lower()
            if not ctype.startswith("image/") and not _looks_like_image(content):
                log.warning("not an image (%s): %s", ctype or "no content-type", cand)
                continue
            sha = hashlib.sha256(content).hexdigest()
            ext = _ext_from(resp.headers.get("Content-Type", ""), cand)
            dest = config.IMAGES_DIR / f"{sha}{ext}"
            if not dest.exists():
                dest.write_bytes(content)
            return str(dest), sha
        except Exception as exc:
            log.warning("image download failed (%s): %s", type(exc).__name__, cand)
    return None, None
