"""Stage 1: structural inspection.

`hdf5` reports what is *in* a file, carrying no vocabulary at all.
`nexus` layers NeXus interpretation on that result. Neither knows
anything about CDIF -- see the pipeline note in README.md.
"""

from cdifnexmetadata.inspect.hdf5 import (
    Dataset,
    Group,
    HDF5Inspector,
    InspectionResult,
    inspect_file,
)
from cdifnexmetadata.inspect.nexus import (
    NeXusResult,
    NXAxis,
    NXData,
    NXEntry,
    NXField,
    NXGroup,
    NXSignal,
    is_nexus,
    read_nexus,
    resolve_nxdata,
)

__all__ = [
    "Dataset",
    "Group",
    "HDF5Inspector",
    "InspectionResult",
    "inspect_file",
    "NeXusResult",
    "NXAxis",
    "NXData",
    "NXEntry",
    "NXField",
    "NXGroup",
    "NXSignal",
    "is_nexus",
    "read_nexus",
    "resolve_nxdata",
]
