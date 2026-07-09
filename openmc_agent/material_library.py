"""Controlled nominal alloy composition library.

This module provides reactor-grade *structural alloy* nominal weight-fraction
compositions (Zircaloy-4, SS304, Inconel-718) and a small resolver that maps
aliases and pure-element approximations back to canonical alloy ids.

Scope and safety boundary
-------------------------
- These are **nominal engineering approximations** taken from publicly
  available materials-engineering handbooks (e.g. ASTM / ASM nominal ranges),
  not the official VERA benchmark material specifications.
- They are intentionally *replaceable*: the registry is a plain dict, and the
  constants are only initial defaults. Users can supply their own
  :class:`AlloyComposition` entries via :func:`get_alloy_composition` /
  :func:`register_alloy_composition`.
- They are NOT used to silently confirm benchmark constants. The composition
  policy layer (see :mod:`openmc_agent.material_policy`) decides when to apply
  them; application is always recorded in a material composition report and
  flagged with an ``materials.alloy_library_applied`` info issue.

Why this matters
----------------
Reducing Zircaloy-4 to pure Zr, SS304 to pure Fe, or Inconel-718 to pure Ni
removes real absorption from Sn/Cr/Ni/Nb/Mo/... and biases keff high. Replacing
those approximations with nominal compositions restores most of the lost
absorption and gives a quantifiable baseline that can be compared against a
pure-element baseline without claiming benchmark agreement.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel


class AlloyComposition(AgentBaseModel):
    """A nominal alloy composition entry.

    Attributes
    ----------
    alloy_id
        Canonical identifier (e.g. ``zircaloy4``).
    display_name
        Human-readable name (e.g. ``Zircaloy-4``).
    basis
        Composition basis. Currently only ``weight_frac`` is supported.
    elements
        Mapping of OpenMC natural-element symbol -> weight fraction in [0, 1].
        Must sum to 1.0 within :data:`WEIGHT_SUM_TOL`.
    density_g_cm3
        Optional nominal density; not required (densities come from the plan).
    source_note
        Human-readable provenance note. Must explicitly state that this is a
        nominal engineering approximation, not the final benchmark spec.
    approximation_level
        ``nominal`` for handbook midpoints, ``range_midpoint`` for mid of an
        allowed range, ``placeholder`` for clearly stub values.
    warnings
        Optional caveats carried into the material composition report.
    """

    alloy_id: str
    display_name: str
    basis: Literal["weight_frac"] = "weight_frac"
    elements: dict[str, float]
    density_g_cm3: float | None = None
    source_note: str
    approximation_level: Literal["nominal", "range_midpoint", "placeholder"] = "nominal"
    warnings: list[str] = Field(default_factory=list)


WEIGHT_SUM_TOL = 1e-6


# ---------------------------------------------------------------------------
# Nominal compositions.
#
# These values use the "balance" convention (e.g. Fe balance, Zr balance).
# ``_build_registry`` resolves the balance element to exactly 1 - sum(others)
# so the final dict sums to 1.0 within WEIGHT_SUM_TOL.
# ---------------------------------------------------------------------------

_ZIRCALOY4_MINOR: dict[str, float] = {
    "Sn": 0.0150,
    "Fe": 0.0021,
    "Cr": 0.0010,
    "O":  0.0012,
}
_ZIRCALOY4_BALANCE = "Zr"

_SS304_MINOR: dict[str, float] = {
    "Cr": 0.180,
    "Ni": 0.080,
    "Mn": 0.020,
    "Si": 0.010,
    "C":  0.0008,
    "P":  0.00045,
    "S":  0.00030,
}
_SS304_BALANCE = "Fe"

_INCONEL718_MINOR: dict[str, float] = {
    "Cr": 0.190,
    "Fe": 0.185,
    "Nb": 0.051,
    "Mo": 0.030,
    "Ti": 0.009,
    "Al": 0.005,
    "Co": 0.005,
}
_INCONEL718_BALANCE = "Ni"


def _with_balance(minor: dict[str, float], balance: str) -> dict[str, float]:
    """Return a copy of ``minor`` with the balance element set to 1 - sum."""
    out = dict(minor)
    total = sum(out.values())
    if total >= 1.0:
        raise ValueError(
            f"minor fractions already sum to {total!r} >= 1; cannot add balance "
            f"element {balance!r}"
        )
    out[balance] = 1.0 - total
    return out


_ZIRCALOY4_ELEMENTS = _with_balance(_ZIRCALOY4_MINOR, _ZIRCALOY4_BALANCE)
_SS304_ELEMENTS = _with_balance(_SS304_MINOR, _SS304_BALANCE)
_INCONEL718_ELEMENTS = _with_balance(_INCONEL718_MINOR, _INCONEL718_BALANCE)


_BASE_SOURCE_NOTE = (
    "Nominal engineering approximation (publicly available handbook midpoints); "
    "NOT the official VERA benchmark material specification. Replaceable."
)

ALLOY_COMPOSITIONS: dict[str, AlloyComposition] = {
    "zircaloy4": AlloyComposition(
        alloy_id="zircaloy4",
        display_name="Zircaloy-4",
        elements=_ZIRCALOY4_ELEMENTS,
        density_g_cm3=6.56,
        source_note=_BASE_SOURCE_NOTE,
        approximation_level="nominal",
        warnings=[
            "Nominal Zircaloy-4 composition; oxygen and Fe/Cr may vary by vendor.",
        ],
    ),
    "ss304": AlloyComposition(
        alloy_id="ss304",
        display_name="Stainless Steel 304",
        elements=_SS304_ELEMENTS,
        density_g_cm3=8.00,
        source_note=_BASE_SOURCE_NOTE,
        approximation_level="nominal",
        warnings=[
            "Nominal AISI 304 composition; C/N/Mo may vary by heat.",
        ],
    ),
    "inconel718": AlloyComposition(
        alloy_id="inconel718",
        display_name="Inconel 718",
        elements=_INCONEL718_ELEMENTS,
        density_g_cm3=8.19,
        source_note=_BASE_SOURCE_NOTE,
        approximation_level="nominal",
        warnings=[
            "Nominal Inconel-718 composition; Nb+Ta and Ti/Al may vary.",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # Zircaloy-4
    "zircaloy4": "zircaloy4",
    "zircaloy_4": "zircaloy4",
    "zircaloy-4": "zircaloy4",
    "zirc4": "zircaloy4",
    "zr4": "zircaloy4",
    "grid_zircaloy4": "zircaloy4",
    "grid_zircaloy_4": "zircaloy4",
    "spacer_zircaloy4": "zircaloy4",
    "spacer_zircaloy_4": "zircaloy4",
    "cladding_zircaloy4": "zircaloy4",
    "clad_zircaloy4": "zircaloy4",
    # SS304
    "ss304": "ss304",
    "ss_304": "ss304",
    "ss-304": "ss304",
    "stainless_steel_304": "ss304",
    "stainless304": "ss304",
    "stainless_304": "ss304",
    "stainlesssteel304": "ss304",
    "core_plate_ss304": "ss304",
    "core_plate_ss_304": "ss304",
    # Inconel-718
    "inconel718": "inconel718",
    "inconel_718": "inconel718",
    "inconel-718": "inconel718",
    "grid_inconel718": "inconel718",
    "grid_inconel_718": "inconel718",
    "spacer_inconel718": "inconel718",
    "spacer_inconel_718": "inconel718",
    "in718": "inconel718",
}

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_alias_key(name: str) -> str:
    """Lowercase, strip, collapse non-alphanumeric to nothing.

    The aliases above include hyphen / underscore variants explicitly so that
    callers can also normalize more aggressively. We collapse to alnum only so
    that ``ss-304`` and ``ss_304`` both reach ``ss304``.
    """
    return _NON_ALNUM_RE.sub("", name.strip().lower())


def canonical_alloy_id(name: str) -> str | None:
    """Return the canonical alloy id for ``name`` or ``None`` if unknown.

    Tries exact alias lookup first, then a normalized alnum-only lookup so that
    arbitrary prefixes/suffixes still resolve.
    """
    if not name:
        return None
    key = name.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    norm = _normalize_alias_key(name)
    if norm in _ALIASES:
        return _ALIASES[norm]
    # Fuzzy: if any alias starts with the normalized key or vice-versa and the
    # alias is a structural alloy, accept it. This keeps the resolver permissive
    # for names like ``zircaloy4_grid`` without enumerating every prefix.
    for alias_key, target in _ALIASES.items():
        a_norm = _normalize_alias_key(alias_key)
        if norm == a_norm:
            return target
        if len(norm) >= 4 and (norm.startswith(a_norm) or a_norm.startswith(norm)):
            return target
    return None


def get_alloy_composition(name: str) -> AlloyComposition | None:
    """Return the :class:`AlloyComposition` for ``name`` or ``None``."""
    cid = canonical_alloy_id(name)
    if cid is None:
        return None
    return ALLOY_COMPOSITIONS.get(cid)


def register_alloy_composition(entry: AlloyComposition) -> None:
    """Register or replace an alloy composition entry.

    Also registers a self-alias so :func:`canonical_alloy_id` finds it.
    """
    ALLOY_COMPOSITIONS[entry.alloy_id] = entry
    _ALIASES[entry.alloy_id.lower()] = entry.alloy_id


def normalize_weight_fractions(elements: dict[str, float]) -> dict[str, float]:
    """Normalize a weight-fraction dict so values sum to exactly 1.0.

    Accepts either 0-1 fractions (sum near 1) or 0-100 percentages (sum near
    100). Raises ``ValueError`` if the sum is wildly off, if any value is
    negative, or if the dict is empty.
    """
    if not elements:
        raise ValueError("cannot normalize an empty composition dict")
    if any(v < 0 for v in elements.values()):
        raise ValueError(f"negative weight fraction in {elements!r}")
    total = sum(elements.values())
    if total <= 0:
        raise ValueError(f"non-positive composition sum {total!r}")
    # If the values look like percentages (sum clearly above ~1.5), rescale.
    if total > 1.5:
        scale = 100.0
    else:
        scale = 1.0
    out = {k: v / scale for k, v in elements.items()}
    renorm = sum(out.values())
    if abs(renorm - 1.0) > 0.05:
        raise ValueError(
            f"composition sum {renorm!r} is too far from 1.0 after normalization"
        )
    # Final exact renormalization so tests can compare with tight tolerance.
    return {k: v / renorm for k, v in out.items()}


__all__ = [
    "AlloyComposition",
    "ALLOY_COMPOSITIONS",
    "WEIGHT_SUM_TOL",
    "canonical_alloy_id",
    "get_alloy_composition",
    "register_alloy_composition",
    "normalize_weight_fractions",
]
