"""Tests for the XDI binding.

XDI is the second input format. These tests exist mostly to hold the
line on the claim the architecture makes: that a second format is a
parser, not a pipeline. Everything downstream of `map_xdi` is the same
code the NeXus binding uses.
"""
from __future__ import annotations

import pytest

from cdifnexmetadata.emit import (  # noqa: E402
    XDI_MEDIA_TYPE,
    XDI_SPECIFICATION,
    emit_document,
)
from cdifnexmetadata.inspect.xdi import inspect_xdi, is_xdi  # noqa: E402
from cdifnexmetadata.map.xdi import load_xdi_crosswalk, map_xdi  # noqa: E402

SPACED = """\
# XDI/1.0 test/1.0
# Element.symbol: Fe
# Element.edge: K
# Facility.name: APS
# Beamline.name: 13-ID-E
# Mono.name: Si(111)
# Sample.temperature: room temperature
# Column.1: energy eV
# Column.2: i0
# Column.3: itrans
# ///
# a user comment
# ---
# energy i0 itrans
7100.0  1000.0  900.0
7110.0  1001.0  880.0
7120.0  1002.0  870.0
"""

#: The same file as the reference library writes it: no space after the
#: '#' on the version line or the separators.
CLOSED_UP = (
    SPACED.replace("# XDI/1.0", "#XDI/1.0")
    .replace("# ///", "#/////////")
    .replace("# ---", "#---------")
)


def _write(tmp_path, text, name="scan.xdi"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# recognising the format
# ---------------------------------------------------------------------------

def test_a_file_is_xdi_when_its_first_line_says_so(tmp_path):
    assert is_xdi(_write(tmp_path, SPACED))


def test_the_space_after_the_hash_is_optional(tmp_path):
    """118 of the 272 files in the XAS Data Library write "#XDI/1.0"
    closed up. A reader that rejects them is not reading the format."""
    assert is_xdi(_write(tmp_path, CLOSED_UP, "closed.xdi"))


def test_a_binary_file_is_not_xdi(tmp_path):
    p = tmp_path / "thing.h5"
    p.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 64)
    assert not is_xdi(p)


def test_a_text_file_that_is_not_xdi_is_not_xdi(tmp_path):
    assert not is_xdi(_write(tmp_path, "# some notes\n1 2 3\n", "notes.txt"))
    assert not is_xdi(_write(tmp_path, "", "empty.txt"))


def test_the_extension_does_not_decide(tmp_path):
    """Dispatch is on what the file says it is, not what it is called."""
    assert is_xdi(_write(tmp_path, SPACED, "mislabelled.txt"))
    assert not is_xdi(_write(tmp_path, "not xdi\n", "mislabelled.xdi"))


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def test_headers_columns_and_rows_are_read(tmp_path):
    _insp, x = inspect_xdi(_write(tmp_path, SPACED))
    assert x.is_xdi and x.xdi_version == "1.0"
    assert x.application == "test/1.0"
    assert x.headers["Element.symbol"] == "Fe"
    assert x.labels == ["energy", "i0", "itrans"]
    assert x.row_count == 3
    assert "a user comment" in x.comments


def test_the_closed_up_form_reads_identically(tmp_path):
    _i1, spaced = inspect_xdi(_write(tmp_path, SPACED, "a.xdi"))
    _i2, closed = inspect_xdi(_write(tmp_path, CLOSED_UP, "b.xdi"))
    assert closed.headers == spaced.headers
    assert closed.labels == spaced.labels
    assert closed.row_count == spaced.row_count


def test_headers_are_found_whatever_their_case(tmp_path):
    text = SPACED.replace("# Facility.name:", "# Facility.Name:")
    _insp, x = inspect_xdi(_write(tmp_path, text))
    assert x.header("Facility.name") == "APS"


def test_column_headers_win_over_the_label_line(tmp_path):
    """A file may give both. The Column.N headers are the declared
    answer; the label line is a convention."""
    text = SPACED.replace("# energy i0 itrans", "# E I0 IT")
    _insp, x = inspect_xdi(_write(tmp_path, text))
    assert x.labels == ["energy", "i0", "itrans"]


def test_a_file_with_no_header_end_says_so(tmp_path):
    text = SPACED.replace("# ---\n", "")
    _insp, x = inspect_xdi(_write(tmp_path, text))
    assert any("no header-end line" in w for w in x.warnings)


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------

def test_headers_and_columns_reach_the_same_concept_hub(tmp_path):
    _insp, x = inspect_xdi(_write(tmp_path, SPACED))
    record = map_xdi(x).records[0]
    assert record.value_of("cdifxas:elementanalyzed") == "Fe"
    assert record.value_of("cdifxas:facility") == "APS"
    arrays = {
        c for c, vals in record.values.items() if any(v.is_array for v in vals)
    }
    assert "cdifxas:monochromatorenergy" in arrays
    assert "cdifxas:incidentintensity" in arrays


