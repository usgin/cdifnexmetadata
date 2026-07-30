"""Tests for NeXus interpretation (stage 1b).

Fixtures are synthesised so the suite runs offline with no binary test
data. Each resolution tier gets a fixture that isolates it.
"""
from __future__ import annotations

import os

import pytest

h5py = pytest.importorskip("h5py")
import numpy as np  # noqa: E402

from cdifnexmetadata.inspect import inspect_file, is_nexus, read_nexus  # noqa: E402
from cdifnexmetadata.inspect.nexus import (  # noqa: E402
    TIER_HEURISTIC,
    TIER_LINK,
    TIER_NXDL,
    TIER_SIGNAL_ATTR,
)


def _entry(f, name="entry", definition=None, nx_class="NXentry"):
    g = f.create_group(name)
    g.attrs["NX_class"] = nx_class
    if definition:
        g["definition"] = definition
    return g


# ---------------------------------------------------------------------------
# sniffing
# ---------------------------------------------------------------------------

def test_plain_hdf5_is_not_nexus(tmp_path):
    p = tmp_path / "plain.h5"
    with h5py.File(p, "w") as f:
        f.create_group("stuff").create_dataset("x", data=np.arange(10))
    r = inspect_file(p)
    assert not is_nexus(r)
    nx = read_nexus(r)
    assert not nx.is_nexus
    assert nx.entries == []
    assert not nx.warnings          # not-NeXus is a valid outcome


def test_extension_is_not_evidence(tmp_path):
    """A .nxs file with no NX_class markers is not NeXus."""
    p = tmp_path / "misleading.nxs"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=np.arange(10))
    assert not is_nexus(inspect_file(p))


def test_nx_class_marker_is_evidence(tmp_path):
    p = tmp_path / "e.h5"          # note: not .nxs
    with h5py.File(p, "w") as f:
        _entry(f)
    assert is_nexus(inspect_file(p))


def test_markers_without_entry_warns(tmp_path):
    p = tmp_path / "noentry.nxs"
    with h5py.File(p, "w") as f:
        g = f.create_group("thing")
        g.attrs["NX_class"] = "NXinstrument"
    nx = read_nexus(inspect_file(p))
    assert nx.is_nexus
    assert any("NXentry" in w for w in nx.warnings)


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_entry(tmp_path):
    p = tmp_path / "multi.nxs"
    with h5py.File(p, "w") as f:
        f.attrs["default"] = "scan1"
        for i in (1, 2, 3):
            e = _entry(f, f"scan{i}", definition="NXxas")
            e["title"] = f"scan number {i}"
            e["start_time"] = f"2026-07-2{i}T10:00:00"
    return p


def test_finds_all_entries(multi_entry):
    nx = read_nexus(inspect_file(multi_entry))
    assert len(nx.entries) == 3
    assert nx.is_multi_entry
    assert {e.name for e in nx.entries} == {"scan1", "scan2", "scan3"}


def test_default_entry_recorded(multi_entry):
    nx = read_nexus(inspect_file(multi_entry))
    assert nx.default_entry == "scan1"
    assert nx.entry("scan1") is not None


def test_dangling_default_warns(tmp_path):
    """Real files do this -- FeXAS.nxs names a default that is not an
    entry name. Surface it rather than silently ignoring."""
    p = tmp_path / "dangling.nxs"
    with h5py.File(p, "w") as f:
        f.attrs["default"] = "nosuchthing"
        _entry(f, "actual")
    nx = read_nexus(inspect_file(p))
    assert any("default" in w for w in nx.warnings)


def test_entry_convenience_fields(multi_entry):
    e = read_nexus(inspect_file(multi_entry)).entry("scan2")
    assert e.definition == "NXxas"
    assert e.title == "scan number 2"
    assert e.start_time.startswith("2026-07-22")


def test_definitions_collected(multi_entry):
    assert read_nexus(inspect_file(multi_entry)).definitions == ["NXxas"]


def test_single_entry_not_flagged_multi(tmp_path):
    p = tmp_path / "one.nxs"
    with h5py.File(p, "w") as f:
        _entry(f)
    assert not read_nexus(inspect_file(p)).is_multi_entry


# ---------------------------------------------------------------------------
# class-indexed navigation
# ---------------------------------------------------------------------------

