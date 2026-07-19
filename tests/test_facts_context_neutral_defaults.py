"""Tests for Phase 8B Step 2 + Phase 8C Step 0: context-neutral defaults.

Phase 8C Step 0 extends the contract from the PatchGenerationContext level
to the Pydantic schema level: ``FactsPatch.model_scope`` must default to
``"unknown"`` so that an LLM that omits the field cannot silently lock the
entire single-assembly patch family.  The previous schema default
``"single_assembly"`` was the authoritative BYPASS_PATH that contaminated
every multi-assembly benchmark.
"""

from openmc_agent.plan_builder.patch_generator import (
    ContextFactValue,
    PatchGenerationContext,
)
from openmc_agent.plan_builder.patches import FactsPatch
from openmc_agent.plan_builder.validators import PatchValidationContext


def test_model_scope_defaults_to_none():
    ctx = PatchGenerationContext()
    assert ctx.model_scope is None, (
        f"model_scope should default to None, got {ctx.model_scope!r}"
    )


def test_facts_patch_model_scope_defaults_to_unknown():
    """Phase 8C Step 0: schema-level default must NOT choose a patch family."""
    facts = FactsPatch()
    assert facts.model_scope == "unknown", (
        f"FactsPatch.model_scope default must be 'unknown' so that an "
        f"LLM that omits the field does not silently select the "
        f"single-assembly patch family. Got {facts.model_scope!r}."
    )


def test_facts_patch_boolean_feature_flags_are_none_by_default():
    """Tri-state booleans default to None so omission is observable.

    A ``False`` default would silently disable spacer grids / axial geometry
    / special pin maps when the LLM omits the field, which can mask a
    missing-coverage defect as a deliberate negative answer.
    """
    facts = FactsPatch()
    assert facts.has_axial_geometry is None
    assert facts.has_spacer_grids is None
    assert facts.has_special_pin_map is None


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


def test_neutral_defaults_do_not_force_single_assembly_family():
    """End-to-end regression: a FactsPatch with no explicit scope must not
    carry any value that downstream code can interpret as 'single-assembly
    decided'.  This protects assembler.py:1479-1481 where the scope selects
    the patch family.
    """
    facts = FactsPatch()
    dumped = facts.model_dump()
    assert dumped["model_scope"] == "unknown"
    assert dumped["has_axial_geometry"] is None
    assert dumped["has_spacer_grids"] is None
    assert dumped["has_special_pin_map"] is None
    # None of the optional scope-bearing fields should pick a concrete value
    # when the LLM omits them.
    assert dumped["assembly_count"] is None
    assert dumped["core_lattice_size"] is None
    assert dumped["selected_variant"] is None
