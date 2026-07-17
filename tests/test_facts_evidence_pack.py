from openmc_agent.plan_builder.closed_loop.facts_evidence import _paragraphs, build_facts_evidence_packs
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def test_evidence_chunks_preserve_lines_and_source_order() -> None:
    text = "# Heading\nfirst\n\nsecond\nthird\n"
    packs = build_facts_evidence_packs(
        requirement_text=text, facts_patch={"patch_type": "facts"}, confirmed_facts={},
        planning_metadata={}, policy=PlanClosedLoopPolicy(facts_review_chunk_chars=12),
    )
    excerpts = [pack.source_excerpts[0] for pack in packs]
    assert "".join(chunk_text for _, _, chunk_text in _paragraphs(text, 12)) == text
    assert [item.line_start for item in excerpts] == sorted(item.line_start for item in excerpts)
    assert all(item.evidence_hash for item in excerpts)


def test_evidence_chunks_coalesce_small_markdown_paragraphs() -> None:
    text = "".join(f"item {index}\n\n" for index in range(80))
    packs = build_facts_evidence_packs(
        requirement_text=text, facts_patch={"patch_type": "facts"}, confirmed_facts={},
        planning_metadata={}, policy=PlanClosedLoopPolicy(
            facts_review_chunk_chars=1024,
            max_facts_review_chunks=8,
        ),
    )

    excerpts = [pack.source_excerpts[0] for pack in packs]
    assert len(excerpts) == 1
    assert excerpts[0].text == text.strip()
    assert excerpts[0].line_start == 1
    assert excerpts[0].line_end == 160
    assert not packs[0].metadata["source_truncated"]


def test_evidence_chunks_keep_an_overlong_physical_line_intact() -> None:
    long_line = "x" * 30 + "\n"
    text = "short\n" + long_line + "tail\n"
    packs = build_facts_evidence_packs(
        requirement_text=text, facts_patch={"patch_type": "facts"}, confirmed_facts={},
        planning_metadata={}, policy=PlanClosedLoopPolicy(facts_review_chunk_chars=12),
    )

    excerpts = [pack.source_excerpts[0] for pack in packs]
    assert "".join(chunk_text for _, _, chunk_text in _paragraphs(text, 12)) == text
    assert [item.text for item in excerpts] == ["short", long_line.strip(), "tail"]
    assert [(item.line_start, item.line_end) for item in excerpts] == [(1, 1), (2, 2), (3, 3)]
