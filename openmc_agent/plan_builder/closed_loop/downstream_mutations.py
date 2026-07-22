"""Deterministic mutation corpus for offline downstream gate qualification."""

from __future__ import annotations

from typing import Any, Callable

from .assembled_plan_issue_policy import assembled_plan_issue_owner
from .axial_geometry_issue_policy import axial_geometry_issue_owner
from .placement_issue_policy import placement_issue_owner


def _copy_bundle(bundle: Any, mutate: Callable[[dict[str, Any]], None]) -> Any:
    raw = bundle.model_dump(mode="json")
    mutate(raw["normalized_state"])
    raw["bundle_hash"] = ""
    raw["fixture_fingerprint"] = ""
    return bundle.__class__.model_validate(raw)


def _patch(state: dict[str, Any], patch_type: str) -> dict[str, Any]:
    for envelope in state["patches"].values():
        if envelope.get("patch_type") == patch_type:
            return envelope["content"]
    raise KeyError(patch_type)


def placement_mutations(bundle: Any) -> list[tuple[str, Any, str]]:
    def location(state: dict[str, Any]) -> None:
        _patch(state, "pin_map")["localized_insert_intents"][0]["coordinates"] = [[0, 0]]

    def binding(state: dict[str, Any]) -> None:
        _patch(state, "localized_insert_profiles")["profiles"][0]["segments"][0]["universe_id"] = "missing"

    return [
        ("localized_insert_location", _copy_bundle(bundle, location), "localized_insert.coordinates_not_in_host_path"),
        ("material_universe_binding", _copy_bundle(bundle, binding), "localized_insert.required_universe_missing"),
    ]


def axial_mutations(bundle: Any) -> list[tuple[str, Any, str]]:
    def domain(state: dict[str, Any]) -> None:
        _patch(state, "axial_layers")["layers"][1]["z_min_cm"] = 95.0

    def overlay(state: dict[str, Any]) -> None:
        _patch(state, "axial_overlays")["overlays"][0]["through_path_preserved"] = False

    return [
        ("domain", _copy_bundle(bundle, domain), "axial.layer_zero_thickness"),
        ("overlay_through_path", _copy_bundle(bundle, overlay), "axial.overlay_through_path_not_preserved"),
    ]


def assembled_mutations(bundle: Any) -> list[tuple[str, Any, str]]:
    def reference(state: dict[str, Any]) -> None:
        state["assembled_plan"]["complex_model"]["lattices"][0]["universe_pattern"][0][0] = "missing"

    def renderer(state: dict[str, Any]) -> None:
        state["assembled_plan"]["complex_model"]["kind"] = "unsupported_model_kind"

    return [
        ("reference_integrity", _copy_bundle(bundle, reference), "assembled.renderer_skeleton_only"),
        ("renderer_capability", _copy_bundle(bundle, renderer), "gate_replay.state_reconstruction_failed"),
    ]


def owner_for(gate_id: str, code: str) -> Any:
    if gate_id == "placement":
        return placement_issue_owner(code)
    if gate_id == "axial_geometry":
        return axial_geometry_issue_owner(code)
    if gate_id == "assembled_plan":
        return assembled_plan_issue_owner(code)
    return None


__all__ = ["placement_mutations", "axial_mutations", "assembled_mutations", "owner_for"]
