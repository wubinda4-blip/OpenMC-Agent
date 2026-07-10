#!/usr/bin/env python3
"""Generate a VERA3 geometry acceptance pack for human review.

Assembles a VERA3 3A or 3B plan from patch fixtures, runs acceptance checks,
exports OpenMC XML, generates XY/XZ plot slices, and optionally runs
``openmc --geometry-debug``. The output is a self-contained directory an
expert can review to verify geometric correctness.

Usage::

    python scripts/generate_vera3_geometry_acceptance.py \\
        --variant 3B \\
        --export-xml \\
        --plot \\
        --geometry-debug \\
        --out data/evals/vera3_geometry/3B

No long keff runs are performed; this script only validates geometry and XML.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.renderers.assembly import RectAssemblyRenderer

from tests.helpers.vera3_acceptance import (
    CONTRACT_PATH,
    load_vera3_geometry_contract,
    diagnose_vera3_component_geometry,
    validate_vera3_plan_structure,
    load_vera3_reference,
)

FIXTURE_MAP = {
    "3A": ROOT / "tests/fixtures/vera3_patches/vera3_3a_patches.json",
    "3B": ROOT / "tests/fixtures/vera3_patches/vera3_3b_patches.json",
}

PITCH_CM = 1.26
LATTICE_SIZE = 17


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate VERA3 geometry acceptance pack")
    p.add_argument("--variant", choices=["3A", "3B"], required=True)
    p.add_argument("--patch-fixture", type=Path, default=None,
                   help="Override path to patch fixture JSON")
    p.add_argument("--contract", type=Path, default=CONTRACT_PATH,
                   help="Path to geometry contract JSON")
    p.add_argument("--out", type=Path, required=True, help="Output directory")
    p.add_argument("--export-xml", action="store_true", help="Export OpenMC XML")
    p.add_argument("--plot", action="store_true", help="Generate plot PNGs")
    p.add_argument("--geometry-debug", action="store_true",
                   help="Run openmc --geometry-debug")
    return p.parse_args()


def _load_plan(variant: str, fixture_path: Path) -> Any:
    with open(fixture_path) as f:
        data = json.load(f)
    patches = [parse_patch_content(p["patch_type"], p) for p in data["patches"]]
    result = assemble_simulation_plan_from_patches(patches)
    if not result.ok:
        raise RuntimeError(f"Assembly failed: {[i.message for i in result.issues if i.severity == 'error']}")
    return result.plan


def _xy_z_slices(variant: str) -> list[dict[str, Any]]:
    base = [
        {"z": 10.8, "label": "lower_fuel_end_plug"},
        {"z": 14.0, "label": "active_fuel_lower_grid"},
        {"z": 100.0, "label": "ordinary_active_fuel"},
        {"z": 378.5, "label": "upper_fuel_end_plug"},
        {"z": 382.0, "label": "fuel_pin_upper_plenum"},
        {"z": 389.0, "label": "upper_plenum_top_grid"},
        {"z": 396.0, "label": "upper_shoulder_coolant"},
    ]
    if variant == "3B":
        base.extend([
            {"z": 16.0, "label": "pyrex_poison_beginning"},
            {"z": 100.0, "label": "pyrex_poison_span"},
            {"z": 376.0, "label": "pyrex_poison_near_top"},
            {"z": 377.0, "label": "after_poison_span"},
            {"z": 384.0, "label": "thimble_plug_interval"},
            {"z": 389.0, "label": "thimble_plug_top_grid"},
            {"z": 394.5, "label": "after_thimble_interval"},
        ])
    return base


def _xz_positions() -> list[dict[str, Any]]:
    return [
        {"row_col_1based": [9, 9], "label": "ordinary_fuel_pin"},
        {"row_col_1based": [3, 6], "label": "pyrex_coordinate"},
        {"row_col_1based": [3, 9], "label": "thimble_coordinate"},
        {"row_col_1based": [5, 5], "label": "ordinary_guide_tube"},
        {"row_col_1based": [9, 9], "label": "instrument_tube"},
    ]


def _row_col_to_xy(row_0: int, col_0: int) -> tuple[float, float]:
    half = LATTICE_SIZE / 2.0
    x = (col_0 - half + 0.5) * PITCH_CM
    y = -(row_0 - half + 0.5) * PITCH_CM
    return x, y


def _generate_plots(outdir: Path, variant: str, model_py: Path) -> list[str]:
    plots_dir = outdir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    generated = []

    import openmc

    for sl in _xy_z_slices(variant):
        z = sl["z"]
        label = sl["label"]
        fname = plots_dir / f"{variant}_xy_z{z:06.1f}_{label}.png"
        try:
            plot = openmc.Plot()
            plot.filename = str(fname)
            plot.width = (21.5, 21.5)
            plot.basis = "xy"
            plot.origin = (21.5 / 2, 21.5 / 2, z)
            plot.pixels = (400, 400)
            openmc.plot_inline([plot])
            generated.append(str(fname))
        except Exception:
            pass

    for pos in _xz_positions():
        r1, c1 = pos["row_col_1based"]
        r0, c0 = r1 - 1, c1 - 1
        x, y = _row_col_to_xy(r0, c0)
        fname = plots_dir / f"{variant}_xz_{pos['label']}.png"
        try:
            plot = openmc.Plot()
            plot.filename = str(fname)
            plot.width = (21.5, 519.0)
            plot.basis = "xz"
            plot.origin = (x, y, 204.0)
            plot.pixels = (400, 1000)
            openmc.plot_inline([plot])
            generated.append(str(fname))
        except Exception:
            pass

    return generated


def _run_geometry_debug(outdir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"ran": False}
    try:
        proc = subprocess.run(
            ["openmc", "--geometry-debug"],
            cwd=str(outdir),
            capture_output=True, text=True, timeout=120,
        )
        result["ran"] = True
        result["returncode"] = proc.returncode
        stderr = proc.stderr
        stdout = proc.stdout
        result["overlap_count"] = stderr.lower().count("overlap")
        result["lost_particle_count"] = stderr.lower().count("lost particle")
        result["undefined_region_errors"] = stderr.lower().count("undefined region")
        result["duplicate_errors"] = stderr.lower().count("duplicate")
        debug_file = outdir / "geometry_debug.log"
        debug_file.write_text(proc.stdout + "\n--- STDERR ---\n" + proc.stderr)
    except FileNotFoundError:
        result["error"] = "openmc not found"
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    return result


def main() -> int:
    args = _parse_args()
    outdir = args.out
    outdir.mkdir(parents=True, exist_ok=True)
    outdir = outdir.resolve()

    fixture_path = args.patch_fixture or FIXTURE_MAP[args.variant]
    contract = load_vera3_geometry_contract(args.contract)

    print(f"[1/6] Loading plan from {fixture_path}")
    plan = _load_plan(args.variant, fixture_path)

    plan_json = plan.model_dump(mode="json")
    (outdir / "simulation_plan.json").write_text(json.dumps(plan_json, indent=2))

    print(f"[2/6] Running acceptance checks")
    reference = load_vera3_reference()
    acceptance_issues = validate_vera3_plan_structure(plan, reference, variant=args.variant)
    diag_issues = diagnose_vera3_component_geometry(plan, contract, variant=args.variant)

    all_issues = acceptance_issues + diag_issues
    report: dict[str, Any] = {
        "variant": args.variant,
        "fixture": str(fixture_path),
        "contract": str(args.contract),
        "issues": [
            {"code": i.code, "severity": i.severity, "message": i.message}
            for i in all_issues
        ],
        "error_count": sum(1 for i in all_issues if i.severity == "error"),
        "warning_count": sum(1 for i in all_issues if i.severity == "warning"),
        "pyrex_upper_profile_unresolved": True,
        "note": "Pyrex upper axial profile conflict (376.441 vs 398.641 cm) is deliberately retained; not benchmark-accurate.",
    }
    (outdir / "geometry_acceptance_report.json").write_text(json.dumps(report, indent=2))

    summary_lines = [
        f"# VERA3 {args.variant} Geometry Acceptance Summary",
        "",
        f"- Errors: {report['error_count']}",
        f"- Warnings: {report['warning_count']}",
        f"- Pyrex upper profile conflict: **UNRESOLVED** (deliberately retained)",
        "",
        "## Issues",
        "",
    ]
    for i in all_issues:
        summary_lines.append(f"- [{i.severity.upper()}] {i.code}: {i.message}")
    summary_lines.append("")
    (outdir / "geometry_acceptance_summary.md").write_text("\n".join(summary_lines))

    print(f"[3/6] Rendering model.py")
    renderer = RectAssemblyRenderer()
    rr = renderer.render(plan, outdir)
    if rr.renderability not in ("exportable", "runnable"):
        print(f"  WARNING: renderability={rr.renderability}")

    if args.export_xml:
        print(f"[4/6] Exporting XML")
        model_py = outdir / "model.py"
        proc = subprocess.run(
            [sys.executable, str(model_py)],
            cwd=str(outdir), capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            report["xml_export"] = {"ok": False, "stderr": proc.stderr[:500]}
            print(f"  XML export FAILED: {proc.stderr[:200]}")
        else:
            xml_files = sorted(f.name for f in outdir.glob("*.xml"))
            report["xml_export"] = {"ok": True, "files": xml_files}
            print(f"  XML exported: {xml_files}")
    else:
        print(f"[4/6] Skipping XML export (--export-xml not set)")

    if args.plot and report.get("xml_export", {}).get("ok"):
        print(f"[5/6] Generating plots")
        try:
            plot_files = _generate_plots(outdir, args.variant, outdir / "model.py")
            report["plots"] = plot_files
            print(f"  Generated {len(plot_files)} plots")
        except Exception as exc:
            report["plots_error"] = str(exc)
            print(f"  Plot generation failed: {exc}")
    else:
        print(f"[5/6] Skipping plots")

    if args.geometry_debug and report.get("xml_export", {}).get("ok"):
        print(f"[6/6] Running geometry debug")
        debug_result = _run_geometry_debug(outdir)
        report["geometry_debug"] = debug_result
        if debug_result.get("ran"):
            print(f"  Overlaps: {debug_result.get('overlap_count', '?')}")
            print(f"  Lost particles: {debug_result.get('lost_particle_count', '?')}")
    else:
        print(f"[6/6] Skipping geometry debug")

    (outdir / "geometry_acceptance_report.json").write_text(json.dumps(report, indent=2))

    if report["error_count"] > 0:
        print(f"\nDONE with {report['error_count']} errors")
    else:
        print(f"\nDONE: 0 errors, {report['warning_count']} warnings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
