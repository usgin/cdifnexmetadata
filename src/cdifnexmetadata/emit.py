"""Stage 3a: concept records to CDIF schema.org JSON-LD.

Everything before this stage is about *finding* things. This stage is
about *saying* them, and it is the only place that knows what a CDIF
document looks like. Changing the CDIF profiles should touch this file
and nothing upstream of it.

Two inputs, because CDIF needs two kinds of fact
------------------------------------------------

Core and discovery are largely technique-independent — a title, a time
range, a checksum, a file size. Those come from the NeXus structure and
the file on disk, via `inspect`. The domain facts — which element, which
edge, what the monochromator was set to, which arrays were measured —
come from the concept records, via `map`. So `emit` reads both, and the
split is not incidental: it is why a second technique needs a crosswalk
rather than a rewrite of this module.

The concept-to-CDIF binding lives here, in code
-----------------------------------------------

`map` answers "what concept is this value". `emit` answers "where does
that concept go in a CDIF document". Those are separate questions and
the second one is not data — it is the shape of a schema.org graph, with
nesting, typing and cross-references that a flat table cannot express.
So `CONCEPT_SLOTS` below is a dict in code rather than a fourth TSV.

Arrays are variables; scalars are context
-----------------------------------------

A concept `map` recorded as an array is something that was *measured*,
so it becomes a `schema:variableMeasured` and a component of the data
structure. A concept recorded as a scalar describes the *conditions* of
that measurement, so it lands in the instrument, sample or event
description. That single distinction drives most of the layout, and it
falls out of `is_array` rather than needing to be restated per concept.

Nothing is asserted that was not found
--------------------------------------

`dcterms:conformsTo` is written per profile only when the content for
that profile is actually present — the "detect conformance, don't assert
it" decision recorded in README.md. A file with no `NXdata` gets core and
discovery and does not claim data_description.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cdifnexmetadata.inspect.hdf5 import InspectionResult
from cdifnexmetadata.inspect.nexus import NeXusResult, NXEntry
from cdifnexmetadata.map.concepts import ConceptRecord, ConceptValue, MappingResult
from cdifnexmetadata.map.crosswalk import load_concept_units
from cdifnexmetadata.map.normalise import normalize_datetime

CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
    "spdx": "http://spdx.org/rdf/terms#",
    "cdi": "http://ddialliance.org/Specification/DDI-CDI/1.0/RDF/",
    "cdif": "https://w3id.org/cdif/",
    "ex": "https://example.org/",
    "nxs": "https://manual.nexusformat.org/classes/",
    "xas": "https://w3id.org/cdif/xas/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

PROFILE = "https://w3id.org/cdif/{}"

#: This tool, named in the catalog record so a reader can tell how the
#: metadata was produced and from what.
APPLICATION = "cdifnexmetadata"

#: Media types for the two input bindings.
HDF5_MEDIA_TYPE = "application/x-hdf5"
XDI_MEDIA_TYPE = "application/x-xdi"

#: The XDI specification, as the XAS profile names it.
#: The iSamples term the XAS profile requires on a sample, alongside the
#: bare "MaterialSample" string. Both, and in that combination -- the
#: profile checks for each separately.
#: What the XAS profile requires each peer instrument to carry, and the
#: unit the XDI dictionary fixes where there is one.
#:
#: A file that omits one of these still has to produce a describable
#: instrument, so the property is emitted with the sentinel rather than
#: left out -- the same choice the RML pipeline makes, and the reason its
#: documents validate where an omission would not.
#:
#: UNKNOWN, not a plausible default. The RML pipeline writes
#: "Synchrotron X-ray Source" for a missing source type, which is true of
#: every file in this corpus and is still an assertion no file made. A
#: reader can act on "unknown"; it cannot tell an asserted default from a
#: recorded fact.
UNKNOWN = "unknown"

#: What to report for a source type no file recorded.
#:
#: An XAS measurement is made at a synchrotron -- that is what the
#: technique requires -- so naming one is reading the technique, not
#: guessing about the instrument. XDI says it is XAS by being XDI; a
#: NeXus file says it by declaring an NXxas definition.
#:
#: Any other technique gets the OGC nil URI instead. An NXmx or NXtomo
#: file may well have been measured at a synchrotron, but nothing in the
#: file says so, and "we did not record this" is the honest answer where
#: "synchrotron" would be a supposition about someone else's instrument.
SYNCHROTRON_SOURCE = "Synchrotron X-ray Source"

REQUIRED_INSTRUMENT_PROPERTIES = {
    "xas:source": (("probe", None), ("xraysourcetype", None)),
    "xas:xraymonochromator": (
        ("monochromatortype", None),
        ("dspacing", "Angstrom"),
        ("reflectionplane", None),
    ),
}

MATERIAL_SAMPLE_IRI = (
    "https://w3id.org/isample/vocabulary/materialsampleobjecttype/"
    "materialsample"
)

XDI_SPECIFICATION = (
    "https://github.com/XraySpectroscopy/XAS-Data-Interchange/"
    "blob/master/specification/spec.md"
)

#: Placeholder base for generated identifiers. Real deployments pass
#: their own; this one at least resolves to a page saying what it is.
DEFAULT_BASE = "https://w3id.org/cdif/testing"

#: Sentinel conventions, shared with the XDI converter so both bindings
#: produce the same shape of "we looked and it was not there".
MISSING_TEXT = "Missing"
OGC_NIL_MISSING = "http://www.opengis.net/def/nil/OGC/0/missing"

#: HDF5 dtype -> XSD. Deliberately coarse: CDIF wants to know whether a
#: consumer should parse this as an integer, a real or a string, not
#: which C type the writer happened to use.
_XSD = {
    "int": "xsd:integer", "uint": "xsd:nonNegativeInteger",
    "float": "xsd:decimal", "bool": "xsd:boolean",
    "bytes": "xsd:string", "str": "xsd:string", "object": "xsd:string",
}


def _xsd_for(dtype: str) -> str:
    d = (dtype or "").lower()
    for prefix, xsd in _XSD.items():
        if d.startswith(prefix):
            return xsd
    return "xsd:string"


# ---------------------------------------------------------------------------
# concept -> CDIF binding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Slot:
    """Where a scalar concept goes, and how it should read there."""

    #: Which sub-object of the document takes it.
    target: str
    #: schema.org property, or the propertyID when target is a
    #: PropertyValue bag.
    prop: str
    label: str = ""


#: Scalar concepts and their destinations. Concepts absent from this
#: table still reach the output as additionalProperty on the acquisition
#: event -- losing a value because nobody wrote a binding for it would be
#: the worst of the available failures.
#:
#: The propertyID is the concept local, unchanged. That is not cosmetic:
#: the XAS profile names the very same tokens in its own enumerations
#: (`xas:xraysourcetype`, `xas:monochromatortype`, `xas:edgeenergy`,
#: `xas:temperature`), so inventing a tidier spelling here -- an earlier
#: version had `xas:xray_source_type` -- silently produces a document
#: that cannot satisfy the profile.
CONCEPT_SLOTS: dict[str, Slot] = {
    "cdifxas:facility": Slot("facility", "facility", "facility"),
    "cdifxas:beamline": Slot("instrument", "beamline", "beamline"),
    # The profile matches the probe entry by name as well as by
    # propertyID, and it spells it with a capital.
    "cdifxas:probe": Slot("source", "probe", "Probe"),
    "cdifxas:xraysourcetype": Slot("source", "xraysourcetype",
                                   "X-ray source type"),
    "cdifxas:elementanalyzed": Slot("keyword", "element", "element analyzed"),
    "cdifxas:edgeanalyzed": Slot("keyword", "edge", "absorption edge"),
    # Edge energy belongs to the measurement, not to a piece of hardware,
    # and the profile enumerates it among the activity properties.
    "cdifxas:edgeenergy": Slot("activity", "edgeenergy", "edge energy"),
    "cdifxas:xasmeasurementmode": Slot("technique", "mode", "detection mode"),
    "cdifxas:dspacing": Slot("monochromator", "dspacing",
                             "monochromator d-spacing"),
    "cdifxas:reflectionplane": Slot("monochromator", "reflectionplane",
                                    "reflection plane"),
    "cdifxas:monochromatortype": Slot("monochromator", "monochromatortype",
                                      "monochromator crystal"),
    "cdifxas:temperature": Slot("sample", "temperature",
                                "sample temperature"),
    "cdifxas:samplepreparation": Slot("sample", "samplepreparation",
                                      "sample preparation"),
}

#: What the profile calls the two peer instruments of an XAS acquisition.
#: These are `const` values in its `contains` constraints, so a document
#: using any other token has no beamline and no monochromator as far as
#: validation is concerned.
BEAMLINE_TYPE = "xas:beamline"
SOURCE_TYPE = "xas:source"
MONOCHROMATOR_TYPE = "xas:xraymonochromator"
ANALYSIS_EVENT = "xas:analysisevent"

#: Element symbol -> name, for the keyword DefinedTerm. Only the ones a
#: XAS beamline actually runs; an unknown symbol still emits a term with
#: the symbol as its name rather than being dropped.
_ELEMENTS = {
    "H": "Hydrogen", "C": "Carbon", "N": "Nitrogen", "O": "Oxygen",
    "Na": "Sodium", "Mg": "Magnesium", "Al": "Aluminium", "Si": "Silicon",
    "P": "Phosphorus", "S": "Sulfur", "Cl": "Chlorine", "K": "Potassium",
    "Ca": "Calcium", "Ti": "Titanium", "V": "Vanadium", "Cr": "Chromium",
    "Mn": "Manganese", "Fe": "Iron", "Co": "Cobalt", "Ni": "Nickel",
    "Cu": "Copper", "Zn": "Zinc", "Ga": "Gallium", "Ge": "Germanium",
    "As": "Arsenic", "Se": "Selenium", "Br": "Bromine", "Sr": "Strontium",
    "Y": "Yttrium", "Zr": "Zirconium", "Nb": "Niobium", "Mo": "Molybdenum",
    "Ru": "Ruthenium", "Rh": "Rhodium", "Pd": "Palladium", "Ag": "Silver",
    "Cd": "Cadmium", "In": "Indium", "Sn": "Tin", "Sb": "Antimony",
    "Te": "Tellurium", "I": "Iodine", "Ba": "Barium", "La": "Lanthanum",
    "Ce": "Cerium", "W": "Tungsten", "Re": "Rhenium", "Os": "Osmium",
    "Ir": "Iridium", "Pt": "Platinum", "Au": "Gold", "Hg": "Mercury",
    "Pb": "Lead", "Bi": "Bismuth", "Th": "Thorium", "U": "Uranium",
}

#: Term sets the xasDocument profile mandates by name. The edge list is
#: the XDI dictionary even for a NeXus-sourced value -- the profile names
#: it as the authority for edge names, and the binding a value arrived
#: through does not change which vocabulary defines it.
XDI_DICTIONARY = (
    "https://github.com/XraySpectroscopy/XAS-Data-Interchange/"
    "blob/master/specification/dictionary.md"
)
SWEET_ELEMENTS = "http://sweetontology.net/matrElement"
#: Where the profile expects the detection mode to have come from.
NXXAS_MODE_TERMSET = "nxs:Field/NXxas/ENTRY/DATA/mode"

XAS_TECHNIQUE = {
    "@type": ["schema:DefinedTerm"],
    "schema:identifier": "http://purl.org/pan-science/PaNET/PaNET01196",
    "schema:inDefinedTermSet": "http://purl.org/pan-science/PaNET/PaNET.owl",
    "schema:name": "X-Ray Absorption Spectroscopy",
    "schema:termCode": "XAS",
}


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------

@dataclass
class EmitResult:
    document: dict[str, Any] = field(default_factory=dict)
    #: Profiles the content actually satisfies, in the order written.
    profiles: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    """A URI-safe fragment. Entry names carry dots (`FeFoil.001`), which
    are legal in a path segment but read badly in an identifier."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_") or "x"


