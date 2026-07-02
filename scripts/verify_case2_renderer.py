"""Deterministic case2 verification: drive the plan graph with a case2-shaped plan.

This mirrors exactly what scripts/run_inspect.sh does AFTER the LLM produces a
SimulationPlan, but replaces the LLM call with a fixed plan so it runs offline.

The plan encodes case2's 15x15 assembly with:
- complete default materials (UO2 fuel, stainless-steel guide-tube fill) so the
  default F/G assembly is exportable;
- a candidate ``burnable_poison_universe`` (with an incomplete ``borosilicate_glass``
  material) that is defined but NOT inserted into the default lattice.

The acceptance criteria lock in the reachability fix: the incomplete candidate
material must only warn, never downgrade the default model to a skeleton.
"""

import json
from pathlib import Path

from openmc_agent.graph import build_plan_graph
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    LatticeSpec,
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    UniverseSpec,
)
from openmc_agent.tools import ToolResult


# 21 guide-tube positions from case2.md (1-indexed row, col).
GUIDE_TUBES_1INDEXED = [
    (8, 8),
    (4, 8), (12, 8), (8, 4), (8, 12),
    (4, 4), (4, 12), (12, 4), (12, 12),
    (4, 6), (4, 10), (6, 4), (6, 12), (10, 4), (10, 12),
    (12, 6), (12, 10), (6, 6), (6, 10), (10, 6), (10, 10),
]


def build_case2_pattern() -> list[list[str]]:
    guide = {(r - 1, c - 1) for r, c in GUIDE_TUBES_1INDEXED}
    return [
        ["guide_tube_universe" if (r, c) in guide else "fuel_pin_universe" for c in range(15)]
        for r in range(15)
    ]


def build_case2_plan() -> SimulationPlan:
    pattern = build_case2_pattern()
    guide_count = sum(row.count("guide_tube_universe") for row in pattern)
    fuel_count = sum(row.count("fuel_pin_universe") for row in pattern)
    assert guide_count == 21 and fuel_count == 204, (guide_count, fuel_count)

    model = ComplexModelSpec(
        name="15x15 PWR assembly (case2)",
        kind="assembly",
        materials=[
            # Default active materials: complete enough to export XML. Densities
            # and compositions follow the engineering defaults in case2.md sec. 4.
            ComplexMaterialSpec(
                id="uo2_fuel",
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=3.1, percent_type="wo"),
                    NuclideSpec(name="U238", percent=96.9, percent_type="wo"),
                    NuclideSpec(name="O16", percent=2.0),
                ],
            ),
            ComplexMaterialSpec(
                id="guide_steel",
                name="stainless steel guide fill",
                density_unit="g/cm3",
                density_value=7.9,
                composition=[
                    NuclideSpec(name="Fe", percent=71.0, percent_type="wo"),
                    NuclideSpec(name="Cr", percent=18.0, percent_type="wo"),
                    NuclideSpec(name="Ni", percent=11.0, percent_type="wo"),
                ],
            ),
            # Candidate burnable-poison material: deliberately incomplete (partial
            # density flagged for confirmation, no composition). case2.md sec. 4.6
            # does not give borosilicate-glass composition, density, or boron
            # isotope abundance. This must NOT block the default model.
            ComplexMaterialSpec(
                id="borosilicate_glass",
                name="borosilicate glass (candidate)",
                density_unit="g/cm3",
                requires_human_confirmation=[
                    "density value",
                    "composition",
                    "boron isotope abundance",
                ],
            ),
        ],
        cells=[
            CellSpec(id="fuel_cell", name="fuel pin", fill_type="material", fill_id="uo2_fuel"),
            CellSpec(
                id="guide_cell",
                name="guide tube",
                fill_type="material",
                fill_id="guide_steel",
            ),
            CellSpec(
                id="bp_glass_cell",
                name="bp glass",
                fill_type="material",
                fill_id="borosilicate_glass",
            ),
        ],
        universes=[
            UniverseSpec(id="fuel_pin_universe", name="fuel pin", cell_ids=["fuel_cell"]),
            UniverseSpec(
                id="guide_tube_universe",
                name="guide tube",
                cell_ids=["guide_cell"],
            ),
            # Candidate universe: defined but NOT inserted into the default lattice.
            UniverseSpec(
                id="burnable_poison_universe",
                name="candidate burnable poison",
                cell_ids=["bp_glass_cell"],
            ),
        ],
        lattices=[
            LatticeSpec(
                id="assembly_lattice",
                name="15x15 rectangular lattice",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                shape=(15, 15),
                universe_pattern=pattern,
            )
        ],
        assemblies=[
            AssemblySpec(
                id="assembly",
                name="root assembly",
                lattice_id="assembly_lattice",
                pitch_cm=18.9,
                boundary="reflective",
            )
        ],
        settings=RunSettingsSpec(batches=10, inactive=2, particles=1000),
        requires_human_confirmation=[
            "cross_sections.xml path",
            "thermal scattering data",
            "whether burnable poison rods are actually inserted",
        ],
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(18.9, 18.9), filename="case2_xy.png")],
    )


