"""Regression tests for the VERA3B missing-grid-replacement-universe fixture.

Validates the recorded regression evidence at
``tests/fixtures/regressions/vera3b_missing_grid_replacement_universe.json``:
the undefined ``grid_cell`` resolution, the chosen repair branch, and the
expected preservation of non-grid loadings and overlay count.
"""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "regressions"
    / "vera3b_missing_grid_replacement_universe.json"
)


def _load_regression() -> dict:
    with open(_FIXTURE_PATH) as f:
        return json.load(f)


def test_regression_fixture_loads() -> None:
    data = _load_regression()
    expected_keys = {
        "issue_codes",
        "grid_operations",
        "replacement_id_resolution",
        "layers_using_grid_loading",
        "existing_grid_overlay_count",
        "repair_branch",
    }
    assert expected_keys.issubset(data.keys()), (
        f"missing keys: {expected_keys - set(data.keys())}"
    )


def test_regression_replacement_id_is_undefined() -> None:
    data = _load_regression()
    resolution = data["replacement_id_resolution"]
    assert resolution["is_universe_id"] is False
    assert resolution["is_cell_id"] is False
    assert resolution["is_material_id"] is False


def test_regression_repair_branch() -> None:
    data = _load_regression()
    assert data["repair_branch"] == "remove_redundant_grid_transformation", (
        f"expected remove_redundant_grid_transformation, got {data['repair_branch']!r}"
    )


def test_regression_grid_operations_count() -> None:
    data = _load_regression()
    ops = data["grid_operations"]
    assert len(ops) == 2, f"expected exactly 2 grid operations, got {len(ops)}"
    assert all(op["replacement_universe_id"] == "grid_cell" for op in ops)


def test_regression_layers_preserve_non_grid() -> None:
    data = _load_regression()
    grid_loading_ids = {"spacer_grid_loadings", "top_grid_loading"}
    for layer in data["layers_using_grid_loading"]:
        before = layer["loading_ids_before"]
        after = layer["loading_ids_after"]
        non_grid_before = [lid for lid in before if lid not in grid_loading_ids]
        assert non_grid_before == after, (
            f"layer {layer['layer_id']!r} did not preserve non-grid loadings: "
            f"before={before}, after={after}"
        )
        assert not any(lid in grid_loading_ids for lid in after), (
            f"layer {layer['layer_id']!r} still references a grid loading: {after}"
        )


def test_regression_overlay_count() -> None:
    data = _load_regression()
    assert data["existing_grid_overlay_count"] == 8, (
        f"expected 8 spacer_grid overlays, got {data['existing_grid_overlay_count']}"
    )
