# AGENTS.md — hdf5metadata

Orientation for future Claude Code (or human) sessions.

## What this repo is

A Python tool that reads a NeXus-formatted HDF5 file and emits CDIF 1.1
schema.org JSON-LD describing it — core, discovery, dataDescription, and
dataStructure profiles, to the extent the file's own internal description
supports each.

**Read [`DESIGN.md`](./DESIGN.md) first.** It has the pipeline architecture,
the NeXus→CDIF mapping tables, the decisions already made (and why), the open
questions, and a survey of reusable prior art in two other repositories.

## Current state

Design phase. Repo has scaffolding + design docs; no extraction code yet.

## Key external references

| Thing | Where |
|-------|-------|
| CDIF profile schemas + SHACL | `Cross-Domain-Interoperability-Framework/metadataBuildingBlocks`, `_sources/profiles/` |
| Conformance detection | `CDIF/validation/detect_conformance.py` + `tools/FrameAndValidate.py --conformance` |
| NeXus base classes | <https://manual.nexusformat.org/classes/> |
| NeXus example files | <https://github.com/nexusformat/exampledata> |
| Golden-reference example | `Cross-Domain-Interoperability-Framework/profile-datastructure` → `examples/FeXAS/NEXUS-withDataStructureComponent.json` |

## Conventions

Carried forward deliberately from the prior codebases surveyed in DESIGN.md:

- **Stage boundary is sacred.** `inspect/` emits plain structural dicts with no
  CDIF vocabulary. `map/` holds all semantics. When CDIF vocabulary changes,
  only `map/` moves.
- **Import-guard heavy dependencies.** `h5py`, `numpy`, `pyshacl` missing
  produces a warning in the result, not an ImportError.
- **Accumulate, don't raise.** Non-fatal problems go into `warnings: list[str]`
  on the result object.
- **Detect conformance, don't assert it.** Emit a `dcterms:conformsTo` entry
  only when the content satisfies that profile.
- **Per-profile validation.** Never one monolithic schema — that approach was
  explicitly deprecated in the ADA project after it became unmaintainable.
- **Report gaps.** Follow `harvest_rda.py`'s pattern: end a run with a
  per-field report of what could *not* be populated. For an extraction tool
  that is the most useful output.

## Sentinel values

Aligned with the CDIF-XAS pipeline (`smrgeoinfo/cdif-xas`), so documents from
both tools read consistently:

- `"Missing"` — required text/name field the source didn't supply
- `"unknown"` — required numeric/enumerated field needing domain-expert input
- `<http://www.opengis.net/def/nil/OGC/0/missing>` — required URI-shape value

Prefer *omitting* an optional field over filling it with a sentinel. Sentinels
are for fields the profile requires.

## Gotchas already known

- **JSON-LD blank-node `@id`s (`_:b1`) fail plain-JSON validators** like
  Oxygen even though they're valid RDF. Materialize them as real IRIs
  (`ex:blank/b1`) before writing output.
- **URI-shape values must be `{"@id": ...}` objects**, not bare strings — CDIF
  SHACL enforces this on `schema:propertyID`, `schema:additionalType`,
  `dcterms:conformsTo`, `cdif:isDefinedBy_RepresentedVariable`, `cdif:uses`.
- **`schema:` prefix must expand to `http://schema.org/`**, not `https://`.
  The `https` variant is a different IRI and silently breaks framing.
- **CDIF SHACL requires an IV↔RV round trip**: every RepresentedVariable
  referenced by a DataStructureComponent needs an InstanceVariable pointing
  back at it via `cdif:uses`.
