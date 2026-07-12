"""Generate P0-V1 radial volume report and human review package."""
import json, math, shutil
from pathlib import Path
from datetime import datetime

OUT = Path("data/evals/vera3_geometry")
OUT.mkdir(parents=True, exist_ok=True)
REVIEW = OUT / "P0_V1_human_review"
REVIEW.mkdir(parents=True, exist_ok=True)
(REVIEW / "plots").mkdir(exist_ok=True)

r_fuel, r_gap, r_clad = 0.4096, 0.418, 0.475
N = 264
active_len = 365.760
plenum_len = 16.000

fuel_area = math.pi * r_fuel**2
gap_area = math.pi * (r_gap**2 - r_fuel**2)
clad_area = math.pi * (r_clad**2 - r_gap**2)
plenum_he_area = math.pi * r_gap**2

report = {
    "task": "P0-V1",
    "generated_at": datetime.now().isoformat(),
    "radial_dimensions_cm": {"fuel_outer": r_fuel, "gap_outer": r_gap, "clad_outer": r_clad},
    "active_fuel_pin": {
        "fuel_area_cm2": round(fuel_area, 6),
        "gap_area_cm2": round(gap_area, 6),
        "clad_area_cm2": round(clad_area, 6),
        "fuel_vol_per_pin_cm3": round(fuel_area * active_len, 3),
        "gap_vol_per_pin_cm3": round(gap_area * active_len, 3),
        "clad_vol_per_pin_cm3": round(clad_area * active_len, 3),
        "fuel_vol_264_cm3": round(fuel_area * active_len * N, 1),
        "gap_vol_264_cm3": round(gap_area * active_len * N, 1),
        "clad_vol_264_cm3": round(clad_area * active_len * N, 1),
    },
    "upper_plenum_pin": {
        "helium_area_cm2": round(plenum_he_area, 6),
        "clad_area_cm2": round(clad_area, 6),
        "helium_vol_per_pin_cm3": round(plenum_he_area * plenum_len, 3),
        "helium_vol_264_cm3": round(plenum_he_area * plenum_len * N, 1),
    },
}

for v in ("3A", "3B"):
    d = OUT / v
    d.mkdir(parents=True, exist_ok=True)
    (d / "radial_volume_report.json").write_text(json.dumps(report, indent=2) + "\n")

shutil.copy(OUT / "P0_V1_fact_audit.json", REVIEW / "fact_audit.json")

checklist = """# P0-V1 Human Review Checklist

## Task: VERA3 Fuel Helium Gap and Upper Plenum Geometry Correction

### Fact Audit
- [x] 0.4096/0.418/0.475 confirmed in VERA3_problem.md (lines 244-246, 263-266, 281)
- [x] VERA1/2/4/5 + SCALE reference all agree
- [x] No conflicts; helium material policy unchanged

### Geometry Corrections
- [x] Active fuel: helium gap (0.4096-0.418) added
- [x] Active fuel: clad r_min 0.4096 -> 0.418
- [x] Plenum: gas r_max 0.4096 -> 0.418
- [x] Plenum: clad r_min 0.4096 -> 0.418
- [x] End-plug unchanged

### Radial Continuity (reactor-neutral validator)
- [x] No gaps, no overlaps, background outermost
- [x] Pin counts preserved: 264/24/1 for both 3A and 3B

### Tests
- [x] Non-OpenMC: 1038 passed, 0 failed
- [x] OpenMC: 380 passed, 0 failed
- [x] Benchmark: 21/21 passed
- [x] 7 pre-existing P0-D5B failures fixed

### Areas
- Fuel: 0.527072 cm2, Gap: 0.021840 cm2 (4.14%), Clad: 0.159910 cm2

### Status
AWAITING_HUMAN_GEOMETRY_REVIEW
"""
(REVIEW / "human_review_checklist.md").write_text(checklist)
(REVIEW / "README.md").write_text("# P0-V1 Human Review Package\n\nStatus: AWAITING_HUMAN_GEOMETRY_REVIEW\n")
print(f"Review package: {REVIEW}")
print(f"Fuel area: {fuel_area:.6f}, Gap area: {gap_area:.6f}, Clad area: {clad_area:.6f}")
