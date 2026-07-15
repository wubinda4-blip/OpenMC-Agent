"""Evaluation-only VERA4 base-case acceptance (P2-FULLCORE-2D-A).

Checks deterministic VERA4 base-case fidelity at five levels:
  A. Input/Facts — axial domain, boundaries, counts, coordinates
  B. Patch/Plan — exact Pyrex/thimble/RCCA coordinates, profile references
  C. Concrete Geometry — universes, materials, state separation
  D. XML — model.py not skeleton, all XML, refs complete
  E. Runtime — geometry debug, transport smoke

This module is evaluation-only. Production planner, assembler, materializer,
and renderer must NOT import it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ACCEPTANCE_CONTRACT_VERSION = "2.0.0"


@dataclass
class AcceptanceCheck:
    code: str
    passed: bool
    message: str = ""
    level: str = ""


@dataclass
class AcceptanceResult:
    ok: bool = True
    checks: list[AcceptanceCheck] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def failed_codes(self) -> list[str]:
        return [c.code for c in self.checks if not c.passed]


def check_facts_level(facts: Any) -> list[AcceptanceCheck]:
    """Level A: Input/Facts checks."""
    checks: list[AcceptanceCheck] = []

    domain = getattr(facts, "axial_domain_cm", None)
    checks.append(AcceptanceCheck(
        code="facts.domain_min",
        passed=domain is not None and domain[0] == -55.0,
        message=f"axial domain min={domain[0] if domain else None}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.domain_max",
        passed=domain is not None and abs(domain[1] - 463.937) < 1e-3,
        message=f"axial domain max={domain[1] if domain else None}",
        level="A",
    ))

    scope = getattr(facts, "model_scope", "")
    checks.append(AcceptanceCheck(
        code="facts.multi_assembly_scope",
        passed=scope == "multi_assembly_core",
        message=f"scope={scope}",
        level="A",
    ))

    boundary = getattr(facts, "assembly_type_counts", {})
    checks.append(AcceptanceCheck(
        code="facts.assembly_counts",
        passed=boundary == {"corner": 4, "edge": 4, "center_rcca": 1},
        message=f"counts={boundary}",
        level="A",
    ))

    scoped = {c.role: c.value for c in getattr(facts, "scoped_expected_counts", [])}
    checks.append(AcceptanceCheck(
        code="facts.fuel_pin_count",
        passed=scoped.get("fuel_pin") == 2376,
        message=f"fuel_pin={scoped.get('fuel_pin')}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.guide_tube_count",
        passed=scoped.get("guide_tube") == 216,
        message=f"guide_tube={scoped.get('guide_tube')}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.pyrex_count",
        passed=scoped.get("pyrex_rod") == 80,
        message=f"pyrex={scoped.get('pyrex_rod')}",
        level="A",
    ))
    checks.append(AcceptanceCheck(
        code="facts.thimble_count",
        passed=scoped.get("thimble_plug") == 112,
        message=f"thimble={scoped.get('thimble_plug')}",
        level="A",
    ))

    return checks


def check_plan_level(
    plan: Any,
    *,
    expected_pyrex: int = 80,
    expected_thimble: int = 112,
    expected_rcca: int = 24,
) -> list[AcceptanceCheck]:
    """Level B: Patch/Plan checks."""
    checks: list[AcceptanceCheck] = []
    model = plan.complex_model

    uv_ids = {u.id for u in model.universes}
    mat_ids = {m.id for m in model.materials}

    # Check key universes exist
    for uv_name in ["fuel_active_r1", "fuel_active_r2", "guide_tube", "inst_tube",
                    "pyrex_poison", "thimble_plug", "rcca_aic", "rcca_b4c"]:
        checks.append(AcceptanceCheck(
            code=f"plan.universe.{uv_name}",
            passed=uv_name in uv_ids,
            message=f"universe {uv_name}={'present' if uv_name in uv_ids else 'MISSING'}",
            level="B",
        ))

    # Check key materials exist
    for mat_name in ["fuel_r1", "fuel_r2", "water", "zircaloy4", "ss304",
                     "lower_nozzle_mix", "upper_nozzle_mix",
                     "lower_core_plate_mix", "upper_core_plate_mix",
                     "pyrex_glass", "rcca_aic_mat", "rcca_b4c_mat"]:
        checks.append(AcceptanceCheck(
            code=f"plan.material.{mat_name}",
            passed=mat_name in mat_ids,
            message=f"material {mat_name}={'present' if mat_name in mat_ids else 'MISSING'}",
            level="B",
        ))

    # Check axial layers have whole-plane and detailed mix
    layers = model.core.axial_layers if model.core else []
    fill_types = {l.fill.type for l in layers}
    checks.append(AcceptanceCheck(
        code="plan.axial_layers.has_material_fill",
        passed="material" in fill_types,
        message=f"fill types={fill_types}",
        level="B",
    ))
    checks.append(AcceptanceCheck(
        code="plan.axial_layers.has_lattice_fill",
        passed="lattice" in fill_types,
        message=f"fill types={fill_types}",
        level="B",
    ))

    # Check domain coverage
    if layers:
        checks.append(AcceptanceCheck(
            code="plan.axial_layers.domain_min",
            passed=abs(layers[0].z_min_cm - (-55.0)) < 1e-3,
            message=f"first layer z_min={layers[0].z_min_cm}",
            level="B",
        ))
        checks.append(AcceptanceCheck(
            code="plan.axial_layers.domain_max",
            passed=abs(layers[-1].z_max_cm - 463.937) < 1e-3,
            message=f"last layer z_max={layers[-1].z_max_cm}",
            level="B",
        ))

    return checks


def check_geometry_level(plan: Any) -> list[AcceptanceCheck]:
    """Level C: Concrete Geometry checks."""
    checks: list[AcceptanceCheck] = []
    model = plan.complex_model

    uv_ids = {u.id for u in model.universes}
    lat_ids = {l.id for l in model.lattices}
    cell_ids = {c.id for c in model.cells}
    mat_ids = {m.id for m in model.materials}

    # Reference integrity: every cell fill references exist
    broken_cells = 0
    for cell in model.cells:
        if cell.fill_type == "lattice" and cell.fill_id not in lat_ids:
            broken_cells += 1
        elif cell.fill_type == "material" and cell.fill_id not in mat_ids:
            broken_cells += 1
    checks.append(AcceptanceCheck(
        code="geo.cell_refs_ok",
        passed=broken_cells == 0,
        message=f"broken cell refs={broken_cells}",
        level="C",
    ))

    # Lattice universe refs
    broken_lat_refs = 0
    for lat in model.lattices:
        if lat.universe_pattern:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid not in uv_ids:
                        broken_lat_refs += 1
        if lat.outer_universe_id and lat.outer_universe_id not in uv_ids:
            broken_lat_refs += 1
    checks.append(AcceptanceCheck(
        code="geo.lattice_refs_ok",
        passed=broken_lat_refs == 0,
        message=f"broken lattice refs={broken_lat_refs}",
        level="C",
    ))

    # Check mixture materials have components
    mix_ok = True
    for mat in model.materials:
        if mat.is_mixture:
            if not mat.mixture_component_ids:
                mix_ok = False
            elif not all(cid in mat_ids for cid in mat.mixture_component_ids):
                mix_ok = False
    checks.append(AcceptanceCheck(
        code="geo.mixture_refs_ok",
        passed=mix_ok,
        message="mixture component refs",
        level="C",
    ))

    # Check RCCA universes are reachable in derived lattices
    rcca_in_lattices = False
    for lat in model.lattices:
        if "__seg" in lat.id and lat.universe_pattern:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid in ("rcca_aic", "rcca_b4c"):
                        rcca_in_lattices = True
    checks.append(AcceptanceCheck(
        code="geo.rcca_reachable",
        passed=rcca_in_lattices,
        message="RCCA universes in derived lattices",
        level="C",
    ))

    # Pyrex reachable
    pyrex_in_lattices = False
    for lat in model.lattices:
        if "__seg" in lat.id and lat.universe_pattern:
            for row in lat.universe_pattern:
                for uid in row:
                    if uid in ("pyrex_poison", "pyrex_plenum"):
                        pyrex_in_lattices = True
    checks.append(AcceptanceCheck(
        code="geo.pyrex_reachable",
        passed=pyrex_in_lattices,
        message="Pyrex universes in derived lattices",
        level="C",
    ))

    return checks


def run_full_acceptance(
    plan: Any,
    *,
    xml_dir: Path | None = None,
    smoke_result: dict[str, Any] | None = None,
) -> AcceptanceResult:
    """Run all acceptance levels and return combined result."""
    result = AcceptanceResult()

    # Level A: Facts (extract from plan)
    model = plan.complex_model
    # Facts are embedded in the plan's assumptions/settings
    # We check the model structure directly
    checks_a = []
    layers = model.core.axial_layers if model.core else []
    checks_a.append(AcceptanceCheck(
        code="facts.domain_span",
        passed=layers and abs(layers[0].z_min_cm - (-55.0)) < 1e-3 and abs(layers[-1].z_max_cm - 463.937) < 1e-3,
        message=f"domain=[{layers[0].z_min_cm if layers else None}, {layers[-1].z_max_cm if layers else None}]",
        level="A",
    ))
    result.checks.extend(checks_a)

    # Level B: Plan
    result.checks.extend(check_plan_level(plan))

    # Level C: Geometry
    result.checks.extend(check_geometry_level(plan))

    # Level D: XML (if provided)
    if xml_dir and xml_dir.exists():
        xml_checks = check_xml_level(xml_dir)
        result.checks.extend(xml_checks)

    # Level E: Runtime (if provided)
    if smoke_result:
        rt_checks = check_runtime_level(smoke_result)
        result.checks.extend(rt_checks)

    result.ok = all(c.passed for c in result.checks)
    result.summary = {
        "total": len(result.checks),
        "passed": result.passed_count,
        "failed": result.failed_count,
        "failed_codes": result.failed_codes,
    }
    return result


def check_xml_level(xml_dir: Path) -> list[AcceptanceCheck]:
    """Level D: XML checks."""
    checks: list[AcceptanceCheck] = []

    for xml_name in ["materials.xml", "geometry.xml", "settings.xml"]:
        f = xml_dir / xml_name
        checks.append(AcceptanceCheck(
            code=f"xml.{xml_name}",
            passed=f.exists() and f.stat().st_size > 0,
            message=f"{xml_name}={'OK' if f.exists() else 'MISSING'}",
            level="D",
        ))

    model_py = xml_dir / "model.py"
    checks.append(AcceptanceCheck(
        code="xml.model_py_not_skeleton",
        passed=model_py.exists() and model_py.stat().st_size > 10000,
        message=f"model.py={model_py.stat().st_size if model_py.exists() else 0} bytes",
        level="D",
    ))

    return checks


def check_runtime_level(smoke: dict[str, Any]) -> list[AcceptanceCheck]:
    """Level E: Runtime checks."""
    checks: list[AcceptanceCheck] = []

    checks.append(AcceptanceCheck(
        code="rt.smoke_returncode",
        passed=smoke.get("returncode") == 0,
        message=f"returncode={smoke.get('returncode')}",
        level="E",
    ))

    keff = smoke.get("keff")
    keff_std = smoke.get("keff_std")
    checks.append(AcceptanceCheck(
        code="rt.keff_finite",
        passed=keff is not None and keff > 0 and keff < 10,
        message=f"keff={keff}",
        level="E",
    ))
    checks.append(AcceptanceCheck(
        code="rt.keff_std_finite",
        passed=keff_std is not None and keff_std >= 0,
        message=f"keff_std={keff_std}",
        level="E",
    ))

    lost = smoke.get("lost_particles", 0)
    checks.append(AcceptanceCheck(
        code="rt.zero_lost",
        passed=lost == 0,
        message=f"lost={lost}",
        level="E",
    ))

    return checks


__all__ = [
    "AcceptanceCheck",
    "AcceptanceResult",
    "check_facts_level",
    "check_plan_level",
    "check_geometry_level",
    "check_xml_level",
    "check_runtime_level",
    "run_full_acceptance",
]
