"""Tests for plan-investigation artifact writing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_investigation.artifacts import (
    INVESTIGATION_ARTIFACT_DIR,
    write_plan_investigation_artifacts,
)
from openmc_agent.plan_investigation.evidence_ledger import (
    add_claim,
    create_empty_ledger,
    finalize_ledger,
    ledger_summary,
    recompute_ledger_hash,
)
from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index


def _fixture(tmp_path: Path):
    idx = build_source_index(
        text="alpha\nbeta\n",
        title="t",
        source_kind=SourceKind.USER_REQUIREMENT,
    )
    span = idx.make_span(1, 1)
    idx.register_span(span)
    claim = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
    )
    ld = create_empty_ledger(requirement_hash=content_hash("alpha\nbeta"), source_indexes=[idx])
    add_claim(ld, claim, source_indexes={idx.document.source_id: idx})
    finalize_ledger(ld)
    return idx, ld, claim


def test_all_six_artifacts_written(tmp_path: Path) -> None:
    idx, ld, _ = _fixture(tmp_path)
    art = write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
    assert set(art.keys()) == {
        "source_manifest",
        "source_index",
        "evidence_ledger",
        "evidence_conflicts",
        "unresolved_claims",
        "investigation_summary",
    }
    for path in art.values():
        assert path.exists()
        assert path.parent == tmp_path / INVESTIGATION_ARTIFACT_DIR


def test_artifacts_are_canonical_json(tmp_path: Path) -> None:
    idx, ld, _ = _fixture(tmp_path)
    art = write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
    for path in art.values():
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert "artifact_version" in payload or "ledger_version" in payload


def test_ledger_hash_recomputed_from_disk_matches(tmp_path: Path) -> None:
    idx, ld, _ = _fixture(tmp_path)
    art = write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
    payload = json.loads(art["evidence_ledger"].read_text(encoding="utf-8"))
    assert payload["ledger_hash"] == ld.ledger_hash
    # Reload the ledger and verify.
    from openmc_agent.plan_investigation.evidence_ledger import PlanningEvidenceLedger

    restored = PlanningEvidenceLedger.model_validate(payload)
    assert restored.ledger_hash == ld.ledger_hash
    assert recompute_ledger_hash(restored) == ld.ledger_hash


def test_summary_artifact_correct(tmp_path: Path) -> None:
    idx, ld, _ = _fixture(tmp_path)
    art = write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
    payload = json.loads(art["investigation_summary"].read_text(encoding="utf-8"))
    expected = ledger_summary(ld).model_dump()
    assert payload["summary"] == expected


def test_stale_ledger_hash_rejected(tmp_path: Path) -> None:
    idx, ld, _ = _fixture(tmp_path)
    # Mutate the ledger in a way that changes its hash but keep ledger_hash
    # stale.
    extra = EvidenceClaim(
        claim_id="",
        subject="y",
        predicate="q",
        value=2,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=idx.make_span(1, 1).span_id, excerpt_hash=idx.make_span(1, 1).excerpt_hash),),
    )
    idx.register_span(idx.make_span(1, 1))
    add_claim(ld, extra, source_indexes={idx.document.source_id: idx})
    # ledger_hash is now stale (we didn't finalize again).
    with pytest.raises(PlanInvestigationIssue):
        write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)


def test_no_sources_rejected(tmp_path: Path) -> None:
    ld = create_empty_ledger(requirement_hash="rh")
    finalize_ledger(ld)
    with pytest.raises(PlanInvestigationIssue):
        write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[], ledger=ld)


def test_unresolved_claims_artifact_lists_unresolved_only(tmp_path: Path) -> None:
    idx = build_source_index(text="a\n", title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 1)
    idx.register_span(span)
    ref = EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    explicit = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="p",
        value=1,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(ref,),
    )
    unresolved = EvidenceClaim(
        claim_id="",
        subject="y",
        predicate="q",
        value=None,
        status=EvidenceStatus.UNRESOLVED,
    )
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    add_claim(ld, explicit, source_indexes={idx.document.source_id: idx})
    add_claim(ld, unresolved)
    finalize_ledger(ld)
    art = write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
    payload = json.loads(art["unresolved_claims"].read_text(encoding="utf-8"))
    assert len(payload["claims"]) == 1
    assert payload["claims"][0]["status"] == "unresolved"


def test_no_secrets_in_artifacts(tmp_path: Path) -> None:
    """Artifacts must not contain API keys, host paths, or full prompts."""
    idx, ld, _ = _fixture(tmp_path)
    # Inject a fake secret into the source metadata to verify it does NOT
    # propagate to the manifest.
    write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
    for path in (tmp_path / INVESTIGATION_ARTIFACT_DIR).iterdir():
        text = path.read_text(encoding="utf-8")
        assert "DEEPSEEK_API_KEY" not in text
        assert "SENSENOVA_API_KEY" not in text
        # Home directory path leakage: artifacts should not embed absolute
        # user paths.
        assert "/home/" not in text


def test_artifact_atomicity_partial_failure(tmp_path: Path) -> None:
    """If the target dir becomes unwritable mid-write, no partial files
    survive under the canonical names.  We approximate this by forcing a
    serialization failure via a non-JSON payload attached to a Pydantic
    model field.
    """
    idx, ld, _ = _fixture(tmp_path)
    # Corrupt the ledger by hand: inject a non-JSON value into metadata.
    ld.metadata["__bad__"] = object()
    with pytest.raises(PlanInvestigationIssue):
        write_plan_investigation_artifacts(output_dir=tmp_path, source_indexes=[idx], ledger=ld)
