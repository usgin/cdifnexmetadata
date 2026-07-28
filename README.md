# hdf5metadata

Extract [CDIF](https://cross-domain-interoperability-framework.github.io/) metadata
from [NeXus](https://manual.nexusformat.org/)-formatted HDF5 files, using the
structure and conventions described inside the file itself.

**Status: design phase.** No extraction code yet — see
[`DESIGN.md`](./DESIGN.md) for the planned architecture and
[`AGENTS.md`](./AGENTS.md) for orientation.

## What it does

Given a NeXus HDF5 file (`.nxs`, `.h5`, `.hdf5`), emit a schema.org JSON-LD
document declaring conformance to as many CDIF 1.1 profiles as the file's
content supports:

| Profile | What NeXus supplies |
|---------|---------------------|
| [`core/1.1`](https://w3id.org/cdif/core/1.1) | file size, checksum, MIME type, title, identifier, description |
| [`discovery/1.1`](https://w3id.org/cdif/discovery/1.1) | `start_time`/`end_time` → temporal coverage; `NXsource`/`NXinstrument` names; `NXuser` → creator; `NXentry/definition` → `schema:measurementTechnique` |
| [`data_description/1.1`](https://w3id.org/cdif/data_description/1.1) | `NXdata` signal + axes → `schema:variableMeasured` with units and long names |
| [`data_structure/1.1`](https://w3id.org/cdif/data_structure/1.1) | dataset shapes / dtypes / internal HDF5 paths → `cdi:DimensionalDataStructure` with Measure and Dimension components |

Conformance is **detected from content, not asserted** — the emitted
`schema:subjectOf/dcterms:conformsTo` declares only the profiles the extracted
metadata actually satisfies.

## Why NeXus specifically

NeXus is HDF5 plus a self-describing convention: every group carries an
`NX_class` attribute naming its base class (`NXentry`, `NXinstrument`,
`NXsample`, `NXdata`, `NXdetector`, `NXsource`, …), and `NXdata` groups carry
`signal` / `axes` attributes identifying which array is the measurement and
which are its coordinates. That convention is exactly the information CDIF's
data-description and data-structure profiles need, so a well-formed NeXus file
can be mapped with very little guessing.

Files that are plain HDF5 with no `NX_class` markers still get core-profile
metadata plus a best-effort structural description.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+ and `h5py`. To check emitted documents against the
CDIF profiles as well as produce them:

```bash
pip install -e ".[validate]"
```

## Usage

```bash
hdf5metadata path/to/file.nxs -o metadata.jsonld
```

Describe several files at once, writing one document per file:

```bash
hdf5metadata data/*.nxs -o metadata/
```

The files need not be the same technique. The crosswalk is chosen from
the application definition each file declares — `NXxas` and its detection
modes, or `NXsas` — so a mixed folder needs no per-file configuration.
`--crosswalk` overrides that where you need it. A file declaring
something no crosswalk covers still yields the base-class concepts
(facility, beamline, probe, sample temperature) and says in a warning
that the technique-specific ones are missing.

See what was extracted, and — as importantly — what was looked for and
not found. The report goes to stderr, so the document on stdout stays
pipeable:

```bash
hdf5metadata scan.nxs --report
```

### Worked examples

`exampleData/` holds five NeXus files and `exampleMetadata/` the CDIF
document generated from each, with a README in both. They span what the
extractor actually meets rather than only what it handles well: a
26-entry XAS file that validates against a full CDIF profile, a real SAS
beamline file that departs from its own declared definition, the
definition-generated counterpart for contrast, and two techniques no
crosswalk covers yet — included so the gap can be read directly instead
of inferred.

```bash
python exampleMetadata/generate.py --profile-dir ../XAS-CDIF/release
```

### Validating

Validation needs the profile's schema, frame and SHACL shapes. They are
**not bundled**: they belong to the CDIF profile repositories and are
versioned there, so vendoring them would pin this package to a snapshot
and invite the copies to drift. Point at a release directory instead:

```bash
hdf5metadata scan.nxs --validate --profile-dir ../XAS-CDIF/release
```

or set `HDF5METADATA_PROFILE_DIR` once for the environment. Without
either, validation reports itself **skipped** rather than passing — a run
that checked nothing must not read like a run that found nothing wrong.

Exit codes suit a pipeline: `0` when everything asked for succeeded, `1`
when a document failed validation, `2` when a file could not be read.

### What conformance is claimed

`dcterms:conformsTo` is written per profile only where the content for
that profile is actually present — detected, not asserted. A file with no
measured arrays gets core and discovery and does not claim
`data_description`.

### Non-standard file layouts

Writers that predate or diverge from the NeXus standard put things in
their own places: the Athena/GSECARS writer uses `NXscan` and
`NXxrayedge`, neither a NeXus base class, and hangs ~20 `beamline_*`
fields off `NXsource`. Those locations live in
`src/hdf5metadata/data/legacy-paths.tsv`, consulted only for concepts the
standard crosswalk did not find, and never overriding a standards-based
value. Values recovered that way carry a `convention` marker so a
consumer can tell. Pass `--no-legacy` to use standard paths only.

## Related work

- [CDIF metadataBuildingBlocks](https://github.com/Cross-Domain-Interoperability-Framework/metadataBuildingBlocks) — profile schemas + SHACL this tool validates against
- [NeXus format documentation](https://manual.nexusformat.org/)
- [nexusformat/exampledata](https://github.com/nexusformat/exampledata) — test corpus

## License

Documentation and metadata content: [CC-BY-4.0](./LICENSES/CC-BY-4.0.txt).
See [`REUSE.toml`](./REUSE.toml) for per-file licensing.
