import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openmc_agent.schemas import MaterialSpec, SimulationSpec, ValidationReport


DEFAULT_MATERIAL_RECORDS_PATH = Path("data/examples/material_specs.jsonl")
DEFAULT_SIMULATION_RECORDS_PATH = Path("data/runs/simulation_runs.jsonl")


def append_material_record(
    *,
    requirement: str,
    model: str,
    material_spec: MaterialSpec,
    validation_report: ValidationReport,
    path: str | Path = DEFAULT_MATERIAL_RECORDS_PATH,
    timestamp: str | None = None,
) -> dict[str, Any]:
    record = {
        "requirement": requirement,
        "model": model,
        "material_spec": material_spec.model_dump(mode="json"),
        "validation_report": validation_report.model_dump(mode="json"),
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }

    records_path = Path(path)
    records_path.parent.mkdir(parents=True, exist_ok=True)
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    records_path = Path(path)
    if not records_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with records_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                records.append(json.loads(text))
    return records


def append_simulation_record(
    *,
    requirement: str,
    model: str,
    simulation_spec: SimulationSpec | None,
    validation_report: ValidationReport,
    path: str | Path = DEFAULT_SIMULATION_RECORDS_PATH,
    simulation_plan: dict[str, Any] | None = None,
    model_path: str | None = None,
    error: str = "",
    retry_count: int = 0,
    retry_history: list[dict[str, Any]] | None = None,
    pending_expert_questions: list[str] | None = None,
    human_loop_events: list[dict[str, Any]] | None = None,
    investigation_trace: list[dict[str, Any]] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    record = {
        "requirement": requirement,
        "model": model,
        "simulation_spec": (
            simulation_spec.model_dump(mode="json") if simulation_spec is not None else None
        ),
        "simulation_plan": simulation_plan,
        "validation_report": validation_report.model_dump(mode="json"),
        "model_path": model_path,
        "error": error,
        "retry_count": retry_count,
        "retry_history": retry_history or [],
        "pending_expert_questions": pending_expert_questions or [],
        "human_loop_events": human_loop_events or [],
        "investigation_trace": investigation_trace or [],
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }

    records_path = Path(path)
    records_path.parent.mkdir(parents=True, exist_ok=True)
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record
