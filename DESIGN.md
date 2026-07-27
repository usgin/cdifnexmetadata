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

- **`nxs:` = `https://manual.nexusformat.org/classes/`** — provisional,
  pending a real NeXus concept namespace.
  **Known caveat:** naive concatenation does not resolve.
  `nxs:NXentry` → `.../classes/NXentry` → **404**; only the
  two-segment forms `.../classes/base_classes/NXentry.html` and
  `.../classes/applications/NXxas.html` resolve (verified
  2026-07-27). So when emitting a *dereferenceable* link (e.g.
  `schema:url` on a DefinedTerm), build the full two-segment URL;
  reserve the bare `nxs:NXfoo` compact form for identifier-only
  positions. Revisit when either the NeXusOntology PURLs are
  registered or a w3id namespace appears — neither exists today.
  Rationale for rejecting the NeXusOntology PURLs outright is in the
  ecosystem survey below.
- **HDF5 internal paths are locators, not indices.** Physical mapping
  uses `cdif:LocatorMapping` with `cdif:locator` =
  `/entry/instrument/detector/data`. (`cdif:TextMapping` +
  `cdif:index` is the tabular-text analog and does not apply here.)
- **A multi-`NXentry` file is modelled as an archive of parts.**
  See "Multi-entry files" below.
- **Units are normalized to QUDT/UCUM** where a confident mapping
  exists. See "Units" below.
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

## Multi-entry files

A NeXus file with N `NXentry` groups is **modelled as an archive of
parts**, by analogy with a zip bundle. Not hypothetical: `FeXAS.nxs`
holds 26 (`FeFoil.001` … a scan series). CDIF's
`cdifArchiveDistribution` pattern — a `schema:DataDownload` whose
`schema:hasPart` lists components — is the closest existing shape,
and this is a genuinely new case not covered by
`docs/kotahiWorkflow-design.md` §4.2–4.5, which assumes the parts are
separate byte streams rather than groups inside one container.

Shape:

- One `schema:Dataset` for the file, one `schema:distribution`
  (the HDF5 file itself).
- One part per `NXentry`, each carrying its own dataset-level
  metadata — title, times, sample, its own DataStructure.
- **Most part metadata is by reference, not repeated.** A scan series
  shares its instrument, source, sample, and *data structure*; only
  what actually varies per entry (title, start/end time, scan
  parameters) is stated on the part.
- The shared `cdi:DataStructure` is emitted once with a stable `@id`
  and referenced by every part whose layout matches it. Structural
  identity is decided by comparing the (signal, axes, shapes, dtypes)
  signature across entries; entries with matching signatures share
  one DataStructure object.

Open sub-questions to settle during implementation:

- Which entry supplies the file-level `schema:name`/`description` —
  the first, the one named by the root `@default` attribute, or a
  synthesized summary? Root `@default` is the NeXus-blessed pointer
  and should win when present. (`FeXAS.nxs` has `default='fefoil'`.)
- Whether a part is `schema:hasPart` on the Dataset or on the
  distribution. CDIF's archive pattern puts `hasPart` on the
  DataDownload, with parts as `schema:MediaObject` explicitly *not*
  also typed `DataDownload`. An `NXentry` is not separately
  retrievable, so MediaObject-without-DataDownload fits.
- Whether entries that differ structurally should split the file into
  multiple Datasets instead. Defer — needs a real heterogeneous
  example.

## Units

NeXus `units` attributes are free text (`"eV"`, `"counts"`,
`"Angstroms"`, `"mm"`). **Attempt QUDT/UCUM normalization**, and:

- Always keep the source string verbatim in `schema:unitText` — never
  lose what the file actually said.
- Add `schema:unitCode` (UCUM) and/or a QUDT unit IRI **only on a
  confident match**. No guessing; an unmatched unit is not an error.
- Record unmatched unit strings in `warnings` so the gap report shows
  which vocabulary entries are missing.
- The NXDL definitions additionally give a per-field *unit category*
  (`NX_ENERGY`, `NX_LENGTH`, `NX_TIME`, …; ~47 of them). That is
  coarser than a unit but machine-readable and free — use it to
  constrain/validate the normalization (a field declared `NX_ENERGY`
  whose units parse to a length is a red flag worth warning about).

## Tabled

- **`NXsubentry` with a different `definition` than its parent** —
  a single file claiming two application definitions. Real in
  multi-modal data, but we have no concrete example to reason from.
  Revisit when one turns up.

## NeXus ecosystem survey (2026-07-27)

Survey of the `nexusformat` GitHub organization for things to consume
rather than reinvent.

### Which definitions repo — **`XraySpectroscopy/nexus_definitions`**

For XAS work, use the XAS community's fork
<https://github.com/XraySpectroscopy/nexus_definitions> (branch
`main`) in preference to upstream. As of 2026-07-27 it is **44 commits
ahead / 1 behind** upstream and actively worked (last push
2026-07-08). The NeXusOntology generation scripts are intended to run
against these NXDL files.

