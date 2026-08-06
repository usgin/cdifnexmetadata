"""Tests for CDIF JSON-LD emission (stage 3a).

Fixtures are synthesised, and the crosswalk is written inline, so the
suite runs offline and a failure means the emitter changed rather than
that upstream revised a mapping row.
"""
from __future__ import annotations

import pytest

h5py = pytest.importorskip("h5py")
import numpy as np  # noqa: E402

from cdifnexmetadata.emit import (  # noqa: E402
    NXXAS_MODE_TERMSET,
    OGC_NIL_MISSING,
    XDI_DICTIONARY,
    emit_document,
)
from cdifnexmetadata.inspect import inspect_file, read_nexus  # noqa: E402
from cdifnexmetadata.map import map_nexus  # noqa: E402

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


def _structures(doc):
    """Every distinct structure in a document, wherever it is stated.

    Structures sit on the parts that have them, inline on the first part
    and by @id reference on the rest, so collecting them means walking
    the parts and keeping the ones defined rather than referenced. A file
    with no parts states its structure on the distribution instead.
    """
    dist = doc["schema:distribution"][0]
    found = {}
    for part in dist.get("schema:hasPart", []):
        s = part.get("cdi:isStructuredBy")
        if isinstance(s, dict) and "cdi:has_DataStructureComponent" in s:
            found[s["@id"]] = s
    if not found:
        for s in dist.get("cdi:isStructuredBy", []):
            found[s["@id"]] = s
    return list(found.values())

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
        for s in _structures(doc)
        for c in s["cdi:has_DataStructureComponent"]
    }
    assert used and used == defined


def test_hdf5_paths_are_locators_not_indices(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "scan1")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    mapping = _structures(doc)[0][
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
        for s in _structures(doc)
        for c in s["cdi:has_DataStructureComponent"]
    }
    assert by_concept["monochromatorenergy"] == "cdi:DimensionComponent"
    assert by_concept["incidentintensity"] == "cdi:MeasureComponent"


# ---------------------------------------------------------------------------
# archive of parts
# ---------------------------------------------------------------------------

def test_entries_become_parts_sharing_one_structure_by_reference(tmp_path):
    """A scan series shares its layout. The emitter writes the structure
    once and has every matching part point at it, rather than repeating
    it per entry."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        for i in range(3):
            _scan(f, f"scan{i}", title=f"scan {i}")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    parts = doc["schema:distribution"][0]["schema:hasPart"]
    assert len(parts) == 3
    # One structure, stated once.
    structures = _structures(doc)
    assert len(structures) == 1
    # Every part names it, and only the first states it.
    assert {p_["cdi:isStructuredBy"]["@id"] for p_ in parts} == {
        structures[0]["@id"]}
    inline = [p_ for p_ in parts
              if "cdi:has_DataStructureComponent" in p_["cdi:isStructuredBy"]]
    assert len(inline) == 1
    # A part is the bytes AND a dataset: an NXentry has its own
    # variables and its own structure, which MediaObject alone cannot say.
    assert all(p_["@type"] == ["schema:MediaObject", "schema:Dataset"]
               for p_ in parts)


def test_a_structurally_different_entry_gets_its_own_structure(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "sample1", dets=("i0",))
        _scan(f, "sample2", dets=("i0",))
        _scan(f, "reference", dets=("i0", "itrans"))

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    assert len(_structures(doc)) == 2
    assert len(doc["schema:distribution"][0]["schema:hasPart"]) == 3


def test_each_part_records_when_its_own_entry_was_measured(tmp_path):
    """A scan series measured over three days has one acquisition per
    entry. The file-level event spans them all, which answers "when did
    this batch run" -- not "when was this spectrum taken"."""
    pth = tmp_path / "f.nxs"
    with h5py.File(pth, "w") as f:
        for i, (s, e) in enumerate((
            ("2020-08-10T09:18:48", "2020-08-10T09:22:32"),
            ("2020-08-11T21:14:18", "2020-08-11T21:29:09"),
            ("2020-08-12T21:57:17", "2020-08-12T22:12:09"),
        )):
            entry = _scan(f, f"scan{i}", start=s)
            entry["end_time"] = e

    doc = _emit(pth, _xas_crosswalk(tmp_path)).document
    parts = doc["schema:distribution"][0]["schema:hasPart"]
    starts = [p_["prov:wasGeneratedBy"][0]["schema:startTime"] for p_ in parts]
    assert starts == ["2020-08-10T09:18:48", "2020-08-11T21:14:18",
                      "2020-08-12T21:57:17"]
    ends = [p_["prov:wasGeneratedBy"][0]["schema:endTime"] for p_ in parts]
    assert ends[0] == "2020-08-10T09:22:32"

    # The file-level event still spans the whole series.
    event = doc["prov:wasGeneratedBy"][0]
    assert event["schema:startTime"] == "2020-08-10T09:18:48"
    assert event["schema:endTime"] == "2020-08-12T22:12:09"

    # Distinct activities, one per part -- not one node reused.
    ids = [p_["prov:wasGeneratedBy"][0]["@id"] for p_ in parts]
    assert len(set(ids)) == 3


