"""Inbox Catalog — a privacy-first engine that turns your own purchase and
shipment-confirmation emails into a structured, image-searchable catalog of the
physical things you own.

Read-only IMAP, secrets never written to disk, image embeddings computed locally
with CLIP, every write dry-run-gated by default. The collection *domain* is a
pluggable :class:`~inboxcatalog.profile.CollectionProfile`.
"""
from __future__ import annotations

__version__ = "0.1.1"
