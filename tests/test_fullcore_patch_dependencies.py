"""Tests for full-core patch dependencies and ordering (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.executor import (
    _DEFAULT_ORDER,
    _DEPENDENCIES,
    _PATCH_DEPENDENTS,
    _expand_patch_repair_targets,
)


def test_assembly_catalog_in_default_order():
    assert "assembly_catalog" in _DEFAULT_ORDER


def test_core_layout_in_default_order():
    assert "core_layout" in _DEFAULT_ORDER


def test_order_has_core_layout_after_assembly_catalog():
    """core_layout should come after assembly_catalog in the canonical order."""
    idx_catalog = _DEFAULT_ORDER.index("assembly_catalog")
    idx_layout = _DEFAULT_ORDER.index("core_layout")
    assert idx_layout > idx_catalog


def test_order_has_core_layout_before_settings():
    idx_layout = _DEFAULT_ORDER.index("core_layout")
    idx_settings = _DEFAULT_ORDER.index("settings")
    assert idx_layout < idx_settings


def test_assembly_catalog_dependencies():
    """assembly_catalog depends on facts and universes."""
    deps = _DEPENDENCIES["assembly_catalog"]
    assert "facts" in deps
    assert "universes" in deps


def test_core_layout_dependencies():
    """core_layout depends on facts and assembly_catalog."""
    deps = _DEPENDENCIES["core_layout"]
    assert "facts" in deps
    assert "assembly_catalog" in deps


def test_patch_dependents_assembly_catalog():
    """assembly_catalog has downstream dependents."""
    deps = _PATCH_DEPENDENTS["assembly_catalog"]
    assert "axial_layers" in deps
    assert "axial_overlays" in deps
    assert "core_layout" in deps


def test_expand_repair_targets_includes_core_layout():
    """Fixing assembly_catalog should cascade to core_layout."""
    targets = _expand_patch_repair_targets(["assembly_catalog"])
    assert "assembly_catalog" in targets
    assert "core_layout" in targets
    assert "axial_layers" in targets


def test_expand_repair_targets_from_facts():
    """Fixing facts should cascade to everything."""
    targets = _expand_patch_repair_targets(["facts"])
    assert "assembly_catalog" in targets
    assert "core_layout" in targets
    assert "settings" in targets
