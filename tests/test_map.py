"""Tests for the concept-keyed mapping layer (stage 2).

Fixtures are synthesised so the suite runs offline with no binary test
data, and the crosswalk used is written inline rather than the bundled
one -- a test that fails because upstream revised an SSSOM row is testing
the crosswalk, not the mapper.
"""
from __future__ import annotations

import pytest

h5py = pytest.importorskip("h5py")
import numpy as np  # noqa: E402

from hdf5metadata.inspect import inspect_file, read_nexus  # noqa: E402
from hdf5metadata.map.concepts import map_entry, map_nexus  # noqa: E402
from hdf5metadata.map.legacy import load_legacy  # noqa: E402
from hdf5metadata.map.crosswalk import (  # noqa: E402
    Mapping,
    load_crosswalk,
    parse_path,
    resolve_mapping,
)

HEADER = (
    "subject_id\tsubject_label\tpredicate_id\tobject_id\tobject_label\t"
    "confidence\tcomment"
)


def _crosswalk(tmp_path, *rows):
    p = tmp_path / "cw.sssom.tsv"
    p.write_text(
        "# mapping_set_id: https://example.org/test\n"
        + HEADER + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return load_crosswalk(p)


def _row(subject, predicate, obj, label="", conf="1.0", comment=""):
    return f"{subject}\t{label}\t{predicate}\t{obj}\t\t{conf}\t{comment}"


def _entry(f, name="entry", definition=None):
    g = f.create_group(name)
    g.attrs["NX_class"] = "NXentry"
    if definition:
        g["definition"] = definition
    return g


def _group(parent, name, nx_class):
    g = parent.create_group(name)
    g.attrs["NX_class"] = nx_class
    return g


def _read(path):
    return read_nexus(inspect_file(path))


# ---------------------------------------------------------------------------
# crosswalk parsing
# ---------------------------------------------------------------------------

def test_definition_and_path_split_from_object_id(tmp_path):
    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:exactMatch", "nxdl:NXsource/name"),
        _row("cdifxas:mode", "skos:narrowMatch", "nxdl:NXxas_trans"),
    )
    facility, mode = cw.mappings
    assert facility.definition == "NXsource"
    assert facility.path == "/name"
    assert mode.definition == "NXxas_trans"
    # A definition-level mapping has no path: its value IS the definition.
    assert mode.path == ""
    assert facility.is_identifying and not mode.is_identifying


def test_uppercase_segments_are_placeholders():
    segs = parse_path("/ENTRY:NXentry/INSTRUMENT:NXinstrument/i0:NXdetector/data")
    assert [s.is_placeholder for s in segs] == [True, True, False, False]
    assert segs[2].nx_class == "NXdetector"
    assert segs[3].nx_class is None       # trailing field, not a group


def test_base_class_mappings_apply_to_every_definition(tmp_path):
    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:exactMatch", "nxdl:NXsource/name"),
        _row("cdifxas:dspacing", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/x"),
    )
    concepts = {m.subject_id for m in cw.for_definition("NXxas_tfy")}
    assert "cdifxas:facility" in concepts
    assert "cdifxas:dspacing" not in concepts


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

def test_base_class_path_resolves_against_the_class_not_the_entry(tmp_path):
    """The regression this layer was rewritten for.

    `nxdl:NXsource/name` means the `name` of any NXsource, wherever one
    sits. Walking it entry-relative looks for `name` directly under the
    entry and finds nothing -- which is how an earlier version mapped
    zero concepts from a file full of them.
    """
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "source", "NXsource")["name"] = "APS 13-ID-E"

    entry = _read(p).entries[0]
    m = Mapping("cdifxas:facility", "", "skos:exactMatch",
                "nxdl:NXsource/name", "")
    hits = resolve_mapping(entry, m)
    assert [h.value for h in hits] == ["APS 13-ID-E"]


