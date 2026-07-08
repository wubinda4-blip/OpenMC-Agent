"""Benchmark/reference-backed deterministic patch source (Phase 7D).

Loads structural patches (pin_map, axial_layers, axial_overlays, settings)
from benchmark reference files so that well-known benchmarks like VERA3
don't depend entirely on LLM output for structural facts.

Design constraints
------------------
* **No hardcoded benchmark facts in production code.**  All numbers come
  from JSON reference files loaded at runtime.
* **Test fixtures are the initial reference source.**  The
  ``tests/fixtures/vera3_patches/`` files serve as reference patches.
  Production use can point to ``data/benchmarks/`` or similar.
* **Reference patches must still pass validation.**  They are not trusted
  blindly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .patches import (
    AxialLayersPatch,
    AxialOverlaysPatch,
    PinMapPatch,
    SettingsPatch,
    parse_patch_content,
)


# Patch types that can be sourced from reference.
REFERENCE_PATCH_TYPES: frozenset[str] = frozenset({
    "pin_map", "axial_layers", "axial_overlays", "settings",
})

# Default fixture locations (test-only; production should use reference_path).
_FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "vera3_patches"

_BENCHMARK_VARIANT_FILES: dict[str, dict[str, str]] = {
    "VERA3": {
        "3A": "vera3_3a_patches.json",
        "3B": "vera3_3b_patches.json",
    },
}


def load_benchmark_reference(
    *,
    benchmark_id: str | None = None,
    variant: str | None = None,
    reference_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load benchmark reference patch data.

    Parameters
    ----------
    benchmark_id
        e.g. ``"VERA3"``.
    variant
        e.g. ``"3B"``.
    reference_path
        Explicit path to a reference file.  Overrides benchmark lookup.

    Returns
    -------
    dict | None
        The raw patch list dict (``{"patches": [...]}``), or ``None`` if
        no reference is available.
    """
    # Explicit path takes priority.
    if reference_path is not None:
        p = Path(reference_path)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    # Benchmark/variant lookup.
    if benchmark_id is None:
        return None

    variant_files = _BENCHMARK_VARIANT_FILES.get(benchmark_id.upper())
    if variant_files is None:
        return None

    # Try exact variant match, then fallback to any available.
    fname = variant_files.get(variant or "")
    if fname is None:
        # Try first available variant.
        if variant_files:
            fname = next(iter(variant_files.values()))
        else:
            return None

    ref_file = _FIXTURE_DIR / fname
    if not ref_file.is_file():
        return None

    try:
        return json.loads(ref_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_reference_patch(
    *,
    patch_type: str,
    reference: dict[str, Any],
    variant: str | None = None,
) -> BaseModel | None:
    """Build a single patch from reference data.

    Parameters
    ----------
    patch_type
        One of :data:`REFERENCE_PATCH_TYPES`.
    reference
        The raw reference dict (from :func:`load_benchmark_reference`).
    variant
        Optional variant filter (e.g. only return 3B patches).

    Returns
    -------
    BaseModel | None
        The parsed patch model, or ``None`` if not found in reference.
    """
    if patch_type not in REFERENCE_PATCH_TYPES:
        return None

    patches_list = reference.get("patches", [])
    if not isinstance(patches_list, list):
        return None

    for entry in patches_list:
        if not isinstance(entry, dict):
            continue
        if entry.get("patch_type") != patch_type:
            continue
        # Variant filter: if variant is specified and patch has a variant field,
        # check it matches (or is absent).
        if variant is not None:
            entry_variant = entry.get("variant")
            if entry_variant is not None and entry_variant != variant:
                continue
        try:
            return parse_patch_content(patch_type, entry)
        except Exception:
            continue

    return None


__all__ = [
    "REFERENCE_PATCH_TYPES",
    "load_benchmark_reference",
    "build_reference_patch",
]
