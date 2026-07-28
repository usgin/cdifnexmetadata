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
it" decision in DESIGN.md. A file with no `NXdata` gets core and
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

from hdf5metadata.inspect.hdf5 import InspectionResult
from hdf5metadata.inspect.nexus import NeXusResult, NXEntry
from hdf5metadata.map.concepts import ConceptRecord, ConceptValue, MappingResult

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

def _emit_entry(
    entry: NXEntry,
    record: ConceptRecord,
    base: str,
    struct_id: str,
) -> dict[str, Any]:
    """One NXentry as a part: what varies between entries, plus a
    reference to the structure it shares with its siblings."""
    eid = _slug(record.entry_name)
    part: dict[str, Any] = {
        "@id": f"{base}/{eid}",
        "@type": ["schema:MediaObject"],
        "schema:name": _text(entry.field_value("title") or record.entry_name),
        "schema:contentUrl": f"{base}#{record.entry_path}",
        "cdi:isStructuredBy": {"@id": struct_id},
    }
    start = entry.field_value("start_time")
    end = entry.field_value("end_time")
    if start:
        part["schema:temporalCoverage"] = (
            f"{_text(start)}/{_text(end)}" if end else _text(start)
        )
    if record.definition:
        part["dcterms:conformsTo"] = [
            {"@id": f"nxs:applications/{record.definition}.html"}
        ]
    return part


