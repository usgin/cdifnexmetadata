"""NeXus interpretation of an HDF5 structural report.

Stage 1b. Takes the vocabulary-free result from :mod:`cdifnexmetadata.inspect.hdf5`
and reads it as NeXus: which groups are `NXentry`, what application
definition each declares, which array in an `NXdata` is the measurement
and which are its coordinates.

**Still no CDIF vocabulary here.** This module speaks NeXus, not
schema.org — the `map/` layer turns what this produces into CDIF.

Two deliberate generalisations
------------------------------

**Groups are indexed by `NX_class`, not by hardcoded path.** Rather than
walking to `entry/instrument/monochromator/energy`, callers ask an entry
for every `NXmonochromator` under it. This keeps the module
general-purpose across techniques and, more practically, robust to the
NeXus definitions being revised — the restructured `NXxas` moved energy
out from under `NXmonochromator`, and a path-walking implementation
would have broken on exactly that.

**Every resolution records how it was reached.** Signal and axis
identification runs through four tiers of decreasing reliability, and
each result carries the tier that produced it. A consumer can then treat
a heuristic guess differently from a declared `@signal` attribute, and
the eventual CDIF output can be honest about which is which.

Signal/axis resolution tiers
----------------------------

1. ``signal_attribute`` — the `NXdata@signal` / `@axes` attributes, or
   the older per-field `@signal` / `@axis` / `@primary`. Authoritative.
2. ``link_target`` — where `NXdata` reaches its arrays by link, the
   target says what each one is: linked from an `NXdetector` it is a
   measurement, from an `NXmonochromator` or a field named like an axis
   it is a coordinate. Real files rely on this more than on tier 1.
3. ``nxdl`` — the declared application definition's own tree, when a
   resolver is supplied. Requires the definitions; optional.
4. ``heuristic`` — shape agreement and naming conventions. Last resort,
   always warned about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from cdifnexmetadata.inspect.hdf5 import Dataset, Group, InspectionResult

#: Attribute naming a group's NeXus base class.
NX_CLASS = "NX_class"

#: NX_class values whose datasets are measurements rather than coordinates.
MEASUREMENT_CLASSES = {"NXdetector", "NXmonitor"}

#: NX_class values whose datasets are coordinates rather than measurements.
COORDINATE_CLASSES = {"NXmonochromator", "NXpositioner", "NXcrystal"}

#: Field names conventionally used for the independent variable.
AXIS_NAME_HINTS = {
    "energy", "wavelength", "angle", "two_theta", "theta", "q",
    "momentum_transfer", "time", "time_of_flight", "temperature",
    "position", "x", "y", "z", "frequency",
}

#: Resolution tiers, most to least reliable.
TIER_SIGNAL_ATTR = "signal_attribute"
TIER_LINK = "link_target"
TIER_NXDL = "nxdl"
TIER_HEURISTIC = "heuristic"


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

@dataclass
class NXField:
    """A dataset read as a NeXus field."""

    name: str
    path: str
    units: str | None = None
    long_name: str | None = None
    shape: tuple[int, ...] = ()
    dtype: str = ""
    size: int = 0
    value: Any = None
    has_value: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_scalar(self) -> bool:
        return self.shape == ()


@dataclass
class NXGroup:
    """A group read as a NeXus base class instance."""

    name: str
    path: str
    nx_class: str | None
    fields: dict[str, NXField] = field(default_factory=dict)
    groups: list["NXGroup"] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def find(self, nx_class: str) -> list["NXGroup"]:
        """Every descendant of the given NX_class, at any depth."""
        out = [g for g in self.groups if g.nx_class == nx_class]
        for g in self.groups:
            out.extend(g.find(nx_class))
        return out

    def first(self, nx_class: str) -> "NXGroup | None":
        found = self.find(nx_class)
        return found[0] if found else None

    def field_value(self, *names: str) -> Any:
        """Value of the first field matching any of ``names``, at any
        depth. Returns None when absent -- callers decide whether that
        is a problem."""
        for n in names:
            if n in self.fields and self.fields[n].has_value:
                return self.fields[n].value
        for g in self.groups:
            v = g.field_value(*names)
            if v is not None:
                return v
        return None

    def walk(self) -> Iterator["NXGroup"]:
        yield self
        for g in self.groups:
            yield from g.walk()


@dataclass
class NXSignal:
    """An array identified as a measurement."""

    name: str
    path: str
    units: str | None = None
    shape: tuple[int, ...] = ()
    resolution: str = TIER_HEURISTIC
    #: NX_class of the group the array physically lives in, when linked.
    source_class: str | None = None


@dataclass
class NXAxis:
    """An array identified as a coordinate."""

    name: str
    path: str
    units: str | None = None
    shape: tuple[int, ...] = ()
    #: Dimension of the signal this axis spans, when known.
    dimension: int | None = None
    resolution: str = TIER_HEURISTIC
    source_class: str | None = None


@dataclass
class NXData:
    """An NXdata group with its signal/axis interpretation."""

    path: str
    name: str
    signals: list[NXSignal] = field(default_factory=list)
    axes: list[NXAxis] = field(default_factory=list)
    #: Lowest (least reliable) tier used to reach this interpretation.
    resolution: str = TIER_HEURISTIC
    warnings: list[str] = field(default_factory=list)


@dataclass
class NXEntry:
    """One NXentry -- a single measurement within the file."""

    name: str
    path: str
    #: Value of the `definition` field: the application definition, e.g.
    #: "NXxas". None when the file declares none.
    definition: str | None = None
    definition_version: str | None = None
    root: NXGroup | None = None
    data: list[NXData] = field(default_factory=list)

    def find(self, nx_class: str) -> list[NXGroup]:
        return self.root.find(nx_class) if self.root else []

    def first(self, nx_class: str) -> NXGroup | None:
        return self.root.first(nx_class) if self.root else None

    def field_value(self, *names: str) -> Any:
        return self.root.field_value(*names) if self.root else None

    @property
    def title(self) -> Any:
        return self.field_value("title")

    @property
    def start_time(self) -> Any:
        return self.field_value("start_time")

    @property
    def end_time(self) -> Any:
        return self.field_value("end_time")

    @property
    def identifier(self) -> Any:
        return self.field_value("entry_identifier")


@dataclass
class NeXusResult:
    """NeXus reading of a whole file."""

    is_nexus: bool = False
    entries: list[NXEntry] = field(default_factory=list)
    #: Entry named by the root `@default` attribute, if any.
    default_entry: str | None = None
    #: Distinct application definitions declared across entries.
    definitions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_multi_entry(self) -> bool:
        return len(self.entries) > 1

    def entry(self, name: str) -> NXEntry | None:
        return next(
            (e for e in self.entries if e.name == name or e.path == name), None
        )


# ---------------------------------------------------------------------------
# sniffing
# ---------------------------------------------------------------------------

def is_nexus(result: InspectionResult) -> bool:
    """True if the file carries NeXus markers.

    Extension is not evidence -- plenty of NeXus files are named .h5 and
    plenty of plain HDF5 files are not NeXus. The marker is an NX_class
    attribute on any group.
    """
    return any(g.attributes.get(NX_CLASS) for g in result.groups)


def _nx_class(g: Group) -> str | None:
    v = g.attributes.get(NX_CLASS)
    return v if isinstance(v, str) else None


# ---------------------------------------------------------------------------
# tree construction
# ---------------------------------------------------------------------------

def _to_field(d: Dataset) -> NXField:
    units = d.attributes.get("units")
    long_name = d.attributes.get("long_name")
    return NXField(
        name=d.name,
        path=d.path,
        units=units if isinstance(units, str) else None,
        long_name=long_name if isinstance(long_name, str) else None,
        shape=d.shape,
        dtype=d.dtype,
        size=d.size,
        value=d.value,
        has_value=d.has_value,
        attributes=d.attributes,
    )


def _build_group(
    result: InspectionResult, group: Group, warnings: list[str]
) -> NXGroup:
    node = NXGroup(
        name=group.name,
        path=group.path,
        nx_class=_nx_class(group),
        attributes=group.attributes,
    )
    for child in result.children_of(group.path):
        if isinstance(child, Dataset):
            node.fields[child.name] = _to_field(child)
        else:
            node.groups.append(_build_group(result, child, warnings))
    return node


# ---------------------------------------------------------------------------
# signal / axis resolution
# ---------------------------------------------------------------------------

def _class_of_path(result: InspectionResult, path: str) -> str | None:
    """NX_class of the group containing the given dataset path."""
    parent = path.rsplit("/", 1)[0] or "/"
    g = result.group(parent)
    return _nx_class(g) if g else None


def _resolve_by_attributes(
    result: InspectionResult, nxdata: Group, node: NXData
) -> bool:
    """Tier 1: the NXdata @signal and @axes attributes."""
    signal = nxdata.attributes.get("signal")
    if not signal:
        # Older convention: a field carries @signal=1.
        for name, f in _fields_of(result, nxdata).items():
            if f.attributes.get("signal"):
                signal = name
                break
    if not signal:
        return False

    fields = _fields_of(result, nxdata)
    signames = [signal] if isinstance(signal, str) else list(signal)
    for s in signames:
        f = fields.get(s)
        if f is None:
            node.warnings.append(
                f"@signal names {s!r} but no such field is present"
            )
            continue
        node.signals.append(
            NXSignal(
                name=f.name, path=f.path, units=f.units, shape=f.shape,
                resolution=TIER_SIGNAL_ATTR,
            )
        )

    axes = nxdata.attributes.get("axes")
    if axes:
        axnames = [axes] if isinstance(axes, str) else list(axes)
        for i, a in enumerate(axnames):
            if a in (".", None):
                continue
            f = fields.get(a)
            if f is None:
                node.warnings.append(
                    f"@axes names {a!r} but no such field is present"
                )
                continue
            node.axes.append(
                NXAxis(
                    name=f.name, path=f.path, units=f.units, shape=f.shape,
                    dimension=i, resolution=TIER_SIGNAL_ATTR,
                )
            )
    return bool(node.signals)


def _resolve_by_links(
    result: InspectionResult, nxdata: Group, node: NXData
) -> bool:
    """Tier 2: where NXdata reaches arrays by link, the target says what
    each one is. See the module docstring."""
    if not nxdata.links:
        return False

    found_any = False
    for name, link in nxdata.links.items():
        target = link.get("target")
        if not target:
            continue
        src_class = _class_of_path(result, target)
        tds = result.dataset(target)
        units = tds.attributes.get("units") if tds else None
        shape = tds.shape if tds else ()

        if src_class in COORDINATE_CLASSES or name.lower() in AXIS_NAME_HINTS:
            node.axes.append(
                NXAxis(
                    name=name, path=target,
                    units=units if isinstance(units, str) else None,
                    shape=shape, resolution=TIER_LINK, source_class=src_class,
                )
            )
            found_any = True
        elif src_class in MEASUREMENT_CLASSES:
            node.signals.append(
                NXSignal(
                    name=name, path=target,
                    units=units if isinstance(units, str) else None,
                    shape=shape, resolution=TIER_LINK, source_class=src_class,
                )
            )
            found_any = True
    return found_any


def _resolve_by_nxdl(
    nxdata: Group, node: NXData, definition: str | None,
    resolver: Callable[[str], Any] | None,
) -> bool:
    """Tier 3: the declared application definition's own tree.

    ``resolver`` takes a definition name and returns something exposing
    ``signal_fields`` and ``axis_fields`` sequences, or None. Kept behind
    a callable so this module stays offline-testable and does not depend
    on how definitions are fetched or cached.
    """
    if not definition or resolver is None:
        return False
    try:
        spec = resolver(definition)
    except Exception:
        return False
    if spec is None:
        return False

    fields = {n: n for n in nxdata.children}
    found = False
    for name in getattr(spec, "signal_fields", ()) or ():
        if name in fields:
            node.signals.append(
                NXSignal(name=name, path=f"{nxdata.path}/{name}",
                         resolution=TIER_NXDL)
            )
            found = True
    for i, name in enumerate(getattr(spec, "axis_fields", ()) or ()):
        if name in fields:
            node.axes.append(
                NXAxis(name=name, path=f"{nxdata.path}/{name}",
                       dimension=i, resolution=TIER_NXDL)
            )
            found = True
    return found


def _sweep_unclassified(
    result: InspectionResult, nxdata: Group, node: NXData
) -> int:
    """Tier 4: classify arrays no higher tier accounted for.

    Runs after every tier, not only as a last resort. A higher tier
    frequently explains *some* of a group's contents and not all of it:
    in real NXas files the linked-in detector channels resolve at tier 2
    while the derived absorption coefficients stored alongside them
    (``mutrans``, ``mufluor``) are plain datasets that no link mentions.
    Returning at the first tier that produced anything would silently
    drop them -- and they are usually the arrays a user most wants.

    Each result is tagged ``heuristic`` individually, so a consumer can
    still tell a swept array from a declared one.
    """
    fields = _fields_of(result, nxdata)
    arrays = {n: f for n, f in fields.items() if f.size > 1}
    if not arrays:
        return 0

    known = {s.name for s in node.signals} | {a.name for a in node.axes}
    known |= {s.path.rsplit("/", 1)[-1] for s in node.signals}
    known |= {a.path.rsplit("/", 1)[-1] for a in node.axes}

    added = 0
    for name, f in arrays.items():
        if name in known:
            continue
        if name.lower() in AXIS_NAME_HINTS:
            node.axes.append(
                NXAxis(name=name, path=f.path, units=f.units, shape=f.shape,
                       resolution=TIER_HEURISTIC)
            )
        else:
            node.signals.append(
                NXSignal(name=name, path=f.path, units=f.units, shape=f.shape,
                         resolution=TIER_HEURISTIC)
            )
        added += 1
    return added


def _fields_of(
    result: InspectionResult, group: Group
) -> dict[str, NXField]:
    return {
        c.name: _to_field(c)
        for c in result.children_of(group.path)
        if isinstance(c, Dataset)
    }


def _lowest_tier(node: NXData) -> str:
    order = [TIER_SIGNAL_ATTR, TIER_LINK, TIER_NXDL, TIER_HEURISTIC]
    used = {s.resolution for s in node.signals} | {a.resolution for a in node.axes}
    for tier in reversed(order):
        if tier in used:
            return tier
    return TIER_HEURISTIC


def resolve_nxdata(
    result: InspectionResult,
    nxdata: Group,
    definition: str | None = None,
    nxdl_resolver: Callable[[str], Any] | None = None,
) -> NXData:
    """Identify signals and axes in one NXdata group, trying each tier in
    turn and recording which one produced each result."""
    node = NXData(path=nxdata.path, name=nxdata.name)

    # Tiers are tried in order of reliability, but they are additive, not
    # exclusive -- see _sweep_unclassified on why stopping at the first
    # productive tier loses real arrays.
    resolved = _resolve_by_attributes(result, nxdata, node)
    if not resolved:
        resolved = _resolve_by_links(result, nxdata, node)
    if not resolved:
        resolved = _resolve_by_nxdl(nxdata, node, definition, nxdl_resolver)

    swept = _sweep_unclassified(result, nxdata, node)

    if not resolved and swept:
        node.warnings.append(
            "signal/axis identification rests entirely on heuristics; the "
            "file declares neither @signal/@axes nor resolvable links"
        )
    elif swept:
        node.warnings.append(
            f"{swept} array(s) were not accounted for by @signal/@axes or "
            f"links and were classified heuristically"
        )
    if not node.signals:
        node.warnings.append("no signal could be identified")

    node.resolution = _lowest_tier(node)
    return node


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _definition_name(value) -> str | None:
    """The application definition as a single name.

    Real writers do not all store `definition` as a scalar string. Soleil,
    SLS and the APS area-detector writer store it as a one-element array,
    which arrives here as a list -- and a list is unhashable, so it used
    to take down the whole read the moment the distinct definitions were
    collected. Take the first entry: a NeXus entry declares one
    application definition, so a sequence is a serialisation detail
    rather than a claim to conform to several.
    """
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if v not in (None, "")), None)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_nexus(
    result: InspectionResult,
    nxdl_resolver: Callable[[str], Any] | None = None,
) -> NeXusResult:
    """Read a structural result as NeXus.

    Returns a result with ``is_nexus`` False and no entries when the file
    carries no NeXus markers -- that is a valid outcome, not an error.
    """
    out = NeXusResult(is_nexus=is_nexus(result))
    if not out.is_nexus:
        return out

    root = result.group("/")
    if root is not None:
        default = root.attributes.get("default")
        if isinstance(default, str):
            out.default_entry = default

    entry_groups = [
        g for g in result.groups if _nx_class(g) == "NXentry"
    ]
    if not entry_groups:
        out.warnings.append(
            "file carries NX_class markers but declares no NXentry"
        )
        return out

    for g in entry_groups:
        entry = NXEntry(name=g.name, path=g.path)
        entry.root = _build_group(result, g, out.warnings)

        definition = entry.root.fields.get("definition")
        if definition is not None and definition.has_value:
            entry.definition = _definition_name(definition.value)
            ver = definition.attributes.get("version")
            entry.definition_version = ver if isinstance(ver, str) else None

        for sub in entry.root.walk():
            if sub.nx_class != "NXdata":
                continue
            gsub = result.group(sub.path)
            if gsub is None:
                continue
            entry.data.append(
                resolve_nxdata(result, gsub, entry.definition, nxdl_resolver)
            )

        out.entries.append(entry)

    out.definitions = sorted(
        {e.definition for e in out.entries if e.definition}
    )
    if out.default_entry and not out.entry(out.default_entry):
        out.warnings.append(
            f"root @default names {out.default_entry!r} but no such entry "
            f"was found"
        )
    return out
