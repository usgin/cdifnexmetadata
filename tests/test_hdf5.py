"""Tests for stage-1 structural inspection.

Fixtures are synthesised rather than committed, so the suite runs
anywhere without shipping binary test data. One test additionally runs
against a real NeXus file if `HDF5METADATA_TEST_NXS` points at one.
"""
from __future__ import annotations

import os

import pytest

h5py = pytest.importorskip("h5py")
import numpy as np  # noqa: E402  (after importorskip)

from hdf5metadata.inspect import HDF5Inspector, inspect_file  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_file(tmp_path):
    """A minimal NeXus-shaped file exercising every structural feature."""
    p = tmp_path / "simple.nxs"
    with h5py.File(p, "w") as f:
        f.attrs["creator"] = "test suite"
        f.attrs["default"] = "entry"

        entry = f.create_group("entry")
        entry.attrs["NX_class"] = "NXentry"
        entry["definition"] = "NXxas"          # scalar string -> read
        entry["title"] = "a test measurement"

        instr = entry.create_group("instrument")
        instr.attrs["NX_class"] = "NXinstrument"

        det = instr.create_group("i0")
        det.attrs["NX_class"] = "NXdetector"
        big = det.create_dataset("data", data=np.arange(500, dtype="float64"))
        big.attrs["units"] = "counts"

        mono = instr.create_group("monochromator")
        mono.attrs["NX_class"] = "NXmonochromator"
        e = mono.create_dataset("energy", data=np.linspace(7000, 7500, 500))
        e.attrs["units"] = "eV"
        mono["d_spacing"] = 3.1355            # scalar float -> read

        data = entry.create_group("data")
        data.attrs["NX_class"] = "NXdata"
        # NXdata reaches the real arrays by soft link, as real files do.
        data["energy"] = h5py.SoftLink("/entry/instrument/monochromator/energy")
        data["i0"] = h5py.SoftLink("/entry/instrument/i0/data")
        data.create_dataset("mutrans", data=np.zeros(500))

        # Small array -> read; just above threshold -> not read.
        entry.create_dataset("small", data=np.arange(10))
        entry.create_dataset("justover", data=np.arange(65))
    return p


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------

def test_walks_groups_and_datasets(simple_file):
    r = inspect_file(simple_file)
    assert not r.warnings

    paths = {g.path for g in r.groups}
    assert "/" in paths                        # root reported as a group
    assert {"/entry", "/entry/instrument", "/entry/data"} <= paths

    ds = {d.path for d in r.datasets}
    assert "/entry/instrument/i0/data" in ds
    assert "/entry/definition" in ds


def test_root_attributes(simple_file):
    r = inspect_file(simple_file)
    assert r.root_attributes["creator"] == "test suite"
    assert r.root_attributes["default"] == "entry"


def test_group_attributes_preserved_verbatim(simple_file):
    """Stage 1 must not interpret NX_class -- only carry it."""
    r = inspect_file(simple_file)
    assert r.group("/entry").attributes["NX_class"] == "NXentry"
    assert r.group("/entry/data").attributes["NX_class"] == "NXdata"


def test_dataset_shape_dtype_and_units(simple_file):
    r = inspect_file(simple_file)
    d = r.dataset("/entry/instrument/monochromator/energy")
    assert d.shape == (500,)
    assert d.ndim == 1
    assert d.size == 500
    assert "float" in d.dtype
    assert d.attributes["units"] == "eV"


# ---------------------------------------------------------------------------
# the value-reading policy: small values are metadata, big arrays are data
# ---------------------------------------------------------------------------

def test_scalar_string_value_is_read(simple_file):
    """A scalar string like 'NXxas' IS the metadata -- it must be read."""
    r = inspect_file(simple_file)
    d = r.dataset("/entry/definition")
    assert d.has_value
    assert d.value == "NXxas"


def test_scalar_float_value_is_read(simple_file):
    r = inspect_file(simple_file)
    d = r.dataset("/entry/instrument/monochromator/d_spacing")
    assert d.has_value
    assert d.value == pytest.approx(3.1355)


