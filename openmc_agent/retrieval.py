"""Read-only code retrieval tools and an LLM-driven investigation loop.

This module gives the OpenMC agent a Claude-Code-like ability to look before
it leaps: when ``reflect_plan`` cannot fix a bug by regenerating the whole
SimulationPlan, the LLM can first grep/read the repository, the rendered
``model.py``, the error catalog, and (optionally) the OpenMC library source,
then emit a surgical JSON Patch against the SimulationPlan IR.

Design constraints honoured here:

* **Read-only.** No write/move/delete tools are registered.
* **Path safety.** Every tool resolves its target under a caller-supplied
  ``roots`` whitelist via :func:`_resolve_within_roots`.
* **Extensibility.** New tools / roots / phases are additive — append to
  :data:`DEFAULT_TOOL_SPECS` / :data:`DEFAULT_TOOL_DISPATCH` or pass a custom
  resolver. The LangGraph topology is untouched; this loop runs *inside* the
  generate/reflect node bodies, not as new graph nodes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from openmc_agent.llm import StructuredOutputResult, generate_structured_output
from openmc_agent.tools import ToolResult


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Declaration of one retrieval tool, shown to the LLM."""

    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation requested by the LLM."""

    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class RetrievalOutcome:
    """Return value of :func:`run_retrieval_loop`."""

    findings: str
    patch: list[dict[str, Any]] | None
    trace: list[dict[str, Any]]
    ok: bool = True
    error: str = ""


class RetrievalStep(BaseModel):
    """LLM output schema for one iteration of the investigation loop.

    Pydantic (not a frozen dataclass) because it flows through
    :func:`openmc_agent.llm.generate_structured_output`, which validates the
    model with ``schema.model_validate``.
    """

    action: Literal["investigate", "done"] = Field(
        description=(
            "investigate=call a retrieval tool to gather evidence; "
            "done=finish and optionally emit a patch"
        )
    )
    reasoning: str = Field(
        description="One short sentence explaining why this step is needed."
    )
    tool: str | None = Field(
        default=None,
        description="Tool name when action='investigate'; null when action='done'.",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments when action='investigate'; empty when action='done'.",
    )
    findings: str = Field(
        default="",
        description=(
            "When action='done': summarize what the retrieved evidence shows and "
            "how it fixes the error."
        ),
    )
    patch: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "When action='done': optional JSON Patch (RFC 6902) targeting the "
            "SimulationPlan JSON. Each item is "
            "{op:'add'|'replace'|'remove', path:'/...', value:...}. Only patch "
            "fields verified against retrieved evidence. Set null when the defect "
            "is too large for a surgical patch."
        ),
    )
    no_patch_reason: str = Field(
        default="",
        description="When patch is null: explain why a patch cannot fix this defect.",
    )


# ---------------------------------------------------------------------------
# Path whitelist
# ---------------------------------------------------------------------------


def _resolved_roots(roots: list[Path]) -> list[Path]:
    """Resolve and de-duplicate roots, keeping only existing directories."""
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        try:
            resolved = Path(root).resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if not resolved.is_dir() or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _resolve_within_roots(raw_path: str, roots: list[Path]) -> Path:
    """Resolve ``raw_path`` to an absolute path that must live under a root.

    Raises :class:`PermissionError` if the resolved path escapes every root.
    ``Path.resolve`` collapses ``..``, symlinks, and ``.`` so the subsequent
    ``relative_to`` prefix check is robust against traversal tricks. Relative
    paths are joined to each root in turn; the first root that contains the
    resolved path wins.
    """
    candidate = Path(raw_path)
    raw_candidates = (
        [candidate] if candidate.is_absolute() else [root / candidate for root in roots]
    )
    resolved_roots = [root.resolve(strict=False) for root in roots]
    for raw in raw_candidates:
        try:
            resolved = raw.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        for root in resolved_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
    raise PermissionError(
        f"path {raw_path!r} resolves outside all retrieval roots; "
        f"roots={[str(r) for r in resolved_roots]}"
    )


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


# ---------------------------------------------------------------------------
# Read-only retrieval tools
# ---------------------------------------------------------------------------


def grep_code(
    pattern: str,
    *,
    roots: list[Path],
    file_glob: str = "*.py",
    max_matches: int = 30,
    context_lines: int = 2,
) -> ToolResult:
    """Recursively search file contents under ``roots`` with a regex.

    Uses Python ``re`` (never shells out to the system ``grep``) so a crafted
    pattern cannot inject shell commands. Returns matching lines with file
    path, line number, and surrounding context.
    """
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return ToolResult(name="grep_code", ok=False, error=f"invalid regex: {exc}")
    resolved_roots = _resolved_roots(roots)
    matches: list[str] = []
    seen_files: set[Path] = set()
    truncated = False
    for root in resolved_roots:
        for file_path in root.rglob(file_glob):
            if not file_path.is_file() or file_path in seen_files:
                continue
            seen_files.add(file_path)
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            for idx, line in enumerate(lines):
                if regex.search(line):
                    start = max(0, idx - context_lines)
                    end = min(len(lines), idx + context_lines + 1)
                    snippet = "\n".join(
                        f"{start + k + 1}: {lines[start + k]}" for k in range(end - start)
                    )
                    matches.append(f"{file_path}:{idx + 1}:\n{snippet}")
                    if len(matches) >= max_matches:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break
    header = f"pattern={pattern!r} glob={file_glob!r} matches={len(matches)}"
    if truncated:
        header += f" (truncated at {max_matches})"
    body = "\n\n".join(matches)
    return ToolResult(
        name="grep_code",
        ok=True,
        stdout=_truncate(f"{header}\n{body}", 4000),
    )


def read_file(
    path: str,
    *,
    roots: list[Path],
    start_line: int = 1,
    end_line: int | None = None,
    max_lines: int = 200,
) -> ToolResult:
    """Read a line slice of a file under ``roots``."""
    try:
        resolved = _resolve_within_roots(path, roots)
    except PermissionError as exc:
        return ToolResult(name="read_file", ok=False, error=str(exc))
    if not resolved.is_file():
        return ToolResult(name="read_file", ok=False, error=f"not a file: {resolved}")
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(name="read_file", ok=False, error=str(exc))
    lines = text.splitlines()
    start = max(1, start_line)
    end = len(lines) if end_line is None else min(end_line, len(lines))
    end = min(end, start + max_lines - 1)
    selected = lines[start - 1 : end]
    body = "\n".join(f"{start + i}: {selected[i]}" for i in range(len(selected)))
    return ToolResult(
        name="read_file",
        ok=True,
        stdout=_truncate(body, 4000),
        artifacts=[str(resolved)],
    )


def list_dir(
    path: str,
    *,
    roots: list[Path],
    max_entries: int = 100,
) -> ToolResult:
    """List entries in a directory under ``roots``."""
    try:
        resolved = _resolve_within_roots(path, roots)
    except PermissionError as exc:
        return ToolResult(name="list_dir", ok=False, error=str(exc))
    if not resolved.is_dir():
        return ToolResult(name="list_dir", ok=False, error=f"not a directory: {resolved}")
    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError as exc:
        return ToolResult(name="list_dir", ok=False, error=str(exc))
    lines: list[str] = []
    for entry in entries[:max_entries]:
        if entry.is_dir():
            lines.append(f"{entry.name}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{entry.name} ({size} bytes)")
    return ToolResult(
        name="list_dir",
        ok=True,
        stdout="\n".join(lines) or "(empty)",
        artifacts=[str(resolved)],
    )


def locate_definition(
    symbol: str,
    *,
    roots: list[Path],
    max_results: int = 10,
) -> ToolResult:
    """Find where a Python class/function/field is defined under ``roots``."""
    safe = re.escape(symbol)
    patterns = [
        rf"^\s*(class|def)\s+{safe}\b",
        rf"^\s*{safe}\s*[:=]",
    ]
    merged: list[str] = []
    for pattern in patterns:
        result = grep_code(
            pattern,
            roots=roots,
            file_glob="*.py",
            max_matches=max_results,
            context_lines=1,
        )
        if not result.ok:
            continue
        # grep_code stdout is "header\n<match blocks>"; drop the header and keep
        # only non-empty match bodies so a `matches=0` header is not mistaken for a hit.
        lines = result.stdout.split("\n")
        body_after_header = "\n".join(lines[1:]).strip() if lines else ""
        if body_after_header:
            merged.append(body_after_header)
    body = "\n---\n".join(merged).strip()
    return ToolResult(
        name="locate_definition",
        ok=True,
        stdout=body or f"no definition of {symbol!r} found under roots",
    )


# ---------------------------------------------------------------------------
# Tool registry (additive extension point)
# ---------------------------------------------------------------------------


DEFAULT_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="grep_code",
        description=(
            "Search file contents under whitelisted directories using a Python regex. "
            "Returns matching lines with file path, line number, and surrounding context."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex pattern"},
                "file_glob": {"type": "string", "default": "*.py"},
                "max_matches": {"type": "integer", "default": 30},
            },
            "required": ["pattern"],
        },
    ),
    ToolSpec(
        name="read_file",
        description="Read a line slice of a file under whitelisted directories.",
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "relative or absolute path"},
                "start_line": {"type": "integer", "default": 1},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="list_dir",
        description="List entries in a directory under whitelisted roots.",
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="locate_definition",
        description=(
            "Find where a Python class/function/field is defined under whitelisted roots. "
            "Use this to look up an OpenMC API symbol, a SimulationPlan field, "
            "or an error_catalog entry."
        ),
        parameters_schema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    ),
]


DEFAULT_TOOL_DISPATCH: dict[str, Callable[..., ToolResult]] = {
    "grep_code": grep_code,
    "read_file": read_file,
    "list_dir": list_dir,
    "locate_definition": locate_definition,
}


# ---------------------------------------------------------------------------
# Investigation loop
# ---------------------------------------------------------------------------


def _tool_result_excerpt(result: ToolResult) -> str:
    parts = [f"ok={result.ok}"]
    if result.error:
        parts.append(f"error={_truncate(result.error, 400)}")
    if result.stdout:
        parts.append(f"stdout=\n{_truncate(result.stdout, 1500)}")
    return "\n".join(parts)


def _build_investigation_prompt(
    *,
    phase: Literal["generate", "reflect"],
    task_brief: str,
    plan_summary: str,
    tool_specs: list[ToolSpec],
    roots_repr: str,
    history: list[dict[str, Any]],
    error_catalog_hints: list[dict[str, Any]] | None,
    iteration: int,
    max_iterations: int,
    force_done: bool,
) -> str:
    specs_repr = json.dumps(
        [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters_schema,
            }
            for spec in tool_specs
        ],
        ensure_ascii=False,
        indent=2,
    )
    plan_section = ""
    if plan_summary:
        plan_section = (
            "CURRENT SimulationPlan JSON (target for any patch; do not echo it back):\n"
            f"{_truncate(plan_summary, 4000)}\n\n"
        )
    hints_section = ""
    if error_catalog_hints:
        hints_section = (
            "ERROR CATALOG HINTS (pre-matched; verify against retrieved evidence "
            "before trusting):\n"
            f"{json.dumps(error_catalog_hints, ensure_ascii=False, indent=2)}\n\n"
        )
    history_section = "(none yet)"
    if history:
        history_section = "\n\n".join(
            f"step {i + 1}: tool={h['tool']!r} args={h['arguments']}\n{h['result_excerpt']}"
            for i, h in enumerate(history)
        )

    instructions = (
        "INSTRUCTIONS:\n"
        "1. To gather evidence, output "
        "{action:'investigate', tool:<name>, arguments:{...}}.\n"
        "2. When you have enough evidence, output "
        "{action:'done', findings:'...', patch:[...] | null}.\n"
        "3. patch is a JSON Patch (RFC 6902) targeting the SimulationPlan JSON. Each op "
        "is {op:'add'|'replace'|'remove', path:'/...', value:...} where path is a JSON "
        "Pointer (e.g. '/model_spec/pin_cell/fuel/temperature_k').\n"
        "4. Only patch fields you VERIFIED against retrieved evidence (grep/read results).\n"
        "5. If the defect is structural and too large for a surgical patch (e.g. an entire "
        "lattice universe_pattern is wrong), set patch=null and explain in no_patch_reason. "
        "Do not patch renderer-generated derived axial lattices directly; patch the source "
        "IR in complex_model.lattice_loadings instead. "
        "A full plan regeneration will follow.\n"
        "6. Do NOT patch capability_report -- it is locally recomputed.\n"
        "7. Do NOT pass 'roots' in arguments; roots are system-controlled.\n"
    )
    force_hint = ""
    if force_done:
        force_hint = (
            f"\nThis is your LAST step (iteration {iteration + 1}/{max_iterations}): "
            "you MUST output action='done'.\n"
        )

    verb = "patching" if phase == "reflect" else "producing"
    return (
        f"You are investigating a {phase} problem before {verb} an OpenMC SimulationPlan.\n\n"
        f"TASK:\n{_truncate(task_brief, 4000)}\n\n"
        f"{plan_section}"
        f"AVAILABLE RETRIEVAL TOOLS:\n{specs_repr}\n\n"
        f"WHITELISTED ROOTS (you may only read inside these):\n{roots_repr}\n\n"
        f"{hints_section}"
        f"PRIOR INVESTIGATION STEPS THIS SESSION:\n{history_section}\n\n"
        f"{instructions}{force_hint}"
    )


def run_retrieval_loop(
    *,
    phase: Literal["generate", "reflect"],
    task_brief: str,
    plan_summary: str,
    roots: list[Path],
    investigation_llm: Callable[[str], StructuredOutputResult[RetrievalStep]],
    tool_dispatch: dict[str, Callable[..., ToolResult]] | None = None,
    tool_specs: list[ToolSpec] | None = None,
    max_iterations: int = 4,
    error_catalog_hints: list[dict[str, Any]] | None = None,
) -> RetrievalOutcome:
    """Run a multi-turn retrieval loop.

    Each iteration: build a prompt from the task brief + prior tool history,
    ask the LLM for a :class:`RetrievalStep`, execute an investigate tool or
    terminate with findings + optional patch. Bounded by ``max_iterations``;
    the final iteration forces ``action='done'`` via the prompt.
    """
    dispatch = tool_dispatch or DEFAULT_TOOL_DISPATCH
    specs = tool_specs or DEFAULT_TOOL_SPECS
    resolved = _resolved_roots(roots)
    roots_repr = "\n".join(f"- {r}" for r in resolved) or "(none)"
    history: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    carried_findings = ""

    for iteration in range(max_iterations):
        force_done = iteration == max_iterations - 1
        user_prompt = _build_investigation_prompt(
            phase=phase,
            task_brief=task_brief,
            plan_summary=plan_summary,
            tool_specs=specs,
            roots_repr=roots_repr,
            history=history,
            error_catalog_hints=error_catalog_hints,
            iteration=iteration,
            max_iterations=max_iterations,
            force_done=force_done,
        )
        result = investigation_llm(user_prompt)
        if not result.ok or result.value is None:
            return RetrievalOutcome(
                findings=carried_findings,
                patch=None,
                trace=trace,
                ok=False,
                error=result.error or f"investigation llm failed at iteration {iteration}",
            )
        step: RetrievalStep = result.value
        trace.append(
            {
                "iteration": iteration,
                "action": step.action,
                "reasoning": step.reasoning,
                "tool": step.tool,
                "arguments": step.arguments,
            }
        )
        if step.action == "done":
            return RetrievalOutcome(
                findings=step.findings or carried_findings,
                patch=step.patch,
                trace=trace,
            )
        # action == "investigate"
        tool_name = step.tool or ""
        tool_fn = dispatch.get(tool_name)
        if tool_fn is None:
            history.append(
                {
                    "tool": tool_name,
                    "arguments": step.arguments,
                    "result_excerpt": f"ERROR: unknown tool {tool_name!r}",
                }
            )
            continue
        arguments = {k: v for k, v in step.arguments.items() if k != "roots"}
        try:
            tool_result = tool_fn(roots=list(resolved), **arguments)
        except TypeError as exc:
            history.append(
                {
                    "tool": tool_name,
                    "arguments": arguments,
                    "result_excerpt": f"ERROR: bad arguments: {exc}",
                }
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive, keep loop alive
            history.append(
                {
                    "tool": tool_name,
                    "arguments": arguments,
                    "result_excerpt": f"ERROR: {exc}",
                }
            )
            continue
        history.append(
            {
                "tool": tool_name,
                "arguments": arguments,
                "result_excerpt": _tool_result_excerpt(tool_result),
            }
        )

    return RetrievalOutcome(
        findings=carried_findings,
        patch=None,
        trace=trace,
        ok=False,
        error=f"investigation did not converge in {max_iterations} iterations",
    )


def make_default_investigation_llm(
    model: str | None = None,
    client: Any | None = None,
) -> Callable[[str], StructuredOutputResult[RetrievalStep]]:
    """Build the default investigation LLM callable.

    Wraps :func:`generate_structured_output` with ``schema=RetrievalStep``.
    The system prompt is ``BASE_SYSTEM_PROMPT`` automatically (because
    ``RetrievalStep.__name__ != 'SimulationPlan'``); retrieval-specific rules
    live in the user prompt built by :func:`_build_investigation_prompt`.
    """

    def _call(user_prompt: str) -> StructuredOutputResult[RetrievalStep]:
        return generate_structured_output(
            requirement=user_prompt,
            schema=RetrievalStep,
            model=model,
            client=client,
        )

    return _call
