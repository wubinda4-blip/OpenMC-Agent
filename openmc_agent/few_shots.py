from dataclasses import dataclass


@dataclass(frozen=True)
class FewShotExample:
    name: str
    trigger_terms: tuple[str, ...]
    requirement: str
    structured_outline: str

    def model_dump(self) -> dict[str, str]:
        return {
            "name": self.name,
            "requirement": self.requirement,
            "structured_outline": self.structured_outline,
        }


FEW_SHOT_EXAMPLES = (
    FewShotExample(
        name="official_material_enrichment_and_water",
        trigger_terms=(
            "material",
            "materials",
            "材料",
            "燃料",
            "uo2",
            "enrichment",
            "富集",
            "water",
            "水",
            "moderator",
            "慢化剂",
            "thermal",
            "热散射",
        ),
        requirement=(
            "Define enriched uranium fuel and light-water moderator using OpenMC "
            "material API patterns from the official user guide."
        ),
        structured_outline=(
            "For known enriched uranium, prefer a ComplexMaterialSpec/MaterialSpec that maps "
            "to openmc.Material.add_element('U', 1.0, enrichment=<wt% U235>) plus oxygen "
            "via chemical_formula='UO2' or explicit O16 only when supplied. For light water, "
            "use H1:O16 = 2:1, density in g/cm3, and include thermal_scattering='c_H_in_H2O' "
            "when requested/available. Do not invent density, enrichment, temperature, or "
            "cross_sections paths; put missing values in requires_human_confirmation. "
            "Official basis: OpenMC User Guide 5.1, 5.2, 5.7."
        ),
    ),
    FewShotExample(
        name="official_region_cell_universe_geometry",
        trigger_terms=(
            "geometry",
            "几何",
            "region",
            "cell",
            "universe",
            "surface",
            "边界",
            "反射",
            "vacuum",
            "真空",
            "圆柱",
            "zcylinder",
        ),
        requirement=(
            "Describe OpenMC CSG cells using surfaces, half-spaces, boolean regions, "
            "and repeatable universes."
        ),
        structured_outline=(
            "Represent pin geometry as surfaces plus non-overlapping CellSpec regions: "
            "inside fuel is negative half-space of ZCylinder; annuli use +inner & -outer; "
            "square or axial bounds use X/Y/ZPlane with explicit boundary_type when finite. "
            "Cells fill materials or nested universes/lattices; each repeatable pin gets a "
            "UniverseSpec and the root geometry must ultimately contain at least one root "
            "universe/cell path. Official basis: OpenMC User Guide 6.1, 6.2, 6.3."
        ),
    ),
    FewShotExample(
        name="pin_cell_with_cladding",
        trigger_terms=("pin", "栅元", "燃料棒", "cladding", "包壳", "gap", "间隙"),
        requirement="Build one reflective UO2-water pin cell with fuel, gap, cladding, and moderator.",
        structured_outline=(
            "Use model_spec.kind='pin_cell', define fuel/moderator/cladding materials, "
            "three ZCylinder radii, annular region ordering, reflective x/y boundary if "
            "the case describes an infinite lattice approximation, xy plot, and "
            "low-particle eigenvalue smoke settings."
        ),
    ),
    FewShotExample(
        name="rectangular_assembly_lattice",
        trigger_terms=("assembly", "组件", "rect", "rectangular", "lattice", "栅格", "栅阵", "17x17", "15x15"),
        requirement="Describe a PWR assembly made from repeated pin-cell universes.",
        structured_outline=(
            "Use complex_model.kind='assembly', ComplexMaterialSpec entries, "
            "UniverseSpec for pin universes, RectLattice via LatticeSpec, "
            "pitch_cm=(x_pitch,y_pitch), lower_left derived from pitch*shape when centered, "
            "and universe_pattern rows ordered top-to-bottom because OpenMC RectLattice's "
            "first row is highest y. For natural-language ring/symmetry layouts, expand "
            "the full matrix first, then set expected_counts and verify counts before "
            "claiming runnable renderability. capability_report.supported_renderer='assembly' "
            "only when all materials and lattice links are complete. Official basis: "
            "OpenMC User Guide 6.4.1."
        ),
    ),
    FewShotExample(
        name="mox_material_mixture",
        trigger_terms=(
            "mox",
            "plutonium",
            "puo2",
            "uo2",
            "混合氧化物",
            "钚",
            "weight",
            "wo",
            "重量",
        ),
        requirement="Describe a MOX fuel material made by mixing uranium oxide and plutonium oxide.",
        structured_outline=(
            "When source data gives MOX as UO2/PuO2 fractions, model it as a derived "
            "ComplexMaterialSpec with mixing fractions whose basis is explicit ('wo', 'ao', "
            "or 'vo'), mirroring openmc.Material.mix_materials([uo2, puo2], [...], 'wo'). "
            "Fractions for atomic/weight mixtures must sum to one. Do not mix materials "
            "that already carry S(a,b); attach thermal scattering only to moderators. "
            "Official basis: OpenMC User Guide 5.6."
        ),
    ),
    FewShotExample(
        name="core_with_reflector_and_control_rods",
        trigger_terms=("core", "全堆", "堆芯", "reflector", "反射层", "control", "控制棒"),
        requirement="Describe a core layout with fuel assemblies, radial reflector, and control rods.",
        structured_outline=(
            "Use complex_model.kind='core', CoreSpec, ReflectorSpec, ControlRodSpec, "
            "assembly ids, lattice ids, CoreSpec.boundary_conditions for the six outer faces, "
            "CoreSpec.axial_layers with fill={type,id} for fuel-height and axial reflector "
            "regions; define lattice_loadings with base_lattice_id + overrides and reference "
            "them through AxialLayerSpec.loading_id when an axial slice needs a different "
            "assembly loading (e.g. control rods inserted only in part of the fuel height). "
            "Rectangular core lattice rows follow OpenMC RectLattice top-to-bottom order; "
            "hard assembly/pin counts must be encoded as expected_counts where available. "
            "supported_renderer='core' for complete rectangular "
            "core lattices, and explicit human-confirmation items for missing dimensions."
        ),
    ),
    FewShotExample(
        name="official_eigenvalue_settings_and_source",
        trigger_terms=(
            "settings",
            "source",
            "eigenvalue",
            "criticality",
            "keff",
            "k-effective",
            "临界",
            "源",
            "粒子",
            "batches",
            "inactive",
        ),
        requirement="Set up OpenMC execution settings and a constrained source for eigenvalue diagnostics.",
        structured_outline=(
            "Use SettingsSpec/RunSettingsSpec with run_mode='eigenvalue' unless the case "
            "explicitly asks fixed source; include batches, inactive, particles, and optional "
            "generations_per_batch when supplied. For startup source, map to "
            "openmc.IndependentSource with an OpenMC stats spatial distribution and prefer "
            "constraints={'fissionable': True} for k-eigenvalue source rejection when the "
            "renderer supports it. Keep diagnostic low-particle smoke settings distinct from "
            "production-quality physics settings. Official basis: OpenMC User Guide 7.1, "
            "7.2, 7.3.4."
        ),
    ),
    FewShotExample(
        name="triso_compact",
        trigger_terms=("triso", "颗粒", "compact", "包覆"),
        requirement="Describe a TRISO fuel compact with kernel and coating layers.",
        structured_outline=(
            "Use TRISOSpec with strictly increasing TRISOLayerSpec.outer_radius_cm values, "
            "matrix material id, packing_fraction, supported_renderer='triso', and OpenMC API context for TRISO/pack_spheres."
        ),
    ),
    FewShotExample(
        name="pebble_bed",
        trigger_terms=("pebble", "球床", "燃料球", "sphere", "球"),
        requirement="Describe fuel pebbles containing TRISO particles in a pebble-bed model.",
        structured_outline=(
            "Use PebbleSpec, PackedSphereSpec for pebble or TRISO packing, Sphere surfaces, "
            "and mark stochastic packing or homogenization assumptions explicitly."
        ),
    ),
)

