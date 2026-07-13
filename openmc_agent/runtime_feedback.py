"""Structured runtime failure classification for OpenMC tool results.

This module provides a reactor-agnostic runtime feedback contract. It converts
raw :class:`~openmc_agent.tools.ToolResult` outputs into structured
:class:`RuntimeFailure` records with a stable classification, owner patch-type
hints, and a deterministic error fingerprint.

Design rules
------------
* **Source rejection dominates crash noise.** When OpenMC rejects too many
  source sites, downstream segfault / MPI-abort lines are downstream noise and
  must not become the primary issue.
* **Cross-section environment errors are never plan-repairable.** They classify
  as ``environment``.
* **Overlap / lost-particle are geometry plan-fixable**, but the owner patch
  type stays a candidate set (e.g. ``cells``, ``regions``, ``surfaces``) until a
  deterministic repair can narrow it.
* **Missing nuclide data** is ``human_fact`` when the catalog route is
  ``ask_expert``, or ``materials`` when it can be fixed in the plan.
* **Unknown errors are never auto-marked safe-repairable.**
* **Fingerprint is stable across run directories.** Timestamps, PIDs, absolute
  temporary paths, and hex addresses are stripped before hashing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openmc_agent.tools import ToolResult
from openmc_agent.workflow_trace import summarize_issues


class RuntimeFailureClass(str, Enum):
    PLAN_FIXABLE = "plan_fixable"
    ENVIRONMENT = "environment"
    HUMAN_FACT = "human_fact"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Classification metadata per runtime issue code.
# --------------------------------------------------------------------------- #

# The precedence order is fixed: a higher-priority code shadows a lower one
# as the *primary* issue. This list must stay aligned with
# ``error_catalog.py`` runtime entries.
_CLASSIFICATION_PRECEDENCE: list[tuple[str, RuntimeFailureClass, list[str], bool]] = [
    # (issue_code, classification, owner_patch_types, environment_only)
    # --- environment (highest priority: cannot be plan-fixed) ---
    (
        "runtime.cross_sections_missing",
        RuntimeFailureClass.ENVIRONMENT,
        [],
        True,
    ),
    (
        "runtime.cross_sections_invalid",
        RuntimeFailureClass.ENVIRONMENT,
        [],
        True,
    ),
    # --- source rejection: primary root cause over downstream crash noise ---
    (
        "runtime.openmc_source_rejection_failure",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["settings.source"],
        False,
    ),
    # --- source pre-flight issues: same classification as source rejection ---
    (
        "runtime.source_not_in_active_fuel_region",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["settings.source"],
        False,
    ),
    (
        "runtime.source_default_z_extent",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["settings.source"],
        False,
    ),
    (
        "runtime.manual_source_bounds_missing",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["settings.source"],
        False,
    ),
    (
        "runtime.unknown_source_strategy",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["settings.source"],
        False,
    ),
    (
        "runtime.source_strategy_not_rendered",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["settings.source"],
        False,
    ),
    # --- geometry plan-fixable ---
    (
        "runtime.geometry_overlap",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["cells", "regions", "surfaces"],
        False,
    ),
    (
        "runtime.lost_particle",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["cells", "regions", "surfaces"],
        False,
    ),
    # --- material nuclide data: depends on catalog route ---
    (
        "runtime.material_missing_nuclide_data",
        RuntimeFailureClass.HUMAN_FACT,
        ["materials"],
        False,
    ),
    # --- transient / crash ---
    (
        "runtime.openmc_timeout",
        RuntimeFailureClass.TRANSIENT,
        [],
        False,
    ),
    (
        "runtime.openmc_process_crash",
        RuntimeFailureClass.TRANSIENT,
        [],
        False,
    ),
    # --- geometry load failure ---
    (
        "runtime.dagmc_or_geometry_load_failed",
        RuntimeFailureClass.PLAN_FIXABLE,
        ["cells", "lattices"],
        False,
    ),
    # --- unknown (lowest priority) ---
    (
        "runtime.openmc_unknown_error",
        RuntimeFailureClass.UNKNOWN,
        [],
        False,
    ),
]

_PRECEDENCE_MAP: dict[str, tuple[int, RuntimeFailureClass, list[str], bool]] = {
    entry[0]: (idx, entry[1], entry[2], entry[3])
    for idx, entry in enumerate(_CLASSIFICATION_PRECEDENCE)
}

# Codes that are environment-only: even if they appear alongside a
# plan-fixable code, the environment classification takes priority.
_ENVIRONMENT_CODES = {
    code
    for code, (_, cls, _, env) in _PRECEDENCE_MAP.items()
    if cls is RuntimeFailureClass.ENVIRONMENT or env
}


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class RuntimeFailure:
    """A single classified runtime failure extracted from tool results."""

    failure_id: str
    stage: str
    tool_name: str
    returncode: int | None
    primary_issue_code: str
    secondary_issue_codes: list[str]
    normalized_message: str
    raw_error_excerpt: str
    error_fingerprint: str
    plan_hash: str | None = None
    artifact_paths: list[str] = field(default_factory=list)
    classification: RuntimeFailureClass = RuntimeFailureClass.UNKNOWN
    owner_patch_types: list[str] = field(default_factory=list)
    requires_human_confirmation: bool = False
    environment_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "stage": self.stage,
            "tool_name": self.tool_name,
            "returncode": self.returncode,
            "primary_issue_code": self.primary_issue_code,
            "secondary_issue_codes": list(self.secondary_issue_codes),
            "normalized_message": self.normalized_message,
            "raw_error_excerpt": self.raw_error_excerpt,
            "error_fingerprint": self.error_fingerprint,
            "plan_hash": self.plan_hash,
            "artifact_paths": list(self.artifact_paths),
            "classification": self.classification.value,
            "owner_patch_types": list(self.owner_patch_types),
            "requires_human_confirmation": self.requires_human_confirmation,
            "environment_only": self.environment_only,
            "metadata": dict(self.metadata),
        }


@dataclass
class RuntimeIterationRecord:
    """Compact record of one repair iteration attempt."""

    iteration: int
    plan_hash_before: str | None
    plan_hash_after: str | None
    tool_stage: str
    issue_fingerprint: str
    attempted_action: str
    target_patch_type: str | None
    changed_paths: list[str]
    result: str
    elapsed_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "plan_hash_before": self.plan_hash_before,
            "plan_hash_after": self.plan_hash_after,
            "tool_stage": self.tool_stage,
            "issue_fingerprint": self.issue_fingerprint,
            "attempted_action": self.attempted_action,
            "target_patch_type": self.target_patch_type,
            "changed_paths": list(self.changed_paths),
            "result": self.result,
            "elapsed_ms": self.elapsed_ms,
        }


# --------------------------------------------------------------------------- #
# Error normalization and fingerprinting
# --------------------------------------------------------------------------- #

# Patterns removed during normalization so that identical root causes produce
# identical fingerprints regardless of the run directory or wall-clock time.
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b",
    re.IGNORECASE,
)
_PID_RE = re.compile(r"\b(?:PID|pid|process id)\s*[:=]?\s*\d+\b")
_HEX_ADDR_RE = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")
_ABS_PATH_RE = re.compile(r"/(?:tmp|var|home|Users|opt|app|data|workspace)[^\s'\"<>]+")
_LINE_NUM_RE = re.compile(r":\d{2,}:")  # file.cpp:12345: → file.cpp:
_RUN_DIR_RE = re.compile(r"(?:run|runs|out|output)[-_]?\d*", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"[ \t]+")


def normalize_runtime_error(text: str) -> str:
    """Strip volatile tokens (timestamps, PIDs, paths, addresses) from *text*.

    The result is used as the fingerprint base and as the stable
    ``normalized_message`` stored on :class:`RuntimeFailure`.
    """
    if not text:
        return ""
    result = text
    result = _TIMESTAMP_RE.sub("<ts>", result)
    result = _PID_RE.sub("<pid>", result)
    result = _HEX_ADDR_RE.sub("<addr>", result)
    result = _ABS_PATH_RE.sub("<path>", result)
    result = _LINE_NUM_RE.sub(":<line>:", result)
    result = _RUN_DIR_RE.sub("<rundir>", result)
    result = _WHITESPACE_RE.sub(" ", result)
    # Collapse repeated placeholder runs.
    result = re.sub(r"(<rundir>)+", "<rundir>", result)
    result = re.sub(r"\s+", " ", result)
    return result.strip()


def compute_runtime_error_fingerprint(normalized_text: str) -> str:
    """Return a stable ``rt_<sha256[:16]>`` fingerprint for *normalized_text*."""
    if not normalized_text:
        return "rt_empty"
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()[:16]
    return f"rt_{digest}"


# --------------------------------------------------------------------------- #
# Classification engine
# --------------------------------------------------------------------------- #

# Issue codes that require human confirmation per catalog policy.
_HUMAN_CONFIRMATION_CODES = {
    "runtime.cross_sections_missing",
    "runtime.cross_sections_invalid",
    "runtime.material_missing_nuclide_data",
    "runtime.openmc_unknown_error",
    "runtime.openmc_timeout",
    "runtime.openmc_process_crash",
}


def _classify_issue_codes(
    issue_codes: list[str],
) -> tuple[str | None, list[str], RuntimeFailureClass, list[str], bool, bool]:
    """Pick the primary issue code by precedence.

    Returns ``(primary, secondaries, classification, owner_patch_types,
    requires_human_confirmation, environment_only)``.
    """
    if not issue_codes:
        return None, [], RuntimeFailureClass.UNKNOWN, [], False, False

    # Deduplicate preserving first-seen order.
    seen: list[str] = []
    for code in issue_codes:
        if code not in seen:
            seen.append(code)

    # Environment codes always dominate.
    env_present = [c for c in seen if c in _ENVIRONMENT_CODES]

    # Sort by precedence index; unknown codes get the lowest priority.
    def _precedence_key(code: str) -> int:
        return _PRECEDENCE_MAP.get(code, (len(_CLASSIFICATION_PRECEDENCE),))[0]

    if env_present:
        primary = sorted(env_present, key=_precedence_key)[0]
    else:
        primary = sorted(seen, key=_precedence_key)[0]

    secondaries = [c for c in seen if c != primary]
    _, classification, owner, env_only = _PRECEDENCE_MAP.get(
        primary,
        (len(_CLASSIFICATION_PRECEDENCE), RuntimeFailureClass.UNKNOWN, [], False),
    )
    requires_human = primary in _HUMAN_CONFIRMATION_CODES
    return primary, secondaries, classification, owner, requires_human, env_only


def _extract_error_excerpt(tool_result: ToolResult, max_chars: int = 400) -> str:
    """Pull the first meaningful error line from the tool result output."""
    text = tool_result.error or tool_result.stderr or tool_result.stdout
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(
            token in lowered
            for token in ("error", "fatal", "overlap", "lost particle", "traceback", "timeout")
        ):
            return stripped[:max_chars]
    return text.strip()[:max_chars]


def classify_runtime_tool_results(
    tool_results: list[ToolResult],
    *,
    plan_hash: str | None = None,
    stage: str = "execute_tools",
) -> list[RuntimeFailure]:
    """Classify all failed tool results into structured :class:`RuntimeFailure`.

    Only tool results with ``ok=False`` and at least one issue code (or a
    non-empty error) produce a ``RuntimeFailure``.
    """
    failures: list[RuntimeFailure] = []
    for idx, result in enumerate(tool_results):
        if result.ok:
            continue
        issue_summary = summarize_issues(result.issues)
        issue_codes: list[str] = issue_summary.get("issue_codes", [])  # type: ignore[assignment]
        # If no structured issues but a clear error, fall back to unknown.
        if not issue_codes and not result.error:
            continue

        primary, secondaries, classification, owner, requires_human, env_only = (
            _classify_issue_codes(issue_codes)
        )
        if primary is None:
            primary = "runtime.openmc_unknown_error"
            classification = RuntimeFailureClass.UNKNOWN
            requires_human = True

        raw_excerpt = _extract_error_excerpt(result)
        normalized = normalize_runtime_error(raw_excerpt)
        fingerprint = compute_runtime_error_fingerprint(normalized)

        failure = RuntimeFailure(
            failure_id=f"rf_{idx:03d}_{fingerprint}",
            stage=stage,
            tool_name=result.name,
            returncode=result.returncode,
            primary_issue_code=primary,
            secondary_issue_codes=secondaries,
            normalized_message=normalized,
            raw_error_excerpt=raw_excerpt,
            error_fingerprint=fingerprint,
            plan_hash=plan_hash,
            artifact_paths=list(result.artifacts),
            classification=classification,
            owner_patch_types=owner,
            requires_human_confirmation=requires_human,
            environment_only=env_only,
            metadata={
                "command": list(result.command),
                "issue_count": len(result.issues),
            },
        )
        failures.append(failure)
    return failures
