# AGENTS.md — cdifnexmetadata

Orientation for future Claude Code (or human) sessions.

## What this repo is

A Python tool that reads a NeXus-formatted HDF5 file **or an XDI text
file** and emits CDIF 1.1
schema.org JSON-LD describing it — core, discovery, dataDescription, and
dataStructure profiles, to the extent the file's own internal description
supports each.

**Read [`docs/DESIGN-2026-07-27.md`](./docs/DESIGN-2026-07-27.md) first.** It has the pipeline architecture,
the NeXus→CDIF mapping tables, the decisions already made (and why), the open
questions, and a survey of reusable prior art in two other repositories.

## Current state

All four stages implemented and tested: 189 tests, 2 skipped.

- **Two input formats.** NeXus/HDF5 and XDI, dispatched on what the file
  declares.
- **Three crosswalks bundled** in `src/cdifnexmetadata/data/`:
  `cdifxas-to-nexus` and `xdi-to-cdifxas` (copies, mastered in XAS-CDIF),
  `cdifsas-to-nexus` (authored here). Plus `legacy-paths.tsv` for
  writers that diverge from the standard.
- **Worked examples** in `exampleData/` and `exampleMetadata/`, spanning
  XAS, SAS, an XDI file, and two techniques no crosswalk covers.
- **Validation** against JSON Schema + SHACL via `--profile-dir`.
  FeXAS.nxs and all 55 XDI files in the XAS-CDIF corpus validate clean
  against the strict `xasDocument` composite.

Not started: importing schema.org metadata, or a form for the CDIF core
properties neither format carries (creator, licence, identifiers).

## Key external references

| Thing | Where |
|-------|-------|
| CDIF profile schemas + SHACL | `Cross-Domain-Interoperability-Framework/metadataBuildingBlocks`, `_sources/profiles/` |
| Conformance detection | `CDIF/validation/detect_conformance.py` + `tools/FrameAndValidate.py --conformance` |
| NeXus base classes | <https://manual.nexusformat.org/classes/> |
| NeXus example files | <https://github.com/nexusformat/exampledata> |
| Golden-reference example | `Cross-Domain-Interoperability-Framework/profile-datastructure` → `examples/FeXAS/NEXUS-withDataStructureComponent.json` |

## Conventions

Carried forward deliberately from the prior codebases surveyed in docs/DESIGN-2026-07-27.md:

- **Stage boundary is sacred.** `inspect/` emits plain structural dicts with no
  CDIF vocabulary. `map/` holds all semantics. When CDIF vocabulary changes,
  only `map/` moves.
- **Two input bindings, one hub.** `inspect/nexus.py` + `map/concepts.py`
  read HDF5; `inspect/xdi.py` + `map/xdi.py` read XDI. Both produce the
  same `ConceptRecord`, keyed on concept URIs (`cdifxas:facility`), with
  the source location carried *beside* each value rather than encoded in
  the key. That is what lets a second format be a parser rather than a
  second pipeline — do not key the intermediate on anything
  format-specific.
- **Dispatch on what the file declares, never on its extension.** XDI
  announces itself on line 1; anything else is tried as HDF5. A `.txt`
  holding XDI is read as XDI.
- **Adding a technique is a crosswalk.** Drop an SSSOM TSV in
  `data/`; selection picks it up from the application definitions it
  covers. No code change, no registration step. NXsas was added this
  way.
- **Emission is the only place that knows CDIF.** `CONCEPT_SLOTS` in
  `emit.py` is deliberately Python, not a fourth TSV: "where does this
  concept go in a schema.org graph" involves nesting and
  cross-references a flat table cannot express.
- **`cdi:isStructuredBy` goes on the distribution.** The JSON Schema
  admits it only on a distribution item and the SHACL rule reaches it
  there. Each part references its structure by `@id`. Dataset level
  validates only because it is ignored.
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

## Units: two properties, two claims

`schema:unitText` is what **the file** recorded. `schema:unitCode` is
what **the concept** is, from the glossary, and is written only where the
file is silent. Never write an empty `unitText` -- that asserts the unit
IS the empty string, and a consumer cannot tell it from a unit nobody
recorded.

The split is what lets a machine agent distinguish three states it must
act on differently: the file says eV; the concept is dimensionless
(`unit:UNITLESS`, no format records this because to a physicist it goes
without saying); nobody knows. Detector intensities are the third case --
arbitrary counts, not dimensionless -- so they get nothing, deliberately.

The glossary's claims arrive as `data/cdifxas-units.tsv`, generated
upstream by `build_crosswalk.py`. Its absence is not an error.

## Sentinel values

Aligned with the CDIF-XAS pipeline (`smrgeoinfo/cdif-xas`), so documents from
both tools read consistently:

- `"Missing"` — required text/name field the source didn't supply
- `"unknown"` — required numeric/enumerated field needing domain-expert input
- `<http://www.opengis.net/def/nil/OGC/0/missing>` — required URI-shape value

Prefer *omitting* an optional field over filling it with a sentinel. Sentinels
are for fields the profile requires.

## Derivations, and why they are not crosswalk rows

Some concepts a profile requires are determined by a file without being
stated in it. These live in `map/xdi.py::_derive`, not in the crosswalk,
because a crosswalk row says "this header means this concept" and none of
these has a header. Each derived value records in its `note` where it
came from.

| concept | derived from |
|---|---|
| `probe` | the format — XDI describes X-ray absorption and nothing else |
| detection mode | which intensity columns are present |
| reflection plane | split out of `Mono.name` (`Si(311)` → `Si` + `3 1 1`) |
| d-spacing units | the XDI dictionary, which fixes the tag in Angstrom |

None of these appears as a field in any of the 272 files in the XAS Data
Library. A crosswalk row for one would map something that does not exist.

## Locators belong to the mapping, not the variable

The path a value came from is emitted as `cdif:locator` inside
`cdif:hasPhysicalMapping` on the DataStructureComponent, typed
`cdif:LocatorMapping`, with `cdif:formats_InstanceVariable` pointing back
at the variable. Do not move it onto the InstanceVariable: in DDI-CDI
physical position is a property of the mapping from bytes to meaning, and
the variable carries `propertyID` and `physicalDataType` instead.

Every variable in the corpus has one. If a count says otherwise, check
the test -- grepping for `"/entry/` misses files whose entry is named
something else, which is most of them.

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
