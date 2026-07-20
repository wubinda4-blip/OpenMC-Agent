"""Input-driven semantic coverage tests."""

from __future__ import annotations

from types import SimpleNamespace

from openmc_agent.plan_investigation.semantic_coverage import compile_semantic_coverage


class _Ledger:
    def __init__(self, claims):
        self.claims = claims


def _claim(*, claim_id, predicate, source_refs=(), qualifiers=None, subject=""):
    return SimpleNamespace(
        claim_id=claim_id,
        predicate=predicate,
        source_refs=source_refs,
        confirmed_by_human=False,
        qualifiers=qualifiers or {},
        metadata={},
        value=None,
        required_by_json_paths=(),
        subject=subject,
    )


def test_facts_coverage_matches_semantic_predicate_and_source_backing() -> None:
    context = SimpleNamespace(patch_type="facts", feature_contract=None)
    ledger = _Ledger({"c1": _claim(claim_id="c1", predicate="model_scope", source_refs=("s1",))})
    coverage = compile_semantic_coverage(
        context=context, ledger=ledger, evidence_claim_ids=["c1"]
    )
    assert coverage.covered_targets == 1
    assert coverage.source_backed_targets == 1
    assert coverage.coverage_complete


def test_material_requirement_targets_are_input_driven() -> None:
    requirement = SimpleNamespace(requirement_id="fuel_region_2", role="fuel")
    context = SimpleNamespace(
        patch_type="materials",
        material_requirement_set=SimpleNamespace(requirements=[requirement]),
    )
    ledger = _Ledger(
        {
            "c1": _claim(
                claim_id="c1",
                predicate="material_role_required",
                source_refs=("s1",),
                qualifiers={"requirement_id": "fuel_region_2"},
            )
        }
    )
    coverage = compile_semantic_coverage(context=context, ledger=ledger)
    assert coverage.targets[0].target_id == "materials:fuel_region_2"
    assert coverage.coverage_complete


def test_universe_requirement_without_matching_claim_remains_incomplete() -> None:
    requirement = SimpleNamespace(
        requirement_id="plug_c",
        profile_kind="plug",
        component_kind="guide_tube",
    )
    context = SimpleNamespace(
        patch_type="universes",
        universe_requirement_set=SimpleNamespace(requirements=[requirement]),
    )
    coverage = compile_semantic_coverage(context=context, ledger=_Ledger({}))
    assert coverage.unresolved_targets == 1
    assert not coverage.coverage_complete

def test_explicit_unresolved_claim_counts_as_addressed() -> None:
    requirement = SimpleNamespace(requirement_id="plug_c", profile_kind="plug")
    context = SimpleNamespace(
        patch_type="universes",
        universe_requirement_set=SimpleNamespace(requirements=[requirement]),
    )
    unresolved = _claim(
        claim_id="c_unresolved",
        predicate="universe_requirement",
        qualifiers={"requirement_id": "plug_c"},
    )
    unresolved.status = "unresolved"
    coverage = compile_semantic_coverage(
        context=context, ledger=_Ledger({"c_unresolved": unresolved})
    )
    assert coverage.coverage_complete
    assert coverage.explicit_unresolved_targets == 1