def test_a_part_activity_references_the_instruments_it_does_not_repeat(tmp_path):
    """The same beamline measured every entry. Repeating its description
    per part would assert one beamline per scan; a reference denotes the
    one node the file-level event describes in full.

    It also satisfies cdifProvActivity, which requires prov:used on any
    activity reached through prov:wasGeneratedBy -- which a part's is."""
    pth = tmp_path / "f.nxs"
    with h5py.File(pth, "w") as f:
        _scan(f, "a", start="2020-08-10T09:18:48")
        _scan(f, "b", start="2020-08-11T09:18:48")

    doc = _emit(pth, _xas_crosswalk(tmp_path)).document
    parts = doc["schema:distribution"][0]["schema:hasPart"]
    used = parts[0]["prov:wasGeneratedBy"][0]["prov:used"]
    assert used, "a part activity must carry at least one prov:used"
    # References only: an @id and nothing else.
    assert all(set(u) == {"@id"} for u in used)
    # And they denote the nodes the file-level event describes.
    described = {u["@id"] for u in doc["prov:wasGeneratedBy"][0]["prov:used"]}
    assert {u["@id"] for u in used} <= described


def test_a_nexus_timestamp_is_normalised_to_iso_8601(tmp_path):
    """NeXus files write `2020-08-10 09:18:48` -- a space, not a T.
    Nothing rejects it: the schema and the SHACL both say "ISO8601
    date-time" in prose and require only a string, so it validates
    cleanly and still throws in a consumer that parses it as one."""
    pth = tmp_path / "f.nxs"
    with h5py.File(pth, "w") as f:
        _scan(f, "only", start="2020-08-10 09:18:48")

    doc = _emit(pth, _xas_crosswalk(tmp_path)).document
    assert doc["prov:wasGeneratedBy"][0]["schema:startTime"] == (
        "2020-08-10T09:18:48")


def test_an_entry_with_no_time_gets_no_acquisition(tmp_path):
    """Rather than an activity asserting an empty startTime."""
    pth = tmp_path / "f.nxs"
    with h5py.File(pth, "w") as f:
        _scan(f, "a")
        _scan(f, "b")

    doc = _emit(pth, _xas_crosswalk(tmp_path)).document
    for part in doc["schema:distribution"][0]["schema:hasPart"]:
        assert "prov:wasGeneratedBy" not in part


