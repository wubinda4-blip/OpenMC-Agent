# Full-Core MVP

## Status: FULL_CORE_MVP_TRANSPORT_SMOKE_PASSED

## What Was Built

### 1. Reusable Assembly Universe
- Assembly geometry with boundary surfaces set to `transmission` instead of `reflective`
- Root cell x/y bounds stripped — core lattice provides x/y clipping
- Can be placed at multiple positions in a core lattice

### 2. Full-Core MVP Schema (Script-Based)
- 3×3 assembly lattice using VERA3 3A reusable assembly universe
- Homogenized water+steel reflector ring
- Vacuum outer boundary (radial + axial)
- Source constrained to fissionable region (active fuel z-range)

### 3. Transport Results
- **3×3 Core**: keff = 0.99452 ± 0.005 (30 batches, 2000 particles)
- **Leakage**: 4.6% (expected for small core with vacuum BC)
- **Assembly fission tally**: 9 entries, peak-to-average = 1.35 (center peaks)
- **Zero lost particles, zero overlaps**

### 4. VERA3 Assembly Transport (Single Assembly)
- **3A**: keff = 1.17547 ± 0.005 (ref 1.175722, Δ = 0.025%)
- **3B**: keff = 0.99487 ± 0.006 (ref 1.000154, Δ = 0.53%)

## Architecture

```
Pin/Tube Universe (fuel_pin, guide_tube, instrument_tube, pyrex, thimble)
    ↓
3D Assembly Universe (axial layers + spacer-grid overlays)
    ↓ (boundary=transmission, x/y bounds stripped)
2D Core RectLattice (3×3 assembly positions + reflector)
    ↓
Root Universe (vacuum outer boundary)
    ↓
Full-Core Geometry
```

## What Is NOT Done (Deferred)

- Real full-core loading map (user must provide)
- Assembly type definitions with different enrichment/burnup
- Reflector/baffle/barrel/vessel fidelity
- Control-bank layout and insertion
- Pin-wise full-core power tally
- 49-layer benchmark power acceptance
- Production convergence
- Depletion and thermal-hydraulic feedback
- Hex core support

## Next Steps

1. User provides real full-core assembly loading map
2. Define multiple assembly types (different enrichments, burnups, BP locations)
3. Add baffle/barrel/vessel geometry
4. Add control rod guide tubes and banks
5. Production-level convergence and benchmark acceptance
