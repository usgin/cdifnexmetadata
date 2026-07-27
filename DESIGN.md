# hdf5metadata — design

**Status: proposed, not yet implemented.** 2026-07-27.

## Goal

Given a NeXus-formatted HDF5 file, extract as much CDIF 1.1 metadata as the
file's own internal description supports — core, discovery, dataDescription,
dataStructure — and emit it as schema.org JSON-LD.

Design constraint: **detect, don't assert.** The tool declares conformance to a
CDIF profile only when the extracted content actually satisfies that profile.
A file with no `NXdata` group gets no dataDescription claim.

## Pipeline

Three stages with a hard boundary between structure and semantics. This split
is inherited from working code in `amds-ldeo/metadata` (see Prior Art) and is
the main reason that code is reusable.

```
Stage 1  INSPECT    walk the file; emit a plain dict of structure.
                    NO CDIF vocabulary at this layer.
                    inspect/hdf5.py   — generic h5py walker
                    inspect/nexus.py  — NX_class-aware overlay

Stage 2  MAP        inspection dict -> CDIF JSON-LD fragments.
                    ALL semantics live here.
                    map/core.py · discovery.py
                       · data_description.py · data_structure.py

Stage 3  EMIT +     assemble fragments, add @context, detect which profiles
         VALIDATE   are satisfied, write schema:subjectOf/dcterms:conformsTo,
                    validate against per-profile JSON Schema + SHACL.
                    emit.py · validate.py
```

Rationale for the boundary: stage 1 is testable against real files without any
CDIF knowledge; stage 2 is pure functions over dicts, testable without I/O.
When CDIF vocabulary changes (as it did between 1.0 and 1.1), only stage 2 moves.

## Proposed layout

```
src/hdf5metadata/
    inspect/
        hdf5.py          generic walker: groups, datasets, shapes,
                         dtypes, attrs, optional min/max
        nexus.py         NX_class dispatch; NXentry/NXdata/NXinstrument/
                         NXsample/NXdetector/NXsource traversal;
                         signal/axes resolution
        sniff.py         is this NeXus or plain HDF5?
    map/
        core.py          identifier, name, description, license,
                         distribution (contentUrl, encodingFormat,
                         contentSize, spdx:checksum)
        discovery.py     keywords, measurementTechnique, creator,
                         temporal coverage, instrument/facility
        data_description.py   variableMeasured -> cdi:InstanceVariable
        data_structure.py     cdi:isStructuredBy -> DataStructure +
                              components + cdif:hasPhysicalMapping
    context.py           the @context prefix block
    emit.py              assemble + conformsTo detection
    validate.py          per-profile JSON Schema + SHACL
    cli.py               hdf5metadata <file> [-o out.jsonld] [--profile ...]
tests/
    data/                FeXAS.nxs + golden reference JSON-LD
    test_*.py
```

## NeXus → CDIF mapping

### core/1.1

| CDIF | NeXus source |
|------|--------------|
| `schema:name` | `NXentry/title`, else filename stem |
| `schema:identifier` | `NXentry/entry_identifier`, else none |
| `schema:description` | `NXentry/experiment_description` / `NXnote` |
| `schema:distribution[].schema:contentSize` | file size |
| `schema:distribution[].spdx:checksum` | computed SHA-256 |
| `schema:distribution[].schema:encodingFormat` | `application/x-hdf5` |
| `schema:distribution[].dcterms:conformsTo` | `{@id: nxs:...}` for the NeXus application definition, when `NXentry/definition` is present |

### discovery/1.1

| CDIF | NeXus source |
|------|--------------|
| `schema:temporalCoverage` | `NXentry/start_time` .. `end_time` |
| `schema:measurementTechnique` | `NXentry/definition` (e.g. `NXxas`, `NXsas`, `NXmx`) as a `schema:DefinedTerm` in the NeXus definitions vocabulary |
| `schema:creator` / `schema:contributor` | `NXuser` groups (name, email, facility_user_id, ORCID if present) |
| instrument / facility | `NXinstrument/name`, `NXsource/name` + `NXsource/type` + `NXsource/probe` |
| `schema:keywords` | derived from `definition`, `NXsource/probe`, `NXsample/chemical_formula` |

