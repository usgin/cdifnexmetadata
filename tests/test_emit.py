"""Tests for CDIF JSON-LD emission (stage 3a).

Fixtures are synthesised, and the crosswalk is written inline, so the
suite runs offline and a failure means the emitter changed rather than
that upstream revised a mapping row.
"""
from __future__ import annotations

import pytest

h5py = pytest.importorskip("h5py")
import numpy as np  # noqa: E402

from hdf5metadata.emit import (  # noqa: E402
    NXXAS_MODE_TERMSET,
    OGC_NIL_MISSING,
    XDI_DICTIONARY,
    emit_document,
)
from hdf5metadata.inspect import inspect_file, read_nexus  # noqa: E402
from hdf5metadata.map import map_nexus  # noqa: E402

from tests.test_map import _crosswalk, _entry, _group, _row  # noqa: E402


def _emit(path, cw, legacy=None):
    insp = inspect_file(path)
    nx = read_nexus(insp)
    return emit_document(insp, nx, map_nexus(nx, cw, legacy))


def _xas_crosswalk(tmp_path):
    trans = ("nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "{}:NXdetector/data")
    return _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:exactMatch", "nxdl:NXsource/name"),
        _row("cdifxas:beamline", "skos:exactMatch", "nxdl:NXinstrument/name"),
        _row("cdifxas:incidentintensity", "skos:exactMatch",
             trans.format("i0")),
        _row("cdifxas:transmittedintensity", "skos:exactMatch",
             trans.format("itrans")),
        _row("cdifxas:monochromatorenergy", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "MONOCHROMATOR:NXmonochromator/energy"),
    )


def _scan(f, name, dets=("i0", "itrans"), facility="APS", title=None,
          start=None):
    e = _entry(f, name=name, definition="NXxas_trans")
    if title:
        e["title"] = title
    if start:
        e["start_time"] = start
    inst = _group(e, "instrument", "NXinstrument")
    inst["name"] = "13-ID-E"
    _group(inst, "source", "NXsource")["name"] = facility
    _group(inst, "monochromator", "NXmonochromator")["energy"] = np.arange(
        443.0)
    for d in dets:
        _group(inst, d, "NXdetector")["data"] = np.arange(443.0)
    return e


# ---------------------------------------------------------------------------
# structure of the document
# ---------------------------------------------------------------------------

def test_arrays_become_variables_and_scalars_become_context(tmp_path):
    """The distinction that drives the layout: a measured array is a
    variable, a scalar describes the conditions it was measured under."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1", title="Fe foil")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    names = {v["schema:name"] for v in doc["schema:variableMeasured"]}
    assert names == {"data", "energy"}          # the three arrays, by field

    # The facility is a scalar, so it is context -- never a variable.
    assert "facility" not in str(doc["schema:variableMeasured"])
    assert doc["prov:wasGeneratedBy"][0]["schema:location"][
        "schema:name"] == "APS"


def test_every_variable_round_trips_to_a_structure_component(tmp_path):
    """CDIF requires the variable and the component to reference each
    other; building them apart is how that round trip gets broken."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    used = {u for v in doc["schema:variableMeasured"] for u in v["cdif:uses"]}
    defined = {
        c["cdif:isDefinedBy_RepresentedVariable"]["@id"]
        for s in doc["cdi:isStructuredBy"]
        for c in s["cdi:has_DataStructureComponent"]
    }
    assert used and used == defined


