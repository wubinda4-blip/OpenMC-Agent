"""Structured trace support for OpenMC Agent workflows.

Trace recording is deliberately side-channel only: helpers in this module must
not raise into the main workflow, and default payloads store compact summaries
or previews rather than full prompts and evidence dumps.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from openmc_agent.retrieval_orchestrator import RetrievalContext
from openmc_agent.schemas import (
    AgentBaseModel,
    RenderCapabilityReport,
    SimulationPlan,
    ValidationIssue,
    ValidationReport,
)


TraceEventType = Literal[
    "plan_generated",
    "validation_completed",
    "capability_assessed",
    "auto_repair_attempted",
    "auto_repair_completed",
    "retrieval_started",
    "retrieval_completed",
    "reflect_plan_started",
    "reflect_plan_completed",
    "ask_expert_started",
    "ask_expert_completed",
    "render_started",
    "render_completed",
    "export_xml_completed",
    "smoke_test_completed",
    "workflow_completed",
    "workflow_failed",
]


class TraceEvent(AgentBaseModel):
    event_id: str
    event_type: TraceEventType
    timestamp: str
    round_index: int = 0
    summary: str = ""
    issue_codes: list[str] = Field(default_factory=list)
    route_hints: list[str] = Field(default_factory=list)
    renderability: str | None = None
    supported_renderer: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowTrace(AgentBaseModel):
    trace_id: str
    created_at: str
    workflow_name: str = "openmc_agent"
    user_request_preview: str | None = None
    events: list[TraceEvent] = Field(default_factory=list)
    final_status: Literal["unknown", "valid", "invalid", "skeleton", "failed"] = "unknown"
    final_renderability: str | None = None
    final_supported_renderer: str | None = None
    warnings: list[str] = Field(default_factory=list)


class TraceConfig(AgentBaseModel):
    enabled: bool = True
    capture_prompt_preview: bool = False
    capture_plan_preview: bool = True
    capture_evidence_preview: bool = True
    max_preview_chars: int = 1200
    max_events: int = 200


class TraceRecorder:
    """Small in-memory recorder with JSON/JSONL export helpers."""

    def __init__(
        self,
        config: TraceConfig | None = None,
        trace_id: str | None = None,
        trace: WorkflowTrace | dict[str, Any] | None = None,
    ) -> None:
        self.config = config or TraceConfig()
        if isinstance(trace, WorkflowTrace):
            self.trace = trace
        elif isinstance(trace, dict):
            try:
                self.trace = WorkflowTrace.model_validate(trace)
            except Exception:
                self.trace = _new_trace(trace_id)
        else:
            self.trace = _new_trace(trace_id)

    def add_event(
        self,
        event_type: TraceEventType,
        *,
        round_index: int = 0,
        summary: str = "",
        issue_codes: list[str] | None = None,
        route_hints: list[str] | None = None,
        renderability: str | None = None,
        supported_renderer: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceEvent | None:
        if not self.config.enabled:
            return None
        if len(self.trace.events) >= self.config.max_events:
            if not any("max_events" in warning for warning in self.trace.warnings):
                self.trace.warnings.append(
                    f"trace max_events={self.config.max_events} reached; later events dropped"
                )
            return None
        try:
            event = TraceEvent(
                event_id=f"evt_{uuid4().hex[:12]}",
                event_type=event_type,
                timestamp=_utc_now(),
                round_index=round_index,
                summary=_truncate(summary, self.config.max_preview_chars),
                issue_codes=list(issue_codes or []),
                route_hints=list(route_hints or []),
                renderability=renderability,
                supported_renderer=supported_renderer,
                metadata=_json_safe(metadata or {}, self.config.max_preview_chars),
            )
            self.trace.events.append(event)
            return event
        except Exception as exc:  # pragma: no cover - defensive side channel
            try:
                self.trace.warnings.append(f"failed to add trace event {event_type}: {exc}")
            except Exception:
                pass
            return None

    def export_json(self) -> dict[str, Any]:
        return self.trace.model_dump(mode="json")

    def export_jsonl(self) -> str:
        lines = [
            json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            for event in self.trace.events
        ]
        return "\n".join(lines) + ("\n" if lines else "")

    def summarize(self) -> str:
        counts: dict[str, int] = {}
        for event in self.trace.events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        parts = [
            f"trace_id={self.trace.trace_id}",
            f"events={len(self.trace.events)}",
            f"final_status={self.trace.final_status}",
        ]
        parts.extend(f"{name}={count}" for name, count in sorted(counts.items()))
        if self.trace.warnings:
            parts.append(f"warnings={len(self.trace.warnings)}")
        return ", ".join(parts)


def summarize_issues(issues: list[ValidationIssue] | None) -> dict[str, Any]:
    issue_list = list(issues or [])
    return {
        "issue_count": len(issue_list),
        "issue_codes": [issue.code for issue in issue_list],
        "route_hints": [issue.route_hint for issue in issue_list if issue.route_hint],
        "requires_retrieval_count": sum(issue.requires_retrieval for issue in issue_list),
        "requires_human_confirmation_count": sum(
            issue.requires_human_confirmation for issue in issue_list
        ),
    }


def summarize_retrieval_context(context: RetrievalContext | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(context, dict):
        try:
            context = RetrievalContext.model_validate(context)
        except Exception:
            context = None
    if context is None:
        return {
            "issue_count": 0,
            "grep_request_count": 0,
            "grep_result_count": 0,
            "grep_evidence_count": 0,
            "graph_node_count": 0,
            "graph_edge_count": 0,
            "rag_chunk_count": 0,
            "rag_evidence_count": 0,
            "merged_evidence_count": 0,
            "warnings": [],
            "skipped_steps": [],
        }
    graph_context = context.graph_context
    return {
        "issue_count": len(context.issues),
        "grep_request_count": len(context.grep_requests),
        "grep_result_count": len(context.grep_results),
        "grep_evidence_count": len(context.grep_evidence),
        "graph_node_count": len(graph_context.nodes) if graph_context else 0,
        "graph_edge_count": len(graph_context.edges) if graph_context else 0,
        "rag_chunk_count": len(context.rag_result.chunks) if context.rag_result else 0,
        "rag_evidence_count": len(context.rag_evidence),
        "merged_evidence_count": len(context.merged_evidence),
        "warnings": list(context.warnings),
        "skipped_steps": list(context.skipped_steps),
    }


def summarize_capability_report(
    report: RenderCapabilityReport | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(report, dict):
        try:
            report = RenderCapabilityReport.model_validate(report)
        except Exception:
            report = None
    if report is None:
        return {
            "renderability": None,
            "is_executable": None,
            "supported_renderer": None,
            "unsupported_subsystems": [],
            "required_human_confirmations": [],
        }
    return {
        "renderability": report.renderability,
        "is_executable": report.is_executable,
        "supported_renderer": report.supported_renderer,
        "unsupported_subsystems": list(report.unsupported_subsystems),
        "required_human_confirmations": list(report.required_human_confirmations),
    }


def summarize_validation_report(
    report: ValidationReport | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(report, dict):
        try:
            report = ValidationReport.model_validate(report)
        except Exception:
            report = None
    if report is None:
        return {
            "is_valid": False,
            "error_count": 0,
            "warning_count": 0,
            "issue_count": 0,
            "issue_codes": [],
            "route_hints": [],
            "requires_retrieval_count": 0,
            "requires_human_confirmation_count": 0,
        }
    issue_summary = summarize_issues(report.issues)
    return {
        "is_valid": report.is_valid,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        **issue_summary,
    }


def preview_plan(plan: SimulationPlan | dict[str, Any] | None, max_chars: int = 1200) -> str:
    if plan is None:
        return ""
    try:
        if isinstance(plan, SimulationPlan):
            payload = plan.model_dump(mode="json")
        elif isinstance(plan, dict):
            payload = plan
        else:
            payload = {"repr": repr(plan)}
        return _truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), max_chars)
    except Exception:
        return _truncate(repr(plan), max_chars)


def save_trace_json(trace: WorkflowTrace, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(trace.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_trace_jsonl(trace: WorkflowTrace, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        for event in trace.events
    ]
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def trace_from_raw(raw: Any) -> WorkflowTrace:
    if isinstance(raw, WorkflowTrace):
        return raw
    if isinstance(raw, dict):
        try:
            return WorkflowTrace.model_validate(raw)
        except Exception:
            pass
    return _new_trace()


def _new_trace(trace_id: str | None = None) -> WorkflowTrace:
    return WorkflowTrace(trace_id=trace_id or f"trace_{uuid4().hex[:16]}", created_at=_utc_now())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def _json_safe(value: Any, max_preview_chars: int) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=_json_default)
        decoded = json.loads(encoded)
    except Exception:
        return _truncate(repr(value), max_preview_chars)
    return _truncate_large_strings(decoded, max_preview_chars)


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump_json"):
        return json.loads(value.model_dump_json())
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {
            key: val
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
    return repr(value)


def _truncate_large_strings(value: Any, max_preview_chars: int) -> Any:
    if isinstance(value, str):
        return _truncate(value, max_preview_chars)
    if isinstance(value, list):
        return [_truncate_large_strings(item, max_preview_chars) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _truncate_large_strings(val, max_preview_chars)
            for key, val in value.items()
        }
    return value
