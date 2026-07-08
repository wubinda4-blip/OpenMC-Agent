"""Material-id alias resolution for patch validation and assembly."""

from __future__ import annotations

import re

from openmc_agent.schemas import AgentBaseModel


class MaterialResolutionResult(AgentBaseModel):
    ok: bool
    original_id: str
    resolved_id: str | None = None
    reason: str | None = None
    issue_code: str | None = None


_DEFAULT_ALIASES: dict[str, str] = {
    "grid_zircaloy4": "zircaloy4",
    "grid_zircaloy_4": "zircaloy4",
    "spacer_zircaloy4": "zircaloy4",
    "zircaloy-4": "zircaloy4",
    "inconel-718": "inconel718",
    "ss-304": "ss304",
    "stainless_steel_304": "ss304",
}


def _normalize_material_id(material_id: str) -> str:
    mid = material_id.strip().lower()
    mid = mid.replace("-", "_")
    mid = re.sub(r"[^a-z0-9_]+", "_", mid)
    mid = re.sub(r"_+", "_", mid).strip("_")
    return mid


def _known_lookup(known_material_ids: set[str]) -> dict[str, str]:
    return {_normalize_material_id(mid): mid for mid in known_material_ids}


def resolve_material_id(
    material_id: str,
    known_material_ids: set[str],
    aliases: dict[str, str] | None = None,
) -> MaterialResolutionResult:
    """Resolve a material id against known ids and generic aliases."""
    if material_id in known_material_ids:
        return MaterialResolutionResult(
            ok=True,
            original_id=material_id,
            resolved_id=material_id,
            reason="material id already exists",
        )

    lookup = _known_lookup(known_material_ids)
    normalized = _normalize_material_id(material_id)
    if normalized in lookup:
        return MaterialResolutionResult(
            ok=True,
            original_id=material_id,
            resolved_id=lookup[normalized],
            reason="material id normalized to known id",
            issue_code="patch.axial_overlays.material_alias_resolved",
        )

    alias_map: dict[str, str] = {
        _normalize_material_id(k): v for k, v in _DEFAULT_ALIASES.items()
    }
    if aliases:
        alias_map.update({_normalize_material_id(k): v for k, v in aliases.items()})

    alias_target = alias_map.get(normalized)
    if alias_target:
        if alias_target in known_material_ids:
            resolved = alias_target
        else:
            resolved = lookup.get(_normalize_material_id(alias_target))
        if resolved is not None:
            return MaterialResolutionResult(
                ok=True,
                original_id=material_id,
                resolved_id=resolved,
                reason=f"material alias resolved to {resolved!r}",
                issue_code="patch.axial_overlays.material_alias_resolved",
            )

    return MaterialResolutionResult(
        ok=False,
        original_id=material_id,
        resolved_id=None,
        reason=f"material id {material_id!r} is not defined",
        issue_code="patch.axial_overlays.material_missing",
    )


__all__ = ["MaterialResolutionResult", "resolve_material_id"]
