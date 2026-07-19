"""Phase 8C Step 0 — Facts capability truth matrix.

This module pins the truth-matrix from
``docs/phase8c_step0_facts_truth_audit.md`` as an executable test, so the
project cannot quietly regress on any of the audit findings.

Each row asserts the *currently-true* state of one capability.  When a
later Phase 8C step delivers the capability, the assertion is flipped to
positive.  This keeps the audit honest: a reader can run this test and
see exactly which defects remain.
"""

from __future__ import annotations

import importlib
import inspect

from openmc_agent.plan_builder.patches import FactsPatch
from openmc_agent.plan_investigation import campaign_truthfulness as ct


# ---------------------------------------------------------------------------
# Step 0 deliverables (now true)
# ---------------------------------------------------------------------------


def test_truth_matrix_facts_schema_default_is_unknown():
    """Row: 'Neutral model_scope default' — delivered in Step 0."""
    assert FactsPatch().model_scope == "unknown"


def test_truth_matrix_facts_boolean_defaults_are_none():
    """Row: 'Boolean feature flags observable' — delivered in Step 0."""
    facts = FactsPatch()
    assert facts.has_axial_geometry is None
    assert facts.has_spacer_grids is None
    assert facts.has_special_pin_map is None


def test_truth_matrix_truthfulness_codes_registered():
    """Row: 'Truth-violation codes registered' — delivered in Step 0.

    Every Phase 8C code must appear in ``INVESTIGATION_TRUTH_VIOLATIONS`` so
    auditors can detect regressions.
    """
    required = {
        ct.TV_FACTS_DEFAULT_SCOPE_CONTAMINATION,
        ct.TV_FACTS_DEFAULT_VALUE_RENDERED_AS_AUTHORITATIVE,
        ct.TV_FACTS_UNKNOWN_CONTEXT_RENDERED,
        ct.TV_FACTS_INVESTIGATION_COMPLETED_WITHOUT_MANDATORY_COVERAGE,
        ct.TV_FACTS_MANDATORY_TARGET_DROPPED,
        ct.TV_FACTS_CONTRACT_MISSING,
        ct.TV_FACTS_CONTRACT_HASH_MISMATCH,
        ct.TV_FACTS_LOCKED_SLOT_MODIFIED,
        ct.TV_FACTS_REQUIRED_SLOT_DROPPED,
        ct.TV_FACTS_FUEL_VARIANT_DROPPED,
        ct.TV_FACTS_LOCALIZED_INSERT_DROPPED,
        ct.TV_FACTS_SCOPE_DOWNGRADED,
        ct.TV_FACTS_FEATURE_FLAG_DISABLED,
        ct.TV_FACTS_UNRESOLVED_VALUE_FABRICATED,
        ct.TV_FACTS_CONFLICT_SILENTLY_RESOLVED,
        ct.TV_FACTS_RETRY_REGISTERED_BUT_NOT_EXECUTED,
        ct.TV_FACTS_SPECIAL_ROUTE_NOT_EXECUTED,
        ct.TV_FACTS_CANDIDATE_VALIDATED_ON_LIVE_STATE,
        ct.TV_FACTS_COMMIT_WITHOUT_REVIEWER_REPLAY,
        ct.TV_FACTS_GATE_REOPENED_WITHOUT_HASH_CHANGE,
        ct.TV_FACTS_REVIEWER_OUTPUT_REUSED,
        ct.TV_FACTS_DOWNSTREAM_CONTRACT_NOT_RECOMPILED,
        ct.TV_FACTS_NO_PROGRESS_LOOP_CONTINUED,
        ct.TV_FACTS_REAL_CANARY_CLAIM_WITHOUT_ACCEPTANCE,
    }
    actual = set(ct.INVESTIGATION_TRUTH_VIOLATIONS)
    missing = required - actual
    assert not missing, f"Missing truth codes: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Step 0 documentation gaps (now closed by the audit doc)
# ---------------------------------------------------------------------------


def test_truth_audit_document_exists():
    """Row: 'Audit document produced' — delivered in Step 0."""
    import pathlib

    path = pathlib.Path("docs/phase8c_step0_facts_truth_audit.md")
    assert path.exists(), "Facts truth audit document must exist at Step 0"
    text = path.read_text(encoding="utf-8")
    # The audit must answer every mandatory question.
    for needle in (
        "model_scope",
        "accepted_input_hash",
        "semantic",
        "no-progress",
        "BYPASS_PATH",
        "DUPLICATED_PATH",
        "REAL_CANARY",
    ):
        assert needle in text, f"Audit doc missing required section: {needle}"


# ---------------------------------------------------------------------------
# Reactor-neutral enforcement (production code must not special-case VERA4)
# ---------------------------------------------------------------------------


