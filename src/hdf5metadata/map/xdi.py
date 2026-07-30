"""XDI headers and columns to CDIF concepts.

The second binding onto the concept hub. Where the NeXus mapper resolves
class-qualified paths through a tree, this one looks keys up in a
dictionary — XDI is flat, so nothing in `map/crosswalk.py` is needed.
Both produce the same `ConceptRecord`, which is what lets stage 3 stay
ignorant of where a value came from.

The crosswalk runs the other way round
--------------------------------------

`cdifxas-to-nexus.sssom.tsv` has CDIF concepts as subjects and NeXus
paths as objects. `xdi-to-cdifxas.sssom.tsv` is the reverse: XDI keys as
subjects, concepts as objects. That is not an inconsistency to paper
over — SSSOM sets are named for their direction, and this one was
published pointing that way. The loader reads the direction rather than
assuming one.

Headers are conditions, columns are measurements
------------------------------------------------

`Sample.temperature` describes the circumstances of a scan;
`Column.energy` names an array that was recorded. The same split the
NeXus mapper gets from `is_array` is available here from which side of
the header-end line a thing was declared on, and it drives the same
downstream layout.
"""
from __future__ import annotations

import re
from pathlib import Path

from hdf5metadata.inspect.xdi import XDIResult
from hdf5metadata.map.concepts import (
    ConceptRecord,
    ConceptValue,
    MappingResult,
)
from hdf5metadata.emit import OGC_NIL_MISSING
from hdf5metadata.map.crosswalk import Crosswalk, DATA_DIR, load_crosswalk

DEFAULT_XDI_CROSSWALK = DATA_DIR / "xdi-to-cdifxas.sssom.tsv"

#: Two concepts the XAS profile requires that no XDI header carries.
#:
#: Neither appears as a field in any of the 272 files in the XAS Data
#: Library, so a crosswalk row for either would map something that does
#: not exist. They are derived instead, and each derived value says in
#: its note where it came from -- the alternative, leaving them out, is a
#: document that cannot satisfy the profile over information the file
#: does in fact determine.
#:
#: The probe follows from the format. XDI is the X-ray Absorption Data
#: Interchange format; it describes nothing but X-ray absorption.
XDI_PROBE = "x-ray"

#: Units the XDI dictionary specifies for a tag, where a file states the
#: value and not the unit. Supplying these is reading the specification,
#: not guessing: the dictionary fixes the unit for the tag, so a file
#: that omits it is not ambiguous, merely terse.
_DICTIONARY_UNITS = {
    "mono.d_spacing": "Angstrom",
}

#: "Si(111)" -- the crystal material and the reflection in one string.
#: The crosswalk already records that Mono.name conflates the two, which
#: is why it maps at closeMatch rather than exactMatch; splitting it
#: recovers both without asserting anything the file does not contain.
_MONO_NAME = re.compile(
    r"^\s*\[?\s*'?\s*"
    r"(?P<material>[A-Za-z][A-Za-z0-9]*)"
    r"\s*[\(\[]?\s*"
    r"(?P<reflection>\d\s*\d\s*\d|\d+(?:\s+\d+){2})"
    r"\s*[\)\]]?\s*'?\s*\]?\s*$"
)

#: The detection mode follows from which intensities were recorded. This
#: is the same inference every XDI reader makes to plot a spectrum:
#: a transmitted-beam column means transmission, a fluorescence column
#: means fluorescence. Ordered, because a file carrying both is a
#: transmission measurement with fluorescence recorded alongside.
_MODE_BY_CONCEPT = (
    ("cdifxas:transmittedintensity", "Transmission"),
    ("cdifxas:absorptioncoefficient", "Transmission"),
    ("cdifxas:fluorescenceintensity", "Fluorescence"),
    ("cdifxas:fluorescenceabsorptioncoefficient", "Fluorescence"),
    ("cdifxas:electronyieldintensity", "Electron Yield"),
)

#: Column labels a file may use for the same quantity. The crosswalk
#: names the canonical ones; these are spellings seen in real files that
#: mean the same thing.
_LABEL_ALIASES = {
    "i1": "itrans",
    "i2": "irefer",
    "iref": "irefer",
    "ifluo": "ifluor",
    "mu": "mutrans",
    "muref": "murefer",
    "e": "energy",
}


def load_xdi_crosswalk(path: str | Path | None = None) -> Crosswalk:
    """Load the XDI crosswalk, which is published subject-XDI."""
    return load_crosswalk(path or DEFAULT_XDI_CROSSWALK)


def _index(crosswalk: Crosswalk) -> dict[str, tuple[str, str, float, str]]:
    """XDI key (lower-cased) -> (concept, predicate, confidence, comment).

    Reads the direction off the row rather than assuming it: whichever of
    subject and object carries the `xdi:` prefix is the key, and the
    other is the concept. A set published either way round then works.
    """
    out: dict[str, tuple[str, str, float, str]] = {}
    for m in crosswalk.mappings:
        subject, obj = m.subject_id, m.object_id
        if subject.startswith("xdi:"):
            key, concept = subject, obj
        elif obj.startswith("xdi:"):
            key, concept = obj, subject
        else:
            continue
        out[key.split(":", 1)[1].lower()] = (
            concept, m.predicate_id, m.confidence, m.comment,
        )
    return out


