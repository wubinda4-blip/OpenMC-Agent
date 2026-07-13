"""Update 3A/3B spacer-grid overlays from homogenized_open_region to mass_conserving_outer_frame."""
import json
from pathlib import Path

END_MASS = 1017.0
MID_MASS = 875.0
CELL_COUNT = 289

END_NOTE = "VERA3 Problem Spec Section 9: Inconel-718 end grid, 1017 g, height 3.866 cm"
MID_NOTE = "VERA3 Problem Spec Section 9: Zircaloy-4 mid grid, 875 g, height 3.810 cm"

for fname in ["vera3_3a_patches.json", "vera3_3b_patches.json"]:
    path = Path("tests/fixtures/vera3_patches") / fname
    data = json.loads(path.read_text())
    changed = False
    for patch in data.get("patches", []):
        if patch.get("patch_type") != "axial_overlays":
            continue
        for ov in patch.get("overlays", []):
            if ov.get("geometry_mode") != "homogenized_open_region":
                continue
            ov["geometry_mode"] = "mass_conserving_outer_frame"
            is_end = "end" in ov["overlay_id"]
            ov["total_mass_g"] = END_MASS if is_end else MID_MASS
            ov["cell_count"] = CELL_COUNT
            ov["source_note"] = END_NOTE if is_end else MID_NOTE
            ov["material_density_source"] = (
                "material:inconel718.density_g_cm3" if is_end
                else "material:zircaloy4.density_g_cm3"
            )
            changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Updated {fname}")
    else:
        print(f"No changes needed in {fname}")
