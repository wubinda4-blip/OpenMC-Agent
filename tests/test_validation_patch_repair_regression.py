import json
from pathlib import Path


def test_vera3_retry_exhaustion_fixture_records_repeated_target_issue() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/regressions/vera3_3a_validation_retry_exhaustion.json").read_text())
    assert fixture["three_rounds_same_issue_fingerprint"] is True
    assert fixture["direct_failure_reason"] == "targeted full-patch regeneration repeated the same invalid pin-map result"
