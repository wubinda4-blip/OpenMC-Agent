"""Deterministic case2 verification: drive the plan graph with a case2-shaped plan.

This mirrors exactly what scripts/run_inspect.sh does AFTER the LLM produces a
SimulationPlan, but replaces the LLM call with a fixed plan so it runs offline.
The plan encodes case2's 15x15 assembly with guide-tube positions and material
gaps (no density / cross-section data), matching the case2.md scenario.
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
        ["guide" if (r, c) in guide else "fuel" for c in range(15)]
        for r in range(15)
    ]


def build_case2_plan() -> SimulationPlan:
    pattern = build_case2_pattern()
    guide_count = sum(row.count("guide") for row in pattern)
    fuel_count = sum(row.count("fuel") for row in pattern)
    assert guide_count == 21 and fuel_count == 204, (guide_count, fuel_count)

    model = ComplexModelSpec(
        name="15x15 PWR assembly (case2)",
        kind="assembly",
        materials=[
            # Material details are intentionally gaps per case2.md: the source
            # document does not give density / composition / cross_sections.xml.
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel (composition pending)",
                chemical_formula="UO2",
                requires_human_confirmation=["density", "enrichment", "temperature"],
            ),
            ComplexMaterialSpec(
                id="guide",
                name="guide tube / coolant (composition pending)",
                requires_human_confirmation=["density", "composition"],
            ),
        ],
        cells=[
            CellSpec(id="fuel_cell", name="fuel pin", fill_type="material", fill_id="fuel"),
            CellSpec(id="guide_cell", name="guide tube", fill_type="material", fill_id="guide"),
        ],
        universes=[
            UniverseSpec(id="fuel", name="fuel pin universe", cell_ids=["fuel_cell"]),
            UniverseSpec(id="guide", name="guide tube universe", cell_ids=["guide_cell"]),
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
                boundary=None,  # case2: boundary must be confirmed by a core model
            )
        ],
        settings=RunSettingsSpec(batches=10, inactive=2, particles=1000),
        requires_human_confirmation=[
            "burnable poison rod insertion pattern",
            "cross_sections.xml path",
            "thermal scattering data",
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
    print(f"  reasons (first 5):")
    for reason in cap.reasons[:5]:
        print(f"    - {reason}")
    print(f"  required_human_confirmations (first 6):")
    for item in cap.required_human_confirmations[:6]:
        print(f"    - {item}")
    print(f"Generated files in {outdir}:")
    for name in sorted(p.name for p in outdir.iterdir()):
        print(f"    - {name}")
    model_path = outdir / "model.py"
    if model_path.exists():
        text = model_path.read_text(encoding="utf-8")
        print(f"model.py contains 'NOT EXECUTABLE': {'NOT EXECUTABLE' in text}")
        print(f"model.py contains 'TODO': {'TODO' in text}")
        export_lines = [ln for ln in text.splitlines() if "export_to_xml()" in ln]
        print(f"export_to_xml calls all commented: {all(ln.lstrip().startswith('#') for ln in export_lines)}")
    tool_names = [t["name"] for t in state.get("tool_results", [])]
    print(f"Tools executed: {tool_names} (expected [] for skeleton)")
    print(f"openmc.run attempted: {'run_smoke_test' in tool_names}")
    print("=" * 70)
    # Acceptance assertions
    assert state["validation_report"].is_valid, "plan must validate"
    assert cap.renderability == "skeleton", cap.renderability
    assert model_path.exists(), "skeleton model.py must be generated"
    assert (outdir / "capability_report.json").exists()
    assert (outdir / "TODO.md").exists()
    assert "run_smoke_test" not in tool_names, "no OpenMC run for non-executable model"
    assert any("missing density" in r for r in cap.reasons)
    print("ALL CASE2 ACCEPTANCE CRITERIA PASSED")


if __name__ == "__main__":
    main()
