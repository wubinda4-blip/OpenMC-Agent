"""Offline knowledge ingestion for local OpenMC Agent documentation assets.

This module turns local documents and examples into annotated RAG chunks plus
graph-compatible nodes and edges. The implementation is intentionally
deterministic: no network calls, no embeddings, and no LLM annotation.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from openmc_agent.knowledge_graph import GraphEdge, GraphNode
from openmc_agent.rag_search import DocumentChunk, _chunk_file
from openmc_agent.schemas import AgentBaseModel


KnowledgeSourceType = Literal[
    "openmc_docs",
    "openmc_api_docs",
    "openmc_examples",
    "project_docs",
    "project_examples",
    "benchmark_docs",
    "input_cases",
    "internal_notes",
    "readme",
]


class KnowledgeSource(AgentBaseModel):
    source_id: str
    source_type: KnowledgeSourceType
    root_path: str
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeIngestionConfig(AgentBaseModel):
    sources: list[KnowledgeSource] = Field(default_factory=list)
    max_file_bytes: int = 1_000_000
    max_chunk_chars: int = 1800
    chunk_overlap_chars: int = 150
    output_dir: str | None = None
    write_json: bool = True
    write_jsonl: bool = True


class KnowledgeIngestionResult(AgentBaseModel):
    chunks: list[DocumentChunk] = Field(default_factory=list)
    graph_nodes: list[GraphNode] = Field(default_factory=list)
    graph_edges: list[GraphEdge] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    concept_counts: dict[str, int] = Field(default_factory=dict)
    api_counts: dict[str, int] = Field(default_factory=dict)
    schema_path_counts: dict[str, int] = Field(default_factory=dict)


DEFAULT_INCLUDE_GLOBS = ["**/*.md", "**/*.rst", "**/*.txt", "**/*.py", "**/*.json", "**/*.yaml", "**/*.yml", "**/*.toml"]
DEFAULT_EXCLUDE_GLOBS = [
    ".git/**",
    "**/.git/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    ".mypy_cache/**",
    "**/.mypy_cache/**",
    ".venv/**",
    "venv/**",
    "**/site-packages/**",
    "**/*.h5",
    "**/*.hdf5",
    "**/*.xml",
]
SUPPORTED_SUFFIXES = {".md", ".rst", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}


def default_knowledge_ingestion_config() -> KnowledgeIngestionConfig:
    """Return the default local knowledge source manifest."""
    return KnowledgeIngestionConfig(
        sources=[
            KnowledgeSource(
                source_id="project_docs",
                source_type="project_docs",
                root_path="docs",
                include_globs=["**/*.md"],
            ),
            KnowledgeSource(
                source_id="project_examples",
                source_type="project_examples",
                root_path="examples",
                include_globs=["**/*.py", "**/*.md"],
            ),
            KnowledgeSource(
                source_id="input_cases",
                source_type="input_cases",
                root_path="Input",
                include_globs=["**/*.md"],
            ),
            KnowledgeSource(
                source_id="readme",
                source_type="readme",
                root_path=".",
                include_globs=["README.md"],
            ),
            KnowledgeSource(
                source_id="openmc_docs",
                source_type="openmc_docs",
                root_path="openmc_docs",
                include_globs=["**/*.rst", "**/*.md"],
            ),
            KnowledgeSource(
                source_id="openmc_examples",
                source_type="openmc_examples",
                root_path="openmc_examples",
                include_globs=["**/*.py", "**/*.md"],
            ),
        ]
    )


def load_knowledge_sources_manifest(path: str | Path) -> KnowledgeIngestionConfig:
    """Load a JSON source manifest, or return defaults when the file is missing."""
    manifest_path = Path(path)
    if not manifest_path.exists():
        return default_knowledge_ingestion_config()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = {"sources": payload}
    if not isinstance(payload, dict):
        raise ValueError(f"knowledge manifest must be a JSON object or list: {manifest_path}")
    return KnowledgeIngestionConfig.model_validate(payload)


def ingest_knowledge_sources(
    config: KnowledgeIngestionConfig,
) -> KnowledgeIngestionResult:
    """Scan configured local sources and build annotated chunks plus graph assets."""
    active_config = config if config.sources else default_knowledge_ingestion_config()
    chunks: list[DocumentChunk] = []
    warnings: list[str] = []
    source_counts: dict[str, int] = {}

    for source in active_config.sources:
        root = Path(source.root_path).resolve(strict=False)
        if not root.exists():
            warnings.append(f"source root does not exist: {source.source_id} ({source.root_path})")
            continue
        paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in paths:
            if not _path_allowed(path, root, source):
                continue
            try:
                if path.stat().st_size > active_config.max_file_bytes:
                    warnings.append(f"skipped large file: {_display_path(path)}")
                    continue
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                warnings.append(f"skipped non-utf8 or binary file: {_display_path(path)}")
                continue
            except OSError as exc:
                warnings.append(f"failed to read {_display_path(path)}: {exc}")
                continue

            try:
                raw_chunks = _chunk_file(path, text)
            except Exception as exc:  # pragma: no cover - defensive path
                warnings.append(f"failed to chunk {_display_path(path)}: {exc}")
                continue

            for raw_chunk in raw_chunks:
                for chunk in _chunks_for_source(raw_chunk, source, active_config):
                    chunks.append(annotate_chunk(chunk))
                    source_counts[source.source_id] = source_counts.get(source.source_id, 0) + 1

    graph_nodes, graph_edges = chunks_to_graph(chunks)
    result = KnowledgeIngestionResult(
        chunks=chunks,
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        warnings=warnings,
        source_counts=source_counts,
        concept_counts=_count_refs(chunk.concept_ids for chunk in chunks),
        api_counts=_count_refs(chunk.api_refs for chunk in chunks),
        schema_path_counts=_count_refs(chunk.schema_paths for chunk in chunks),
    )
    if active_config.output_dir:
        save_knowledge_ingestion_result(result, active_config.output_dir)
    return result


def annotate_text_refs(text: str, path: str = "") -> dict[str, list[str]]:
    """Return high-precision rule annotations for text and path context."""
    haystack = f"{path}\n{text}"
    lower = haystack.casefold()
    refs: dict[str, list[str]] = {
        "concept_ids": [],
        "api_refs": [],
        "schema_paths": [],
        "doc_refs": [],
        "example_refs": [],
        "benchmark_refs": [],
        "issue_codes": [],
    }

    def add(key: str, values: list[str]) -> None:
        refs[key].extend(values)

    if _has(haystack, r"\bopenmc\.Material\b|\bMaterial\.set_density\b|\bset_density\b"):
        add("concept_ids", ["openmc.material", "openmc.material.density_unit", "openmc.material.density_value"])
        add("api_refs", ["openmc.Material", "openmc.Material.set_density"])
        add("doc_refs", ["openmc.usersguide.materials"])
    if _has(haystack, r"\badd_nuclide\b"):
        add("concept_ids", ["openmc.material.nuclide_name", "openmc.material.composition_fraction"])
        add("api_refs", ["openmc.Material.add_nuclide"])
    if _has(haystack, r"\badd_element\b"):
        add("concept_ids", ["openmc.material.composition_fraction"])
        add("api_refs", ["openmc.Material.add_element"])
    if _has(haystack, r"add_s_alpha_beta|thermal scattering|S\(a,b\)"):
        add("concept_ids", ["openmc.material.thermal_scattering"])
        add("api_refs", ["openmc.Material.add_s_alpha_beta"])
        add("doc_refs", ["openmc.usersguide.materials"])
    if _has(haystack, r"\bmacroscopic\b|add_macroscopic"):
        add("concept_ids", ["openmc.material.macroscopic"])
        add("api_refs", ["openmc.Material.add_macroscopic"])
    if _has(haystack, r"OPENMC_CROSS_SECTIONS|cross_sections\.xml|cross sections missing"):
        add("concept_ids", ["openmc.data.cross_sections"])
        add("doc_refs", ["openmc.usersguide.cross_sections"])
        add("issue_codes", ["runtime.cross_sections_missing"])

    if _has(haystack, r"\bopenmc\.Cell\b|\bopenmc\.Universe\b|\bopenmc\.Geometry\b"):
        add("concept_ids", ["openmc.geometry.cell", "openmc.geometry.universe"])
        add("api_refs", ["openmc.Cell", "openmc.Universe", "openmc.Geometry"])
        add("doc_refs", ["openmc.usersguide.geometry"])
    if _has(haystack, r"openmc\.(XPlane|YPlane|ZPlane|ZCylinder|Sphere)\b|\bsurface\b"):
        add("concept_ids", ["openmc.geometry.surface"])
        add("api_refs", ["openmc.XPlane", "openmc.YPlane", "openmc.ZPlane", "openmc.ZCylinder", "openmc.Sphere"])
    if _has(haystack, r"boolean region|region boolean|boundary_type|\bregion\b|reflective|vacuum|periodic"):
        add("concept_ids", ["openmc.geometry.region_boolean_expression", "openmc.geometry.boundary_type"])
        add("doc_refs", ["openmc.usersguide.geometry"])

    if _has(haystack, r"\bRectLattice\b|universe_pattern|fill_universe|expected_counts|row|column|pin map|loading pattern"):
        add("concept_ids", ["openmc.geometry.lattice", "openmc.geometry.rect_lattice", "openmc.geometry.pin_map", "openmc.geometry.loading_pattern"])
        add("schema_paths", ["complex_model.lattices", "LatticeSpec.universe_pattern", "LatticeSpec.fill_universe", "LatticeSpec.expected_counts"])
    if _has(haystack, r"\bHexLattice\b|\brings\b|outer universe|outer_universe|outer_universe_id"):
        add("concept_ids", ["openmc.geometry.lattice", "openmc.geometry.hex_lattice"])
        add("api_refs", ["openmc.HexLattice", "openmc.api.HexLattice"])
        add("schema_paths", ["LatticeSpec.rings", "LatticeSpec.outer_universe_id"])
        add("doc_refs", ["openmc.usersguide.geometry"])
    if _has(haystack, r"\bpitch\b|lower_left|lattice\.universes"):
        add("concept_ids", ["openmc.geometry.lattice_pitch", "openmc.geometry.lattice"])

    if _has(haystack, r"\bopenmc\.Settings\b|\bbatches\b|\binactive\b|\bparticles\b|run_mode|eigenvalue|\bsource\b"):
        add("concept_ids", ["openmc.settings", "openmc.settings.batches", "openmc.settings.inactive", "openmc.settings.particles", "openmc.settings.run_mode"])
        add("api_refs", ["openmc.Settings"])
    if _has(haystack, r"openmc\.run|export_to_xml|geometry_debug|lost particle"):
        add("concept_ids", ["openmc.execution.smoke_test", "openmc.execution.export_xml", "openmc.execution.geometry_debug"])

    if "geometry overlap" in lower:
        add("issue_codes", ["runtime.geometry_overlap"])
        add("concept_ids", ["openmc.geometry.region_boolean_expression", "openmc.geometry.surface"])
    if "lost particle" in lower:
        add("issue_codes", ["runtime.lost_particle"])
    if _has(haystack, r"XML export|dangling reference|missing universe"):
        add("issue_codes", ["export_xml.dangling_lattice_universe"])
    if "missing material" in lower:
        add("issue_codes", ["export_xml.dangling_material_ref"])
    if "missing cell" in lower:
        add("issue_codes", ["export_xml.dangling_region_surface"])
    if "pin count mismatch" in lower:
        add("issue_codes", ["lattice.pin_count_mismatch"])
    if "pin map mismatch" in lower:
        add("issue_codes", ["lattice.pin_map_mismatch"])

    if _has(haystack, r"\bVERA\b"):
        add("benchmark_refs", ["benchmark.vera"])
    if _has(haystack, r"\bCASL\b"):
        add("benchmark_refs", ["benchmark.casl"])
    if _has(haystack, r"\bC5G7\b"):
        add("benchmark_refs", ["benchmark.c5g7"])
    if _has(haystack, r"\bBEAVRS\b"):
        add("benchmark_refs", ["benchmark.beavrs"])
    if _has(haystack, r"Watts Bar"):
        add("benchmark_refs", ["benchmark.watts_bar"])
    if _has(haystack, r"pin-cell|pin cell"):
        add("concept_ids", ["reactor.pin_cell"])
    if _has(haystack, r"\bassembly\b|17x17|guide tube|fission chamber"):
        add("concept_ids", ["reactor.assembly"])
    if _has(haystack, r"\bcore\b|reflector|control rod"):
        add("concept_ids", ["reactor.core"])
    if _has(haystack, r"\bMOX\b"):
        add("concept_ids", ["reactor.mox"])
    if _has(haystack, r"\bUO2\b|UO₂"):
        add("concept_ids", ["reactor.uo2"])
    if _has(haystack, r"guide tube"):
        add("concept_ids", ["reactor.guide_tube"])
    if _has(haystack, r"fission chamber"):
        add("concept_ids", ["reactor.fission_chamber"])
    if _has(haystack, r"control rod"):
        add("concept_ids", ["reactor.control_rod"])
    if _has(haystack, r"\breflector\b"):
        add("concept_ids", ["reactor.reflector"])

    normalized_path = path.replace("\\", "/")
    if "/examples/" in f"/{normalized_path}" or normalized_path.startswith("examples/"):
        refs["example_refs"].append(_stable_ref("example", normalized_path))
    if "/docs/" in f"/{normalized_path}" or normalized_path.startswith("docs/"):
        refs["doc_refs"].append(_doc_ref_for_path(normalized_path))
    if "/Input/" in f"/{normalized_path}" or normalized_path.startswith("Input/"):
        refs["benchmark_refs"].append(_stable_ref("input", normalized_path))

    return {key: _dedupe(values) for key, values in refs.items()}


def annotate_chunk(chunk: DocumentChunk) -> DocumentChunk:
    """Annotate a chunk with deterministic metadata refs."""
    refs = annotate_text_refs(chunk.text, chunk.path)
    metadata = dict(chunk.metadata)
    metadata.update(
        {
            "annotation_method": "rules",
            "benchmark_refs": refs["benchmark_refs"],
            "issue_codes": refs["issue_codes"],
            "example_refs": refs["example_refs"],
        }
    )
    return chunk.model_copy(
        update={
            "doc_refs": _dedupe([*chunk.doc_refs, *refs["doc_refs"]]),
            "api_refs": _dedupe([*chunk.api_refs, *refs["api_refs"]]),
            "concept_ids": _dedupe([*chunk.concept_ids, *refs["concept_ids"]]),
            "schema_paths": _dedupe([*chunk.schema_paths, *refs["schema_paths"]]),
            "metadata": metadata,
        }
    )


def chunks_to_graph(
    chunks: list[DocumentChunk],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Convert annotated chunks to graph-compatible nodes and edges."""
    nodes_by_id: dict[str, GraphNode] = {}
    edges_by_key: dict[tuple[str, str, str], GraphEdge] = {}

    for chunk in chunks:
        chunk_node_id = f"doc_chunk.{_stable_hash(chunk.chunk_id)}"
        label = Path(chunk.path).name
        if chunk.section:
            label = f"{label}: {chunk.section}"
        metadata = {
            "node_subtype": "doc_chunk",
            "chunk_id": chunk.chunk_id,
            "path": chunk.path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "source_type": chunk.source_type,
            "knowledge_source": chunk.metadata.get("knowledge_source"),
            "knowledge_source_type": chunk.metadata.get("knowledge_source_type"),
            "doc_refs": chunk.doc_refs,
            "api_refs": chunk.api_refs,
            "concept_ids": chunk.concept_ids,
            "schema_paths": chunk.schema_paths,
            "benchmark_refs": chunk.metadata.get("benchmark_refs", []),
            "issue_codes": chunk.metadata.get("issue_codes", []),
            "annotation_method": chunk.metadata.get("annotation_method", "rules"),
            "retrieval_hints": _chunk_retrieval_hints(chunk),
            "ref_id": chunk.chunk_id,
        }
        nodes_by_id.setdefault(
            chunk_node_id,
            GraphNode(
                id=chunk_node_id,
                type="doc_ref",
                title=label,
                description=_truncate(chunk.text, 320),
                aliases=_dedupe([chunk.chunk_id, chunk.path, chunk.section, *chunk.doc_refs]),
                metadata=metadata,
            ),
        )
        for concept_id in chunk.concept_ids:
            target = f"concept.{concept_id}"
            nodes_by_id.setdefault(
                target,
                GraphNode(
                    id=target,
                    type="openmc_concept",
                    title=concept_id,
                    metadata={"concept_id": concept_id},
                ),
            )
            _add_edge(edges_by_key, chunk_node_id, target, "mentions")
        for api_ref in chunk.api_refs:
            target = f"api.{api_ref}"
            nodes_by_id.setdefault(
                target,
                GraphNode(
                    id=target,
                    type="openmc_api",
                    title=api_ref,
                    aliases=[api_ref.removeprefix("openmc.api.")],
                    metadata={"api_ref": api_ref},
                ),
            )
            _add_edge(edges_by_key, chunk_node_id, target, "mentions")
        for schema_path in chunk.schema_paths:
            target = schema_path if schema_path.startswith("schema.") else f"schema.{schema_path}"
            nodes_by_id.setdefault(
                target,
                GraphNode(
                    id=target,
                    type="schema_field",
                    title=schema_path,
                    metadata={"schema_path": schema_path.removeprefix("schema.")},
                ),
            )
            _add_edge(edges_by_key, chunk_node_id, target, "related_to")
        for issue_code in chunk.metadata.get("issue_codes", []):
            target = f"issue.{issue_code}"
            nodes_by_id.setdefault(
                target,
                GraphNode(
                    id=target,
                    type="validation_issue",
                    title=issue_code,
                    metadata={"error_code": issue_code},
                ),
            )
            _add_edge(edges_by_key, chunk_node_id, target, "related_to")
        for benchmark_ref in chunk.metadata.get("benchmark_refs", []):
            target = f"concept.{benchmark_ref}"
            nodes_by_id.setdefault(
                target,
                GraphNode(
                    id=target,
                    type="openmc_concept",
                    title=benchmark_ref,
                    metadata={"concept_id": benchmark_ref},
                ),
            )
            _add_edge(edges_by_key, chunk_node_id, target, "mentions")
        for example_ref in chunk.metadata.get("example_refs", []):
            target = f"example.{example_ref}"
            nodes_by_id.setdefault(
                target,
                GraphNode(
                    id=target,
                    type="example_ref",
                    title=example_ref,
                    metadata={"ref_id": example_ref},
                ),
            )
            _add_edge(edges_by_key, target, chunk_node_id, "demonstrated_by")

    return list(nodes_by_id.values()), list(edges_by_key.values())