**NXxas has been restructured there, not just edited.** The fork
deletes `applications/NXxas.nxdl.xml` (the old 127-line definition)
and adds, under `contributed_definitions/`:

| New definition | Lines | What it is |
|---|---:|---|
| `NXxas.nxdl.xml` | 145 | leaner base; delegates to new classes |
| `NXxas_trans.nxdl.xml` | 219 | transmission |
| `NXxas_tey.nxdl.xml` | 102 | total electron yield |
| `NXxas_tfy.nxdl.xml` | 100 | total fluorescence yield |
| `NXxas_pey.nxdl.xml` | 109 | partial electron yield |
| `NXxas_pfy.nxdl.xml` | 627 | partial fluorescence yield |
| `NXxas_herfd.nxdl.xml` | 636 | high-energy-resolution fluorescence detected |
| `NXelement.nxdl.xml` | 158 | supporting base class |
| `NXabsorption_edge.nxdl.xml` | 167 | supporting base class |
| `NXemission_line.nxdl.xml` | 632 | supporting base class |
| `NXauger_line.nxdl.xml` | 976 | supporting base class |

Two consequences for us:

1. **Detection mode is becoming the application definition.** Where
   the old NXxas had a `mode` field with an enumeration, the new
   scheme makes transmission / TEY / TFY / PFY / HERFD / PEY separate
   definitions. That maps *directly* onto
   `schema:measurementTechnique` — read `NXentry/definition` and you
   have the technique, no enumeration lookup needed.
2. **Definitions can live in `contributed_definitions/`, not just
   `applications/`.** The resolver must search both.

### Resilience requirements (these definitions are in flux)

The XAS definitions are actively being revised and may move or change
shape. The code must therefore:

- **Search all three directories** — `applications/`,
  `contributed_definitions/`, `base_classes/` — and never hardcode
  which one a definition lives in. NXxas has already moved once.
- **Never hardcode the list of application definitions.** Discover
  them by listing the directories at load time.
- **Pin a commit SHA by default, allow override.** Config takes a
  repo + ref; default to a known-good SHA so a mid-flight upstream
  change cannot silently alter our output. Make the pin trivially
  bumpable and record the resolved SHA in the output provenance.
- **Treat a missing or unparseable definition as a degraded tier, not
  a failure.** If `NXentry/definition` names something we cannot
  resolve, fall through to heuristics and record it in `warnings` —
  emit core+discovery rather than nothing.
- **Tolerate unknown NXDL constructs.** Parse defensively: unknown
  elements/attributes are ignored, not fatal. The NXDL grammar itself
  is stable but the definition content is not.
- **No structural assumptions beyond the NXDL grammar.** Read the
  tree the definition declares; do not assume, e.g., that XAS energy
  is always under `NXmonochromator` — the new NXxas does not put it
  there.

### `nexusformat/definitions` — **the upstream baseline**

The authoritative machine-readable standard. 142 base classes, 45
application definitions, 93 contributed definitions, as NXDL XML
(namespace `http://definition.nexusformat.org/nxdl/3.1`). Current
release `v2026.01`.

Why it matters to us:

- **Application definitions nest the full expected tree** —
  `NXentry → NXinstrument → NXmonochromator/energy` etc. That is a
  ready-made path map for HDF5 traversal, which we need precisely
  because real files often omit the in-file `signal`/`axes` hints
  (see the three-tier strategy below).
- `<enumeration>` elements give **controlled vocabularies for free**
  (e.g. XAS detection mode `Transmission` / `Fluorescence`) —
  directly usable as `schema:DefinedTerm` values.
- `<field>` carries `type` (`NX_FLOAT`, `NX_DATE_TIME`, …) and a
  `units` *category* (`NX_ENERGY`, …) → feeds
  `cdif:physicalDataType` and unit handling.
- `dev_tools/utils/nxdl_utils.py` has **h5py-aware** helpers
  (`get_hdf_root`, `get_best_child`, `get_nx_namefit`,
  `get_node_at_nxdl_path`, `get_inherited_nodes`) that resolve an
  h5py node against NXDL inheritance. Crib from this rather than
  writing name-matching from scratch.