def test_large_array_value_is_not_read(simple_file):
    r = inspect_file(simple_file)
    d = r.dataset("/entry/instrument/i0/data")
    assert not d.has_value
    assert d.value is None
    assert d.size == 500       # shape still reported


def test_inline_threshold_boundary(simple_file):
    r = inspect_file(simple_file)
    assert r.dataset("/entry/small").has_value        # 10 elements
    assert not r.dataset("/entry/justover").has_value  # 65 > 64


def test_inline_threshold_is_configurable(simple_file):
    r = HDF5Inspector(max_inline_size=1000).inspect_file(simple_file)
    assert r.dataset("/entry/instrument/i0/data").has_value


# ---------------------------------------------------------------------------
# links -- the reason a naive walk loses structure
# ---------------------------------------------------------------------------

def test_soft_links_are_recorded(simple_file):
    """visititems does not follow soft links, so without the links map
    the NXdata child names vanish entirely. Real NeXus files reach their
    signal and axes this way."""
    r = inspect_file(simple_file)
    links = r.group("/entry/data").links

    assert links["energy"]["type"] == "soft"
    assert links["energy"]["target"] == "/entry/instrument/monochromator/energy"
    assert links["i0"]["target"] == "/entry/instrument/i0/data"

    # The link names are absent from the dataset list -- which is exactly
    # why they must be captured on the group.
    assert r.dataset("/entry/data/energy") is None


def test_real_datasets_alongside_links(simple_file):
    r = inspect_file(simple_file)
    assert "mutrans" not in r.group("/entry/data").links
    assert r.dataset("/entry/data/mutrans") is not None


def test_children_lists_links_and_datasets(simple_file):
    r = inspect_file(simple_file)
    assert set(r.group("/entry/data").children) == {"energy", "i0", "mutrans"}


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def test_stats_off_by_default(simple_file):
    r = inspect_file(simple_file)
    assert r.dataset("/entry/instrument/i0/data").min_value is None


def test_stats_when_requested(simple_file):
    r = HDF5Inspector(compute_stats=True).inspect_file(simple_file)
    d = r.dataset("/entry/instrument/i0/data")
    assert d.min_value == pytest.approx(0.0)
    assert d.max_value == pytest.approx(499.0)


def test_stats_ignore_non_finite(tmp_path):
    p = tmp_path / "nan.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=np.array([1.0, np.nan, 3.0, np.inf]))
    d = HDF5Inspector(compute_stats=True).inspect_file(p).dataset("/x")
    assert d.min_value == pytest.approx(1.0)
    assert d.max_value == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# robustness -- nothing raises
# ---------------------------------------------------------------------------

def test_missing_file_warns_does_not_raise(tmp_path):
    r = inspect_file(tmp_path / "nope.h5")
    assert r.warnings
    assert not r.datasets


def test_non_hdf5_file_warns_does_not_raise(tmp_path):
    p = tmp_path / "not-hdf5.txt"
    p.write_text("this is plainly not an HDF5 file")
    r = inspect_file(p)
    assert any("HDF5" in w for w in r.warnings)
    assert not r.datasets


def test_nan_coerced_for_json(tmp_path):
    """JSON has no NaN; a scalar NaN must not poison serialization."""
    import json

    p = tmp_path / "scalarnan.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=np.float64("nan"))
    r = inspect_file(p)
    assert r.dataset("/x").value is None
    json.dumps(r.to_dict())          # must not raise


def test_to_dict_is_json_serializable(simple_file):
    import json

    text = json.dumps(inspect_file(simple_file).to_dict())
    assert "NXentry" in text


# ---------------------------------------------------------------------------
# real-file smoke test (opt-in)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("HDF5METADATA_TEST_NXS"),
    reason="set HDF5METADATA_TEST_NXS to a real NeXus file to enable",
)
def test_real_nexus_file():
    r = inspect_file(os.environ["HDF5METADATA_TEST_NXS"])
    assert not r.warnings
    assert r.groups and r.datasets
    entries = [
        g for g in r.groups
        if g.attributes.get("NX_class") == "NXentry"
    ]
    assert entries, "expected at least one NXentry"