### data_description/1.1

Driven by `NXdata`. The group's `signal` attribute names the dependent
variable; `axes` names the coordinate arrays in dimension order.

Each becomes a `schema:variableMeasured` entry dual-typed
`["cdi:InstanceVariable", "schema:PropertyValue"]` with:

- `schema:name` — dataset name
- `schema:description` — `long_name` attribute
- `schema:unitText` — `units` attribute (NeXus convention: on the field)
- `cdif:physicalDataType` — from HDF5 dtype
- `schema:minValue` / `schema:maxValue` — computed, size-guarded
- `cdif:uses` — `@id` reference to the RepresentedVariable the corresponding
  DataStructureComponent defines (the round-trip CDIF SHACL requires)

### data_structure/1.1

`cdi:isStructuredBy` → `cdi:DimensionalDataStructure` (NeXus data is
n-dimensional by nature) with:

- one `cdi:MeasureComponent` per `signal` dataset
- one `cdi:DimensionComponent` per axis, in `axes` order
- each component `cdif:isDefinedBy_RepresentedVariable` → a stable `@id`

**Physical mapping:** the HDF5 internal path (`/entry/instrument/detector/data`)
is a *locator*, not a column index — so `cdif:LocatorMapping` is the right
mapping subclass, with the HDF5 path as `cdif:locator`. (`cdif:TextMapping`
with `cdif:index` is the tabular-text analog.) This is a natural fit and worth
confirming against the CDIF data-structure implementation guide.

## Decisions made

- **`nxs:` namespace** = `https://manual.nexusformat.org/classes/`.
  Prior art disagrees — `adaMetadata-frame-v1.jsonld` uses this one,
  `yaml_to_jsonld.py` uses `http://purl.org/nexusformat/definitions/`.
  Picking the one that actually dereferences to documentation.
- **Detect conformance, don't assert it.** Follows
  `CDIF/validation/detect_conformance.py`, which derives profile conformance
  from content via SPARQL ASK + per-class SHACL.
- **Per-profile validation, not one monolithic schema.** Explicit convention
  in the ADA project after their monolithic v3 schema was deprecated.
- **Optional heavy dependencies are import-guarded.** `h5py`/`numpy` missing
  produces a warning in the result, not a crash. Established pattern in both
  prior codebases.
- **Non-fatal problems accumulate in `warnings: list[str]`** on the result
  rather than raising. Same.

## Open questions

- Does a NeXus file with multiple `NXentry` groups become one CDIF Dataset with
  multiple distributions, or multiple Datasets? (Affects the core mapping.)
  `docs/kotahiWorkflow-design.md` §4.2–4.5 in the ADA repo discusses CDIF's
  single-file vs collection vs archive-bundle distribution shapes — read before
  deciding.
- Unit strings: NeXus `units` values are free text (`"eV"`, `"counts"`,
  `"mm"`). Emit as `schema:unitText` only, or attempt QUDT/UCUM normalization?
  No prior art parses units — everything copies the raw string.
- `NXsubentry` with a different `definition` than its parent `NXentry` — a
  single file claiming two application definitions. Real in multi-modal data.

## Prior art surveyed (2026-07-27)

### `C:\GithubC\amds-ldeo\metadata`

