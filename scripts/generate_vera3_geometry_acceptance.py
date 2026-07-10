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
    validate_rendered_vera3_geometry,
    load_vera3_reference,
)

FIXTURE_MAP = {
    "3A": ROOT / "tests/fixtures/vera3_patches/vera3_3a_patches.json",
    "3B": ROOT / "tests/fixtures/vera3_patches/vera3_3b_patches.json",
}

PITCH_CM = 1.26
LATTICE_SIZE = 17
ASSEMBLY_PITCH = 21.50


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


def _xz_positions(variant: str) -> list[dict[str, Any]]:
    if variant == "3B":
        return [
            {"row_col_1based": [1, 1], "label": "ordinary_fuel_pin", "expected_component": "fuel"},
            {"row_col_1based": [3, 6], "label": "guide_path_at_pyrex_coordinate", "expected_component": "pyrex"},
            {"row_col_1based": [3, 9], "label": "guide_path_at_thimble_coordinate", "expected_component": "thimble"},
            {"row_col_1based": [9, 9], "label": "instrument_tube", "expected_component": "instrument"},
        ]
    return [
        {"row_col_1based": [1, 1], "label": "ordinary_fuel_pin", "expected_component": "fuel"},
        {"row_col_1based": [3, 6], "label": "guide_tube_coordinate", "expected_component": "guide"},
        {"row_col_1based": [9, 9], "label": "instrument_tube", "expected_component": "instrument"},
    ]


def _row_col_to_xy(row_0: int, col_0: int) -> tuple[float, float]:
    x = (col_0 + 0.5) * PITCH_CM
    y = (LATTICE_SIZE - 1 - row_0 + 0.5) * PITCH_CM
    return x, y


def _xy_to_row_col(x: float, y: float) -> tuple[int, int]:
    col_0 = int(x / PITCH_CM)
    row_0 = LATTICE_SIZE - 1 - int(y / PITCH_CM)
    return row_0, col_0


