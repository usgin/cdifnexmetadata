"""Concept-keyed intermediate: NeXus entries to CDIF concept values.

Stage 2. Turns each `NXentry` into a record keyed on **canonical CDIF
concept URIs**, driven by the SSSOM crosswalk. This is the hub of the
architecture — XDI and NeXus are two bindings onto it, and CDIF JSON-LD
is its serialization.

Keying on concepts, not on the binding
--------------------------------------

The production XDI converter keys its intermediate on XDI-flavoured
names (`cdi:Facility_name`), fusing the concept with the format it came
from. That works for one input format and blocks a second. Here the key
is `cdifxas:facility`, and *which* file field produced it is recorded
alongside the value rather than encoded in the key. A second binding is
then a second parser, not a second pipeline.

Provenance travels with every value
-----------------------------------

Each value carries the file path it came from, the SSSOM predicate that
licensed the mapping, and its confidence. A `closeMatch` at 0.8 is
distinguishable from an `exactMatch` at 1.0 downstream, so the eventual
CDIF output can be as confident as its evidence and no more.

Detection mode decides what `intensity` means
---------------------------------------------

`/ENTRY/intensity` is a different concept in every mode — the absorption
coefficient in transmission, the fluorescence-derived coefficient in TFY,
the electron-yield-derived one in TEY. Nothing in the field itself says
which. So the mapper reads `definition` first and selects the mappings
for that definition; a file that declares no definition gets only the
base-class concepts, with a warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cdifnexmetadata.inspect.nexus import (
    NeXusResult,
    NXEntry,
    NXField,
    NXGroup,
)
from cdifnexmetadata.map.crosswalk import (
    Crosswalk,
    Mapping,
    resolve_mapping,
    select_crosswalk,
)
from cdifnexmetadata.map.legacy import LegacyTable, load_legacy, resolve_legacy


@dataclass
class ConceptValue:
    """One value bound to one concept, with how it got there."""

    concept: str
    value: Any = None
    units: str | None = None
    #: Where in the file this came from.
    source_path: str = ""
    #: SSSOM predicate that licensed the mapping.
    predicate: str = ""
    confidence: float = 1.0
    #: Set when the concept is present as an array we did not read.
    is_array: bool = False
    shape: tuple[int, ...] = ()
    dtype: str = ""
    #: The name the source gave this thing -- a NeXus field name, an XDI
    #: column label. Kept separately from `source_path` because the two
    #: coincide in HDF5, where the path ends in the field name, and do
    #: not in XDI, where a column is located by position.
    label: str = ""
    #: The NeXus `long_name` attribute, where the writer supplied one.
    #: It is the field's own description of itself, so it beats anything
    #: the crosswalk could say generically.
    long_name: str | None = None
    #: Free-text note from the crosswalk row, where it flags a caveat.
    note: str = ""
    #: Set when the value came from the legacy path table rather than the
    #: standards crosswalk, naming the writer convention that put it
    #: there. Machine-checkable so a consumer can filter on it rather than
    #: having to read `note`.
    convention: str = ""

    #: Where the value sits when the source is tabular: the 1-based
    #: column, and the observed field width in characters. Set by the XDI
    #: binding; a NeXus value has a path instead, and the emitter picks
    #: the physical-mapping subclass from which of the two it finds.
    index: int | None = None
    width: tuple[int, int] | None = None

    @property
    def has_value(self) -> bool:
        return self.value is not None


@dataclass
class ConceptRecord:
    """One NXentry expressed as concept values."""

    entry_name: str
    entry_path: str
    definition: str | None = None
    values: dict[str, list[ConceptValue]] = field(default_factory=dict)
    #: Values bound to a **schema.org property** rather than to a concept,
    #: keyed by the property CURIE. Held apart from `values` on purpose:
    #: this record is keyed on concepts, and `schema:author` is not one --
    #: it is a serialization target. Folding them together would put a
    #: property URI everywhere the code means "a CDIF XAS concept", which
    #: is the fusion of concept with binding this module exists to avoid.
    #:
    #: Populated only by the XDI binding, from `xdi-to-cdif.sssom.tsv`.
    #: A NeXus file carries no bibliographic headers.
    bibliographic: dict[str, ConceptValue] = field(default_factory=dict)
    #: Raw `Publication.*` headers other than the DOI, in file order.
    #:
    #: Held as text rather than mapped because XDI does not say what they
    #: are about: `Publication.authors` may name the authors of the
    #: dataset or the authors of a paper derived from it, and the two
    #: license different statements. Until that is settled they are
    #: carried as prose, which asserts nothing.
    publication_fields: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Header values this reader replaced rather than read -- see
    #: map/normalise.py. Kept apart from `warnings` because a warning
    #: says something may be wrong, whereas these say the emitted value
    #: is deliberately not the one in the file, which a consumer needs
    #: even when nothing is wrong at all.
    conversion_notes: list[str] = field(default_factory=list)

    def add(self, cv: ConceptValue) -> None:
        self.values.setdefault(cv.concept, []).append(cv)

    def first(self, concept: str) -> ConceptValue | None:
        got = self.values.get(concept)
        return got[0] if got else None

    def value_of(self, concept: str) -> Any:
        cv = self.first(concept)
        return cv.value if cv else None

    @property
    def concepts(self) -> set[str]:
        return set(self.values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_name": self.entry_name,
            "entry_path": self.entry_path,
            "definition": self.definition,
            "values": {
                concept: [
                    {
                        "value": cv.value,
                        "units": cv.units,
                        "source_path": cv.source_path,
                        "predicate": cv.predicate,
                        "confidence": cv.confidence,
                        "is_array": cv.is_array,
                        "shape": list(cv.shape),
                        "dtype": cv.dtype,
                        **({"note": cv.note} if cv.note else {}),
                        **({"convention": cv.convention}
                           if cv.convention else {}),
                    }
                    for cv in vals
                ]
                for concept, vals in sorted(self.values.items())
            },
            **({"publication_fields": dict(self.publication_fields)}
               if self.publication_fields else {}),
            **({"bibliographic": {
                prop: {
                    "value": cv.value,
                    "source_path": cv.source_path,
                    "predicate": cv.predicate,
                    "confidence": cv.confidence,
                    **({"note": cv.note} if cv.note else {}),
                }
                for prop, cv in sorted(self.bibliographic.items())
            }} if self.bibliographic else {}),
            "warnings": self.warnings,
            "conversion_notes": self.conversion_notes,
        }


@dataclass
class MappingResult:
    """Every entry in a file, mapped."""

    records: list[ConceptRecord] = field(default_factory=list)
    crosswalk_source: str = ""
    #: Why this crosswalk was chosen. A silent choice between
    #: vocabularies cannot be debugged from the output alone.
    crosswalk_reason: str = ""
    legacy_source: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_multi_entry(self) -> bool:
        return len(self.records) > 1

    def structural_signature(self, record: ConceptRecord) -> tuple:
        """Key identifying entries that share a data structure.

        Entries in a scan series differ in title and timestamps but share
        their layout, so the emitter writes one `cdi:DataStructure` and has
        every matching part reference it. Two entries share a structure
        when the same concepts are present as arrays with the same shapes
        and dtypes.
        """
        return tuple(sorted(
            (concept, cv.shape, cv.dtype)
            for concept, vals in record.values.items()
            for cv in vals
            if cv.is_array
        ))

    def structure_groups(self) -> dict[tuple, list[ConceptRecord]]:
        """Records grouped by structural signature."""
        out: dict[tuple, list[ConceptRecord]] = {}
        for r in self.records:
            out.setdefault(self.structural_signature(r), []).append(r)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "crosswalk_source": self.crosswalk_source,
            "crosswalk_reason": self.crosswalk_reason,
            "legacy_source": self.legacy_source,
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------

def _value_from_field(
    f: NXField, m: Mapping
) -> ConceptValue:
    cv = ConceptValue(
        concept=m.subject_id,
        units=f.units,
        source_path=f.path,
        predicate=m.predicate_id,
        confidence=m.confidence,
        shape=f.shape,
        dtype=f.dtype,
        label=f.name,
        long_name=f.long_name,
        note=m.comment,
    )
    if f.has_value:
        cv.value = f.value
    else:
        # Deliberately not read: a measured array is data, not metadata.
        # Its presence and shape are still the answer to "is this concept
        # in this file", which is what the data-structure profile needs.
        cv.is_array = True
    return cv


def _value_from_group(g: NXGroup, m: Mapping) -> ConceptValue:
    """A concept mapped to a group rather than a field — an analyzer
    crystal, say. The group's presence is the fact; a name if it has one."""
    return ConceptValue(
        concept=m.subject_id,
        value=g.name or None,
        source_path=g.path,
        predicate=m.predicate_id,
        confidence=m.confidence,
        note=m.comment,
    )


