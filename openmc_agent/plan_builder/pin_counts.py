"""Deterministic pin-map count helpers for incremental plan building."""

from __future__ import annotations

from collections import Counter


_UNIVERSE_ID_TO_ROLE: tuple[tuple[str, str], ...] = (
    ("fuel_pin_universe", "fuel_pin"),
    ("fuel_pin", "fuel_pin"),
    ("guide_tube_universe", "guide_tube"),
    ("guide_tube", "guide_tube"),
    ("instrument_tube_universe", "instrument_tube"),
    ("instrument_tube", "instrument_tube"),
    ("pyrex_rod_universe", "pyrex_rod"),
    ("pyrex_rod", "pyrex_rod"),
    ("thimble_plug_universe", "thimble_plug"),
    ("thimble_plug", "thimble_plug"),
)

_KNOWN_ROLES: tuple[str, ...] = (
    "fuel_pin",
    "guide_tube",
    "instrument_tube",
    "pyrex_rod",
    "thimble_plug",
)


def role_for_universe_id(universe_id: str) -> str:
    """Return a stable pin role for a universe id."""
    normalized = universe_id.strip().lower()
    for token, role in _UNIVERSE_ID_TO_ROLE:
        if normalized == token or normalized.endswith(f"_{token}"):
            return role
    return universe_id


def compute_pin_map_actual_counts(expanded_pattern: list[list[str]]) -> dict[str, int]:
    """Count pin roles in an expanded universe pattern."""
    counts: Counter[str] = Counter()
    for row in expanded_pattern:
        for universe_id in row:
            counts[role_for_universe_id(str(universe_id))] += 1
    for role in _KNOWN_ROLES:
        counts.setdefault(role, 0)
    return dict(counts)


def compute_pin_role_counts(
    expanded_pattern: list[list[str]],
    universe_kind_by_id: dict[str, str],
) -> dict[str, int]:
    """Count pin roles using an explicit universe-id to kind mapping."""
    counts: Counter[str] = Counter()
    for row in expanded_pattern:
        for universe_id in row:
            uid = str(universe_id)
            counts[universe_kind_by_id.get(uid, role_for_universe_id(uid))] += 1
    for role in _KNOWN_ROLES:
        counts.setdefault(role, 0)
    return dict(counts)


__all__ = [
    "compute_pin_map_actual_counts",
    "compute_pin_role_counts",
    "role_for_universe_id",
]