Caveats: **no JSON / JSON Schema / RDF form exists** — parse the XML
(it's flat and stable; `lxml` is enough). The repo is **not
pip-installable**; consume by pinning a tag and vendoring the ~280
`*.nxdl.xml` files. **Licence is LGPL-3.0-or-later** — ship the
licence text if we vendor the XML. Deriving JSON-LD *output* from
them is not a derivative-work concern in practice.

### `nexusformat/NeXusOntology` — **reference, don't depend**

An OWL (RDF/XML) rendering of the definitions: 57 BaseClass, 34
Application, 1261 Field, 284 Attribute, 33 Units terms. Attractive in
principle — it would give us ready-made concept IRIs. **Rejected as an
identifier source on four grounds:**

1. **The PURLs are dead.** Base IRI is
   `http://purl.org/nexusformat/definitions/`; every term IRI 404s.
   The purl domain was never registered. Open issue #6 (2025-06-19),
   unanswered.
2. **The IRIs are about to change.** Open PR #8 (mergeable) rebuilds
   against definitions v2025.11 and **renames every IRI** to a flat
   hash namespace (`…/definitions#nxdata-signal-attribute`).
3. **No licence.** `license: null`, no LICENSE file — all rights
   reserved by default.
4. **Stale.** Pinned to definitions v2024.02; current is v2026.01.

Still useful as *reference*: it carries 2119 `rdfs:seeAlso` values
pointing at resolvable manual anchors, which is corroboration for the
"use manual URLs as identifiers" decision above.

**Expected to improve.** The XAS community's plan is to regenerate the
ontology with the scripts at
<https://github.com/nexusformat/NeXusOntology/tree/main/script> from
the NXDL in `XraySpectroscopy/nexus_definitions`. If that lands *and*
the PURLs get registered (or a w3id namespace appears) *and* a licence
is added, the ontology becomes the right identifier source and the
`nxs:`-manual-URL decision above should be revisited. Track it —
don't design around it yet.

### `pynxtools` (PyPI, FAIRmat) — **evaluate as an optional extra**

Apache-2.0, actively maintained (v0.15.0). Bundles the definitions as
a submodule and does **real HDF5-against-application-definition
validation** — i.e. it answers "does this file actually conform to
NXxas?", which is exactly our `measurementTechnique` conformance
question. Strongest off-the-shelf option. Heavy (NOMAD ecosystem), so
it belongs behind an optional extra, not in core dependencies.

### `nexusformat` (PyPI, NeXpy) — **considered, probably not**

Modified BSD, v2.0.2. High-level tree API (`nxload()`) that resolves
NXdata signal/axes for you. Tempting, but pulls in scipy, pygments,
colored, hdf5plugin for what amounts to tree navigation we can do in
~200 lines of h5py. Revisit if the traversal code gets unwieldy.

### Others — **not useful**

- `nexusformat/features` — NIAC "recipes": per-technique Python files
  keyed by opaque hex IDs, essentially hardcoded path→validator maps.
  Useful only as reference for which paths matter per technique.
- `nexusformat/cnxvalidate` — C validator; needs compilation
  (libxml2 + HDF5). `pynxtools` covers the same ground in Python.
- `nexusformat/python-nxs` — NAPI bindings, last touched 2020. h5py
  is strictly better.
- `nexusformat/w3id.org` — just a fork of `perma-id/w3id.org` with no
  NeXus-specific content. `w3id.org/nexusformat/*` does **not**
  resolve; NeXus has no w3id namespace.
- `nexusformat/code`, `communications`, `NIAC`, `wiki`,
  `hdf5xmp`, `HDF5-External-Filter-Plugins` — out of scope.

### Consequence: three-tier signal/axes resolution

Reality check on `FeXAS.nxs` (well-formed NeXus, written by
`xraylarch xdi2nexus`, `NX_class` on every group, `definition` =
`NXxas`): its `NXdata` group has **no `signal` and no `axes`
attribute** — only `NX_class`. The canonical convention is simply not
present.

So the extractor cannot rely on in-file hints alone:

1. **In-file attributes** — `NXdata@signal`, `@axes`, `@default`,
   and the older `@axis`/`@primary` field attributes. Use when present.
2. **NXdata link targets** — see below. Cheap, in-file, and present in
   real data where tier 1 is not.
3. **NXDL application definition** — read `NXentry/definition`, load
   the matching definition, and use its nested tree as the path map for
   which field is the measurement and which are coordinates. This is why
   we consume the definitions repo.
4. **Heuristics** — shape agreement (all 1-D arrays of equal length in
   one `NXdata`), name conventions, `units` presence. Last resort;
   record in `warnings` when used so output is honest about it.

### Tier 2 in detail: link targets carry the structure

Found while testing `inspect/hdf5.py` against `FeXAS.nxs`. Its `NXdata`
group has no `signal` and no `axes` — but it reaches the real arrays by
**soft link**, and the link targets say exactly what each one is:

```
/FeFoil.001/data/energy  ->  /FeFoil.001/instrument/monochromator/energy
/FeFoil.001/data/i0      ->  /FeFoil.001/instrument/i0/data
/FeFoil.001/data/itrans  ->  /FeFoil.001/instrument/itrans/data
```

A dataset linked in from `NXmonochromator/energy` is an axis; one linked
from an `NXdetector` is a measured channel. That is recoverable
structure, in the file, without consulting NXDL — so it belongs above the
definition lookup in the tier order.

**This is why `inspect/hdf5.py` records links per group.** `visititems`
does not follow soft links and visits each underlying object once, so a
naive walk loses these child names entirely: `/FeFoil.001/data/energy`
never appears as a dataset. Without the `links` map the whole `NXdata`
group would look empty apart from the two arrays physically stored in
it. Covered by `test_soft_links_are_recorded`.

NeXus also uses *hard* links with a `target` attribute naming the
canonical location; the inspector records those too, distinguishing an
alias from an original.

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
