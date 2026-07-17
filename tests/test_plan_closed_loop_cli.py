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
