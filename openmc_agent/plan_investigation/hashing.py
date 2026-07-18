"""Canonical hashing primitives for plan investigation artifacts.

Reuses the canonical JSON serializer from
:mod:`openmc_agent.plan_builder.closed_loop.fingerprints` so every hash in
this package is consistent with the rest of the plan closed-loop protocol.

All hashes are SHA-256 over canonical JSON (sorted keys, ``ensure_ascii=False``,
``allow_nan=False``).  Python's built-in ``hash()`` is never used.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

from openmc_agent.plan_builder.closed_loop.fingerprints import canonical_json_dumps

__all__ = [
    "canonical_json_dumps",
    "sha256_hex",
    "content_hash",
    "short_id",
    "stable_short_id",
]


def sha256_hex(payload: str) -> str:
    """SHA-256 hex digest of a UTF-8 encoded string."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def content_hash(value: Any) -> str:
    """SHA-256 hex digest over the canonical JSON form of ``value``."""
    return sha256_hex(canonical_json_dumps(value))


def short_id(prefix: str, value: Any, *, length: int = 16) -> str:
    """Return ``f"{prefix}_{short}"`` where ``short`` is the first hex chars
    of :func:`content_hash` over ``value``.

    The prefix MUST be non-empty and match ``[a-z][a-z0-9_]*``.  ``length``
    defaults to 16 hex characters (64 bits of collision resistance), matching
    the rest of the plan closed-loop fingerprints.
    """
    if not prefix:
        raise ValueError("id prefix must be non-empty")
    return f"{prefix}_{content_hash(value)[:length]}"


def stable_short_id(prefix: str, payload: dict[str, Any], *, length: int = 16) -> str:
    """Variant of :func:`short_id` that accepts an already-prepared mapping
    and hashes it deterministically.  Keys and values must be JSON-compatible.
    """
    return short_id(prefix, payload, length=length)


def normalize_for_hash(text: str) -> str:
    """NFC-normalize a string.  Exposed for symmetry with source normalizer."""
    return unicodedata.normalize("NFC", text)
