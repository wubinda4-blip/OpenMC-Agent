"""LLM patch generator for incremental plan building (Phase 4).

Calls an LLM to generate **exactly one** patch at a time.  Each patch is a
small JSON object (a few hundred to a few thousand characters) — never a 25 KB
monolithic SimulationPlan.  Parse failures and validation failures trigger a
targeted retry for the *current patch only*; already-valid patches in the
:class:`PlanBuildState` are never touched.

Design constraints
------------------
* **No full plan.**  The generator never outputs a SimulationPlan.
* **No full lattice.**  PinMapPatch contains only special coordinates.
* **Fake-LLM friendly.**  The ``llm_client`` is a simple callable
  ``(prompt: str) -> str``; tests inject a :class:`FakePatchLLM`.
* **No OpenMC, no renderer.**
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .patches import (
    PatchParseError,
    parse_patch_content,
)
from .patch_prompts import build_patch_prompt, build_retry_prompt
from .state import PlanBuildState, PlanPatchEnvelope
from .validators import (
    PatchValidationContext,
    PatchValidationResult,
    validate_patch,
)


# ---------------------------------------------------------------------------
# Context / attempt / result models
# ---------------------------------------------------------------------------


class PatchGenerationContext(AgentBaseModel):
    """Context passed to the patch generator and prompt builder."""

    benchmark_id: str | None = None
    selected_variant: str | None = None
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    extracted_facts: dict[str, Any] = Field(default_factory=dict)
    validated_patch_summaries: dict[str, Any] = Field(default_factory=dict)
    reference_summary: dict[str, Any] = Field(default_factory=dict)
    strict_benchmark: bool = False
    expected_counts: dict[str, int] = Field(default_factory=dict)
    known_material_ids: list[str] = Field(default_factory=list)
    known_universe_ids: list[str] = Field(default_factory=list)
    known_lattice_ids: list[str] = Field(default_factory=list)
    active_fuel_region_cm: tuple[float, float] | None = None
    axial_domain_cm: tuple[float, float] | None = None


class PatchGenerationAttempt(AgentBaseModel):
    """Record of a single LLM call attempt."""

    attempt_index: int
    raw_text: str | None = None
    raw_chars: int = 0
    parsed: bool = False
    validated: bool = False
    issues: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    contains_full_plan_markers: bool = False
    contains_full_lattice_suspected: bool = False


class PatchGenerationResult(AgentBaseModel):
    """Result of :func:`generate_patch`."""

    ok: bool = False
    patch_type: str
    envelope: PlanPatchEnvelope | None = None
    parsed_patch: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    attempts: list[PatchGenerationAttempt] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Output diagnostics
# ---------------------------------------------------------------------------

# Markers that suggest the LLM returned a full SimulationPlan instead of a patch.
_FULL_PLAN_MARKERS: tuple[str, ...] = (
    '"complex_model"',
    '"simulation_plan"',
    '"materials.xml"',
    '"geometry.xml"',
    '"settings.xml"',
    '"plot_specs"',
    '"capability_report"',
    '"execution_check"',
)

# Thresholds for full-lattice suspicion in pin_map output.
_FULL_LATTICE_COORD_THRESHOLD: int = 80
_FULL_LATTICE_RAW_CHARS_THRESHOLD: int = 3000


def _detect_full_plan_markers(raw: str) -> bool:
    """Check if raw output contains markers of a full SimulationPlan."""
    return any(marker in raw for marker in _FULL_PLAN_MARKERS)


def _detect_full_lattice(raw: str, patch_type: str) -> bool:
    """Check if pin_map raw output looks like a full expanded lattice."""
    if patch_type != "pin_map":
        return False
    if len(raw) > _FULL_LATTICE_RAW_CHARS_THRESHOLD:
        return True
    coord_count = raw.count("[") + raw.count("(")
    if coord_count > _FULL_LATTICE_COORD_THRESHOLD:
        return True
    return False


# ---------------------------------------------------------------------------
# Fake LLM for testing
# ---------------------------------------------------------------------------


class FakePatchLLM:
    """Simple fake LLM that returns pre-scripted responses.

    Each call to ``__call__`` pops the next response from the list.
    """

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            return '{"patch_type": "settings"}'
        return self.responses.pop(0)


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

# Matches a JSON object starting with { and ending with the matching }.
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def parse_llm_patch_json(raw_text: str, patch_type: str) -> dict[str, Any]:
    """Extract a JSON dict from raw LLM output.

    Handles markdown fences and surrounding text.  Returns the parsed dict.
    Raises ``PatchParseError`` if no valid JSON can be extracted.
    """
    if not raw_text or not raw_text.strip():
        raise PatchParseError(patch_type, "empty LLM response")

    text = raw_text.strip()

    # Strip markdown fences.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse first.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: extract the first {...} block.
    match = _JSON_OBJECT_RE.search(raw_text)
    if match:
        candidate = match.group(0)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        # Try stripping trailing commas (common LLM mistake).
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise PatchParseError(
        patch_type,
        f"could not extract valid JSON from LLM response",
        details=raw_text[:200],
    )


# ---------------------------------------------------------------------------
# Validation context adapter
# ---------------------------------------------------------------------------


def _to_validation_context(
    gen_context: PatchGenerationContext | None,
) -> PatchValidationContext:
    """Adapt a PatchGenerationContext to a PatchValidationContext."""
    if gen_context is None:
        return PatchValidationContext()
    return PatchValidationContext(
        benchmark_id=gen_context.benchmark_id,
        selected_variant=gen_context.selected_variant,
        expected_counts=gen_context.expected_counts,
        known_material_ids=gen_context.known_material_ids,
        known_universe_ids=gen_context.known_universe_ids,
        known_lattice_ids=gen_context.known_lattice_ids,
        axial_domain_cm=gen_context.axial_domain_cm,
        active_fuel_region_cm=gen_context.active_fuel_region_cm,
        strict_benchmark=gen_context.strict_benchmark,
    )


# ---------------------------------------------------------------------------
# Main generate_patch API
# ---------------------------------------------------------------------------


def generate_patch(
    *,
    patch_type: str,
    requirement: str,
    state: PlanBuildState | None = None,
    context: PatchGenerationContext | None = None,
    llm_client: Any | None = None,
    max_attempts: int = 2,
) -> PatchGenerationResult:
    """Generate a single patch via LLM with targeted retry.

    Parameters
    ----------
    patch_type
        One of the :data:`~openmc_agent.plan_builder.patches.PatchType` values.
    requirement
        The user/benchmark requirement text.
    state
        Optional build state for context (validated patch summaries).
    context
        Generation context with confirmed facts, expected counts, etc.
    llm_client
        A callable ``(prompt: str) -> str``.  If ``None``, returns an error
        result with ``patch_generation.no_llm_client``.
    max_attempts
        Maximum number of LLM calls (including the first).

    Returns
    -------
    PatchGenerationResult
    """
    if llm_client is None:
        return PatchGenerationResult(
            ok=False,
            patch_type=patch_type,
            issues=[{
                "code": "patch_generation.no_llm_client",
                "severity": "error",
                "message": "llm_client is None; cannot generate patch",
            }],
        )

    # Enrich context with validated patch summaries from state.
    effective_context = context or PatchGenerationContext()
    if state is not None:
        for env in state.patches.values():
            if env.status == "valid":
                effective_context.validated_patch_summaries.setdefault(
                    env.patch_type, {"status": "valid", "patch_id": env.patch_id}
                )

    val_context = _to_validation_context(effective_context)
    attempts: list[PatchGenerationAttempt] = []
    last_issues: list[dict[str, Any]] = []

    for attempt_idx in range(max_attempts):
        attempt = PatchGenerationAttempt(attempt_index=attempt_idx)

        # Build prompt.
        if attempt_idx == 0:
            prompt = build_patch_prompt(patch_type, requirement, effective_context)
        else:
            prompt = build_retry_prompt(
                patch_type, requirement, effective_context,
                last_issues, attempt_idx,
            )

        # Call LLM.
        try:
            raw = llm_client(prompt)
        except Exception as exc:
            attempt.error = str(exc)
            attempt.issues.append({
                "code": "patch_generation.llm_error",
                "severity": "error",
                "message": f"LLM client raised: {exc}",
            })
            attempts.append(attempt)
            last_issues = attempt.issues
            continue

        attempt.raw_text = raw
        attempt.raw_chars = len(raw)

        # Output diagnostics: detect forbidden patterns.
        attempt.contains_full_plan_markers = _detect_full_plan_markers(raw)
        if attempt.contains_full_plan_markers:
            attempt.issues.append({
                "code": "patch_generation.full_plan_markers_detected",
                "severity": "warning",
                "message": "raw output contains markers of a full SimulationPlan",
            })
        attempt.contains_full_lattice_suspected = _detect_full_lattice(raw, patch_type)
        if attempt.contains_full_lattice_suspected:
            attempt.issues.append({
                "code": "patch_generation.pin_map_full_lattice_detected",
                "severity": "warning",
                "message": "pin_map raw output appears to contain full lattice entries",
            })

        # Parse JSON.
        try:
            content = parse_llm_patch_json(raw, patch_type)
        except PatchParseError as exc:
            attempt.error = str(exc)
            attempt.issues.append({
                "code": "patch_generation.json_parse_error",
                "severity": "error",
                "message": str(exc),
            })
            attempts.append(attempt)
            last_issues = attempt.issues
            continue

        # Parse into patch model.
        try:
            parsed_model = parse_patch_content(patch_type, content)
        except PatchParseError as exc:
            attempt.error = str(exc)
            attempt.issues.append({
                "code": "patch_generation.schema_error",
                "severity": "error",
                "message": str(exc),
            })
            attempts.append(attempt)
            last_issues = attempt.issues
            continue

        attempt.parsed = True

        # Validate.
        val_result: PatchValidationResult = validate_patch(parsed_model, val_context)
        attempt.validated = val_result.ok
        attempt.issues = [i.model_dump(mode="json") for i in val_result.issues]
        attempts.append(attempt)

        if val_result.ok:
            # Success — build envelope and return.
            envelope = PlanPatchEnvelope(
                patch_id=f"patch_{patch_type}_{attempt_idx}",
                patch_type=patch_type,
                content=parsed_model.model_dump(mode="json"),
                source="llm",
                status="valid",
                issues=[i.model_dump(mode="json") for i in val_result.issues if i.severity != "info"],
            )
            return PatchGenerationResult(
                ok=True,
                patch_type=patch_type,
                envelope=envelope,
                parsed_patch=parsed_model.model_dump(mode="json"),
                validation=val_result.model_dump(mode="json"),
                attempts=attempts,
                issues=[i for a in attempts for i in a.issues],
            )

        # Validation failed — prepare retry issues.
        last_issues = [
            i.model_dump(mode="json")
            for i in val_result.issues
            if i.severity == "error"
        ]
        if not last_issues:
            last_issues = attempt.issues

    # Max attempts exceeded.
    all_issues: list[dict[str, Any]] = [i for a in attempts for i in a.issues]
    all_issues.append({
        "code": "patch_generation.max_attempts_exceeded",
        "severity": "error",
        "message": f"{patch_type} generation failed after {len(attempts)} attempt(s)",
    })
    return PatchGenerationResult(
        ok=False,
        patch_type=patch_type,
        attempts=attempts,
        issues=all_issues,
    )


__all__ = [
    "PatchGenerationContext",
    "PatchGenerationAttempt",
    "PatchGenerationResult",
    "FakePatchLLM",
    "generate_patch",
    "parse_llm_patch_json",
]
