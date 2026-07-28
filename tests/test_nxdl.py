"""Tests for NXDL fetching, parsing and the tier-3 resolver.

Almost everything here runs offline: parsing tests use inline XML, and
repository tests pre-populate the cache. Only the tests marked
``network`` touch GitHub; run with ``-m "not network"`` to skip them.
"""
from __future__ import annotations

import json

import pytest

from hdf5metadata.nxdl import (
    DEFINITION_DIRS,
    NXDLDefinition,
    Repository,
    load,
    make_resolver,
    parse,
)

NS = 'xmlns="http://definition.nexusformat.org/nxdl/3.1"'


def _nxdl(body: str, name="NXtest", category="application", extends=None) -> str:
    ext = f' extends="{extends}"' if extends else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<definition {NS} name="{name}" category="{category}"{ext}>'
        f"{body}</definition>"
    )


def _seed(cache_dir, ref, directory, name, xml):
    p = cache_dir / ref / directory / f"{name}.nxdl.xml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(xml, encoding="utf-8")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_parses_category_and_extends():
    d = parse(_nxdl("", extends="NXxas"), "NXxas_trans")
    assert d.category == "application"
    assert d.extends == "NXxas"
    assert d.is_application


def test_parses_nested_groups_and_fields():
    d = parse(_nxdl(
        '<group type="NXentry">'
        '  <field name="definition" type="NX_CHAR"/>'
        '  <group type="NXinstrument" name="instrument">'
        '    <group type="NXdetector" name="i0">'
        '      <field name="data" type="NX_NUMBER"/>'
        "    </group>"
        "  </group>"
        "</group>"
    ), "NXtest")
    assert [g.name for g in d.find("NXdetector")] == ["i0"]
    assert d.fields_named("definition")
    assert d.fields_named("data")[0].type == "NX_NUMBER"


def test_parses_units_and_doc():
    d = parse(_nxdl(
        '<group type="NXentry">'
        '  <field name="energy" type="NX_FLOAT" units="NX_ENERGY">'
        "    <doc>Incident photon energy.</doc>"
        "  </field>"
        "</group>"
    ), "NXtest")
    f = d.fields_named("energy")[0]
    assert f.units == "NX_ENERGY"
    assert "Incident photon energy" in f.doc


def test_enumeration_is_extracted():
    """Enumerations are free controlled vocabulary -- edges, modes, etc."""
    d = parse(_nxdl(
        '<group type="NXentry">'
        '  <field name="mode">'
        "    <enumeration>"
        '      <item value="Transmission"/><item value="Fluorescence"/>'
        "    </enumeration>"
        "  </field>"
        "</group>"
    ), "NXtest")
    assert d.enumeration_for("mode") == ["Transmission", "Fluorescence"]


def test_enumeration_absent_is_empty_not_error():
    d = parse(_nxdl('<group type="NXentry"><field name="x"/></group>'), "NXtest")
    assert d.enumeration_for("x") == []
    assert d.enumeration_for("nonexistent") == []


def test_unknown_elements_are_ignored_not_fatal():
    """The grammar is stable but the definitions are being revised -- a
    tool that rejects a file for gaining an element is worse than one
    that reads what it understands."""
    d = parse(_nxdl(
        '<group type="NXentry">'
        '  <field name="ok"/>'
        '  <link name="alias" target="/somewhere"/>'
        '  <symbols><symbol name="nP"><doc>points</doc></symbol></symbols>'
        '  <futureThing name="from 2030" wat="yes"/>'
        "</group>"
    ), "NXtest")
    assert d.fields_named("ok")
    assert not d.warnings


def test_malformed_xml_warns_does_not_raise():
    d = parse("<definition><unclosed>", "NXbroken")
    assert d.warnings
    assert d.root is None
    assert d.signal_fields == ()      # still usable as a resolver result


# ---------------------------------------------------------------------------
# signal / axis discovery -- three NXDL generations
# ---------------------------------------------------------------------------

def test_field_level_axis_convention():
    """Upstream NXxas marks the axis with axis="1" on the field."""
    d = parse(_nxdl(
        '<group type="NXentry"><group type="NXdata">'
        '  <field name="energy" axis="1" type="NX_FLOAT"/>'
        '  <field name="absorbed_beam" signal="1" type="NX_NUMBER"/>'
        "</group></group>"
    ), "NXxas")
    assert d.axis_fields == ("energy",)
    assert d.signal_fields == ("absorbed_beam",)