def test_hdf5_paths_are_locators_not_indices(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    mapping = doc["cdi:isStructuredBy"][0][
        "cdi:has_DataStructureComponent"][0]["cdif:hasPhysicalMapping"]
    assert mapping["@type"] == ["cdif:LocatorMapping"]
    assert mapping["cdif:locator"].startswith("/scan1/")
    assert "cdif:index" not in mapping


def test_the_energy_axis_is_a_dimension_not_a_measure(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    by_concept = {
        c["@id"].rsplit("/", 1)[-1]: c["@type"][0]
        for s in doc["cdi:isStructuredBy"]
        for c in s["cdi:has_DataStructureComponent"]
    }
    assert by_concept["monochromatorenergy"] == "cdi:DimensionComponent"
    assert by_concept["incidentintensity"] == "cdi:MeasureComponent"


# ---------------------------------------------------------------------------
# archive of parts
# ---------------------------------------------------------------------------

def test_entries_become_parts_sharing_one_structure_by_reference(tmp_path):
    """A scan series shares its layout. DESIGN.md emits the structure
    once and has every matching part point at it, rather than repeating
    it per entry."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        for i in range(3):
            _scan(f, f"scan{i}", title=f"scan {i}")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    parts = doc["schema:distribution"][0]["schema:hasPart"]
    assert len(parts) == 3
    assert len(doc["cdi:isStructuredBy"]) == 1
    referenced = {p_["cdi:isStructuredBy"]["@id"] for p_ in parts}
    assert referenced == {doc["cdi:isStructuredBy"][0]["@id"]}
    assert all(p_["@type"] == ["schema:MediaObject"] for p_ in parts)


def test_a_structurally_different_entry_gets_its_own_structure(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "sample1", dets=("i0",))
        _scan(f, "sample2", dets=("i0",))
        _scan(f, "reference", dets=("i0", "itrans"))

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    assert len(doc["cdi:isStructuredBy"]) == 2
    assert len(doc["schema:distribution"][0]["schema:hasPart"]) == 3


def test_a_single_entry_file_still_emits_one_part(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "only")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    assert len(doc["schema:distribution"][0]["schema:hasPart"]) == 1
    assert "schema:description" not in doc      # the multi-entry note


# ---------------------------------------------------------------------------
# detected conformance
# ---------------------------------------------------------------------------

def test_profiles_are_claimed_only_where_the_content_exists(tmp_path):
    """Detect conformance, don't assert it. A file with nothing measured
    must not claim data_description."""
    bare = tmp_path / "bare.nxs"
    with h5py.File(bare, "w") as f:
        _entry(f, name="e", definition="NXxas_trans")

    result = _emit(bare, _xas_crosswalk(tmp_path))
    assert result.profiles == ["core/1.1"]
    assert "data_description/1.1" not in result.profiles

    full = tmp_path / "full.nxs"
    with h5py.File(full, "w") as f:
        _scan(f, "scan1")
    result = _emit(full, _xas_crosswalk(tmp_path))
    assert "data_description/1.1" in result.profiles
    assert "data_structure/1.1" in result.profiles
    assert "discovery/1.1" in result.profiles


def test_declared_profiles_match_what_is_written_into_the_record(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    result = _emit(p, _xas_crosswalk(tmp_path))
    written = {
        c["@id"] for c in
        result.document["schema:subjectOf"]["dcterms:conformsTo"]
    }
    assert written == {f"https://w3id.org/cdif/{p_}" for p_ in result.profiles}


# ---------------------------------------------------------------------------
# what the XAS profile mandates
# ---------------------------------------------------------------------------

def test_detection_mode_is_collected_across_entries_not_only_shared(tmp_path):
    """In FeXAS the reference foil is Transmission and the sample scans
    are Fluorescence, so the mode is exactly what does NOT agree between
    entries. Taking only agreed values would drop it entirely."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        for name, mode in (("ref", "Transmission"), ("s1", "Fluorescence")):
            e = _scan(f, name)
            _group(e, "data", "NXdata")["mode"] = mode

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:xasmeasurementmode", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/DATA:NXdata/mode"),
    )
    doc = _emit(p, cw).document
    modes = {
        t["schema:name"] for t in doc["schema:measurementTechnique"]
        if t.get("schema:inDefinedTermSet") == NXXAS_MODE_TERMSET
    }
    assert modes == {"Transmission", "Fluorescence"}


def test_mandated_keywords_carry_the_role_tag_the_profile_reads(tmp_path):
    """Both mandated keywords are DefinedTerms, so `schema:about` is what
    the profile uses to tell the edge from the element."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        scan = _group(e, "scan", "NXscan")
        xe = _group(scan, "xrayedge", "NXxrayedge")
        xe["element"] = "Fe"
        xe["edge"] = "K"

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:elementanalyzed", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/scan:NXscan/"
             "xrayedge:NXxrayedge/element"),
        _row("cdifxas:edgeanalyzed", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/scan:NXscan/"
             "xrayedge:NXxrayedge/edge"),
    )
    by_about = {
        k["schema:about"]: k for k in _emit(p, cw).document["schema:keywords"]
    }
    assert by_about["element.symbol"]["schema:name"] == "Iron"
    assert by_about["element.edge"]["schema:name"] == "K-edge"
    assert by_about["element.edge"]["schema:inDefinedTermSet"] == (
        XDI_DICTIONARY)


def test_absent_licence_is_the_nil_uri_not_an_open_one(tmp_path):
    """A NeXus file carries no licence field. Saying "looked, absent"
    beats implying the data is unrestricted."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")
    assert _emit(p, _xas_crosswalk(tmp_path)).document[
        "schema:license"] == [OGC_NIL_MISSING]


