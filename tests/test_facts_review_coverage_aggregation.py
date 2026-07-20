"""Phase 8B Step 4B-1 — Facts review coverage aggregation tests.

Covers:

* ``insufficient_evidence`` normalises to ``complete_with_gaps`` and does NOT
  block the gate when findings are only warnings.
* Unknown ``review_status`` values fail closed.
* A single stage with an ``error``-severity finding blocks the gate.
* All stages ``complete`` with only ``warning`` findings passes.
* Raw outputs captured at the provider boundary are non-empty (P1 telemetry fix).
* ``_aggregate_coverage`` unit tests for direct logic validation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_builder.closed_loop.facts_evidence import build_facts_evidence_packs
from openmc_agent.plan_builder.closed_loop.facts_reviewer import (
    _aggregate_coverage,
    run_facts_review,
)
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.review_io import (
    _ACCEPTED_REVIEW_STATUSES,
    _REVIEW_STATUS_FIXES,
    normalize_llm_review_candidate,
)
from openmc_agent.plan_builder.closed_loop.facts_review_stages import (
    FactsReviewStage,
    STAGE_ORDER,
)
from openmc_agent.plan_builder.state import PlanBuildState


# ---------------------------------------------------------------------------
# _aggregate_coverage unit tests
# ---------------------------------------------------------------------------


class TestAggregateCoverage:
    """Direct tests for the _aggregate_coverage helper."""

    def test_all_complete_no_findings_passes(self):
        outputs = [
            {"output": {"review_status": "complete", "findings": []}}
            for _ in range(6)
        ]
        assert _aggregate_coverage(outputs, expected_stage_count=6)

    def test_all_complete_with_gaps_with_warnings_passes(self):
        outputs = [
            {"output": {"review_status": "complete_with_gaps", "findings": [
                {"severity": "warning"},
            ]}}
            for _ in range(6)
        ]
        assert _aggregate_coverage(outputs, expected_stage_count=6)

    def test_mixed_complete_and_with_gaps_passes(self):
        outputs = [
            {"output": {"review_status": "complete", "findings": []}},
            {"output": {"review_status": "complete_with_gaps", "findings": [
                {"severity": "warning"},
            ]}},
        ]
        assert _aggregate_coverage(outputs, expected_stage_count=2)

    def test_error_finding_blocks(self):
        outputs = [
            {"output": {"review_status": "complete", "findings": []}},
            {"output": {"review_status": "complete", "findings": [
                {"severity": "error"},
            ]}},
        ]
        assert not _aggregate_coverage(outputs, expected_stage_count=2)

    def test_single_error_finding_blocks_even_if_others_ok(self):
        outputs = [
            {"output": {"review_status": "complete", "findings": []}}
            for _ in range(5)
        ] + [
            {"output": {"review_status": "complete", "findings": [
                {"severity": "error"},
            ]}},
        ]
        assert not _aggregate_coverage(outputs, expected_stage_count=6)

    def test_unknown_status_blocks(self):
        outputs = [
            {"output": {"review_status": "unknown_status", "findings": []}}
        ]
        assert not _aggregate_coverage(outputs, expected_stage_count=1)

    def test_empty_outputs_blocks(self):
        assert not _aggregate_coverage([], expected_stage_count=6)

    def test_wrong_stage_count_blocks(self):
        outputs = [
            {"output": {"review_status": "complete", "findings": []}}
            for _ in range(3)
        ]
        assert not _aggregate_coverage(outputs, expected_stage_count=6)

    def test_missing_review_status_blocks(self):
        outputs = [{"output": {"findings": []}}]
        assert not _aggregate_coverage(outputs, expected_stage_count=1)

    def test_empty_review_status_blocks(self):
        outputs = [{"output": {"review_status": "", "findings": []}}]
        assert not _aggregate_coverage(outputs, expected_stage_count=1)

    def test_none_expected_stage_count_skips_count_check(self):
        outputs = [
            {"output": {"review_status": "complete", "findings": []}}
        ]
        assert _aggregate_coverage(outputs, expected_stage_count=None)


# ---------------------------------------------------------------------------
# Normaliser mapping tests
# ---------------------------------------------------------------------------


class TestReviewStatusNormalisation:
    """Verify _REVIEW_STATUS_FIXES and _ACCEPTED_REVIEW_STATUSES."""

    def test_insufficient_evidence_maps_to_complete_with_gaps(self):
        assert _REVIEW_STATUS_FIXES["insufficient_evidence"] == "complete_with_gaps"

    def test_completed_maps_to_complete(self):
        assert _REVIEW_STATUS_FIXES["completed"] == "complete"

    def test_reviewed_maps_to_complete(self):
        assert _REVIEW_STATUS_FIXES["reviewed"] == "complete"

    def test_accepted_statuses_contains_both(self):
        assert "complete" in _ACCEPTED_REVIEW_STATUSES
        assert "complete_with_gaps" in _ACCEPTED_REVIEW_STATUSES

    def test_insufficient_evidence_via_normalizer(self):
        from openmc_agent.plan_builder.closed_loop.models import FactsReviewModelOutput
        candidate = {
            "review_status": "insufficient_evidence",
            "reviewed_evidence_hashes": [],
            "coverage_summary": {},
            "findings": [],
        }
        normalized = normalize_llm_review_candidate(candidate, FactsReviewModelOutput)
        assert normalized["review_status"] == "complete_with_gaps"

    def test_unknown_status_preserved_by_normalizer(self):
        from openmc_agent.plan_builder.closed_loop.models import FactsReviewModelOutput
        candidate = {
            "review_status": "something_bizarre",
            "reviewed_evidence_hashes": [],
            "coverage_summary": {},
            "findings": [],
        }
        normalized = normalize_llm_review_candidate(candidate, FactsReviewModelOutput)
        assert normalized["review_status"] == "something_bizarre"
        assert normalized["review_status"] not in _ACCEPTED_REVIEW_STATUSES


# ---------------------------------------------------------------------------
# End-to-end run_facts_review tests (non-staged path)
# ---------------------------------------------------------------------------


def _make_policy(staged: bool = False) -> PlanClosedLoopPolicy:
    policy = PlanClosedLoopPolicy()
    policy.facts_review_stage_split = staged
    return policy


def _make_packs() -> list:
    policy = PlanClosedLoopPolicy()
    return build_facts_evidence_packs(
        requirement_text="A 17x17 PWR assembly.\n",
        facts_patch={"patch_type": "facts"},
        confirmed_facts={},
        planning_metadata={},
        policy=policy,
    )


class _ScriptedClient:
    """Returns pre-built JSON payloads in sequence."""

    def __init__(self, payloads: list[str]):
        self._payloads = list(payloads)
        self._index = 0

    def __call__(self, _prompt: str) -> str:
        if self._index >= len(self._payloads):
            return self._payloads[-1]
        result = self._payloads[self._index]
        self._index += 1
        return result


def _make_complete_payload(pack) -> str:
    evidence = pack.source_excerpts[0].evidence_hash
    return json.dumps({
        "review_status": "complete",
        "reviewed_evidence_hashes": [evidence],
        "coverage_summary": {},
        "findings": [],
    })


def _make_warning_payload(pack) -> str:
    evidence = pack.source_excerpts[0].evidence_hash
    return json.dumps({
        "review_status": "insufficient_evidence",
        "reviewed_evidence_hashes": [evidence],
        "coverage_summary": {},
        "findings": [
            {
                "code": "WARN_TEST",
                "severity": "warning",
                "category": "source_coverage",
                "message": "minor gap",
                "evidence_hashes": [evidence],
                "affected_json_paths": ["/model_scope"],
                "repairable_by_llm": True,
                "requires_human": False,
                "confidence": 0.5,
            },
        ],
    })


def _make_error_payload(pack) -> str:
    evidence = pack.source_excerpts[0].evidence_hash
    return json.dumps({
        "review_status": "complete",
        "reviewed_evidence_hashes": [evidence],
        "coverage_summary": {},
        "findings": [
            {
                "code": "ERR_TEST",
                "severity": "error",
                "category": "physical_ambiguity",
                "message": "blocking issue",
                "evidence_hashes": [evidence],
                "affected_json_paths": ["/model_scope"],
                "repairable_by_llm": True,
                "requires_human": False,
                "confidence": 0.9,
            },
        ],
    })


def _make_unknown_status_payload(pack) -> str:
    evidence = pack.source_excerpts[0].evidence_hash
    return json.dumps({
        "review_status": "something_unknown",
        "reviewed_evidence_hashes": [evidence],
        "coverage_summary": {},
        "findings": [],
    })


class TestRunFactsReviewNonStaged:
    """Non-staged run_facts_review coverage decisions."""

    def test_complete_passes(self):
        packs = _make_packs()
        client = _ScriptedClient([_make_complete_payload(packs[0])])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        assert result.ok
        assert result.coverage_complete
        assert result.failure_code == ""

    def test_insufficient_evidence_with_warning_passes(self):
        """complete_with_gaps + only warning findings → gate passes."""
        packs = _make_packs()
        client = _ScriptedClient([_make_warning_payload(packs[0])])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        assert result.ok
        assert result.coverage_complete
        assert result.failure_code == ""

    def test_error_finding_blocks(self):
        """error-severity finding → coverage incomplete."""
        packs = _make_packs()
        client = _ScriptedClient([_make_error_payload(packs[0])])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        assert result.ok
        assert not result.coverage_complete
        assert result.failure_code == "facts_review.coverage_incomplete"

    def test_unknown_status_blocks(self):
        """Unknown review_status → schema rejects (fail closed)."""
        packs = _make_packs()
        client = _ScriptedClient([_make_unknown_status_payload(packs[0])])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        # Unknown status is rejected by the Literal at schema level.
        assert not result.ok
        assert not result.coverage_complete

    def test_source_too_large_blocks_at_aggregation(self):
        """source_too_large is valid Literal but not in accepted statuses."""
        packs = _make_packs()
        evidence = packs[0].source_excerpts[0].evidence_hash
        payload = json.dumps({
            "review_status": "source_too_large",
            "reviewed_evidence_hashes": [evidence],
            "coverage_summary": {},
            "findings": [],
        })
        client = _ScriptedClient([payload])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        assert result.ok  # schema accepted it
        assert not result.coverage_complete  # but aggregation rejected it
        assert result.failure_code == "facts_review.coverage_incomplete"


# ---------------------------------------------------------------------------
# P1: Raw output capture at provider boundary
# ---------------------------------------------------------------------------


class TestRawOutputCapture:
    """Verify that raw_outputs are captured at the provider boundary (P1)."""

    def test_raw_outputs_non_empty_on_success(self):
        """After a successful call, raw_outputs should contain the LLM response."""
        packs = _make_packs()
        payload = _make_complete_payload(packs[0])
        client = _ScriptedClient([payload])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        assert len(result.raw_outputs) >= 1
        assert result.raw_outputs[0] == payload

    def test_raw_outputs_non_empty_on_warning_payload(self):
        """Raw outputs captured even when review_status is insufficient_evidence."""
        packs = _make_packs()
        payload = _make_warning_payload(packs[0])
        client = _ScriptedClient([payload])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        assert len(result.raw_outputs) >= 1
        assert result.raw_outputs[0] == payload

    def test_raw_outputs_non_empty_on_parse_failure(self):
        """Raw outputs captured even when the call fails to parse."""
        packs = _make_packs()
        # Invalid JSON that will fail schema validation.
        client = _ScriptedClient(["{this is not valid json"])
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=_make_policy(staged=False),
        )
        # Even on failure, raw_outputs should have content from the provider.
        assert len(result.raw_outputs) >= 1
        assert result.raw_outputs[0]


class TestStagedCoverageAggregation:
    """Staged-mode coverage aggregation with the new logic."""

    @pytest.fixture
    def staged_policy(self):
        policy = PlanClosedLoopPolicy()
        policy.facts_review_stage_split = True
        policy.max_total_additional_llm_calls = 50
        return policy

    def test_all_stages_complete_with_gaps_and_warnings_passes(self, staged_policy):
        """All 6 stages return complete_with_gaps + warning → passes."""
        packs = _make_packs()
        evidence = packs[0].source_excerpts[0].evidence_hash
        payload = json.dumps({
            "review_status": "insufficient_evidence",
            "reviewed_evidence_hashes": [evidence],
            "coverage_summary": {},
            "findings": [
                {
                    "code": "MINOR_WARN",
                    "severity": "warning",
                    "category": "source_coverage",
                    "message": "minor",
                    "evidence_hashes": [evidence],
                    "affected_json_paths": ["/model_scope"],
                    "repairable_by_llm": True,
                    "requires_human": False,
                    "confidence": 0.3,
                },
            ],
        })
        client = _ScriptedClient([payload] * len(STAGE_ORDER))
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=staged_policy,
        )
        assert result.ok
        assert result.coverage_complete
        assert result.failure_code == ""

    def test_one_stage_error_blocks(self, staged_policy):
        """5 stages complete + 1 stage error-finding → blocked."""
        packs = _make_packs()
        evidence = packs[0].source_excerpts[0].evidence_hash
        ok_payload = json.dumps({
            "review_status": "complete",
            "reviewed_evidence_hashes": [evidence],
            "coverage_summary": {},
            "findings": [],
        })
        err_payload = json.dumps({
            "review_status": "complete",
            "reviewed_evidence_hashes": [evidence],
            "coverage_summary": {},
            "findings": [
                {
                    "code": "BLOCKING",
                    "severity": "error",
                    "category": "physical_ambiguity",
                    "message": "blocking",
                    "evidence_hashes": [evidence],
                    "affected_json_paths": ["/model_scope"],
                    "repairable_by_llm": True,
                    "requires_human": False,
                    "confidence": 0.9,
                },
            ],
        })
        # 5 ok + 1 error in the middle
        payloads = [ok_payload] * 3 + [err_payload] + [ok_payload] * 2
        client = _ScriptedClient(payloads)
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=staged_policy,
        )
        assert result.ok
        assert not result.coverage_complete
        assert result.failure_code == "facts_review.coverage_incomplete"

    def test_raw_outputs_captured_in_staged_mode(self, staged_policy):
        """Raw outputs should be captured for every stage in staged mode."""
        packs = _make_packs()
        evidence = packs[0].source_excerpts[0].evidence_hash
        payload = json.dumps({
            "review_status": "complete",
            "reviewed_evidence_hashes": [evidence],
            "coverage_summary": {},
            "findings": [],
        })
        client = _ScriptedClient([payload] * len(STAGE_ORDER))
        result = run_facts_review(
            evidence_packs=packs, reviewer_client=client,
            state=PlanBuildState(state_id="s", requirement_text="r"),
            policy=staged_policy,
        )
        assert len(result.raw_outputs) == len(STAGE_ORDER)
        for raw in result.raw_outputs:
            assert raw
            assert "review_status" in raw
