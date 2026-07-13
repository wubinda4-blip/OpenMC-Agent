"""Replace stub materials with exact VERA3 reference atom densities.

All values from VERA3_reference.md Table P3-3 (atom/barn-cm).
SCALE IDs mapped to OpenMC GNDS names.
"""
import json
from pathlib import Path

# Exact number densities from VERA3_reference.md Table P3-3
# Format: {nuclide_name: density_in_atom_barn_cm}

MATERIALS = {
    "fuel_3a": {
        "name": "UO2 Fuel 3.1% (3A)",
        "role": "fuel",
        "temperature_K": 600,
        "composition": {
            "O16": 4.57642e-02,
            "U234": 6.11864e-06,
            "U235": 7.18132e-04,
            "U236": 3.29861e-06,
            "U238": 2.21546e-02,
        },
    },
    "fuel_3b": {
        "name": "UO2 Fuel 2.619% (3B)",
        "role": "fuel",
        "temperature_K": 565,
        "composition": {
            "O16": 4.57617e-02,
            "U234": 5.09503e-06,
            "U235": 6.06733e-04,
            "U236": 2.76809e-06,
            "U238": 2.22663e-02,
        },
    },
    "helium": {
        "name": "Helium Gap",
        "role": "gas_gap",
        "composition": {"He4": 2.68714e-05},
    },
    "zircaloy4": {
        "name": "Zircaloy-4",
        "role": "cladding",
        "composition": {
            "Cr50": 3.30121e-06, "Cr52": 6.36606e-05, "Cr53": 7.21860e-06, "Cr54": 1.79686e-06,
            "Fe54": 8.68307e-06, "Fe56": 1.36306e-04, "Fe57": 3.14789e-06, "Fe58": 4.18926e-07,
            "Zr90": 2.18865e-02, "Zr91": 4.77292e-03, "Zr92": 7.29551e-03, "Zr94": 7.39335e-03, "Zr96": 1.19110e-03,
            "Sn112": 4.68066e-06, "Sn114": 3.18478e-06, "Sn115": 1.64064e-06,
            "Sn116": 7.01616e-05, "Sn117": 3.70592e-05, "Sn118": 1.16872e-04,
            "Sn119": 4.14504e-05, "Sn120": 1.57212e-04, "Sn122": 2.23417e-05, "Sn124": 2.79392e-05,
            "Hf174": 3.54138e-09, "Hf176": 1.16423e-07, "Hf177": 4.11686e-07,
            "Hf178": 6.03806e-07, "Hf179": 3.01460e-07, "Hf180": 7.76449e-07,
        },
    },
    "inconel718": {
        "name": "Inconel-718",
        "role": "grid_inconel",
        "composition": {
            "Si28": 4.04885e-03, "Si29": 2.05685e-04, "Si30": 1.35748e-04,
            "Ti46": 2.12518e-04, "Ti47": 1.91652e-04, "Ti48": 1.89901e-03, "Ti49": 1.39360e-04, "Ti50": 1.33435e-04,
            "Cr50": 6.18222e-04, "Cr52": 1.19218e-02, "Cr53": 1.35184e-03, "Cr54": 3.36501e-04,
            "Fe54": 3.61353e-04, "Fe56": 5.67247e-03, "Fe57": 1.31002e-04, "Fe58": 1.74340e-05,
            "Ni58": 4.17608e-02, "Ni60": 1.60862e-02, "Ni61": 6.99255e-04, "Ni62": 2.22953e-03, "Ni64": 5.67796e-04,
        },
    },
    "ss304": {
        "name": "SS-304",
        "role": "ss304",
        "composition": {
            "C": 3.20895e-04,
            "Si28": 1.58197e-03, "Si29": 8.03653e-05, "Si30": 5.30394e-05,
            "P31": 6.99938e-05,
            "Cr50": 7.64915e-04, "Cr52": 1.47506e-02, "Cr53": 1.67260e-03, "Cr54": 4.16346e-04,
            "Mn55": 1.75387e-03,
            "Fe54": 3.44776e-03, "Fe56": 5.41225e-02, "Fe57": 1.24992e-03, "Fe58": 1.66342e-04,
            "Ni58": 5.30854e-03, "Ni60": 2.04484e-03, "Ni61": 8.88879e-05, "Ni62": 2.83413e-04, "Ni64": 7.21770e-05,
        },
    },
    "borated_water_3a": {
        "name": "Borated Water 1300 ppm (3A)",
        "role": "coolant",
        "temperature_K": 600,
        "composition": {
            "H1": 4.96224e-02, "B10": 1.07070e-05, "B11": 4.30971e-05, "O16": 2.48112e-02,
        },
    },
    "borated_water_3b": {
        "name": "Borated Water 1066 ppm (3B)",
        "role": "coolant",
        "temperature_K": 565,
        "composition": {
            "H1": 4.96340e-02, "B10": 8.77976e-06, "B11": 3.53397e-05, "O16": 2.48170e-02,
        },
    },
    "pyrex_3b": {
        "name": "Pyrex Borosilicate Glass (3B)",
        "role": "poison",
        "composition": {
            "B10": 9.63266e-04, "B11": 3.90172e-03, "O16": 4.67761e-02,
            "Si28": 1.81980e-02, "Si29": 9.24474e-04, "Si30": 6.10133e-04,
        },
    },
    # Homogenized nozzle/plate materials from reference Table P3-3
    "lower_nozzle_3a": {
        "name": "Lower Nozzle Homogenized (3A)",
        "role": "homogenized",
        "temperature_K": 600,
        "composition": {
            "H1": 3.57661e-02, "B10": 7.70514e-06, "B11": 3.10142e-05, "C": 8.96008e-05,
            "O16": 1.78830e-02,
            "Si28": 7.90985e-04, "Si29": 4.01826e-05, "Si30": 2.65197e-05,
            "P31": 3.49969e-05,
            "Cr50": 3.82458e-04, "Cr52": 7.37532e-03, "Cr53": 8.36302e-04, "Cr54": 2.08173e-04,
            "Mn55": 8.76936e-04,
            "Fe54": 1.72388e-03, "Fe56": 2.70613e-02, "Fe57": 6.24963e-04, "Fe58": 8.31710e-05,
            "Ni58": 2.65427e-03, "Ni60": 1.02242e-03, "Ni61": 4.44439e-05, "Ni62": 1.41707e-04, "Ni64": 3.60885e-05,
        },
    },
    "upper_nozzle_3a": {
        "name": "Upper Nozzle Homogenized (3A)",
        "role": "homogenized",
        "temperature_K": 600,
        "composition": {
            "H1": 4.01211e-02, "B10": 8.65222e-06, "B11": 3.48263e-05, "C": 6.14459e-05,
            "O16": 2.00606e-02,
            "Si28": 3.02920e-04, "Si29": 1.53886e-05, "Si30": 1.01561e-05,
            "P31": 1.34026e-05,
            "Cr50": 1.46468e-04, "Cr52": 2.82449e-03, "Cr53": 3.20275e-04, "Cr54": 7.97232e-05,
            "Mn55": 3.35836e-04,
            "Fe54": 6.60188e-04, "Fe56": 1.03635e-02, "Fe57": 2.39339e-04, "Fe58": 3.18517e-05,
            "Ni58": 1.01650e-03, "Ni60": 3.91552e-04, "Ni61": 1.70205e-05, "Ni62": 5.42688e-05, "Ni64": 1.38207e-05,
        },
    },
    "lower_nozzle_3b": {
        "name": "Lower Nozzle Homogenized (3B)",
        "role": "homogenized",
        "temperature_K": 565,
        "composition": {
            "H1": 3.57744e-02, "B10": 6.32374e-06, "B11": 2.54539e-05, "C": 8.96008e-05,
            "O16": 1.78872e-02,
            "Si28": 7.90985e-04, "Si29": 4.01826e-05, "Si30": 2.65197e-05,
            "P31": 3.49969e-05,
            "Cr50": 3.82458e-04, "Cr52": 7.37532e-03, "Cr53": 8.36302e-04, "Cr54": 2.08173e-04,
            "Mn55": 8.76936e-04,
            "Fe54": 1.72388e-03, "Fe56": 2.70613e-02, "Fe57": 6.24963e-04, "Fe58": 8.31710e-05,
            "Ni58": 2.65427e-03, "Ni60": 1.02242e-03, "Ni61": 4.44439e-05, "Ni62": 1.41707e-04, "Ni64": 3.60885e-05,
        },
    },
    "upper_nozzle_3b": {
        "name": "Upper Nozzle Homogenized (3B)",
        "role": "homogenized",
        "temperature_K": 565,
        "composition": {
            "H1": 4.01305e-02, "B10": 7.09198e-06, "B11": 2.85461e-05, "C": 6.14459e-05,
            "O16": 2.00653e-02,
            "Si28": 4.41720e-04, "Si29": 2.24397e-05, "Si30": 1.48097e-05,
            "P31": 1.95438e-05,
            "Cr50": 2.13581e-04, "Cr52": 4.11869e-03, "Cr53": 4.67027e-04, "Cr54": 1.16253e-04,
            "Mn55": 4.89719e-04,
            "Fe54": 9.62690e-04, "Fe56": 1.51122e-02, "Fe57": 3.49006e-04, "Fe58": 4.64463e-05,
            "Ni58": 1.48226e-03, "Ni60": 5.70964e-04, "Ni61": 2.48194e-05, "Ni62": 7.91351e-05, "Ni64": 2.01534e-05,
        },
    },
    "core_plate_3a": {
        "name": "Core Plate Homogenized (3A)",
        "role": "homogenized",
        "temperature_K": 600,
        "composition": {
            "H1": 2.48112e-02, "B10": 5.33040e-06, "B11": 2.14555e-05, "C": 1.60447e-04,
            "O16": 1.24056e-02,
        },
    },
    "core_plate_3b": {
        "name": "Core Plate Homogenized (3B)",
        "role": "homogenized",
        "temperature_K": 565,
        "composition": {
            "H1": 2.48171e-02, "B10": 4.40970e-06, "B11": 1.77496e-05, "C": 1.60447e-04,
            "O16": 1.24085e-02,
        },
    },
}


