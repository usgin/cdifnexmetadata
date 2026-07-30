"""Reading XDI, the text interchange format for XAS spectra.

The second input binding. XDI is a flat text format — a version line, a
block of `# Namespace.tag: value` headers, optional free comments, a
header-end line, an optional column-label line, then whitespace-
separated numeric columns.

Why this needs almost none of the NeXus machinery
-------------------------------------------------

`inspect/nexus.py` exists because HDF5 is a tree and finding a value
means walking it by class. XDI is a dictionary. So the concepts come out
by lookup, not by path resolution, and nothing in `map/crosswalk.py` is
involved. That asymmetry is the point of keying the intermediate on
concepts: two formats this different converge because they are asked the
same question, not because they are read the same way.

Separator lines follow the specification, not the examples
----------------------------------------------------------

The spec defines the header-end line as a comment token followed by
three or more dashes, with no whitespace token between them. Its
illustration happens to show a space, and 258 of the 272 files in the
XAS Data Library do not. Both are accepted here — a reader that rejects
the reference library is not reading the format.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hdf5metadata.inspect.hdf5 import InspectionResult

#: The first line of an XDI file. This is the whole sniff test: the spec
#: makes the version line mandatory and first, so a file that starts any
#: other way is not XDI whatever its extension says.
#:
#: The space after the '#' is optional, and that is not a nicety: 118 of
#: the 272 files in the XAS Data Library write "#XDI/1.0" closed up, as
#: do 9 of the 55 in the XAS-CDIF corpus. The specification asks for a
#: comment token followed by the version, and says nothing about
#: whitespace between them -- the same reading that makes "#-----" a
#: valid header-end line.
XDI_MAGIC = re.compile(r"^#\s*XDI/")

_VERSION = re.compile(r"^#\s*XDI/(?P<version>[\d.]+)\s*(?P<application>.*)$")
_FIELD = re.compile(r"^#\s*(?P<key>[A-Za-z_][\w]*\.[\w]+)\s*:\s*(?P<value>.*)$")
_FIELD_END = re.compile(r"^#\s*/{3,}\s*$")
_HEADER_END = re.compile(r"^#\s*-{3,}\s*$")
_COMMENT = re.compile(r"^#\s?(?P<text>.*)$")

#: How much of a file to read when sniffing. The version line is first,
#: so this never needs to be large -- and a binary file must not be
#: dragged into memory to find that out.
_SNIFF_BYTES = 512


def is_xdi(path: str | Path) -> bool:
    """Whether a file announces itself as XDI on its first line."""
    try:
        with open(path, "rb") as f:
            head = f.read(_SNIFF_BYTES)
    except OSError:
        return False
    if b"\x00" in head:
        return False                    # binary; XDI is US-ASCII text
    try:
        first = head.decode("utf-8", "strict").splitlines()[0]
    except (UnicodeDecodeError, IndexError):
        return False
    return bool(XDI_MAGIC.match(first.lstrip("﻿")))


@dataclass
class XDIEntry:
    """One spectrum.

    An XDI file holds exactly one, unlike a NeXus file which may hold
    dozens. The type exists so the emitter can treat both the same way.
    """

    name: str
    path: str = "/"
    definition: str | None = None
    definition_version: str | None = None
    title: Any = None
    start_time: Any = None
    end_time: Any = None
    identifier: Any = None


@dataclass
class XDIResult:
    """An XDI file, read.

    Deliberately shaped like `NeXusResult` where the emitter looks --
    `definitions`, `default_entry`, `entries` -- so stage 3 needs no
    knowledge of which format produced it.
    """

    is_xdi: bool = False
    #: `Namespace.tag` -> value, exactly as written.
    headers: dict[str, str] = field(default_factory=dict)
    #: Column number -> label, from the `Column.N` headers.
    columns: dict[int, str] = field(default_factory=dict)
    #: Column number -> unit, where the label carried one. The XDI
    #: dictionary allows `Column.1: energy eV`, and 33 of the 55 files in
    #: the reference corpus use it, so the unit is recorded rather than
    #: discarded with the rest of the label.
    column_units: dict[int, str] = field(default_factory=dict)
    #: Labels from the line after the header-end, when present. Files
    #: routinely give these instead of `Column.N` headers.
    array_labels: list[str] = field(default_factory=list)
    #: Free-text user comments, in order.
    comments: list[str] = field(default_factory=list)
    xdi_version: str | None = None
    application: str | None = None
    row_count: int = 0
    #: Column number (1-based) -> (min, max) field width in characters,
    #: measured over the data rows. A field is taken to run from the end
    #: of the previous field to the end of this one, so the width
    #: includes the padding in front of the value -- which is what a
    #: fixed-width reader slices on.
    #:
    #: min == max for every column means the file really is fixed-width;
    #: 21 of the 55 files in the reference corpus are. Where they differ
    #: the file is whitespace-separated and a reader must tokenise.
    column_widths: dict[int, tuple[int, int]] = field(default_factory=dict)
    entries: list[XDIEntry] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    default_entry: str | None = None
    warnings: list[str] = field(default_factory=list)

    def header(self, *keys: str) -> str | None:
        """First present header among ``keys``, matched case-insensitively.

        Real files disagree about case -- `Facility.Name`, `Sample.Prep`
        -- and the spec does not require any particular one.
        """
        lowered = {k.lower(): v for k, v in self.headers.items()}
        for k in keys:
            got = lowered.get(k.lower())
            if got:
                return got
        return None

    @property
    def labels(self) -> list[str]:
        """Column labels, preferring the `Column.N` headers and falling
        back to the label line."""
        if self.columns:
            return [self.columns[n] for n in sorted(self.columns)]
        return list(self.array_labels)




def _measure_fields(result: "XDIResult", line: str) -> None:
    """Widen each column's observed field extent by one data row.

    Width is measured to the end of the field rather than the length of
    the token, because a fixed-width layout pads on the left: in
    `       12508.00       2.000000`, the value is 8 characters and the
    field is 15. Slicing needs the 15.
    """
    previous_end = 0
    for position, match in enumerate(re.finditer(r"\S+", line), start=1):
        width = match.end() - previous_end
        previous_end = match.end()
        low, high = result.column_widths.get(position, (width, width))
        result.column_widths[position] = (min(low, width), max(high, width))


def _split_column_label(value: str) -> tuple[str, str | None]:
    """`Column.N` value -> (name, unit).

    The XDI dictionary writes a column label as a name optionally
    followed by a unit: `energy eV`. Beamline software sometimes appends
    its own provenance after a `||` separator --
    `itrans counts || 13BMD:scaler1_calc3.VAL` -- which is neither name
    nor unit, so anything from the separator on is dropped first.

    Only a second token is taken as a unit. A third would mean the label
    is prose rather than `name unit`, and guessing which word is the unit
    would be worse than recording none.
    """
    if not value:
        return "", None
    head = value.split("||", 1)[0].strip()
    parts = head.split()
    if not parts:
        return "", None
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], None


def inspect_xdi(
    path: str | Path,
) -> tuple[InspectionResult, XDIResult]:
    """Read an XDI file into the same two-part shape the HDF5 reader
    returns: file facts, and the format's own reading of the content."""
    p = Path(path)
    result = XDIResult()
    inspection = InspectionResult(filename=p.name, source=str(p))
    try:
        inspection.file_size = p.stat().st_size
    except OSError:
        pass

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        inspection.warnings.append(f"cannot read {p.name}: {e}")
        return inspection, result

    lines = text.splitlines()
    if not lines or not XDI_MAGIC.match(lines[0].lstrip("﻿")):
        result.warnings.append(
            "first line does not declare XDI ('# XDI/1.0'); not an XDI file"
        )
        return inspection, result
    result.is_xdi = True

    m = _VERSION.match(lines[0].lstrip("﻿"))
    if m:
        result.xdi_version = m.group("version")
        result.application = (m.group("application") or "").strip() or None

    in_header = True
    pending_labels: str | None = None
    for line in lines[1:]:
        if in_header:
            if _HEADER_END.match(line):
                in_header = False
                continue
            if _FIELD_END.match(line):
                continue
            field_match = _FIELD.match(line)
            if field_match:
                key = field_match.group("key").strip()
                value = field_match.group("value").strip()
                if key in result.headers:
                    result.warnings.append(
                        f"{key} appears more than once; keeping the last"
                    )
                result.headers[key] = value
                continue
            comment = _COMMENT.match(line)
            if comment:
                text_ = comment.group("text").strip()
                if text_:
                    result.comments.append(text_)
                    # The label line is the last comment before the data,
                    # so remember each candidate and keep the final one.
                    pending_labels = text_
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # A comment between header-end and data is the column-label
            # line in most real files.
            if not result.array_labels:
                result.array_labels = stripped.lstrip("#").split()
            continue
        result.row_count += 1
        _measure_fields(result, line.rstrip("\n"))

    if not in_header and pending_labels and not result.array_labels:
        # Label line written as the last header comment rather than after
        # the header-end marker.
        candidate = pending_labels.split()
        if len(candidate) > 1:
            result.array_labels = candidate

    for key, value in result.headers.items():
        head, _, tag = key.partition(".")
        if head.lower() == "column" and tag.isdigit():
            name, unit = _split_column_label(value)
            result.columns[int(tag)] = name
            if unit:
                result.column_units[int(tag)] = unit

    if not in_header and result.row_count == 0:
        result.warnings.append("header parsed but no data rows were found")
    if in_header:
        result.warnings.append(
            "no header-end line ('# ---'); the whole file was read as header"
        )

    entry = XDIEntry(
        name=p.stem,
        path="/",
        title=result.header("Sample.name", "Scan.title"),
        start_time=result.header("Scan.start_time"),
        end_time=result.header("Scan.end_time"),
        identifier=result.header("Scan.id", "Sample.id"),
    )
    result.entries = [entry]
    return inspection, result
