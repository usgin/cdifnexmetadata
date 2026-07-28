# hdf5metadata

Extracts [CDIF 1.1](https://cross-domain-interoperability-framework.github.io/)
metadata from scientific data files and emits it as schema.org JSON-LD,
using the structure and conventions described inside the file itself.

Reads **NeXus-formatted HDF5** and **XDI** text today.

```bash
hdf5metadata scan.nxs spectrum.xdi -o metadata/
```

## What it does, concretely

Given `FeXAS.nxs` — a 2.6 MB HDF5 container holding 26 X-ray absorption
spectra — it produces one `schema:Dataset` with 26 parts, 10 measured
variables, two shared data structures, a SHA-256 checksum, temporal
coverage, element and edge keywords and three peer instruments, and
declares five CDIF profiles. That document validates clean against the
`xasDocument` composite: JSON Schema and SHACL, zero violations.

Given a 30 KB XDI text file it produces the same shape of document from
a completely different input, through the same emitter.

## How it works

Four stages, with a hard boundary between structure and semantics.

```
inspect/    read the file          -> plain structural objects, no CDIF vocabulary
map/        apply a crosswalk      -> concept-keyed record, ALL semantics here
emit.py     assemble the document  -> CDIF JSON-LD, the only CDIF-aware module
validate.py check it               -> framing, JSON Schema, SHACL
```

### The concept hub

The intermediate is keyed on **concept URIs**, not on anything the input
format calls things:

```json
"cdifxas:monochromatortype": [{
  "value": "Si",
  "source_path": "#Mono.name",
  "predicate": "skos:closeMatch",
  "confidence": 0.8,
  "note": "XDI Mono.name conflates crystal material and reflection..."
}]
```

Where a value came from travels *beside* it — a path, an SSSOM predicate
and a confidence — rather than being encoded in the key. That is the one
decision the rest follows from: a second input format becomes a second
parser rather than a second pipeline, and one concept can carry values
from several places, each saying which.

### The modules

| module | lines | what it does |
|---|--:|---|
| `inspect/hdf5.py` | 469 | generic h5py walker; small values are metadata, large arrays are data |
| `inspect/nexus.py` | 618 | `NX_class` overlay; four-tier resolution of which array is the signal |
| `inspect/xdi.py` | 243 | XDI reader; sniffs `# XDI/…` on line 1 |
| `nxdl/repository.py` | 241 | fetches NXDL definitions, pinned to a commit SHA, cached |
| `nxdl/definition.py` | 366 | parses NXDL, resolves `extends` inheritance |
| `map/crosswalk.py` | 462 | SSSOM loading, NXDL path matching, crosswalk selection |
| `map/concepts.py` | 494 | the concept-keyed record; NeXus binding |
| `map/xdi.py` | 305 | XDI binding — dictionary lookup, not tree walking |
| `map/legacy.py` | 114 | where non-standard writers actually put things |
| `emit.py` | 992 | concept records to CDIF JSON-LD |
| `validate.py` | 462 | framing, JSON Schema, SHACL |
| `cli.py` | 234 | dispatch, batch, reporting, exit codes |

189 tests, 2 skipped. They run offline — fixtures are synthesised and
crosswalks written inline, so a failure means this code changed rather
than that upstream revised a mapping row.

## Adding a technique is a crosswalk

Drop an SSSOM TSV in `src/hdf5metadata/data/`. Selection reads the
application definition each file declares and picks the crosswalk that
covers it. No code change, no registration step.

Small-angle scattering was added exactly that way — 62 lines of TSV, and
nothing in the reader, mapper or emitter moved. A real `NXsas` beamline
file yields 22 concepts and 4 variables.

| bundled crosswalk | direction |
|---|---|
| `cdifxas-to-nexus.sssom.tsv` | CDIF XAS concept → NeXus path *(vendored)* |
| `xdi-to-cdifxas.sssom.tsv` | XDI key → CDIF XAS concept *(vendored)* |
| `cdifsas-to-nexus.sssom.tsv` | CDIF SAS concept → NeXus path |
| `legacy-paths.tsv` | writer conventions that diverge from the standard |

The two vendored files are copies from
[XAS-CDIF](https://github.com/smrgeoinfo/XAS-CDIF); refresh with
`python -m hdf5metadata.map.crosswalk --refresh`.

## Adding an input format is a parser

XDI support is `inspect/xdi.py` plus `map/xdi.py`. Emission, profile
detection, validation and the CLI are untouched shared code.

It needed almost none of the NeXus machinery: HDF5 is a tree, so finding
a value means walking it by class; XDI is a dictionary, so concepts come
out by lookup and `map/crosswalk.py` is not involved at all. Two formats
this different converge because they are asked the same question, not
because they are read the same way.

Dispatch is on **what the file declares**, never its extension — a
`.txt` holding XDI is read as XDI.

## Decisions worth knowing

**Arrays are variables; scalars are context.** A concept recorded as an
array was *measured*, so it becomes a `schema:variableMeasured` and a
data-structure component. A scalar describes the *conditions*, so it
lands on an instrument, the sample, or the acquisition event. That one
distinction drives most of the layout.

**Measured arrays are never read.** Shape and dtype answer "is this
concept present, and what shape", which is what the data-structure
profile needs. The numbers are data.

**HDF5 paths are locators, not indices.** Physical mapping uses
`cdif:LocatorMapping` with `cdif:locator` = `/entry/instrument/i0/data`.

**Structures sit on the distribution.** The JSON Schema admits
`cdi:isStructuredBy` only on a distribution item. Each is inline with
its components; each part references the one it uses by `@id`. A file
with 26 entries and two layouts can then say which entry has which.

**Conformance is detected, not asserted.** A profile is claimed only
where the content for it exists. A file with no measured arrays gets
core and discovery and does not claim `data_description`.

**Nothing is silently dropped.** A concept with no CDIF binding is
emitted as `additionalProperty` with a warning. An unmapped data column
still becomes a variable, carrying the OGC nil URI as its `propertyID`.

**Some concepts are derived, and say so.** The probe, the detection
mode, the reflection plane and the d-spacing unit are determined by an
XDI file without being stated in it. Each derived value records in its
`note` where it came from.

**Sentinels where a profile requires what a file omits** — `unknown`, or
the OGC nil URI, with a description saying it was not recorded. A
missing source type becomes `Synchrotron X-ray Source` only where the
file declares XAS; an `NXtomo` file may well have been measured at a
synchrotron, but nothing in it says so.

## Installation

```bash
pip install -e .
```

Python 3.11+ and `h5py`. To validate as well as produce:

```bash
pip install -e ".[validate]"
```

## Usage

One file to stdout, or many to a directory:

```bash
hdf5metadata scan.nxs
hdf5metadata data/*.nxs data/*.xdi -o metadata/
```

Mixed techniques and formats need no per-file configuration.

See what was extracted, and what was looked for and not found. The
report goes to stderr, so stdout stays pipeable:

```bash
hdf5metadata scan.nxs --report
```

### Validating

The profile's schema, frame and SHACL shapes are **not bundled** — they
belong to the CDIF profile repositories and are versioned there.

```bash
hdf5metadata scan.nxs --validate --profile-dir ../XAS-CDIF/release
```

or set `HDF5METADATA_PROFILE_DIR`. Without either, validation reports
itself **skipped** rather than passing: a run that checked nothing must
not read like a run that found nothing wrong. A missing optional
dependency is likewise a skip, never a pass.

Exit codes suit a pipeline: `0` succeeded, `1` failed validation, `2`
unreadable file.

### Non-standard file layouts

Writers that predate or diverge from the standard put things elsewhere —
the Athena/GSECARS writer uses `NXscan` and `NXxrayedge`, neither a NeXus
base class. Those locations live in `data/legacy-paths.tsv`, consulted
only for concepts the standards crosswalk did not find, and never
overriding a standards-based value. Recovered values carry a
`convention` marker. Pass `--no-legacy` to use standard paths only.

## Worked examples

`exampleData/` holds eight source files and `exampleMetadata/` the
document generated from each, with a README in both. They span what the
extractor actually meets rather than only what it handles well: a
26-entry XAS file that validates against a full profile, a real SAS
beamline file that departs from its own declared definition, an XDI
file, a deliberately thin file that *fails*, and two techniques no
crosswalk covers yet.

```bash
python exampleMetadata/generate.py --profile-dir ../XAS-CDIF/release
```

## Not yet done

- Importing schema.org metadata, or a form, for the CDIF core properties
  neither format carries — creator, licence, identifiers. Every document
  currently emits placeholders, with identifiers under
  `https://w3id.org/cdif/testing/`.
- QUDT/UCUM unit normalisation.
- Technique-neutral concepts (facility, beamline, probe, temperature)
  still sit in the `cdifxas:` namespace because that crosswalk was
  written first. They belong somewhere neutral before a third domain
  arrives.

## Related work

- [`DESIGN.md`](./DESIGN.md) — the reasoning behind these decisions, the
  ecosystem survey, and the prior art the design drew on
- [`STATUS.md`](./STATUS.md) — state of play across this and the CDIF XAS
  vocabulary effort
- [`AGENTS.md`](./AGENTS.md) — conventions and known gotchas
- [`docs/NXsas.md`](./docs/NXsas.md) — what NXsas is, and how real files
  depart from it
- [CDIF metadataBuildingBlocks](https://github.com/Cross-Domain-Interoperability-Framework/metadataBuildingBlocks)
  — the profile schemas and SHACL this tool validates against
- [NeXus format documentation](https://manual.nexusformat.org/)
- [nexusformat/exampledata](https://github.com/nexusformat/exampledata)
  — where most of `exampleData/` came from

## License

Documentation and metadata content: [CC-BY-4.0](./LICENSES/CC-BY-4.0.txt).
See [`REUSE.toml`](./REUSE.toml) for per-file licensing.
