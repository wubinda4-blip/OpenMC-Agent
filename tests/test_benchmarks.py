import h5py

from openmc_agent.benchmarks.c5g7 import export_mgxs_hdf5, make_macroscopic_materials


def test_export_c5g7_mgxs_hdf5(tmp_path):
    path = export_mgxs_hdf5(tmp_path / "mgxs.h5")

    assert path.exists()
    with h5py.File(path, "r") as h5_file:
        assert {"uo2", "mox43", "mox7", "mox87", "water"}.issubset(h5_file.keys())


def test_make_c5g7_macroscopic_materials():
    materials = make_macroscopic_materials()

    assert materials["uo2"].density_units == "macro"
    assert materials["uo2"].density == 1.0
    assert getattr(materials["uo2"], "_macroscopic") == "uo2"
