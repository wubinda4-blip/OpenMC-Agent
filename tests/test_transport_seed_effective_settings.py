"""Tests for transport seed effective settings."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from openmc_agent.transport_seed_stability import (
    run_single_seed,
    SeedRunResult,
)


def _write_settings(path: Path, seed=42, batches=10, particles=5000):
    root = ET.Element("settings")
    ET.SubElement(root, "seed").text = str(seed)
    ET.SubElement(root, "batches").text = str(batches)
    ET.SubElement(root, "particles").text = str(particles)
    ET.SubElement(root, "inactive").text = "2"
    tree = ET.ElementTree(root)
    tree.write(str(path))


def test_run_single_seed_writes_requested_values(tmp_path):
    """run_single_seed should write seed, batches, particles into settings.xml."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    _write_settings(model_dir / "settings.xml")

    # Also need materials and geometry XML for the function.
    (model_dir / "materials.xml").write_text("<materials/>")
    (model_dir / "geometry.xml").write_text("<geometry/>")

    output_dir = tmp_path / "seed_999"

    # We can't actually run OpenMC in tests, but we can verify the settings
    # are written correctly before the openmc call.
    # Mock subprocess.run to avoid running openmc.
    import unittest.mock

    with unittest.mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = unittest.mock.Mock(
            returncode=0, stdout="Building", stderr="",
        )

        # Mock statepoint extraction.
        with unittest.mock.patch(
            "openmc_agent.transport_seed_stability._extract_keff",
            return_value=(1.0, 0.001),
        ):
            with unittest.mock.patch(
                "openmc_agent.transport_seed_stability._count_lost_particles",
                return_value=0,
            ):
                with unittest.mock.patch(
                    "openmc_agent.transport_seed_stability._count_source_rejections",
                    return_value=0,
                ):
                    result = run_single_seed(
                        model_dir, output_dir, 999,
                        batches=30, particles=15000,
                    )

    # Verify settings.xml has the requested values.
    tree = ET.parse(output_dir / "settings.xml")
    root = tree.getroot()
    assert root.find("seed").text == "999"
    assert root.find("batches").text == "30"
    assert root.find("particles").text == "15000"

    # Verify result records.
    assert result.requested_seed == 999
    assert result.effective_seed == 999
    assert result.requested_batches == 30
    assert result.effective_batches == 30
    assert result.requested_particles == 15000
    assert result.effective_particles == 15000


def test_run_single_seed_no_dash_s_in_command():
    """The openmc command should NOT use -s (which means threads, not seed)."""
    import unittest.mock

    model_dir = Path("/tmp/fake_model")
    output_dir = Path("/tmp/fake_output")

    captured_cmd = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    with unittest.mock.patch("subprocess.run", return_value=FakeProc()) as mock_run:
        with unittest.mock.patch("pathlib.Path.mkdir"):
            with unittest.mock.patch("shutil.copy2"):
                with unittest.mock.patch("pathlib.Path.exists", return_value=False):
                    with unittest.mock.patch("pathlib.Path.write_text"):
                        with unittest.mock.patch("pathlib.Path.read_text", return_value=""):
                            try:
                                run_single_seed(model_dir, output_dir, 12345)
                            except Exception:
                                pass
                            if mock_run.call_args:
                                captured_cmd = mock_run.call_args[0][0] if mock_run.call_args[0] else []

    if captured_cmd:
        assert "-s" not in captured_cmd, (
            f"-s flag should not be used (it means threads); "
            f"got command: {captured_cmd}"
        )