def test_date_modified_is_the_files_own_mtime(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    import datetime
    import os
    expected = datetime.datetime.fromtimestamp(
        os.stat(p).st_mtime, datetime.timezone.utc).date().isoformat()
    assert _emit(p, _xas_crosswalk(tmp_path)).document[
        "schema:dateModified"] == expected


# ---------------------------------------------------------------------------
# nothing is silently lost
# ---------------------------------------------------------------------------

def test_a_concept_with_no_binding_is_kept_and_flagged(tmp_path):
    """Dropping a value because nobody wrote a binding would be the worst
    of the available failures; dropping it silently would be worse."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        _group(e, "sample", "NXsample")["exotic_field"] = "42"

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:somethingnobodybound", "skos:exactMatch",
             "nxdl:NXsample/exotic_field"),
    )
    result = _emit(p, cw)
    props = result.document["prov:wasGeneratedBy"][0][
        "schema:additionalProperty"]
    assert any(pv["schema:value"] == "42" for pv in props)
    assert any("no CDIF binding" in w for w in result.warnings)


def test_legacy_provenance_survives_into_the_document(tmp_path):
    """A consumer should be able to see a value came from a non-standard
    layout without re-running the extractor."""
    from hdf5metadata.map.legacy import load_legacy

    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        _group(_group(e, "scan", "NXscan"), "xrayedge", "NXxrayedge")
        e["scan"]["edge_energy"] = "7112.000"

    lg_file = tmp_path / "legacy.tsv"
    lg_file.write_text(
        "concept\tconvention\tpath\tconfidence\tcomment\n"
        "cdifxas:edgeenergy\tgsecars-athena\t/scan:NXscan/edge_energy\t0.9\t\n",
        encoding="utf-8")
    cw = _crosswalk(tmp_path, _row("cdifxas:edgeenergy", "skos:exactMatch",
                                   "nxdl:NXabsorption_edge/energy"))
    doc = _emit(p, cw, load_legacy(lg_file)).document
    # Edge energy is a property of the measurement, not of a piece of
    # hardware, so the profile puts it on the activity.
    props = doc["prov:wasGeneratedBy"][0]["schema:additionalProperty"]
    edge = next(pv for pv in props if pv["schema:value"] == "7112.000")
    assert "gsecars-athena" in edge["schema:description"]


def test_a_file_with_no_nexus_content_still_emits_core(tmp_path):
    p = tmp_path / "plain.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=np.arange(10))

    result = _emit(p, _xas_crosswalk(tmp_path))
    assert result.profiles == ["core/1.1"]
    assert result.document["schema:name"] == "plain"
    assert result.document["schema:distribution"][0]["spdx:checksum"][
        "spdx:algorithm"] == "spdx:checksumAlgorithm_sha256"


def test_prov_used_nests_the_instrument_so_framing_keeps_it(tmp_path):
    """A flat prov:used validates as raw JSON and then disappears when
    framed, because a frame drops what it does not declare. The profile
    frame declares schema:instrument beneath prov:used, so that is the
    shape the entity has to take."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    used = _emit(p, _xas_crosswalk(tmp_path)).document[
        "prov:wasGeneratedBy"][0]["prov:used"]
    assert used and all("schema:instrument" in u for u in used)
    assert all(u["@type"] == ["schema:Thing", "prov:Entity"] for u in used)
    inst = used[0]["schema:instrument"]
    assert inst["@type"] == ["schema:Product", "schema:Thing"]
    assert inst["schema:name"] == "13-ID-E"


def test_the_catalog_record_is_an_iri_not_a_blank_node(tmp_path):
    """SHACL targets the catalog record by identity, and nothing outside
    a document can refer to a blank node inside it."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    record = doc["schema:subjectOf"]
    assert record["@id"] == doc["@id"] + "/metadata"
    assert record["schema:about"]["@id"] == doc["@id"]


def test_a_variable_is_described_in_the_writers_own_words_when_it_can_be(
    tmp_path,
):
    """NeXus `long_name` is the field describing itself in this file,
    which beats any label the crosswalk could supply generically."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        e["instrument/i0"]["data"].attrs["long_name"] = "I0 ion chamber"

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    described = {
        v["schema:description"] for v in doc["schema:variableMeasured"]
    }
    assert "I0 ion chamber" in described
    # And a field without one still gets a readable label, never a blank.
    assert all(v["schema:description"].strip() for v
               in doc["schema:variableMeasured"])