def test_group_level_signal_axes_attributes():
    d = parse(_nxdl(
        '<group type="NXentry"><group type="NXdata">'
        '  <attribute name="signal"><enumeration>'
        '    <item value="counts"/></enumeration></attribute>'
        '  <attribute name="axes"><enumeration>'
        '    <item value="two_theta"/></enumeration></attribute>'
        '  <field name="counts"/><field name="two_theta"/>'
        "</group></group>"
    ), "NXtest")
    assert d.signal_fields == ("counts",)
    assert d.axis_fields == ("two_theta",)


def test_no_hints_falls_back_to_naming_within_nxdata():
    d = parse(_nxdl(
        '<group type="NXentry"><group type="NXdata">'
        '  <field name="energy"/><field name="intensity"/>'
        "</group></group>"
    ), "NXtest")
    assert d.axis_fields == ("energy",)
    assert d.signal_fields == ("intensity",)


def test_declared_hints_are_never_overridden_by_naming():
    """A declared answer must win over a guess, even when the guess
    would look more plausible."""
    d = parse(_nxdl(
        '<group type="NXentry"><group type="NXdata">'
        '  <field name="energy" signal="1"/>'   # perverse but declared
        '  <field name="intensity"/>'
        "</group></group>"
    ), "NXtest")
    assert d.signal_fields == ("energy",)
    assert "intensity" not in d.axis_fields


def test_no_nxdata_yields_nothing():
    """Honest emptiness: the caller must fall through to heuristics
    rather than be handed a guess dressed as authority."""
    d = parse(_nxdl(
        '<group type="NXentry"><field name="intensity"/></group>'
    ), "NXtest")
    assert d.signal_fields == ()
    assert d.axis_fields == ()


# ---------------------------------------------------------------------------
# repository: cache, offline, search order
# ---------------------------------------------------------------------------

def test_fetch_from_cache_searches_every_directory(tmp_path):
    """NXxas has already moved between directories once -- nothing may
    assume which one holds a definition."""
    for directory in DEFINITION_DIRS:
        cache = tmp_path / directory
        _seed(cache, "REF", directory, "NXthing", _nxdl("<group/>"))
        repo = Repository(ref="REF", cache_dir=cache, offline=True)
        got = repo.fetch("NXthing")
        assert got is not None
        assert got[1].directory == directory


def test_missing_definition_returns_none_and_warns(tmp_path):
    repo = Repository(ref="REF", cache_dir=tmp_path, offline=True)
    assert repo.fetch("NXnope") is None
    assert repo.warnings


def test_offline_never_hits_network(tmp_path):
    repo = Repository(ref="REF", cache_dir=tmp_path, offline=True)
    repo.timeout = 0.001              # would fail loudly if used
    assert repo.fetch("NXanything") is None
    assert load("NXanything", repo) is None


def test_cache_is_keyed_by_ref(tmp_path):
    """Changing the pin must not read files fetched under the old one."""
    _seed(tmp_path, "REF_A", "base_classes", "NXthing", _nxdl("<group/>"))
    assert Repository(ref="REF_A", cache_dir=tmp_path,
                      offline=True).fetch("NXthing") is not None
    assert Repository(ref="REF_B", cache_dir=tmp_path,
                      offline=True).fetch("NXthing") is None


def test_index_read_from_cache(tmp_path):
    p = tmp_path / "REF" / "_index.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(["NXone", "NXtwo"]), encoding="utf-8")
    repo = Repository(ref="REF", cache_dir=tmp_path, offline=True)
    assert repo.list_definitions() == ["NXone", "NXtwo"]


# ---------------------------------------------------------------------------
# inheritance
# ---------------------------------------------------------------------------

def test_extends_merges_parent_structure(tmp_path):
    """NXxas_trans extends NXxas, and the parent holds the element/edge
    structure the child does not repeat."""
    _seed(tmp_path, "REF", "contributed_definitions", "NXbase", _nxdl(
        '<group type="NXentry">'
        '  <group type="NXelement" name="element">'
        '    <field name="name"/></group>'
        '  <field name="intensity"/>'
        "</group>", name="NXbase", extends=None,
    ))
    _seed(tmp_path, "REF", "contributed_definitions", "NXchild", _nxdl(
        '<group type="NXentry">'
        '  <group type="NXinstrument" name="instrument">'
        '    <group type="NXdetector" name="i0">'
        '      <field name="data"/></group></group>'
        "</group>", name="NXchild", extends="NXbase",
    ))
    d = load("NXchild", Repository(ref="REF", cache_dir=tmp_path, offline=True))
    assert d.inherited == ["NXbase"]
    assert [g.name for g in d.find("NXelement")] == ["element"]   # from parent
    assert [g.name for g in d.find("NXdetector")] == ["i0"]       # own
    assert d.fields_named("intensity")                            # from parent


