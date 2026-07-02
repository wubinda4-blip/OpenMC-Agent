import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from openmc_agent.graph import (
    ExportXmlToolFn,
    GeneratePlanFn,
    GenerateSpecFn,
    PlotToolFn,
    RepairPlanFn,
    RepairSpecFn,
    SmokeTestToolFn,
    build_graph,
    build_plan_graph,
)
from openmc_agent.llm import (
    DEFAULT_MODEL,
    generate_structured_output,
    repair_structured_output,
    set_llm_progress,
)
from openmc_agent.tools import export_xml, run_geometry_plots, run_smoke_test


@dataclass(frozen=True)
class InspectResult:
    ok: bool
    transcript: str
    model_path: Path | None = None
    xml_export_ok: bool = False
    xml_export_error: str = ""
    transcript_data: dict | None = None


def inspect_requirement(
    requirement: str,
    *,
    model: str = DEFAULT_MODEL,
    output_dir: str | Path = "data/runs/inspect",
    max_retries: int = 3,
    generate_spec: GenerateSpecFn = generate_structured_output,
    repair_spec: RepairSpecFn = repair_structured_output,
    use_plan: bool = False,
    enable_plots: bool = False,
    enable_smoke_test: bool = False,
    expert_feedback: list[str] | None = None,
    generate_plan: GeneratePlanFn = generate_structured_output,
    repair_plan: RepairPlanFn = repair_structured_output,
    export_xml_tool: ExportXmlToolFn = export_xml,
    plot_tool: PlotToolFn = run_geometry_plots,
    smoke_test_tool: SmokeTestToolFn = run_smoke_test,
    verbose: bool = False,
) -> InspectResult:
    set_llm_progress(verbose)
    if use_plan:
        return _inspect_plan_requirement(
            requirement,
            model=model,
            output_dir=output_dir,
            max_retries=max_retries,
            enable_plots=enable_plots,
            enable_smoke_test=enable_smoke_test,
            expert_feedback=expert_feedback or [],
            generate_plan=generate_plan,
            repair_plan=repair_plan,
            export_xml_tool=export_xml_tool,
            plot_tool=plot_tool,
            smoke_test_tool=smoke_test_tool,
            verbose=verbose,
        )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    records_path = output_path / "inspect_runs.jsonl"
    if verbose:
        print("[agent] Building legacy SimulationSpec workflow...", file=sys.stderr)

    graph = build_graph(
        generate_spec=generate_spec,
        repair_spec=repair_spec,
        max_retries=max_retries,
    )
    if verbose:
        print("[agent] Invoking LLM and validation workflow...", file=sys.stderr)
    state = graph.invoke(
        {
            "requirement": requirement,
            "model": model,
            "output_dir": str(output_path),
            "records_path": str(records_path),
            "verbose": verbose,
        }
    )

    model_path = Path(state["model_path"]) if state.get("model_path") else None
    xml_export_ok = False
    xml_export_error = ""
    if model_path is not None:
        if verbose:
            print("[agent] Exporting OpenMC XML files...", file=sys.stderr)
        xml_export_ok, xml_export_error = _export_xml(model_path)

    transcript = _format_transcript(
        requirement=requirement,
        state=state,
        model_path=model_path,
        xml_export_ok=xml_export_ok,
        xml_export_error=xml_export_error,
    )
    report = state.get("validation_report")
    ok = bool(report and report.is_valid and model_path is not None and xml_export_ok)
    return InspectResult(
        ok=ok,
        transcript=transcript,
        model_path=model_path,
        xml_export_ok=xml_export_ok,
        xml_export_error=xml_export_error,
        transcript_data=_legacy_transcript_data(
            requirement=requirement,
            state=state,
            model_path=model_path,
            xml_export_ok=xml_export_ok,
            xml_export_error=xml_export_error,
        ),
    )


def inspect_markdown_file(
    path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    output_dir: str | Path = "data/runs/inspect",
    max_retries: int = 3,
    generate_spec: GenerateSpecFn = generate_structured_output,
    repair_spec: RepairSpecFn = repair_structured_output,
    **kwargs,
) -> InspectResult:
    requirement = read_markdown_requirement(path)
    return inspect_requirement(
        requirement,
        model=model,
        output_dir=output_dir,
        max_retries=max_retries,
        generate_spec=generate_spec,
        repair_spec=repair_spec,
        **kwargs,
    )