def _checksum(path: Path) -> dict[str, Any] | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return {
        "@type": ["spdx:Checksum"],
        "spdx:algorithm": "spdx:checksumAlgorithm_sha256",
        "spdx:checksumValue": h.hexdigest(),
    }


def _readable(local: str) -> str:
    """`fluorescenceabsorptioncoefficient` -> `fluorescence absorption
    coefficient`. The concept locals run their words together, which is
    fine as an identifier and poor as a human-facing label."""
    for word in (
        "absorptioncoefficient", "intensity", "energy", "monochromator",
        "fluorescence", "electronyield", "incident", "transmitted",
        "reference", "uncertainty", "emission", "count", "time",
    ):
        local = local.replace(word, f" {word} ")
    return " ".join(local.split()).strip() or local


def _suffix(inspection: InspectionResult) -> str:
    """The source file's own extension, so a generated contentUrl points
    at something with the right name rather than always saying .nxs."""
    return Path(inspection.filename).suffix or ".dat"


def _modified(inspection: InspectionResult) -> str:
    """The file's last-modified date. Falls back to today only when the
    file is not on disk to ask."""
    if inspection.source:
        try:
            ts = os.stat(inspection.source).st_mtime
            return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        except OSError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _scalar_text(value: Any) -> str:
    """A PropertyValue value as the profile wants it: a string. Sequences
    become space-separated, which is how NeXus writes a reflection plane
    in its own text serialisations."""
    if isinstance(value, (list, tuple)):
        return " ".join(_text(v) for v in value)
    return _text(value)


