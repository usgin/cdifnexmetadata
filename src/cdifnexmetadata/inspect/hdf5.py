"""Generic HDF5 structural inspection.

Stage 1 of the pipeline. Walks an HDF5 file and reports what is *in* it —
groups, datasets, shapes, dtypes, attributes, links — as plain data
structures. **No NeXus semantics and no CDIF vocabulary appear here.**
That separation is deliberate: this module is testable against any HDF5
file without knowing anything about NeXus, and when CDIF vocabulary
changes nothing in this file moves.

`inspect.nexus` layers `NX_class` interpretation on top of the result
this module produces.

Design notes
------------

**Small values are metadata; large arrays are data.** A scalar string
dataset holding ``"NXxas"`` is metadata we need; a 443-element float
array of measured intensities is not. So dataset *values* are read only
below a size threshold (see ``max_inline_size``), and everything larger
reports shape/dtype/statistics only. Without this the inspector would
either drag whole data arrays into memory or lose the field values that
carry the actual metadata.

**Links are recorded, not followed.** NeXus makes heavy use of HDF5 hard
links — an ``NXdata`` group commonly links to a dataset that physically
lives under ``NXdetector``. ``visititems`` visits each underlying object
exactly once, so a naive walk silently loses the second name. Groups
therefore carry a ``links`` map recording every child link and its
target, which lets a NeXus-aware layer reconstruct the logical tree.

**Nothing raises.** Non-fatal problems accumulate in
``InspectionResult.warnings``. A corrupt dataset in an otherwise readable
file yields a warning and a partial result, not an exception.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, BinaryIO

try:
    import h5py
    HAVE_H5PY = True
except ImportError:  # pragma: no cover - exercised by absence
    HAVE_H5PY = False

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    HAVE_NUMPY = False


#: Datasets with at most this many elements have their values read.
#: Above it only shape, dtype and (optionally) statistics are reported.
DEFAULT_MAX_INLINE_SIZE = 64

#: Statistics are skipped above this element count regardless, to avoid
#: pulling large arrays through memory for a number nobody asked for.
DEFAULT_MAX_STATS_SIZE = 10_000_000


@dataclass
class Dataset:
    """One HDF5 dataset."""

    path: str
    name: str
    shape: tuple[int, ...]
    dtype: str
    ndim: int
    size: int
    attributes: dict[str, Any] = field(default_factory=dict)

    #: Value, present only for datasets at or below ``max_inline_size``.
    #: ``None`` is ambiguous with a genuine null, so use ``has_value``.
    value: Any = None
    has_value: bool = False

    compression: str | None = None
    chunks: tuple[int, ...] | None = None

    min_value: float | None = None
    max_value: float | None = None

    #: True when this dataset is reachable under more than one path.
    #: See the module docstring on links.
    is_link_target: bool = False


@dataclass
class Group:
    """One HDF5 group."""

    path: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)

    #: Names of direct children, in file order.
    children: list[str] = field(default_factory=list)

    #: Child name -> link description, for every child that is a link.
    #: Values are ``{"type": "hard"|"soft"|"external", "target": str,
    #: "filename": str}``. Hard links are only recorded when the target
    #: has another name in the file.
    links: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class InspectionResult:
    """Everything stage 1 knows about a file."""

    filename: str
    source: str | None = None
    file_size: int | None = None

    root_attributes: dict[str, Any] = field(default_factory=dict)
    groups: list[Group] = field(default_factory=list)
    datasets: list[Dataset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- convenience lookups ------------------------------------------------

    def group(self, path: str) -> Group | None:
        return next((g for g in self.groups if g.path == path), None)

    def dataset(self, path: str) -> Dataset | None:
        return next((d for d in self.datasets if d.path == path), None)

    def children_of(self, path: str) -> list[Group | Dataset]:
        """Direct children of a group path, groups and datasets alike."""
        prefix = path.rstrip("/") + "/"
        out: list[Group | Dataset] = []
        for item in (*self.groups, *self.datasets):
            if item.path.startswith(prefix) and "/" not in item.path[len(prefix):]:
                out.append(item)
        return out

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form."""
        return {
            "filename": self.filename,
            "source": self.source,
            "file_size": self.file_size,
            "root_attributes": self.root_attributes,
            "groups": [
                {
                    "path": g.path,
                    "name": g.name,
                    "attributes": g.attributes,
                    "children": g.children,
                    "links": g.links,
                }
                for g in self.groups
            ],
            "datasets": [
                {
                    "path": d.path,
                    "name": d.name,
                    "shape": list(d.shape),
                    "dtype": d.dtype,
                    "ndim": d.ndim,
                    "size": d.size,
                    "attributes": d.attributes,
                    **({"value": d.value} if d.has_value else {}),
                    "compression": d.compression,
                    "chunks": list(d.chunks) if d.chunks else None,
                    "min_value": d.min_value,
                    "max_value": d.max_value,
                    "is_link_target": d.is_link_target,
                }
                for d in self.datasets
            ],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# value coercion
# ---------------------------------------------------------------------------

def _decode(value: Any) -> Any:
    """Coerce an h5py/numpy value into something JSON can hold.

    h5py hands back bytes for variable-length strings, numpy scalars for
    numbers, and ndarrays for everything else. All three need flattening
    before the result can be serialized or compared.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if HAVE_NUMPY:
        if isinstance(value, np.ndarray):
            if value.dtype.kind in ("S", "O", "U"):
                return [_decode(v) for v in value.tolist()]
            return value.tolist()
        if isinstance(value, np.bytes_):
            return value.tobytes().decode("utf-8", errors="replace")
        if isinstance(value, np.str_):
            return str(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, (np.integer, np.floating)):
            item = value.item()
            # JSON has no NaN/Infinity.
            if isinstance(item, float) and not _finite(item):
                return None
            return item

    if isinstance(value, float) and not _finite(value):
        return None
    if isinstance(value, (list, tuple)):
        return [_decode(v) for v in value]
    return value


def _finite(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


def _read_attributes(obj: Any, path: str, warnings: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        keys = list(obj.attrs.keys())
    except Exception as e:
        warnings.append(f"{path}: could not list attributes ({type(e).__name__})")
        return out
    for key in keys:
        try:
            out[key] = _decode(obj.attrs[key])
        except Exception as e:
            warnings.append(
                f"{path}: attribute {key!r} unreadable ({type(e).__name__})"
            )
    return out


# ---------------------------------------------------------------------------
# inspector
# ---------------------------------------------------------------------------

class HDF5Inspector:
    """Walks an HDF5 file and reports its structure.

    Parameters
    ----------
    max_inline_size:
        Datasets with at most this many elements have their values read
        into the result. Raise it if a file carries metadata in larger
        arrays; lower it to keep results small.
    compute_stats:
        Compute min/max for numeric datasets. Off by default because it
        reads the data.
    max_stats_size:
        Skip statistics above this element count even when
        ``compute_stats`` is on.
    """

    def __init__(
        self,
        max_inline_size: int = DEFAULT_MAX_INLINE_SIZE,
        compute_stats: bool = False,
        max_stats_size: int = DEFAULT_MAX_STATS_SIZE,
    ) -> None:
        self.max_inline_size = max_inline_size
        self.compute_stats = compute_stats
        self.max_stats_size = max_stats_size

    # -- entry points -------------------------------------------------------

    def inspect_file(self, path: str | os.PathLike) -> InspectionResult:
        path = os.fspath(path)
        result = InspectionResult(
            filename=os.path.basename(path), source=str(path)
        )
        try:
            result.file_size = os.path.getsize(path)
        except OSError as e:
            result.warnings.append(f"could not stat file ({type(e).__name__})")

        if not HAVE_H5PY:
            result.warnings.append(
                "h5py is not installed; no structure could be read. "
                "Install with: pip install h5py"
            )
            return result

        try:
            with h5py.File(path, "r") as f:
                self._walk(f, result)
        except Exception as e:
            result.warnings.append(
                f"could not open as HDF5: {type(e).__name__}: {e}"
            )
        return result

    def inspect_fileobj(
        self, fileobj: BinaryIO, filename: str = "data.h5"
    ) -> InspectionResult:
        """Inspect an open binary stream. h5py accepts file-like objects."""
        result = InspectionResult(filename=filename)
        if not HAVE_H5PY:
            result.warnings.append("h5py is not installed; no structure read.")
            return result
        try:
            with h5py.File(fileobj, "r") as f:
                self._walk(f, result)
        except Exception as e:
            result.warnings.append(
                f"could not open as HDF5: {type(e).__name__}: {e}"
            )
        return result

    # -- traversal ----------------------------------------------------------

    def _walk(self, f: "h5py.File", result: InspectionResult) -> None:
        result.root_attributes = _read_attributes(f, "/", result.warnings)

        # Root is a group too; callers reasonably expect it present.
        result.groups.append(
            Group(
                path="/",
                name="",
                attributes=result.root_attributes,
                children=list(f.keys()),
                links=self._links_of(f, "/", result.warnings),
            )
        )

        # Track object ids so a dataset reachable by two names is flagged
        # rather than silently reported once. See module docstring.
        seen_ids: dict[Any, str] = {}

        def visit(name: str, obj: Any) -> None:
            path = "/" + name
            try:
                if isinstance(obj, h5py.Group):
                    result.groups.append(
                        Group(
                            path=path,
                            name=name.rsplit("/", 1)[-1],
                            attributes=_read_attributes(
                                obj, path, result.warnings
                            ),
                            children=list(obj.keys()),
                            links=self._links_of(obj, path, result.warnings),
                        )
                    )
                elif isinstance(obj, h5py.Dataset):
                    ds = self._inspect_dataset(obj, path, result.warnings)
                    try:
                        oid = obj.id.get_object_header_version(), obj.ref
                        key = bytes(obj.ref)
                        if key in seen_ids:
                            ds.is_link_target = True
                        else:
                            seen_ids[key] = path
                    except Exception:
                        pass
                    result.datasets.append(ds)
            except Exception as e:
                result.warnings.append(
                    f"{path}: could not inspect ({type(e).__name__}: {e})"
                )

        try:
            f.visititems(visit)
        except Exception as e:
            result.warnings.append(
                f"traversal stopped early: {type(e).__name__}: {e}"
            )

    def _links_of(
        self, group: Any, path: str, warnings: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Record soft and external links, and hard links that NeXus uses
        to give a dataset a second name."""
        out: dict[str, dict[str, Any]] = {}
        try:
            names = list(group.keys())
        except Exception:
            return out
        for name in names:
            try:
                link = group.get(name, getlink=True)
            except Exception as e:
                warnings.append(
                    f"{path}/{name}: could not read link ({type(e).__name__})"
                )
                continue
            if isinstance(link, h5py.SoftLink):
                out[name] = {"type": "soft", "target": link.path}
            elif isinstance(link, h5py.ExternalLink):
                out[name] = {
                    "type": "external",
                    "target": link.path,
                    "filename": link.filename,
                }
            else:
                # Hard link. NeXus marks a linked-in dataset with a
                # `target` attribute naming its canonical location, which
                # is the only way to tell an alias from an original.
                try:
                    target = group[name].attrs.get("target")
                except Exception:
                    target = None
                if target is not None:
                    target = _decode(target)
                    if target != f"{path.rstrip('/')}/{name}":
                        out[name] = {"type": "hard", "target": target}
        return out

    def _inspect_dataset(
        self, ds: Any, path: str, warnings: list[str]
    ) -> Dataset:
        info = Dataset(
            path=path,
            name=path.rsplit("/", 1)[-1],
            shape=tuple(ds.shape) if ds.shape is not None else (),
            dtype=str(ds.dtype),
            ndim=int(ds.ndim),
            size=int(ds.size),
            attributes=_read_attributes(ds, path, warnings),
            compression=ds.compression,
            chunks=tuple(ds.chunks) if ds.chunks else None,
        )

        if info.size <= self.max_inline_size:
            try:
                info.value = _decode(ds[()])
                info.has_value = True
            except Exception as e:
                warnings.append(
                    f"{path}: value unreadable ({type(e).__name__})"
                )

        if (
            self.compute_stats
            and HAVE_NUMPY
            and info.size <= self.max_stats_size
            and info.size > 0
        ):
            self._add_stats(ds, info, path, warnings)

        return info

    @staticmethod
    def _add_stats(
        ds: Any, info: Dataset, path: str, warnings: list[str]
    ) -> None:
        try:
            if not np.issubdtype(ds.dtype, np.number):
                return
            data = ds[()]
            finite = data[np.isfinite(data)]
            if finite.size:
                info.min_value = float(finite.min())
                info.max_value = float(finite.max())
        except Exception as e:
            warnings.append(
                f"{path}: statistics unavailable ({type(e).__name__})"
            )


def inspect_file(
    path: str | os.PathLike, **kwargs: Any
) -> InspectionResult:
    """Convenience wrapper over :class:`HDF5Inspector`."""
    return HDF5Inspector(**kwargs).inspect_file(path)
