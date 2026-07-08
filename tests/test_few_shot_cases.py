"""Tests for few_shot_cases: slim IR, structural-feature extraction, loaders."""
import json

from openmc_agent.few_shot_cases import (
    extract_structural_features,
    list_gold_case_ids,
    load_gold_case_meta,
    load_monolithic_few_shot,
    load_patch_few_shots,
    slim_ir_from_plan,
)


# ---------------------------------------------------------------------------
# slim_ir_from_plan
# ---------------------------------------------------------------------------


def _sample_assembly_plan() -> dict:
    return {
        "schema_version": "simulation_plan.v2",
        "model_spec": None,
        "complex_model": {
            "name": "Sample",
            "kind": "assembly",
            "materials": [
                {
                    "id": "fuel",
                    "name": "UO2",
                    "density_unit": "g/cm3",
                    "density_value": 10.257,
                    "composition": [
                        {"name": "U235", "percent": 3.1, "percent_type": "wo", "kind": "nuclide"}
                    ],
                    "chemical_formula": None,
                    "macroscopic": None,
                    "enrichment_percent": None,
                    "enrichment_target": None,
                    "enrichment_type": None,
                    "temperature_k": 600.0,
                    "source": "benchmark table X",
                    "assumptions": ["density assumed"],
                    "requires_human_confirmation": [],
                    "volume_cm3": None,
                }
            ],
            "lattices": [],
            "core": None,
        },
        "capability_report": {"supported_renderer": "assembly"},
        "execution_check": {"smoke": True},
        "plot_specs": [],
        "expert_assumptions": [],
        "expert_feedback": [],
    }


def test_slim_ir_strips_metadata_and_runtime_fields() -> None:
    slim = slim_ir_from_plan(_sample_assembly_plan())
    blob = json.dumps(slim)
    for forbidden in (
        "source",
        "assumptions",
        "requires_human_confirmation",
        "volume_cm3",
        "macroscopic",
        "capability_report",
        "execution_check",
        "plot_specs",
        "expert_assumptions",
        "expert_feedback",
    ):
        assert forbidden not in blob, f"{forbidden!r} should be stripped"


def test_slim_ir_preserves_structural_fields() -> None:
    slim = slim_ir_from_plan(_sample_assembly_plan())
    cm = slim["complex_model"]
    assert cm["kind"] == "assembly"
    assert cm["materials"][0]["id"] == "fuel"
    assert cm["materials"][0]["density_value"] == 10.257
    assert cm["materials"][0]["composition"][0]["name"] == "U235"


def test_slim_ir_compacts_long_lattice_pattern() -> None:
    plan = _sample_assembly_plan()
    plan["complex_model"]["lattices"] = [
        {
            "id": "lat",
            "pitch_cm": [1.26, 1.26],
            "universe_pattern": [[f"u{c}" for c in range(17)] for _ in range(17)],
        }
    ]
    slim = slim_ir_from_plan(plan)
    pattern = slim["complex_model"]["lattices"][0]["universe_pattern"]
    assert len(pattern) == 5
    assert pattern[3] == ["..."]
    assert pattern[0][0] == "u0"
    assert pattern[4][0] == "u0"


def test_slim_ir_keeps_short_lattice_pattern_intact() -> None:
    plan = _sample_assembly_plan()
    plan["complex_model"]["lattices"] = [
        {
            "id": "lat",
            "pitch_cm": [1.26, 1.26],
            "universe_pattern": [["a", "b"], ["c", "d"]],
        }
    ]
    slim = slim_ir_from_plan(plan)
    pattern = slim["complex_model"]["lattices"][0]["universe_pattern"]
    assert pattern == [["a", "b"], ["c", "d"]]


# ---------------------------------------------------------------------------
# extract_structural_features
# ---------------------------------------------------------------------------


def test_extract_features_assembly_with_overlay() -> None:
    feats = extract_structural_features(
        "Build a 17x17 fuel assembly with spacer grids and 3D axial layers"
    )
    assert "assembly" in feats
    assert "17x17" in feats
    assert "axial_overlay" in feats
    assert "3d" in feats


def test_extract_features_pin_cell() -> None:
    feats = extract_structural_features("a single reflective UO2 pin cell")
    assert "pin_cell" in feats
    assert "assembly" not in feats


def test_extract_features_quarter_core_with_reflector() -> None:
    feats = extract_structural_features(
        "quarter core symmetry with radial reflector and control rods"
    )
    assert "core" in feats
    assert "quarter" in feats
    assert "reflector" in feats
    assert "control_rod" in feats


# ---------------------------------------------------------------------------
# gold-case loaders
# ---------------------------------------------------------------------------


def test_list_gold_case_ids_includes_assembly_3d() -> None:
    assert "assembly_3d_with_spacer_grids" in list_gold_case_ids()


def test_load_gold_case_meta_has_structural_features() -> None:
    meta = load_gold_case_meta("assembly_3d_with_spacer_grids")
    assert "structural_features" in meta
    assert "assembly" in meta["structural_features"]


def test_load_monolithic_few_shot_returns_slim_ir_and_digest() -> None:
    fs = load_monolithic_few_shot("assembly_3d_with_spacer_grids")
    assert "slim_ir" in fs and "digest" in fs
    assert fs["slim_ir"]["complex_model"]["kind"] == "assembly"


def test_load_patch_few_shots_materials_from_assembly_3d() -> None:
    patches = load_patch_few_shots("materials", ["assembly_3d_with_spacer_grids"])
    assert len(patches) == 1
    assert patches[0]["patch_type"] == "materials"


def test_load_patch_few_shots_empty_for_case_without_patches() -> None:
    assert load_patch_few_shots("materials", ["pin_cell_basic"]) == []


def test_load_patch_few_shots_respects_limit() -> None:
    patches = load_patch_few_shots(
        "materials", ["assembly_3d_with_spacer_grids"], limit=0
    )
    assert patches == []
