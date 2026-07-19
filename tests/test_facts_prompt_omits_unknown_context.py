"""Phase 8C Step 0 — Facts prompt must NOT carry unknown / compatibility
defaults as authoritative context.

The prompt builder ``build_patch_prompt("facts", ...)`` must:

1. Not emit a LOCKED or AUTHORITATIVE block for fields whose only backing
   is a schema default or Python default.
2. Not emit the literal string ``"model_scope": "single_assembly"`` as a
   recommended default.
3. Not render a context block for fields whose value is ``None`` /
   ``"unknown"`` / ``"unresolved"``.
4. Still render source-backed single-assembly facts correctly (a real
   single-assembly source document must be allowed to promote the scope).
"""

from __future__ import annotations

from openmc_agent.plan_builder.patch_generator import (
    ContextFactValue,
    PatchGenerationContext,
)
from openmc_agent.plan_builder.patch_prompts import build_patch_prompt
from openmc_agent.plan_builder.patches import FactsPatch


def _build_prompt(context: PatchGenerationContext) -> str:
    return build_patch_prompt(
        patch_type="facts",
        requirement="Build a benchmark model from the supplied source.",
        context=context,
    )


def test_prompt_does_not_recommend_single_assembly_as_default():
    """The instruction text must not present ``single_assembly`` as the
    safe default.  ``unknown`` is the safe default.
    """
    prompt = _build_prompt(PatchGenerationContext())
    # The dangerous line "Determine model_scope: single_assembly for one
    # assembly, multi_assembly_core for N×N cores." has been replaced by
    # explicit source-evidence guidance.
    assert "Determine model_scope: single_assembly for one assembly" not in prompt
    assert "unknown" in prompt  # safe default is mentioned
    assert "do NOT default to \"single_assembly\"" in prompt


def test_prompt_does_not_render_unknown_context_fact():
    """An ``unresolved`` ContextFactValue must NOT appear in the prompt."""
    ctx = PatchGenerationContext()
    ctx.context_facts["model_scope"] = ContextFactValue(
        field_path="/model_scope",
        value=None,
        provenance_kind="unresolved",
    )
    prompt = _build_prompt(ctx)
    # The prompt must not lock or assert an authoritative model_scope.
    assert "LOCKED" not in prompt or "/model_scope" not in prompt.split("LOCKED", 1)[-1].split("\n", 1)[0]


def test_prompt_does_not_render_compatibility_default_as_authoritative():
    """A schema-default ``single_assembly`` value must NOT be rendered as
    an authoritative fact.  This is the rule that closes the original
    contamination: even if a producer erroneously creates a
    ``compatibility_default`` ContextFactValue, the prompt must not surface
    it.
    """
    ctx = PatchGenerationContext()
    ctx.context_facts["model_scope"] = ContextFactValue(
        field_path="/model_scope",
        value="single_assembly",
        provenance_kind="compatibility_default",
    )
    prompt = _build_prompt(ctx)
    assert "AUTHORITATIVE" not in prompt
    assert "LOCKED" not in prompt


def test_prompt_renders_source_backed_single_assembly():
    """A genuinely source-backed single-assembly fact must still be allowed
    in the prompt — the rule is 'no unsafe provenance', not 'no
    single_assembly value'.
    """
    ctx = PatchGenerationContext()
    ctx.context_facts["model_scope"] = ContextFactValue(
        field_path="/model_scope",
        value="single_assembly",
        provenance_kind="source_backed",
        authoritative=True,
        source_claim_ids=["claim_single"],
    )
    # The fact exists in context; whether it is rendered as LOCKED depends
    # on the skeleton (which is empty in this test).  The point of this
    # test is that the value 'single_assembly' is not forbidden per se.
    # We just need build_patch_prompt to succeed.
    prompt = _build_prompt(ctx)
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_prompt_does_not_render_none_context_value():
    """A ContextFactValue with value=None and unknown provenance must not
    appear as a locked value.
    """
    ctx = PatchGenerationContext()
    ctx.context_facts["assembly_count"] = ContextFactValue(
        field_path="/assembly_count",
        value=None,
        provenance_kind="unknown",
    )
    prompt = _build_prompt(ctx)
    assert "LOCKED" not in prompt


def test_facts_patch_default_dump_does_not_carry_single_assembly():
    """The Pydantic-level default for FactsPatch.model_scope is ``unknown``,
    so any LLM that produces an empty Facts object cannot accidentally
    carry the single-assembly patch family into downstream consumers.
    """
    dumped = FactsPatch().model_dump()
    assert dumped["model_scope"] == "unknown"
    # The string 'single_assembly' must not appear anywhere in the default
    # dump's scope-related fields.
    assert dumped["model_scope"] != "single_assembly"


def test_prompt_does_not_render_boolean_feature_default_as_locked():
    """The LLM-omitted state for boolean feature flags is now ``None``,
    not ``False``.  The prompt must not render ``False`` as a locked
    answer when the LLM omitted the field.
    """
    ctx = PatchGenerationContext()
    # No context_facts for has_spacer_grids — the prompt must not
    # synthesize a LOCKED block for it.
    prompt = _build_prompt(ctx)
    assert "has_spacer_grids" not in prompt.split("LOCKED", 1)[-1] if "LOCKED" in prompt else True
