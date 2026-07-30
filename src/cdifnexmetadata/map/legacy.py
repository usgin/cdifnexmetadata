"""Where non-standard writers actually put things.

A file can declare a NeXus application definition and still be laid out
by a writer that predates it. FeXAS.nxs declares ``NXxas`` and carries
its element and edge at ``scan:NXscan/xrayedge:NXxrayedge/{element,edge}``
-- neither ``NXscan`` nor ``NXxrayedge`` is a NeXus base class. The
crosswalk correctly finds nothing there, and the values are lost unless
something else knows to look.

Why this is a separate table
----------------------------

The SSSOM crosswalk states what a CDIF concept corresponds to *in the
standard*. This table states where a particular writer *actually put*
the value. Those are different kinds of claim: the first is an alignment
between vocabularies and is worth publishing as such; the second is a
record of local practice that will keep growing as more conventions turn
up. Folding the second into the first would make the crosswalk a
quirks list and stop it being citable as an alignment.

Rank, not override
------------------

Legacy paths are consulted only after the crosswalk and its
same-family fallback, and only for concepts still missing. A
standards-based value is never displaced by a legacy one, whatever the
confidences say -- so adding a convention here can fill gaps but cannot
change an answer the standard already gave. That is what makes the table
safe to extend without re-testing everything that came before.

The rule does cost something, and the cost is worth stating. In
FeXAS.nxs ``NXsource/name`` is ``"APS, undulator 36mm, 66 poles,
13-ID-E"`` -- facility, insertion device and beamline concatenated --
while ``facility_name`` alongside it is just ``"APS"``. The standards
path wins and ``facility`` gets the concatenation. That is a question
about whether ``cdifxas:facility`` should map to ``NXsource/name`` at
all, and it belongs in the crosswalk, not in an override switch here.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from cdifnexmetadata.inspect.nexus import NXEntry, NXField, NXGroup
from cdifnexmetadata.map.crosswalk import DATA_DIR, parse_path, resolve_segments

DEFAULT_LEGACY = DATA_DIR / "legacy-paths.tsv"


@dataclass
class LegacyPath:
    """One row: a concept, and where a given writer puts it."""

    concept: str
    convention: str
    path: str
    confidence: float = 1.0
    comment: str = ""


@dataclass
class LegacyTable:
    paths: list[LegacyPath] = field(default_factory=list)
    source: str = ""

    def conventions(self) -> set[str]:
        return {p.convention for p in self.paths}

    def concepts(self) -> set[str]:
        return {p.concept for p in self.paths}


def load_legacy(path: str | Path | None = None) -> LegacyTable:
    """Load the legacy path table. A missing file yields an empty table,
    so the layer is optional rather than required."""
    p = Path(path) if path else DEFAULT_LEGACY
    table = LegacyTable(source=str(p))
    if not p.is_file():
        return table

    body = [
        line for line in p.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    for row in csv.DictReader(body, delimiter="\t"):
        concept = (row.get("concept") or "").strip()
        rowpath = (row.get("path") or "").strip()
        if not concept or not rowpath:
            continue
        try:
            confidence = float(row.get("confidence") or 1.0)
        except ValueError:
            confidence = 1.0
        table.paths.append(
            LegacyPath(
                concept=concept,
                convention=(row.get("convention") or "").strip(),
                path=rowpath,
                confidence=confidence,
                comment=(row.get("comment") or "").strip(),
            )
        )
    return table


def resolve_legacy(
    entry: NXEntry, legacy: LegacyPath
) -> list[NXField | NXGroup]:
    """What a legacy path refers to. Entry-relative always: these paths
    describe a concrete file layout, not a class contract."""
    if entry.root is None:
        return []
    return resolve_segments([entry.root], parse_path(legacy.path))