def test_a_column_is_an_array_whose_numbers_are_not_read(tmp_path):
    """Same treatment the NeXus mapper gives a dataset: the shape is what
    the data-structure profile needs, the numbers are data."""
    _insp, x = inspect_xdi(_write(tmp_path, SPACED))
    record = map_xdi(x).records[0]
    energy = record.first("cdifxas:monochromatorenergy")
    assert energy.is_array and energy.value is None
    assert energy.shape == (3,)
    assert energy.label == "energy"


def test_unmapped_headers_are_reported_not_dropped_silently(tmp_path):
    text = SPACED.replace(
        "# Element.symbol: Fe", "# Element.symbol: Fe\n# Beamline.exotic: 7")
    _insp, x = inspect_xdi(_write(tmp_path, text))
    record = map_xdi(x).records[0]
    assert any("no crosswalk entry" in w for w in record.warnings)


def test_the_crosswalk_direction_is_read_not_assumed():
    """cdifxas-to-nexus has concepts as subjects; xdi-to-cdifxas has them
    as objects. The loader reads whichever way a set was published."""
    cw = load_xdi_crosswalk()
    assert cw.mappings
    assert all(m.subject_id.startswith("xdi:") for m in cw.mappings)


# ---------------------------------------------------------------------------
# through the shared emitter
# ---------------------------------------------------------------------------

def _emit(tmp_path, text=SPACED):
    insp, x = inspect_xdi(_write(tmp_path, text))
    return emit_document(
        insp, x, map_xdi(x),
        encoding_format=XDI_MEDIA_TYPE,
        format_specification=XDI_SPECIFICATION,
    )


def test_an_xdi_document_declares_the_xdi_specification(tmp_path):
    dist = _emit(tmp_path).document["schema:distribution"][0]
    assert dist["schema:encodingFormat"] == [XDI_MEDIA_TYPE]
    assert dist["dcterms:conformsTo"] == [{"@id": XDI_SPECIFICATION}]


def test_an_xdi_file_is_xas_without_declaring_a_nexus_definition(tmp_path):
    """The format is the X-ray Absorption Data Interchange format and
    describes nothing else, so declaring it declares the technique."""
    result = _emit(tmp_path)
    assert "xasCore/1.0" in result.profiles
    techniques = {
        t["schema:name"]
        for t in result.document["schema:measurementTechnique"]
    }
    assert "X-Ray Absorption Spectroscopy" in techniques


def test_variables_are_named_by_their_column_label(tmp_path):
    """An XDI column is located by position, so the label has to travel
    separately -- unlike an HDF5 path, which ends in the field name."""
    doc = _emit(tmp_path).document
    assert {v["schema:name"] for v in doc["schema:variableMeasured"]} == {
        "energy", "i0", "itrans"
    }


def test_the_same_emitter_serves_both_bindings(tmp_path):
    """The point of the concept hub: everything downstream of mapping is
    shared. If this drifts, the architecture claim has stopped being
    true."""
    doc = _emit(tmp_path).document
    for key in ("@context", "schema:name", "schema:distribution",
                "schema:variableMeasured", "schema:subjectOf",
                "prov:wasGeneratedBy"):
        assert key in doc, f"{key} missing from the XDI document"
    dist = doc["schema:distribution"][0]
    # An XDI file holds one spectrum, so it is the single-entry shape:
    # no parts, and the structure on the distribution, which is the whole
    # of the data. Same rule as a one-entry NeXus file -- one emitter,
    # one placement rule.
    assert "schema:hasPart" not in dist
    structures = dist["cdi:isStructuredBy"]
    assert len(structures) == 1
    assert structures[0]["cdi:has_DataStructureComponent"]



def test_a_column_label_carrying_a_unit_keeps_it():
    """The XDI dictionary writes a column label as a name optionally
    followed by a unit -- `energy eV` -- and 33 of the 55 reference files
    use it. Taking only the first token threw the unit away, which is the
    one place the RML pipeline carried more information than this one.

    Beamline software sometimes appends its own provenance after `||`;
    that is neither name nor unit."""
    from cdifnexmetadata.inspect.xdi import _split_column_label as split

    assert split("energy eV") == ("energy", "eV")
    assert split("itrans counts || 13BMD:scaler1_calc3.VAL") == (
        "itrans", "counts")
    # No unit stated, and none invented.
    assert split("i0") == ("i0", None)
    assert split("") == ("", None)
    # Three tokens is prose, not `name unit` -- guessing would be worse
    # than recording nothing.
    assert split("energy in eV") == ("energy", None)


