"""Tests for material normalization provenance reports."""

from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.material_normalization import normalize_material_semantics
from openmc_agent.material_validation import (
    validate_normalized_material,
    write_material_normalization_report,
)
from openmc_agent.schemas import CompositionValueBasis, MaterialSpec, NuclideSpec


def _n(name, pct, pt="ao"):
    return NuclideSpec(name=name, percent=pct, percent_type=pt)


def test_provenance_report_written(tmp_path):
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, norm = normalize_material_semantics(fuel)
    inv = validate_normalized_material(new_fuel)

    path = write_material_normalization_report(
        tmp_path, [(norm, inv)],
        run_id="test_run", git_sha="abc123",
    )
    report = json.loads(Path(path).read_text())
    assert report["contract_version"] == "1.0.0"
    assert report["run_id"] == "test_run"
    assert report["git_sha"] == "abc123"
    assert len(report["materials"]) == 1
    mat = report["materials"][0]
    assert mat["material_name"] == "fuel"
    assert mat["original_basis"] == "stoichiometric_ratio"
    assert mat["normalized_basis"] == "atom_fraction"
    assert mat["normalization_status"] == "deterministically_normalized"
    assert mat["deterministic"] is True
    assert mat["original_hash"]
    assert mat["normalized_hash"]
    assert mat["renderer_input_hash"] == mat["normalized_hash"]


def test_provenance_records_operations(tmp_path):
    water = MaterialSpec(
        name="borated_water", density_unit="g/cm3", density_value=0.743,
        composition=[_n("B10", 0.001066), _n("B11", 0.004), _n("H1", 0.666), _n("O16", 0.329)],
        composition_basis=CompositionValueBasis.PPM_BY_WEIGHT,
    )
    new_water, norm = normalize_material_semantics(water)
    inv = validate_normalized_material(new_water)

    path = write_material_normalization_report(tmp_path, [(norm, inv)])
    report = json.loads(Path(path).read_text())
    mat = report["materials"][0]
    assert len(mat["operations"]) == 1
    op = mat["operations"][0]
    assert op["operation"] == "boron_ppm_to_atom_fraction"
    assert "ppm_value" in op["parameters"]


def test_provenance_summary(tmp_path):
    fuel = MaterialSpec(
        name="fuel", density_unit="g/cm3", density_value=10.0,
        composition=[_n("U235", 2.619), _n("U238", 97.381), _n("O16", 2.0)],
        composition_basis=CompositionValueBasis.STOICHIOMETRIC_RATIO,
    )
    new_fuel, norm = normalize_material_semantics(fuel)
    inv = validate_normalized_material(new_fuel)

    write_material_normalization_report(tmp_path, [(norm, inv)])
    report = json.loads((tmp_path / "material_normalization_report.json").read_text())
    s = report["summary"]
    assert s["total_materials"] == 1
    assert s["deterministically_normalized"] == 1
    assert s["ambiguous"] == 0
    assert s["all_render_ready"] is True
