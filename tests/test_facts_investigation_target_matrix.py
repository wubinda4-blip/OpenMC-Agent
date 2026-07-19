"""Tests for Facts investigation semantic coverage matrix."""

from openmc_agent.plan_investigation.executor_injection import (
    FactsInvestigationTarget,
    FactsInvestigationCoverageMatrix,
    SemanticCoverageConfig,
    check_facts_semantic_coverage,
    _semantic_targets_for_feature_contract,
)


class FakeFeatureContract:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_target_defaults():
    target = FactsInvestigationTarget(
        target_id="model_scope",
        semantic_kind="model_scope",
        facts_json_path="/model_scope",
    )
    assert target.required is False
    assert target.covered is False
    assert target.source_backed is False


def test_matrix_defaults():
    matrix = FactsInvestigationCoverageMatrix()
    assert matrix.total_targets == 0
    assert matrix.fraction == 0.0


def test_semantic_coverage_config_defaults():
    config = SemanticCoverageConfig()
    assert "model_scope" in config.required_targets
    assert config.require_source_backed is True
    assert config.min_fraction == 0.5


def test_targets_from_simple_contract():
    contract = FakeFeatureContract(
        multi_assembly_core=False,
        has_spacer_grid=False,
        has_localized_insert=False,
        has_multiple_fuel_variants=False,
    )
    targets = _semantic_targets_for_feature_contract(contract)
    target_ids = {t.target_id for t in targets}
    assert "model_scope" in target_ids
    assert "assembly_count" in target_ids
    assert "fuel_variants" in target_ids
    assert "core_lattice_size" not in target_ids


def test_targets_from_multi_assembly_contract():
    contract = FakeFeatureContract(
        multi_assembly_core=True,
        has_spacer_grid=True,
        has_localized_insert=True,
        has_multiple_fuel_variants=True,
    )
    targets = _semantic_targets_for_feature_contract(contract)
    target_ids = {t.target_id for t in targets}
    assert "core_lattice_size" in target_ids
    assert "assembly_type_counts" in target_ids
    assert "fuel_variants" in target_ids


def test_check_semantic_coverage_empty_ledger():
    contract = FakeFeatureContract(multi_assembly_core=False)
    matrix = check_facts_semantic_coverage(
        ledger=None,
        feature_contract=contract,
    )
    assert matrix.total_targets > 0
    assert matrix.covered_targets == 0
    assert matrix.fraction == 0.0


def test_check_semantic_coverage_with_targets():
    contract = FakeFeatureContract(multi_assembly_core=True)
    targets = _semantic_targets_for_feature_contract(contract)
    matrix = check_facts_semantic_coverage(
        ledger={"claims": {}},
        feature_contract=contract,
        targets=targets,
    )
    assert matrix.total_targets == len(targets)


def test_required_target_not_covered_is_unresolved():
    contract = FakeFeatureContract(
        multi_assembly_core=True,
        has_spacer_grid=False,
        has_localized_insert=False,
        has_multiple_fuel_variants=False,
    )
    targets = _semantic_targets_for_feature_contract(contract)
    matrix = check_facts_semantic_coverage(
        ledger=None,
        feature_contract=contract,
        targets=targets,
    )
    required_ids = {t.target_id for t in matrix.targets if t.required}
    assert "model_scope" in required_ids
    assert "core_lattice_size" in required_ids
    assert "assembly_type_counts" in required_ids