def map_entry(
    entry: NXEntry,
    crosswalk: Crosswalk,
    legacy: LegacyTable | None = None,
) -> ConceptRecord:
    """Express one entry as concept values."""
    record = ConceptRecord(
        entry_name=entry.name,
        entry_path=entry.path,
        definition=entry.definition,
    )

    if not entry.definition:
        record.warnings.append(
            "entry declares no application definition; only base-class "
            "concepts can be mapped, and mode-dependent concepts such as "
            "the meaning of 'intensity' cannot be determined"
        )

    discriminating = crosswalk.discriminating_classes()
    applicable = crosswalk.for_definition(entry.definition)
    if not applicable:
        # Not a reason to stop: a file declaring a family base has no
        # mappings of its own, and everything it does carry comes from
        # the fallback below.
        record.warnings.append(
            f"crosswalk has no mappings for definition "
            f"{entry.definition!r}"
        )

    for m in applicable:
        if not m.is_identifying:
            # narrowMatch/relatedMatch describe a relationship, not a
            # value location -- e.g. detection mode narrowMatching the
            # application definition itself. Handled separately below.
            continue
        if not m.path:
            continue
        for hit in resolve_mapping(entry, m, discriminating):
            if isinstance(hit, NXField):
                record.add(_value_from_field(hit, m))
            elif isinstance(hit, NXGroup):
                record.add(_value_from_group(hit, m))

    _try_sibling_definitions(record, entry, crosswalk)
    _apply_legacy(record, entry, legacy)

    still_missing = {
        m.subject_label or m.subject_id
        for m in applicable
        if m.is_identifying and m.path and m.subject_id not in record.values
    }
    if still_missing:
        # Normal, not an error: a file need not carry every concept its
        # definition allows.
        record.warnings.append(
            f"{len(still_missing)} mapped concept(s) not found in this "
            f"entry: " + ", ".join(sorted(still_missing)[:12])
            + (" ..." if len(still_missing) > 12 else "")
        )

    _add_definition_derived(record, entry, crosswalk)
    return record


