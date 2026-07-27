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

Requires Python 3.11+ and `h5py`.

## Usage

```bash
hdf5metadata path/to/file.nxs -o metadata.jsonld
```

*(CLI not yet implemented — see DESIGN.md)*

## Related work

- [CDIF metadataBuildingBlocks](https://github.com/Cross-Domain-Interoperability-Framework/metadataBuildingBlocks) — profile schemas + SHACL this tool validates against
- [NeXus format documentation](https://manual.nexusformat.org/)
- [nexusformat/exampledata](https://github.com/nexusformat/exampledata) — test corpus

## License

Documentation and metadata content: [CC-BY-4.0](./LICENSES/CC-BY-4.0.txt).
See [`REUSE.toml`](./REUSE.toml) for per-file licensing.
