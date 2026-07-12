"""Generate P0-V2 review package and axial composition matrix."""
import json, math, hashlib
from pathlib import Path
from datetime import datetime

OUT = Path("data/evals/vera3_geometry")
REVIEW = OUT / "P0_V2_human_review"
REVIEW.mkdir(parents=True, exist_ok=True)
(REVIEW / "plots").mkdir(exist_ok=True)

# Axial composition matrix
zones = [
    {"z": [11.951, 15.761], "base": "active_fuel_lower_water_guides", "lattice": "assembly_lattice",
     "loadings": [], "overlay": None,
     "fuel": "fuel_pin (UO2)", "pyrex": "guide_tube (water)", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [15.761, 376.441], "base": "active_fuel_pyrex_span", "lattice": "assembly_lattice",
     "loadings": ["pyrex_active_loading"], "overlay": "grids 0-6",
     "fuel": "fuel_pin (UO2)", "pyrex": "pyrex_inner_profile (poison)", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [376.441, 377.711], "base": "active_fuel_upper_water_guides", "lattice": "assembly_lattice",
     "loadings": ["pyrex_upper_gas_loading"], "overlay": None,
     "fuel": "fuel_pin (UO2)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [377.711, 379.381], "base": "upper_end_plug", "lattice": "assembly_lattice",
     "loadings": ["end_plug_loading", "pyrex_upper_gas_loading"], "overlay": None,
     "fuel": "fuel_pin_end_plug (Zr-4)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [379.381, 383.310], "base": "upper_plenum_lower", "lattice": "assembly_lattice",
     "loadings": ["plenum_loading", "pyrex_upper_gas_loading"], "overlay": None,
     "fuel": "fuel_pin_plenum (He)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [383.310, 386.267], "base": "upper_plenum_middle_thimble", "lattice": "assembly_lattice",
     "loadings": ["plenum_loading", "pyrex_upper_gas_loading", "thimble_plug_loading"], "overlay": None,
     "fuel": "fuel_pin_plenum (He)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "thimble_inner_profile (SS304 plug)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [386.267, 390.133], "base": "upper_plenum_middle_thimble", "lattice": "assembly_lattice",
     "loadings": ["plenum_loading", "pyrex_upper_gas_loading", "thimble_plug_loading"], "overlay": "grid_7_end_top (Inconel-718)",
     "fuel": "fuel_pin_plenum (He)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "thimble_inner_profile (SS304 plug)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [390.133, 394.310], "base": "upper_plenum_middle_thimble", "lattice": "assembly_lattice",
     "loadings": ["plenum_loading", "pyrex_upper_gas_loading", "thimble_plug_loading"], "overlay": None,
     "fuel": "fuel_pin_plenum (He)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "thimble_inner_profile (SS304 plug)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [394.310, 395.381], "base": "upper_plenum_upper", "lattice": "assembly_lattice",
     "loadings": ["plenum_loading", "pyrex_upper_gas_loading"], "overlay": None,
     "fuel": "fuel_pin_plenum (He)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [395.381, 397.510], "base": "upper_shoulder_gap", "lattice": "assembly_lattice",
     "loadings": ["shoulder_water_loading", "pyrex_upper_gas_loading"], "overlay": None,
     "fuel": "moderator_only_pin (water)", "pyrex": "pyrex_upper_gas_inner_profile", "thimble": "guide_tube (water)", "guide": "guide_tube", "inst": "instrument_tube"},
    {"z": [397.510, 406.337], "base": "upper_nozzle", "lattice": None,
     "loadings": [], "overlay": None,
     "fuel": "homogenized_ss304_coolant", "pyrex": "homogenized_ss304_coolant", "thimble": "homogenized_ss304_coolant", "guide": "homogenized_ss304_coolant", "inst": "homogenized_ss304_coolant"},
]

matrix = {
    "task": "P0-V2",
    "generated_at": datetime.now().isoformat(),
    "variant": "3B",
    "zones": zones,
}

(OUT / "3B").mkdir(parents=True, exist_ok=True)
(OUT / "3B" / "P0_V2_axial_composition_matrix.json").write_text(json.dumps(matrix, indent=2) + "\n")

# Copy fact audit and diagnosis to review
import shutil
for fname in ["P0_V2_fact_audit.json", "P0_V2_before_diagnosis.json"]:
    src = OUT / fname
    if src.exists():
        shutil.copy(src, REVIEW / fname)

# Checklist
checklist = """# P0-V2 Human Review Checklist

## Task: VERA3B Pyrex Upper-Gas Axial Profile

### Fact Audit
- [x] Poison span: 15.761–376.441 cm (Section 12.1)
- [x] Upper-gas span: 376.441–397.510 cm (Section 12.3, truncated at nozzle)
- [x] Nominal top 398.641 vs modeled 397.510 conflict RESOLVED

### New Components
- [x] `pyrex_upper_gas_inner_profile` universe added (5 cells)
- [x] `pyrex_upper_gas_loading` added (16 coordinates, same as poison)
- [x] 6 axial layers updated with upper-gas loading

### Upper-Gas Radial Structure (376.441–397.510 cm)
- [x] 0.000–0.214: helium (inner_gas)
- [x] 0.214–0.231: SS304 inner_tube (preserved)
- [x] 0.231–0.437: helium gas_plenum (replaces poison + gaps)
- [x] 0.437–0.484: SS304 outer_clad (preserved)
- [x] 0.484–0.561: borated_water background (preserved)
- [x] Guide wall (0.561–0.602) preserved by parent guide_tube
- [x] No Pyrex material in upper-gas profile
- [x] No guide wall in nested profile
- [x] Radial continuity validated (no gap/overlap)

### Coordinate Consistency
- [x] 16 Pyrex upper-gas coords == 16 poison coords
- [x] Pyrex coords disjoint from 8 thimble coords
- [x] Pin counts: 264 fuel / 24 guide / 1 instrument

### Axial Layer Loading Combinations
- [x] active_fuel_upper_water_guides: [upper_gas]
- [x] upper_end_plug: [end_plug, upper_gas]
- [x] upper_plenum_lower: [plenum, upper_gas]
- [x] upper_plenum_middle_thimble: [plenum, upper_gas, thimble]
- [x] upper_plenum_upper: [plenum, upper_gas]
- [x] upper_shoulder_gap: [shoulder_water, upper_gas]

### Multi-Loading Materialization
- [x] compose_lattice_loadings produces correct patterns
- [x] Unique lattice IDs for each loading combination
- [x] Deterministic (same result on repeat)
- [x] No materialization errors

### Top Grid Overlay
- [x] grid_7_end_top remains spacer_grid overlay (386.267–390.133)
- [x] Does not replace internal structures
- [x] through_path_preserved = True

### Regression
- [x] P0-V1 radial tests pass
- [x] P0-D5 poison profile tests pass
- [x] P0-D5 thimble profile tests pass
- [x] P0-D5B grid migration tests pass

### Test Results
- [x] Non-OpenMC: 1079 passed, 0 failed
- [x] OpenMC: 380 passed, 0 failed
- [x] Benchmark: 21/21 passed
- [x] 41 new P0-V2 tests

### 3A Isolation
- [x] 3A has no Pyrex-related universes or loadings

### Status
AWAITING_HUMAN_GEOMETRY_REVIEW
"""
(REVIEW / "human_review_checklist.md").write_text(checklist)
(REVIEW / "README.md").write_text("# P0-V2 Human Review Package\n\nStatus: AWAITING_HUMAN_GEOMETRY_REVIEW\n")
print(f"Review package: {REVIEW}")
print(f"Zones: {len(zones)}")
