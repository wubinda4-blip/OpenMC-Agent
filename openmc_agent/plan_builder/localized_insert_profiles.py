"""Localized insert profile registry, resolver, and validators (P2-FULLCORE-2C-A).

This module provides:

* :class:`ResolvedSegment` — a profile segment resolved to absolute z.
* :class:`ResolvedLocalizedInsertProfile` — a fully resolved profile for one
  intent instance, with absolute z bounds and provenance.
* :func:`resolve_profile_anchor` — compute the effective anchor z.
* :func:`resolve_profile_absolute_segments` — translate relative segments to
  absolute coordinates using the resolved anchor.
* :func:`validate_profile_registry` — check a ``LocalizedInsertProfilesPatch``
  for duplicates, missing segments, universe gaps, overlaps, etc.
* :func:`validate_profile_references` — check that intents referencing
  ``axial_profile_id`` actually find a matching profile.
* :func:`resolve_profile_for_intent` — resolve one intent's profile.
* :func:`resolve_all_profiles_for_catalog` — resolve all intents in a catalog.

Anchor semantics
----------------

``anchor_kind`` determines how relative segment coordinates are translated to
absolute z:

* ``"bottom"`` — ``absolute_z = anchor_z + relative_z``
* ``"top"``    — ``absolute_z = anchor_z - (profile_total_height - relative_z)``
* ``"center"`` — ``absolute_z = anchor_z + relative_z_from_center``
* ``"absolute"`` — relative fields are already absolute; no translation.

The effective ``anchor_z_cm`` comes from the **intent** (if present), falling
back to the **profile** default.  If both are present and differ, it is an
error (``profile_anchor_conflict``).

For ``anchor_kind="absolute"``, ``intent.anchor_z_cm`` must be ``None``
(otherwise ``profile_anchor_conflict``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    LocalizedInsertAxialProfilePatchItem,
    LocalizedInsertAxialSegmentPatchItem,
    LocalizedInsertIntentPatchItem,
    LocalizedInsertProfilesPatch,
)


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

_Z_TOLERANCE_CM: float = 1e-6


def _close(a: float, b: float, tol: float = _Z_TOLERANCE_CM) -> bool:
    return abs(a - b) < tol


# ---------------------------------------------------------------------------
# Resolved types
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSegment:
    """A profile segment resolved to absolute z coordinates."""

    segment_id: str
    absolute_z_min_cm: float
    absolute_z_max_cm: float
    universe_id: str
    role: str = ""


@dataclass
class ResolvedLocalizedInsertProfile:
    """A fully resolved profile for one insert intent instance."""

    profile_id: str
    insert_id: str
    assembly_type_id: str
    anchor_kind: str
    anchor_z_cm: float | None
    control_state_id: str | None
    resolved_segments: list[ResolvedSegment] = field(default_factory=list)
    absolute_z_min_cm: float | None = None
    absolute_z_max_cm: float | None = None
    universe_ids: list[str] = field(default_factory=list)
    provenance: str = ""
    deterministic: bool = True
    issues: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------------


def resolve_profile_anchor(
    intent: LocalizedInsertIntentPatchItem,
    profile: LocalizedInsertAxialProfilePatchItem,
) -> tuple[float | None, list[dict[str, str]]]:
    """Resolve the effective anchor z_cm for an intent + profile pair.

    Returns
    -------
    (anchor_z_cm, issues)
        ``anchor_z_cm`` is ``None`` if resolution failed (issues explain why).
        For ``anchor_kind="absolute"``, anchor is unused and returns a
        sentinel of ``0.0`` (relative = absolute).
    """
    issues: list[dict[str, str]] = []

    if profile.anchor_kind == "absolute":
        if intent.anchor_z_cm is not None:
            issues.append({
                "code": "localized_insert.profile_anchor_conflict",
                "message": (
                    f"intent {intent.insert_id!r} provides anchor_z_cm but "
                    f"profile {profile.profile_id!r} uses anchor_kind='absolute'; "
                    "remove intent anchor for absolute profiles"
                ),
            })
            return None, issues
        return 0.0, issues

    intent_anchor = intent.anchor_z_cm
    profile_anchor = profile.anchor_z_cm

    if intent_anchor is not None and profile_anchor is not None:
        if not _close(intent_anchor, profile_anchor):
            issues.append({
                "code": "localized_insert.profile_anchor_conflict",
                "message": (
                    f"intent {intent.insert_id!r} anchor_z_cm={intent_anchor} "
                    f"conflicts with profile {profile.profile_id!r} "
                    f"anchor_z_cm={profile_anchor}"
                ),
            })
            return None, issues
        return intent_anchor, issues

    anchor = intent_anchor if intent_anchor is not None else profile_anchor
    if anchor is None:
        issues.append({
            "code": "localized_insert.profile_anchor_missing",
            "message": (
                f"profile {profile.profile_id!r} for intent {intent.insert_id!r} "
                f"has anchor_kind={profile.anchor_kind!r} but no anchor_z_cm "
                "(neither intent nor profile provides one)"
            ),
        })
        return None, issues

    return anchor, issues


def _profile_total_height(profile: LocalizedInsertAxialProfilePatchItem) -> float:
    """Compute total profile height from segments."""
    if not profile.segments:
        return 0.0
    z_min = min(s.relative_z_min_cm for s in profile.segments)
    z_max = max(s.relative_z_max_cm for s in profile.segments)
    return z_max - z_min


def _profile_center_offset(profile: LocalizedInsertAxialProfilePatchItem) -> float:
    """Compute the relative z of the profile center."""
    if not profile.segments:
        return 0.0
    z_min = min(s.relative_z_min_cm for s in profile.segments)
    z_max = max(s.relative_z_max_cm for s in profile.segments)
    return (z_min + z_max) / 2.0


def resolve_profile_absolute_segments(
    profile: LocalizedInsertAxialProfilePatchItem,
    anchor_z_cm: float,
) -> list[ResolvedSegment]:
    """Translate relative segments to absolute coordinates.

    Parameters
    ----------
    profile
        The profile definition with relative segment coordinates.
    anchor_z_cm
        The resolved anchor position.  For ``anchor_kind="absolute"``,
        this is ignored (segments are already absolute).
    """
    resolved: list[ResolvedSegment] = []

    if profile.anchor_kind == "absolute":
        for seg in profile.segments:
            resolved.append(ResolvedSegment(
                segment_id=seg.segment_id,
                absolute_z_min_cm=seg.relative_z_min_cm,
                absolute_z_max_cm=seg.relative_z_max_cm,
                universe_id=seg.universe_id,
                role=seg.role,
            ))
        return resolved

    total_height = _profile_total_height(profile)
    center_offset = _profile_center_offset(profile)

    for seg in profile.segments:
        if profile.anchor_kind == "bottom":
            abs_z_min = anchor_z_cm + seg.relative_z_min_cm
            abs_z_max = anchor_z_cm + seg.relative_z_max_cm
        elif profile.anchor_kind == "top":
            abs_z_min = anchor_z_cm - (total_height - seg.relative_z_min_cm)
            abs_z_max = anchor_z_cm - (total_height - seg.relative_z_max_cm)
        elif profile.anchor_kind == "center":
            abs_z_min = anchor_z_cm + (seg.relative_z_min_cm - center_offset)
            abs_z_max = anchor_z_cm + (seg.relative_z_max_cm - center_offset)
        else:
            abs_z_min = seg.relative_z_min_cm
            abs_z_max = seg.relative_z_max_cm

        if abs_z_min > abs_z_max:
            abs_z_min, abs_z_max = abs_z_max, abs_z_min

        resolved.append(ResolvedSegment(
            segment_id=seg.segment_id,
            absolute_z_min_cm=abs_z_min,
            absolute_z_max_cm=abs_z_max,
            universe_id=seg.universe_id,
            role=seg.role,
        ))

    return resolved


def resolve_profile_for_intent(
    intent: LocalizedInsertIntentPatchItem,
    profiles: LocalizedInsertProfilesPatch | dict[str, LocalizedInsertAxialProfilePatchItem],
    assembly_type_id: str = "",
) -> ResolvedLocalizedInsertProfile | None:
    """Resolve one intent's axial profile into absolute segments.

    Returns ``None`` if the intent has no ``axial_profile_id``, or if the
    referenced profile is not found (issues are recorded in the result).
    """
    if intent.axial_profile_id is None:
        return None

    if isinstance(profiles, LocalizedInsertProfilesPatch):
        profile_map: dict[str, LocalizedInsertAxialProfilePatchItem] = {
            p.profile_id: p for p in profiles.profiles
        }
    else:
        profile_map = profiles

    profile = profile_map.get(intent.axial_profile_id)
    if profile is None:
        return ResolvedLocalizedInsertProfile(
            profile_id=intent.axial_profile_id,
            insert_id=intent.insert_id,
            assembly_type_id=assembly_type_id,
            anchor_kind="unknown",
            anchor_z_cm=None,
            control_state_id=intent.control_state_id,
            deterministic=False,
            issues=[{
                "code": "localized_insert.profile_ref_missing",
                "message": (
                    f"intent {intent.insert_id!r} references profile "
                    f"{intent.axial_profile_id!r} not found in registry"
                ),
            }],
        )

    anchor, anchor_issues = resolve_profile_anchor(intent, profile)
    all_issues = list(anchor_issues)

    if anchor is None:
        return ResolvedLocalizedInsertProfile(
            profile_id=profile.profile_id,
            insert_id=intent.insert_id,
            assembly_type_id=assembly_type_id,
            anchor_kind=profile.anchor_kind,
            anchor_z_cm=None,
            control_state_id=intent.control_state_id,
            deterministic=False,
            issues=all_issues,
        )

    resolved_segments = resolve_profile_absolute_segments(profile, anchor)

    abs_zs = [s.absolute_z_min_cm for s in resolved_segments] + [
        s.absolute_z_max_cm for s in resolved_segments
    ]
    abs_z_min = min(abs_zs) if abs_zs else None
    abs_z_max = max(abs_zs) if abs_zs else None
    universe_ids = list({s.universe_id for s in resolved_segments})

    return ResolvedLocalizedInsertProfile(
        profile_id=profile.profile_id,
        insert_id=intent.insert_id,
        assembly_type_id=assembly_type_id,
        anchor_kind=profile.anchor_kind,
        anchor_z_cm=anchor,
        control_state_id=intent.control_state_id,
        resolved_segments=resolved_segments,
        absolute_z_min_cm=abs_z_min,
        absolute_z_max_cm=abs_z_max,
        universe_ids=universe_ids,
        provenance=f"resolved from profile={profile.profile_id} anchor={anchor}",
        deterministic=True,
        issues=all_issues,
    )


def resolve_all_profiles_for_catalog(
    catalog: AssemblyCatalogPatch,
    profiles: LocalizedInsertProfilesPatch | dict[str, LocalizedInsertAxialProfilePatchItem],
) -> list[ResolvedLocalizedInsertProfile]:
    """Resolve all insert intents that reference profiles in a catalog."""
    if isinstance(profiles, LocalizedInsertProfilesPatch):
        profile_map: dict[str, LocalizedInsertAxialProfilePatchItem] = {
            p.profile_id: p for p in profiles.profiles
        }
    else:
        profile_map = profiles

    results: list[ResolvedLocalizedInsertProfile] = []
    for atype in catalog.assembly_types:
        for intent in atype.pin_map.localized_insert_intents:
            if intent.axial_profile_id is None:
                continue
            resolved = resolve_profile_for_intent(
                intent, profile_map, assembly_type_id=atype.assembly_type_id,
            )
            if resolved is not None:
                results.append(resolved)
    return results


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@dataclass
class ProfileValidationResult:
    """Result of validating a profile registry."""

    ok: bool = True
    issues: list[dict[str, str]] = field(default_factory=list)


def validate_profile_registry(
    patch: LocalizedInsertProfilesPatch,
    known_universe_ids: list[str] | None = None,
) -> ProfileValidationResult:
    """Validate a ``LocalizedInsertProfilesPatch`` for structural integrity.

    Checks:
    * profile_id uniqueness
    * segment_id uniqueness within a profile
    * relative bounds finite and z_min < z_max
    * segments ordered (no overlap, no gap unless policy allows)
    * universe references exist
    * non-empty profiles
    """
    issues: list[dict[str, str]] = []
    known_uvs = set(known_universe_ids) if known_universe_ids else None
    seen_profile_ids: set[str] = set()

    for profile in patch.profiles:
        if profile.profile_id in seen_profile_ids:
            issues.append({
                "code": "localized_insert.profile_id_duplicate",
                "severity": "error",
                "message": f"duplicate profile_id {profile.profile_id!r}",
            })
            continue
        seen_profile_ids.add(profile.profile_id)

        if not profile.segments:
            issues.append({
                "code": "localized_insert.profile_empty",
                "severity": "error",
                "message": f"profile {profile.profile_id!r} has no segments",
            })
            continue

        seg_issues = validate_profile_segments(profile, known_uvs)
        for iss in seg_issues.issues:
            iss.setdefault("path", f"profiles[{profile.profile_id}]")
        issues.extend(seg_issues.issues)

    errors = [i for i in issues if i.get("severity", "error") == "error"]
    return ProfileValidationResult(ok=len(errors) == 0, issues=issues)


def validate_profile_segments(
    profile: LocalizedInsertAxialProfilePatchItem,
    known_universe_ids: set[str] | None = None,
) -> ProfileValidationResult:
    """Validate segments within a single profile."""
    issues: list[dict[str, str]] = []
    seen_seg_ids: set[str] = set()

    for seg in profile.segments:
        if seg.segment_id in seen_seg_ids:
            issues.append({
                "code": "localized_insert.profile_segment_invalid",
                "severity": "error",
                "message": f"duplicate segment_id {seg.segment_id!r} in profile {profile.profile_id!r}",
            })
        seen_seg_ids.add(seg.segment_id)

        if not math.isfinite(seg.relative_z_min_cm) or not math.isfinite(seg.relative_z_max_cm):
            issues.append({
                "code": "localized_insert.profile_segment_invalid",
                "severity": "error",
                "message": f"segment {seg.segment_id!r} has non-finite bounds",
            })

        if seg.relative_z_min_cm >= seg.relative_z_max_cm:
            issues.append({
                "code": "localized_insert.profile_segment_invalid",
                "severity": "error",
                "message": (
                    f"segment {seg.segment_id!r} has relative_z_min >= relative_z_max "
                    f"({seg.relative_z_min_cm} >= {seg.relative_z_max_cm})"
                ),
            })

        if known_universe_ids is not None and seg.universe_id not in known_universe_ids:
            issues.append({
                "code": "localized_insert.profile_universe_missing",
                "severity": "error",
                "message": (
                    f"segment {seg.segment_id!r} universe {seg.universe_id!r} "
                    "not found in known universes"
                ),
            })

    sorted_segs = sorted(profile.segments, key=lambda s: s.relative_z_min_cm)

    for i in range(len(sorted_segs) - 1):
        cur = sorted_segs[i]
        nxt = sorted_segs[i + 1]
        if cur.relative_z_max_cm > nxt.relative_z_min_cm + _Z_TOLERANCE_CM:
            issues.append({
                "code": "localized_insert.profile_segment_overlap",
                "severity": "error",
                "message": (
                    f"segments overlap: {cur.segment_id!r} ends at "
                    f"{cur.relative_z_max_cm} but {nxt.segment_id!r} starts at "
                    f"{nxt.relative_z_min_cm}"
                ),
            })
        elif cur.relative_z_max_cm < nxt.relative_z_min_cm - _Z_TOLERANCE_CM:
            issues.append({
                "code": "localized_insert.profile_segment_gap",
                "severity": "warning",
                "message": (
                    f"gap between {cur.segment_id!r} (ends {cur.relative_z_max_cm}) "
                    f"and {nxt.segment_id!r} (starts {nxt.relative_z_min_cm})"
                ),
            })

    errors = [i for i in issues if i.get("severity", "error") == "error"]
    return ProfileValidationResult(ok=len(errors) == 0, issues=issues)


def validate_profile_references(
    catalog: AssemblyCatalogPatch,
    profiles: LocalizedInsertProfilesPatch,
) -> ProfileValidationResult:
    """Check that all intent profile references resolve to real profiles."""
    issues: list[dict[str, str]] = []
    known_profile_ids = {p.profile_id for p in profiles.profiles}

    for atype in catalog.assembly_types:
        for intent in atype.pin_map.localized_insert_intents:
            if intent.axial_profile_id is None:
                if intent.z_min_cm is None or intent.z_max_cm is None:
                    issues.append({
                        "code": "localized_insert.profile_ref_missing",
                        "severity": "error",
                        "message": (
                            f"intent {intent.insert_id!r} in {atype.assembly_type_id!r} "
                            "has neither axial_profile_id nor z_min/z_max"
                        ),
                    })
                continue

            if intent.axial_profile_id not in known_profile_ids:
                issues.append({
                    "code": "localized_insert.profile_ref_missing",
                    "severity": "error",
                    "message": (
                        f"intent {intent.insert_id!r} in {atype.assembly_type_id!r} "
                        f"references profile {intent.axial_profile_id!r} not in registry"
                    ),
                })

            if intent.z_min_cm is not None and intent.z_max_cm is not None:
                issues.append({
                    "code": "localized_insert.profile_anchor_conflict",
                    "severity": "error",
                    "message": (
                        f"intent {intent.insert_id!r} uses both axial_profile_id "
                        f"and explicit z_min/z_max — choose one definition"
                    ),
                })

    errors = [i for i in issues if i.get("severity", "error") == "error"]
    return ProfileValidationResult(ok=len(errors) == 0, issues=issues)


__all__ = [
    "ResolvedSegment",
    "ResolvedLocalizedInsertProfile",
    "ProfileValidationResult",
    "resolve_profile_anchor",
    "resolve_profile_absolute_segments",
    "resolve_profile_for_intent",
    "resolve_all_profiles_for_catalog",
    "validate_profile_registry",
    "validate_profile_segments",
    "validate_profile_references",
]