def _property_value(
    prop: str, cv: ConceptValue, base: str, label: str = ""
) -> dict[str, Any]:
    pv: dict[str, Any] = {
        "@type": ["schema:PropertyValue"],
        "schema:propertyID": [{"@id": f"xas:{prop}"}],
        "schema:name": label or prop.replace("_", " "),
        # Always a string. The profile types schema:value as one, and a
        # d-spacing emitted as a JSON float or a reflection plane emitted
        # as [3, 1, 1] silently fails the constraint that says the
        # monochromator must report those values at all.
        "schema:value": _scalar_text(cv.value),
    }
    if cv.units:
        pv["schema:unitText"] = cv.units
    if cv.convention:
        # Provenance survives into the document: a consumer can see that
        # this value came from a non-standard layout without re-running
        # the extractor.
        pv["schema:description"] = (
            f"read from a non-standard file layout ({cv.convention})"
        )
    return pv


# ---------------------------------------------------------------------------
# per-entry emission
# ---------------------------------------------------------------------------

def _coverage(entry: Any) -> str | None:
    """When an entry was measured, as a date or an ISO 8601 interval.

    `.start_time` and `.end_time` are properties on both NXEntry and
    XDIEntry. Going through them rather than a NeXus-only field lookup is
    what lets one function serve either binding.

    Separate from `_emit_entry` because the document needs this even when
    it emits no parts: a file with one entry states its own coverage, and
    reading the times back off the parts would silently lose them.
    """
    start = entry.start_time
    if not start:
        return None
    # `_scalar_text`, not `_text`: h5py hands back a one-element array for
    # a scalar string field, and `str()` on that writes the brackets into
    # the document.
    #
    # Normalised here rather than only in the XDI binding, because NeXus
    # files write `2020-08-10 09:18:48` -- a space, not a `T`. Nothing
    # rejects it: the schema and the SHACL both say "ISO8601 date-time"
    # in prose and require only a string. So it validates cleanly and
    # still throws in any consumer that parses it as one.
    def iso(value: Any) -> str:
        text = _scalar_text(value)
        return normalize_datetime(text) or text

    end = entry.end_time
    return f"{iso(start)}/{iso(end)}" if end else iso(start)


