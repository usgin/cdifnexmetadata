"""Stage 2: NeXus semantics to CDIF concepts.

All the semantics live here. `crosswalk` holds the SSSOM correspondence
and the NXDL-path matching; `concepts` produces the concept-keyed
intermediate that is the hub of the architecture -- see DESIGN.md.
"""

from hdf5metadata.map.concepts import (
    ConceptRecord,
    ConceptValue,
    MappingResult,
    map_entry,
    map_nexus,
)
from hdf5metadata.map.crosswalk import (
    Crosswalk,
    Mapping,
    Segment,
    load_crosswalk,
    parse_path,
    resolve_path,
)

__all__ = [
    "ConceptRecord",
    "ConceptValue",
    "Crosswalk",
    "Mapping",
    "MappingResult",
    "Segment",
    "load_crosswalk",
    "map_entry",
    "map_nexus",
    "parse_path",
    "resolve_path",
]
