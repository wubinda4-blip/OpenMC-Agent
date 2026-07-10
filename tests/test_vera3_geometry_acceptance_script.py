"""Tests for the VERA3 geometry acceptance script."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/generate_vera3_geometry_acceptance.py")


class TestAcceptanceScript:
    def test_3a_runs_successfully(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--variant", "3A",
             "--export-xml",
             "--out", str(tmp_path / "3A")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[:500]
        report_path = tmp_path / "3A" / "geometry_acceptance_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["error_count"] == 0

    def test_3b_runs_successfully(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--variant", "3B",
             "--export-xml",
             "--out", str(tmp_path / "3B")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[:500]
        report_path = tmp_path / "3B" / "geometry_acceptance_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["error_count"] == 0

    def test_3b_xml_exported(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--variant", "3B",
             "--export-xml",
             "--out", str(tmp_path / "3B_xml")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[:500]
        for fname in ["materials.xml", "geometry.xml", "settings.xml"]:
            assert (tmp_path / "3B_xml" / fname).exists()

    def test_3b_pyrex_conflict_retained(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--variant", "3B",
             "--export-xml",
             "--out", str(tmp_path / "3B_conflict")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[:500]
        report = json.loads((tmp_path / "3B_conflict" / "geometry_acceptance_report.json").read_text())
        assert report.get("pyrex_upper_profile_unresolved") is True

    def test_summary_md_generated(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--variant", "3A",
             "--out", str(tmp_path / "3A_md")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[:500]
        assert (tmp_path / "3A_md" / "geometry_acceptance_summary.md").exists()

    def test_simulation_plan_json_generated(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--variant", "3B",
             "--out", str(tmp_path / "3B_plan")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[:500]
        plan_path = tmp_path / "3B_plan" / "simulation_plan.json"
        assert plan_path.exists()
        plan = json.loads(plan_path.read_text())
        assert plan["complex_model"]["kind"] == "assembly"
