from openmc_agent.few_shots import (
    FEW_SHOT_EXAMPLES,
    build_gold_few_shot_examples,
    select_few_shots,
)


def _names(requirement: str, *, limit: int = 3) -> list[str]:
    return [example["name"] for example in select_few_shots(requirement, limit=limit)]


def test_selects_official_material_examples_for_enriched_water_case() -> None:
    names = _names("建立 UO2 富集燃料和轻水慢化剂材料，包含热散射")

    assert names[0] == "official_material_enrichment_and_water"


def test_selects_assembly_examples_for_numbered_assembly() -> None:
    names = _names("根据自然语言展开 17x17 组件 lattice，并校验每种 pin 数量")
    # The gold 2D assembly case is the strongest structural match for a 17x17
    # assembly request; the abstract rectangular_assembly_lattice outline must
    # also appear.
    assert "gold_assembly_2d_lattice" in names
    assert "rectangular_assembly_lattice" in names


def test_selects_official_settings_example_for_eigenvalue_source() -> None:
    names = _names("设置 eigenvalue keff 临界计算 source batches inactive particles")

    assert names[0] == "official_eigenvalue_settings_and_source"


def test_default_few_shot_remains_pin_cell_when_no_terms_match() -> None:
    names = _names("完全没有领域关键词的请求")

    assert names == ["pin_cell_with_cladding"]


# ---------------------------------------------------------------------------
# Reactor-type-neutrality self-check (CLAUDE.md universality constraint)
# ---------------------------------------------------------------------------

_REACTOR_TYPE_NAMES = {"pwr", "bwr", "vver", "htgr", "sfr", "candu", "vera", "c5g7"}


def test_no_reactor_type_name_in_abstract_trigger_terms() -> None:
    for ex in FEW_SHOT_EXAMPLES:
        terms = {t.lower() for t in ex.trigger_terms}
        assert not (terms & _REACTOR_TYPE_NAMES), (
            f"{ex.name} names a reactor type: {terms & _REACTOR_TYPE_NAMES}"
        )


def test_no_reactor_type_name_in_gold_cases() -> None:
    for ex in build_gold_few_shot_examples():
        terms = {t.lower() for t in ex.trigger_terms}
        feats = {f.lower() for f in ex.structural_features}
        ids = {ex.gold_case_id.lower()} if ex.gold_case_id else set()
        assert not ((terms | feats | ids) & _REACTOR_TYPE_NAMES), (
            f"{ex.name} names a reactor type"
        )


# ---------------------------------------------------------------------------
# Gold-case selection by structural signature
# ---------------------------------------------------------------------------


def test_gold_assembly_3d_selected_for_spacer_grid_requirement() -> None:
    selected = select_few_shots(
        "Build a 17x17 fuel assembly with spacer grids and 3D axial layers"
    )
    names = [ex["name"] for ex in selected]
    assert "gold_assembly_3d_with_spacer_grids" in names


def test_structural_features_boost_gold_ranking() -> None:
    selected = select_few_shots("17x17 fuel assembly spacer grid 3D nozzle end plug")
    assert selected[0]["name"].startswith("gold_")


def test_gold_case_dump_carries_gold_case_id() -> None:
    selected = select_few_shots(
        "Build a 17x17 fuel assembly with spacer grids and 3D axial layers"
    )
    gold = [ex for ex in selected if ex.get("gold_case_id")]
    assert gold, "expected at least one gold case with gold_case_id"
    assert gold[0]["gold_case_id"] == "assembly_3d_with_spacer_grids"