def _derive(record: ConceptRecord, xdi: XDIResult) -> None:
    """Add the concepts the file determines but does not state.

    Kept apart from the crosswalk loop on purpose: a crosswalk row says
    "this header means this concept", and neither of these has a header.
    Writing them as rows would put fields in the mapping set that no XDI
    file has ever contained.
    """
    version_line = f"#XDI/{xdi.xdi_version}" if xdi.xdi_version else "#XDI"

    if "cdifxas:probe" not in record.values:
        record.add(ConceptValue(
            concept="cdifxas:probe",
            value=XDI_PROBE,
            source_path=version_line,
            confidence=1.0,
            note=(
                "implied by the format: XDI describes X-ray absorption "
                "only, and no XDI header carries the probe"
            ),
        ))

    mono = record.first("cdifxas:monochromatortype")
    if mono and mono.value and "cdifxas:reflectionplane" not in record.values:
        m = _MONO_NAME.match(str(mono.value))
        if m:
            digits = m.group("reflection").split() or list(
                m.group("reflection"))
            if len(digits) == 1:
                digits = list(digits[0])
            record.add(ConceptValue(
                concept="cdifxas:reflectionplane",
                value=" ".join(digits),
                source_path=mono.source_path,
                confidence=0.9,
                note=(
                    f"read out of Mono.name ({mono.value!r}), which XDI "
                    f"uses for the crystal material and the reflection "
                    f"together"
                ),
            ))
            mono.value = m.group("material")
            mono.note = (
                (mono.note + "; " if mono.note else "")
                + "reflection split out into reflectionplane"
            )

    if "cdifxas:xasmeasurementmode" not in record.values:
        for concept, mode in _MODE_BY_CONCEPT:
            if concept in record.values:
                source = record.first(concept).source_path
                record.add(ConceptValue(
                    concept="cdifxas:xasmeasurementmode",
                    value=mode,
                    source_path=source,
                    confidence=0.9,
                    note=(
                        f"derived from the presence of "
                        f"{concept.split(':')[-1]}; XDI has no detection "
                        f"mode field"
                    ),
                ))
                break


def map_xdi(
    xdi: XDIResult,
    crosswalk: Crosswalk | None = None,
) -> MappingResult:
    """Express an XDI file as concept values."""
    cw = crosswalk or load_xdi_crosswalk()
    out = MappingResult(
        crosswalk_source=cw.source,
        crosswalk_reason="XDI binding; the file declares its format on line 1",
    )

    if not cw.mappings:
        out.warnings.append(
            f"crosswalk at {cw.source} is empty; no concepts can be mapped")
        return out
    if not xdi.is_xdi:
        out.warnings.append("file is not XDI; nothing to map")
        return out

    lookup = _index(cw)
    entry = xdi.entries[0] if xdi.entries else None
    record = ConceptRecord(
        entry_name=entry.name if entry else "spectrum",
        entry_path="/",
        definition=None,
    )

    # -- headers: the conditions the scan was made under --------------
    unmapped: list[str] = []
    for key, value in sorted(xdi.headers.items()):
        head, _, _tag = key.partition(".")
        if head.lower() == "column":
            continue                    # handled with the arrays below
        hit = lookup.get(key.lower())
        if hit is None:
            unmapped.append(key)
            continue
        concept, predicate, confidence, comment = hit
        record.add(ConceptValue(
            concept=concept,
            value=value,
            units=_DICTIONARY_UNITS.get(key.lower()),
            source_path=f"#{key}",
            predicate=predicate,
            confidence=confidence,
            note=comment,
        ))

    # -- columns: what was actually measured ---------------------------
    #
    # Recorded as arrays without reading them, exactly as the NeXus
    # mapper treats a dataset it declines to load: the shape is what the
    # data-structure profile needs, and the numbers are data.
    for position, label in enumerate(xdi.labels, start=1):
        # The label may have carried a unit -- `Column.1: energy eV`.
        # It is the file's own statement about the column, so it goes on
        # the value as units, the same as a NeXus `units` attribute.
        units = xdi.column_units.get(position)
        width = xdi.column_widths.get(position)
        canonical = _LABEL_ALIASES.get(label.lower(), label.lower())
        hit = lookup.get(f"column.{canonical}")
        if hit is None:
            # Still recorded. A column that was measured belongs in the
            # data description whether or not anyone has named its
            # concept -- dropping it loses the fact that the file has
            # seven columns, which is exactly what the data-structure
            # profile is for. The concept is the OGC nil URI, so a
            # consumer can see the measurement exists and that nothing
            # is claimed about what it means.
            unmapped.append(f"Column.{label}")
            record.add(ConceptValue(
                concept=OGC_NIL_MISSING,
                units=units,
                index=position,
                width=width,
                source_path=f"#column:{position}",
                confidence=0.0,
                is_array=True,
                shape=(xdi.row_count,),
                dtype="float64",
                label=label,
                long_name=label,
                note=(
                    f"column {label!r} was recorded in the file; no "
                    f"crosswalk row names its concept"
                ),
            ))
            continue
        concept, predicate, confidence, comment = hit
        record.add(ConceptValue(
            concept=concept,
            units=units,
            index=position,
            width=width,
            source_path=f"#column:{position}",
            predicate=predicate,
            confidence=confidence,
            is_array=True,
            shape=(xdi.row_count,),
            dtype="float64",
            label=label,
            long_name=label,
            note=comment,
        ))

    _derive(record, xdi)

    if unmapped:
        # Not an error: XDI lets a file define its own namespaces, and a
        # crosswalk covering every extension anyone has invented is not a
        # thing that can exist. Saying which were skipped is what lets
        # someone decide whether one deserves a row.
        record.warnings.append(
            f"{len(unmapped)} header(s)/column(s) had no crosswalk entry: "
            + ", ".join(sorted(unmapped)[:12])
            + (" ..." if len(unmapped) > 12 else "")
        )

    out.records.append(record)
    out.warnings.extend(xdi.warnings)
    return out
