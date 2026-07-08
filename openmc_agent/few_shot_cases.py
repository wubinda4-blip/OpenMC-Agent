"""Gold few-shot cases for both monolithic and incremental (patch) plan paths.

Each case lives under ``data/few_shot_cases/<case_id>/`` and provides:
* ``meta.json`` — structural features + trigger terms (reactor-type agnostic),
* ``monolithic_slim_ir.json`` — a trimmed SimulationPlan (monolithic path),
* ``digest.md`` — a short structural summary,
* ``patches/<patch_type>.json`` — per-patch exemplars (incremental path).

Design constraints
------------------
* **Reactor-type agnostic.** Case ids, structural features and trigger terms
  must never name a reactor type (PWR/VERA/C5G7/...). Selection is by
  structural signature only.
* **Illustrative, not authoritative.** Few-shot content demonstrates correct
  IR/patch *structure*; it is not a source of benchmark constants or real
  loading maps (per CLAUDE.md safety boundary).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FEW_SHOT_CASES_DIR: Path = Path(__file__).resolve().parent.parent / "data" / "few_shot_cases"

# Keys stripped from a SimulationPlan when building the slim monolithic IR.
_SLIM_DROP_KEYS: frozenset[str] = frozenset({
    "source",
    "assumptions",
    "requires_human_confirmation",
    "volume_cm3",
    "macroscopic",
})

# Top-level (plan) keys stripped — runtime/eval metadata, not model structure.
_SLIM_DROP_TOP_KEYS: frozenset[str] = frozenset({
    "capability_report",
    "execution_check",
    "plot_specs",
    "expert_assumptions",
    "expert_feedback",
    "records_path",
    "output_dir",
})

# Lattice universe_pattern matrices longer than this get compacted to a
# head + ellipsis + tail so the exemplar stays prompt-sized.
_PATTERN_COMPACT_THRESHOLD = 6
_PATTERN_HEAD = 3
_PATTERN_TAIL = 1


def slim_ir_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a trimmed copy of a SimulationPlan dict for few-shot display.

    Strips metadata/runtime fields and compacts long lattice patterns so the
    exemplar stays small enough to inject into a prompt while preserving the
    structural skeleton (materials, cells, universes, lattices, core).
    """
    def _trim_node(node: Any, *, top_level: bool = False) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in _SLIM_DROP_KEYS:
                    continue
                if top_level and key in _SLIM_DROP_TOP_KEYS:
                    continue
                out[key] = _trim_node(value)
            return out
        if isinstance(node, list):
            return [_trim_node(item) for item in node]
        return node

    trimmed = _trim_node(plan, top_level=True)
    _compact_lattice_patterns(trimmed)
    return trimmed


def _compact_lattice_patterns(plan: dict[str, Any]) -> None:
    """In-place compact overly long ``universe_pattern`` matrices."""
    model = plan.get("complex_model") if isinstance(plan, dict) else None
    if not isinstance(model, dict):
        return
    lattices = model.get("lattices")
    if not isinstance(lattices, list):
        return
    for lat in lattices:
        if not isinstance(lat, dict):
            continue
        pattern = lat.get("universe_pattern")
        if not isinstance(pattern, list) or len(pattern) <= _PATTERN_COMPACT_THRESHOLD:
            continue
        head = pattern[:_PATTERN_HEAD]
        tail = pattern[-_PATTERN_TAIL:]
        lat["universe_pattern"] = [*head, ["..."], *tail]


# ---------------------------------------------------------------------------
# Structural-feature extraction (reactor-type agnostic)
# ---------------------------------------------------------------------------

_LATTICE_SIZE_RE = re.compile(r"(\d{1,2})\s*[x×]\s*(\d{1,2})")

_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pin_cell", ("pin cell", "pin-cell", "pincell", "栅元", "燃料棒")),
    ("assembly", ("assembly", "组件", "fuel assembly")),
    ("core", ("core", "全堆", "堆芯")),
)


def extract_structural_features(requirement: str) -> set[str]:
    """Extract reactor-type-agnostic structural features from a requirement.

    Used to match a new request against gold cases by structural signature
    (kind, lattice size, presence of spacer grids / reflector / control rods /
    symmetry), never by reactor-type name.
    """
    text = requirement.lower()
    feats: set[str] = set()

    for label, keys in _KIND_RULES:
        if any(k in text for k in keys):
            feats.add(label)

    for a, b in _LATTICE_SIZE_RE.findall(text):
        feats.add(f"{int(a)}x{int(b)}")

    if any(k in text for k in ("spacer grid", "spacer-grid", "格架", "定位格架", "axial overlay", "axial_overlay")):
        feats.add("axial_overlay")
    if any(k in text for k in ("reflector", "反射层")):
        feats.add("reflector")
    if any(k in text for k in ("quarter", "四分之一", "1/4", "symmetric quadrant")):
        feats.add("quarter")
    if any(k in text for k in ("control rod", "control-rod", "控制棒", "absorber", "吸收棒")):
        feats.add("control_rod")
    if any(k in text for k in ("3d", "3-d", "三维", "three-dimensional")):
        feats.add("3d")
    if any(k in text for k in ("2d", "2-d", "二维", "two-dimensional")):
        feats.add("2d")
    if any(k in text for k in ("mox", "钚", "plutonium", "mixed oxide")):
        feats.add("mox")

    return feats


# ---------------------------------------------------------------------------
# Gold-case loaders (data/few_shot_cases/<case_id>/)
# ---------------------------------------------------------------------------


def list_gold_case_ids() -> list[str]:
    """Return all configured gold case ids (sorted)."""
    if not FEW_SHOT_CASES_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in FEW_SHOT_CASES_DIR.iterdir()
        if p.is_dir() and (p / "meta.json").is_file()
    )


def load_gold_case_meta(case_id: str) -> dict[str, Any]:
    """Load ``meta.json`` (structural features + trigger terms) for a case."""
    meta_path = FEW_SHOT_CASES_DIR / case_id / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"gold case meta not found: {case_id}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_monolithic_few_shot(case_id: str) -> dict[str, Any]:
    """Load slim IR + digest for the monolithic plan path."""
    base = FEW_SHOT_CASES_DIR / case_id
    slim_path = base / "monolithic_slim_ir.json"
    if not slim_path.is_file():
        raise FileNotFoundError(f"slim IR not found for case: {case_id}")
    digest_path = base / "digest.md"
    return {
        "case_id": case_id,
        "slim_ir": json.loads(slim_path.read_text(encoding="utf-8")),
        "digest": digest_path.read_text(encoding="utf-8") if digest_path.is_file() else "",
    }


def load_patch_few_shots(
    patch_type: str,
    case_ids: list[str],
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Load per-patch exemplars for the incremental plan path.

    Returns up to ``limit`` patch dicts of the requested ``patch_type``,
    one per matching case that publishes that patch. Empty list if none.
    Truncation to the prompt token budget is the caller's responsibility.
    """
    out: list[dict[str, Any]] = []
    for case_id in case_ids:
        if len(out) >= limit:
            break
        path = FEW_SHOT_CASES_DIR / case_id / "patches" / f"{patch_type}.json"
        if not path.is_file():
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out