def _ambiguous_paths(crosswalk: Crosswalk) -> set[str]:
    """Definition-relative paths that mean different concepts in
    different definitions.

    ``/ENTRY/intensity`` is the prime case: the absorption coefficient in
    NXxas_trans, the fluorescence-derived one in NXxas_tfy, the
    electron-yield one in NXxas_tey. Which concept it is, is knowable only
    from the declared definition — so it is exactly the path that must not
    be guessed at.
    """
    by_path: dict[str, set[str]] = {}
    for m in crosswalk.mappings:
        if m.is_identifying and m.path:
            by_path.setdefault(m.path, set()).add(m.subject_id)
    return {p for p, concepts in by_path.items() if len(concepts) > 1}


def _apply_legacy(
    record: ConceptRecord, entry: NXEntry, legacy: LegacyTable | None
) -> None:
    """Fill what remains from where non-standard writers actually put it.

    Last resort by construction: only concepts still missing after the
    crosswalk and its same-family fallback are considered, so a
    standards-based value is never displaced. See `map.legacy` for why
    this is a separate table from the SSSOM crosswalk, and for what the
    never-override rule costs.
    """
    if legacy is None or not legacy.paths:
        return
    filled: list[str] = []
    conventions: set[str] = set()
    for lp in legacy.paths:
        if lp.concept in record.values:
            continue
        for hit in resolve_legacy(entry, lp):
            cv = ConceptValue(
                concept=lp.concept,
                units=getattr(hit, "units", None),
                source_path=hit.path,
                predicate="skos:exactMatch",
                confidence=lp.confidence,
                note=lp.comment,
                convention=lp.convention,
            )
            if isinstance(hit, NXField):
                if hit.has_value:
                    cv.value = hit.value
                else:
                    cv.is_array = True
                cv.shape, cv.dtype = hit.shape, hit.dtype
                cv.long_name = hit.long_name
            else:
                cv.value = hit.name or None
            record.add(cv)
            filled.append(f"{lp.concept.split(':')[-1]} <- {lp.path}")
            conventions.add(lp.convention)
    if filled:
        record.warnings.append(
            f"{len(filled)} concept(s) filled from non-standard paths "
            f"({', '.join(sorted(conventions))}): " + ", ".join(sorted(filled))
        )


