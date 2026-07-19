"""Tests for Phase 8B Step 2: context-neutral defaults and provenance tracking."""

from openmc_agent.plan_builder.patch_generator import (
    ContextFactValue,
    PatchGenerationContext,
)
from openmc_agent.plan_builder.validators import PatchValidationContext


def test_model_scope_defaults_to_none():
    ctx = PatchGenerationContext()
    assert ctx.model_scope is None, (
        f"model_scope should default to None, got {ctx.model_scope!r}"
    )


def test_context_fact_value_defaults():
    cfv = ContextFactValue(field_path="/model_scope")
    assert cfv.provenance_kind == "unresolved"
    assert cfv.confidence == 0.0
    assert cfv.authoritative is False
    assert cfv.value is None


def test_context_fact_value_with_source():
    cfv = ContextFactValue(
        field_path="/model_scope",
        value="multi_assembly_core",
        provenance_kind="source_backed",
        confidence=0.95,
        source_claim_ids=["claim_001"],
        source_span_ids=["span_001"],
    )
    assert cfv.provenance_kind == "source_backed"
    assert cfv.value == "multi_assembly_core"
    assert cfv.confidence == 0.95


def test_context_facts_in_gen_context():
    ctx = PatchGenerationContext()
    assert ctx.context_facts == {}
    ctx.context_facts["model_scope"] = ContextFactValue(
        field_path="/model_scope",
        value="single_assembly",
        provenance_kind="human_confirmed",
        authoritative=True,
    )
    assert len(ctx.context_facts) == 1
    assert ctx.context_facts["model_scope"].authoritative is True


def test_facts_consistency_still_passes_with_none_scope():
    from openmc_agent.plan_builder.planning_scope import planning_feature_contract
    from openmc_agent.plan_builder.closed_loop.facts_consistency import (
        run_facts_consistency_preflight,
    )

    contract = planning_feature_contract({"feature_summary": {}})
    result = run_facts_consistency_preflight(
        feature_contract=contract,
        facts_patch={"patch_type": "facts"},
    )
    assert result.ok
    assert not any(
        "model_scope" in item.get("code", "") for item in result.issues
    )


def test_validation_context_model_scope_default_none():
    vctx = PatchValidationContext()
    assert vctx.model_scope is None
