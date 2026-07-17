"""Material properties required by already-selected executable geometry."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import Field
from openmc_agent.schemas import AgentBaseModel

class StructuralDensityPolicy(str):
    SOURCE_ONLY = "source_only"
    APPROVED_LIBRARY = "approved_library"
    ALLOW_APPROXIMATE = "allow_approximate"
    REQUIRE_HUMAN = "require_human"

class MaterialExecutionRequirement(AgentBaseModel):
    requirement_id: str
    consumer_patch_type: str
    consumer_id: str
    material_id: str
    required_properties: list[str] = Field(default_factory=list)
    reason: str
    source_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class MaterialExecutionIssue(AgentBaseModel):
    code: str
    material_id: str
    affected_consumer_ids: list[str] = Field(default_factory=list)
    required_property: str
    owner_patch_type: Literal["materials"] = "materials"
    severity: Literal["error", "warning"] = "error"
    repairable_by_llm: bool = True
    requires_human: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

class MaterialExecutionReadinessResult(AgentBaseModel):
    requirements: list[MaterialExecutionRequirement] = Field(default_factory=list)
    issues: list[MaterialExecutionIssue] = Field(default_factory=list)
    @property
    def ok(self) -> bool: return not self.issues

def build_material_execution_requirements(*, materials_patch: dict[str, Any], axial_overlays_patch: dict[str, Any]) -> list[MaterialExecutionRequirement]:
    overlays = axial_overlays_patch.get("overlays", axial_overlays_patch.get("axial_overlays", [])) if isinstance(axial_overlays_patch, dict) else []
    requirements: list[MaterialExecutionRequirement] = []
    for index, overlay in enumerate(overlays or []):
        if not isinstance(overlay, dict) or overlay.get("geometry_mode") != "mass_conserving_outer_frame":
            continue
        material_id = overlay.get("material_id")
        if not isinstance(material_id, str) or not material_id:
            continue
        if isinstance(overlay.get("effective_density_g_cm3"), (int, float)) and overlay["effective_density_g_cm3"] > 0:
            continue
        consumer_id = str(overlay.get("overlay_id") or overlay.get("id") or f"overlay_{index}")
        requirements.append(MaterialExecutionRequirement(requirement_id=f"density:{material_id}", consumer_patch_type="axial_overlays", consumer_id=consumer_id, material_id=material_id, required_properties=["density_g_cm3"], reason="mass_conserving_outer_frame requires density to derive geometry", source_paths=[f"/overlays/{index}/material_id"]))
    return requirements

def validate_material_execution_readiness(*, materials_patch: dict[str, Any], axial_overlays_patch: dict[str, Any], policy: str = "source_only") -> MaterialExecutionReadinessResult:
    requirements = build_material_execution_requirements(materials_patch=materials_patch, axial_overlays_patch=axial_overlays_patch)
    material_map = {str(item.get("material_id")): item for item in materials_patch.get("materials", []) if isinstance(item, dict)}
    grouped: dict[str, list[MaterialExecutionRequirement]] = {}
    for item in requirements: grouped.setdefault(item.material_id, []).append(item)
    issues: list[MaterialExecutionIssue] = []
    for material_id, grouped_requirements in grouped.items():
        material = material_map.get(material_id, {})
        density = material.get("density_g_cm3") if isinstance(material, dict) else None
        if not isinstance(density, (int, float)) or density <= 0:
            issues.append(MaterialExecutionIssue(code="materials.execution_density_required", material_id=material_id, affected_consumer_ids=[item.consumer_id for item in grouped_requirements], required_property="density_g_cm3", repairable_by_llm=policy in {"approved_library", "allow_approximate"}, requires_human=policy in {"source_only", "require_human"}, metadata={"density_policy": policy, "count": len(grouped_requirements)}))
    return MaterialExecutionReadinessResult(requirements=requirements, issues=issues)
