"""Update 3A/3B fixtures: add nozzle/core-plate mixture materials and update layer fills."""
import json, math
from pathlib import Path

# Source facts
ASSEMBLY_PITCH = 21.50
ASSEMBLY_AREA = ASSEMBLY_PITCH ** 2
SS304_MASS = 6250.0
SS304_DENSITY = 8.00
LOWER_NOZZLE_H = 6.053
UPPER_NOZZLE_H = 8.827

# Compute volume fractions
v_ss304 = SS304_MASS / SS304_DENSITY  # 781.25 cm3
v_lower_total = ASSEMBLY_AREA * LOWER_NOZZLE_H
v_upper_total = ASSEMBLY_AREA * UPPER_NOZZLE_H

f_lower_ss304 = v_ss304 / v_lower_total
f_lower_coolant = 1.0 - f_lower_ss304
f_upper_ss304 = v_ss304 / v_upper_total
f_upper_coolant = 1.0 - f_upper_ss304

print(f"Lower nozzle: SS304={f_lower_ss304:.10f}, coolant={f_lower_coolant:.10f}")
print(f"Upper nozzle: SS304={f_upper_ss304:.10f}, coolant={f_upper_coolant:.10f}")
print(f"Core plate:   SS304=0.5, coolant=0.5")

for variant in ["3a", "3b"]:
    vupper = variant.upper()
    path = Path(f"tests/fixtures/vera3_patches/vera3_{variant}_patches.json")
    data = json.loads(path.read_text())

    for patch in data["patches"]:
        if patch["patch_type"] != "materials":
            continue
        # Check if mixtures already exist
        existing_ids = {m["material_id"] for m in patch["materials"]}
        mixtures_to_add = []
        for name, f_ss, f_cool, extra in [
            (f"lower_nozzle_mixture_{variant}", f_lower_ss304, f_lower_coolant,
             f"Lower nozzle homogenized mixture: SS304 ({SS304_MASS}g) + coolant, height={LOWER_NOZZLE_H}cm"),
            (f"upper_nozzle_mixture_{variant}", f_upper_ss304, f_upper_coolant,
             f"Upper nozzle homogenized mixture: SS304 ({SS304_MASS}g) + coolant, height={UPPER_NOZZLE_H}cm"),
            (f"core_plate_mixture_{variant}", 0.5, 0.5,
             "Core plate homogenized mixture: 50/50 SS304 + coolant"),
        ]:
            if name not in existing_ids:
                mixtures_to_add.append({
                    "material_id": name,
                    "name": f"{' '.join(w.capitalize() for w in name.split('_'))}",
                    "role": "homogenized_mixture",
                    "composition_status": "derived_from_mixture",
                    "mixture_components": [
                        {"material_id": "ss304", "volume_fraction": f_ss},
                        {"material_id": "borated_water", "volume_fraction": f_cool},
                    ],
                    "variant_scope": vupper,
                    "derivation_method": "volume_fraction_mixture",
                    "source_note": extra,
                    "warnings": [],
                })
        patch["materials"].extend(mixtures_to_add)
        print(f"Added {len(mixtures_to_add)} mixtures to {variant}")

    # Update axial layer fills
    for patch in data["patches"]:
        if patch["patch_type"] != "axial_layers":
            continue
        for layer in patch["layers"]:
            lid = layer["layer_id"]
            if lid == "lower_core_plate":
                layer["fill_id"] = f"core_plate_mixture_{variant}"
            elif lid == "lower_nozzle":
                layer["fill_id"] = f"lower_nozzle_mixture_{variant}"
            elif lid == "upper_nozzle":
                layer["fill_id"] = f"upper_nozzle_mixture_{variant}"
            elif lid == "upper_core_plate":
                layer["fill_id"] = f"core_plate_mixture_{variant}"
        print(f"Updated layer fills for {variant}")

    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {path}")