def test_child_wins_over_parent(tmp_path):
    _seed(tmp_path, "REF", "base_classes", "NXp", _nxdl(
        '<group type="NXentry"><field name="x" units="NX_LENGTH"/></group>',
        name="NXp",
    ))
    _seed(tmp_path, "REF", "base_classes", "NXc", _nxdl(
        '<group type="NXentry"><field name="x" units="NX_ENERGY"/></group>',
        name="NXc", extends="NXp",
    ))
    d = load("NXc", Repository(ref="REF", cache_dir=tmp_path, offline=True))
    assert d.fields_named("x")[0].units == "NX_ENERGY"


def test_unloadable_parent_warns_but_still_returns(tmp_path):
    _seed(tmp_path, "REF", "base_classes", "NXc", _nxdl(
        '<group type="NXentry"><field name="own"/></group>',
        name="NXc", extends="NXmissingparent",
    ))
    d = load("NXc", Repository(ref="REF", cache_dir=tmp_path, offline=True))
    assert d is not None
    assert d.fields_named("own")
    assert any("could not be loaded" in w for w in d.warnings)


def test_cyclic_extends_terminates(tmp_path):
    for a, b in (("NXa", "NXb"), ("NXb", "NXa")):
        _seed(tmp_path, "REF", "base_classes", a, _nxdl(
            '<group type="NXentry"/>', name=a, extends=b))
    d = load("NXa", Repository(ref="REF", cache_dir=tmp_path, offline=True))
    assert d is not None          # terminated rather than recursing forever


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------

def test_resolver_memoises(tmp_path):
    """A 26-entry file must resolve each definition once, not once per
    entry."""
    _seed(tmp_path, "REF", "base_classes", "NXthing",
          _nxdl('<group type="NXentry"/>', name="NXthing"))
    repo = Repository(ref="REF", cache_dir=tmp_path, offline=True)

    reads = []
    original = repo.fetch

    def counting(name):
        reads.append(name)
        return original(name)

    repo.fetch = counting                       # type: ignore[method-assign]
    resolve = make_resolver(repo)
    for _ in range(26):
        resolve("NXthing")
    assert reads.count("NXthing") == 1


def test_resolver_returns_none_for_unknown(tmp_path):
    resolve = make_resolver(
        Repository(ref="REF", cache_dir=tmp_path, offline=True)
    )
    assert resolve("NXnope") is None


def test_resolver_drives_tier3(tmp_path):
    """End-to-end: a definition with declared hints resolves an NXdata
    that has none of its own."""
    h5py = pytest.importorskip("h5py")
    import numpy as np

    from hdf5metadata.inspect import inspect_file, read_nexus
    from hdf5metadata.inspect.nexus import TIER_NXDL

    _seed(tmp_path / "cache", "REF", "applications", "NXdemo", _nxdl(
        '<group type="NXentry"><group type="NXdata">'
        '  <field name="theta" axis="1"/>'
        '  <field name="counts" signal="1"/>'
        "</group></group>", name="NXdemo",
    ))

    p = tmp_path / "demo.nxs"
    with h5py.File(p, "w") as f:
        e = f.create_group("entry")
        e.attrs["NX_class"] = "NXentry"
        e["definition"] = "NXdemo"
        d = e.create_group("data")
        d.attrs["NX_class"] = "NXdata"          # no @signal, no links
        d.create_dataset("theta", data=np.arange(20.0))
        d.create_dataset("counts", data=np.arange(20.0))

    resolve = make_resolver(
        Repository(ref="REF", cache_dir=tmp_path / "cache", offline=True)
    )
    nd = read_nexus(inspect_file(p), nxdl_resolver=resolve).entries[0].data[0]
    assert nd.resolution == TIER_NXDL
    assert [s.name for s in nd.signals] == ["counts"]
    assert [a.name for a in nd.axes] == ["theta"]


# ---------------------------------------------------------------------------
# network (opt-in)
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_real_definitions_load(tmp_path):
    repo = Repository(cache_dir=tmp_path)
    d = load("NXxas_trans", repo)
    assert d is not None
    assert d.extends == "NXxas"
    assert d.inherited == ["NXxas"]
    assert d.source.directory == "contributed_definitions"
    assert sorted(g.name for g in d.find("NXdetector")) == ["i0", "iref", "itrans"]
    # Inherited from the base, not declared in the child.
    assert d.find("NXelement")


@pytest.mark.network
def test_real_edge_enumeration(tmp_path):
    d = load("NXabsorption_edge", Repository(cache_dir=tmp_path))
    values = d.enumeration_for("name")
    assert len(values) == 39
    assert "K" in values and "L3" in values