def test_application_definition_path_resolves_entry_relative(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas_trans")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "i0", "NXdetector")["data"] = np.arange(5.0)

    entry = _read(p).entries[0]
    m = Mapping(
        "cdifxas:incidentintensity", "", "skos:exactMatch",
        "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
        "i0:NXdetector/data", "",
    )
    assert [h.name for h in resolve_mapping(entry, m)] == ["data"]


def test_absent_class_resolves_to_nothing_rather_than_raising(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _entry(f, definition="NXxas")

    entry = _read(p).entries[0]
    m = Mapping("cdifxas:temperature", "", "skos:exactMatch",
                "nxdl:NXsample/temperature", "")
    assert resolve_mapping(entry, m) == []


def test_missing_literal_name_is_a_miss_when_siblings_share_the_class(tmp_path):
    """`iey` absent must not silently become "any detector".

    i0, itrans and ifluor are all NXdetector; only the name distinguishes
    them. Falling back to the class would report the incident-beam
    monitor as an electron-yield measurement.
    """
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        for det in ("i0", "itrans", "ifluor"):
            _group(inst, det, "NXdetector")["data"] = np.arange(5.0)

    entry = _read(p).entries[0]
    iey = Mapping(
        "cdifxas:electronyieldintensity", "", "skos:exactMatch",
        "nxdl:NXxas_tey/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
        "iey:NXdetector/data", "",
    )
    assert resolve_mapping(entry, iey) == []

    i0 = Mapping(
        "cdifxas:incidentintensity", "", "skos:exactMatch",
        "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
        "i0:NXdetector/data", "",
    )
    assert [h.path for h in resolve_mapping(entry, i0)] == [
        "/entry/instrument/i0/data"
    ]


def test_missing_literal_name_is_forgiven_when_the_class_is_unique(tmp_path):
    """A lone NXsource is the source whatever it is called."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "synchrotron", "NXsource")["probe"] = "x-ray"

    entry = _read(p).entries[0]
    m = Mapping(
        "cdifxas:probe", "", "skos:exactMatch",
        "nxdl:NXxas/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
        "source:NXsource/probe", "",
    )
    assert [h.value for h in resolve_mapping(entry, m)] == ["x-ray"]


# ---------------------------------------------------------------------------
# mapping entries
# ---------------------------------------------------------------------------

def test_values_carry_provenance_and_predicate(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "source", "NXsource")["name"] = "APS"

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:closeMatch", "nxdl:NXsource/name",
             label="facility", conf="0.8", comment="beware"),
    )
    rec = map_entry(_read(p).entries[0], cw)
    cv = rec.first("cdifxas:facility")
    assert cv.value == "APS"
    assert cv.predicate == "skos:closeMatch"
    assert cv.confidence == 0.8
    assert cv.note == "beware"
    assert cv.source_path.endswith("/instrument/source/name")


def test_large_arrays_are_recorded_but_not_read(tmp_path):
    """A measured array is data, not metadata -- but its shape is still
    the answer to "is this concept present", which data_structure needs."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas_trans")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "i0", "NXdetector")["data"] = np.arange(4096.0)

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:incidentintensity", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "i0:NXdetector/data"),
    )
    cv = map_entry(_read(p).entries[0], cw).first("cdifxas:incidentintensity")
    assert cv.is_array and cv.value is None
    assert cv.shape == (4096,)


def test_detection_mode_comes_from_the_declared_definition(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _entry(f, definition="NXxas_tfy")

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:fluorescencemode", "skos:narrowMatch", "nxdl:NXxas_tfy"),
        _row("cdifxas:transmissionmode", "skos:narrowMatch",
             "nxdl:NXxas_trans"),
    )
    rec = map_entry(_read(p).entries[0], cw)
    assert rec.value_of("cdifxas:fluorescencemode") == "NXxas_tfy"
    assert rec.first("cdifxas:transmissionmode") is None


def test_entry_without_a_definition_warns_and_maps_base_classes_only(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f)
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "source", "NXsource")["name"] = "APS"
        _group(inst, "i0", "NXdetector")["data"] = np.arange(5.0)

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:exactMatch", "nxdl:NXsource/name"),
        _row("cdifxas:incidentintensity", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "i0:NXdetector/data"),
    )
    rec = map_entry(_read(p).entries[0], cw)
    assert rec.concepts == {"cdifxas:facility"}
    assert any("no application definition" in w for w in rec.warnings)


# ---------------------------------------------------------------------------
# sibling-definition fallback
# ---------------------------------------------------------------------------

