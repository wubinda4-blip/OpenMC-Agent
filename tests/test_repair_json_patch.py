from copy import deepcopy
from openmc_agent.repair_proposal import RepairPatchOperation, apply_repair_patch_to_clone


def test_json_patch_applies_to_clone_and_preserves_original():
    plan = {"materials": [{"composition_status": "confirmed"}], "items": [1]}
    original = deepcopy(plan)
    result = apply_repair_patch_to_clone(plan, [RepairPatchOperation(op="replace", path="/materials/0/composition_status", value="nominal")])
    assert result.ok
    assert result.plan["materials"][0]["composition_status"] == "nominal"
    assert plan == original


def test_root_replacement_and_bad_array_index_fail():
    assert not apply_repair_patch_to_clone({}, [RepairPatchOperation(op="replace", path="", value={})]).ok
    assert not apply_repair_patch_to_clone({"a": []}, [RepairPatchOperation(op="replace", path="/a/0", value=1)]).ok


def test_add_allows_numeric_array_append_index():
    result = apply_repair_patch_to_clone(
        {"assumptions": ["existing"]},
        [RepairPatchOperation(op="add", path="/assumptions/1", value="appended")],
    )
    assert result.ok
    assert result.plan["assumptions"] == ["existing", "appended"]