def read_markdown_requirement(path: str | Path) -> str:
    md_path = Path(path)
    if md_path.suffix.lower() != ".md":
        raise ValueError(f"Expected a .md file, got {md_path}")
    text = md_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Markdown requirement file is empty: {md_path}")
    return text


def _export_xml(model_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, model_path.name],
        cwd=model_path.parent,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout).strip()


def _format_transcript(
    *,
    requirement: str,
    state: dict,
    model_path: Path | None,
    xml_export_ok: bool,
    xml_export_error: str,
) -> str:
    spec = state.get("simulation_spec")
    report = state.get("validation_report")
    retry_history = state.get("retry_history", [])
    model_path_text = str(model_path) if model_path is not None else "None"
    xml_status = "success" if xml_export_ok else "skipped"
    if model_path is not None and not xml_export_ok:
        xml_status = "failed"

    sections = [
        "[1] 用户需求",
        requirement,
        "",
        "[2] LLM 结构化输出",
        _json_dump(spec.model_dump(mode="json") if spec is not None else None),
        "",
        "[3] 验证结果",
        f"is_valid={report.is_valid if report is not None else False}",
        f"errors={report.errors if report is not None else [state.get('error', 'unknown error')]}",
        "",
        "[4] 修复过程",
        f"retry_count={state.get('retry_count', 0)}",
        _json_dump(retry_history),
        "",
        "[5] 最终执行结果",
        f"model.py={model_path_text}",
        f"xml_export={xml_status}",
    ]
    if xml_export_error:
        sections.append(f"xml_error={xml_export_error}")
    return "\n".join(sections)


