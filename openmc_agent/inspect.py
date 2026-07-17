import argparse
import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.types import Command

from openmc_agent.few_shots import select_few_shots
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
from openmc_agent.logging_setup import configure_logging
from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
from openmc_agent.repair_proposal import make_repair_proposal_client
from openmc_agent.retrieval import make_default_investigation_llm
from openmc_agent.run_supervisor import make_run_supervisor_client
from openmc_agent.semantic_audit import make_semantic_audit_client
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
    operating_state: str | None = None,
    enable_plots: bool = False,
    enable_smoke_test: bool = False,
    expert_feedback: list[str] | None = None,
    interactive_feedback: bool = False,
    max_expert_rounds: int = 0,
    generate_plan: GeneratePlanFn = generate_structured_output,
    repair_plan: RepairPlanFn = repair_structured_output,
    export_xml_tool: ExportXmlToolFn = export_xml,
    plot_tool: PlotToolFn = run_geometry_plots,
    smoke_test_tool: SmokeTestToolFn = run_smoke_test,
    enable_investigation: bool = True,
    investigation_max_iterations: int = 4,
    enable_openmc_source_root: bool = False,
    knowledge_graph_path: str | None = None,
    enable_semantic_audit: bool = False,
    enable_llm_repair_proposer: bool = False,
    enable_run_supervisor: bool = False,
    run_supervisor_mode: str = "advisory",
    reference_patch_policy: str = "off",
    use_gold_few_shots: bool = True,
    allow_monolithic_fallback: bool = False,
    plan_loop_mode: str = "off",
    max_plan_review_rounds: int = 2,
    max_plan_repair_rounds: int = 2,
    max_plan_human_rounds: int = 2,
    max_plan_no_progress_rounds: int = 1,
    max_plan_additional_llm_calls: int = 20,
    plan_reviewer_model: str | None = None,
    plan_repair_model: str | None = None,
    facts_review_chunk_chars: int = 12000,
    max_facts_review_chunks: int = 8,
    plan_human_mode: str = "off",
    verbose: bool = False,
) -> InspectResult:
    configure_logging("INFO" if verbose else "WARNING")
    if operating_state:
        requirement = compose_operating_state_requirement(
            requirement, operating_state
        )
    if use_plan:
        return _inspect_plan_requirement(
            requirement,
            model=model,
            output_dir=output_dir,
            max_retries=max_retries,
            enable_plots=enable_plots,
            enable_smoke_test=enable_smoke_test,
            expert_feedback=expert_feedback or [],
            interactive_feedback=interactive_feedback,
            max_expert_rounds=max_expert_rounds,
            generate_plan=generate_plan,
            repair_plan=repair_plan,
            export_xml_tool=export_xml_tool,
            plot_tool=plot_tool,
            smoke_test_tool=smoke_test_tool,
            enable_investigation=enable_investigation,
            investigation_max_iterations=investigation_max_iterations,
            enable_openmc_source_root=enable_openmc_source_root,
            knowledge_graph_path=knowledge_graph_path,
            enable_semantic_audit=enable_semantic_audit,
            enable_llm_repair_proposer=enable_llm_repair_proposer,
            enable_run_supervisor=enable_run_supervisor,
            run_supervisor_mode=run_supervisor_mode,
            reference_patch_policy=reference_patch_policy,
            use_gold_few_shots=use_gold_few_shots,
            allow_monolithic_fallback=allow_monolithic_fallback,
            plan_loop_mode=plan_loop_mode,
            max_plan_review_rounds=max_plan_review_rounds,
            max_plan_repair_rounds=max_plan_repair_rounds,
            max_plan_human_rounds=max_plan_human_rounds,
            max_plan_no_progress_rounds=max_plan_no_progress_rounds,
            max_plan_additional_llm_calls=max_plan_additional_llm_calls,
            plan_reviewer_model=plan_reviewer_model,
            plan_repair_model=plan_repair_model,
            facts_review_chunk_chars=facts_review_chunk_chars,
            max_facts_review_chunks=max_facts_review_chunks,
            plan_human_mode=plan_human_mode,
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
    operating_state: str | None = None,
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
        operating_state=operating_state,
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


def compose_operating_state_requirement(
    requirement: str, operating_state: str
) -> str:
    """Prepend an explicit 'model only this operating state' directive to the
    full requirement text.

    The original problem description is kept verbatim below the directive so
    shared geometry/material context is preserved; the LLM is told to extract
    only the requested state's parameters and ignore the others.
    """
    state = operating_state.strip()
    header = (
        "=== Operating-state selection ===\n"
        f'This problem description defines multiple operating states '
        f'(e.g., 1A/1B/1C/...). Model ONLY operating state "{state}" in this '
        f"run. Extract its parameters from the state table / description below "
        f"and ignore all other states; do not merge or average parameters "
        f"across states.\n\n"
        "=== Original problem description ===\n"
    )
    return header + requirement


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
    interactive_feedback: bool,
    max_expert_rounds: int,
    generate_plan: GeneratePlanFn,
    repair_plan: RepairPlanFn,
    export_xml_tool: ExportXmlToolFn,
    plot_tool: PlotToolFn,
    smoke_test_tool: SmokeTestToolFn,
    enable_investigation: bool,
    investigation_max_iterations: int,
    enable_openmc_source_root: bool,
    knowledge_graph_path: str | None,
    enable_semantic_audit: bool,
    enable_llm_repair_proposer: bool,
    enable_run_supervisor: bool,
    run_supervisor_mode: str,
    reference_patch_policy: str,
    use_gold_few_shots: bool,
    allow_monolithic_fallback: bool,
    plan_loop_mode: str,
    max_plan_review_rounds: int,
    max_plan_repair_rounds: int,
    max_plan_human_rounds: int,
    max_plan_no_progress_rounds: int,
    max_plan_additional_llm_calls: int,
    plan_reviewer_model: str | None,
    plan_repair_model: str | None,
    facts_review_chunk_chars: int,
    max_facts_review_chunks: int,
    plan_human_mode: str,
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

    semantic_audit_client = None
    llm_repair_client = None
    run_supervisor_client = None
    if enable_semantic_audit or enable_llm_repair_proposer or enable_run_supervisor:
        if verbose:
            print("[agent] Initializing LLM intelligence modules...", file=sys.stderr)
        if enable_semantic_audit:
            semantic_audit_client = make_semantic_audit_client(
                llm=make_patch_llm_client(model_name=model), model_name=model
            )
        if enable_llm_repair_proposer:
            llm_repair_client = make_repair_proposal_client(
                llm=make_patch_llm_client(model_name=model), model_name=model
            )
        if enable_run_supervisor:
            run_supervisor_client = make_run_supervisor_client(
                llm=make_patch_llm_client(model_name=model), model_name=model
            )

    from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy

    plan_loop_policy = PlanClosedLoopPolicy(
        mode=plan_loop_mode,
        max_review_rounds_per_gate=max_plan_review_rounds,
        max_repair_rounds_per_gate=max_plan_repair_rounds,
        max_human_rounds_per_gate=max_plan_human_rounds,
        max_no_progress_rounds=max_plan_no_progress_rounds,
        max_total_additional_llm_calls=max_plan_additional_llm_calls,
        facts_review_chunk_chars=facts_review_chunk_chars,
        max_facts_review_chunks=max_facts_review_chunks,
        plan_human_mode=plan_human_mode,
        enable_human_gate=plan_human_mode == "ambiguity_only",
    )
    plan_reviewer_client = (
        make_patch_llm_client(model_name=plan_reviewer_model or model)
        if plan_loop_mode != "off" else None
    )
    plan_repair_client = (
        make_patch_llm_client(model_name=plan_repair_model or model)
        if plan_loop_mode == "controlled" else None
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
        investigation_llm=make_default_investigation_llm(model) if enable_investigation else None,
        investigation_max_iterations=investigation_max_iterations,
        enable_openmc_source_root=enable_openmc_source_root,
        checkpoint_path=(output_path / "checkpoints.sqlite") if interactive_feedback else None,
        knowledge_graph_path=knowledge_graph_path,
        enable_semantic_audit=enable_semantic_audit,
        semantic_audit_client=semantic_audit_client,
        semantic_audit_model=model,
        enable_llm_repair_proposer=enable_llm_repair_proposer,
        llm_repair_client=llm_repair_client,
        llm_repair_model=model,
        enable_run_supervisor=enable_run_supervisor,
        run_supervisor_mode=run_supervisor_mode,
        run_supervisor_client=run_supervisor_client,
        run_supervisor_model=model,
        reference_patch_policy=reference_patch_policy,
        allow_monolithic_fallback_for_incremental_failure=allow_monolithic_fallback,
        plan_loop_policy=plan_loop_policy,
        plan_reviewer_client=plan_reviewer_client,
        plan_repair_client=plan_repair_client,
        select_examples=functools.partial(
            select_few_shots, include_gold=use_gold_few_shots
        ),
    )
    if verbose:
        print("[agent] Invoking LLM. This can take 30-120 seconds on remote models...", file=sys.stderr)
    initial_state = {
        "requirement": requirement,
        "model": model,
        "output_dir": str(output_path),
        "records_path": str(records_path),
        "expert_feedback": expert_feedback,
        "max_expert_rounds": max_expert_rounds if interactive_feedback else 0,
        "verbose": verbose,
        "plan_human_interactive": bool(interactive_feedback and plan_human_mode == "ambiguity_only"),
    }
    config = (
        {"configurable": {"thread_id": f"inspect-plan-{uuid.uuid4().hex}"}}
        if interactive_feedback
        else None
    )
    state = _invoke_plan_graph_with_optional_feedback(
        graph=graph,
        initial_state=initial_state,
        config=config,
        interactive_feedback=interactive_feedback,
    )
    if verbose:
        print("[agent] Workflow finished. Formatting transcript...", file=sys.stderr)

    model_path = Path(state["model_path"]) if state.get("model_path") else None
    transcript_data = _plan_transcript_data(
        requirement=requirement,
        state=state,
        model_path=model_path,
        expert_feedback=state.get("expert_feedback", expert_feedback),
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


def _invoke_plan_graph_with_optional_feedback(
    *,
    graph,
    initial_state: dict,
    config: dict | None,
    interactive_feedback: bool,
) -> dict:
    state = graph.invoke(initial_state, config) if config else graph.invoke(initial_state)
    while _interrupt_payload(state) is not None:
        payload = _interrupt_payload(state) or {}
        if not interactive_feedback:
            return state
        reply = _read_expert_feedback(payload)
        feedback = reply.get("feedback", "")
        decision_action = reply.get("decision_action")
        # should_continue remains False for empty free text so the graph applies
        # the explicit defer/review-only semantics; an explicit decision command
        # or non-empty feedback drives the graph forward.
        should_continue = bool(feedback.strip()) or decision_action is not None
        state = graph.invoke(
            Command(
                resume={
                    "expert_feedback": feedback,
                    "should_continue": should_continue,
                    "decision_action": decision_action,
                }
            ),
            config,
        )
    return state


def _interrupt_payload(state: dict) -> dict | None:
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else {"value": value}


def _eprint(*parts: str) -> None:
    print("".join(parts), file=sys.stderr, flush=True)


def _stderr_supports_ansi() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "") == "dumb":
        return False
    return sys.stderr.isatty()


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _stderr_supports_ansi() else text


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _stderr_supports_ansi() else text


def _terminal_width(default: int = 72) -> int:
    try:
        return max(40, shutil.get_terminal_size((default, 20)).columns)
    except OSError:
        return default


def _print_feedback_panel(
    round_index: Any,
    max_rounds: Any,
    questions: list[str],
    instruction: str,
    blockers: dict | None = None,
) -> None:
    width = _terminal_width()
    _eprint()
    _eprint(_dim("─" * width))
    # Real execution blockers go at the top so material assumptions cannot
    # mask the structural/environment defect that actually forced a skeleton.
    if blockers and blockers.get("has_blocking_issue"):
        _eprint(f"  {_bold('真实执行状态')}")
        _eprint(
            f"    Renderability: {blockers.get('renderability', 'unknown')}    "
            f"Executable: {str(blockers.get('is_executable', False)).lower()}"
        )
        structural = [
            b for b in blockers.get("blocking_issues", [])
            if isinstance(b, dict) and b.get("route_type") == "agent-fixable"
        ]
        if structural:
            _eprint(
                _dim(
                    "    以下问题由 Agent 负责修复，不需要专家提供物理事实："
                )
            )
        for blk in blockers.get("blocking_issues", []):
            if not isinstance(blk, dict):
                continue
            tag = {
                "agent-fixable": "agent-fixable",
                "environment": "environment",
                "human-required": "human-required",
            }.get(blk.get("route_type", ""), blk.get("route_type", ""))
            _eprint(
                f"    [{blk.get('code', '?')}] ({tag}) {blk.get('message', '')}"
            )
        _eprint(_dim("─" * width))
    _eprint(f"  {_bold('专家反馈')}  {_dim(f'· 轮次 {round_index}/{max_rounds}')}")
    if questions:
        for index, question in enumerate(questions, start=1):
            _eprint(f"    {_bold(f'{index}.')} {question}")
    else:
        _eprint("    （无材料/事实确认问题；可自由补充建模要点或直接接受）")
    if instruction:
        _eprint(f"  {_dim(instruction)}")
    _eprint(_dim("─" * width))
    _eprint(
        "  "
        + _bold(":a")
        + _dim(" 接受假设(仅本次)  ")
        + _bold(":d")
        + _dim(" 推迟  ")
        + _bold(":r")
        + _dim(" 让Agent修复  ")
        + _bold(":s")
        + _dim(" 接受review-only  ")
        + _bold(":q")
        + _dim(" 中止  ")
        + _bold(":e")
        + _dim(" 编辑器")
    )
    _eprint(
        "  "
        + _dim("多行输入，")
        + _bold("空行结束")
        + _dim("；直接回车＝")
        + _dim("推迟确认（可执行模型）或接受review-only（skeleton）")
    )


def _read_decoded_line() -> str | None:
    # 直接读字节并以 replace 解码:在 utf-8 locale 下 sys.stdin 默认 errors=
    # surrogateescape,IME/终端送入的残字节(如 0xE5 0xAE)会被解码成 lone
    # surrogate (U+DCxx),下游 pydantic_core 无法把含 lone surrogate 的字符串
    # 编码为合法 UTF-8,会抛 string_unicode ValidationError。replace 把残字节
    # 替换成 U+FFFD,保证下游拿到的始终是合法 Unicode。
    raw = sys.stdin.buffer.readline()
    if not raw:  # EOF
        return None
    return raw.decode("utf-8", "replace")


def _accumulate_inline(first_line: str | None) -> str:
    lines: list[str] = []
    if first_line is not None:
        lines.append(first_line)
    while True:
        line = _read_decoded_line()
        if line is None or line.strip() == "":
            break
        lines.append(line)
    return "".join(lines)


def _edit_in_external_editor() -> str | None:
    """Open $EDITOR on a temp file and return its contents, or None if unavailable.

    Honors ``OPENMC_AGENT_EDITOR`` > ``EDITOR`` > ``VISUAL``; otherwise falls
    back to nano/vim/vi on PATH. Returns None when stdin is not a TTY or no
    editor is found so the caller can degrade to inline multi-line input.
    """
    if not sys.stdin.isatty():
        return None
    editor = (
        os.environ.get("OPENMC_AGENT_EDITOR")
        or os.environ.get("EDITOR")
        or os.environ.get("VISUAL")
    )
    if editor:
        command = shlex.split(editor)
    else:
        found = shutil.which("nano") or shutil.which("vim") or shutil.which("vi")
        if not found:
            return None
        command = [found]

    handle = tempfile.NamedTemporaryFile(
        "w+", suffix=".md", delete=False, encoding="utf-8"
    )
    handle.write(
        "# 在下方输入专家反馈，保存退出即提交。以 '#' 开头的注释行会被忽略。\n\n"
    )
    handle.flush()
    path = handle.name
    handle.close()
    try:
        subprocess.run([*command, path], check=False)
        with open(path, "r", encoding="utf-8") as reader:
            text = reader.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    kept = [
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ]
    return "\n".join(kept).strip()


_EXPERT_COMMAND_ACTION: dict[str, str] = {
    ":a": "accept_assumptions_for_this_run",
    ":d": "defer_confirmations",
    ":r": "continue_repair",
    ":s": "accept_review_only",
    ":q": "abort",
}


def _read_feedback_input() -> tuple[str, str | None]:
    """Return (feedback_text, decision_action).

    ``decision_action`` is one of the ExpertFeedbackDecision actions when the
    user typed a ``:a/:d/:r/:s/:q`` command, else None (free text or empty).
    """
    first = _read_decoded_line()
    if first is None:
        return "", None
    first_stripped = first.strip()

    if first_stripped in (":e", ":editor"):
        edited = _edit_in_external_editor()
        if edited is not None:
            return edited, None
        _eprint(_dim("（未找到可用编辑器，改为 inline 多行输入，空行结束）"))
        return _accumulate_inline(first_line=None), None

    if first_stripped in _EXPERT_COMMAND_ACTION:
        return "", _EXPERT_COMMAND_ACTION[first_stripped]

    if first_stripped == "":
        return "", None
    return _accumulate_inline(first_line=first), None


def _read_expert_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Render the expert-feedback panel and collect the expert's reply.

    Returns a dict with ``feedback`` (text, possibly multi-line; empty means no
    free-text feedback) and ``decision_action`` (an explicit
    ExpertFeedbackDecision action when a ``:command`` was used, else None).
    """
    round_index = payload.get("round", "?")
    max_rounds = payload.get("max_rounds", "?")
    questions = payload.get("questions") or []
    instruction = payload.get("instruction") or ""
    blockers = payload.get("capability_blockers")

    _print_feedback_panel(round_index, max_rounds, questions, instruction, blockers)

    feedback, decision_action = _read_feedback_input()
    feedback = feedback.strip()
    if decision_action:
        _eprint(_dim(f"专家指令：{decision_action}"))
    elif feedback:
        line_count = feedback.count("\n") + 1
        _eprint(
            _dim(
                f"已收到专家反馈（{line_count} 行 / {len(feedback)} 字符），"
                "写回图状态并继续。"
            )
        )
    else:
        _eprint(
            _dim(
                "未输入反馈：可执行模型将推迟确认；skeleton 将作为 review-only 保留"
                "（BLOCKED_REVIEW_ONLY）。"
            )
        )
    return {"feedback": feedback, "decision_action": decision_action}


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
        "pending_expert_questions": state.get("pending_expert_questions", []),
        "expert_round_count": state.get("expert_round_count", 0),
        "expert_feedback_action": state.get("expert_feedback_action", "none"),
        "resolved_expert_items": state.get("resolved_expert_items", []),
        "plan_patch": state.get("plan_patch"),
        "patch_confidence": state.get("patch_confidence"),
        "patch_reason": state.get("patch_reason"),
        "patch_error": state.get("patch_error"),
        "human_loop_events": state.get("human_loop_events", []),
        "tool_results": tool_results,
        "investigation_trace": state.get("investigation_trace", []),
        "investigation_findings": state.get("investigation_findings", ""),
        "semantic_audit_result": state.get("semantic_audit_result"),
        "repair_proposal_result": state.get("repair_proposal_result"),
        "run_supervisor_result": state.get("run_supervisor_result"),
        "raw_llm_outputs": state.get("raw_llm_outputs", []),
        "plan_artifacts": state.get("plan_artifacts", []),
        "model_path": str(model_path) if model_path is not None else None,
        "error": state.get("error", ""),
        "capability_blocker_summary": state.get("capability_blocker_summary") or {},
        "expert_question_groups": state.get("expert_question_groups") or [],
        "expert_feedback_decision": state.get("expert_feedback_decision") or {},
        "expert_assumption_acknowledgements": state.get("expert_assumption_acknowledgements") or [],
        "workflow_outcome": state.get("workflow_outcome") or {},
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
        "[8a] 待专家确认问题",
        _json_dump(data.get("pending_expert_questions", [])),
        "",
        "[8b] 专家反馈处理",
        _json_dump(
            {
                "action": data.get("expert_feedback_action"),
                "resolved_expert_items": data.get("resolved_expert_items", []),
                "plan_patch": data.get("plan_patch"),
                "patch_confidence": data.get("patch_confidence"),
                "patch_reason": data.get("patch_reason"),
                "patch_error": data.get("patch_error"),
            }
        ),
        "",
        "[8c] 人机回路事件",
        _json_dump(data.get("human_loop_events", [])),
        "",
        "[9] 工具执行结果",
        _json_dump(data.get("tool_results", [])),
        "",
        "[9a] 代码检索轨迹",
        _json_dump(data.get("investigation_trace", [])),
        "",
        "[9b] 检索发现",
        str(data.get("investigation_findings", "")),
        "",
        "[9c] Plan artifacts",
        "\n".join(data.get("plan_artifacts", []) or ["(none)"]),
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
    parser.add_argument(
        "--state",
        dest="operating_state",
        default=None,
        help="Select one operating state (e.g., 1A) when the markdown describes "
             "multiple states; only that state is modeled.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", default="data/runs/inspect")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--plan", action="store_true", help="Use SimulationPlan workflow")
    parser.add_argument("--plot", action="store_true", help="Run OpenMC geometry plots")
    parser.add_argument("--smoke-test", action="store_true", help="Run low-particle OpenMC smoke test")
    parser.add_argument(
        "--interactive-feedback",
        action="store_true",
        default=None,
        help="Enable LangGraph interrupt/resume expert questions.",
    )
    parser.add_argument(
        "--no-interactive-feedback",
        action="store_false",
        dest="interactive_feedback",
        help="Disable expert question interrupts, useful for batch runs.",
    )
    parser.add_argument("--max-expert-rounds", type=int, default=2)
    parser.add_argument("--expert-feedback", action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--show-raw-llm", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print a concise terminal run summary; write full JSON to transcript.json.",
    )
    parser.add_argument(
        "--enable-semantic-audit",
        action="store_true",
        help="Run the semantic audit LLM module after capability assessment.",
    )
    parser.add_argument(
        "--enable-llm-repair",
        action="store_true",
        help="Run the proposal-only LLM repair module after semantic audit.",
    )
    parser.add_argument(
        "--enable-run-supervisor",
        action="store_true",
        help="Run the advisory run-supervisor LLM module before routing.",
    )
    parser.add_argument(
        "--controlled-route",
        action="store_true",
        help="Let the run-supervisor override routing after policy validation.",
    )
    parser.add_argument(
        "--investigate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable code retrieval + surgical IR patch in reflect_plan (default: on). "
        "Pass --no-investigate to disable.",
    )
    parser.add_argument(
        "--investigate-iterations",
        type=int,
        default=4,
        help="Max retrieval-loop iterations per generate/reflect phase (default: 4).",
    )
    parser.add_argument(
        "--investigate-openmc-source",
        action="store_true",
        help="Also let retrieval grep the installed OpenMC library source (default: off).",
    )
    parser.add_argument(
        "--knowledge-dir",
        dest="knowledge_dir",
        default=None,
        help="Directory of persisted knowledge assets (knowledge_graph_nodes.json, "
        "knowledge_summary.json) produced by the ingestion CLI. Also settable via "
        "the OPENMC_AGENT_KNOWLEDGE_DIR environment variable.",
    )
    parser.add_argument(
        "--reference-patch-policy",
        choices=[
            "off",
            "fallback_after_llm_failure",
            "prefer_reference_for_structural",
            "reference_only_for_structural",
        ],
        default="off",
        help="Benchmark reference-patch policy for incremental planning (default: off). "
        "off = no benchmark reference data loaded.",
    )
    parser.add_argument(
        "--gold-few-shots",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable gold few-shot examples from data/few_shot_cases/ (default: off). "
        "Pass --gold-few-shots to enable, --no-gold-few-shots to explicitly disable.",
    )
    parser.add_argument(
        "--allow-monolithic-fallback",
        action="store_true",
        help="Allow monolithic plan fallback when incremental patch generation fails "
        "(default: off).",
    )
    parser.add_argument(
        "--plan-loop-mode", choices=["off", "advisory", "controlled"], default="off",
        help="Phase-0 plan closed-loop mode (default: off). advisory writes only foundation artifacts.",
    )
    parser.add_argument("--max-plan-review-rounds", type=int, default=2)
    parser.add_argument("--max-plan-repair-rounds", type=int, default=2)
    parser.add_argument("--max-plan-human-rounds", type=int, default=2)
    parser.add_argument("--max-plan-no-progress-rounds", type=int, default=1)
    parser.add_argument("--max-plan-additional-llm-calls", type=int, default=20)
    parser.add_argument("--plan-reviewer-model", default=None)
    parser.add_argument("--plan-repair-model", default=None)
    parser.add_argument("--facts-review-chunk-chars", type=int, default=12000)
    parser.add_argument("--max-facts-review-chunks", type=int, default=8)
    parser.add_argument("--plan-human-mode", choices=["off", "ambiguity_only"], default="off")
    args = parser.parse_args(argv)
    if args.compact and args.json_output:
        parser.error("Use either --compact or --json, not both")

    expert_feedback = list(args.expert_feedback)
    interactive_feedback = (
        bool(args.interactive_feedback)
        if args.interactive_feedback is not None
        else sys.stdin.isatty()
    )

    use_plan = (
        args.plan
        or args.plan_loop_mode != "off"
        or args.plot
        or args.smoke_test
        or interactive_feedback
        or bool(expert_feedback)
    )
    if args.md_file and args.requirement:
        parser.error("Use either a positional requirement or --md-file, not both")
    if args.md_file:
        result = inspect_markdown_file(
            args.md_file,
            model=args.model,
            output_dir=args.output_dir,
            max_retries=args.max_retries,
            use_plan=use_plan,
            operating_state=args.operating_state,
            enable_plots=args.plot,
            enable_smoke_test=args.smoke_test,
            expert_feedback=expert_feedback,
            interactive_feedback=interactive_feedback,
            max_expert_rounds=args.max_expert_rounds,
            verbose=args.verbose,
            enable_investigation=args.investigate,
            investigation_max_iterations=args.investigate_iterations,
            enable_openmc_source_root=args.investigate_openmc_source,
            knowledge_graph_path=args.knowledge_dir,
            enable_semantic_audit=args.enable_semantic_audit,
            enable_llm_repair_proposer=args.enable_llm_repair,
            enable_run_supervisor=args.enable_run_supervisor or args.controlled_route,
            run_supervisor_mode="controlled_route" if args.controlled_route else "advisory",
            reference_patch_policy=args.reference_patch_policy,
            use_gold_few_shots=args.gold_few_shots,
            allow_monolithic_fallback=args.allow_monolithic_fallback,
            plan_loop_mode=args.plan_loop_mode,
            max_plan_review_rounds=args.max_plan_review_rounds,
            max_plan_repair_rounds=args.max_plan_repair_rounds,
            max_plan_human_rounds=args.max_plan_human_rounds,
            max_plan_no_progress_rounds=args.max_plan_no_progress_rounds,
            max_plan_additional_llm_calls=args.max_plan_additional_llm_calls,
            plan_reviewer_model=args.plan_reviewer_model,
            plan_repair_model=args.plan_repair_model,
            facts_review_chunk_chars=args.facts_review_chunk_chars,
            max_facts_review_chunks=args.max_facts_review_chunks,
            plan_human_mode=args.plan_human_mode,
        )
    elif args.requirement:
        result = inspect_requirement(
            args.requirement,
            model=args.model,
            output_dir=args.output_dir,
            max_retries=args.max_retries,
            use_plan=use_plan,
            operating_state=args.operating_state,
            enable_plots=args.plot,
            enable_smoke_test=args.smoke_test,
            expert_feedback=expert_feedback,
            interactive_feedback=interactive_feedback,
            max_expert_rounds=args.max_expert_rounds,
            verbose=args.verbose,
            enable_investigation=args.investigate,
            investigation_max_iterations=args.investigate_iterations,
            enable_openmc_source_root=args.investigate_openmc_source,
            knowledge_graph_path=args.knowledge_dir,
            enable_semantic_audit=args.enable_semantic_audit,
            enable_llm_repair_proposer=args.enable_llm_repair,
            enable_run_supervisor=args.enable_run_supervisor or args.controlled_route,
            run_supervisor_mode="controlled_route" if args.controlled_route else "advisory",
            reference_patch_policy=args.reference_patch_policy,
            use_gold_few_shots=args.gold_few_shots,
            allow_monolithic_fallback=args.allow_monolithic_fallback,
            plan_loop_mode=args.plan_loop_mode,
            max_plan_review_rounds=args.max_plan_review_rounds,
            max_plan_repair_rounds=args.max_plan_repair_rounds,
            max_plan_human_rounds=args.max_plan_human_rounds,
            max_plan_no_progress_rounds=args.max_plan_no_progress_rounds,
            max_plan_additional_llm_calls=args.max_plan_additional_llm_calls,
            plan_reviewer_model=args.plan_reviewer_model,
            plan_repair_model=args.plan_repair_model,
            facts_review_chunk_chars=args.facts_review_chunk_chars,
            max_facts_review_chunks=args.max_facts_review_chunks,
            plan_human_mode=args.plan_human_mode,
        )
    else:
        parser.error("Provide a requirement or --md-file")

    if args.compact:
        print(_format_compact_summary(result, Path(args.output_dir)))
    elif args.json_output:
        payload = dict(result.transcript_data or {"transcript": result.transcript})
        if not args.show_raw_llm:
            payload.pop("raw_llm_outputs", None)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(result.transcript)
    return 0 if result.ok else 1


def _format_compact_summary(result: InspectResult, output_path: Path) -> str:
    """Render the terminal-facing result without echoing the full plan or input."""
    data = result.transcript_data or {}
    report = data.get("validation_report") or {}
    capability = data.get("capability_report") or {}
    render = data.get("render_outcome") or {}
    outcome = data.get("workflow_outcome") or {}
    blocker_summary = data.get("capability_blocker_summary") or {}
    renderability = capability.get("renderability", "unknown")
    outcome_status = outcome.get("status") if isinstance(outcome, dict) else None
    # Display label: a review-only skeleton is BLOCKED_REVIEW_ONLY, not a vague
    # FAIL. result.ok stays False internally so CI never treats a skeleton as a
    # success; only the human-facing label changes.
    if outcome_status == "blocked_review_only" or (
        not result.ok and renderability in {"skeleton", "none"} and outcome_status != "fail"
    ):
        status_label = "BLOCKED_REVIEW_ONLY"
    elif result.ok:
        status_label = "PASS"
    else:
        status_label = "FAIL"
    lines = [
        "\nOpenMC Agent Run Summary",
        f"  status: {status_label}",
        f"  validation: {'valid' if report.get('is_valid') else 'invalid'}",
        f"  capability: {renderability} / "
        f"{capability.get('supported_renderer', 'unknown')}",
        f"  retry_count: {data.get('retry_count', 0)}",
    ]
    if status_label == "BLOCKED_REVIEW_ONLY":
        lines.append(
            f"  model.py: review-only skeleton"
            if renderability == "skeleton"
            else "  model.py: not generated"
        )
        lines.append("  OpenMC execution: not attempted")
        codes = blocker_summary.get("primary_blocker_codes") or outcome.get("reason_codes") or []
        if codes:
            lines.append(f"  blocking issues: {', '.join(codes)}")
        decision = data.get("expert_feedback_decision") or {}
        if isinstance(decision, dict) and decision.get("action"):
            lines.append(f"  expert decision: {decision.get('action')}")
    for name, key in (
        ("semantic_audit", "semantic_audit_result"),
        ("llm_repair", "repair_proposal_result"),
        ("run_supervisor", "run_supervisor_result"),
    ):
        value = data.get(key)
        if isinstance(value, dict):
            status = value.get("status") or value.get("final_action") or "completed"
            suffix = " fallback" if value.get("fallback_used") else ""
            lines.append(f"  {name}: {status}{suffix}")
    if status_label != "BLOCKED_REVIEW_ONLY":
        for line in render.get("lines", [])[:3]:
            lines.append(f"  output: {line}")
    if data.get("error"):
        lines.append(f"  error: {data['error']}")
    lines.extend(
        [
            f"  model.py: {data.get('model_path') or '(not generated)'}",
            f"  full report: {output_path / 'transcript.json'}",
            f"  node log: {output_path / 'cli.log'}",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
