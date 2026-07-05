import json
from pathlib import Path

from openmc_agent.inspect import InspectResult
from openmc_agent.inspect import (
    compose_operating_state_requirement,
    inspect_markdown_file,
    inspect_requirement,
    main,
)
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    ExecutionCheckSpec,
    GeometrySpec,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
    UniverseSpec,
)
from openmc_agent.tools import ToolResult


def make_spec() -> SimulationSpec:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )
    moderator = MaterialSpec(
        name="Water moderator",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )
    return SimulationSpec(
        name="Observable UO2 pin-cell",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


def make_plan() -> SimulationPlan:
    return SimulationPlan(
        model_spec=make_spec(),
        plot_specs=[
            PlotSpec(
                basis="xy",
                origin=(0.0, 0.0, 0.0),
                width_cm=(1.26, 1.26),
                pixels=(300, 300),
                filename="pin_cell_xy.png",
            )
        ],
        execution_check=ExecutionCheckSpec(
            settings=RunSettingsSpec(batches=5, inactive=1, particles=100)
        ),
    )


def test_inspect_requirement_shows_structured_output_and_exports_xml(
    tmp_path: Path,
) -> None:
    def fake_generate_spec(*, requirement: str, schema, model: str):
        assert requirement == "建立一个 UO2 pin-cell 临界计算"
        return StructuredOutputResult(ok=True, value=make_spec())

    result = inspect_requirement(
        "建立一个 UO2 pin-cell 临界计算",
        output_dir=tmp_path,
        generate_spec=fake_generate_spec,
    )

    assert result.ok is True
    assert result.model_path == tmp_path / "model.py"
    assert result.model_path.exists()
    assert result.xml_export_ok is True
    assert (tmp_path / "materials.xml").exists()
    assert (tmp_path / "geometry.xml").exists()
    assert (tmp_path / "settings.xml").exists()
    assert (tmp_path / "tallies.xml").exists()

    output = result.transcript
    assert "[1] 用户需求" in output
    assert "建立一个 UO2 pin-cell 临界计算" in output
    assert "[2] LLM 结构化输出" in output
    assert '"name": "Observable UO2 pin-cell"' in output
    assert "[3] 验证结果" in output
    assert "is_valid=True" in output
    assert "[4] 修复过程" in output
    assert "retry_count=0" in output
    assert "[5] 最终执行结果" in output
    assert "xml_export=success" in output


def test_inspect_requirement_reports_validation_failure(tmp_path: Path) -> None:
    invalid_spec = make_spec()
    invalid_spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )

    def fake_generate_spec(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=invalid_spec)

    def fake_repair_spec(
        *,
        requirement: str,
        schema,
        model: str,
        previous_spec: SimulationSpec,
        validation_errors: list[str],
    ):
        return StructuredOutputResult(ok=True, value=invalid_spec)

    result = inspect_requirement(
        "异常：燃料半径 10cm 的 UO2 pin-cell",
        output_dir=tmp_path,
        generate_spec=fake_generate_spec,
        repair_spec=fake_repair_spec,
        max_retries=1,
    )

    assert result.ok is False
    assert result.model_path is None
    assert result.xml_export_ok is False
    assert "fuel_radius_cm" in result.transcript
    assert "xml_export=skipped" in result.transcript


def test_inspect_markdown_file_uses_markdown_content_as_requirement(
    tmp_path: Path,
) -> None:
    md_path = tmp_path / "requirement.md"
    md_path.write_text(
        "# 建模需求\n\n建立一个 UO2 pin-cell 临界计算\n",
        encoding="utf-8",
    )

    def fake_generate_spec(*, requirement: str, schema, model: str):
        assert "# 建模需求" in requirement
        assert "建立一个 UO2 pin-cell 临界计算" in requirement
        return StructuredOutputResult(ok=True, value=make_spec())

    result = inspect_markdown_file(
        md_path,
        output_dir=tmp_path / "output",
        generate_spec=fake_generate_spec,
    )

    assert result.ok is True
    assert "# 建模需求" in result.transcript
    assert "建立一个 UO2 pin-cell 临界计算" in result.transcript


