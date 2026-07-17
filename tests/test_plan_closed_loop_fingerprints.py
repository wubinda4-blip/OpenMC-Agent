from __future__ import annotations

import subprocess
import sys

from openmc_agent.plan_builder.closed_loop.fingerprints import (
    canonical_json_dumps, compute_candidate_hash, compute_issue_fingerprint,
    compute_source_excerpt_hash,
)


def test_fingerprints_are_semantic_and_stable_across_processes() -> None:
    assert canonical_json_dumps({"b": 1, "a": {"y": 2, "x": 3}}) == canonical_json_dumps({"a": {"x": 3, "y": 2}, "b": 1})
    base = compute_issue_fingerprint(gate_id="facts", code="x", affected_patch_type="facts", json_path="/x", expected=1, actual=2)
    assert base != compute_issue_fingerprint(gate_id="facts", code="y", affected_patch_type="facts", json_path="/x", expected=1, actual=2)
    assert base != compute_issue_fingerprint(gate_id="facts", code="x", affected_patch_type="facts", json_path="/y", expected=1, actual=2)
    assert compute_candidate_hash(target_patch_type="facts", candidate_patch={"x": 1}) != compute_candidate_hash(target_patch_type="facts", candidate_patch={"x": 2})
    assert compute_source_excerpt_hash("a", 1, 2, "x") != compute_source_excerpt_hash("a", 2, 2, "x")
    code = "from openmc_agent.plan_builder.closed_loop.fingerprints import compute_issue_fingerprint; print(compute_issue_fingerprint(gate_id='facts', code='x', json_path='/x'))"
    assert base != "" and subprocess.check_output([sys.executable, "-c", code], text=True).strip() == compute_issue_fingerprint(gate_id="facts", code="x", json_path="/x")