@pytest.fixture
def instrument_file(tmp_path):
    p = tmp_path / "instr.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        instr = e.create_group("instrument")
        instr.attrs["NX_class"] = "NXinstrument"
        instr["name"] = "B18"
        src = instr.create_group("source")
        src.attrs["NX_class"] = "NXsource"
        src["name"] = "Diamond"
        src["probe"] = "x-ray"
        for det in ("i0", "itrans"):
            d = instr.create_group(det)
            d.attrs["NX_class"] = "NXdetector"
            d.create_dataset("data", data=np.arange(100.0))
        mono = instr.create_group("monochromator")
        mono.attrs["NX_class"] = "NXmonochromator"
        cry = mono.create_group("crystal")
        cry.attrs["NX_class"] = "NXcrystal"
        cry["d_spacing"] = 3.1355
    return p


def test_find_by_class_is_recursive(instrument_file):
    e = read_nexus(inspect_file(instrument_file)).entries[0]
    assert [g.name for g in e.find("NXinstrument")] == ["instrument"]
    assert sorted(g.name for g in e.find("NXdetector")) == ["i0", "itrans"]
    # NXcrystal is nested two levels down.
    assert [g.name for g in e.find("NXcrystal")] == ["crystal"]


def test_field_value_searches_depth(instrument_file):
    """d_spacing lives at instrument/monochromator/crystal -- the caller
    should not have to know that path."""
    e = read_nexus(inspect_file(instrument_file)).entries[0]
    assert e.field_value("d_spacing") == pytest.approx(3.1355)


def test_field_value_absent_returns_none(instrument_file):
    e = read_nexus(inspect_file(instrument_file)).entries[0]
    assert e.field_value("no_such_field") is None


def test_first_returns_none_when_absent(instrument_file):
    e = read_nexus(inspect_file(instrument_file)).entries[0]
    assert e.first("NXslit") is None


# ---------------------------------------------------------------------------
# tier 1: @signal / @axes
# ---------------------------------------------------------------------------

def test_tier1_signal_and_axes_attributes(tmp_path):
    p = tmp_path / "t1.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f)
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d.attrs["signal"] = "counts"
        d.attrs["axes"] = "energy"
        d.create_dataset("counts", data=np.arange(50.0))
        en = d.create_dataset("energy", data=np.linspace(7000, 7100, 50))
        en.attrs["units"] = "eV"
    nd = read_nexus(inspect_file(p)).entries[0].data[0]
    assert nd.resolution == TIER_SIGNAL_ATTR
    assert [s.name for s in nd.signals] == ["counts"]
    assert [a.name for a in nd.axes] == ["energy"]
    assert nd.axes[0].units == "eV"


def test_tier1_dangling_signal_warns(tmp_path):
    p = tmp_path / "t1bad.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f)
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d.attrs["signal"] = "missing_field"
        d.create_dataset("something", data=np.arange(20.0))
    nd = read_nexus(inspect_file(p)).entries[0].data[0]
    assert any("missing_field" in w for w in nd.warnings)


# ---------------------------------------------------------------------------
# tier 2: link targets -- the tier real files actually rely on
# ---------------------------------------------------------------------------

@pytest.fixture
def linked_file(tmp_path):
    """Mirrors FeXAS.nxs: no @signal/@axes, NXdata reaches arrays by soft
    link, plus derived arrays stored directly in the group."""
    p = tmp_path / "linked.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        instr = e.create_group("instrument")
        instr.attrs["NX_class"] = "NXinstrument"
        for det in ("i0", "itrans"):
            g = instr.create_group(det)
            g.attrs["NX_class"] = "NXdetector"
            g.create_dataset("data", data=np.arange(100.0))
        mono = instr.create_group("monochromator")
        mono.attrs["NX_class"] = "NXmonochromator"
        en = mono.create_dataset("energy", data=np.linspace(7000, 7100, 100))
        en.attrs["units"] = "eV"

        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d["energy"] = h5py.SoftLink("/entry/instrument/monochromator/energy")
        d["i0"] = h5py.SoftLink("/entry/instrument/i0/data")
        d["itrans"] = h5py.SoftLink("/entry/instrument/itrans/data")
        d.create_dataset("mutrans", data=np.zeros(100))   # derived, not linked
    return p


def test_tier2_link_target_class_decides_role(linked_file):
    nd = read_nexus(inspect_file(linked_file)).entries[0].data[0]
    axes = {a.name: a for a in nd.axes}
    sigs = {s.name: s for s in nd.signals}

    # From NXmonochromator -> coordinate.
    assert axes["energy"].resolution == TIER_LINK
    assert axes["energy"].source_class == "NXmonochromator"
    assert axes["energy"].units == "eV"

    # From NXdetector -> measurement.
    assert sigs["i0"].resolution == TIER_LINK
    assert sigs["i0"].source_class == "NXdetector"
    assert sigs["itrans"].source_class == "NXdetector"