def test_inspect_cli_accepts_markdown_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    md_path = tmp_path / "requirement.md"
    md_path.write_text("建立一个 UO2 pin-cell 临界计算\n", encoding="utf-8")

    def fake_inspect_markdown_file(path, **kwargs):
        assert Path(path) == md_path
        assert kwargs["output_dir"] == str(tmp_path / "output")
        return type(
            "Result",
            (),
            {
                "ok": True,
                "transcript": "[1] 用户需求\n建立一个 UO2 pin-cell 临界计算",
            },
        )()

    monkeypatch.setattr(
        "openmc_agent.inspect.inspect_markdown_file",
        fake_inspect_markdown_file,
    )

    exit_code = main(
        [
            "--md-file",
            str(md_path),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert exit_code == 0
    assert "建立一个 UO2 pin-cell 临界计算" in capsys.readouterr().out


def test_compose_operating_state_requirement_prepends_directive() -> None:
    composed = compose_operating_state_requirement(
        "# 算例\n\n建立 UO2 pin-cell\n", "1A"
    )
    assert composed.startswith("=== Operating-state selection ===")
    assert '"1A"' in composed
    assert "Original problem description" in composed
    # The original markdown is kept verbatim below the directive.
    assert "# 算例" in composed
    assert "建立 UO2 pin-cell" in composed
    # The directive precedes the original description.
    assert composed.index("Operating-state selection") < composed.index("# 算例")


def test_inspect_markdown_file_injects_operating_state(tmp_path: Path) -> None:
    md_path = tmp_path / "requirement.md"
    md_path.write_text(
        "# 算例\n\n本题分为多个计算工况：1A / 1B。\n",
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    def fake_generate_spec(*, requirement: str, schema, model: str):
        captured["requirement"] = requirement
        return StructuredOutputResult(ok=True, value=make_spec())

    result = inspect_markdown_file(
        md_path,
        operating_state="1A",
        output_dir=tmp_path / "output",
        generate_spec=fake_generate_spec,
    )

    assert result.ok is True
    assert captured["requirement"].startswith("=== Operating-state selection ===")
    assert '"1A"' in captured["requirement"]
    assert "# 算例" in captured["requirement"]


def test_inspect_cli_passes_state_to_markdown_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    md_path = tmp_path / "requirement.md"
    md_path.write_text("建立一个 UO2 pin-cell 临界计算\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_inspect_markdown_file(path, **kwargs):
        captured["path"] = path
        captured["operating_state"] = kwargs.get("operating_state")
        return type(
            "Result",
            (),
            {
                "ok": True,
                "transcript": "[1] 用户需求\n建立一个 UO2 pin-cell 临界计算",
            },
        )()

    monkeypatch.setattr(
        "openmc_agent.inspect.inspect_markdown_file",
        fake_inspect_markdown_file,
    )

    exit_code = main(
        [
            "--md-file",
            str(md_path),
            "--state",
            "1A",
        ]
    )

    assert exit_code == 0
    assert Path(captured["path"]) == md_path
    assert captured["operating_state"] == "1A"


def test_inspect_requirement_plan_mode_shows_tool_results(tmp_path: Path) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        assert schema is SimulationPlan
        return StructuredOutputResult(ok=True, value=make_plan(), raw_response='{"ok": true}')

    result = inspect_requirement(
        "建立一个 UO2 pin-cell，并绘制几何截面图",
        output_dir=tmp_path,
        use_plan=True,
        enable_plots=True,
        enable_smoke_test=True,
        expert_feedback=["xy 图不够，要看 xz 截面"],
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(
            name="export_xml",
            ok=True,
            returncode=0,
            artifacts=[str(tmp_path / "materials.xml")],
        ),
        plot_tool=lambda run_dir: ToolResult(
            name="run_geometry_plots",
            ok=True,
            returncode=0,
            artifacts=[str(tmp_path / "pin_cell_xy.png")],
        ),
        smoke_test_tool=lambda run_dir, plan: ToolResult(
            name="run_smoke_test",
            ok=True,
            returncode=0,
            stdout="k-effective 1.0",
        ),
    )

    assert result.ok is True
    assert result.model_path == tmp_path / "model.py"
    assert result.transcript_data is not None
    assert result.transcript_data["simulation_plan"]["plot_specs"][0]["basis"] == "xy"
    assert result.transcript_data["expert_feedback"] == ["xy 图不够，要看 xz 截面"]
    assert [item["name"] for item in result.transcript_data["tool_results"]] == [
        "export_xml",
        "run_geometry_plots",
        "run_smoke_test",
    ]
    assert "[9] 工具执行结果" in result.transcript
    assert "run_smoke_test" in result.transcript
    assert "[9c] Plan artifacts" in result.transcript
    assert "simulation_plan.json" in result.transcript
    assert "raw_response.txt" in result.transcript
    assert '{"ok": true}' not in result.transcript
    assert result.transcript_data["plan_artifacts"]
    assert (tmp_path / "transcript.json").exists()


def test_inspect_requirement_plan_mode_prints_node_progress(
    tmp_path: Path,
    capsys,
) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_plan())

    result = inspect_requirement(
        "建立一个 UO2 pin-cell，并观察节点进度",
        output_dir=tmp_path,
        use_plan=True,
        enable_plots=True,
        enable_smoke_test=True,
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        verbose=True,
    )

    assert result.ok is True
    stderr = capsys.readouterr().err
    assert "[node:receive_requirement]" in stderr
    assert "[node:generate_plan]" in stderr
    assert "[node:validate_plan]" in stderr
    assert "[node:render_plan_script]" in stderr
    assert "[node:execute_tools] running export_xml" in stderr
    assert "[node:execute_tools] running run_geometry_plots" in stderr
    assert "[node:execute_tools] running run_smoke_test" in stderr
    assert "[node:save_record]" in stderr


def test_inspect_requirement_plan_mode_marks_skeleton_not_ok(tmp_path: Path) -> None:
    def fake_generate_plan(*, requirement: str, schema, model: str):
        complex_model = ComplexModelSpec(
            name="incomplete assembly",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    requires_human_confirmation=["density"],
                )
            ],
            cells=[
                CellSpec(
                    id="fuel_cell",
                    name="fuel",
                    fill_type="material",
                    fill_id="fuel",
                )
            ],
            universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="assembly lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin"]],
                )
            ],
            assemblies=[
                AssemblySpec(
                    id="assembly",
                    name="assembly",
                    lattice_id="assembly_lattice",
                )
            ],
        )
        return StructuredOutputResult(
            ok=True,
            value=SimulationPlan(
                schema_version="simulation_plan.v2",
                model_spec=None,
                complex_model=complex_model,
                capability_report=RenderCapabilityReport(
                    is_executable=False,
                    supported_renderer="none",
                ),
                plot_specs=[
                    PlotSpec(
                        basis="xy",
                        width_cm=(1.26, 1.26),
                        filename="assembly_xy.png",
                    )
                ],
            ),
        )

    result = inspect_requirement(
        "建立一个材料缺密度的组件模型",
        output_dir=tmp_path,
        use_plan=True,
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        plot_tool=lambda run_dir: ToolResult(name="run_geometry_plots", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
    )

    assert result.ok is False
    assert result.model_path == tmp_path / "model.py"
    assert result.transcript_data is not None
    assert result.transcript_data["ok"] is False
    assert result.transcript_data["render_outcome"]["status"] == "skeleton"
    assert "Status: NOT EXECUTABLE" in result.transcript


def test_inspect_cli_json_enables_interactive_expert_feedback(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    def fake_inspect_requirement(requirement, **kwargs):
        assert kwargs["use_plan"] is True
        assert kwargs["enable_plots"] is True
        assert kwargs["enable_smoke_test"] is True
        assert kwargs["expert_feedback"] == []
        assert kwargs["interactive_feedback"] is True
        assert kwargs["max_expert_rounds"] == 2
        return InspectResult(
            ok=True,
            transcript="human transcript",
            transcript_data={
                "ok": True,
                "requirement": requirement,
                "expert_feedback": ["增加 xz 截面"],
            },
        )

    monkeypatch.setattr("openmc_agent.inspect.inspect_requirement", fake_inspect_requirement)

    exit_code = main(
        [
            "建立一个 UO2 pin-cell",
            "--plot",
            "--smoke-test",
            "--interactive-feedback",
            "--json",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["expert_feedback"] == ["增加 xz 截面"]


def test_inspect_cli_tty_defaults_to_interactive_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeTty:
        def isatty(self):
            return True

        def readline(self):
            return ""

    def fake_inspect_requirement(requirement, **kwargs):
        assert kwargs["use_plan"] is True
        assert kwargs["interactive_feedback"] is True
        assert kwargs["max_expert_rounds"] == 2
        return InspectResult(ok=True, transcript="ok", transcript_data={"ok": True})

    monkeypatch.setattr("sys.stdin", FakeTty())
    monkeypatch.setattr("openmc_agent.inspect.inspect_requirement", fake_inspect_requirement)

    exit_code = main(["建立一个 UO2 pin-cell", "--output-dir", str(tmp_path)])

    assert exit_code == 0


def test_inspect_cli_can_disable_tty_interactive_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeTty:
        def isatty(self):
            return True

    def fake_inspect_requirement(requirement, **kwargs):
        assert kwargs["interactive_feedback"] is False
        return InspectResult(ok=True, transcript="ok", transcript_data={"ok": True})

    monkeypatch.setattr("sys.stdin", FakeTty())
    monkeypatch.setattr("openmc_agent.inspect.inspect_requirement", fake_inspect_requirement)

    exit_code = main(
        [
            "建立一个 UO2 pin-cell",
            "--no-interactive-feedback",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
