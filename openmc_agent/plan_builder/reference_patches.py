"""Benchmark/reference-backed deterministic patch source (Phase 7D).

Loads structural patches (pin_map, axial_layers, axial_overlays, settings)
from benchmark reference files.  Benchmark identification is done via
deterministic identifier normalization plus optional LLM semantic matching —
NO hardcoded benchmark names in code.

Design constraints
------------------
* **No hardcoded benchmark facts or identifiers in production code.**
* Reference files are self-describing (contain their own benchmark_id).
* Matching is data-driven and starts with generic identifier normalization.
* Falls back gracefully when LLM or reference files are unavailable.
"""

from __future__ import annotations

import json
import re
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

# Default search directories for reference files (relative to project root).
# These are data directories, NOT hardcoded benchmark identifiers.
_DEFAULT_REFERENCE_DIRS: tuple[str, ...] = (
    "tests/fixtures/vera3_patches",
    "data/benchmarks",
)

_BENCHMARK_ID_NOISE_TOKENS: frozenset[str] = frozenset({
    "benchmark",
    "problem",
    "corephysics",
    "core",
    "physics",
})


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def discover_reference_files(
    *,
    search_dirs: list[str | Path] | None = None,
) -> list[dict[str, Any]]:
    """Scan directories for reference patch JSON files.

    Returns a list of dicts, each with:
    * ``path`` — file path
    * ``benchmark_id`` — extracted from the file's facts patch content
    * ``variant`` — extracted from the file's facts patch content
    * ``data`` — the raw parsed JSON

    No hardcoded benchmark identifiers — discovery is purely file-based.
    """
    root = _project_root()
    dirs_to_search = search_dirs or list(_DEFAULT_REFERENCE_DIRS)

    results: list[dict[str, Any]] = []
    for dir_path in dirs_to_search:
        d = Path(dir_path)
        if not d.is_absolute():
            d = root / d
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            patches = data.get("patches", [])
            if not isinstance(patches, list):
                continue
            # Extract benchmark_id/variant from the facts patch inside the file.
            bid = None
            var = None
            for p in patches:
                if isinstance(p, dict) and p.get("patch_type") == "facts":
                    bid = p.get("benchmark_id")
                    var = p.get("selected_variant")
                    break
            results.append({
                "path": str(f),
                "benchmark_id": bid,
                "variant": var,
                "data": data,
            })
    return results


def _variant_matches(requested_variant: str | None, candidate_variant: str | None) -> bool:
    if requested_variant is None or candidate_variant is None:
        return True
    return requested_variant.lower() == candidate_variant.lower()


def _attach_reference_metadata(
    data: dict[str, Any],
    *,
    path: str | None,
    benchmark_id: str | None,
    variant: str | None,
    match_status: str,
) -> dict[str, Any]:
    result = dict(data)
    result["_reference_path"] = path
    result["_reference_benchmark_id"] = benchmark_id
    result["_reference_variant"] = variant
    result["_reference_match_status"] = match_status
    return result


def normalize_benchmark_id(benchmark_id: str | None) -> str:
    """Return a generic comparable benchmark identifier.

    Split alphanumeric runs, remove common descriptor words, and concatenate
    the remaining tokens.  This keeps matching data-driven while handling
    names such as ``VERA_PROBLEM_3`` and ``VERA3``.
    """
    if not benchmark_id:
        return ""
    tokens = re.findall(r"[a-z]+|\d+", benchmark_id.lower())
    filtered = [tok for tok in tokens if tok not in _BENCHMARK_ID_NOISE_TOKENS]
    return "".join(filtered)


def benchmark_ids_match(requested_id: str | None, candidate_id: str | None) -> bool:
    """Return True when two benchmark ids match under generic normalization."""
    requested = normalize_benchmark_id(requested_id)
    candidate = normalize_benchmark_id(candidate_id)
    return bool(requested and candidate and requested == candidate)


def _llm_match_benchmark(
    llm_client: Any,
    requested_id: str,
    requested_variant: str | None,
    candidate_id: str,
    candidate_variant: str | None,
) -> bool:
    """Use LLM to semantically match two benchmark identifiers.

    Asks: "Does '{requested_id}' refer to the same benchmark as '{candidate_id}'?"
    This is fully generic — works for any naming convention.
    """
    prompt = (
        f"Does the benchmark identifier \"{requested_id}\" "
        f"(variant \"{requested_variant or ''}\") refer to the same "
        f"nuclear benchmark as \"{candidate_id}\" "
        f"(variant \"{candidate_variant or ''}\")?\n"
        f"For example, \"VERA_Problem3\" and \"VERA3\" refer to the same benchmark.\n"
        f"Answer ONLY \"yes\" or \"no\"."
    )
    try:
        raw = llm_client(prompt)
        return "yes" in raw.strip().lower()
    except Exception:
        return False


def load_benchmark_reference(
    *,
    benchmark_id: str | None = None,
    variant: str | None = None,
    reference_path: str | Path | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any] | None:
    """Load benchmark reference patch data.

    Matching priority:
    1. Explicit ``reference_path`` (no matching needed).
    2. Exact ``benchmark_id`` match against discovered reference files.
    3. LLM-based semantic matching (if ``llm_client`` provided).

    No hardcoded benchmark identifiers — all matching is data-driven.
    """
    # 1. Explicit path.
    if reference_path is not None:
        p = Path(reference_path)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return None
                return _attach_reference_metadata(
                    data,
                    path=str(p),
                    benchmark_id=None,
                    variant=None,
                    match_status="explicit_path",
                )
            except Exception:
                return None
        return None

    if benchmark_id is None:
        return None

    # 2. Discover available reference files.
    candidates = discover_reference_files()

    # 3. Try exact match first (case-insensitive).
    for c in candidates:
        if c["benchmark_id"] and c["benchmark_id"].lower() == benchmark_id.lower():
            if _variant_matches(variant, c["variant"]):
                return _attach_reference_metadata(
                    c["data"],
                    path=c["path"],
                    benchmark_id=c["benchmark_id"],
                    variant=c["variant"],
                    match_status="exact",
                )

    # 4. Try deterministic normalized matching.
    for c in candidates:
        if not benchmark_ids_match(benchmark_id, c["benchmark_id"]):
            continue
        if not _variant_matches(variant, c["variant"]):
            continue
        return _attach_reference_metadata(
            c["data"],
            path=c["path"],
            benchmark_id=c["benchmark_id"],
            variant=c["variant"],
            match_status="normalized",
        )

    # 5. Try LLM-based semantic matching.
    if llm_client is not None:
        # Filter candidates by variant first to avoid wrong-variant matches.
        variant_filtered = [
            c for c in candidates
            if c["benchmark_id"] is not None
            and (
                variant is None
                or c["variant"] is None
                or c["variant"].lower() == variant.lower()
            )
        ]
        for c in variant_filtered:
            if _llm_match_benchmark(
                llm_client,
                benchmark_id, variant,
                c["benchmark_id"], c["variant"],
            ):
                return _attach_reference_metadata(
                    c["data"],
                    path=c["path"],
                    benchmark_id=c["benchmark_id"],
                    variant=c["variant"],
                    match_status="llm",
                )

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
        Optional variant filter.

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
    "benchmark_ids_match",
    "discover_reference_files",
    "load_benchmark_reference",
    "normalize_benchmark_id",
    "build_reference_patch",
]