def _try_sibling_definitions(
    record: ConceptRecord, entry: NXEntry, crosswalk: Crosswalk
) -> None:
    """Fill remaining concepts from more specific definitions in the same
    family.

    A file may declare the family base — `definition=NXxas` — while its
    structure is that of one specific mode, either because it predates the
    restructuring that split the modes out, or because the writer declared
    the general case. Its monochromator d-spacing is genuinely present at
    the path `NXxas_trans` gives, and refusing to look there loses a real
    value over a bookkeeping detail.

    Two constraints keep this from becoming guesswork. Only definitions
    whose names extend the declared one are consulted, so a declared
    technique is never read as an unrelated one. And any path that names
    different concepts in different definitions is skipped outright — the
    fallback fills concepts whose location is unambiguous, and stays
    silent about the ones only the declaration could disambiguate.
    """
    declared = entry.definition
    if declared:
        ambiguous = _ambiguous_paths(crosswalk)
        borrowed: list[str] = []
        for m in crosswalk.mappings:
            if not (m.is_identifying and m.path and m.definition):
                continue
            if m.definition == declared or not m.definition.startswith(declared):
                continue
            if m.subject_id in record.values or m.path in ambiguous:
                continue
            hits = resolve_mapping(entry, m, crosswalk.discriminating_classes())
            for hit in hits:
                cv = (
                    _value_from_field(hit, m)
                    if isinstance(hit, NXField)
                    else _value_from_group(hit, m)
                )
                cv.note = "; ".join(
                    x for x in (
                        f"path from {m.definition}, which the entry does "
                        f"not declare",
                        m.comment,
                    ) if x
                )
                record.add(cv)
            if hits:
                borrowed.append(f"{m.subject_label or m.subject_id} "
                                f"<- {m.definition}")
        if borrowed:
            record.warnings.append(
                f"entry declares {declared}; {len(borrowed)} concept(s) "
                f"matched paths from a more specific definition in the same "
                f"family: " + ", ".join(sorted(borrowed))
            )


def _add_definition_derived(
    record: ConceptRecord, entry: NXEntry, crosswalk: Crosswalk
) -> None:
    """Concepts whose value IS the application definition.

    In the restructured NXxas family the detection mode is not a field —
    it is which definition the file declares. Those rows carry
    `skos:narrowMatch` against the definition with an empty path, so
    resolving them means reading `definition` rather than a field.
    """
    if not entry.definition:
        return
    for m in crosswalk.mappings:
        if m.path or m.definition != entry.definition:
            continue
        if m.predicate_id != "skos:narrowMatch":
            continue
        record.add(
            ConceptValue(
                concept=m.subject_id,
                value=entry.definition,
                source_path=f"{entry.path}/definition",
                predicate=m.predicate_id,
                confidence=m.confidence,
                note=m.comment,
            )
        )


def map_nexus(
    nexus: NeXusResult,
    crosswalk: Crosswalk | None = None,
    legacy: LegacyTable | None = None,
) -> MappingResult:
    """Express every entry in a file as concept values."""
    if crosswalk is not None:
        cw, why = crosswalk, "supplied by the caller"
    else:
        cw, why = select_crosswalk(nexus.definitions)
    lg = legacy if legacy is not None else load_legacy()
    out = MappingResult(
        crosswalk_source=cw.source,
        crosswalk_reason=why,
        legacy_source=lg.source,
    )

    if not cw.mappings:
        out.warnings.append(
            f"crosswalk at {cw.source} is empty; no concepts can be mapped"
        )
        return out

    if not nexus.is_nexus:
        out.warnings.append("file carries no NeXus markers; nothing to map")
        return out

    if why.startswith("no bundled crosswalk") or why.startswith("the file"):
        out.warnings.append(why)

    for entry in nexus.entries:
        out.records.append(map_entry(entry, cw, lg))

    if out.is_multi_entry:
        groups = out.structure_groups()
        out.warnings.append(
            f"{len(out.records)} entries resolve to {len(groups)} distinct "
            f"data structure(s); these become parts of one "
            f"dataset sharing structure by reference"
        )
    return out
