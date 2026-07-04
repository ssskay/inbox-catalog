"""Profile registry.

Profiles register themselves by name. The engine resolves the active profile via
``INBOX_PROFILE`` (or the ``--profile`` CLI flag) and never imports a domain
module directly. To add a domain, drop a new module here that builds a
``CollectionProfile`` and calls :func:`register`.
"""
from __future__ import annotations

from ..profile import CollectionProfile

_REGISTRY: dict[str, CollectionProfile] = {}


def register(profile: CollectionProfile) -> CollectionProfile:
    _REGISTRY[profile.name] = profile
    return profile


def available() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def load(name: str) -> CollectionProfile:
    _ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise SystemExit(
            f"unknown profile {name!r}. Available: {', '.join(available()) or '(none)'}"
        )


_LOADED = False


def _ensure_loaded() -> None:
    """Import the bundled profile modules so they self-register. Add new
    profiles to this list (or replace with entry-point discovery)."""
    global _LOADED
    if _LOADED:
        return
    # Importing the module runs its module-level register() call. Referencing
    # the resulting profile here makes the import genuinely used (no noqa) and
    # asserts the self-registration side effect actually happened.
    from . import amazon, demo
    assert demo.DEMO_PROFILE.name in _REGISTRY
    assert amazon.AMAZON_PROFILE.name in _REGISTRY
    _LOADED = True