def test_a_text_column_gets_a_text_mapping_with_its_field_width(tmp_path):
    """The physical mapping says how to find the values, and that differs
    by source. A column index means a text table, so TextMapping with the
    column and the width a reader slices on; a NeXus path means a
    container only a bespoke reader opens, so LocatorMapping.

    Width is measured to the end of the field, not the length of the
    token: a fixed-width layout pads on the left, so in
    `       12508.00` the value is 8 characters and the field is 15.
    """
    p = tmp_path / "f.xdi"
    p.write_text(SPACED, encoding="utf-8")
    doc = _emit(tmp_path).document
    structure = doc["schema:distribution"][0]["cdi:isStructuredBy"][0]
    mappings = [c["cdif:hasPhysicalMapping"]
                for c in structure["cdi:has_DataStructureComponent"]]
    assert mappings, "no physical mappings emitted"
    assert all(m["@type"] == ["cdif:TextMapping"] for m in mappings)
    assert all("cdif:index" in m for m in mappings)
    # No locator: a column position is not a path, and claiming one
    # would send a reader looking for something that is not there.
    assert not any("cdif:locator" in m for m in mappings)
    widths = [(m.get("cdi:minimumLength"), m.get("cdi:maximumLength"))
              for m in mappings]
    assert all(w[0] is not None and w[1] is not None for w in widths)
    assert all(w[0] <= w[1] for w in widths)

@pytest.mark.parametrize("text", [SPACED, CLOSED_UP])
def test_both_separator_styles_produce_the_same_document(tmp_path, text):
    result = _emit(tmp_path, text)
    assert result.profiles == [
        "core/1.1", "discovery/1.1", "data_description/1.1",
        "data_structure/1.1", "xasCore/1.0",
    ]


def test_the_sample_is_typed_as_a_material_sample(tmp_path):
    """The profile wants schema:Product and schema:Thing on @type, and
    both the bare "MaterialSample" string and the iSamples IRI on
    additionalType. No NeXus example exercised this, because none of them
    carried sample properties; the first XDI file through found it."""
    from cdifnexmetadata.emit import MATERIAL_SAMPLE_IRI

    sample = _emit(tmp_path).document["prov:wasGeneratedBy"][0][
        "schema:object"]
    assert set(sample["@type"]) == {"schema:Product", "schema:Thing"}
    assert {"@id": MATERIAL_SAMPLE_IRI} in sample["schema:additionalType"]
    assert "MaterialSample" in sample["schema:additionalType"]
    assert sample["schema:name"]


def test_probe_is_derived_from_the_format(tmp_path):
    """No XDI header carries the probe -- not one of the 272 files in the
    XAS Data Library has such a field. It follows from the format, which
    describes X-ray absorption and nothing else."""
    _insp, x = inspect_xdi(_write(tmp_path, SPACED))
    probe = map_xdi(x).records[0].first("cdifxas:probe")
    assert probe.value == "x-ray"
    assert "implied by the format" in probe.note


def test_detection_mode_is_derived_from_the_columns(tmp_path):
    """XDI has no detection-mode field either. Which intensities were
    recorded is what distinguishes the modes, and it is the same
    inference every XDI reader makes to plot a spectrum."""
    _insp, x = inspect_xdi(_write(tmp_path, SPACED))
    mode = map_xdi(x).records[0].first("cdifxas:xasmeasurementmode")
    assert mode.value == "Transmission"
    assert "derived from" in mode.note

    fluo = SPACED.replace("# Column.3: itrans", "# Column.3: ifluor")
    fluo = fluo.replace("# energy i0 itrans", "# energy i0 ifluor")
    _insp2, x2 = inspect_xdi(_write(tmp_path, fluo, "f.xdi"))
    assert map_xdi(x2).records[0].value_of(
        "cdifxas:xasmeasurementmode") == "Fluorescence"


def test_mono_name_is_split_into_material_and_reflection(tmp_path):
    """XDI writes "Si(111)" for what CDIF keeps as two concepts. The
    crosswalk already records the conflation; splitting recovers both
    without asserting anything the file does not contain."""
    _insp, x = inspect_xdi(_write(tmp_path, SPACED))
    record = map_xdi(x).records[0]
    assert record.value_of("cdifxas:monochromatortype") == "Si"
    assert record.value_of("cdifxas:reflectionplane") == "1 1 1"