| Path | Use |
|------|-----|
| `ada_metadata_forms/bundle_ingestion/services/emd_inspector.py` | **The blueprint.** EMD is HDF5 + a group-type attribute convention — structurally identical to NeXus's `NX_class`. Dispatches on `emd_group_type`, pairs data datasets with sibling `dim1..dimN` axis datasets, handles linear-vs-explicit axis calibration, recursive metadata-group extraction, heuristic fallback when the marker attribute is absent. Copy the *pattern*. |
| `.../services/hdf5_inspector.py` | Generic `visititems` walker with dataclass results and numpy→JSON coercion. Lift as stage-1 skeleton. |
| `.../services/inspector_to_jsonld.py` | Stage-2 emission layer. Already produces `cdi:DimensionalDataStructure` + Measure/Dimension components. **Predicates are stale** (`cdi:has`, `cdi:cubepath`, `cdi:isDefinedBy_InstanceVariable`) — must be updated to CDIF 1.1 (`cdif:isDefinedBy_RepresentedVariable`, `cdif:hasPhysicalMapping`). |
| `.../services/netcdf_inspector.py` | CF `standard_name`/`units`/dimension handling — closest existing analog to NeXus axis semantics. |
| `core/services/schema_registry.py` | Profile detection preferring the most specific declared profile; schema caching. |
| `validate-ada-v3.py` | Standalone validator with readable `anyOf`-aware error formatting. |
| `docs/kotahiWorkflow-design.md` §4.2–4.5 | CDIF distribution shapes: `cdifArchiveDistribution`, `cdifTabularData`/`cdifDataCube` as internal-structure extension points. **Read before finalizing the core mapping.** |
| `tools/harvest_rda.py` | The only CDIF-targeting code there; note its per-field coverage/gap report at the end — a good pattern for reporting what the extractor *couldn't* fill. |

### `C:\GithubC\smrgeoinfo\IEDADataSubmission`

| Path | Use |
|------|-----|
| `dspback-django/ada_bridge/inspectors.py` | Second HDF5 inspector — `inspect_hdf5` + `_collect_hdf5_vars` + `_get_hdf5_attr`. Cleaner uniform result contract than the amds-ldeo one; guarded min/max with `np.isfinite` masking. Merge the best of both. |
| `dspback-django/ada_bridge/bundle_service.py` | Extension-set dispatch + manifest envelope. Note: extension-only, no content sniffing — we need content sniffing to tell NeXus from plain HDF5. |
| `dspback-django/records/validators.py` | 28 lines; draft-aware (`$schema` → Draft7 vs Draft202012), errors sorted by path, flat `"path: message"` output. Copy-paste ready. |
| `dspback-django/records/profile_detection.py` | Three-tier cascade: `dcterms:conformsTo` → `schema:additionalType` → `measurementTechnique.termCode`. Structure reusable; its ~90 `ada:*` mappings are not. |
| `dspback/utils/jsonld/formatter.py` | spatial/temporal coverage → GeoJSON, if discovery needs it. |

### Hazards not to inherit

- The EMD inspector is **duplicated and drifted** between `emd_inspector.py` and
  `specialized_inspectors.py` (the latter adds `_parse_legacy_emd`). Don't copy
  both.
- The `nxs:` prefix resolves to **two different URIs** across the ADA codebase.
  Resolved above.
- Neither codebase executes JSON-LD framing (`pyld`) or SHACL (`pyshacl`) —
  a frame file exists in ADA but nothing applies it. We add both.

## Test strategy

1. **Golden-reference test.** `FeXAS.nxs` (2.7 MB, real XAS from a Diamond
   beamline) has a hand-authored CDIF JSON-LD companion at
   `Cross-Domain-Interoperability-Framework/profile-datastructure` →
   `examples/FeXAS/NEXUS-withDataStructureComponent.json`. Diff generated
   output against it; the delta is the specification of remaining work.
2. **Validation gate.** Every emitted document must pass JSON Schema + SHACL
   for each profile it claims. Reuse
   `CDIF/validation/tools/FrameAndValidate.py --conformance`.
3. **Breadth corpus.** `nexusformat/exampledata` (48 MB; ANSTO, APS, DLS,
   IPNS, SLS, Soleil, SwissFEL). Heavy on MX/SAS/diffraction, light on
   spectroscopy — good for proving *general-purpose* rather than
   XAS-specific. Report coverage per file rather than pass/fail; a file that
   yields only core+discovery is a correct result, not a failure.
