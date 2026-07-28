"""Command line entry point.

    hdf5metadata FILE [FILE ...] [-o OUT] [--validate --profile-dir DIR]

Exit codes are meant to be usable in a pipeline: 0 when everything asked
for succeeded, 1 when a document failed validation, 2 when a file could
not be read at all. A run that emitted a document but could not check it
is still 0 — nothing was found wrong — but the skip is printed, because
"not checked" and "checked and fine" must not look alike.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from hdf5metadata.emit import DEFAULT_BASE, emit_document
from hdf5metadata.inspect import inspect_file, read_nexus
from hdf5metadata.map import map_nexus
from hdf5metadata.map.concepts import MappingResult
from hdf5metadata.map.crosswalk import load_crosswalk
from hdf5metadata.map.legacy import LegacyTable, load_legacy
from hdf5metadata.validate import Profile, find_profile, validate_document

#: Lets a working environment be configured once rather than passed on
#: every invocation.
PROFILE_DIR_ENV = "HDF5METADATA_PROFILE_DIR"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hdf5metadata",
        description="Extract CDIF metadata from NeXus-formatted HDF5 files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  hdf5metadata scan.nxs
  hdf5metadata scan.nxs -o scan.jsonld
  hdf5metadata *.nxs -o out/ --validate --profile-dir ../XAS-CDIF/release
  hdf5metadata scan.nxs --report        # what was found, and what was not
""")
    p.add_argument("files", nargs="+", type=Path,
                   help="HDF5/NeXus files to describe")
    p.add_argument("-o", "--output", type=Path,
                   help="output file, or a directory when given several "
                        "inputs (default: stdout)")
    p.add_argument("--base", default=DEFAULT_BASE,
                   help=f"base URI for generated identifiers "
                        f"(default: {DEFAULT_BASE})")
    p.add_argument("--source-url",
                   help="URL the file is published at; used for "
                        "schema:contentUrl instead of a generated one")
    p.add_argument("--crosswalk", type=Path,
                   help="SSSOM crosswalk TSV (default: the bundled one)")
    p.add_argument("--legacy", type=Path,
                   help="legacy path table TSV (default: the bundled one); "
                        "pass --no-legacy to use none")
    p.add_argument("--no-legacy", action="store_true",
                   help="do not consult the legacy path table")
    p.add_argument("--validate", action="store_true",
                   help="check the document against the CDIF profile")
    p.add_argument("--profile-dir", type=Path,
                   help=f"directory holding the profile schema, frame and "
                        f"SHACL shapes (or set {PROFILE_DIR_ENV})")
    p.add_argument("--report", action="store_true",
                   help="print what was extracted and what was not, to "
                        "stderr, instead of only the document")
    p.add_argument("--indent", type=int, default=2,
                   help="JSON indent (default: 2)")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress warnings on stderr")
    return p


def _resolve_profile(args) -> Profile:
    directory = args.profile_dir or os.environ.get(PROFILE_DIR_ENV)
    if not directory:
        return Profile()
    return find_profile(directory)


def _report(path: Path, mapping: MappingResult, out) -> None:
    """What was found, and — as importantly — what was looked for and
    was not there."""
    print(f"\n{path.name}", file=out)
    print(f"  crosswalk: {Path(mapping.crosswalk_source).name}", file=out)
    for record in mapping.records[:3]:
        concepts = sorted(c.split(":", 1)[-1] for c in record.values)
        print(f"  {record.entry_name} ({record.definition or 'no definition'})"
              f": {len(concepts)} concepts", file=out)
        print(f"      {', '.join(concepts)}", file=out)
    if len(mapping.records) > 3:
        print(f"  ... and {len(mapping.records) - 3} more entries", file=out)
    groups = mapping.structure_groups()
    if len(mapping.records) > 1:
        print(f"  {len(mapping.records)} entries -> {len(groups)} distinct "
              f"data structure(s)", file=out)
    for record in mapping.records[:1]:
        for w in record.warnings:
            print(f"  ! {w}", file=out)


def _process(path: Path, args, profile: Profile, err) -> tuple[dict, int]:
    inspection = inspect_file(path)
    for w in inspection.warnings:
        if not args.quiet:
            print(f"{path.name}: {w}", file=err)

    nexus = read_nexus(inspection)
    if not nexus.is_nexus and not args.quiet:
        print(f"{path.name}: no NeXus markers; emitting file-level core only",
              file=err)

    crosswalk = load_crosswalk(args.crosswalk) if args.crosswalk else None
    # An empty table, not None: None means "use the bundled default" to
    # map_nexus, so passing it would make --no-legacy do nothing.
    legacy = LegacyTable(source="disabled") if args.no_legacy else (
        load_legacy(args.legacy) if args.legacy else None)
    mapping = map_nexus(nexus, crosswalk, legacy)

    result = emit_document(
        inspection, nexus, mapping,
        base=args.base, source_url=args.source_url)

    if args.report:
        _report(path, mapping, err)
    if not args.quiet:
        for w in result.warnings:
            print(f"{path.name}: {w}", file=err)
        print(f"{path.name}: claims {', '.join(result.profiles)}", file=err)

    status = 0
    if args.validate:
        validation = validate_document(result.document, profile)
        print(f"{path.name}: validation {validation.summary()}", file=err)
        for issue in validation.failures:
            print(f"  {issue}", file=err)
        if not args.quiet:
            for issue in validation.issues:
                if not issue.is_failure:
                    print(f"  {issue}", file=err)
        if validation.failures:
            status = 1
    return result.document, status


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    err = sys.stderr
    profile = _resolve_profile(args)

    if args.validate and profile.is_empty and not args.quiet:
        print(f"warning: no profile artifacts found"
              f"{' in ' + profile.source if profile.source else ''}; "
              f"pass --profile-dir or set {PROFILE_DIR_ENV}", file=err)

    many = len(args.files) > 1
    if many and args.output and args.output.suffix:
        print("error: --output must be a directory when several files are "
              "given", file=err)
        return 2

    status = 0
    for path in args.files:
        if not path.is_file():
            print(f"error: {path}: no such file", file=err)
            status = max(status, 2)
            continue
        try:
            document, file_status = _process(path, args, profile, err)
        except OSError as e:
            print(f"error: {path}: {e}", file=err)
            status = max(status, 2)
            continue
        status = max(status, file_status)

        text = json.dumps(document, indent=args.indent, ensure_ascii=False)
        if args.output is None:
            print(text)
        else:
            target = (args.output / f"{path.stem}.jsonld") if many or \
                args.output.is_dir() else args.output
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
            if not args.quiet:
                print(f"{path.name}: written to {target}", file=err)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