def test_d_spacing_gets_the_unit_the_dictionary_specifies(tmp_path):
    """The XDI dictionary fixes Mono.d_spacing in Angstrom, so a file
    that states the number and not the unit is terse, not ambiguous."""
    text = SPACED.replace(
        "# Mono.name: Si(111)", "# Mono.name: Si(111)\n# Mono.d_spacing: 3.1355")
    _insp, x = inspect_xdi(_write(tmp_path, text))
    dspacing = map_xdi(x).records[0].first("cdifxas:dspacing")
    assert dspacing.value == "3.1355"
    assert dspacing.units == "Angstrom"


def test_a_required_property_the_file_omits_becomes_a_sentinel(tmp_path):
    """The profile requires the monochromator to report a d-spacing. The
    Diamond B18 series gives Mono.name and no Mono.d_spacing, so the
    property is emitted as unknown -- omitting it makes the instrument
    undescribable, and guessing asserts a number nobody measured."""
    from cdifnexmetadata.emit import UNKNOWN

    text = SPACED.replace("# Mono.name: Si(111)", "# Mono.name: Si(111)")
    doc = _emit(tmp_path, text).document
    mono = next(
        u["schema:instrument"]
        for u in doc["prov:wasGeneratedBy"][0]["prov:used"]
        if u["schema:instrument"]["schema:additionalType"][0]["@id"]
        == "xas:xraymonochromator")
    by_id = {p["schema:propertyID"][0]["@id"]: p
             for p in mono["schema:additionalProperty"]}
    dspacing = by_id["xas:dspacing"]
    assert dspacing["schema:value"] == UNKNOWN
    assert dspacing["schema:unitText"] == "Angstrom"
    assert "not recorded in the source file" in dspacing["schema:description"]
    # A value the file did supply is untouched.
    assert by_id["xas:monochromatortype"]["schema:value"] == "Si"


def test_a_sentinel_never_displaces_a_recorded_value(tmp_path):
    text = SPACED.replace(
        "# Mono.name: Si(111)",
        "# Mono.name: Si(111)\n# Mono.d_spacing: 3.1355")
    doc = _emit(tmp_path, text).document
    mono = next(
        u["schema:instrument"]
        for u in doc["prov:wasGeneratedBy"][0]["prov:used"]
        if u["schema:instrument"]["schema:additionalType"][0]["@id"]
        == "xas:xraymonochromator")
    by_id = {p["schema:propertyID"][0]["@id"]: p
             for p in mono["schema:additionalProperty"]}
    assert by_id["xas:dspacing"]["schema:value"] == "3.1355"
    assert "not recorded" not in by_id["xas:dspacing"].get(
        "schema:description", "")


def test_an_unmapped_column_is_still_described(tmp_path):
    """A column that was measured belongs in the data description
    whether or not anyone has named its concept. Dropping it loses the
    fact that the file has that many columns, which is what the
    data-structure profile exists to record."""
    from cdifnexmetadata.emit import OGC_NIL_MISSING

    text = SPACED.replace(
        "# Column.3: itrans",
        "# Column.3: itrans\n# Column.4: lnitiref").replace(
        "# energy i0 itrans", "# energy i0 itrans lnitiref")
    doc = _emit(tmp_path, text).document
    by_name = {v["schema:name"]: v for v in doc["schema:variableMeasured"]}
    assert set(by_name) == {"energy", "i0", "itrans", "lnitiref"}
    assert by_name["lnitiref"]["schema:propertyID"] == [
        {"@id": OGC_NIL_MISSING}]
    assert by_name["i0"]["schema:propertyID"] != [{"@id": OGC_NIL_MISSING}]


def test_unmapped_columns_get_distinct_identifiers(tmp_path):
    """They share a concept -- the nil URI -- so the identifier has to
    come from the label, or every unnamed column collides on one @id."""
    text = SPACED.replace(
        "# Column.3: itrans",
        "# Column.3: itrans\n# Column.4: lnitiref\n# Column.5: time").replace(
        "# energy i0 itrans", "# energy i0 itrans lnitiref time")
    doc = _emit(tmp_path, text).document
    ids = [v["@id"] for v in doc["schema:variableMeasured"]]
    assert len(ids) == len(set(ids)) == 5


def test_the_catalog_record_says_how_it_was_made(tmp_path):
    """A catalog record is machine output. It names the tool and the
    source format, and carries no creator -- naming a person as the
    author of a generated artifact misattributes it."""
    from cdifnexmetadata.emit import APPLICATION

    record = _emit(tmp_path).document["schema:subjectOf"]
    assert APPLICATION in record["schema:description"]
    assert "XDI" in record["schema:description"]
    assert "schema:creator" not in record


def test_the_creator_belongs_to_the_dataset(tmp_path):
    doc = _emit(tmp_path).document
    assert doc["schema:creator"]["schema:name"] == "Missing"
