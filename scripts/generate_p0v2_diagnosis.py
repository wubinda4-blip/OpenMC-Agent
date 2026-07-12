"""Generate P0-V2 before-diagnosis JSON."""
import json
from pathlib import Path

fixture = json.loads(Path("tests/fixtures/vera3_patches/vera3_3b_patches.json").read_text())
axial = next(p for p in fixture["patches"] if p["patch_type"] == "axial_layers")

pyrex_loading = next(l for l in axial["lattice_loadings"] if l["loading_id"] == "pyrex_active_loading")
pyrex_op = next(t for t in pyrex_loading["transformations"] if t["operation_kind"] == "nested_component_override")
coords = pyrex_op["target_coordinates"]

thimble_loading = next(l for l in axial["lattice_loadings"] if l["loading_id"] == "thimble_plug_loading")
thimble_op = next(t for t in thimble_loading["transformations"] if t["operation_kind"] == "nested_component_override")
thimble_coords = thimble_op["target_coordinates"]

layers_after_poison = []
for layer in axial["layers"]:
    z_min = layer["z_min_cm"]
    if z_min >= 376.441:
        loading_ids = layer.get("loading_ids") or ([layer["loading_id"]] if layer.get("loading_id") else [])
        has_pyrex = any("pyrex" in lid for lid in loading_ids)
        layers_after_poison.append({
            "layer_id": layer["layer_id"],
            "z_range": [z_min, layer["z_max_cm"]],
            "loading_ids": loading_ids,
            "has_pyrex_upper_gas": has_pyrex,
        })

diag = {
    "task": "P0-V2",
    "pyrex_poison_loading": {
        "loading_id": "pyrex_active_loading",
        "span_cm": [15.761, 376.441],
        "coordinate_count": len(coords),
    },
    "pyrex_coordinates_internal": coords,
    "pyrex_coordinates_source_1based": [[r+1, c+1] for r, c in coords],
    "poison_span_cm": [15.761, 376.441],
    "upper_gas_required_span_cm": [376.441, 397.510],
    "layers_after_poison_top": layers_after_poison,
    "thimble_span_cm": [383.31, 394.31],
    "thimble_coordinate_count": len(thimble_coords),
    "top_grid_span_cm": [386.267, 390.133],
    "missing_profile": "pyrex_upper_gas_inner_profile",
    "missing_loading": "pyrex_upper_gas_loading",
    "current_failure_mode": "After z=376.441, all 16 Pyrex positions revert to ordinary water-filled guide tubes because no upper-gas loading exists.",
    "layers_missing_upper_gas": [l["layer_id"] for l in layers_after_poison if not l["has_pyrex_upper_gas"]],
}
out = Path("data/evals/vera3_geometry/P0_V2_before_diagnosis.json")
out.write_text(json.dumps(diag, indent=2) + "\n")
print(f"Diagnosis: {len(layers_after_poison)} layers after poison top")
print(f"Missing upper-gas in: {diag['layers_missing_upper_gas']}")
