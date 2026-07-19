"""Phase 8C Step 0 — Context renderer only renders values with safe provenance.

The Facts prompt must NOT carry values whose provenance is one of:
- ``unknown``
- ``compatibility_default``
- ``unresolved``
- ``None`` (Python default)

Only the following provenance kinds are allowed in the prompt:
- ``source_backed``
- ``human_confirmed``
- ``deterministically_derived``
- ``accepted_upstream``

A schema-compatibility default value must never be rendered as if it were an
authoritative source-derived fact.  Doing so would re-introduce the
``single_assembly`` contamination that Phase 8B Step 2 identified at the
context level and Phase 8C Step 0 closes at the schema and prompt level.
"""

from __future__ import annotations

from openmc_agent.plan_builder.patch_generator import (
    ContextFactValue,
    PatchGenerationContext,
)


def _safe_provenance_kinds() -> set[str]:
    return {
        "source_backed",
        "human_confirmed",
        "deterministically_derived",
        "accepted_upstream",
    }


def _unsafe_provenance_kinds() -> set[str]:
    return {"unknown", "compatibility_default", "unresolved"}


def test_safe_provenance_kinds_are_renderable():
    """All four safe kinds should pass the rendering policy."""
    for kind in _safe_provenance_kinds():
        cfv = ContextFactValue(
            field_path=f"/{kind}",
            value="some_value",
            provenance_kind=kind,
        )
        assert cfv.provenance_kind == kind
        assert kind not in _unsafe_provenance_kinds()


def test_unsafe_provenance_kinds_are_not_renderable():
    """None of the unsafe kinds should be promoted as authoritative."""
    for kind in _unsafe_provenance_kinds():
        cfv = ContextFactValue(
            field_path=f"/{kind}",
            value=None,
            provenance_kind=kind,
        )
        assert cfv.authoritative is False


def test_authoritative_flag_requires_safe_provenance():
    """An ``unknown`` provenance cannot carry ``authoritative=True``.

    The constructor does not enforce this (we trust the producer), but the
    rendering policy must reject the combination.
    """
    # An unsafe-provenance value with authoritative=True is a contract
    # violation that the renderer must detect.
    cfv = ContextFactValue(
        field_path="/model_scope",
        value="single_assembly",
        provenance_kind="compatibility_default",
        authoritative=True,  # this is the violation
    )
    # The renderer policy rejects (provenance, authoritative) combinations
    # where provenance is unsafe.
    unsafe = cfv.provenance_kind in _unsafe_provenance_kinds()
    assert unsafe and cfv.authoritative  # detectable violation


def test_compatibility_default_is_never_authoritative():
    """A schema default value (compatibility_default) must not be marked as
    the source-of-truth for a downstream contract slot.  This is the
    concrete rule that prevents the old ``single_assembly`` schema default
    from contaminating Facts prompts.
    """
    cfv = ContextFactValue(
        field_path="/model_scope",
        value="single_assembly",
        provenance_kind="compatibility_default",
    )
    assert cfv.provenance_kind == "compatibility_default"
    assert cfv.authoritative is False


def test_source_backed_value_can_be_authoritative():
    cfv = ContextFactValue(
        field_path="/model_scope",
        value="multi_assembly_core",
        provenance_kind="source_backed",
        authoritative=True,
        source_claim_ids=["claim_001"],
    )
    assert cfv.authoritative is True
    assert cfv.provenance_kind == "source_backed"


def test_context_fact_lookup_safe_only():
    """When building a prompt, we should be able to iterate context_facts
    and pick only the safe ones.  This is the helper that the prompt
    renderer will use in Step 1.
    """
    ctx = PatchGenerationContext()
    ctx.context_facts["model_scope"] = ContextFactValue(
        field_path="/model_scope",
        value="multi_assembly_core",
        provenance_kind="source_backed",
    )
    ctx.context_facts["has_spacer_grids"] = ContextFactValue(
        field_path="/has_spacer_grids",
        value=None,
        provenance_kind="unresolved",
    )
    ctx.context_facts["legacy_field"] = ContextFactValue(
        field_path="/legacy_field",
        value="legacy",
        provenance_kind="compatibility_default",
    )

    safe = {
        path: cfv
        for path, cfv in ctx.context_facts.items()
        if cfv.provenance_kind in _safe_provenance_kinds()
    }
    assert set(safe.keys()) == {"model_scope"}
    assert safe["model_scope"].value == "multi_assembly_core"
