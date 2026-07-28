"""SSSOM crosswalk loading and NXDL-path matching.

Stage 2 begins here. This module holds the correspondence between NeXus
concept paths and CDIF concept URIs, and the machinery to decide which
groups and fields in a real file a given NXDL path refers to.

Why matching is not string comparison
--------------------------------------

An SSSOM object looks like::

    nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/i0:NXdetector/data

while the file it must match looks like::

    /FeFoil.001/instrument/i0/data

The NXDL form names *classes* and uses uppercase placeholders where any
instance will do; the file names *instances*. So matching walks the
NeXus tree comparing `NX_class` per segment, and only compares names
where the NXDL segment gives a literal one.

This is also why the mapper is robust to the definitions being revised.
A path written against one revision keeps matching as long as the class
structure holds, and when it genuinely stops matching the concept simply
goes unfilled — with a warning — rather than producing a wrong value.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from hdf5metadata.inspect.nexus import NXEntry, NXField, NXGroup

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CROSSWALK = DATA_DIR / "cdifxas-to-nexus.sssom.tsv"

#: Upstream, for `--refresh` and for the provenance note in data/README.md.
UPSTREAM = (
    "https://raw.githubusercontent.com/smrgeoinfo/XAS-CDIF/"
    "cdifxasRelease/crosswalk/cdifxas-to-nexus.sssom.tsv"
)

#: Predicates we treat as "this concept is this field". closeMatch is
#: included because a close match still identifies the right field; the
#: predicate travels with the value so a consumer can weigh it.
IDENTIFYING = {"skos:exactMatch", "skos:closeMatch"}

_CURIE = re.compile(r"^(?P<prefix>[A-Za-z][\w.-]*):(?P<local>.+)$")


@dataclass
class Mapping:
    """One SSSOM row."""

    subject_id: str
    subject_label: str
    predicate_id: str
    object_id: str
    object_label: str
    confidence: float = 1.0
    comment: str = ""

    @property
    def concept(self) -> str:
        """Subject as a CURIE — the CDIF concept."""
        return self.subject_id

    @property
    def definition(self) -> str | None:
        """NXDL definition named by the object, e.g. ``NXxas_trans``."""
        m = _CURIE.match(self.object_id)
        local = m.group("local") if m else self.object_id
        return local.split("/", 1)[0] or None

    @property
    def path(self) -> str:
        """Path within the definition, possibly empty for a
        definition-level mapping such as a detection mode."""
        m = _CURIE.match(self.object_id)
        local = m.group("local") if m else self.object_id
        parts = local.split("/", 1)
        return "/" + parts[1] if len(parts) > 1 else ""

    @property
    def is_identifying(self) -> bool:
        return self.predicate_id in IDENTIFYING

    @property
    def is_base_class(self) -> bool:
        """Whether the object names a path inside a base class rather than
        inside an application definition.

        Decided structurally rather than by name. An application-definition
        path is rooted at the entry and so leads with an ``NXentry``
        segment; a base-class path is relative to an instance of the class
        and does not. Testing the name instead — ``startswith("NXxas")`` —
        silently classes every non-XAS definition as a base class, so an
        ``NXsas`` path would be applied to an XAS file.
        """
        p = self.path.lstrip("/")
        return bool(p) and not p.split("/", 1)[0].endswith(":NXentry")


@dataclass
class Crosswalk:
    """An SSSOM mapping set, indexed for lookup by definition."""

    mappings: list[Mapping] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    source: str = ""
    _discriminating: frozenset[str] | None = None

    def for_definition(self, name: str | None) -> list[Mapping]:
        """Mappings whose object is in the named definition.

        Base-class mappings (``NXsource``, ``NXsample``, ``NXinstrument``…)
        always apply, because a concept that is a property of a kind of
        thing holds wherever that thing appears — the distinction the
        gap analysis turned on.
        """
        out = [m for m in self.mappings if m.is_base_class]
        if name:
            out += [
                m for m in self.mappings
                if m.definition == name and not m.is_base_class
            ]
        return out

    def discriminating_classes(self) -> frozenset[str]:
        """Classes whose instances the crosswalk tells apart by name.

        `NXdetector` appears in the XAS definitions as `i0`, `itrans`,
        `ifluor`, `iey` and `irefer` -- five different concepts, one
        class, distinguished only by name. `NXsource` appears once. So
        the crosswalk itself says which classes carry a meaningful name,
        and there is no need to hardcode a list that would go stale the
        moment a definition adds a detector.

        This matters because of the forgiveness rule in `_match_groups`:
        a missing literal name falls back to the class when the class has
        one instance. For a lone `NXsource` called `synchrotron` that is
        right. For a file with only `i0` in it, it would resolve a
        missing `itrans` to `i0` and report the incident beam as the
        transmitted beam -- a wrong scientific claim, not a near miss.
        """
        if self._discriminating is None:
            names: dict[str, set[str]] = {}
            for m in self.mappings:
                for seg in parse_path(m.path):
                    if seg.nx_class and not seg.is_placeholder and seg.name:
                        names.setdefault(seg.nx_class, set()).add(
                            seg.name.lower())
            self._discriminating = frozenset(
                cls for cls, seen in names.items() if len(seen) > 1)
        return self._discriminating

    def concepts(self) -> set[str]:
        return {m.subject_id for m in self.mappings}

    def definitions(self) -> set[str]:
        return {m.definition for m in self.mappings if m.definition}


def load_crosswalk(path: str | Path | None = None) -> Crosswalk:
    """Load an SSSOM TSV. The YAML metadata block is carried as comment
    lines and is preserved for provenance."""
    p = Path(path) if path else DEFAULT_CROSSWALK
    cw = Crosswalk(source=str(p))
    if not p.is_file():
        return cw

    lines = p.read_text(encoding="utf-8").splitlines()
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in lines:
        if line.startswith("#"):
            stripped = line[1:].strip()
            if ":" in stripped and not stripped.startswith("-"):
                k, _, v = stripped.partition(":")
                if v.strip():
                    meta.setdefault(k.strip(), v.strip())
        else:
            body.append(line)
    cw.metadata = meta

    for row in csv.DictReader(body, delimiter="\t"):
        try:
            confidence = float(row.get("confidence") or 1.0)
        except ValueError:
            confidence = 1.0
        cw.mappings.append(
            Mapping(
                subject_id=(row.get("subject_id") or "").strip(),
                subject_label=(row.get("subject_label") or "").strip(),
                predicate_id=(row.get("predicate_id") or "").strip(),
                object_id=(row.get("object_id") or "").strip(),
                object_label=(row.get("object_label") or "").strip(),
                confidence=confidence,
                comment=(row.get("comment") or "").strip(),
            )
        )
    return cw


# ---------------------------------------------------------------------------
# NXDL path matching
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """One step of an NXDL path."""

    name: str | None
    nx_class: str | None

    @property
    def is_placeholder(self) -> bool:
        """Uppercase names are NXDL's convention for 'any instance of
        this class'. ``ENTRY:NXentry`` matches any entry; ``i0:NXdetector``
        names one."""
        if not self.name:
            return True
        stripped = self.name.replace("_", "")
        return stripped.isupper()


def parse_path(path: str) -> list[Segment]:
    segments: list[Segment] = []
    for raw in path.strip("/").split("/"):
        if not raw:
            continue
        if ":" in raw:
            name, _, cls = raw.partition(":")
            segments.append(Segment(name=name or None, nx_class=cls or None))
        else:
            segments.append(Segment(name=raw, nx_class=None))
    return segments


def _match_groups(
    groups: Iterable[NXGroup],
    seg: Segment,
    discriminating: frozenset[str] = frozenset(),
) -> list[NXGroup]:
    if seg.nx_class:
        candidates = [g for g in groups if g.nx_class == seg.nx_class]
    else:
        candidates = list(groups)
    if not candidates:
        return []
    if not seg.is_placeholder and seg.name:
        named = [
            g for g in candidates if (g.name or "").lower() == seg.name.lower()
        ]
        if named:
            return named
        # The literal name is absent. Where the class is one the
        # crosswalk distinguishes by name -- NXdetector, whose instances
        # are i0, itrans, ifluor, iey -- the name is the whole content of
        # the match and its absence is a miss, however few candidates
        # there are. Reporting the incident-beam monitor as the
        # transmitted beam is a wrong claim, not a near miss.
        if seg.nx_class in discriminating:
            return []
        # Otherwise the name is only a label: a lone NXsource is the
        # source whatever it is called.
        return candidates if len(candidates) == 1 else []
    return candidates


def resolve_segments(
    roots: list[NXGroup],
    segments: list[Segment],
    discriminating: frozenset[str] = frozenset(),
) -> list[NXField | NXGroup]:
    if not segments:
        return list(roots)
    current = list(roots)
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if last and seg.nx_class is None:
            out: list[NXField | NXGroup] = []
            for g in current:
                f = g.fields.get(seg.name or "")
                if f is not None:
                    out.append(f)
            return out
        nxt: list[NXGroup] = []
        for g in current:
            nxt.extend(_match_groups(g.groups, seg, discriminating))
        if not nxt:
            return []
        current = nxt
    return list(current)


def resolve_path(entry: NXEntry, path: str) -> list[NXField | NXGroup]:
    """Everything in ``entry`` that an entry-relative NXDL path refers to.

    Returns fields when the path ends at a field, groups when it ends at
    a group, and an empty list when nothing matches — which callers treat
    as "concept not present in this file", not as an error.
    """
    if entry.root is None:
        return []
    segments = parse_path(path)
    if segments and segments[0].nx_class == "NXentry":
        segments = segments[1:]
    return resolve_segments([entry.root], segments)


def resolve_mapping(
    entry: NXEntry,
    mapping: Mapping,
    discriminating: frozenset[str] = frozenset(),
) -> list[NXField | NXGroup]:
    """Everything in ``entry`` that a crosswalk row's object refers to.

    Two path kinds have to be told apart, and conflating them is why an
    earlier version resolved nothing:

    **Application-definition paths** are entry-relative and lead with an
    ``NXentry`` segment —
    ``nxdl:NXxas_trans/ENTRY:NXentry/INSTRUMENT:NXinstrument/i0:NXdetector/data``.

    **Base-class paths** are relative to an instance of that class —
    ``nxdl:NXsource/name`` means *the* ``name`` field of *any*
    ``NXsource``, wherever one appears. Resolving it entry-relative
    looks for a ``name`` field directly under ``NXentry`` and finds
    nothing.

    The distinction matters beyond mechanics: base-class mappings are how
    a concept that is a property of a kind of thing (a facility name, a
    sample temperature) applies wherever that thing occurs, independent
    of technique. That is the finding the gap analysis turned on.
    """
    if entry.root is None:
        return []
    segments = parse_path(mapping.path)

    if segments and segments[0].nx_class == "NXentry":
        return resolve_segments([entry.root], segments[1:], discriminating)

    definition = mapping.definition
    if definition and definition.startswith("NX"):
        holders = entry.find(definition)
        if holders:
            return resolve_segments(holders, segments, discriminating)
        # The class is absent from this file: the concept simply is not
        # present, which is a normal outcome.
        return []

    return resolve_segments([entry.root], segments, discriminating)


def _refresh(dest: Path = DEFAULT_CROSSWALK) -> int:
    """Re-download the bundled crosswalk from upstream."""
    import urllib.request

    try:
        with urllib.request.urlopen(UPSTREAM, timeout=30) as r:
            text = r.read().decode("utf-8")
    except Exception as e:
        print(f"refresh failed: {type(e).__name__}: {e}")
        return 1
    dest.write_text(text, encoding="utf-8")
    cw = load_crosswalk(dest)
    print(f"refreshed {dest.name}: {len(cw.mappings)} mappings")
    return 0


if __name__ == "__main__":
    import sys

    if "--refresh" in sys.argv:
        raise SystemExit(_refresh())
    cw = load_crosswalk()
    print(f"{cw.source}: {len(cw.mappings)} mappings, "
          f"{len(cw.concepts())} concepts, "
          f"{len(cw.definitions())} definitions")