def test_production_code_has_no_vera4_special_case():
    """Row: 'Production code is reactor-neutral' — must always pass.

    Scans the planning pipeline source tree for any literal 'VERA4',
    'vera4', or related identifiers that would indicate a benchmark-specific
    branch in the production logic.  Test fixtures, scripts, reports, the
    real-canary harness, and reactor-neutral docstring/comment mentions
    are excluded.
    """
    import pathlib

    bad_substrings = ("vera4", "VERA4", "Vera4")
    # These files/dirs are allowed to mention VERA4 (tests, fixtures,
    # reports, scripts, docs, canary harness, campaign evaluation).
    allowed_path_fragments = (
        "/tests/",
        "/docs/",
        "/scripts/",
        "/data/",
        "/fixtures/",
        "test_",
        "phase8",
        "canary",
        "benchmark_cases",
        "real_campaign_harness",
        "campaign_eval",
    )
    root = pathlib.Path("openmc_agent")
    violations: list[str] = []
    for path in root.rglob("*.py"):
        rel = str(path)
        if any(fragment in rel for fragment in allowed_path_fragments):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in bad_substrings:
            if needle in text:
                # Allow the needle to appear inside docstrings or comments
                # that document the absence of the special case.
                lines = text.splitlines()
                bad_lines = []
                for ln in lines:
                    if needle not in ln:
                        continue
                    stripped = ln.strip()
                    is_comment_or_docstring = (
                        stripped.startswith("#")
                        or stripped.startswith('"""')
                        or stripped.startswith("'''")
                        or stripped.startswith('*')
                        or stripped.startswith('"""')
                    )
                    lower = stripped.lower()
                    explains_neutrality = (
                        "reactor-neutral" in lower
                        or "no vera4" in lower
                        or "not hardcoded" in lower
                        or "must not" in lower
                        or "e.g. vera4" in lower
                        or "observed: a vera4" in lower
                        or "vera4-specific" in lower
                        or "vera3/vera4" in lower
                    )
                    if is_comment_or_docstring or explains_neutrality:
                        continue
                    bad_lines.append(stripped[:120])
                if bad_lines:
                    violations.append(f"{rel}: {bad_lines[0]}")
    assert not violations, (
        "Production code contains VERA4-specific branches (reactor-neutral "
        "violation):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Known-pending capabilities — these assertions document the current
# negative state and will flip to positive in Step 1 / Step 2.
# ---------------------------------------------------------------------------


def test_truth_matrix_facts_gate_now_saves_accepted_input_hash():
    """Row: 'accepted_input_hash replay protection' — POSITIVE in Step 1.

    Phase 8C Step 1: the Facts gate now saves ``accepted_input_hash`` on
    ACCEPTED and short-circuits when the input hash is unchanged.  This
    flipped from negative to positive when the gate transaction kernel
    was wired into ``_run_facts_gate``.
    """
    from openmc_agent.plan_builder import executor as executor_mod
    from openmc_agent.plan_builder.closed_loop.facts_evidence import (
        facts_gate_input_hash,
    )

    # The hash helper exists.
    assert callable(facts_gate_input_hash)
    # The gate body now saves the hash.
    src = inspect.getsource(executor_mod)
    start = src.index("    def _run_facts_gate(")
    end = src.find("\n    def ", start + 1)
    body = src[start:end]
    assert 'stage.metadata["accepted_input_hash"]' in body, (
        "Facts Gate must save accepted_input_hash on accept."
    )
    assert "facts_gate_input_hash(state" in body, (
        "Facts Gate must compute the input hash for replay."
    )


def test_truth_matrix_targeted_facts_repair_not_in_production_call_chain():
    """Row: 'Skeleton-driven targeted repair in production' — NEGATIVE in
    Step 0, positive in Step 2.
    """
    # The function exists but has no production caller (only tests).
    from openmc_agent.plan_builder.closed_loop import facts_revision

    assert hasattr(facts_revision, "targeted_facts_repair"), (
        "targeted_facts_repair must exist (introduced in Phase 8B Step 2)"
    )
    # Confirm the executor module does NOT call it yet.
    from openmc_agent.plan_builder import executor as executor_mod

    src = inspect.getsource(executor_mod)
    assert "targeted_facts_repair" not in src, (
        "targeted_facts_repair is now wired into executor — flip this "
        "assertion to positive in Step 2."
    )


def test_truth_matrix_clone_validation_not_in_production_call_chain():
    """Row: 'Clone validation in production' — NEGATIVE in Step 0,
    positive in Step 1.
    """
    from openmc_agent.plan_builder.closed_loop import facts_revision

    assert hasattr(facts_revision, "run_clone_validation")
    from openmc_agent.plan_builder import executor as executor_mod

    src = inspect.getsource(executor_mod)
    assert "run_clone_validation" not in src, (
        "run_clone_validation is now wired into executor — flip this "
        "assertion to positive in Step 1."
    )


def test_truth_matrix_skeleton_now_mines_evidence_ledger():
    """Row: 'Skeleton compiler mines evidence_ledger.claims for values' —
    POSITIVE in Step 2.

    Phase 8C Step 2: the skeleton compiler now iterates
    ``evidence_ledger.claims`` to populate slot values via subject and
    ``required_by_json_paths`` matching.  This flipped from negative to
    positive when ``compile_facts_requirement_skeleton`` was upgraded.
    """
    from openmc_agent.plan_builder import facts_requirement_skeleton as fs

    src = inspect.getsource(fs.compile_facts_requirement_skeleton)
    assert "_select_best_claim" in src, (
        "Skeleton compiler must call _select_best_claim to mine evidence ledger"
    )
    assert "claims" in src, (
        "Skeleton compiler must iterate evidence_ledger.claims"
    )


def test_truth_matrix_gate_transaction_kernel_now_introduced():
    """Row: 'Gate transaction kernel' — POSITIVE in Step 1.

    The kernel module exists and exposes ``run_gate_transaction``.  This
    assertion flipped from negative to positive when Phase 8C Step 1
    introduced the kernel.
    """
    import importlib

    mod = importlib.import_module(
        "openmc_agent.plan_builder.closed_loop.gate_transaction"
    )
    assert hasattr(mod, "run_gate_transaction")
    assert hasattr(mod, "GateTransactionHooks")
    assert hasattr(mod, "GateTransactionResult")
    assert hasattr(mod, "GateFindingBundle")
