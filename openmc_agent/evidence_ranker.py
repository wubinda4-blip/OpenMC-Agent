"""Deterministic evidence reranking, deduplication, and prompt budgeting.

This module is a local post-processing layer. It does not call LLMs,
embeddings, external services, or promote retrieved documentation to confirmed
physical facts.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import Field

from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.schemas import AgentBaseModel


class EvidenceRankerPolicy(AgentBaseModel):
    max_total_evidence: int = 12
    max_grep_evidence: int = 4
    max_graph_evidence: int = 4
    max_graphrag_evidence: int = 6
    max_rag_evidence: int = 4
    max_chars_per_evidence: int = 900
    max_total_chars: int = 6000
    prefer_graphrag_over_plain_rag: bool = True
    keep_exact_grep_matches: bool = True
    drop_low_score_threshold: float = 0.05
    dedup_text_jaccard_threshold: float = 0.85
    dedup_locator_exact: bool = True


class RankedEvidence(AgentBaseModel):
    evidence: RetrievedEvidence
    score: float
    reasons: list[str] = Field(default_factory=list)
    dedup_key: str | None = None
    truncated: bool = False


class EvidenceRankingResult(AgentBaseModel):
    ranked: list[RankedEvidence] = Field(default_factory=list)
    selected: list[RetrievedEvidence] = Field(default_factory=list)
    dropped_duplicates: list[RetrievedEvidence] = Field(default_factory=list)
    dropped_low_score: list[RetrievedEvidence] = Field(default_factory=list)
    dropped_budget: list[RetrievedEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


_SOURCE_BASE_SCORE = {
    "grep": 0.35,
    "graph": 0.25,
    "graphrag": 0.30,
    "rag": 0.15,
    "runtime": 0.10,
    "validator": 0.10,
    "unknown": 0.05,
}
_SOURCE_PRIORITY = {
    "grep": 5,
    "graph": 4,
    "graphrag": 3,
    "rag": 2,
    "runtime": 1,
    "validator": 1,
    "unknown": 0,
}
_FACT_GAP_TOKENS = (
    "cross_sections",
    "missing_nuclide_data",
    "material_missing_nuclide_data",
    "density",
    "composition",
)
_UNSAFE_FACT_PATTERNS = (
    re.compile(r"\b\d+(?:\.\d+)?\s*g\s*/\s*cm3\b", re.I),
    re.compile(r"\bset_density\s*\(", re.I),
    re.compile(r"\badd_(?:nuclide|element)\s*\(", re.I),
    re.compile(r"\bOPENMC_CROSS_SECTIONS\s*=", re.I),
    re.compile(r"/(?:cross_sections|nndc|endfb|jeff|jendl)[^ \n\t]*\.xml", re.I),
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]*|\d+")


def score_evidence(
    evidence: RetrievedEvidence,
    *,
    issue_codes: list[str] | None = None,
    schema_paths: list[str] | None = None,
    concept_ids: list[str] | None = None,
) -> tuple[float, list[str]]:
    """Score evidence using deterministic source and issue relevance signals."""
    current_issue_codes = _normalize_list(issue_codes)
    current_schema_paths = _normalize_list(schema_paths)
    current_concept_ids = _normalize_list(concept_ids)

    source = _source_type(evidence)
    score = _SOURCE_BASE_SCORE.get(source, _SOURCE_BASE_SCORE["unknown"])
    reasons = [f"source={source} base +{score:.2f}"]

    text_and_locator = f"{evidence.locator}\n{evidence.text}".casefold()
    metadata = evidence.metadata or {}

    evidence_issue_codes = _metadata_values(metadata, "issue_codes")
    if evidence.issue_code:
        evidence_issue_codes.append(evidence.issue_code)
    metadata_issue_code = metadata.get("issue_code")
    if isinstance(metadata_issue_code, str):
        evidence_issue_codes.append(metadata_issue_code)
    if _intersects(evidence_issue_codes, current_issue_codes):
        score += 0.25
        reasons.append("issue code match +0.25")
    elif any(_issue_token_hits(code, text_and_locator) for code in current_issue_codes):
        score += 0.10
        reasons.append("issue token in text/locator +0.10")

    evidence_schema_paths = _metadata_values(metadata, "schema_paths")
    if evidence.schema_path:
        evidence_schema_paths.append(evidence.schema_path)
    metadata_schema_path = metadata.get("schema_path")
    if isinstance(metadata_schema_path, str):
        evidence_schema_paths.append(metadata_schema_path)
    if _intersects(evidence_schema_paths, current_schema_paths):
        score += 0.20
        reasons.append("schema path exact match +0.20")
    elif _prefix_intersects(evidence_schema_paths, current_schema_paths):
        score += 0.12
        reasons.append("schema path prefix match +0.12")
    elif any(_schema_field(path) in text_and_locator for path in current_schema_paths):
        score += 0.08
        reasons.append("schema field in text/locator +0.08")

    evidence_concept_ids = _metadata_values(metadata, "related_concept_ids")
    evidence_concept_ids.extend(_metadata_values(metadata, "concept_ids"))
    if evidence.concept_id:
        evidence_concept_ids.append(evidence.concept_id)
    metadata_concept_id = metadata.get("concept_id")
    if isinstance(metadata_concept_id, str):
        evidence_concept_ids.append(metadata_concept_id)
    if _intersects(evidence_concept_ids, current_concept_ids):
        score += 0.20
        reasons.append("concept match +0.20")

    related_api_refs = _metadata_values(metadata, "related_api_refs")
    related_api_refs.extend(_metadata_values(metadata, "api_refs"))
    if related_api_refs and _api_matches_concept(related_api_refs, current_concept_ids):
        score += 0.10
        reasons.append("API ref related to concept +0.10")

    if metadata.get("graph_paths"):
        score += 0.12
        reasons.append("graph path present +0.12")
    if metadata.get("ingested_graph_node_id"):
        score += 0.08
        reasons.append("ingested graph node present +0.08")
    if metadata.get("doc_chunk_id") or metadata.get("chunk_id"):
        score += 0.06
        reasons.append("document chunk id present +0.06")

    if metadata.get("matched_pattern"):
        score += 0.15
        reasons.append("grep matched_pattern +0.15")
    if metadata.get("symbol_hint"):
        score += 0.10
        reasons.append("grep symbol_hint +0.10")
    if _looks_like_repo_source(evidence.locator):
        score += 0.05
        reasons.append("repository locator +0.05")

    if not evidence.text.strip() or not evidence.locator.strip():
        score -= 0.20
        reasons.append("empty text or locator -0.20")
    if _is_repetitive(evidence.text):
        score -= 0.05
        reasons.append("repetitive text -0.05")
    if _is_fact_gap(current_issue_codes, current_concept_ids, metadata) and _looks_like_unsafe_fact(evidence):
        score -= 0.30
        reasons.append("fact-gap unsafe factual detail -0.30")

    return _clamp(score), reasons


def deduplicate_evidence(
    evidence: list[RetrievedEvidence],
    *,
    policy: EvidenceRankerPolicy | None = None,
) -> tuple[list[RetrievedEvidence], list[RetrievedEvidence]]:
    """Drop exact and near-duplicate evidence while preserving strongest sources."""
    active_policy = policy or EvidenceRankerPolicy()
    kept: list[RetrievedEvidence] = []
    dropped: list[RetrievedEvidence] = []

    for item in evidence:
        replacement_index: int | None = None
        duplicate = False
        for index, existing in enumerate(kept):
            if _is_duplicate(existing, item, active_policy):
                duplicate = True
                if _intrinsic_rank_key(item) > _intrinsic_rank_key(existing):
                    replacement_index = index
                break
        if replacement_index is not None:
            dropped.append(kept[replacement_index])
            kept[replacement_index] = item
        elif duplicate:
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped


def rank_and_select_evidence(
    evidence: list[RetrievedEvidence],
    *,
    issue_codes: list[str] | None = None,
    schema_paths: list[str] | None = None,
    concept_ids: list[str] | None = None,
    policy: EvidenceRankerPolicy | None = None,
) -> EvidenceRankingResult:
    """Rerank, deduplicate, truncate, and budget evidence for prompts."""
    active_policy = policy or EvidenceRankerPolicy()
    if not evidence:
        return EvidenceRankingResult(summary=_summary_payload([], [], [], [], []))
    try:
        deduped, dropped_duplicates = deduplicate_evidence(evidence, policy=active_policy)
        ranked: list[RankedEvidence] = []
        dropped_low_score: list[RetrievedEvidence] = []
        for item in deduped:
            score, reasons = score_evidence(
                item,
                issue_codes=issue_codes,
                schema_paths=schema_paths,
                concept_ids=concept_ids,
            )
            if score < active_policy.drop_low_score_threshold:
                dropped_low_score.append(item)
                continue
            ranked.append(
                RankedEvidence(
                    evidence=item,
                    score=score,
                    reasons=reasons,
                    dedup_key=_dedup_key(item),
                )
            )
        ranked.sort(key=_rank_sort_key)

        selected_ranked, dropped_budget = _apply_limits(ranked, active_policy)
        selected: list[RetrievedEvidence] = []
        for item in selected_ranked:
            truncated_evidence, truncated = _truncate_evidence(
                item.evidence,
                active_policy.max_chars_per_evidence,
            )
            item.truncated = truncated
            selected.append(truncated_evidence)

        return EvidenceRankingResult(
            ranked=ranked,
            selected=selected,
            dropped_duplicates=dropped_duplicates,
            dropped_low_score=dropped_low_score,
            dropped_budget=dropped_budget,
            summary=_summary_payload(
                ranked,
                selected,
                dropped_duplicates,
                dropped_low_score,
                dropped_budget,
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive integration path
        return EvidenceRankingResult(
            selected=list(evidence[: active_policy.max_total_evidence]),
            warnings=[f"evidence ranking failed: {exc}"],
            summary={"selected_count": min(len(evidence), active_policy.max_total_evidence)},
        )


def format_ranked_evidence_block(
    result: EvidenceRankingResult,
    *,
    title: str = "Ranked Evidence",
) -> str:
    """Render selected ranked evidence with compact relevance reasons."""
    if not result.selected:
        return ""

    score_by_key = {_evidence_identity(item.evidence): item for item in result.ranked}
    lines = [f"\n[{title}]"]
    if _has_fact_gap_evidence(result.selected):
        lines.append(
            "Evidence is contextual only; human confirmation is still required for missing facts."
        )
    lines.append(
        "Do not use evidence to invent material density, composition, nuclear-data paths, "
        "benchmark constants, or missing loading maps."
    )

    for index, evidence in enumerate(result.selected, start=1):
        ranked = score_by_key.get(_evidence_identity(evidence))
        if ranked is None:
            score = evidence.score if evidence.score is not None else 0.0
            reasons: list[str] = []
        else:
            score = ranked.score
            reasons = ranked.reasons[:4]
        source = _source_type(evidence)
        lines.append(f"{index}. source={source} score={score:.2f}")
        lines.append(f"   locator={evidence.locator}")
        graph_path = _first_graph_path(evidence)
        if graph_path:
            lines.append(f"   graph_path={graph_path}")
        concepts = _metadata_values(evidence.metadata, "related_concept_ids")
        concepts.extend(_metadata_values(evidence.metadata, "concept_ids"))
        if concepts:
            lines.append(f"   concepts={', '.join(_dedupe(concepts)[:4])}")
        if reasons:
            lines.append(f"   relevance={'; '.join(reasons)}")
        lines.append("   text=" + _one_line_preview(evidence.text))
    return "\n".join(lines) + "\n"


def _apply_limits(
    ranked: list[RankedEvidence],
    policy: EvidenceRankerPolicy,
) -> tuple[list[RankedEvidence], list[RetrievedEvidence]]:
    source_counts: dict[str, int] = {}
    selected: list[RankedEvidence] = []
    dropped: list[RetrievedEvidence] = []
    total_chars = 0

    for item in ranked:
        source = _source_type(item.evidence)
        source_limit = _source_limit(source, policy)
        if source_counts.get(source, 0) >= source_limit:
            dropped.append(item.evidence)
            continue
        if len(selected) >= policy.max_total_evidence:
            dropped.append(item.evidence)
            continue
        preview_chars = min(len(item.evidence.text), policy.max_chars_per_evidence)
        if total_chars + preview_chars > policy.max_total_chars:
            dropped.append(item.evidence)
            continue
        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        total_chars += preview_chars
    return selected, dropped


def _truncate_evidence(
    evidence: RetrievedEvidence,
    max_chars: int,
) -> tuple[RetrievedEvidence, bool]:
    limit = max(120, max_chars)
    if len(evidence.text) <= limit:
        return evidence, False
    text = evidence.text[: max(0, limit - 16)].rstrip() + "\n...[truncated]"
    metadata = dict(evidence.metadata)
    metadata["truncated"] = True
    return evidence.model_copy(update={"text": text, "metadata": metadata}), True


def _source_limit(source: str, policy: EvidenceRankerPolicy) -> int:
    if source == "grep":
        return policy.max_grep_evidence
    if source == "graph":
        return policy.max_graph_evidence
    if source == "graphrag":
        return policy.max_graphrag_evidence
    if source == "rag":
        return policy.max_rag_evidence
    return policy.max_total_evidence


def _rank_sort_key(item: RankedEvidence) -> tuple[float, int, int, str]:
    evidence = item.evidence
    return (
        -item.score,
        -_SOURCE_PRIORITY.get(_source_type(evidence), 0),
        len(evidence.locator),
        _stable_hash(f"{evidence.locator}\n{evidence.text}"),
    )


def _is_duplicate(
    existing: RetrievedEvidence,
    candidate: RetrievedEvidence,
    policy: EvidenceRankerPolicy,
) -> bool:
    if policy.dedup_locator_exact and existing.locator == candidate.locator:
        return True
    if _doc_chunk_id(existing) and _doc_chunk_id(existing) == _doc_chunk_id(candidate):
        return True
    if (
        _source_type(existing) == "grep"
        or _source_type(candidate) == "grep"
        or _source_type(existing) == "graph"
        or _source_type(candidate) == "graph"
    ):
        return False
    return _jaccard(_tokens(existing.text), _tokens(candidate.text)) >= policy.dedup_text_jaccard_threshold


def _intrinsic_rank_key(evidence: RetrievedEvidence) -> tuple[int, int, int, str]:
    source = _source_type(evidence)
    richness = sum(
        1
        for key in (
            "graph_paths",
            "doc_chunk_id",
            "chunk_id",
            "matched_pattern",
            "symbol_hint",
            "related_concept_ids",
            "concept_ids",
            "schema_paths",
        )
        if evidence.metadata.get(key)
    )
    return (
        _SOURCE_PRIORITY.get(source, 0),
        richness,
        len(evidence.text),
        _stable_hash(f"{evidence.locator}\n{evidence.text}"),
    )


def _source_type(evidence: RetrievedEvidence) -> str:
    mode = evidence.metadata.get("retrieval_mode") if evidence.metadata else None
    if isinstance(mode, str) and mode:
        return mode
    return str(evidence.source_type or "unknown")


def _doc_chunk_id(evidence: RetrievedEvidence) -> str | None:
    for key in ("doc_chunk_id", "chunk_id"):
        value = evidence.metadata.get(key) if evidence.metadata else None
        if isinstance(value, str) and value:
            return value
    return None


def _metadata_values(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _normalize_list(values: list[str] | None) -> list[str]:
    return _dedupe([str(value) for value in values or [] if value])


def _intersects(left: list[str], right: list[str]) -> bool:
    left_set = {item.casefold() for item in left if item}
    return any(item.casefold() in left_set for item in right if item)


def _prefix_intersects(left: list[str], right: list[str]) -> bool:
    for a in left:
        a_key = a.casefold()
        for b in right:
            b_key = b.casefold()
            if a_key.startswith(b_key) or b_key.startswith(a_key):
                return True
    return False


def _schema_field(path: str) -> str:
    return path.rsplit(".", 1)[-1].casefold()


def _issue_token_hits(issue_code: str, text: str) -> bool:
    tokens = [
        token
        for token in re.split(r"[^A-Za-z0-9_]+", issue_code.casefold())
        if len(token) >= 4 and token not in {"runtime", "export_xml", "lattice"}
    ]
    return any(token in text for token in tokens)


def _api_matches_concept(api_refs: list[str], concept_ids: list[str]) -> bool:
    api_text = " ".join(api_refs).casefold()
    for concept in concept_ids:
        tail = concept.rsplit(".", 1)[-1].replace("_", "").casefold()
        if tail and tail in api_text.replace("_", ""):
            return True
    return False


def _looks_like_repo_source(locator: str) -> bool:
    path = locator.split(":", 1)[0].split(" (", 1)[0]
    return path.startswith(("openmc_agent/", "tests/", "examples/", "docs/", "Input/"))


def _is_fact_gap(
    issue_codes: list[str],
    concept_ids: list[str],
    metadata: dict[str, Any],
) -> bool:
    if metadata.get("requires_human_confirmation"):
        return True
    haystack = " ".join([*issue_codes, *concept_ids]).casefold()
    return any(token in haystack for token in _FACT_GAP_TOKENS)


def _looks_like_unsafe_fact(evidence: RetrievedEvidence) -> bool:
    haystack = f"{evidence.locator}\n{evidence.text}"
    return any(pattern.search(haystack) for pattern in _UNSAFE_FACT_PATTERNS)


def _is_repetitive(text: str) -> bool:
    tokens = _tokens(text)
    if len(tokens) < 40:
        return False
    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    return unique_ratio < 0.18


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_RE.findall(text) if len(token) >= 2]


def _jaccard(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _dedup_key(evidence: RetrievedEvidence) -> str:
    chunk_id = _doc_chunk_id(evidence)
    if chunk_id:
        return f"chunk:{chunk_id}"
    return f"{_source_type(evidence)}:{evidence.locator}"


def _evidence_identity(evidence: RetrievedEvidence) -> str:
    return _stable_hash(
        f"{_source_type(evidence)}\n{evidence.locator}\n{_doc_chunk_id(evidence) or ''}"
    )


def _first_graph_path(evidence: RetrievedEvidence) -> str:
    graph_paths = evidence.metadata.get("graph_paths") if evidence.metadata else None
    if not isinstance(graph_paths, list) or not graph_paths:
        return ""
    first = graph_paths[0]
    if isinstance(first, dict):
        explanation = first.get("explanation")
        if isinstance(explanation, str):
            return explanation
        nodes = first.get("nodes")
        if isinstance(nodes, list):
            return " -> ".join(str(node) for node in nodes[:4])
    return ""


def _has_fact_gap_evidence(evidence: list[RetrievedEvidence]) -> bool:
    return any(item.metadata.get("requires_human_confirmation") for item in evidence)


def _one_line_preview(text: str) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= 260:
        return compact
    return compact[:257].rstrip() + "..."


def _summary_payload(
    ranked: list[RankedEvidence],
    selected: list[RetrievedEvidence],
    dropped_duplicates: list[RetrievedEvidence],
    dropped_low_score: list[RetrievedEvidence],
    dropped_budget: list[RetrievedEvidence],
) -> dict[str, Any]:
    scores = [item.score for item in ranked]
    return {
        "ranked_count": len(ranked),
        "selected_count": len(selected),
        "dropped_duplicate_count": len(dropped_duplicates),
        "dropped_low_score_count": len(dropped_low_score),
        "dropped_budget_count": len(dropped_budget),
        "evidence_score_min": min(scores) if scores else None,
        "evidence_score_max": max(scores) if scores else None,
        "evidence_score_mean": (sum(scores) / len(scores)) if scores else None,
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 6)))


def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped
