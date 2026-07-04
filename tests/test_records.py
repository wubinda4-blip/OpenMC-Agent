from pathlib import Path

from openmc_agent.records import append_material_record, append_simulation_record, load_jsonl_records
from openmc_agent.schemas import MaterialSpec, NuclideSpec, ValidationReport


def make_material(name: str) -> MaterialSpec:
    return MaterialSpec(
        name=name,
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )


def test_append_material_record_writes_complete_jsonl_record(tmp_path: Path) -> None:
    records_path = tmp_path / "material_specs.jsonl"

    append_material_record(
        requirement="创建 UO2 燃料",
        model="test:model",
        material_spec=make_material("UO2 fuel"),
        validation_report=ValidationReport(is_valid=True),
        path=records_path,
        timestamp="2026-07-02T00:00:00+00:00",
    )

    records = load_jsonl_records(records_path)

    assert len(records) == 1
    assert records[0]["requirement"] == "创建 UO2 燃料"
    assert records[0]["model"] == "test:model"
    assert records[0]["material_spec"]["name"] == "UO2 fuel"
    assert records[0]["validation_report"]["is_valid"] is True
    assert records[0]["timestamp"] == "2026-07-02T00:00:00+00:00"


def test_append_material_record_accumulates_multiple_records(tmp_path: Path) -> None:
    records_path = tmp_path / "nested" / "material_specs.jsonl"

    for index in range(3):
        append_material_record(
            requirement=f"创建材料 {index}",
            model="test:model",
            material_spec=make_material(f"material-{index}"),
            validation_report=ValidationReport(is_valid=True),
            path=records_path,
            timestamp=f"2026-07-02T00:00:0{index}+00:00",
        )

    records = load_jsonl_records(records_path)

    assert [record["material_spec"]["name"] for record in records] == [
        "material-0",
        "material-1",
        "material-2",
    ]


def test_append_simulation_record_writes_plan_artifacts(tmp_path: Path) -> None:
    records_path = tmp_path / "simulation_runs.jsonl"

    append_simulation_record(
        requirement="建立一个 UO2 pin-cell",
        model="test:model",
        simulation_spec=None,
        validation_report=ValidationReport(is_valid=False, errors=["missing plan"]),
        path=records_path,
        plan_artifacts=[
            str(tmp_path / "simulation_plan.json"),
            str(tmp_path / "plan_artifacts" / "000_generate_plan" / "meta.json"),
        ],
    )

    records = load_jsonl_records(records_path)

    assert records[0]["plan_artifacts"] == [
        str(tmp_path / "simulation_plan.json"),
        str(tmp_path / "plan_artifacts" / "000_generate_plan" / "meta.json"),
    ]