def _variables(
    record: ConceptRecord, base: str, entry_slug: str
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
            local = concept.split(":", 1)[-1]
            rv_id = f"{base}/rv/{local}"
            iv_id = f"ex:DV/{entry_slug}/iv/{local}"
            variables.append({
                "@id": iv_id,
                "@type": ["cdi:InstanceVariable", "schema:PropertyValue"],
                "schema:name": Path(cv.source_path).name,
                # The writer's own long_name where there is one: it
                # describes this field in this file, which no generic
                # concept label can do.
                "schema:description": cv.long_name or _readable(local),
                "schema:propertyID": [{"@id": concept.replace(
                    "cdifxas:", "xas:")}],
                "schema:unitText": cv.units or "",
                "cdif:physicalDataType": _xsd_for(cv.dtype),
                "cdif:uses": [rv_id],
            })
            # A NeXus path locates an array inside a container; it is not
            # a column index, so LocatorMapping is the right mapping
            # subclass. TextMapping + cdif:index is the tabular analog.
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
                "cdif:hasPhysicalMapping": {
                    "@id": f"ex:DV/{entry_slug}/pm/{local}",
                    "@type": ["cdif:LocatorMapping"],
                    "cdif:locator": cv.source_path,
                    "cdif:physicalDataType": _xsd_for(cv.dtype),
                    # The back-reference closes the loop CDIF expects:
                    # the mapping says which variable it formats, so a
                    # consumer can go from bytes to meaning either way.
                    "cdif:formats_InstanceVariable": {"@id": iv_id},
                },
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


def _instruments(buckets: dict[str, Any], base: str) -> list[dict[str, Any]]:
    """Source and monochromator as peer instruments of the acquisition,
    each carrying its own settings. Mirrors the shape the XDI converter
    settled on, so the two bindings produce comparable graphs."""
    def used(name: str, kind: str, props: list) -> dict[str, Any]:
        slug = kind.split(":", 1)[-1]
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
    if buckets["source"]:
        out.append(used("X-ray source", SOURCE_TYPE, buckets["source"]))
    if buckets["monochromator"]:
        out.append(used("monochromator", MONOCHROMATOR_TYPE,
                        buckets["monochromator"]))
    return out


def emit_document(
    inspection: InspectionResult,
    nexus: NeXusResult,
    mapping: MappingResult,
    base: str | None = None,
    source_url: str | None = None,
) -> EmitResult:
    """Assemble one CDIF JSON-LD document for a file.

    A multi-entry file becomes one Dataset with one part per NXentry --
    the archive-of-parts model in DESIGN.md. What every entry shares
    (facility, beamline, monochromator, data structure) is stated once at
    file level and referenced; only what varies stays on the part.
    """
    result = EmitResult()
    stem = Path(inspection.filename).stem
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
    structures: list[dict[str, Any]] = []
    variables: list[dict[str, Any]] = []
    seen_variables: set[str] = set()

    for n, (_sig, group) in enumerate(mapping.structure_groups().items(), 1):
        struct_id = f"ex:DV/{slug}/structure/{n}"
        vars_, components = _variables(group[0], base, f"{slug}/{n}")
        if components:
            structures.append({
                "@id": struct_id,
                "@type": ["cdi:DimensionalDataStructure"],
                "schema:name": f"{stem} structure {n}",
                "schema:description":
                    f"shared by {len(group)} of {len(records)} entries",
                "cdi:has_DataStructureComponent": components,
            })
        for v in vars_:
            if v["@id"] not in seen_variables:
                seen_variables.add(v["@id"])
                variables.append(v)
        for rec in group:
            entry = next(
                (e for e in nexus.entries if e.path == rec.entry_path), None)
            if entry is not None:
                parts.append(_emit_entry(entry, rec, base, struct_id))

    # -- distribution -------------------------------------------------------
    distribution: dict[str, Any] = {
        # cdi:PhysicalDataSet alongside DataDownload: the profile wants
        # the byte stream typed as a dataset in its own right, not only
        # as a way of getting one.
        "@type": ["schema:DataDownload", "cdi:PhysicalDataSet"],
        "schema:contentUrl": source_url or f"{base}.nxs",
        "schema:encodingFormat": ["application/x-hdf5"],
    }
    if inspection.file_size is not None:
        distribution["schema:contentSize"] = str(inspection.file_size)
    if inspection.source:
        checksum = _checksum(Path(inspection.source))
        if checksum:
            distribution["spdx:checksum"] = checksum
    if nexus.definitions:
        distribution["dcterms:conformsTo"] = [
            {"@id": f"nxs:applications/{d}.html"} for d in nexus.definitions
        ]
    if parts:
        distribution["schema:hasPart"] = parts

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

    doc: dict[str, Any] = {
        "@context": CONTEXT,
        "@id": base,
        "@type": ["schema:Dataset"],
        "schema:name": title or stem,
        "schema:identifier": f"local:{slug}",
        "schema:url": source_url or f"{base}.nxs",
        "schema:distribution": [distribution],
        # The file's own mtime, not the run time: this states when the
        # data last changed, which is what a harvester wants to compare.
        "schema:dateModified": _modified(inspection),
        # A NeXus file carries no licence field. The OGC nil URI says
        # "looked, absent" rather than implying an unrestricted licence.
        "schema:license": [OGC_NIL_MISSING],
    }
    if len(records) > 1:
        doc["schema:description"] = (
            f"{len(records)} measurements in one NeXus HDF5 container, "
            f"described as parts of one dataset."
        )

    is_xas = any(
        c.startswith("cdifxas:") for r in records for c in r.values)
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
    times = [
        p["schema:temporalCoverage"] for p in parts
        if "schema:temporalCoverage" in p
    ]
    event: dict[str, Any] = {
        "@id": f"{base}/acquisition",
        "@type": ["schema:Action", "prov:Activity"],
        "schema:additionalType": [{"@id": "xas:analysisevent"}],
        "schema:name": f"acquisition of {stem}",
    }
    if buckets["facility"]:
        event["schema:location"] = {
            "@type": ["schema:Place"],
            "schema:additionalType": [{"@id": "xas:facility"}],
            "schema:name": buckets["facility"],
        }
    instruments = _instruments(buckets, base)
    if instruments:
        event["prov:used"] = instruments
    if buckets["sample"]:
        event["schema:object"] = {
            "@type": ["schema:Thing", "prov:Entity"],
            "schema:name": "sample",
            "schema:additionalProperty": buckets["sample"],
        }
    if buckets["activity"] or buckets["unbound"]:
        event["schema:additionalProperty"] = (
            buckets["activity"] + buckets["unbound"])
    if times:
        event["schema:startDate"] = sorted(times)[0].split("/")[0]
    doc["prov:wasGeneratedBy"] = [event]

    if times:
        starts = sorted(t.split("/")[0] for t in times)
        ends = sorted(t.split("/")[-1] for t in times)
        doc["schema:temporalCoverage"] = (
            starts[0] if starts[0] == ends[-1] else f"{starts[0]}/{ends[-1]}"
        )

    if variables:
        doc["schema:variableMeasured"] = variables
    if structures:
        doc["cdi:isStructuredBy"] = structures

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
    if is_xas:
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
        "schema:description": f"metadata record for {stem}",
        "dcterms:conformsTo": [
            {"@id": PROFILE.format(p)} for p in profiles
        ],
        "schema:creator": {
            "@type": ["schema:Person"],
            "schema:name": MISSING_TEXT,
        },
    }

    result.document = doc
    result.warnings.extend(mapping.warnings)
    return result
