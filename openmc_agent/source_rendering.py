"""Settings.xml source round-trip verification.

Reads rendered settings.xml (or a live ``openmc.Settings`` object) and reports
the actual source box parameters, allowing comparison against the declared
``source_strategy`` in the plan.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from openmc_agent.source_settings import SourceBounds, source_bounds_for_plan


__all__ = [
    "RenderedSourceInfo",
    "inspect_rendered_source_settings",
    "compare_source_settings_to_plan",
    "source_rendering_report",
]


class RenderedSourceInfo:
    """Parsed source parameters from settings.xml or an openmc.Settings object."""

    def __init__(
        self,
        *,
        space_type: str = "",
        lower_left: tuple[float, ...] | None = None,
        upper_right: tuple[float, ...] | None = None,
        only_fissionable: bool | None = None,
        source_file: str | None = None,
    ) -> None:
        self.space_type = space_type
        self.lower_left = lower_left
        self.upper_right = upper_right
        self.only_fissionable = only_fissionable
        self.source_file = source_file

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_type": self.space_type,
            "lower_left": list(self.lower_left) if self.lower_left else None,
            "upper_right": list(self.upper_right) if self.upper_right else None,
            "only_fissionable": self.only_fissionable,
            "source_file": self.source_file,
        }

    def matches_bounds(self, expected: SourceBounds) -> bool:
        if self.lower_left is None or self.upper_right is None:
            return False
        tol = 1.0e-4
        return (
            abs(self.lower_left[0] - expected.x_min) < tol
            and abs(self.upper_right[0] - expected.x_max) < tol
            and abs(self.lower_left[1] - expected.y_min) < tol
            and abs(self.upper_right[1] - expected.y_max) < tol
            and abs(self.lower_left[2] - expected.z_min) < tol
            and abs(self.upper_right[2] - expected.z_max) < tol
        )


def inspect_rendered_source_settings(
    settings_xml_path: Path | str | None = None,
    *,
    settings_obj: Any | None = None,
) -> RenderedSourceInfo:
    """Parse source info from settings.xml or a live openmc.Settings object.

    Prefers the live ``openmc.Settings`` object when available (exact attribute
    access); falls back to XML parsing.
    """
    if settings_obj is not None:
        return _inspect_from_settings_obj(settings_obj)
    if settings_xml_path is not None:
        return _inspect_from_xml(Path(settings_xml_path))
    raise ValueError("provide either settings_xml_path or settings_obj")


def _inspect_from_xml(path: Path) -> RenderedSourceInfo:
    info = RenderedSourceInfo(source_file=str(path))
    if not path.exists():
        return info
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return info
    root = tree.getroot()

    source_elem = root.find(".//source")
    if source_elem is None:
        return info

    space_elem = source_elem.find(".//space")
    if space_elem is not None:
        info.space_type = space_elem.get("type", "")

    box_elem = source_elem.find(".//space/box") or source_elem.find(".//box")
    if box_elem is not None:
        ll = box_elem.get("lower_left", "")
        ur = box_elem.get("upper_right", "")
        if ll:
            info.lower_left = tuple(float(x) for x in ll.split())
        if ur:
            info.upper_right = tuple(float(x) for x in ur.split())

    only_fissile_str = source_elem.get("only_fissionable") or box_elem and box_elem.get("only_fissionable")
    if only_fissile_str is not None:
        info.only_fissionable = only_fissile_str.lower() == "true"

    return info


def _inspect_from_settings_obj(settings: Any) -> RenderedSourceInfo:
    info = RenderedSourceInfo()
    src = getattr(settings, "source", None)
    if src is None:
        return info
    space = getattr(src, "space", None)
    if space is None:
        return info

    info.space_type = type(space).__name__

    ll = getattr(space, "lower_left", None)
    ur = getattr(space, "upper_right", None)
    if ll is not None:
        info.lower_left = tuple(float(v) for v in ll)
    if ur is not None:
        info.upper_right = tuple(float(v) for v in ur)

    info.only_fissionable = getattr(space, "only_fissionable", None)
    return info


def compare_source_settings_to_plan(
    rendered: RenderedSourceInfo,
    plan_model: Any,
) -> dict[str, Any]:
    """Compare rendered source settings to the plan's declared source strategy."""
    strategy = getattr(
        getattr(plan_model, "settings", None),
        "source_strategy",
        "active_fuel_box",
    )
    expected_bounds = source_bounds_for_plan(plan_model, source_strategy=strategy)

    matches = False
    if expected_bounds is not None:
        matches = rendered.matches_bounds(expected_bounds)

    return {
        "strategy_expected": strategy,
        "strategy_rendered_space_type": rendered.space_type,
        "rendered_lower_left": list(rendered.lower_left) if rendered.lower_left else None,
        "rendered_upper_right": list(rendered.upper_right) if rendered.upper_right else None,
        "rendered_only_fissionable": rendered.only_fissionable,
        "expected_bounds": {
            "x_min": expected_bounds.x_min if expected_bounds else None,
            "x_max": expected_bounds.x_max if expected_bounds else None,
            "y_min": expected_bounds.y_min if expected_bounds else None,
            "y_max": expected_bounds.y_max if expected_bounds else None,
            "z_min": expected_bounds.z_min if expected_bounds else None,
            "z_max": expected_bounds.z_max if expected_bounds else None,
        } if expected_bounds else None,
        "bounds_match": matches,
    }


def source_rendering_report(
    settings_xml_before: Path | str | None = None,
    settings_xml_after: Path | str | None = None,
    plan_model: Any | None = None,
) -> dict[str, Any]:
    """Generate a full source rendering report comparing before/after XML.

    Used to verify that a source repair actually changed the settings.xml source
    box, and that the new box matches the repaired plan's declared strategy.
    """
    before = inspect_rendered_source_settings(settings_xml_before) if settings_xml_before else None
    after = inspect_rendered_source_settings(settings_xml_after) if settings_xml_after else None

    report: dict[str, Any] = {
        "before": before.to_dict() if before else None,
        "after": after.to_dict() if after else None,
        "source_xml_changed": False,
    }

    if before and after:
        report["source_xml_changed"] = (
            before.to_dict() != after.to_dict()
        )

    if after and plan_model is not None:
        report["comparison"] = compare_source_settings_to_plan(after, plan_model)

    return report