def test_physical_mapping_points_back_at_the_variable_it_formats(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    ivs = {v["@id"] for v in doc["schema:variableMeasured"]}
    formatted = {
        c["cdif:hasPhysicalMapping"]["cdif:formats_InstanceVariable"]["@id"]
        for s in doc["cdi:isStructuredBy"]
        for c in s["cdi:has_DataStructureComponent"]
    }
    assert formatted and formatted <= ivs


def test_beamline_source_and_monochromator_are_separate_peers(tmp_path):
    """The profile distinguishes them: the beamline is where the
    measurement happened, the source is what made the X-rays, the
    monochromator is what selected their energy. Folding source into
    beamline reads sensibly and satisfies neither constraint."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        src = e["instrument/source"]
        src["probe"] = "X-ray"
        src["type"] = "Synchrotron X-ray Source"
        mono = e["instrument/monochromator"]
        cr = mono.create_group("crystal")
        cr.attrs["NX_class"] = "NXcrystal"
        cr["d_spacing"] = 1.6375

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:beamline", "skos:exactMatch", "nxdl:NXinstrument/name"),
        _row("cdifxas:probe", "skos:exactMatch", "nxdl:NXsource/probe"),
        _row("cdifxas:xraysourcetype", "skos:exactMatch",
             "nxdl:NXsource/type"),
        _row("cdifxas:dspacing", "skos:exactMatch",
             "nxdl:NXcrystal/d_spacing"),
    )
    used = _emit(p, cw).document["prov:wasGeneratedBy"][0]["prov:used"]
    types = [u["schema:instrument"]["schema:additionalType"][0]["@id"]
             for u in used]
    assert types == ["xas:beamline", "xas:source", "xas:xraymonochromator"]

    source = next(u["schema:instrument"] for u in used
                  if u["schema:instrument"]["schema:additionalType"][0]["@id"]
                  == "xas:source")
    ids = {pv["schema:propertyID"][0]["@id"]
           for pv in source["schema:additionalProperty"]}
    assert ids == {"xas:probe", "xas:xraysourcetype"}
    probe = next(pv for pv in source["schema:additionalProperty"]
                 if pv["schema:propertyID"][0]["@id"] == "xas:probe")
    assert probe["schema:name"] == "Probe"   # the profile matches on this


def test_property_values_are_strings_even_when_the_file_says_otherwise(
    tmp_path,
):
    """A d-spacing emitted as a JSON float, or a reflection plane emitted
    as [3, 1, 1], silently fails the constraint that says the
    monochromator must report those values at all."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        cr = e["instrument/monochromator"].create_group("crystal")
        cr.attrs["NX_class"] = "NXcrystal"
        cr["d_spacing"] = 1.6375
        cr["d_spacing"].attrs["units"] = "Angstroms"
        cr["reflection"] = np.array([3, 1, 1])

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:dspacing", "skos:exactMatch",
             "nxdl:NXcrystal/d_spacing"),
        _row("cdifxas:reflectionplane", "skos:exactMatch",
             "nxdl:NXcrystal/reflection"),
    )
    mono = _emit(p, cw).document["prov:wasGeneratedBy"][0][
        "prov:used"][0]["schema:instrument"]
    by_id = {pv["schema:propertyID"][0]["@id"]: pv
             for pv in mono["schema:additionalProperty"]}
    assert by_id["xas:dspacing"]["schema:value"].startswith("1.6375")
    assert by_id["xas:dspacing"]["schema:unitText"]      # value AND unit
    assert by_id["xas:reflectionplane"]["schema:value"] == "3 1 1"


def test_property_ids_are_the_concept_locals_the_profile_enumerates(tmp_path):
    """The profile enumerates the very same tokens, so a tidier spelling
    here -- an earlier version had xas:xray_source_type -- produces a
    document that cannot satisfy it."""
    from hdf5metadata.emit import CONCEPT_SLOTS

    for concept, slot in CONCEPT_SLOTS.items():
        if slot.target in ("keyword", "technique", "facility", "instrument"):
            continue
        assert slot.prop == concept.split(":", 1)[-1], (
            f"{concept} emits xas:{slot.prop}, which the profile does not "
            f"name"
        )