def test_tier2_does_not_drop_unlinked_arrays(linked_file):
    """Regression: stopping at the first productive tier lost mutrans --
    the derived absorption coefficient, usually the array of most
    interest."""
    nd = read_nexus(inspect_file(linked_file)).entries[0].data[0]
    sigs = {s.name: s for s in nd.signals}
    assert "mutrans" in sigs
    assert sigs["mutrans"].resolution == TIER_HEURISTIC
    assert any("not accounted for" in w for w in nd.warnings)


def test_mixed_tiers_report_lowest(linked_file):
    nd = read_nexus(inspect_file(linked_file)).entries[0].data[0]
    assert nd.resolution == TIER_HEURISTIC   # least reliable tier used


# ---------------------------------------------------------------------------
# tier 3: NXDL resolver
# ---------------------------------------------------------------------------

def test_tier3_uses_supplied_resolver(tmp_path):
    p = tmp_path / "t3.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXmytechnique")
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d.create_dataset("aaa", data=np.arange(30.0))
        d.create_dataset("bbb", data=np.arange(30.0))

    class Spec:
        signal_fields = ("bbb",)
        axis_fields = ("aaa",)

    def resolver(name):
        assert name == "NXmytechnique"
        return Spec()

    nx = read_nexus(inspect_file(p), nxdl_resolver=resolver)
    nd = nx.entries[0].data[0]
    assert [s.name for s in nd.signals] == ["bbb"]
    assert [a.name for a in nd.axes] == ["aaa"]
    assert nd.resolution == TIER_NXDL


def test_tier3_resolver_failure_is_not_fatal(tmp_path):
    p = tmp_path / "t3bad.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXwhatever")
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d.create_dataset("counts", data=np.arange(30.0))

    def boom(name):
        raise RuntimeError("network is down")

    nd = read_nexus(inspect_file(p), nxdl_resolver=boom).entries[0].data[0]
    assert [s.name for s in nd.signals] == ["counts"]   # fell through
    assert nd.resolution == TIER_HEURISTIC


def test_no_resolver_falls_through(tmp_path):
    p = tmp_path / "t3none.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXsomething")
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d.create_dataset("counts", data=np.arange(30.0))
    nd = read_nexus(inspect_file(p)).entries[0].data[0]
    assert nd.resolution == TIER_HEURISTIC


# ---------------------------------------------------------------------------
# tier 4: heuristics
# ---------------------------------------------------------------------------

def test_tier4_name_hints_pick_axes(tmp_path):
    p = tmp_path / "t4.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f)
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d.create_dataset("energy", data=np.arange(40.0))
        d.create_dataset("absorbance", data=np.arange(40.0))
    nd = read_nexus(inspect_file(p)).entries[0].data[0]
    assert [a.name for a in nd.axes] == ["energy"]
    assert [s.name for s in nd.signals] == ["absorbance"]
    assert any("heuristics" in w for w in nd.warnings)


def test_no_signal_warns(tmp_path):
    p = tmp_path / "empty.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f)
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"
        d["scalar_only"] = 1.0
    nd = read_nexus(inspect_file(p)).entries[0].data[0]
    assert any("no signal" in w for w in nd.warnings)


# ---------------------------------------------------------------------------
# real-file smoke test (opt-in)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("HDF5METADATA_TEST_NXS"),
    reason="set HDF5METADATA_TEST_NXS to a real NeXus file to enable",
)
def test_real_file_resolves():
    nx = read_nexus(inspect_file(os.environ["HDF5METADATA_TEST_NXS"]))
    assert nx.is_nexus
    assert nx.entries
    e = nx.entries[0]
    assert e.definition
    assert e.data, "expected at least one NXdata"
    assert e.data[0].signals, "expected at least one signal"


def test_a_list_valued_definition_is_read_as_one_name(tmp_path):
    """Soleil, SLS and the APS area-detector writer store `definition` as
    a one-element array. A list is unhashable, so this used to take down
    the whole read the moment distinct definitions were collected --
    eight real facility files in the example corpus, from four
    institutions."""
    p = tmp_path / "listdef.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, name="entry")
        e["definition"] = np.array([b"NXsas"])

    nx = read_nexus(inspect_file(p))
    assert nx.entries[0].definition == "NXsas"
    assert nx.definitions == ["NXsas"]      # hashable, so this cannot throw


def test_an_empty_definition_array_is_no_definition(tmp_path):
    p = tmp_path / "emptydef.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, name="entry")
        e["definition"] = np.array([b""])

    assert read_nexus(inspect_file(p)).entries[0].definition is None
