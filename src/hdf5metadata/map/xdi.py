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

from pathlib import Path

from hdf5metadata.inspect.xdi import XDIResult
from hdf5metadata.map.concepts import (
    ConceptRecord,
    ConceptValue,
    MappingResult,
)
from hdf5metadata.map.crosswalk import Crosswalk, DATA_DIR, load_crosswalk

DEFAULT_XDI_CROSSWALK = DATA_DIR / "xdi-to-cdifxas.sssom.tsv"

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
        canonical = _LABEL_ALIASES.get(label.lower(), label.lower())
        hit = lookup.get(f"column.{canonical}")
        if hit is None:
            unmapped.append(f"Column.{label}")
            continue
        concept, predicate, confidence, comment = hit
        record.add(ConceptValue(
            concept=concept,
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