def test_family_base_borrows_unambiguous_paths_from_a_specific_mode(tmp_path):
    """A file declaring `NXxas` still has its monochromator d-spacing at
    the path `NXxas_trans` gives. Losing it over a bookkeeping detail
    would be worse than borrowing the path and saying so."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        mono = _group(inst, "monochromator", "NXmonochromator")
        _group(mono, "crystal", "NXcrystal")["d_spacing"] = 1.6375

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:dspacing", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "MONOCHROMATOR:NXmonochromator/crystal:NXcrystal/d_spacing",
             label="d-spacing"),
    )
    rec = map_entry(_read(p).entries[0], cw)
    cv = rec.first("cdifxas:dspacing")
    assert cv is not None and round(cv.value, 4) == 1.6375
    assert "NXxas_trans" in cv.note
    assert any("more specific definition" in w for w in rec.warnings)


def test_a_path_meaning_different_concepts_per_mode_is_never_borrowed(tmp_path):
    """`/ENTRY/intensity` is the absorption coefficient in transmission
    and the fluorescence-derived one in TFY. Only the declaration can
    tell them apart, so an undeclared file gets neither rather than
    both."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        e["intensity"] = np.arange(5.0)

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:absorptioncoefficient", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/intensity"),
        _row("cdifxas:fluorescenceabsorptioncoefficient", "skos:exactMatch",
             "nxdl:NXxas_tfy/ENTRY:NXentry/intensity"),
    )
    assert map_entry(_read(p).entries[0], cw).concepts == set()


def test_an_unrelated_definition_is_never_borrowed_from(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        e["q"] = np.arange(5.0)

    cw = _crosswalk(
        tmp_path,
        _row("cdifsas:momentumtransfer", "skos:exactMatch",
             "nxdl:NXsas/ENTRY:NXentry/q"),
    )
    assert map_entry(_read(p).entries[0], cw).concepts == set()


def test_concepts_the_definition_allows_but_the_file_lacks_are_reported(
    tmp_path,
):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _entry(f, definition="NXxas")

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:temperature", "skos:exactMatch",
             "nxdl:NXsample/temperature", label="temperature"),
    )
    rec = map_entry(_read(p).entries[0], cw)
    assert any("temperature" in w and "not found" in w for w in rec.warnings)


# ---------------------------------------------------------------------------
# multi-entry files
# ---------------------------------------------------------------------------

def test_entries_sharing_a_layout_share_a_structural_signature(tmp_path):
    """DESIGN.md treats a multi-entry file as an archive of parts, with
    one DataStructure per distinct layout referenced by every part that
    matches it. Two scans of the same sample differ in their values, not
    their shape; a scan that adds a transmission detector does not."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        for name, dets in (
            ("scan1", ("i0", "ifluor")),
            ("scan2", ("i0", "ifluor")),
            ("ref", ("i0", "ifluor", "itrans")),
        ):
            e = _entry(f, name=name, definition="NXxas_trans")
            inst = _group(e, "instrument", "NXinstrument")
            for det in dets:
                _group(inst, det, "NXdetector")["data"] = np.arange(443.0)

    cw = _crosswalk(
        tmp_path,
        *[
            _row(f"cdifxas:{c}", "skos:exactMatch",
                 f"nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
                 f"{d}:NXdetector/data")
            for c, d in (
                ("incidentintensity", "i0"),
                ("fluorescenceintensity", "ifluor"),
                ("transmittedintensity", "itrans"),
            )
        ],
    )
    result = map_nexus(_read(p), cw)
    assert result.is_multi_entry
    groups = result.structure_groups()
    assert len(groups) == 2
    assert sorted(len(v) for v in groups.values()) == [1, 2]


def test_a_non_nexus_file_maps_nothing_and_says_why(tmp_path):
    p = tmp_path / "plain.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=np.arange(10))

    result = map_nexus(_read(p), _crosswalk(
        tmp_path, _row("cdifxas:facility", "skos:exactMatch",
                       "nxdl:NXsource/name")))
    assert result.records == []
    assert any("no NeXus markers" in w for w in result.warnings)


def test_the_bundled_crosswalk_loads_and_covers_every_detection_mode():
    cw = load_crosswalk()
    assert len(cw.mappings) > 40
    assert {"NXxas_trans", "NXxas_tfy", "NXxas_tey"} <= cw.definitions()
    assert all(m.subject_id.startswith("cdifxas:") for m in cw.mappings)


# ---------------------------------------------------------------------------
# legacy paths
# ---------------------------------------------------------------------------

LEGACY_HEADER = "concept\tconvention\tpath\tconfidence\tcomment"


def _legacy(tmp_path, *rows):
    p = tmp_path / "legacy.tsv"
    p.write_text(
        "# a comment line\n" + LEGACY_HEADER + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return load_legacy(p)


def _lrow(concept, convention, path, conf="1.0", comment=""):
    return f"{concept}\t{convention}\t{path}\t{conf}\t{comment}"


def _gsecars(f, name="entry", element="Fe", edge="K"):
    """A file laid out the way the Athena/GSECARS writer lays them out:
    NXscan and NXxrayedge, neither of which is a NeXus base class."""
    e = _entry(f, name=name, definition="NXxas")
    scan = _group(e, "scan", "NXscan")
    scan["edge_energy"] = "7112.000"
    xe = _group(scan, "xrayedge", "NXxrayedge")
    xe["element"] = element
    xe["edge"] = edge
    return e


def test_legacy_recovers_values_the_crosswalk_cannot_see(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _gsecars(f)

    entry = _read(p).entries[0]
    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:elementanalyzed", "skos:exactMatch",
             "nxdl:NXxas/ENTRY:NXentry/element:NXelement/name",
             label="elementanalyzed"),
    )
    # The standard path finds nothing: this file has no NXelement.
    assert map_entry(entry, cw).concepts == set()

    lg = _legacy(tmp_path, _lrow(
        "cdifxas:elementanalyzed", "gsecars-athena",
        "/scan:NXscan/xrayedge:NXxrayedge/element"))
    rec = map_entry(entry, cw, lg)
    cv = rec.first("cdifxas:elementanalyzed")
    assert cv.value == "Fe"
    assert cv.convention == "gsecars-athena"
    assert any("non-standard paths" in w for w in rec.warnings)


def test_legacy_never_displaces_a_standards_based_value(tmp_path):
    """The rule that makes the table safe to extend: adding a convention
    can fill gaps but cannot change an answer the standard already gave."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        src = _group(inst, "source", "NXsource")
        src["name"] = "APS, undulator 36mm, 66 poles, 13-ID-E"
        src["facility_name"] = "APS"

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:facility", "skos:exactMatch", "nxdl:NXsource/name"),
    )
    lg = _legacy(tmp_path, _lrow(
        "cdifxas:facility", "gsecars-athena",
        "/INSTRUMENT:NXinstrument/source:NXsource/facility_name"))
    rec = map_entry(_read(p).entries[0], cw, lg)

    assert rec.value_of("cdifxas:facility").startswith("APS, undulator")
    assert rec.first("cdifxas:facility").convention == ""
    assert len(rec.values["cdifxas:facility"]) == 1


