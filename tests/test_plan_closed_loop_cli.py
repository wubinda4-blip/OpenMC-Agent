from __future__ import annotations

import openmc_agent.inspect as inspect_module


def test_cli_forwards_plan_loop_options(monkeypatch, tmp_path) -> None:
    captured = {}

    class Result:
        ok = True
        transcript = "ok"
        transcript_data = {}

    def fake_inspect(requirement, **kwargs):
        captured["requirement"] = requirement
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(inspect_module, "inspect_requirement", fake_inspect)
    assert inspect_module.main([
        "--plan-loop-mode", "advisory", "--max-plan-review-rounds", "3",
        "--output-dir", str(tmp_path), "requirement",
    ]) == 0
    assert captured["plan_loop_mode"] == "advisory"
    assert captured["max_plan_review_rounds"] == 3
    assert captured["use_plan"] is True


def test_cli_forwards_patch_and_reviewer_output_controls(monkeypatch, tmp_path) -> None:
    captured = {}

    class Result:
        ok = True
        transcript = "ok"
        transcript_data = {}

    def fake_inspect(requirement, **kwargs):
        captured["requirement"] = requirement
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(inspect_module, "inspect_requirement", fake_inspect)
    assert inspect_module.main([
        "--plan-loop-mode", "advisory",
        "--patch-output-mode", "json_object",
        "--patch-max-tokens", "12000",
        "--patch-reasoning-effort", "none",
        "--plan-reviewer-output-mode", "json_object",
        "--plan-reviewer-max-tokens", "8000",
        "--plan-reviewer-reasoning-effort", "none",
        "--output-dir", str(tmp_path), "requirement",
    ]) == 0
    assert captured["patch_output_mode"] == "json_object"
    assert captured["patch_max_tokens"] == 12000
    assert captured["patch_reasoning_effort"] == "none"
    assert captured["plan_reviewer_output_mode"] == "json_object"
    assert captured["plan_reviewer_max_tokens"] == 8000
    assert captured["plan_reviewer_reasoning_effort"] == "none"