def _inspect_plan_requirement(
    requirement: str,
    *,
    model: str,
    output_dir: str | Path,
    max_retries: int,
    enable_plots: bool,
    enable_smoke_test: bool,
    expert_feedback: list[str],
    generate_plan: GeneratePlanFn,
    repair_plan: RepairPlanFn,
    export_xml_tool: ExportXmlToolFn,
    plot_tool: PlotToolFn,
    smoke_test_tool: SmokeTestToolFn,
    verbose: bool,
) -> InspectResult:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    records_path = output_path / "inspect_runs.jsonl"
    if verbose:
        print("[agent] Building SimulationPlan workflow...", file=sys.stderr)
        print(
            "[agent] Steps: retrieve docs -> few-shot -> LLM plan -> validate -> "
            "capability -> render/tools or structured-only -> reflection.",
            file=sys.stderr,
        )

    graph = build_plan_graph(
        generate_plan=generate_plan,
        repair_plan=repair_plan,
        export_xml_tool=export_xml_tool,
        plot_tool=plot_tool,
        smoke_test_tool=smoke_test_tool,
        enable_plots=enable_plots,
        enable_smoke_test=enable_smoke_test,
        max_retries=max_retries,
    )
    if verbose:
        print("[agent] Invoking LLM. This can take 30-120 seconds on remote models...", file=sys.stderr)
    state = graph.invoke(
        {
            "requirement": requirement,
            "model": model,
            "output_dir": str(output_path),
            "records_path": str(records_path),
            "expert_feedback": expert_feedback,
            "verbose": verbose,
        }
    )
    if verbose:
        print("[agent] Workflow finished. Formatting transcript...", file=sys.stderr)

    model_path = Path(state["model_path"]) if state.get("model_path") else None
    transcript_data = _plan_transcript_data(
        requirement=requirement,
        state=state,
        model_path=model_path,
        expert_feedback=expert_feedback,
    )
    transcript = _format_plan_transcript(transcript_data)
    (output_path / "transcript.json").write_text(
        json.dumps(transcript_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = state.get("validation_report")
    export_result = _tool_result_by_name(state.get("tool_results", []), "export_xml")
    xml_export_ok = bool(export_result and export_result.get("ok"))
    ok = _plan_state_ok(report, state, model_path)
    return InspectResult(
        ok=ok,
        transcript=transcript,
        model_path=model_path,
        xml_export_ok=xml_export_ok,
        xml_export_error="" if xml_export_ok else (export_result or {}).get("error", ""),
        transcript_data=transcript_data,
    )


def _legacy_transcript_data(
    *,
    requirement: str,
    state: dict,
    model_path: Path | None,
    xml_export_ok: bool,
    xml_export_error: str,
) -> dict:
    spec = state.get("simulation_spec")
    report = state.get("validation_report")
    return {
        "ok": bool(report and report.is_valid and model_path is not None and xml_export_ok),
        "requirement": requirement,
        "simulation_spec": spec.model_dump(mode="json") if spec is not None else None,
        "validation_report": report.model_dump(mode="json") if report is not None else None,
        "retry_count": state.get("retry_count", 0),
        "retry_history": state.get("retry_history", []),
        "model_path": str(model_path) if model_path is not None else None,
        "xml_export_ok": xml_export_ok,
        "xml_export_error": xml_export_error,
    }


def _plan_transcript_data(
    *,
    requirement: str,
    state: dict,
    model_path: Path | None,
    expert_feedback: list[str],
) -> dict:
    plan = state.get("simulation_plan")
    report = state.get("validation_report")
    capability = plan.capability_report.model_dump(mode="json") if plan is not None else None
    tool_results = state.get("tool_results", [])
    return {
        "ok": _plan_state_ok(report, state, model_path),
        "requirement": requirement,
        "expert_feedback": expert_feedback,
        "openmc_api_docs": state.get("openmc_api_docs", []),
        "few_shot_examples": state.get("few_shot_examples", []),
        "simulation_plan": plan.model_dump(mode="json") if plan is not None else None,
        "capability_report": capability,
        "render_outcome": _render_outcome(
            plan, model_path, tool_results, state.get("output_dir")
        ),
        "validation_report": report.model_dump(mode="json") if report is not None else None,
        "retry_count": state.get("retry_count", 0),
        "retry_history": state.get("retry_history", []),
        "tool_results": tool_results,
        "raw_llm_outputs": state.get("raw_llm_outputs", []),
        "model_path": str(model_path) if model_path is not None else None,
        "error": state.get("error", ""),
    }


def _render_outcome(
    plan: object | None,
    model_path: Path | None,
    tool_results: list[dict],
    output_dir: str | None,
) -> dict:
    """Summarize what the renderer produced so the CLI cannot misread skeleton as runnable."""
    if plan is None:
        return {"status": "no_plan", "lines": []}
    capability = plan.capability_report  # type: ignore[attr-defined]
    renderability = capability.renderability
    lines: list[str] = []

    if renderability == "none":
        lines.append("No model.py generated: no renderer could handle this plan.")
        return {"status": "none", "renderability": renderability, "lines": lines}

    if renderability == "skeleton":
        lines.append("Generated model.py skeleton")
        lines.append("Status: NOT EXECUTABLE")
        lines.append("No OpenMC run attempted because model is not executable")
        sidecars = _sidecar_lines(output_dir)
        if sidecars:
            lines.append(f"See: {', '.join(sidecars)}")
        return {"status": "skeleton", "renderability": renderability, "lines": lines}

    # exportable or runnable
    lines.append("Generated model.py")
    exported = _tool_artifacts(tool_results, "export_xml")
    if exported:
        lines.append(f"Exported XML files: {', '.join(exported)}")
    else:
        lines.append("Exported XML files: (none)")
    if renderability == "runnable":
        smoke = _tool_result_by_name(tool_results, "run_smoke_test")
        if smoke is None:
            lines.append("Smoke test status: skipped (not enabled)")
        elif smoke.get("ok"):
            lines.append("Smoke test status: passed")
        else:
            lines.append(f"Smoke test status: failed ({smoke.get('error', '')})")
    else:
        lines.append("Smoke test status: skipped (renderability=exportable)")
    return {"status": renderability, "renderability": renderability, "lines": lines}


def _sidecar_lines(output_dir: str | None) -> list[str]:
    if not output_dir:
        return []
    base = Path(output_dir)
    candidates = ["capability_report.json", "TODO.md"]
    return [name for name in candidates if (base / name).exists()]


def _tool_artifacts(tool_results: list[dict], name: str) -> list[str]:
    result = _tool_result_by_name(tool_results, name)
    if not result:
        return []
    return [Path(artifact).name for artifact in result.get("artifacts", [])]


def _format_plan_transcript(data: dict) -> str:
    sections = [
        "[1] 用户需求",
        data["requirement"],
        "",
        "[2] LLM 结构化输出",
        _json_dump(data.get("simulation_plan")),
        "",
        "[3] OpenMC API 检索上下文",
        _json_dump(data.get("openmc_api_docs", [])),
        "",
        "[4] Few-shot 示例",
        _json_dump(data.get("few_shot_examples", [])),
        "",
        "[5] Capability Report",
        _json_dump(data.get("capability_report")),
        "",
        "[6] 验证结果",
        _json_dump(data.get("validation_report")),
        "",
        "[7] 修复过程",
        f"retry_count={data.get('retry_count', 0)}",
        _json_dump(data.get("retry_history", [])),
        "",
        "[8] 人类专家反馈",
        _json_dump(data.get("expert_feedback", [])),
        "",
        "[9] 工具执行结果",
        _json_dump(data.get("tool_results", [])),
        "",
        "[10] 渲染结果摘要",
        "\n".join(data.get("render_outcome", {}).get("lines", []) or ["(no render outcome)"]),
        "",
        "[11] 最终执行结果",
        f"model.py={data.get('model_path')}",
        f"ok={data.get('ok')}",
    ]
    if data.get("error"):
        sections.append(f"error={data['error']}")
    return "\n".join(sections)


def _tool_result_by_name(tool_results: list[dict], name: str) -> dict | None:
    for result in tool_results:
        if result.get("name") == name:
            return result
    return None


def _plan_state_ok(report: object, state: dict, model_path: Path | None) -> bool:
    if not report or not getattr(report, "is_valid", False):
        return False
    plan = state.get("simulation_plan")
    if plan is None:
        return False
    renderability = plan.capability_report.renderability
    if renderability == "none":
        # Structured IR was delivered for review even though nothing was rendered.
        return True
    if renderability == "skeleton":
        return False
    # exportable / runnable must produce a model.py.
    return model_path is not None


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect one natural-language OpenMC modeling requirement."
    )
    parser.add_argument(
        "requirement",
        nargs="?",
        help="Natural-language modeling requirement",
    )
    parser.add_argument(
        "--md-file",
        help="Read the natural-language modeling requirement from a Markdown file",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", default="data/runs/inspect")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--plan", action="store_true", help="Use SimulationPlan workflow")
    parser.add_argument("--plot", action="store_true", help="Run OpenMC geometry plots")
    parser.add_argument("--smoke-test", action="store_true", help="Run low-particle OpenMC smoke test")
    parser.add_argument("--interactive-feedback", action="store_true")
    parser.add_argument("--expert-feedback", action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--show-raw-llm", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    expert_feedback = list(args.expert_feedback)
    if args.interactive_feedback:
        feedback = _read_interactive_feedback()
        if feedback:
            expert_feedback.append(feedback)

    use_plan = args.plan or args.plot or args.smoke_test or bool(expert_feedback)
    if args.md_file and args.requirement:
        parser.error("Use either a positional requirement or --md-file, not both")
    if args.md_file:
        result = inspect_markdown_file(
            args.md_file,
            model=args.model,
            output_dir=args.output_dir,
            max_retries=args.max_retries,
            use_plan=use_plan,
            enable_plots=args.plot,
            enable_smoke_test=args.smoke_test,
            expert_feedback=expert_feedback,
            verbose=args.verbose,
        )
    elif args.requirement:
        result = inspect_requirement(
            args.requirement,
            model=args.model,
            output_dir=args.output_dir,
            max_retries=args.max_retries,
            use_plan=use_plan,
            enable_plots=args.plot,
            enable_smoke_test=args.smoke_test,
            expert_feedback=expert_feedback,
            verbose=args.verbose,
        )
    else:
        parser.error("Provide a requirement or --md-file")

    if args.json_output:
        payload = dict(result.transcript_data or {"transcript": result.transcript})
        if not args.show_raw_llm:
            payload.pop("raw_llm_outputs", None)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(result.transcript)
    return 0 if result.ok else 1


def _read_interactive_feedback() -> str:
    print("专家反馈（可直接回车跳过）:", file=sys.stderr)
    return sys.stdin.readline().strip()


if __name__ == "__main__":
    raise SystemExit(main())