DEFAULT_FEW_SHOT_EXAMPLE = next(
    example for example in FEW_SHOT_EXAMPLES if example.name == "pin_cell_with_cladding"
)


def select_few_shots(requirement: str, *, limit: int = 3) -> list[dict[str, str]]:
    terms = _terms(requirement)
    scored: list[tuple[int, FewShotExample]] = []
    for example in FEW_SHOT_EXAMPLES:
        score = sum(1 for term in example.trigger_terms if term.lower() in terms)
        if score:
            scored.append((score, example))
    if not scored:
        scored.append((1, DEFAULT_FEW_SHOT_EXAMPLE))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [example.model_dump() for _, example in scored[:limit]]


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set("".join(char if char.isalnum() else " " for char in lowered).split())
    for keyword in (
        "组件",
        "全堆",
        "堆芯",
        "反射层",
        "控制棒",
        "颗粒",
        "燃料球",
        "球床",
        "栅元",
        "燃料棒",
        "材料",
        "燃料",
        "富集",
        "水",
        "慢化剂",
        "热散射",
        "几何",
        "边界",
        "反射",
        "真空",
        "圆柱",
        "栅阵",
        "钚",
        "重量",
        "临界",
        "源",
        "粒子",
        "间隙",
    ):
        if keyword in lowered:
            tokens.add(keyword)
    return tokens
