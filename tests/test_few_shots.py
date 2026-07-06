from openmc_agent.few_shots import select_few_shots


def _names(requirement: str, *, limit: int = 3) -> list[str]:
    return [example["name"] for example in select_few_shots(requirement, limit=limit)]


def test_selects_official_material_examples_for_enriched_water_case() -> None:
    names = _names("建立 UO2 富集燃料和轻水慢化剂材料，包含热散射")

    assert names[0] == "official_material_enrichment_and_water"


def test_selects_rect_lattice_example_for_numbered_assembly() -> None:
    names = _names("根据自然语言展开 17x17 组件 lattice，并校验每种 pin 数量")

    assert names[0] == "rectangular_assembly_lattice"


def test_selects_official_settings_example_for_eigenvalue_source() -> None:
    names = _names("设置 eigenvalue keff 临界计算 source batches inactive particles")

    assert names[0] == "official_eigenvalue_settings_and_source"


def test_default_few_shot_remains_pin_cell_when_no_terms_match() -> None:
    names = _names("完全没有领域关键词的请求")

    assert names == ["pin_cell_with_cladding"]
