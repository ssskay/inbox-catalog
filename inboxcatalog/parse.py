"""Email -> structured item rows.

Order of operations:
  1. Extract headers (from, subject, date) and bodies (text + html).
  2. Run the active profile's per-seller templates. First match wins.
  3. If no template matches, the caller may opt into a single cached LLM call.

Templates return item dicts with an ``image_url`` the orchestrator downloads.
This module is domain-neutral: it knows how to read an email and dispatch to
templates, but the *set* of templates comes from the active CollectionProfile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Optional

from . import logutil

if TYPE_CHECKING:
    from .profile import CollectionProfile

log = logutil.get("parse")


@dataclass
class EmailCtx:
    uid: str
    from_addr: str
    subject: str
    date_iso: Optional[str]
    text: str
    html: str


@dataclass
class ParseResult:
    items: list[dict] = field(default_factory=list)
    via: str = "none"          # template:<name> | llm | none
    matched_template: bool = False


# --- header / body extraction ---------------------------------------------

def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def header_from(msg: Message) -> str:
    return _decode(msg.get("From", "")).lower()


def header_subject(msg: Message) -> str:
    return _decode(msg.get("Subject", ""))


def header_date_iso(msg: Message) -> Optional[str]:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        return None


def _payload_text(part: Message) -> str:
    try:
        raw = part.get_payload(decode=True)
        # get_payload(decode=True) yields bytes (or None) at runtime, but its
        # type is a broad union; narrow to bytes so .decode() is well-typed and
        # any unexpected non-bytes payload is treated as empty rather than crashing.
        if not isinstance(raw, (bytes, bytearray)):
            return ""
        charset = part.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except Exception:
        return ""


def extract_bodies(msg: Message) -> tuple[str, str]:
    """Return (plain_text, html). Walks multipart; concatenates parts."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                text_parts.append(_payload_text(part))
            elif ctype == "text/html":
                html_parts.append(_payload_text(part))
    else:
        body = _payload_text(msg)
        if msg.get_content_type() == "text/html":
            html_parts.append(body)
        else:
            text_parts.append(body)
    return "\n".join(text_parts), "\n".join(html_parts)


_BLOCK_TAG_RE = re.compile(
    r"(?i)</?(p|div|br|tr|td|th|li|ul|ol|h[1-6]|table|section|header|footer)\b[^>]*>")


def html_to_text(html: str) -> str:
    """Cheap HTML -> text for the keyword gate and regexes. Block-level tags
    become newlines so field captures (e.g. ``[^\\n<]+``) stop at element
    boundaries instead of swallowing the rest of the receipt."""
    if not html:
        return ""
    txt = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    txt = _BLOCK_TAG_RE.sub("\n", txt)          # block boundaries -> newlines
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)      # drop remaining inline tags
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)        # collapse blank lines
    return txt.strip()


def build_ctx(uid: str, msg: Message) -> EmailCtx:
    plain, html = extract_bodies(msg)
    # Templates regex over `text`. Combine the plain part with the HTML-stripped
    # text so details that live only in the HTML body are still visible (many
    # shops put a bare "plain fallback" in text/plain and the real receipt in
    # text/html). `html` stays raw for image-URL extraction.
    html_text = html_to_text(html)
    combined = "\n".join(t for t in (plain, html_text) if t).strip()
    # Strip bare tracking URLs — marketplace plain-text is full of them and they
    # trip price/order regexes (e.g. "order" inside a click-tracking link).
    combined = re.sub(r"https?://\S+", " ", combined)
    combined = re.sub(r"[ \t]+", " ", combined)
    return EmailCtx(
        uid=uid,
        from_addr=header_from(msg),
        subject=header_subject(msg),
        date_iso=header_date_iso(msg),
        text=combined,
        html=html,
    )


# --- dispatch --------------------------------------------------------------

def dispatch(ctx: EmailCtx, profile: "CollectionProfile") -> ParseResult:
    """Run the profile's templates; first match wins. Returns ParseResult."""
    for tpl in profile.templates:
        if tpl.matches(ctx):
            try:
                items = tpl.parse(ctx)
            except Exception as exc:
                log.warning("template %s raised on uid=%s: %s", tpl.name, ctx.uid, exc)
                items = []
            if items:
                log.debug("uid=%s parsed via template:%s -> %d item(s)",
                          ctx.uid, tpl.name, len(items))
                return ParseResult(items=items, via=f"template:{tpl.name}",
                                   matched_template=True)
            # Matched the sender but extracted nothing — still mark matched so we
            # don't burn an LLM call on a known-but-empty layout unless desired.
            log.debug("uid=%s matched template:%s but extracted 0 items",
                      ctx.uid, tpl.name)
            return ParseResult(items=[], via=f"template:{tpl.name}",
                               matched_template=True)
    return ParseResult(items=[], via="none", matched_template=False)
