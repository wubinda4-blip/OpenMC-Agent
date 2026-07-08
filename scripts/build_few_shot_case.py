#!/usr/bin/env python
"""Build gold few-shot case data from existing successful runs.

Generates ``data/few_shot_cases/<case_id>/`` containing:
* ``meta.json`` — reactor-type-agnostic structural features + trigger terms,
* ``monolithic_slim_ir.json`` — a trimmed SimulationPlan (monolithic path),
* ``digest.md`` — a synthesized reactor-neutral structural summary,
* ``patches/<patch_type>.json`` — per-patch exemplars (incremental path),
  only when a patch fixture is available.

Reactor-type neutrality
-----------------------
Case ids, structural features and trigger terms describe STRUCTURE only.
Benchmark identifiers (e.g. VERA3/C5G7/CASL), provenance strings and model
names are anonymized away so the few-shot teaches *structure*, never a
specific reactor. Numeric values are illustrative references, not
authoritative constants (per CLAUDE.md safety/universality boundaries).

Usage::

    conda run -n openmc-env python scripts/build_few_shot_case.py [case_id ...]
    # no args = build all configured cases
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openmc_agent.few_shot_cases import FEW_SHOT_CASES_DIR, slim_ir_from_plan

_ROOT = Path(__file__).resolve().parent.parent
_RUNS = _ROOT / "data" / "runs"

# Lowercased reactor / benchmark / provenance markers scrubbed from strings.
_REACTOR_MARKERS: tuple[str, ...] = (
    "vera", "c5g7", "casl", "westinghouse", "ce-")  # noqa: E741


def _case_display_name(case_id: str) -> str:
    return case_id.replace("_", " ").title()


# Per-case configuration. structural_features / trigger_terms are curated to be
# reactor-type agnostic (structure only).
_CASES: dict[str, dict[str, Any]] = {
    "pin_cell_basic": {
        "source_plan": _RUNS / "VERA1" / "simulation_plan.json",
        "structural_features": ["pin_cell", "2d", "reflective"],
        "trigger_terms": ["pin cell", "pincell", "栅元", "燃料棒", "pin-cell", "reflective", "反射"],
        "source_note": "Curated from a verified reflective pin-cell benchmark; illustrative reference only.",
    },
    "assembly_2d_lattice": {
        "source_plan": _RUNS / "VERA2" / "simulation_plan.json",
        "structural_features": ["assembly", "2d", "17x17"],
        "trigger_terms": ["assembly", "组件", "lattice", "栅阵", "17x17", "2d", "二维", "infinite lattice"],
        "source_note": "Curated from a verified 2D infinite fuel-assembly lattice; illustrative reference only.",
    },
    "assembly_3d_with_spacer_grids": {
        "source_plan": _RUNS / "VERA3" / "3A" / "simulation_plan.json",
        "fixture_patches": _ROOT / "tests" / "fixtures" / "vera3_patches" / "vera3_3a_patches.json",
        "structural_features": [
            "assembly", "3d", "17x17", "axial_overlay", "spacer_grid", "full_symmetry",
        ],
        "trigger_terms": [
            "assembly", "组件", "spacer grid", "格架", "定位格架", "axial", "nozzle",
            "管座", "end plug", "端塞", "3d", "三维", "17x17",
        ],
        "source_note": "Curated from a verified 3D fuel-assembly benchmark with spacer-grid overlays; illustrative reference only.",
    },
    "quarter_core_with_reflector": {
        "source_plan": _RUNS / "case3" / "simulation_plan.json",
        "structural_features": ["core", "quarter", "reflector", "mox"],
        "trigger_terms": ["core", "全堆", "堆芯", "quarter", "四分之一", "reflector", "反射层", "mox", "钚"],
        "source_note": "Curated from a verified quarter-core benchmark with radial reflector; illustrative reference only.",
    },
}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _scrub_markers(node: Any) -> Any:
    """Recursively replace strings that carry reactor/benchmark markers."""
    if isinstance(node, str):
        low = node.lower()
        if any(marker in low for marker in _REACTOR_MARKERS):
            return "[reference]"
        return node
    if isinstance(node, dict):
        return {k: _scrub_markers(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub_markers(v) for v in node]
    return node


def _anonymize_patch(entry: dict[str, Any]) -> dict[str, Any]:
    """Genericize identifying fields of a patch exemplar, keep structure."""
    entry = dict(entry)
    if "benchmark_id" in entry:
        entry["benchmark_id"] = "EXAMPLE"
    if "source_note" in entry and isinstance(entry["source_note"], str):
        entry["source_note"] = ""
    return _scrub_markers(entry)


def _anonymize_plan_names(plan: dict[str, Any], display_name: str) -> None:
    """Replace reactor-specific model names with a neutral label (in place)."""
    cm = plan.get("complex_model")
    if isinstance(cm, dict) and cm.get("name"):
        cm["name"] = display_name
    ms = plan.get("model_spec")
    if isinstance(ms, dict) and ms.get("name"):
        ms["name"] = display_name


def _synth_digest(plan: dict[str, Any], case_id: str) -> str:
    """Build a reactor-neutral structural digest from a raw plan."""
    display = _case_display_name(case_id)
    lines = [f"# Structural digest — {display}", ""]
    cm = plan.get("complex_model")
    ms = plan.get("model_spec")

    if isinstance(cm, dict):
        kind = cm.get("kind", "unknown")
        materials = cm.get("materials") or []
        universes = cm.get("universes") or []
        lattices = cm.get("lattices") or []
        core = cm.get("core") or {}
        layers = core.get("axial_layers") or []
        overlays = core.get("axial_overlays") or []
        lines.append(f"kind: `{kind}`")
        lines.append(f"- materials: {len(materials)} ({', '.join(m.get('id', '?') for m in materials)})")
        lines.append(f"- universes: {len(universes)} ({', '.join(u.get('id', '?') for u in universes)})")
        for lat in lattices:
            pattern = lat.get("universe_pattern") or []
            if pattern:
                lines.append(f"- lattice `{lat.get('id', '?')}` shape: [{len(pattern)}, {len(pattern[0])}]")
        if layers:
            roles = ", ".join(L.get("role", "?") for L in layers)
            lines.append(f"- axial layers: {len(layers)} (roles: {roles})")
        if overlays:
            kinds = ", ".join(o.get("overlay_kind", "?") for o in overlays)
            lines.append(f"- axial overlays: {len(overlays)} (kinds: {kinds})")
    elif isinstance(ms, dict):
        lines.append(f"kind: `{ms.get('kind', 'pin_cell')}`")
        for slot in ("fuel", "moderator", "cladding"):
            mat = ms.get(slot)
            if isinstance(mat, dict):
                lines.append(f"- {slot}: {mat.get('name', '?')}")
        lines.append("- reflective pin-cell approximation (no lattice)")

    lines += [
        "",
        "_Illustrative structural reference, not authoritative constants._",
    ]
    return "\n".join(lines) + "\n"


def build_case(case_id: str) -> None:
    cfg = _CASES[case_id]
    out = FEW_SHOT_CASES_DIR / case_id
    display = _case_display_name(case_id)

    plan = json.loads(Path(cfg["source_plan"]).read_text(encoding="utf-8"))
    _anonymize_plan_names(plan, display)

    # meta.json
    _write_json(out / "meta.json", {
        "case_id": case_id,
        "structural_features": cfg["structural_features"],
        "trigger_terms": cfg["trigger_terms"],
        "source_note": cfg["source_note"],
    })

    # monolithic_slim_ir.json (scrubbed of reactor-specific provenance)
    _write_json(out / "monolithic_slim_ir.json", _scrub_markers(slim_ir_from_plan(plan)))

    # digest.md (reactor-neutral, synthesized)
    (out / "digest.md").write_text(_synth_digest(plan, case_id), encoding="utf-8")

    # patches/*.json from fixture (incremental path)
    fixture_path = cfg.get("fixture_patches")
    if fixture_path and Path(fixture_path).is_file():
        fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        for entry in fixture.get("patches", []):
            patch_type = entry.get("patch_type") if isinstance(entry, dict) else None
            if not patch_type or patch_type == "settings":
                continue  # settings is deterministic; no LLM few-shot needed
            _write_json(out / "patches" / f"{patch_type}.json", _anonymize_patch(entry))


def main(argv: list[str]) -> int:
    ids = argv[1:] or list(_CASES)
    for case_id in ids:
        if case_id not in _CASES:
            print(f"unknown case: {case_id}", file=sys.stderr)
            return 1
        build_case(case_id)
        print(f"built {case_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