def test_legacy_is_optional(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _gsecars(f)
    cw = _crosswalk(tmp_path, _row("cdifxas:facility", "skos:exactMatch",
                                   "nxdl:NXsource/name"))
    # No table at all, and a table pointing at a file that is not there.
    assert map_entry(_read(p).entries[0], cw, None).concepts == set()
    assert load_legacy(tmp_path / "nope.tsv").paths == []


def test_legacy_confidence_and_comment_travel_with_the_value(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        _gsecars(f)
    cw = _crosswalk(tmp_path, _row("cdifxas:edgeenergy", "skos:exactMatch",
                                   "nxdl:NXabsorption_edge/energy"))
    lg = _legacy(tmp_path, _lrow(
        "cdifxas:edgeenergy", "gsecars-athena", "/scan:NXscan/edge_energy",
        conf="0.9", comment="string with no units attribute"))
    cv = map_entry(_read(p).entries[0], cw, lg).first("cdifxas:edgeenergy")
    assert cv.value == "7112.000" and cv.confidence == 0.9
    assert "no units" in cv.note


def test_the_bundled_legacy_table_names_only_real_concepts():
    """A typo in legacy-paths.tsv would otherwise just never match, and
    silently mapping nothing is exactly the failure this suite exists to
    catch."""
    from hdf5metadata.map.crosswalk import DATA_DIR

    legacy = load_legacy()
    # Every bundled crosswalk, not just the XAS one: the legacy table is
    # shared across techniques, so the invariant is that a legacy concept
    # is registered *somewhere*.
    known: set[str] = set()
    for tsv in sorted(DATA_DIR.glob("*-to-nexus.sssom.tsv")):
        known |= load_crosswalk(tsv).concepts()
    assert legacy.paths, "bundled legacy table should not be empty"
    assert legacy.concepts() <= known, (
        f"legacy concepts with no crosswalk entry: "
        f"{sorted(legacy.concepts() - known)}")
    assert "gsecars-athena" in legacy.conventions()
    assert all(p.path.startswith("/") for p in legacy.paths)


def test_discriminating_classes_come_from_the_crosswalk_not_a_hardcoded_list(
    tmp_path,
):
    """Which classes carry a meaningful instance name is a fact about the
    crosswalk, so reading it from there cannot go stale when a definition
    adds a detector."""
    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:incidentintensity", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/i0:NXdetector/data"),
        _row("cdifxas:transmittedintensity", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/itrans:NXdetector/data"),
        _row("cdifxas:facility", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/source:NXsource/name"),
    )
    assert cw.discriminating_classes() == frozenset({"NXdetector"})


def test_a_lone_detector_is_not_mistaken_for_the_one_that_is_missing(tmp_path):
    """With only `i0` present, forgiving a missing `itrans` because the
    class has one instance would report the incident beam as the
    transmitted beam. That is a wrong claim, not a near miss."""
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas_trans")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "i0", "NXdetector")["data"] = np.arange(5.0)

    cw = _crosswalk(
        tmp_path,
        _row("cdifxas:incidentintensity", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "i0:NXdetector/data"),
        _row("cdifxas:transmittedintensity", "skos:exactMatch",
             "nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/"
             "itrans:NXdetector/data"),
    )
    rec = map_entry(_read(p).entries[0], cw)
    assert rec.concepts == {"cdifxas:incidentintensity"}


# ---------------------------------------------------------------------------
# choosing a crosswalk
# ---------------------------------------------------------------------------

def test_the_crosswalk_is_chosen_from_what_the_file_declares():
    """An ingest handed a folder of mixed techniques must not need to be
    told which is which. Being told is how every SAS file in a mixed
    folder silently comes out thin."""
    from hdf5metadata.map.crosswalk import select_crosswalk

    xas, why = select_crosswalk(["NXxas"])
    assert "cdifxas" in why and "NXxas" in why
    assert "cdifxas:dspacing" in xas.concepts()

    sas, why = select_crosswalk(["NXsas"])
    assert "cdifsas" in why and "NXsas" in why
    assert "cdifsas:scatteringintensity" in sas.concepts()


def test_a_mode_specific_definition_still_picks_the_family_crosswalk():
    from hdf5metadata.map.crosswalk import select_crosswalk

    cw, why = select_crosswalk(["NXxas_trans"])
    assert "cdifxas" in why
    assert "cdifxas:transmittedintensity" in cw.concepts()


def test_base_classes_never_decide_the_choice():
    """NXsource and NXsample appear in every crosswalk. Selecting on them
    would make each one look like a match for every file."""
    from hdf5metadata.map.crosswalk import bundled_crosswalks, load_crosswalk

    for path in bundled_crosswalks():
        apps = load_crosswalk(path).application_definitions()
        assert "NXsource" not in apps
        assert "NXsample" not in apps
        assert apps, f"{path.name} covers no application definition"


def test_an_uncovered_technique_falls_back_and_says_so(tmp_path):
    """Still worth running: base-class mappings apply to any definition,
    so facility and beamline are found even for a technique nobody has
    written a crosswalk for. The reason has to make that clear."""
    from hdf5metadata.map.crosswalk import select_crosswalk

    cw, why = select_crosswalk(["NXtomo"])
    assert cw.mappings
    assert "no bundled crosswalk covers NXtomo" in why
    assert "base-class" in why

    p = tmp_path / "t.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXtomo")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "source", "NXsource")["name"] = "Diamond"

    result = map_nexus(_read(p))
    assert result.records[0].value_of("cdifxas:facility") == "Diamond"
    assert any("no bundled crosswalk covers" in w for w in result.warnings)


def test_an_undeclared_file_falls_back_and_says_so():
    from hdf5metadata.map.crosswalk import select_crosswalk

    _cw, why = select_crosswalk([])
    assert "declares no application definition" in why


def test_an_explicit_crosswalk_still_wins(tmp_path):
    p = tmp_path / "f.nxs"
    with h5py.File(p, "w") as f:
        e = _entry(f, definition="NXxas")
        inst = _group(e, "instrument", "NXinstrument")
        _group(inst, "source", "NXsource")["name"] = "APS"

    cw = _crosswalk(tmp_path, _row("cdifxas:facility", "skos:exactMatch",
                                   "nxdl:NXsource/name"))
    result = map_nexus(_read(p), cw)
    assert result.crosswalk_reason == "supplied by the caller"
    assert result.records[0].concepts == {"cdifxas:facility"}