def save_knowledge_ingestion_result(
    result: KnowledgeIngestionResult,
    output_dir: str | Path,
    *,
    write_json: bool = True,
    write_jsonl: bool = True,
) -> None:
    """Persist chunks, graph assets, and summary files to a directory."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if write_json:
        (out / "knowledge_chunks.json").write_text(
            json.dumps([chunk.model_dump(mode="json") for chunk in result.chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out / "knowledge_graph_nodes.json").write_text(
            json.dumps([node.model_dump(mode="json") for node in result.graph_nodes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out / "knowledge_graph_edges.json").write_text(
            json.dumps([edge.model_dump(mode="json") for edge in result.graph_edges], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if write_jsonl:
        (out / "knowledge_chunks.jsonl").write_text(
            "\n".join(json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False) for chunk in result.chunks),
            encoding="utf-8",
        )
    (out / "knowledge_summary.json").write_text(
        json.dumps(_summary_payload(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_ingested_graph(path_or_dir: str | Path) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Load graph nodes and edges previously written by knowledge ingestion."""
    path = Path(path_or_dir)
    nodes_path = path / "knowledge_graph_nodes.json" if path.is_dir() else path
    edges_path = path / "knowledge_graph_edges.json" if path.is_dir() else path.with_name("knowledge_graph_edges.json")
    nodes_payload = json.loads(nodes_path.read_text(encoding="utf-8"))
    edges_payload = json.loads(edges_path.read_text(encoding="utf-8"))
    return (
        [GraphNode.model_validate(item) for item in nodes_payload],
        [GraphEdge.model_validate(item) for item in edges_payload],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest local OpenMC Agent knowledge assets.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--max-file-bytes", type=int, default=None)
    parser.add_argument("--no-json", action="store_true")
    parser.add_argument("--no-jsonl", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = (
            load_knowledge_sources_manifest(args.manifest)
            if args.manifest
            else default_knowledge_ingestion_config()
        )
        if args.root:
            config = config.model_copy(
                update={
                    "sources": [
                        KnowledgeSource(
                            source_id=f"cli_source_{idx}",
                            source_type="project_docs",
                            root_path=root,
                            include_globs=args.include or DEFAULT_INCLUDE_GLOBS,
                            exclude_globs=args.exclude,
                        )
                        for idx, root in enumerate(args.root, start=1)
                    ]
                }
            )
        updates: dict[str, Any] = {
            "output_dir": args.output,
            "write_json": not args.no_json,
            "write_jsonl": not args.no_jsonl,
        }
        if args.max_file_bytes is not None:
            updates["max_file_bytes"] = args.max_file_bytes
        config = config.model_copy(update=updates)
        result = ingest_knowledge_sources(config)
        print(json.dumps(_summary_payload(result), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"knowledge ingestion failed: {exc}")
        return 2


def _path_allowed(path: Path, root: Path, source: KnowledgeSource) -> bool:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    rel = _relative_to(path, root)
    root_rel = _display_path(path)
    includes = source.include_globs or DEFAULT_INCLUDE_GLOBS
    excludes = [*DEFAULT_EXCLUDE_GLOBS, *source.exclude_globs]
    if any(part in {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv"} for part in path.parts):
        return False
    if any(_glob_matches(rel, pattern) or _glob_matches(root_rel, pattern) for pattern in excludes):
        return False
    return any(_glob_matches(rel, pattern) or _glob_matches(path.name, pattern) for pattern in includes)


def _chunks_for_source(
    chunk: DocumentChunk,
    source: KnowledgeSource,
    config: KnowledgeIngestionConfig,
) -> list[DocumentChunk]:
    source_type = _rag_source_type(source)
    text_chunks = _split_text_with_overlap(
        chunk.text,
        max_chars=max(200, config.max_chunk_chars),
        overlap=max(0, min(config.chunk_overlap_chars, config.max_chunk_chars // 2)),
    )
    metadata = dict(chunk.metadata)
    metadata.update(
        {
            "knowledge_source": source.source_id,
            "knowledge_source_type": source.source_type,
            "knowledge_source_version": source.version,
            "source_metadata": source.metadata,
        }
    )
    chunks: list[DocumentChunk] = []
    for index, text in enumerate(text_chunks or [chunk.text]):
        chunk_id = f"{source.source_id}:{chunk.chunk_id}"
        if len(text_chunks) > 1:
            chunk_id = f"{chunk_id}:part{index + 1}"
        chunks.append(
            chunk.model_copy(
                update={
                    "chunk_id": chunk_id,
                    "source_id": source.source_id,
                    "source_type": source_type,
                    "text": text,
                    "metadata": metadata,
                }
            )
        )
    return chunks


def _rag_source_type(source: KnowledgeSource) -> str:
    mapping = {
        "openmc_docs": "openmc_doc",
        "openmc_api_docs": "openmc_api_doc",
        "openmc_examples": "openmc_example",
        "project_docs": "project_doc",
        "project_examples": "project_example",
        "benchmark_docs": "project_doc",
        "input_cases": "internal_note",
        "internal_notes": "internal_note",
        "readme": "project_doc",
    }
    return mapping.get(source.source_type, "unknown")


def _split_text_with_overlap(text: str, *, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _chunk_retrieval_hints(chunk: DocumentChunk) -> list[str]:
    hints = [chunk.section, chunk.title, *chunk.concept_ids[:4], *chunk.api_refs[:3]]
    if chunk.metadata.get("issue_codes"):
        hints.extend(chunk.metadata["issue_codes"][:3])
    return _dedupe([hint for hint in hints if isinstance(hint, str) and len(hint) >= 3])[:8]


def _add_edge(
    edges_by_key: dict[tuple[str, str, str], GraphEdge],
    source: str,
    target: str,
    relation: str,
) -> None:
    key = (source, target, relation)
    edges_by_key.setdefault(
        key,
        GraphEdge(source=source, target=target, relation=relation, weight=1.0),
    )


def _count_refs(ref_groups: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for refs in ref_groups:
        for ref in refs:
            counts[ref] = counts.get(ref, 0) + 1
    return counts


def _summary_payload(result: KnowledgeIngestionResult) -> dict[str, Any]:
    return {
        "chunk_count": len(result.chunks),
        "node_count": len(result.graph_nodes),
        "edge_count": len(result.graph_edges),
        "warnings": result.warnings,
        "source_counts": result.source_counts,
        "concept_counts": result.concept_counts,
        "api_counts": result.api_counts,
        "schema_path_counts": result.schema_path_counts,
    }


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def _glob_matches(value: str, pattern: str) -> bool:
    if fnmatch.fnmatch(value, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatch(value, pattern[3:]):
        return True
    return False


def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _stable_ref(prefix: str, value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", Path(value).with_suffix("").as_posix()).strip("_")
    return f"{prefix}.{stem[:80] or _stable_hash(value)}"


def _doc_ref_for_path(path: str) -> str:
    stem = Path(path).with_suffix("").as_posix()
    return "project." + re.sub(r"[^A-Za-z0-9_]+", ".", stem).strip(".")


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _display_path(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(Path.cwd().resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if not item:
            continue
        key = str(item).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(str(item))
    return deduped


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
