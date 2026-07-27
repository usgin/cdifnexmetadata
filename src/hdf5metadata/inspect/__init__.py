"""Stage 1: structural inspection.

Modules here report what is *in* a file. They carry no CDIF vocabulary
and no NeXus semantics beyond what `nexus` explicitly layers on top --
see the pipeline note in DESIGN.md.
"""

from hdf5metadata.inspect.hdf5 import (
    Dataset,
    Group,
    HDF5Inspector,
    InspectionResult,
    inspect_file,
)

__all__ = [
    "Dataset",
    "Group",
    "HDF5Inspector",
    "InspectionResult",
    "inspect_file",
]
