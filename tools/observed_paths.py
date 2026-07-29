#!/usr/bin/env python3
"""Report the paths concepts are actually found at in the example files.

The crosswalk states NeXus locations in NXDL terms --
`/ENTRY:NXentry/INSTRUMENT:NXinstrument/monochromator:NXmonochromator/energy`
-- because it is a statement about the standard, checked against the live
NXDL. No file on disk looks like that. `map.crosswalk.resolve_mapping`
bridges the two at read time by matching each `name:NXclass` segment
against groups by their NX_class attribute, so one crosswalk row resolves
to `/FeFoil.001/instrument/...` in one file and `/entry/instrument/...`
in another.

That makes the concrete question -- "what path does this concept have in
real data?" -- answerable only by reading real data. This script answers
it over exampleData/ and writes a table pairing each concept's NXDL path
with the paths observed for it.

Paths are entry-relative, so an arbitrary entry name (`/FeFoil.001`)
does not turn into a distinct row.

Usage:
    python tools/observed_paths.py [--data exampleData] [-o FILE]
"""
from __future__ import annotations

import argparse
import collections
import csv
import sys
from pathlib import Path

from hdf5metadata.inspect import inspect_file, read_nexus
from hdf5metadata.map import map_nexus
from hdf5metadata.map.crosswalk import bundled_crosswalks, load_crosswalk

SUFFIXES = (".nxs", ".hdf5", ".h5")


def collect(data_dir: Path):
    """concept -> {entry-relative path -> (definitions, files, conventions)}"""
    found = collections.defaultdict(lambda: collections.defaultdict(
        lambda: (set(), set(), set())))
    for path in sorted(p for p in data_dir.iterdir()
                       if p.suffix.lower() in SUFFIXES):
        try:
            result = map_nexus(read_nexus(inspect_file(path)))
        except Exception as exc:                       # noqa: BLE001
            print(f"  ! {path.name}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue
        for record in result.records:
            entry = record.entry_path.rstrip("/")
            for concept, values in record.values.items():
                for value in (values if isinstance(values, list) else [values]):
                    source = str(value.source_path)
                    rel = source[len(entry):] if source.startswith(entry) \
                        else source
                    defs, files, convs = found[concept][rel]
                    defs.add(record.definition or "")
                    files.add(path.name)
                    convs.add(getattr(value, "convention", None) or "")
    return found


def nxdl_index() -> dict[str, set[str]]:
    """concept local name -> the NXDL path(s) any crosswalk states for it.

    Every bundled crosswalk is read, not just the XAS one: the SAS
    concepts come from cdifsas-to-nexus and would otherwise show a blank
    NXDL path.
    """
    index = collections.defaultdict(set)
    for tsv in bundled_crosswalks():
        for mapping in load_crosswalk(tsv).mappings:
            index[mapping.concept.split(":")[-1]].add(
                f"{mapping.definition}:{mapping.path}"
                if mapping.definition else mapping.path)
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=Path("exampleData"))
    ap.add_argument("-o", "--out", type=Path,
                    default=Path("docs/observed-nexus-paths.tsv"))
    args = ap.parse_args(argv)

    if not args.data.is_dir():
        print(f"error: {args.data} is not a directory", file=sys.stderr)
        return 2

    found = collect(args.data)
    nxdl = nxdl_index()

    rows = []
    for concept in sorted(found):
        for rel in sorted(found[concept]):
            defs, files, convs = found[concept][rel]
            stated = nxdl.get(concept.split(":")[-1], ())
            conventions = sorted(c for c in convs if c)
            rows.append({
                "concept": concept,
                "observed_path": rel,
                "source": ("legacy:" + ",".join(conventions) if conventions
                           else "crosswalk" if stated else "derived"),
                "nxdl_path": " | ".join(sorted(stated)),
                "definition": " | ".join(sorted(d for d in defs if d)),
                "files": " | ".join(sorted(files)),
            })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, delimiter="\t", fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    varying = sum(1 for c in found if len(found[c]) > 1)
    print(f"{len(found)} concepts, {len(rows)} concept/path pairs, "
          f"{varying} concepts observed at more than one path")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