def _emit_entry(
    entry: NXEntry,
    record: ConceptRecord,
    base: str,
    structure: dict[str, Any],
    inherited: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One NXentry as a part: what varies between entries, plus the
    structure it has.

    Typed as both a MediaObject and a Dataset. The manifest profile calls
    a distribution part a MediaObject, which is right about the bytes --
    an entry is a addressable chunk of one HDF5 file -- but an NXentry is
    also a dataset in its own right, with its own variables, its own
    acquisition and its own structure. Asserting only MediaObject loses
    that; asserting both says what it is at each level.

    `structure` is the full structure object on the first part that has
    it and an @id reference on later parts that share it, so a layout is
    stated once and referenced thereafter.

    `inherited` carries what CDIF requires of anything typed
    schema:Dataset -- an identifier, a modification date, licence
    information, and a url. Typing a part as a Dataset without them
    trades one SHACL violation for four per part, and they are all
    genuinely properties of the file that each part sits in, so they are
    inherited rather than invented.
    """
    eid = _slug(record.entry_name)
    part: dict[str, Any] = {
        "@id": f"{base}/{eid}",
        "@type": ["schema:MediaObject", "schema:Dataset"],
        "schema:name": _text(entry.title or record.entry_name),
        "schema:contentUrl": f"{base}#{record.entry_path}",
        "cdi:isStructuredBy": structure,
    }
    # An identifier of its own: the entry is what distinguishes it, and a
    # consumer that harvests parts as datasets needs to be able to name
    # this one.
    part["schema:identifier"] = f"{base}#{record.entry_path}"
    part["schema:url"] = f"{base}#{record.entry_path}"
    for key in ("schema:dateModified", "schema:license",
                "schema:conditionsOfAccess", "schema:creator"):
        if inherited and key in inherited:
            part[key] = inherited[key]

    # CDIF recommends a description and keywords for anything discoverable,
    # and a part typed as a Dataset is exactly that. Built from what this
    # entry measured rather than from the file's aggregate, so the 26
    # spectra in one container are told apart rather than described 26
    # times identically.
    subject = _entry_subject(record)
    element, edge, mode = (subject.get("element"), subject.get("edge"),
                           subject.get("mode"))
    sentence = []
    if element and edge:
        sentence.append(f"{element} {edge}-edge measurement")
    elif element:
        sentence.append(f"{element} measurement")
    else:
        sentence.append("Measurement")
    if mode:
        sentence.append(f"in {mode.lower()}")
    sentence.append(f"recorded as entry {record.entry_path}")
    part["schema:description"] = " ".join(sentence) + "."

    pairs = [("element", element)] if element else []
    if edge:
        pairs.append(("edge", edge))
    if pairs:
        part["schema:keywords"] = _keywords(pairs)
    # No `schema:temporalCoverage` here either -- see the acquisition
    # block in `emit_document`. A part's own acquisition time is
    # currently represented only in the file-wide span on the single
    # activity; giving each entry its own prov:wasGeneratedBy would keep
    # it per-entry, and is the open question in README.md.
    if record.definition:
        part["dcterms:conformsTo"] = [
            {"@id": f"nxs:applications/{record.definition}.html"}
        ]
    return part


def _variables(
    record: ConceptRecord, base: str, entry_slug: str, doc_slug: str
) -> tuple[list[dict], list[dict]]:
    """Array concepts as InstanceVariables, and the DataStructure
    components that define them.

    The two are built together because CDIF requires them to reference
    each other: the variable `cdif:uses` the RepresentedVariable that the
    component `cdif:isDefinedBy_RepresentedVariable` points at. Building
    them apart is how that round trip gets broken.
    """
    variables: list[dict] = []
    components: list[dict] = []
    for concept in sorted(record.values):
        for cv in record.values[concept]:
            if not cv.is_array:
                continue
            # An unmapped column has no concept to name it by, so the
            # identifier comes from the label instead -- otherwise every
            # unnamed column in a file would collide on one @id.
            unmapped = concept == OGC_NIL_MISSING
            local = _slug(cv.label) if unmapped else concept.split(":", 1)[-1]
            rv_id = f"{base}/rv/{local}"
            # Document-scoped, not structure-scoped. Two structures
            # that both measure monochromator energy measure the SAME
            # variable -- same concept, same datatype, same unit -- and
            # differ only in where it sits, which is what the physical
            # mapping records. Scoping the id by structure produced
            # byte-identical InstanceVariables differing only in @id.
            iv_id = f"ex:DV/{doc_slug}/iv/{local}"
            iv = {
                "@id": iv_id,
                "@type": ["cdi:InstanceVariable", "schema:PropertyValue"],
                "schema:name": cv.label or Path(cv.source_path).name,
                # The writer's own long_name where there is one: it
                # describes this field in this file, which no generic
                # concept label can do.
                "schema:description": cv.long_name or _readable(local),
                "schema:propertyID": [{"@id": OGC_NIL_MISSING}]
                if unmapped else
                [{"@id": concept.replace("cdifxas:", "xas:")}],
                "cdif:physicalDataType": _xsd_for(cv.dtype),
                "cdif:uses": [rv_id],
            }
            # Two different claims, kept apart on purpose.
            #
            # schema:unitText is what THIS FILE recorded. Only written
            # when the source says so: an empty string would assert that
            # the unit IS the empty string, where absence says the file
            # did not record one.
            #
            # schema:unitCode is what the CONCEPT is, from the glossary,
            # and is written only where the file is silent. It is how a
            # consumer learns that mutrans is dimensionless rather than
            # unit-unknown -- no XAS format records that, because to a
            # physicist it goes without saying. As an IRI it needs no
            # parsing.
            if cv.units:
                iv["schema:unitText"] = cv.units
            elif not unmapped:
                stated = load_concept_units().get(concept)
                if stated:
                    iv["schema:unitCode"] = {"@id": stated}
            variables.append(iv)
            # The physical mapping says how to find the values, and
            # that differs by what the source is, so the subclass follows
            # the facts the value carries rather than the input format.
            #
            # A column index means a text table: TextMapping, with the
            # column and the field width a reader slices on. Where the
            # observed minimum and maximum widths are equal the file is
            # genuinely fixed-width and the number is exact; where they
            # differ the reader must tokenise, and the range says so.
            #
            # A path means a container that only a bespoke reader can
            # open -- h5py for NeXus -- so LocatorMapping, whose locator
            # is that path.
            if cv.index is not None:
                mapping: dict[str, Any] = {
                    "@id": f"ex:DV/{entry_slug}/pm/{local}",
                    "@type": ["cdif:TextMapping"],
                    "cdif:index": cv.index,
                    "cdif:physicalDataType": _xsd_for(cv.dtype),
                }
                if cv.width:
                    mapping["cdi:minimumLength"] = cv.width[0]
                    mapping["cdi:maximumLength"] = cv.width[1]
            else:
                mapping = {
                    "@id": f"ex:DV/{entry_slug}/pm/{local}",
                    "@type": ["cdif:LocatorMapping"],
                    "cdif:locator": cv.source_path,
                    "cdif:physicalDataType": _xsd_for(cv.dtype),
                }
            # The back-reference closes the loop CDIF expects: the
            # mapping says which variable it formats, so a consumer can
            # go from bytes to meaning either way.
            mapping["cdif:formats_InstanceVariable"] = {"@id": iv_id}
            components.append({
                "@id": f"ex:DV/{entry_slug}/dsc/{local}",
                "@type": ["cdi:MeasureComponent"]
                if not _is_coordinate(local)
                else ["cdi:DimensionComponent"],
                "cdif:isDefinedBy_RepresentedVariable": {
                    "@id": rv_id,
                    "@type": ["cdi:RepresentedVariable"],
                    "schema:name": _readable(local),
                    "cdif:name": {
                        "@type": ["cdi:ObjectName"],
                        "cdif:name": _readable(local),
                    },
                },
                "cdif:hasPhysicalMapping": mapping,
            })
    return variables, components


#: Concepts that are the independent variable of a spectrum rather than
#: something measured against it.
_COORDINATES = {"monochromatorenergy", "energy", "emissionenergy"}


def _is_coordinate(local: str) -> bool:
    return local in _COORDINATES


def _collect(records: list[ConceptRecord]) -> dict[str, list[ConceptValue]]:
    """Scalar concepts shared across entries.

    A scan series states its facility, beamline and monochromator once
    per entry and identically every time. Repeating that 26 times would
    be noise, so identical values are collapsed to one and stated at file
    level; anything that actually varies stays on its part.
    """
    shared: dict[str, list[ConceptValue]] = {}
    for concept in {c for r in records for c in r.values}:
        values = [
            cv for r in records for cv in r.values.get(concept, [])
            if not cv.is_array
        ]
        if not values:
            continue
        distinct = {_text(cv.value) for cv in values if cv.value is not None}
        if len(distinct) == 1 and len(values) == len(records):
            shared[concept] = [values[0]]
    return shared


# ---------------------------------------------------------------------------
# document assembly
# ---------------------------------------------------------------------------

def _place_scalars(
    shared: dict[str, list[ConceptValue]], base: str
) -> dict[str, Any]:
    """Sort scalar concepts into the buckets `CONCEPT_SLOTS` names.

    A concept with no binding is not dropped: it lands on the event as an
    additionalProperty. Losing a value because nobody wrote a binding for
    it would be the worst of the available failures, and losing it
    silently would be worse still -- so the caller warns as well.
    """
    buckets: dict[str, Any] = {
        "facility": None, "instrument": None, "source": [],
        "monochromator": [], "sample": [], "keyword": [],
        "technique": [], "activity": [], "unbound": [],
    }
    for concept, values in sorted(shared.items()):
        cv = values[0]
        if cv.value is None:
            continue
        slot = CONCEPT_SLOTS.get(concept)
        if slot is None:
            buckets["unbound"].append(
                _property_value(concept.split(":")[-1], cv, base))
            continue
        if slot.target in ("facility", "instrument"):
            buckets[slot.target] = _text(cv.value)
        elif slot.target == "keyword":
            buckets["keyword"].append((slot.prop, _text(cv.value)))
        elif slot.target == "technique":
            buckets["technique"].append(_text(cv.value))
        else:
            buckets[slot.target].append(
                _property_value(slot.prop, cv, base, slot.label))
    return buckets


def _entry_subject(record: ConceptRecord) -> dict[str, str]:
    """The element, edge and detection mode this one entry measured.

    The document aggregates these across every entry, which is right for
    the file as a whole and useless for telling one part from another. A
    catalogue harvesting 26 spectra needs to know that this one is the Fe
    K edge and that one is not.
    """
    out: dict[str, str] = {}
    for concept, key in (("cdifxas:elementanalyzed", "element"),
                         ("cdifxas:edgeanalyzed", "edge"),
                         ("cdifxas:xasmeasurementmode", "mode")):
        for cv in record.values.get(concept, []):
            if cv.value is not None and not cv.is_array:
                out[key] = _text(cv.value)
                break
    return out


def _keywords(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind, value in pairs:
        # `schema:about` is not decoration: the profile uses it to tell
        # the two mandated keywords apart, since both are DefinedTerms.
        if kind == "element":
            out.append({
                "@type": ["schema:DefinedTerm"],
                "schema:name": _ELEMENTS.get(value, value),
                "schema:termCode": value,
                "schema:inDefinedTermSet": SWEET_ELEMENTS,
                "schema:about": "element.symbol",
            })
        else:
            out.append({
                "@type": ["schema:DefinedTerm"],
                "schema:name": f"{value}-edge",
                "schema:termCode": value,
                "schema:inDefinedTermSet": XDI_DICTIONARY,
                "schema:about": "element.edge",
            })
    return out


def _with_sentinels(kind: str, props: list, is_xas: bool = False) -> list:
    """Fill out an instrument the profile has requirements for.

    Only properties the profile names for this instrument are added, and
    only when absent -- a value read from the file is never displaced.
    Each sentinel says in its own description that it is one, so a
    consumer can tell a gap from a reading without re-running anything.
    """
    required = REQUIRED_INSTRUMENT_PROPERTIES.get(kind)
    if not required:
        return props
    present = {
        pv["schema:propertyID"][0]["@id"] for pv in props
        if pv.get("schema:propertyID")
    }
    out = list(props)
    for prop, unit in required:
        if f"xas:{prop}" in present:
            continue
        if prop == "xraysourcetype":
            value = SYNCHROTRON_SOURCE if is_xas else OGC_NIL_MISSING
            why = (
                "not recorded in the source file; an XAS measurement is "
                "made at a synchrotron, so the technique supplies this "
                "where the file does not"
                if is_xas else
                "not recorded in the source file, and the technique does "
                "not imply one"
            )
        else:
            value, why = UNKNOWN, (
                "not recorded in the source file; the profile requires "
                "this property, so it is reported as unknown rather than "
                "omitted or guessed"
            )
        sentinel: dict[str, Any] = {
            "@type": ["schema:PropertyValue"],
            "schema:propertyID": [{"@id": f"xas:{prop}"}],
            "schema:name": _readable(prop),
            "schema:value": value,
            "schema:description": why,
        }
        if unit:
            sentinel["schema:unitText"] = unit
        out.append(sentinel)
    return out


def _sample_name(records) -> str | None:
    """A name for the sample, where a binding supplied one."""
    for record in records:
        for concept in ("cdifxas:samplename", "cdifsas:samplename"):
            value = record.value_of(concept)
            if value:
                return _text(value)
    return None


def _instruments(
    buckets: dict[str, Any], base: str, is_xas: bool = False
) -> list[dict[str, Any]]:
    """Source and monochromator as peer instruments of the acquisition,
    each carrying its own settings. Mirrors the shape the XDI converter
    settled on, so the two bindings produce comparable graphs."""
    def used(name: str, kind: str, props: list) -> dict[str, Any]:
        slug = kind.split(":", 1)[-1]
        props = _with_sentinels(kind, props, is_xas)
        # The entity wraps the instrument rather than being it. That
        # nesting is what the profile frame declares, and a frame drops
        # what it does not declare -- so a flat prov:used passes
        # validation of the raw document and then vanishes when framed.
        instrument: dict[str, Any] = {
            "@id": f"{base}/instrument/{slug}",
            "@type": ["schema:Product", "schema:Thing"],
            "schema:name": name,
            "schema:additionalType": [{"@id": kind}],
        }
        if props:
            instrument["schema:additionalProperty"] = props
        return {
            "@id": f"{base}/used/{slug}",
            "@type": ["schema:Thing", "prov:Entity"],
            "schema:instrument": instrument,
        }

    # Three peers, because the profile distinguishes them: the beamline
    # is where the measurement happened, the source is what made the
    # X-rays, the monochromator is what selected their energy. An earlier
    # version folded source into beamline, which reads sensibly and
    # satisfies neither constraint.
    out: list[dict[str, Any]] = []
    if buckets["instrument"]:
        out.append(used(buckets["instrument"], BEAMLINE_TYPE, []))
    # An XAS document must describe a source and a monochromator whether
    # or not the file said anything about them -- the profile requires
    # both peers. Emitting them with sentinels states what is unknown;
    # omitting them states nothing at all and fails validation. For any
    # other technique only what the file actually carries is described.
    if buckets["source"] or is_xas:
        out.append(used("X-ray source", SOURCE_TYPE, buckets["source"]))
    if buckets["monochromator"] or is_xas:
        out.append(used("monochromator", MONOCHROMATOR_TYPE,
                        buckets["monochromator"]))
    return out


def emit_document(
    inspection: InspectionResult,
    nexus: NeXusResult,
    mapping: MappingResult,
    base: str | None = None,
    source_url: str | None = None,
    encoding_format: str = HDF5_MEDIA_TYPE,
    format_specification: str | None = None,
) -> EmitResult:
    """Assemble one CDIF JSON-LD document for a file.

    A multi-entry file becomes one Dataset with one part per NXentry --
    the archive-of-parts model described in README.md. What every entry shares
    (facility, beamline, monochromator, data structure) is stated once at
    file level and referenced; only what varies stays on the part.
    """
    result = EmitResult()
    stem = Path(inspection.filename).stem
    source_label = (
        "XDI" if encoding_format == XDI_MEDIA_TYPE else "NeXus/HDF5"
    )
    # Whether this is XAS is settled before anything is written, because
    # the description, the peer instruments and the profile list all
    # depend on it.
    is_xas = (
        format_specification == XDI_SPECIFICATION
        or any((d or "").startswith("NXxas") for d in nexus.definitions)
    )
    technique_label = (
        "X-ray absorption spectroscopy" if is_xas else "Scientific"
    )
    slug = _slug(stem)
    base = (base or DEFAULT_BASE).rstrip("/") + "/" + slug

    records = mapping.records
    if not records:
        result.warnings.append(
            "no mapped entries; emitting file-level core only"
        )

    shared = _collect(records)
    buckets = _place_scalars(shared, base)
    if buckets["unbound"]:
        result.warnings.append(
            f"{len(buckets['unbound'])} concept(s) had no CDIF binding and "
            f"were emitted as additionalProperty on the acquisition event"
        )

    # -- parts and structures -----------------------------------------------
    #
    # Entries with the same layout share one DataStructure, emitted once
    # and referenced by each. That is what the structural signature
    # computed back in `map` is for.
    parts: list[dict[str, Any]] = []
    part_entries: list[tuple[dict[str, Any], Any]] = []
    structures: list[dict[str, Any]] = []
    variables: list[dict[str, Any]] = []
    seen_variables: set[str] = set()

    # What a part inherits by being a Dataset in this file. Same
    # sources as the document's own values below, so the two cannot drift.
    part_defaults: dict[str, Any] = {
        "schema:dateModified": _modified(inspection),
        "schema:license": [OGC_NIL_MISSING],
        # Neither format records a depositor. The sentinel says "looked,
        # absent" for a part exactly as it does for the file; a deployment
        # with real depositor information overlays both.
        "schema:creator": {
            "@type": ["schema:Person"],
            "schema:name": MISSING_TEXT,
        },
    }

    for n, (_sig, group) in enumerate(mapping.structure_groups().items(), 1):
        struct_id = f"ex:DV/{slug}/structure/{n}"
        vars_, components = _variables(group[0], base, f"{slug}/{n}", slug)
        structure: dict[str, Any] = {"@id": struct_id}
        if components:
            structure = {
                "@id": struct_id,
                "@type": ["cdi:DimensionalDataStructure"],
                "schema:name": f"{stem} structure {n}",
                "schema:description":
                    f"shared by {len(group)} of {len(records)} entries",
                "cdi:has_DataStructureComponent": components,
            }
            structures.append(structure)
        for v in vars_:
            if v["@id"] not in seen_variables:
                seen_variables.add(v["@id"])
                variables.append(v)
        # A file with one entry needs no parts. The part would carry the
        # entry's name, identifier, url, description, keywords, licence
        # and dates -- all of which the dataset already states about
        # itself, because with one entry the dataset IS the entry. The
        # structure goes on the distribution instead, which is then the
        # whole of the data rather than a share of it.
        if len(records) < 2:
            continue
        for i, rec in enumerate(group):
            entry = next(
                (e for e in nexus.entries if e.path == rec.entry_path), None)
            if entry is None:
                continue
            # Inline on the first part that has this structure, an @id
            # reference on the rest. The reference denotes the same node,
            # so a consumer resolving it finds the components.
            part = _emit_entry(
                entry, rec, base,
                structure if i == 0 else {"@id": struct_id},
                inherited=part_defaults)
            parts.append(part)
            # Keep the entry beside its part: the per-part acquisition is
            # attached below, once the instruments it references exist.
            part_entries.append((part, entry))

    # -- distribution -------------------------------------------------------
    distribution: dict[str, Any] = {
        # cdi:PhysicalDataSet alongside DataDownload: the profile wants
        # the byte stream typed as a dataset in its own right, not only
        # as a way of getting one.
        "@type": ["schema:DataDownload", "cdi:PhysicalDataSet"],
        "schema:contentUrl": source_url or f"{base}{_suffix(inspection)}",
        "schema:encodingFormat": [encoding_format],
    }
    if inspection.file_size is not None:
        distribution["schema:contentSize"] = str(inspection.file_size)
    if inspection.source:
        checksum = _checksum(Path(inspection.source))
        if checksum:
            distribution["spdx:checksum"] = checksum
    # What specification the bytes follow. A NeXus file names its
    # application definitions; an XDI file names the XDI specification.
    # The XAS profile requires one or the other to be declared here.
    conforms = [
        {"@id": f"nxs:applications/{d}.html"} for d in nexus.definitions
    ]
    if format_specification:
        conforms.insert(0, {"@id": format_specification})
    if conforms:
        distribution["dcterms:conformsTo"] = conforms
    # Where a file has several entries the structure sits on the part it
    # describes: with 26 entries over two layouts, a structure on the
    # distribution would assert of the whole file something true of only
    # some of its parts.
    #
    # Where a file has one entry there are no parts, and the distribution
    # is the whole of the data, so the structure belongs to it directly.
    # Same rule either way -- the structure is stated about exactly what
    # it describes.
    #
    # An @id reference is not a "bare" reference in RDF: it denotes the
    # same node another part defines inline, so the SHACL check that the
    # target carries cdi:has_DataStructureComponent is satisfied.
    if parts:
        distribution["schema:hasPart"] = parts
    elif structures:
        distribution["cdi:isStructuredBy"] = structures

    # -- the dataset --------------------------------------------------------
    #
    # The root `@default` attribute is NeXus's own pointer at the entry
    # that best represents the file, so it names the dataset when present.
    title = None
    if nexus.default_entry:
        default = next(
            (e for e in nexus.entries if e.name == nexus.default_entry), None)
        title = _text(default.title) if default and default.title else None
    if not title and records:
        entry = next(
            (e for e in nexus.entries if e.path == records[0].entry_path),
            None)
        title = _text(entry.title) if entry and entry.title else None

    # A title of one or two characters -- "Cu" as a Sample.name -- is
    # below what the profile accepts and useless to a reader anyway, so
    # the filename stands in. The file's own words are preferred whenever
    # they are usable.
    if title and len(str(title).strip()) < 3:
        title = f"{title} ({stem})"

    doc: dict[str, Any] = {
        "@context": CONTEXT,
        "@id": base,
        "@type": ["schema:Dataset"],
        "schema:name": title or stem,
        "schema:identifier": f"local:{slug}",
        "schema:url": source_url or f"{base}{_suffix(inspection)}",
        "schema:distribution": [distribution],
        # The file's own mtime, not the run time: this states when the
        # data last changed, which is what a harvester wants to compare.
        "schema:dateModified": _modified(inspection),
        # A NeXus file carries no licence field. The OGC nil URI says
        # "looked, absent" rather than implying an unrestricted licence.
        "schema:license": [OGC_NIL_MISSING],
    }
    # Always a description. CDIF core wants one, and a record with no
    # prose is hard to place even when every field is populated.
    if len(records) > 1:
        doc["schema:description"] = (
            f"{len(records)} measurements in one {source_label} container, "
            f"described as parts of one dataset."
        )
    else:
        subject = title or stem
        doc["schema:description"] = (
            f"{technique_label} measurement, {subject}, "
            f"read from a {source_label} file."
        )

    # Any header value this reader replaced rather than read, said in the
    # record itself and not only in the provenance report. What a
    # consumer needs to know -- that 295.0 K stands in for the words
    # "room temperature" and is not a reading off an instrument -- is
    # invisible in the value, so it has to be stated beside it.
    conversions = list(dict.fromkeys(
        note for r in records for note in r.conversion_notes
    ))
    if conversions:
        doc["schema:description"] += (
            " Conversion notes: " + "; ".join(conversions) + "."
        )

    # The creator belongs to the dataset, not to the record about it. It
    # is a sentinel here because neither format carries one; a deployment
    # with real depositor information overlays it.
    doc["schema:creator"] = {
        "@type": ["schema:Person"],
        "schema:name": MISSING_TEXT,
    }

    # Whether this is XAS is decided by what the file declares, not by
    # the namespace its concepts happen to sit in. Four genuinely
    # technique-neutral concepts -- facility, beamline, probe, source
    # type -- are still minted under cdifxas: because that crosswalk was
    # written first, so sniffing the prefix made an NXsas file claim
    # conformance to the XAS profile and advertise itself as X-ray
    # absorption spectroscopy. A false conformance claim is worse than a
    # missing one: it survives into a catalogue and misroutes the record.
    technique: list[dict[str, Any]] = [dict(XAS_TECHNIQUE)] if is_xas else []
    # Detection mode is collected across all entries rather than from
    # the shared set. In FeXAS the reference foil is Transmission and the
    # 25 sample scans are Fluorescence, so the mode is exactly what does
    # NOT agree -- and taking only agreed values would drop it entirely.
    modes = sorted({
        _text(cv.value)
        for r in records
        for cv in r.values.get("cdifxas:xasmeasurementmode", [])
        if cv.value is not None
    })
    for mode in modes:
        technique.append({
            "@type": ["schema:DefinedTerm"],
            "schema:name": mode,
            "schema:inDefinedTermSet": NXXAS_MODE_TERMSET,
        })
    if technique:
        doc["schema:measurementTechnique"] = technique

    keywords = _keywords(buckets["keyword"])
    if keywords:
        doc["schema:keywords"] = keywords

    if buckets["facility"]:
        doc["schema:contributor"] = [{
            "@type": ["schema:Role"],
            "schema:roleName": "Facility",
            "schema:contributor": {
                "@type": ["schema:Organization"],
                "schema:name": buckets["facility"],
            },
        }]

    # -- acquisition --------------------------------------------------------
    # Read off the entries, not the parts. A file with one entry emits no
    # parts, and taking the times from them dropped the acquisition date
    # from every single-entry file -- which is most XDI files, and every
    # one of them records a scan time.
    times = [c for c in (_coverage(e) for e in nexus.entries) if c]
    event: dict[str, Any] = {
        "@id": f"{base}/acquisition",
        "@type": ["schema:Action", "prov:Activity"],
        "schema:additionalType": [{"@id": ANALYSIS_EVENT}],
        "schema:name": f"acquisition of {stem}",
    }
    if buckets["facility"]:
        event["schema:location"] = {
            "@type": ["schema:Place"],
            "schema:additionalType": [{"@id": "xas:facility"}],
            "schema:name": buckets["facility"],
        }
    instruments = _instruments(buckets, base, is_xas)
    if instruments:
        event["prov:used"] = instruments
    if buckets["sample"]:
        # Typed as a material sample, which the profile requires in two
        # places at once: schema:Product and schema:Thing on @type, and
        # both the bare "MaterialSample" string and the iSamples IRI on
        # additionalType. An earlier version emitted a plain
        # Thing/prov:Entity, which no NeXus example exercised because
        # none of them carried sample properties -- the first XDI file
        # through the pipeline found it.
        event["schema:object"] = {
            "@id": f"{base}/sample",
            "@type": ["schema:Product", "schema:Thing"],
            "schema:additionalType": [
                {"@id": MATERIAL_SAMPLE_IRI},
                "MaterialSample",
            ],
            "schema:name": _sample_name(records) or "sample",
            "schema:additionalProperty": buckets["sample"],
        }
    if buckets["activity"] or buckets["unbound"]:
        event["schema:additionalProperty"] = (
            buckets["activity"] + buckets["unbound"])
    # When the scan ran, on the acquisition -- not as coverage on the
    # dataset. `schema:temporalCoverage` says what period the data is
    # *about*, which for a spectrum is nothing: an absorption edge is a
    # property of a material, not a record of an interval. The time that
    # matters is when the measurement was made, and that belongs to the
    # activity that made it.
    #
    # `startTime`/`endTime`, not `startDate`: schema.org gives startDate
    # to Event and CreativeWork, and Action -- which this is -- takes
    # startTime. The profile's cdifProvActivity shape asks for both by
    # name.
    if times:
        starts = sorted(t.split("/")[0] for t in times)
        ends = sorted(t.split("/")[-1] for t in times)
        event["schema:startTime"] = starts[0]
        if ends[-1] != starts[0]:
            event["schema:endTime"] = ends[-1]
    doc["prov:wasGeneratedBy"] = [event]

    # One acquisition per part, so a file holding many entries says when
    # each was measured rather than only when the batch as a whole ran.
    # The file-level event above spans them all, which for a scan series
    # measured over three days answers a different question from "when
    # was this spectrum taken".
    #
    # `prov:used` here is @id references to the instrument wrappers the
    # file-level event describes in full. The same beamline measured
    # every entry, so repeating its description 26 times would assert 26
    # beamlines; a reference denotes the one node. It also satisfies the
    # cdifProvActivity shape, which requires at least one prov:used on
    # any activity reached through prov:wasGeneratedBy -- and a part's
    # activity is reached exactly that way.
    used_refs = [{"@id": u["@id"]} for u in instruments if "@id" in u]
    for part, entry in part_entries:
        coverage = _coverage(entry)
        if not coverage or not used_refs:
            continue
        start, _, end = coverage.partition("/")
        acquisition: dict[str, Any] = {
            "@id": f"{part['@id']}/acquisition",
            "@type": ["schema:Action", "prov:Activity"],
            "schema:additionalType": [{"@id": ANALYSIS_EVENT}],
            "schema:name": f"acquisition of {entry.name}",
            "schema:startTime": start,
            "prov:used": used_refs,
        }
        if end and end != start:
            acquisition["schema:endTime"] = end
        part["prov:wasGeneratedBy"] = [acquisition]

    if variables:
        doc["schema:variableMeasured"] = variables
    # -- detected conformance ----------------------------------------------
    #
    # Claimed per profile only where the content for it is actually
    # present: "detect conformance, don't assert it".
    profiles = ["core/1.1"]
    if technique or keywords or times or buckets["facility"]:
        profiles.append("discovery/1.1")
    if variables:
        profiles.append("data_description/1.1")
    if structures:
        profiles.append("data_structure/1.1")
    if is_xas and any(r.values for r in records):
        # Declaring NXxas is necessary but not sufficient. xasCore is a
        # claim about content, so an entry that declares the definition
        # and carries none of it must not make the claim -- the whole
        # point of detecting conformance rather than asserting it.
        profiles.append("xasCore/1.0")
    result.profiles = profiles

    doc["schema:subjectOf"] = {
        # The catalog record must be an IRI, not a blank node: SHACL
        # targets it by identity, and a blank node cannot be referred to
        # from outside the document it appears in.
        "@id": f"{base}/metadata",
        "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:about": {"@id": base},
        # What this record is and how it came to exist. No creator: a
        # catalog record is machine output, and naming a person as its
        # author would misattribute a generated artifact.
        "schema:description": (
            f"CDIF metadata generated by {APPLICATION} from a "
            f"{source_label} file ({inspection.filename})."
        ),
        "dcterms:conformsTo": [
            {"@id": PROFILE.format(p)} for p in profiles
        ],
    }

    result.document = doc
    result.warnings.extend(mapping.warnings)
    return result
