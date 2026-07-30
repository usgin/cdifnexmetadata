"""Parsing NXDL definitions, and the tier-3 signal/axis resolver.

NXDL is the XML schema language NeXus uses to define its base classes and
application definitions. This module reads those files into a structure
the inspector can consult, and resolves ``extends`` inheritance so a
technique definition sees what its parent declares.

Parsing is deliberately permissive
-----------------------------------

Unknown elements and attributes are ignored rather than rejected. The
NXDL *grammar* is stable, but the definitions written in it are being
actively revised -- and a tool that refuses to read a file because it
gained an attribute is worse than one that reads what it understands. A
missing definition degrades the caller to a lower resolution tier; it
never raises.

Signal and axis discovery
-------------------------

NXDL marks the plottable array in several ways across its generations:

* ``<field signal="1">`` or ``<field axis="1">`` -- the older per-field
  convention, still used by upstream ``NXxas``.
* ``@signal`` / ``@axes`` attributes declared on an ``NXdata`` group.
* Neither, which is common in the newer definitions -- the restructured
  ``NXxas`` declares ``intensity`` and ``energy`` as plain fields with no
  plotting hints at all.

All three are handled, and the third honestly yields nothing so the
caller falls through to heuristics rather than being told a guess is
authoritative.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterator

from cdifnexmetadata.nxdl.repository import DefinitionSource, Repository

NXDL_NS = "http://definition.nexusformat.org/nxdl/3.1"
_F = f"{{{NXDL_NS}}}field"
_G = f"{{{NXDL_NS}}}group"
_D = f"{{{NXDL_NS}}}doc"
_E = f"{{{NXDL_NS}}}enumeration"
_I = f"{{{NXDL_NS}}}item"
_L = f"{{{NXDL_NS}}}link"
_A = f"{{{NXDL_NS}}}attribute"

#: Names conventionally used for the independent variable when a
#: definition gives no explicit hint. Mirrors inspect.nexus so the two
#: layers agree.
_AXIS_NAME_HINTS = {
    "energy", "wavelength", "angle", "two_theta", "theta", "q",
    "momentum_transfer", "time", "time_of_flight", "position",
    "frequency",
}


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    t = " ".join("".join(el.itertext()).split())
    return t or None


@dataclass
class NXDLField:
    name: str
    type: str | None = None
    units: str | None = None
    optional: bool = True
    doc: str | None = None
    enumeration: list[str] = field(default_factory=list)
    #: Raw plotting hints as declared, if any.
    signal: str | None = None
    axis: str | None = None
    path: str = ""


@dataclass
class NXDLGroup:
    name: str | None
    nx_class: str | None
    optional: bool = True
    doc: str | None = None
    fields: dict[str, NXDLField] = field(default_factory=dict)
    groups: list["NXDLGroup"] = field(default_factory=list)
    #: `@signal` / `@axes` declared on the group itself.
    signal_attr: str | None = None
    axes_attr: list[str] = field(default_factory=list)
    path: str = ""

    def walk(self) -> Iterator["NXDLGroup"]:
        yield self
        for g in self.groups:
            yield from g.walk()

    def find(self, nx_class: str) -> list["NXDLGroup"]:
        out = [g for g in self.groups if g.nx_class == nx_class]
        for g in self.groups:
            out.extend(g.find(nx_class))
        return out


@dataclass
class NXDLDefinition:
    """One parsed NXDL definition."""

    name: str
    category: str | None = None
    extends: str | None = None
    doc: str | None = None
    root: NXDLGroup | None = None
    source: DefinitionSource | None = None
    #: Names of definitions merged in through ``extends``, nearest first.
    inherited: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_application(self) -> bool:
        return self.category == "application"

    def find(self, nx_class: str) -> list[NXDLGroup]:
        return self.root.find(nx_class) if self.root else []

    def fields_named(self, name: str) -> list[NXDLField]:
        """Every field with this name, at any depth."""
        out: list[NXDLField] = []
        if self.root is None:
            return out
        for g in self.root.walk():
            f = g.fields.get(name)
            if f is not None:
                out.append(f)
        return out

    def enumeration_for(self, name: str) -> list[str]:
        """Enumerated values declared for a field, if any. These are free
        controlled vocabulary -- e.g. absorption edge names."""
        for f in self.fields_named(name):
            if f.enumeration:
                return f.enumeration
        return []

    # -- tier-3 resolver interface ------------------------------------------
    #
    # inspect.nexus expects `signal_fields` and `axis_fields`. Returning
    # empty tuples is meaningful: it says the definition gives no
    # plotting hints, so the caller should fall through to heuristics
    # rather than trust a guess made here.

    @property
    def signal_fields(self) -> tuple[str, ...]:
        return tuple(self._plotting()[0])

    @property
    def axis_fields(self) -> tuple[str, ...]:
        return tuple(self._plotting()[1])

    def _plotting(self) -> tuple[list[str], list[str]]:
        signals: list[str] = []
        axes: list[str] = []
        if self.root is None:
            return signals, axes

        for g in self.root.walk():
            # Group-level @signal / @axes.
            if g.signal_attr and g.signal_attr not in signals:
                signals.append(g.signal_attr)
            for a in g.axes_attr:
                if a and a != "." and a not in axes:
                    axes.append(a)
            # Field-level signal= / axis=, the older convention.
            for fname, f in g.fields.items():
                if f.signal and fname not in signals:
                    signals.append(fname)
                elif f.axis and fname not in axes:
                    axes.append(fname)

        # An NXdata declared in the definition, with no explicit hints,
        # still tells us which fields are plottable candidates. Only use
        # naming to split them -- and only when nothing was declared, so
        # a declared answer is never overridden by a guess.
        if not signals and not axes:
            for g in self.root.walk():
                if g.nx_class != "NXdata":
                    continue
                for fname in g.fields:
                    if fname.lower() in _AXIS_NAME_HINTS:
                        axes.append(fname)
                    else:
                        signals.append(fname)
        return signals, axes


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _truthy(v: str | None) -> bool:
    return v is not None and v.strip().lower() in ("true", "1", "yes")


def _parse_field(el: ET.Element, parent_path: str) -> NXDLField:
    name = el.get("name") or ""
    min_occurs = el.get("minOccurs")
    optional = True
    if _truthy(el.get("required")) or (min_occurs and min_occurs != "0"):
        optional = False
    if el.get("optional") is not None:
        optional = _truthy(el.get("optional"))
    return NXDLField(
        name=name,
        type=el.get("type"),
        units=el.get("units"),
        optional=optional,
        doc=_text(el.find(_D)),
        enumeration=[
            i.get("value") for i in el.iter(_I) if i.get("value") is not None
        ],
        signal=el.get("signal"),
        axis=el.get("axis"),
        path=f"{parent_path}/{name}",
    )


def _parse_group(el: ET.Element, parent_path: str = "") -> NXDLGroup:
    nx_class = el.get("type")
    name = el.get("name")
    label = name or (nx_class or "").replace("NX", "").upper()
    path = f"{parent_path}/{label}" if label else parent_path

    axes_attr: list[str] = []
    signal_attr: str | None = None
    for a in el.findall(_A):
        an = a.get("name")
        if an == "signal":
            items = [i.get("value") for i in a.iter(_I) if i.get("value")]
            signal_attr = items[0] if items else None
        elif an == "axes":
            axes_attr = [i.get("value") for i in a.iter(_I) if i.get("value")]

    group = NXDLGroup(
        name=name,
        nx_class=nx_class,
        optional=_truthy(el.get("optional")) if el.get("optional") else True,
        doc=_text(el.find(_D)),
        signal_attr=signal_attr,
        axes_attr=axes_attr,
        path=path,
    )
    for child in el:
        if child.tag == _F:
            f = _parse_field(child, path)
            if f.name:
                group.fields[f.name] = f
        elif child.tag == _G:
            group.groups.append(_parse_group(child, path))
        # Links, attributes and anything unrecognised are skipped rather
        # than rejected -- see the module docstring.
    return group


def parse(
    xml_text: str, name: str, source: DefinitionSource | None = None
) -> NXDLDefinition:
    """Parse NXDL XML. Malformed input yields a definition carrying a
    warning, never an exception."""
    d = NXDLDefinition(name=name, source=source)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        d.warnings.append(f"{name}: XML is not well formed ({e})")
        return d

    d.category = root.get("category")
    d.extends = root.get("extends")
    d.doc = _text(root.find(_D))
    d.root = _parse_group(root)
    return d


# ---------------------------------------------------------------------------
# loading with inheritance
# ---------------------------------------------------------------------------

def load(
    name: str,
    repository: Repository | None = None,
    _seen: set[str] | None = None,
) -> NXDLDefinition | None:
    """Load a definition, merging in what it ``extends``.

    ``NXxas_trans extends NXxas``, and the parent holds the element,
    edge and sample structure the child does not repeat -- so a child
    read in isolation is incomplete. Returns None when the definition
    cannot be found, so callers degrade a tier rather than fail.
    """
    repo = repository or Repository()
    seen = _seen if _seen is not None else set()
    if name in seen:
        return None                       # cyclic extends; ignore quietly
    seen.add(name)

    got = repo.fetch(name)
    if got is None:
        return None
    xml_text, source = got
    d = parse(xml_text, name, source)

    parent_name = d.extends
    if parent_name and parent_name not in ("NXobject", None):
        parent = load(parent_name, repo, seen)
        if parent is not None and parent.root is not None and d.root is not None:
            _merge(d.root, parent.root)
            d.inherited = [parent_name, *parent.inherited]
        elif parent is None:
            d.warnings.append(
                f"{name} extends {parent_name}, which could not be loaded; "
                f"inherited structure is missing"
            )
    return d


def _merge(child: NXDLGroup, parent: NXDLGroup) -> None:
    """Fold parent structure into child. The child always wins -- a
    technique definition may narrow what it inherits."""
    for fname, f in parent.fields.items():
        child.fields.setdefault(fname, f)
    if child.signal_attr is None:
        child.signal_attr = parent.signal_attr
    if not child.axes_attr:
        child.axes_attr = list(parent.axes_attr)

    by_key = {(g.name, g.nx_class): g for g in child.groups}
    for pg in parent.groups:
        key = (pg.name, pg.nx_class)
        if key in by_key:
            _merge(by_key[key], pg)
        else:
            child.groups.append(pg)


# ---------------------------------------------------------------------------
# resolver factory
# ---------------------------------------------------------------------------

def make_resolver(repository: Repository | None = None):
    """Build the ``nxdl_resolver`` callable :func:`inspect.nexus.read_nexus`
    accepts, with a per-run memo so a multi-entry file resolves each
    definition once rather than once per entry.

        >>> from cdifnexmetadata.nxdl import make_resolver
        >>> nx = read_nexus(result, nxdl_resolver=make_resolver())
    """
    repo = repository or Repository()
    memo: dict[str, NXDLDefinition | None] = {}

    def resolve(name: str) -> NXDLDefinition | None:
        if name not in memo:
            memo[name] = load(name, repo)
        return memo[name]

    return resolve