def main() -> None:
    outdir = Path("data/runs/manual/case2-renderer-verify")
    outdir.mkdir(parents=True, exist_ok=True)
    records = outdir / "runs.jsonl"

    def fake_generate_plan(*, requirement, schema, model):
        return StructuredOutputResult(ok=True, value=build_case2_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda p: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda d: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda d, p: ToolResult(name="run_smoke_test", ok=True),
        enable_plots=True,
        enable_smoke_test=True,
    )

    state = graph.invoke(
        {
            "requirement": "case2 deterministic verification",
            "model": "verify:offline",
            "output_dir": str(outdir),
            "records_path": str(records),
        }
    )

    plan = state["simulation_plan"]
    cap = plan.capability_report
    print("=" * 70)
    print("CASE2 DETERMINISTIC VERIFICATION")
    print("=" * 70)
    print(f"SimulationPlan validation: is_valid={state['validation_report'].is_valid}")
    print(f"Capability assessment:")
    print(f"  renderability   = {cap.renderability}")
    print(f"  is_executable   = {cap.is_executable}")
    print(f"  supported_renderer = {cap.supported_renderer}")
    print(f"  blocking reasons:")
    for reason in cap.reasons:
        print(f"    - {reason}")
    print(f"  warnings:")
    for warning in cap.warnings:
        print(f"    - {warning}")
    print(f"Generated files in {outdir}:")
    for name in sorted(p.name for p in outdir.iterdir()):
        print(f"    - {name}")
    model_path = outdir / "model.py"
    if model_path.exists():
        text = model_path.read_text(encoding="utf-8")
        print(f"model.py contains 'NOT EXECUTABLE': {'NOT EXECUTABLE' in text}")
        print(f"model.py contains 'export_to_xml()': {'export_to_xml()' in text}")
        print(f"model.py mentions borosilicate_glass: {'borosilicate_glass' in text}")
    tool_names = [t["name"] for t in state.get("tool_results", [])]
    print(f"Tools executed: {tool_names}")
    print("=" * 70)

    # -- acceptance criteria (reachability fix) --------------------------
    assert state["validation_report"].is_valid, "plan must validate"
    # The default F/G assembly has complete active materials -> exportable/runnable.
    assert cap.renderability in {"exportable", "runnable"}, cap.renderability
    assert cap.is_executable is True
    # The candidate borosilicate_glass must never appear as a blocking reason.
    assert not any("borosilicate_glass" in r for r in cap.reasons), cap.reasons
    # Its gaps surface as warnings / human-confirmations instead.
    soft = "\n".join(cap.warnings + cap.required_human_confirmations)
    assert "borosilicate_glass" in soft, (cap.warnings, cap.required_human_confirmations)
    # The candidate universe is reported as not inserted.
    assert any("burnable_poison_universe" in w for w in cap.warnings), cap.warnings
    # model.py is a real executable export, not a skeleton.
    assert model_path.exists()
    text = model_path.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" not in text
    assert "export_to_xml()" in text
    # The default model must not emit the candidate material.
    assert "borosilicate_glass" not in text
    print("ALL CASE2 ACCEPTANCE CRITERIA PASSED")


if __name__ == "__main__":
    main()