def test_a_single_entry_file_has_no_parts(tmp_path):
    """With one entry the dataset IS the entry, so a part would repeat
    the dataset's own name, identifier, url, description and keywords
    under a second @id. The structure goes on the distribution, which is
    then the whole of the data."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "only")

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    dist = doc["schema:distribution"][0]
    assert "schema:hasPart" not in dist
    structures = dist["cdi:isStructuredBy"]
    assert len(structures) == 1
    assert structures[0]["cdi:has_DataStructureComponent"]
    # A description is always written; a single-entry file gets the
    # single-measurement wording rather than the archive-of-parts note.
    assert "parts of one dataset" not in doc["schema:description"]


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
    # It declares a technique, so discovery is genuine. It measured
    # nothing and carries no XAS content, so neither data_description nor
    # xasCore may be claimed -- declaring NXxas_trans is not the same as
    # satisfying it.
    assert result.profiles == ["core/1.1", "discovery/1.1"]
    assert "data_description/1.1" not in result.profiles
    assert "xasCore/1.0" not in result.profiles

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
    from cdifnexmetadata.map.legacy import load_legacy

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
        for s in _structures(doc)
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
    mono = next(
        u["schema:instrument"]
        for u in _emit(p, cw).document["prov:wasGeneratedBy"][0]["prov:used"]
        if u["schema:instrument"]["schema:additionalType"][0]["@id"]
        == "xas:xraymonochromator")
    by_id = {pv["schema:propertyID"][0]["@id"]: pv
             for pv in mono["schema:additionalProperty"]}
    assert by_id["xas:dspacing"]["schema:value"].startswith("1.6375")
    assert by_id["xas:dspacing"]["schema:unitText"]      # value AND unit
    assert by_id["xas:reflectionplane"]["schema:value"] == "3 1 1"


def test_property_ids_are_the_concept_locals_the_profile_enumerates(tmp_path):
    """The profile enumerates the very same tokens, so a tidier spelling
    here -- an earlier version had xas:xray_source_type -- produces a
    document that cannot satisfy it."""
    from cdifnexmetadata.emit import CONCEPT_SLOTS

    for concept, slot in CONCEPT_SLOTS.items():
        if slot.target in ("keyword", "technique", "facility", "instrument"):
            continue
        assert slot.prop == concept.split(":", 1)[-1], (
            f"{concept} emits xas:{slot.prop}, which the profile does not "
            f"name"
        )


def test_a_non_xas_file_does_not_claim_the_xas_profile(tmp_path):
    """Four technique-neutral concepts -- facility, beamline, probe,
    source type -- are still minted under cdifxas: because that crosswalk
    was written first. Deciding "is this XAS" from that prefix made an
    NXsas file claim conformance to the XAS profile and advertise itself
    as X-ray absorption spectroscopy. A false conformance claim survives
    into a catalogue and misroutes the record."""
    p = tmp_path / "sas.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, name="entry", definition="NXsas")
        inst = _group(e, "instrument", "NXinstrument")
        inst["name"] = "APS 9-ID-C"
        _group(inst, "source", "NXsource")["name"] = "Advanced Photon Source"
        _group(inst, "detector", "NXdetector")["data"] = np.zeros((64, 64))

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:exactMatch", "nxdl:NXsource/name"),
        _row("cdifxas:beamline", "skos:exactMatch", "nxdl:NXinstrument/name"),
        _row("cdifsas:scatteringintensity", "skos:exactMatch",
             "nxdl:NXsas/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "DETECTOR:NXdetector/data"),
    )
    result = _emit(p, cw)
    assert "xasCore/1.0" not in result.profiles
    assert "data_description/1.1" in result.profiles   # it did measure
    names = {t.get("schema:termCode") for t
             in result.document.get("schema:measurementTechnique", [])}
    assert "XAS" not in names


def test_a_missing_source_type_depends_on_the_declared_technique(tmp_path):
    """An XAS measurement is made at a synchrotron -- that is what the
    technique requires -- so naming one reads the technique rather than
    guessing about the instrument. An NXtomo file may well have been
    measured at a synchrotron too, but nothing in it says so."""
    from cdifnexmetadata.emit import OGC_NIL_MISSING, SYNCHROTRON_SOURCE

    def source_type(definition):
        f = tmp_path / f"{definition}.nxs"
        with h5py.File(f, "w") as h:
            e = _entry(h, definition=definition)
            inst = _group(e, "instrument", "NXinstrument")
            inst["name"] = "BL-1"
            _group(inst, "source", "NXsource")["name"] = "Facility X"
        doc = _emit(f, _xas_crosswalk(tmp_path)).document
        peers = doc["prov:wasGeneratedBy"][0].get("prov:used", [])
        for u in peers:
            i = u["schema:instrument"]
            if i["schema:additionalType"][0]["@id"] != "xas:source":
                continue
            for pv in i.get("schema:additionalProperty", []):
                if pv["schema:propertyID"][0]["@id"] == "xas:xraysourcetype":
                    return pv["schema:value"]
        return None

    assert source_type("NXxas") == SYNCHROTRON_SOURCE
    assert source_type("NXxas_trans") == SYNCHROTRON_SOURCE
    # Not XAS: no source peer is invented at all, so nothing is asserted.
    assert source_type("NXtomo") is None


def test_a_non_xas_source_type_that_is_required_uses_the_nil_uri(tmp_path):
    """Where a source peer does exist for a non-XAS file -- because the
    file said something about the source -- a missing type is the OGC nil
    URI, not a supposition about someone else's instrument."""
    from cdifnexmetadata.emit import OGC_NIL_MISSING

    f = tmp_path / "tomo.nxs"
    with h5py.File(f, "w") as h:
        e = _entry(h, definition="NXtomo")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "source", "NXsource")["probe"] = "x-ray"

    cw = _crosswalk(tmp_path,
                    _row("cdifxas:probe", "skos:exactMatch",
                         "nxdl:NXsource/probe"))
    doc = _emit(f, cw).document
    source = next(
        u["schema:instrument"]
        for u in doc["prov:wasGeneratedBy"][0]["prov:used"]
        if u["schema:instrument"]["schema:additionalType"][0]["@id"]
        == "xas:source")
    by_id = {p["schema:propertyID"][0]["@id"]: p["schema:value"]
             for p in source["schema:additionalProperty"]}
    assert by_id["xas:xraysourcetype"] == OGC_NIL_MISSING


def test_structures_sit_on_the_parts_they_describe(tmp_path):
    """A structure describes one entry's layout, so it belongs to the part
    that is that entry. On the distribution it would assert of the whole
    file something true of only some of its parts -- which is wrong the
    moment two entries differ, as they do here."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _scan(f, "sample1", dets=("i0",))
        _scan(f, "sample2", dets=("i0",))
        _scan(f, "reference", dets=("i0", "itrans"))

    doc = _emit(p, _xas_crosswalk(tmp_path)).document
    assert "cdi:isStructuredBy" not in doc          # not at dataset level
    dist = doc["schema:distribution"][0]
    # Not on the distribution either, once there are parts to carry it.
    assert "cdi:isStructuredBy" not in dist

    structures = _structures(doc)
    assert len(structures) == 2

    # Each is defined inline with its components, which is what the
    # SHACL rule actually checks -- a reference to a node carrying no
    # components is the violation.
    assert all(s["cdi:has_DataStructureComponent"] for s in structures)

    # Every part names the structure it has, and the two layouts here are
    # distinguished rather than merged.
    defined = {s["@id"] for s in structures}
    referenced = {p_["cdi:isStructuredBy"]["@id"]
                  for p_ in dist["schema:hasPart"]}
    assert referenced <= defined
    assert len(referenced) == 2
