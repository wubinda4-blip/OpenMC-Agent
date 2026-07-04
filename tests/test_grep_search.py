from pathlib import Path

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.grep_search import (
    GrepMatch,
    GrepSearchRequest,
    GrepSearchResult,
    grep_request_from_issue,
    grep_result_to_evidence,
    grep_search,
)
from openmc_agent.tools import parse_openmc_output


def test_grep_search_finds_text_with_context_and_limits(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "model.py").write_text(
        "alpha = 1\n"
        "def build_geometry():\n"
        "    overlap = 'cells 10 11'\n"
        "    return overlap\n",
        encoding="utf-8",
    )
    (src / "notes.md").write_text("OpenMC geometry_overlap note\n", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "hidden.py").write_text("overlap\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "cached.py").write_text("overlap\n", encoding="utf-8")
    (src / "statepoint.10.h5").write_bytes(b"overlap")

    result = grep_search(
        GrepSearchRequest(
            trigger="manual",
            patterns=["overlap"],
            include_globs=["*.py"],
            search_roots=[str(src)],
            context_lines=1,
            max_matches=1,
        )
    )

    assert result.matches
    assert result.truncated is True
    match = result.matches[0]
    assert match.line_start == 2
    assert match.line_end == 4
    assert match.matched_pattern == "overlap"
    assert "build_geometry" in match.text
    assert ".git" not in match.path
    assert "__pycache__" not in match.path
    assert not match.path.endswith(".h5")


def test_grep_search_fallback_without_rg(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README.md").write_text("HexLattice rings outer_universe_id\n", encoding="utf-8")
    monkeypatch.setattr("openmc_agent.grep_search.shutil.which", lambda name: None)

    result = grep_search(
        GrepSearchRequest(
            trigger="manual",
            patterns=["HexLattice"],
            search_roots=[str(root)],
            max_matches=5,
        )
    )

    assert result.matches[0].matched_pattern == "HexLattice"
    assert any("fallback" in warning for warning in result.warnings)


def test_runtime_geometry_overlap_issue_generates_grep_request() -> None:
    report = parse_openmc_output(
        stdout="",
        stderr="ERROR: Overlap detected between cells 10 and 11",
    )
    request = grep_request_from_issue(report.issues[0])

    assert request.trigger == "runtime_issue"
    assert "geometry_overlap" in request.patterns
    assert "overlap" in request.patterns
    assert "Overlap" in request.patterns or "detected" in request.patterns


def test_export_xml_dangling_lattice_issue_generates_request() -> None:
    issue = issue_from_catalog(
        "export_xml.dangling_lattice_universe",
        message="lattice 7 references missing universe id 12; candidates: 11",
        schema_path="complex_model.lattices[0].universe_pattern",
        grep_patterns=["12", "11", "lattice:7"],
    )

    request = grep_request_from_issue(issue)

    assert request.trigger == "export_xml_issue"
    assert "12" in request.patterns
    assert "11" in request.patterns
    assert "universe_pattern" in request.patterns
    assert "LatticeSpec" in request.patterns


def test_hex_issue_generates_request() -> None:
    issue = issue_from_catalog("lattice.hex.renderer_unsupported")

    request = grep_request_from_issue(issue)

    assert request.trigger == "hex_lattice_issue"
    assert "HexLattice" in request.patterns
    assert "LatticeSpec" in request.patterns
    assert "rings" in request.patterns
    assert "outer_universe_id" in request.patterns


def test_grep_result_to_evidence_dedupes_and_preserves_metadata() -> None:
    request = GrepSearchRequest(
        trigger="validation_issue",
        issue_code="runtime.geometry_overlap",
        schema_path="complex_model.cells[0].region",
        concept_id="openmc.geometry.cell_region",
        patterns=["region"],
    )
    result = GrepSearchResult(
        request=request,
        matches=[
            GrepMatch(
                source_type="project_code",
                path="openmc_agent/schemas.py",
                line_start=10,
                line_end=12,
                matched_pattern="region",
                text="10: region: str",
            ),
            GrepMatch(
                source_type="project_code",
                path="openmc_agent/schemas.py",
                line_start=11,
                line_end=13,
                matched_pattern="cell_region",
                text="11: cell_region",
            ),
        ],
    )

    evidence = grep_result_to_evidence(result)

    assert len(evidence) == 1
    assert evidence[0].source_type == "grep"
    assert evidence[0].locator == "openmc_agent/schemas.py:10-12"
    assert evidence[0].metadata["issue_code"] == "runtime.geometry_overlap"
    assert evidence[0].metadata["schema_path"] == "complex_model.cells[0].region"
    assert evidence[0].metadata["matched_pattern"] == "region"
