"""Shared lattice-loading structural validation.

This module is the single source of truth for lattice-transformation structural
issues that are discovered when axial-layer lattice loadings are composed onto
their base lattice.  Both :func:`validate_simulation_plan` and renderer
``can_render`` diagnostics call :func:`lattice_loading_structural_issues` so the
same structured issues appear at plan-validation time (early) and at renderer
time (defensive), with identical ``code`` and ``schema_path``.

The function performs a *dry-run* of the composition engine
(:func:`compose_lattice_loadings`) without importing OpenMC.  It surfaces the
underlying structured issues:

* ``lattice_transform.loading_ref_missing``
* ``lattice_transform.base_lattice_mismatch``
* ``lattice_transform.replacement_universe_missing``
* ``lattice_transform.source_universe_missing``
* ``renderer.axial_loading_wrong_fill_type``
* ``renderer.axial_loading_materialization_failed`` (summary wrapper)
* ``renderer.axial_loading_base_lattice_mismatch``
* ``renderer.axial_loading_base_lattice_missing``

Each issue is annotated with the ``layer_id``, ``loading_id``, and
``operation_id`` when applicable so downstream deterministic repair oracles can
locate the exact defect.
"""

from __future__ import annotations

from typing import Any

from openmc_agent.lattice_transform import (
    compose_lattice_loadings,
    normalized_layer_loading_ids,
)
from openmc_agent.schemas import ComplexModelSpec, ValidationIssue


def _normalize_path(path: str) -> str:
    """Strip array indices so [0], [1] collapse for dedup."""
    out: list[str] = []
    for part in path.replace(".", "/").split("/"):
        if part and not part.lstrip("-").isdigit():
            out.append(part)
    return ".".join(out)


def _deduplicate(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ValidationIssue] = []
    for issue in issues:
        identity = (issue.code, _normalize_path(issue.schema_path or ""), issue.severity)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(issue)
    return deduped


def lattice_loading_structural_issues(
    model: ComplexModelSpec,
) -> list[ValidationIssue]:
    """Return all structural lattice-loading issues for ``model``.

    Walks every ``core.axial_layer`` that carries ``loading_id`` /
    ``loading_ids``, resolves the referenced loadings, and runs
    :func:`compose_lattice_loadings` as a dry-run.  Any issue produced by the
    composition engine is returned with a precise ``schema_path`` that
    identifies the offending ``layer_id`` / ``loading_id`` / ``operation_id``.

    This makes the *real* execution blocker visible at plan-validation time
    rather than deferring it to ``render()`` or the assess-stage dry-run probe.
    """
    issues: list[ValidationIssue] = []
    if model is None or model.core is None:
        return issues

    layers = model.core.axial_layers
    if not layers:
        return issues

    lattice_by_id: dict[str, Any] = {l.id: l for l in model.lattices}
    loading_by_id: dict[str, Any] = {
        l.id: l for l in (model.lattice_loadings or [])
    }
    universe_ids: set[str] = {u.id for u in model.universes}

    for layer in layers:
        lids = normalized_layer_loading_ids(layer)
        if not lids:
            continue

        layer_path = f"core.axial_layers[{layer.id}]"

        if layer.fill.type != "lattice":
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_wrong_fill_type",
                message=(
                    f"layer {layer.id!r} has loading_ids {lids} but "
                    f"fill.type={layer.fill.type!r} (expected 'lattice')"
                ),
                schema_path=f"{layer_path}.fill",
            ))
            continue

        declared_bases: set[str] = set()
        missing_loading_ids: list[str] = []
        for loading_id in lids:
            loading = loading_by_id.get(loading_id)
            if loading is None:
                missing_loading_ids.append(loading_id)
            else:
                declared_bases.add(loading.base_lattice_id)

        if missing_loading_ids:
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.loading_ref_missing",
                message=(
                    f"layer {layer.id!r}: referenced loadings are missing: "
                    f"{missing_loading_ids}"
                ),
                schema_path=f"{layer_path}.loading_ids",
            ))
            continue

        if len(declared_bases) != 1:
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.base_lattice_mismatch",
                message=(
                    f"layer {layer.id!r}: loadings {lids} do not declare one "
                    f"common base lattice: {sorted(declared_bases)}"
                ),
                schema_path=f"{layer_path}.loading_ids",
            ))
            continue

        base_lattice_id = next(iter(declared_bases))
        base_lattice = lattice_by_id.get(base_lattice_id)
        if base_lattice is None:
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_base_lattice_missing",
                message=(
                    f"layer {layer.id!r}: base lattice {base_lattice_id!r} "
                    "not found"
                ),
                schema_path=f"{layer_path}.fill",
            ))
            continue

        result = compose_lattice_loadings(
            base_lattice=base_lattice,
            loading_ids=lids,
            loading_by_id=loading_by_id,
            universes=model.universes,
            cells=model.cells,
        )
        if not result.ok:
            for i in result.issues:
                if i.severity != "error":
                    continue
                ann = _annotate_issue(i, layer.id, lids, loading_by_id)
                issues.append(ann)
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_materialization_failed",
                message=(
                    f"layer {layer.id!r}: compose_lattice_loadings failed: "
                    + "; ".join(
                        i.message for i in result.issues
                        if i.severity == "error"
                    )
                ),
                schema_path=f"{layer_path}.loading_ids",
            ))

    return _deduplicate(issues)


def _annotate_issue(
    issue: ValidationIssue,
    layer_id: str,
    loading_ids: list[str],
    loading_by_id: dict[str, Any],
) -> ValidationIssue:
    """Enrich a compose_lattice_loadings issue with layer/loading/operation context."""
    schema_path = issue.schema_path or ""
    op_id = _extract_operation_id(issue.message)

    loading_id: str | None = None
    if op_id:
        for lid in loading_ids:
            loading = loading_by_id.get(lid)
            if loading is None:
                continue
            if any(t.operation_id == op_id for t in loading.transformations):
                loading_id = lid
                break
    if loading_id is None and len(loading_ids) == 1:
        loading_id = loading_ids[0]

    parts: list[str] = []
    if loading_id:
        parts.append(f"lattice_loadings[{loading_id}]")
    if op_id:
        parts.append(f"transformations[{op_id}]")
    if schema_path:
        parts.append(schema_path)
    annotated_path = ".".join(parts) if parts else schema_path

    return ValidationIssue(
        severity=issue.severity,
        code=issue.code,
        message=issue.message,
        schema_path=annotated_path,
    )


def _extract_operation_id(message: str) -> str | None:
    """Extract operation_id from a compose error message like ``operation 'xyz': ...``."""
    marker = "operation '"
    idx = message.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = message.find("'", start)
    if end < 0:
        return None
    return message[start:end]


__all__ = [
    "lattice_loading_structural_issues",
]
