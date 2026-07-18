"""Real campaign human answer file tests."""

import json
from pathlib import Path

import pytest

from openmc_agent.real_campaign_harness import (
    HumanAnswerProvenance,
    consume_human_answers,
    load_human_answer_file,
)


def test_load_returns_empty_when_no_path():
    answers, h = load_human_answer_file(None)
    assert answers == {}
    assert h == ""


def test_load_parses_valid_json(tmp_path: Path):
    f = tmp_path / "answers.json"
    f.write_text(json.dumps({"q1": {"answer": "yes"}}), encoding="utf-8")
    answers, h = load_human_answer_file(str(f))
    assert "q1" in answers
    assert len(h) == 16  # short hash


def test_load_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_human_answer_file(str(tmp_path / "missing.json"))


def test_load_raises_on_invalid_json(tmp_path: Path):
    f = tmp_path / "answers.json"
    f.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_human_answer_file(str(f))


def test_load_raises_on_non_object_json(tmp_path: Path):
    f = tmp_path / "answers.json"
    f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_human_answer_file(str(f))


def test_consume_returns_human_answer_provenance():
    prov = consume_human_answers({"q1": "a", "q2": "b"}, ["q1"])
    assert isinstance(prov, HumanAnswerProvenance)
    assert "q1" in prov.consumed_question_fingerprints
    assert "q2" in prov.unused_answers


def test_consume_never_auto_generates_answers():
    """If no answer matches a question, it stays unanswered — no fallback."""
    prov = consume_human_answers({}, ["q1", "q2"])
    assert prov.consumed_question_fingerprints == []
    assert prov.unused_answers == []


def test_consume_records_answer_file_hash():
    prov = consume_human_answers({"q1": "a"}, [])
    assert len(prov.answer_file_hash) == 16


def test_consume_distinguishes_unused_and_consumed():
    prov = consume_human_answers({"q1": "a", "q2": "b", "q3": "c"}, ["q2"])
    assert prov.consumed_question_fingerprints == ["q2"]
    assert set(prov.unused_answers) == {"q1", "q3"}
