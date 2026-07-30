"""Stage 2: NeXus semantics to CDIF concepts.

All the semantics live here. `crosswalk` holds the SSSOM correspondence
and the NXDL-path matching; `legacy` records where non-standard writers
actually put things; `concepts` produces the concept-keyed intermediate
that is the hub of the architecture -- see README.md.
"""

from cdifnexmetadata.map.concepts import (
    ConceptRecord,
    ConceptValue,
    MappingResult,
    map_entry,
    map_nexus,
)
from cdifnexmetadata.map.crosswalk import (
    Crosswalk,
    Mapping,
    Segment,
    load_crosswalk,
    parse_path,
    resolve_path,
)
from cdifnexmetadata.map.legacy import LegacyPath, LegacyTable, load_legacy

__all__ = [
    "ConceptRecord",
    "ConceptValue",
    "Crosswalk",
    "LegacyPath",
    "LegacyTable",
    "Mapping",
    "MappingResult",
    "Segment",
    "load_crosswalk",
    "load_legacy",
    "map_entry",
    "map_nexus",
    "parse_path",
    "resolve_path",
]
