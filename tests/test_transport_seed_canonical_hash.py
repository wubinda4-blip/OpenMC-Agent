"""Tests for transport seed canonical settings hash."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from openmc_agent.transport_seed_stability import (
    canonical_settings_hash_excluding_seed,
)


def _write_settings(path: Path, seed=None, **kwargs) -> None:
    root = ET.Element("settings")
    if seed is not None:
        ET.SubElement(root, "seed").text = str(seed)
    for k, v in kwargs.items():
        ET.SubElement(root, k).text = str(v)
    tree = ET.ElementTree(root)
    tree.write(str(path))


def test_same_settings_different_seed_same_hash(tmp_path):
    """Two settings files differing only in seed → same canonical hash."""
    f1 = tmp_path / "s1.xml"
    f2 = tmp_path / "s2.xml"
    _write_settings(f1, seed=10101, batches=20, particles=10000, inactive=5)
    _write_settings(f2, seed=20202, batches=20, particles=10000, inactive=5)

    h1 = canonical_settings_hash_excluding_seed(f1)
    h2 = canonical_settings_hash_excluding_seed(f2)
    assert h1 == h2
    assert len(h1) == 64


def test_different_batches_different_hash(tmp_path):
    """Settings files differing in batches → different canonical hash."""
    f1 = tmp_path / "s1.xml"
    f2 = tmp_path / "s2.xml"
    _write_settings(f1, seed=10101, batches=20, particles=10000)
    _write_settings(f2, seed=10101, batches=30, particles=10000)

    h1 = canonical_settings_hash_excluding_seed(f1)
    h2 = canonical_settings_hash_excluding_seed(f2)
    assert h1 != h2


def test_no_seed_element(tmp_path):
    """Settings without seed element should still produce a hash."""
    f = tmp_path / "s.xml"
    _write_settings(f, batches=20, particles=10000)
    h = canonical_settings_hash_excluding_seed(f)
    assert len(h) == 64


def test_hash_is_deterministic(tmp_path):
    """Same file → same hash every time."""
    f = tmp_path / "s.xml"
    _write_settings(f, seed=12345, batches=20, particles=10000)
    h1 = canonical_settings_hash_excluding_seed(f)
    h2 = canonical_settings_hash_excluding_seed(f)
    assert h1 == h2
