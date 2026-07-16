"""XML integrity gate for grid geometry.

Verifies that grid objects present in the plan also appear in the exported
XML files (materials.xml, geometry.xml).  Catches the case where decorated
universes, frame cells, or grid materials are in the plan but missing from
the rendered XML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "XMLIntegrityIssue",
    "XMLIntegrityReport",
    "check_grid_xml_integrity",
]


@dataclass
class XMLIntegrityIssue:
    code: str
    severity: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class XMLIntegrityReport:
    ok: bool
    issues: list[XMLIntegrityIssue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[XMLIntegrityIssue]:
        return [i for i in self.issues if i.severity == "error"]


def _read_file(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def check_grid_xml_integrity(
    xml_dir: Path,
    *,
    expected_decorated_count: int = 0,
    expected_grid_materials: set[str] | None = None,
    expected_frame_cell_count: int = 0,
) -> XMLIntegrityReport:
    """Check that grid objects are present in the exported XML.

    Parameters
    ----------
    xml_dir
        Directory containing materials.xml, geometry.xml, etc.
    expected_decorated_count
        Number of grid-decorated universes expected in geometry.xml.
    expected_grid_materials
        Set of grid material IDs expected in materials.xml.
    expected_frame_cell_count
        Number of frame cells expected in geometry.xml.
    """
    issues: list[XMLIntegrityIssue] = []
    xml_dir = Path(xml_dir)

    materials_xml = _read_file(xml_dir / "materials.xml")
    geometry_xml = _read_file(xml_dir / "geometry.xml")
    model_py = _read_file(xml_dir / "model.py")

    # --- Check files exist and are non-empty ---
    if not materials_xml:
        issues.append(XMLIntegrityIssue(
            code="xml.materials_missing",
            severity="error",
            message="materials.xml is missing or empty",
        ))
    if not geometry_xml:
        issues.append(XMLIntegrityIssue(
            code="xml.geometry_missing",
            severity="error",
            message="geometry.xml is missing or empty",
        ))

    if issues:
        return XMLIntegrityReport(ok=False, issues=issues)

    # --- Check grid materials in materials.xml / model.py ---
    # XML uses numeric IDs and display names (e.g., "Inconel-718"), so we
    # also check model.py which preserves the original string IDs.
    if expected_grid_materials:
        for mat_id in expected_grid_materials:
            found = mat_id in model_py or mat_id in materials_xml
            if not found:
                # Try case-insensitive partial match in materials.xml names
                partial = mat_id.replace("_", "").replace("-", "").lower()
                found = partial in materials_xml.lower().replace("-", "").replace("_", "")
            if not found:
                issues.append(XMLIntegrityIssue(
                    code="xml.grid_material_missing",
                    severity="error",
                    message=f"Grid material {mat_id!r} not found in materials.xml or model.py",
                    detail={"material_id": mat_id},
                ))

    # --- Check decorated universes in model.py / geometry.xml ---
    # model.py preserves __grid__ in variable names; geometry.xml uses
    # numeric IDs so we check frame cell names instead.
    if expected_decorated_count > 0:
        # Count __grid__ references in model.py (surfaces, universes)
        grid_refs_in_model = model_py.count("__grid__")
        # Count frame cells in geometry.xml
        frame_in_xml = geometry_xml.count('name="grid_frame"')
        if grid_refs_in_model < expected_decorated_count and frame_in_xml < expected_decorated_count:
            issues.append(XMLIntegrityIssue(
                code="xml.decorated_universe_count_mismatch",
                severity="error",
                message=(
                    f"Expected {expected_decorated_count} grid-decorated universes; "
                    f"found {grid_refs_in_model} __grid__ refs in model.py, "
                    f"{frame_in_xml} frame cells in geometry.xml"
                ),
                detail={
                    "expected": expected_decorated_count,
                    "grid_refs_in_model_py": grid_refs_in_model,
                    "frame_cells_in_geometry_xml": frame_in_xml,
                },
            ))

    # --- Check frame cells in geometry.xml ---
    if expected_frame_cell_count > 0:
        frame_cell_count = geometry_xml.count('name="grid_frame"')
        if frame_cell_count < expected_frame_cell_count:
            issues.append(XMLIntegrityIssue(
                code="xml.frame_cell_count_mismatch",
                severity="error",
                message=(
                    f"Expected {expected_frame_cell_count} frame cells "
                    f"in geometry.xml, found {frame_cell_count}"
                ),
                detail={
                    "expected": expected_frame_cell_count,
                    "actual": frame_cell_count,
                },
            ))

    # --- Check XPlane/YPlane frame surfaces exist ---
    if expected_frame_cell_count > 0:
        xplane_count = geometry_xml.count("type=\"x-plane\"") + geometry_xml.count("type='x-plane'")
        yplane_count = geometry_xml.count("type=\"y-plane\"") + geometry_xml.count("type='y-plane'")
        min_expected_surfaces = expected_frame_cell_count * 4  # 4 xplanes + 4 yplanes per frame
        if (xplane_count + yplane_count) < min_expected_surfaces:
            issues.append(XMLIntegrityIssue(
                code="xml.frame_surface_count_mismatch",
                severity="warning",
                message=(
                    f"Expected at least {min_expected_surfaces} xplane+yplane surfaces "
                    f"for {expected_frame_cell_count} frames, found {xplane_count + yplane_count}"
                ),
                detail={
                    "expected_min": min_expected_surfaces,
                    "actual": xplane_count + yplane_count,
                    "xplane": xplane_count,
                    "yplane": yplane_count,
                },
            ))

    error_count = sum(1 for i in issues if i.severity == "error")
    return XMLIntegrityReport(
        ok=error_count == 0,
        issues=issues,
        summary={
            "expected_decorated_count": expected_decorated_count,
            "expected_frame_cell_count": expected_frame_cell_count,
            "expected_grid_materials": sorted(expected_grid_materials or set()),
            "error_count": error_count,
            "warning_count": sum(1 for i in issues if i.severity == "warning"),
        },
    )
