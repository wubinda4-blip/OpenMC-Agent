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
        name="pin_cell_with_cladding",
        trigger_terms=("pin", "栅元", "燃料棒", "cladding", "包壳"),
        requirement="Build one reflective UO2-water pin cell with fuel, gap, cladding, and moderator.",
        structured_outline=(
            "Use model_spec.kind='pin_cell', define fuel/moderator/cladding materials, "
            "three ZCylinder radii, xy plot, and low-particle eigenvalue smoke settings."
        ),
    ),
    FewShotExample(
        name="rectangular_assembly_lattice",
        trigger_terms=("assembly", "组件", "rect", "lattice", "栅格"),
        requirement="Describe a PWR assembly made from repeated pin-cell universes.",
        structured_outline=(
            "Use complex_model.kind='assembly', ComplexMaterialSpec entries, "
            "UniverseSpec for pin universes, RectLattice via LatticeSpec, "
            "and capability_report.supported_renderer='assembly' when all materials and lattice links are complete."
        ),
    ),
    FewShotExample(
        name="core_with_reflector_and_control_rods",
        trigger_terms=("core", "全堆", "堆芯", "reflector", "反射层", "control", "控制棒"),
        requirement="Describe a core layout with fuel assemblies, radial reflector, and control rods.",
        structured_outline=(
            "Use complex_model.kind='core', CoreSpec, ReflectorSpec, ControlRodSpec, "
            "assembly ids, lattice ids, CoreSpec.boundary_conditions for the six outer faces, "
            "CoreSpec.axial_layers for fuel-height and axial reflector regions, "
            "supported_renderer='core' for complete rectangular core lattices, and explicit "
            "human-confirmation items for missing dimensions."
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


def select_few_shots(requirement: str, *, limit: int = 3) -> list[dict[str, str]]:
    terms = _terms(requirement)
    scored: list[tuple[int, FewShotExample]] = []
    for example in FEW_SHOT_EXAMPLES:
        score = sum(1 for term in example.trigger_terms if term.lower() in terms)
        if score:
            scored.append((score, example))
    if not scored:
        scored.append((1, FEW_SHOT_EXAMPLES[0]))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [example.model_dump() for _, example in scored[:limit]]


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set("".join(char if char.isalnum() else " " for char in lowered).split())
    for keyword in ("组件", "全堆", "堆芯", "反射层", "控制棒", "颗粒", "燃料球", "球床", "栅元", "燃料棒"):
        if keyword in lowered:
            tokens.add(keyword)
    return tokens
