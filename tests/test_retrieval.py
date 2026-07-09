"""Tests for the read-only retrieval tools and the investigation loop."""

from __future__ import annotations

import pytest


from openmc_agent.llm import StructuredOutputResult
from openmc_agent.retrieval import (
    RetrievalStep,
    _resolve_within_roots,
    grep_code,
    list_dir,
    locate_definition,
    read_file,
    run_retrieval_loop,
)
from openmc_agent.tools import ToolResult


# ---------------------------------------------------------------------------
# grep_code
# ---------------------------------------------------------------------------


def test_grep_code_finds_pattern(tmp_path):
    (tmp_path / "a.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
    result = grep_code("class Foo", roots=[tmp_path])
    assert result.ok
    assert "a.py" in result.stdout
    assert "class Foo" in result.stdout


def test_grep_code_respects_file_glob(tmp_path):
    (tmp_path / "a.py").write_text("hello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
    result = grep_code("hello", roots=[tmp_path], file_glob="*.py")
    assert result.ok
    assert "a.py" in result.stdout
    assert "b.txt" not in result.stdout


def test_grep_code_truncates_at_max_matches(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("target\n", encoding="utf-8")
    result = grep_code("target", roots=[tmp_path], max_matches=3)
    assert result.ok
    assert "truncated at 3" in result.stdout


def test_grep_code_invalid_regex(tmp_path):
    result = grep_code("(unbalanced", roots=[tmp_path])
    assert not result.ok
    assert "invalid regex" in result.error


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


def test_read_file_line_range(tmp_path):
    (tmp_path / "f.py").write_text(
        "\n".join(f"LINE {i + 1}" for i in range(20)) + "\n", encoding="utf-8"
    )
    result = read_file("f.py", roots=[tmp_path], start_line=5, end_line=7)
    assert result.ok
    assert "LINE 5" in result.stdout and "LINE 7" in result.stdout
    assert "LINE 8" not in result.stdout


def test_read_file_truncates_long_files(tmp_path):
    (tmp_path / "f.py").write_text(
        "\n".join(f"LINE {i + 1}" for i in range(300)) + "\n", encoding="utf-8"
    )
    result = read_file("f.py", roots=[tmp_path], max_lines=10)
    assert result.ok
    assert "LINE 10" in result.stdout
    assert "LINE 50" not in result.stdout


def test_read_file_rejects_outside_root(tmp_path):
    result = read_file("/etc/passwd", roots=[tmp_path])
    assert not result.ok
    assert "outside" in result.error


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


def test_list_dir_distinguishes_files_and_dirs(tmp_path):
    (tmp_path / "file.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    result = list_dir(".", roots=[tmp_path])
    assert result.ok
    assert "file.py" in result.stdout
    assert "sub/" in result.stdout


def test_list_dir_rejects_outside_root(tmp_path):
    result = list_dir("/etc", roots=[tmp_path])
    assert not result.ok


# ---------------------------------------------------------------------------
# locate_definition
# ---------------------------------------------------------------------------


def test_locate_definition_finds_class_and_function(tmp_path):
    (tmp_path / "f.py").write_text(
        "class Bar:\n    pass\n\ndef baz():\n    pass\n", encoding="utf-8"
    )
    result = locate_definition("Bar", roots=[tmp_path])
    assert result.ok
    assert "class Bar" in result.stdout


def test_locate_definition_reports_miss(tmp_path):
    (tmp_path / "f.py").write_text("nothing here\n", encoding="utf-8")
    result = locate_definition("Missing", roots=[tmp_path])
    assert result.ok
    assert "no definition" in result.stdout


# ---------------------------------------------------------------------------
# path whitelist
# ---------------------------------------------------------------------------


def test_resolve_rejects_dotdot_traversal(tmp_path):
    with pytest.raises(PermissionError):
        _resolve_within_roots("../etc/passwd", [tmp_path])


def test_resolve_rejects_absolute_outside(tmp_path):
    with pytest.raises(PermissionError):
        _resolve_within_roots("/etc/passwd", [tmp_path])


def test_resolve_accepts_relative_under_root(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.py").write_text("x", encoding="utf-8")
    resolved = _resolve_within_roots("sub/f.py", [tmp_path])
    assert resolved.name == "f.py"


def test_resolve_rejects_symlink_escape(tmp_path):
    target = tmp_path.parent / "retrieval_secret_target.txt"
    target.write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(target)
    try:
        with pytest.raises(PermissionError):
            _resolve_within_roots("link", [tmp_path])
    finally:
        target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# run_retrieval_loop
# ---------------------------------------------------------------------------


def _ok(step: RetrievalStep) -> StructuredOutputResult:
    return StructuredOutputResult(ok=True, value=step)


def test_retrieval_loop_investigate_then_done(tmp_path):
    calls = {"n": 0}

    def fake_llm(prompt: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok(
                RetrievalStep(
                    action="investigate",
                    reasoning="look up foo",
                    tool="mytool",
                    arguments={"q": "foo"},
                )
            )
        return _ok(
            RetrievalStep(
                action="done",
                reasoning="found it",
                findings="foo is defined in model.py",
            )
        )

    def fake_tool(*, roots, q):
        return ToolResult(name="mytool", ok=True, stdout=f"hit {q}")

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="find foo",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        tool_dispatch={"mytool": fake_tool},
        max_iterations=4,
    )
    assert outcome.ok
    assert outcome.findings == "foo is defined in model.py"
    assert outcome.patch is None
    assert len(outcome.trace) == 2
    assert outcome.trace[0]["action"] == "investigate"
    assert outcome.trace[1]["action"] == "done"
    assert calls["n"] == 2


def test_retrieval_loop_done_with_patch(tmp_path):
    patch = [{"op": "replace", "path": "/model_spec/pin_cell/fuel/temperature_k", "value": 900}]

    def fake_llm(prompt: str):
        return _ok(
            RetrievalStep(
                action="done",
                reasoning="verified temp",
                findings="temperature should be 900 K",
                patch=patch,
            )
        )

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="t",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        max_iterations=4,
    )
    assert outcome.ok
    assert outcome.patch == patch


def test_retrieval_loop_null_patch_with_reason(tmp_path):
    def fake_llm(prompt: str):
        return _ok(
            RetrievalStep(
                action="done",
                reasoning="too large",
                findings="defect spans the whole lattice",
                patch=None,
                no_patch_reason="lattice universe_pattern is fundamentally wrong",
            )
        )

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="t",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        max_iterations=4,
    )
    assert outcome.ok
    assert outcome.patch is None


def test_retrieval_loop_unknown_tool_recorded_then_done(tmp_path):
    sequence = iter(
        [
            _ok(
                RetrievalStep(
                    action="investigate",
                    reasoning="try nope",
                    tool="nope",
                    arguments={},
                )
            ),
            _ok(RetrievalStep(action="done", reasoning="ok", findings="recovered")),
        ]
    )

    def fake_llm(prompt: str):
        return next(sequence)

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="t",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        max_iterations=4,
    )
    assert outcome.ok
    assert outcome.findings == "recovered"
    assert len(outcome.trace) == 2


def test_retrieval_loop_bad_arguments_recorded_then_done(tmp_path):
    sequence = iter(
        [
            _ok(
                RetrievalStep(
                    action="investigate",
                    reasoning="call",
                    tool="mytool",
                    arguments={"unexpected": 1},
                )
            ),
            _ok(RetrievalStep(action="done", reasoning="ok", findings="recovered")),
        ]
    )

    def fake_llm(prompt: str):
        return next(sequence)

    def fake_tool(*, roots, required):  # pragma: no cover - should not succeed
        return ToolResult(name="mytool", ok=True)

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="t",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        tool_dispatch={"mytool": fake_tool},
        max_iterations=4,
    )
    assert outcome.ok
    assert outcome.findings == "recovered"


def test_retrieval_loop_max_iterations_forces_non_convergence(tmp_path):
    def fake_llm(prompt: str):
        return _ok(
            RetrievalStep(
                action="investigate",
                reasoning="keep digging",
                tool="grep_code",
                arguments={"pattern": "x"},
            )
        )

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="t",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        max_iterations=2,
    )
    assert not outcome.ok
    assert "did not converge" in outcome.error
    assert len(outcome.trace) == 2


def test_retrieval_loop_llm_failure_returns_error_outcome(tmp_path):
    def fake_llm(prompt: str):
        return StructuredOutputResult(ok=False, error="boom")

    outcome = run_retrieval_loop(
        phase="reflect",
        task_brief="t",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        max_iterations=4,
    )
    assert not outcome.ok
    assert "boom" in outcome.error


def test_retrieval_loop_generate_phase_runs(tmp_path):
    """generate phase uses the loop for context gathering (no patch expected)."""

    def fake_llm(prompt: str):
        return _ok(
            RetrievalStep(
                action="done",
                reasoning="surveyed api",
                findings="OpenMC openmc.Cell takes fill= argument",
                patch=None,
            )
        )

    outcome = run_retrieval_loop(
        phase="generate",
        task_brief="build a pin cell",
        plan_summary="",
        roots=[tmp_path],
        investigation_llm=fake_llm,
        max_iterations=4,
    )
    assert outcome.ok
    assert "Cell" in outcome.findings
