from openmc_agent.plan_builder.closed_loop.facts_evidence import build_facts_evidence_packs
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def test_evidence_chunks_preserve_lines_and_source_order() -> None:
    text = "# Heading\nfirst\n\nsecond\nthird\n"
    packs = build_facts_evidence_packs(
        requirement_text=text, facts_patch={"patch_type": "facts"}, confirmed_facts={},
        planning_metadata={}, policy=PlanClosedLoopPolicy(facts_review_chunk_chars=12),
    )
    excerpts = [pack.source_excerpts[0] for pack in packs]
    assert "".join(item.text.replace("\n", "") for item in excerpts).replace(" ", "") == text.replace("\n", "").replace(" ", "")
    assert [item.line_start for item in excerpts] == sorted(item.line_start for item in excerpts)
    assert all(item.evidence_hash for item in excerpts)