def _generate_plots(
    outdir: Path,
    variant: str,
    plan: Any,
) -> dict[str, Any]:
    """Generate XY/XZ plot slices via subprocess in the output directory.

    Returns a dict with ``generated`` (list of file paths) and ``manifest``
    (list of metadata dicts for each plot).
    """
    import hashlib
    import shutil

    plots_dir = outdir / "plots"
    raw_dir = plots_dir / "raw"
    ann_dir = plots_dir / "annotated"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ann_dir.mkdir(parents=True, exist_ok=True)

    # Clean stale XML and PNG files to avoid reading old geometry
    for pattern in ["*.png"]:
        for f in plots_dir.rglob(pattern):
            f.unlink()
    for fname in ["geometry.xml", "materials.xml", "settings.xml", "plots.xml"]:
        stale = outdir / fname
        if stale.exists():
            pass  # keep current XML — it was just exported

    # Compute SHA256 of geometry.xml for provenance
    geom_hash = hashlib.sha256((outdir / "geometry.xml").read_bytes()).hexdigest()
    plan_json = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
    plan_hash = hashlib.sha256(plan_json.encode()).hexdigest()
    model_hash = hashlib.sha256((outdir / "model.py").read_bytes()).hexdigest()

    z_min = -55.0
    z_max = 463.937
    z_center = (z_min + z_max) / 2.0

    manifest: list[dict[str, Any]] = []
    generated: list[str] = []
    errors: list[dict[str, str]] = []

    xy_slices = _xy_z_slices(variant)
    xz_positions = _xz_positions(variant)

    # Build a plots.xml and run openmc -p in the outdir
    import openmc

    all_plots: list[Any] = []

    for sl in xy_slices:
        z = sl["z"]
        label = sl["label"]
        fname = raw_dir / f"{variant}_xy_z{z:06.1f}_{label}.png"
        plot = openmc.Plot()
        plot.filename = str(fname)
        plot.width = (ASSEMBLY_PITCH, ASSEMBLY_PITCH)
        plot.basis = "xy"
        plot.origin = (ASSEMBLY_PITCH / 2, ASSEMBLY_PITCH / 2, z)
        plot.pixels = (400, 400)
        plot.color_by = "material"
        all_plots.append(plot)
        manifest.append({
            "variant": variant,
            "basis": "xy",
            "origin_cm": [ASSEMBLY_PITCH / 2, ASSEMBLY_PITCH / 2, z],
            "width_cm": [ASSEMBLY_PITCH, ASSEMBLY_PITCH],
            "pixels": [400, 400],
            "row_col_1based": None,
            "expected_component": "",
            "geometry_xml_sha256": geom_hash,
            "simulation_plan_sha256": plan_hash,
            "generated_file": str(fname.relative_to(outdir)),
            "label": label,
        })

    for pos in xz_positions:
        r1, c1 = pos["row_col_1based"]
        r0, c0 = r1 - 1, c1 - 1
        x, y = _row_col_to_xy(r0, c0)
        fname = raw_dir / f"{variant}_xz_{pos['label']}.png"
        plot = openmc.Plot()
        plot.filename = str(fname)
        plot.width = (ASSEMBLY_PITCH, z_max - z_min)
        plot.basis = "xz"
        plot.origin = (x, y, z_center)
        plot.pixels = (400, 1000)
        plot.color_by = "material"
        all_plots.append(plot)
        manifest.append({
            "variant": variant,
            "basis": "xz",
            "origin_cm": [x, y, z_center],
            "width_cm": [ASSEMBLY_PITCH, z_max - z_min],
            "pixels": [400, 1000],
            "row_col_1based": [r1, c1],
            "expected_component": pos.get("expected_component", ""),
            "geometry_xml_sha256": geom_hash,
            "simulation_plan_sha256": plan_hash,
            "generated_file": str(fname.relative_to(outdir)),
            "label": pos["label"],
        })

    # Export plots.xml and run openmc -p in outdir
    plots_xml = openmc.Plots(all_plots)
    plots_xml.export_to_xml(str(outdir / "plots.xml"))

    proc = subprocess.run(
        ["openmc", "-p"],
        cwd=str(outdir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        errors.append({
            "variant": variant,
            "plot_name": "openmc -p",
            "exception": f"returncode={proc.returncode}\nstderr={proc.stderr[:500]}",
            "cwd": str(outdir),
        })
    else:
        for entry in manifest:
            fname = outdir / entry["generated_file"]
            if fname.exists():
                generated.append(str(fname))
            else:
                errors.append({
                    "variant": variant,
                    "plot_name": entry["label"],
                    "exception": f"expected file not found: {fname}",
                    "cwd": str(outdir),
                })

    # Generate annotated plots with matplotlib
    try:
        _generate_annotated_plots(raw_dir, ann_dir, manifest, variant)
    except Exception as exc:
        errors.append({
            "variant": variant,
            "plot_name": "annotated",
            "exception": str(exc),
            "cwd": str(ann_dir),
        })

    manifest_path = outdir / "plot_manifest.json"
    manifest_path.write_text(json.dumps({
        "plots": manifest,
        "geometry_xml_sha256": geom_hash,
        "simulation_plan_sha256": plan_hash,
        "model_py_sha256": model_hash,
        "errors": errors,
    }, indent=2))

    return {
        "generated": generated,
        "manifest": manifest,
        "errors": errors,
        "geometry_xml_sha256": geom_hash,
        "model_py_sha256": model_hash,
        "simulation_plan_sha256": plan_hash,
    }


_KEY_Z_LINES = [
    6.053, 10.281, 11.951, 15.761, 376.441, 377.711,
    379.381, 383.31, 394.31, 395.381, 397.510,
]


def _generate_annotated_plots(
    raw_dir: Path,
    ann_dir: Path,
    manifest: list[dict[str, Any]],
    variant: str,
) -> None:
    """Add axes, titles, and key z-lines to raw OpenMC PNGs."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    for entry in manifest:
        raw_path = raw_dir / Path(entry["generated_file"]).name
        if not raw_path.exists():
            continue
        img = mpimg.imread(str(raw_path))
        fig, ax = plt.subplots(figsize=(6, 6) if entry["basis"] == "xy" else (5, 10))

        origin = entry["origin_cm"]
        width = entry["width_cm"]

        if entry["basis"] == "xy":
            extent = [
                origin[0] - width[0] / 2, origin[0] + width[0] / 2,
                origin[1] - width[1] / 2, origin[1] + width[1] / 2,
            ]
            ax.imshow(img, extent=extent, aspect="equal", origin="lower")
            ax.set_xlabel("x [cm]")
            ax.set_ylabel("y [cm]")
            ax.set_title(
                f"{variant} XY z={origin[2]:.1f} cm — {entry.get('label', '')}",
                fontsize=9,
            )
        else:
            extent = [
                origin[0] - width[0] / 2, origin[0] + width[0] / 2,
                origin[2] - width[1] / 2, origin[2] + width[1] / 2,
            ]
            ax.imshow(img, extent=extent, aspect="auto", origin="lower")
            ax.set_xlabel("x [cm]")
            ax.set_ylabel("z [cm]")
            rc = entry.get("row_col_1based")
            rc_str = f" [{rc[0]},{rc[1]}]" if rc else ""
            ax.set_title(
                f"{variant} XZ{rc_str} — {entry.get('label', '')}",
                fontsize=9,
            )
            for z_val in _KEY_Z_LINES:
                ax.axhline(y=z_val, color="yellow", linewidth=0.4, alpha=0.6)

        ann_path = ann_dir / Path(entry["generated_file"]).name
        fig.tight_layout()
        fig.savefig(str(ann_path), dpi=150)
        plt.close(fig)


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
        "plan_level_issues": [
            {"code": i.code, "severity": i.severity, "message": i.message}
            for i in all_issues
        ],
        "rendered_geometry_issues": [],
        "plot_generation_issues": [],
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
            rendered_issues = validate_rendered_vera3_geometry(
                outdir, variant=args.variant, contract=contract,
            )
            report["rendered_geometry_issues"] = [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in rendered_issues
            ]
            report["error_count"] += sum(
                1 for i in rendered_issues if i.severity == "error"
            )
            report["warning_count"] += sum(
                1 for i in rendered_issues if i.severity == "warning"
            )
            print(f"  XML exported: {xml_files}")
    else:
        print(f"[4/6] Skipping XML export (--export-xml not set)")

    if args.plot and report.get("xml_export", {}).get("ok"):
        print(f"[5/6] Generating plots")
        try:
            plot_result = _generate_plots(outdir, args.variant, plan)
            report["plots"] = plot_result["generated"]
            report["plot_errors"] = plot_result.get("errors", [])
            report["plot_generation_issues"] = plot_result.get("errors", [])
            report["geometry_xml_sha256"] = plot_result["geometry_xml_sha256"]
            report["model_py_sha256"] = plot_result["model_py_sha256"]
            report["simulation_plan_sha256"] = plot_result["simulation_plan_sha256"]
            print(f"  Generated {len(plot_result['generated'])} plots")
            if plot_result.get("errors"):
                print(f"  Plot errors: {len(plot_result['errors'])}")
        except Exception as exc:
            import traceback
            report["plots_error"] = str(exc)
            report["plots_traceback"] = traceback.format_exc()
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