def update_fixture(variant: str):
    path = Path(f"tests/fixtures/vera3_patches/vera3_{variant}_patches.json")
    data = json.loads(path.read_text())

    # Variant-specific material mapping
    v = variant  # "3a" or "3b"
    coolant_id = f"borated_water_{v}"
    fuel_id = f"fuel_{v}"

    # Old → new material ID mapping for cell references
    old_to_new = {
        "borated_water": coolant_id,
        "ss304": "ss304",
        "zircaloy4": "zircaloy4",
        "inconel718": "inconel718",
        "helium": "helium",
    }

    for patch in data["patches"]:
        if patch["patch_type"] != "materials":
            continue

        # Remove old stub materials and P0-V4 mixture materials
        old_ids_to_remove = {
            "borated_water",  # will be replaced by variant-specific
            "fuel",  # old fuel id
            "fuel_3a", "fuel_3b",  # might exist
        }
        # Also remove P0-V4 mixtures
        mixture_ids = {
            f"lower_nozzle_mixture_{v}", f"upper_nozzle_mixture_{v}", f"core_plate_mixture_{v}"
        }

        new_materials = []
        existing_ids = set()

        for m in patch["materials"]:
            mid = m["material_id"]
            # Skip old stubs that will be replaced
            if mid == "borated_water":
                continue
            if mid in mixture_ids:
                continue
            new_materials.append(m)
            existing_ids.add(mid)

        # Add exact materials
        materials_to_add = [
            (fuel_id, fuel_id, False),  # fuel replaces old id
            (coolant_id, coolant_id, False),
            ("helium", "helium", "helium" not in existing_ids),
            ("zircaloy4", "zircaloy4", "zircaloy4" not in existing_ids),
            ("inconel718", "inconel718", "inconel718" not in existing_ids),
            ("ss304", "ss304", False),
            (f"lower_nozzle_{v}", f"lower_nozzle_{v}", True),
            (f"upper_nozzle_{v}", f"upper_nozzle_{v}", True),
            (f"core_plate_{v}", f"core_plate_{v}", True),
        ]

        # Handle Pyrex only for 3B
        if v == "3b":
            materials_to_add.append(("pyrex_3b", "pyrex", False))

        for ref_key, mat_id, only_if_missing in materials_to_add:
            ref = MATERIALS[ref_key]
            if only_if_missing and mat_id in existing_ids:
                # Update existing material to exact
                for i, m in enumerate(new_materials):
                    if m["material_id"] == mat_id:
                        new_materials[i] = _make_exact(mat_id, ref)
                        break
            else:
                # Replace or add
                new_materials = [m for m in new_materials if m["material_id"] != mat_id]
                new_materials.append(_make_exact(mat_id, ref))
                existing_ids.add(mat_id)

        patch["materials"] = new_materials

        # Update material aliases
        if "material_aliases" not in data:
            data["material_aliases"] = {}
        data["material_aliases"]["grid_zircaloy4"] = "zircaloy4"

    # Update cell material_id references (borated_water → variant coolant)
    for patch in data["patches"]:
        if patch["patch_type"] != "universes":
            continue
        for univ in patch["universes"]:
            for cell in univ["cells"]:
                if cell.get("material_id") == "borated_water":
                    cell["material_id"] = coolant_id

    # Update axial layer fills
    for patch in data["patches"]:
        if patch["patch_type"] != "axial_layers":
            continue
        for layer in patch["layers"]:
            lid = layer["layer_id"]
            if lid == "lower_core_plate":
                layer["fill_id"] = f"core_plate_{v}"
            elif lid == "lower_nozzle":
                layer["fill_id"] = f"lower_nozzle_{v}"
            elif lid == "upper_nozzle":
                layer["fill_id"] = f"upper_nozzle_{v}"
            elif lid == "upper_core_plate":
                layer["fill_id"] = f"core_plate_{v}"

    # Update overlay material references
    for patch in data["patches"]:
        if patch["patch_type"] != "axial_overlays":
            continue
        for ov in patch["overlays"]:
            # Update total_mass_g stays but material_id mapping
            if ov.get("material_id") == "inconel718":
                ov["material_id"] = "inconel718"  # stays the same
            elif ov.get("material_id") == "grid_zircaloy4":
                ov["material_id"] = "grid_zircaloy4"  # alias resolves to zircaloy4

    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Updated {variant}: {len(new_materials)} materials")


def _make_exact(mat_id: str, ref: dict) -> dict:
    return {
        "material_id": mat_id,
        "name": ref["name"],
        "role": ref["role"],
        "temperature_K": ref.get("temperature_K"),
        "composition": ref["composition"],
        "composition_basis": "atom_density_barn_cm",
        "composition_status": "confirmed",
        "source_note": "VERA3_reference.md Table P3-3 exact atom densities (atom/barn-cm)",
        "warnings": [],
    }


for variant in ["3a", "3b"]:
    update_fixture(variant)
