# Source Strategy Rendering Contract

This document defines the reactor-neutral semantics of `source_strategy` and how
it flows from the settings patch through the assembler, validator, renderer, and
runtime repair loop.

## Consumption Chain

```
SettingsPatch.source_strategy
  → PlanPatchEnvelope.content
  → PlanBuildState
  → assembler: ComplexModelSpec.settings.source_strategy
  → renderer: _render_source_block(spec)
  → openmc.IndependentSource(space=Box(...))
  → settings.xml <source><space><box .../></space></source>
```

The runtime repair oracle (`diagnose_source_runtime_failure` in
`runtime_repair.py`) is the only consumer that reads `source_strategy` from the
patch content. The renderer and validator read it from
`ComplexModelSpec.settings.source_strategy`.

## Strategy Semantics

### active_fuel_box (default)

- **x/y**: assembly/core lattice footprint from `assembly_xy_bounds`.
- **z**: active-fuel z-range from `active_fuel_z_bounds` (lattice-filled axial layers only).
- **only_fissionable**: `True` (from `source_requires_fissionable_constraint`).
- Does NOT include nozzle, plenum, core plate, or other non-fuel axial regions.
- If no active-fuel layer is found, falls back to the full axial domain with a warning.

### assembly_box

- **x/y**: assembly/core lattice footprint (same as active_fuel_box).
- **z**: full axial domain (min/max of all `axial_layers.z_min_cm/z_max_cm`).
- **only_fissionable**: per `source_requires_fissionable_constraint` field.
- Must produce a **different** z-range from `active_fuel_box` when the model has non-fuel axial layers.
- Must NOT internally re-bind to active fuel.

### manual

- Requires `manual_source_bounds_cm = [x_min, x_max, y_min, y_max, z_min, z_max]`.
- If bounds are missing → validation blocker (`runtime.manual_source_bounds_missing`).
- Uses the explicit bounds exactly; renderer, validator, and repair all honour them.
- Must not silently fall back to `active_fuel_box`.
- The `manual_source_bounds_cm` field is **protected**: LLM runtime repair must not edit the numerical values.

### unknown

- Produces validation blocker (`runtime.unknown_source_strategy`).
- Must not be silently interpreted in the renderer.
- Runtime repair may deterministically switch it to `active_fuel_box`.

## Runtime Source Repair

The deterministic source oracle (`propose_source_binding_repair`) only changes:
- `/source_strategy` → `"active_fuel_box"`
- `/source_requires_fissionable_constraint` → `True`

It does NOT:
- Write any benchmark-specific z-range constants.
- Modify `manual_source_bounds_cm` values.
- Touch materials, density, axial layers, universes, pin map, or geometry.

## settings.xml Round-Trip

`source_rendering.py` provides:
- `inspect_rendered_source_settings(path)` — parse source box from settings.xml.
- `compare_source_settings_to_plan(rendered, model)` — verify rendered bounds match declared strategy.
- `source_rendering_report(before, after, model)` — verify repair changed the XML.

## Issue Codes

| Code | Severity | Trigger |
|------|----------|---------|
| `runtime.source_strategy_not_rendered` | error | Renderer produced bounds from a different strategy than declared. |
| `runtime.source_bounds_render_mismatch` | error | Rendered bounds don't match declared strategy bounds. |
| `runtime.manual_source_bounds_missing` | error | `source_strategy=manual` but no `manual_source_bounds_cm`. |
| `runtime.unknown_source_strategy` | error | `source_strategy=unknown`. |
| `runtime.source_repair_no_xml_change` | error | Repair committed but settings.xml source box unchanged. |
