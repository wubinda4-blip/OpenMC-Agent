"""Evidence packing and deterministic preflight for the facts review gate."""

from __future__ import annotations

from typing import Any

from .fingerprints import compute_evidence_pack_hash
from .models import PlanClosedLoopPolicy, PlanEvidencePack, PlanGateId, SourceExcerpt


def _paragraphs(text: str, limit: int) -> list[tuple[int, int, str]]:
    lines = text.splitlines() or [""]
    groups: list[tuple[int, int, str]] = []
    start = 1
    current: list[str] = []
    for index, line in enumerate(lines, 1):
        if line.startswith("#") and current:
            groups.append((start, index - 1, "\n".join(current)))
            start, current = index, [line]
        elif not line.strip() and current:
            current.append(line)
            groups.append((start, index, "\n".join(current)))
            start, current = index + 1, []
        else:
            if not current:
                start = index
            current.append(line)
    if current:
        groups.append((start, len(lines), "\n".join(current)))

    chunks: list[tuple[int, int, str]] = []
    for start, end, group in groups:
        if len(group) <= limit:
            chunks.append((start, end, group))
            continue
        part: list[str] = []
        part_start = start
        for offset, line in enumerate(group.splitlines(), start):
            proposed = "\n".join(part + [line])
            if part and len(proposed) > limit:
                chunks.append((part_start, offset - 1, "\n".join(part)))
                part, part_start = [line], offset
            else:
                part.append(line)
        if part:
            chunks.append((part_start, end, "\n".join(part)))
    return chunks


def build_facts_evidence_packs(
    *, requirement_text: str, facts_patch: dict[str, Any], confirmed_facts: dict[str, Any],
    planning_metadata: dict[str, Any], policy: PlanClosedLoopPolicy,
) -> list[PlanEvidencePack]:
    """Chunk the resolved requirement without dropping or duplicating source text."""
    chunks = _paragraphs(requirement_text, policy.facts_review_chunk_chars)
    source_path = str(planning_metadata.get("resolved_requirement_path") or "resolved_requirement")
    packs: list[PlanEvidencePack] = []
    for index, (line_start, line_end, text) in enumerate(chunks[:policy.max_facts_review_chunks]):
        excerpt = SourceExcerpt(
            source_id=f"facts_source_{index:03d}", source_path=source_path,
            line_start=line_start, line_end=line_end, text=text,
        )
        metadata = {
            "chunk_index": index, "chunk_count": len(chunks),
            "requirement_total_chars": len(requirement_text),
            "reviewed_line_range": [line_start, line_end],
            "source_truncated": len(chunks) > policy.max_facts_review_chunks,
            "facts_summary": {key: facts_patch.get(key) for key in (
                "benchmark_id", "selected_variant", "model_scope", "lattice_size",
                "assembly_count", "core_lattice_size", "active_fuel_region_cm", "axial_domain_cm",
            )},
            "planning_feature_contract": planning_metadata.get("planning_feature_contract", {}),
            "resolved_planning_scope": planning_metadata.get("resolved_planning_scope", {}),
            "facts_consistency_issues": planning_metadata.get("facts_consistency_issues", []),
            "expected_patch_family": planning_metadata.get("expected_patch_family", {}),
        }
        pack = PlanEvidencePack(
            gate_id=PlanGateId.FACTS, source_excerpts=[excerpt],
            confirmed_facts=confirmed_facts, relevant_patches={"facts": facts_patch},
            patch_summaries={"planning_mode_decision": planning_metadata.get("planning_mode_decision", {})},
            deterministic_issues=facts_review_preflight(facts_patch), metadata=metadata,
        )
        packs.append(pack)
    return packs


def facts_review_preflight(facts_patch: dict[str, Any]) -> list[dict[str, Any]]:
    """Small, schema-aligned checks which do not duplicate patch validators."""
    issues: list[dict[str, Any]] = []
    if facts_patch.get("patch_type") != "facts":
        return [{"code": "facts_review.patch_type_invalid", "severity": "error", "blocking": True}]
    if facts_patch.get("model_scope") in {"multi_assembly_core", "full_core"}:
        count = facts_patch.get("assembly_count")
        by_type = facts_patch.get("assembly_type_counts")
        if isinstance(count, int) and isinstance(by_type, dict) and sum(v for v in by_type.values() if isinstance(v, int)) != count:
            issues.append({"code": "facts_review.assembly_count_mismatch", "severity": "error", "blocking": True})
    for name in ("active_fuel_region_cm", "axial_domain_cm"):
        value = facts_patch.get(name)
        if isinstance(value, (list, tuple)) and len(value) == 2 and value[0] >= value[1]:
            issues.append({"code": f"facts_review.{name}_invalid", "severity": "error", "blocking": True})
    variants = [item.get("variant_id") for item in facts_patch.get("fuel_variant_requirements", []) if isinstance(item, dict)]
    if len(variants) != len(set(variants)):
        issues.append({"code": "facts_review.duplicate_fuel_variant_id", "severity": "error", "blocking": True})
    inserts = [item.get("requirement_id") for item in facts_patch.get("localized_insert_requirements", []) if isinstance(item, dict)]
    if len(inserts) != len(set(inserts)):
        issues.append({"code": "facts_review.duplicate_localized_insert_requirement", "severity": "error", "blocking": True})
    return issues
