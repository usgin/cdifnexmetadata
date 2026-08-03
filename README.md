# cdifnexmetadata

Extracts [CDIF 1.1](https://cross-domain-interoperability-framework.github.io/)
metadata from scientific data files and emits it as schema.org JSON-LD,
using the structure and conventions described inside the file itself.

Reads **NeXus-formatted HDF5** and **XDI** text today.

```bash
cdifnexmetadata scan.nxs spectrum.xdi -o metadata/
```

## Quickstart

From nothing to validated CDIF metadata:

```bash
git clone https://github.com/usgin/cdifnexmetadata
cd cdifnexmetadata
uv sync --all-extras          # --all-extras matters: see below
```

Convert a bundled example and print it:

```bash
uv run cdifnexmetadata exampleData/cu_metal_rt.nxs
```

Convert everything to a directory, NeXus and XDI together — the format
is detected from the file, not the extension:

```bash
uv run cdifnexmetadata exampleData/*.nxs exampleData/*.xdi -o metadata/
```

Validate against the CDIF XAS profile, which lives in another
repository:

```bash
git clone https://github.com/CDIF-4-XAS/XAS-CDIF -b cdifxasRelease1.1 ../XAS-CDIF
uv run cdifnexmetadata exampleData/cu_metal_rt.nxs --validate \
    --profile-dir ../XAS-CDIF/release
```

**`--all-extras` is not optional.** `pytest` and the validation
dependencies are declared as optional extras, so a bare `uv sync`
leaves a working library that cannot test or validate itself.

Run the tests with `uv run pytest`.

### What you should see

`exampleMetadata-NEXUS/` and `exampleMetadata-xdi/` hold the output this
repository generates from `exampleData/` and from the XAS-CDIF XDI
corpus respectively, so you can compare against them. Regenerate the
first with `uv run python exampleMetadata-NEXUS/generate.py`.

Not every example validates, on purpose. `cu_metal_10K.nxs` declares
`NXxas` and carries almost none of it, and `NXxas-manual-sketch.hdf5`
follows a layout from the NeXus manual that no real file uses. Both
fail, and the failures are the point — an examples directory where
everything passes tells you nothing about what happens when something
is wrong.

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
| `map/concepts.py` | 508 | the concept-keyed record; NeXus binding |
| `map/xdi.py` | 338 | XDI binding — dictionary lookup, not tree walking |
| `map/normalise.py` | 147 | free-text header values a producer wrote outside the dictionary |
| `map/legacy.py` | 114 | where non-standard writers actually put things |
| `emit.py` | 1173 | concept records to CDIF JSON-LD |
| `validate.py` | 462 | framing, JSON Schema, SHACL |
| `cli.py` | 234 | dispatch, batch, reporting, exit codes |

204 tests, 2 skipped. They run offline — fixtures are synthesised and
crosswalks written inline, so a failure means this code changed rather
than that upstream revised a mapping row.

## Adding a technique is a crosswalk

Drop an SSSOM TSV in `src/cdifnexmetadata/data/`. Selection reads the
application definition each file declares and picks the crosswalk that
covers it. No code change, no registration step.

Small-angle scattering was added exactly that way — 62 lines of TSV, and
nothing in the reader, mapper or emitter moved. A real `NXsas` beamline
file yields 22 concepts and 4 variables.

| bundled crosswalk | direction |
|---|---|
| `cdifxas-to-nexus.sssom.tsv` | CDIF XAS concept → NeXus path *(copied from upstream)* |
| `xdi-to-cdifxas.sssom.tsv` | XDI key → CDIF XAS concept *(copied from upstream)* |
| `cdifsas-to-nexus.sssom.tsv` | CDIF SAS concept → NeXus path |
| `legacy-paths.tsv` | writer conventions that diverge from the standard |

The first two are copies. Their master versions live in
[XAS-CDIF](https://github.com/smrgeoinfo/XAS-CDIF) and are built there
by `crosswalk/build_crosswalk.py`; the copies exist so this package
works offline and so a release is pinned to a known crosswalk revision.
Re-download them with
`python -m cdifnexmetadata.map.crosswalk --refresh`.

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

## Why these choices

The reasoning behind the decisions above, including what was rejected.

### Which NeXus definitions

`XraySpectroscopy/nexus_definitions`, pinned to a commit SHA, not
`nexusformat/definitions`. The XAS fork restructures `NXxas`
substantially: the monolithic application definition is gone, replaced
by one definition per detection mode — `NXxas_trans`, `NXxas_tfy`,
`NXxas_tey`, `NXxas_pey`, `NXxas_pfy`, `NXxas_herfd`. **Detection mode
*is* the application definition** there, which is why the crosswalk maps
it as `skos:narrowMatch` against the definition rather than to a field.

Those definitions are actively being revised, so the code is built to
survive it: all three definition directories are searched and none is
hardcoded, the resolved SHA is recorded, an unresolvable definition
degrades to a lower resolution tier rather than failing, NXDL is parsed
permissively (unknown elements ignored, never rejected), and no
structural assumption is made beyond the NXDL grammar — the new `NXxas`
does *not* put energy under `NXmonochromator`.

### `NeXusOntology` — referenced, not depended on

It would have been the obvious source of concept IRIs. It is not usable:
the PURLs do not resolve, there is no licence, it has been stale about
two years, and an open PR renames every IRI. Concepts are minted under
`https://w3id.org/cdif/xas/` instead, on the mint-now-redirect-later
pattern, so they can be redirected if an official vocabulary appears.

Relatedly, `nxs:` is bound to `https://manual.nexusformat.org/classes/`
provisionally, and naive concatenation does not resolve — `nxs:NXentry`
404s. Only the two-segment forms work, so a dereferenceable link is
built as `nxs:applications/NXxas.html`.

### Why the signal needs four tiers

`FeXAS.nxs` declares `definition=NXxas` and carries **no `signal` or
`axes` attributes at all** — the NeXus-blessed way of saying which array
is plottable is simply absent. So resolution proceeds by: the `signal`
attribute where present; then soft-link targets, since `NXdata` links
like `data/energy → monochromator/energy` reveal the structure; then the
NXDL definition; then naming heuristics.

The tiers are **additive, not exclusive**. An early version returned at
the first success and so resolved `i0`/`ifluor`/`itrans` by link while
dropping `mutrans` and `mufluor` — the derived absorption coefficients,
which are the point of the measurement.

### Multi-entry files are an archive of parts

A NeXus file with N `NXentry` groups is one `schema:Dataset` with N
parts, by analogy with a zip bundle. Not hypothetical: `FeXAS.nxs` holds
26. Most part metadata is by reference — a scan series shares its
instrument, source, sample and data structure, so only what actually
varies per entry is stated on the part. Structural identity is decided
by comparing shapes and dtypes, and entries with matching signatures
share one structure object.

### Hazards deliberately not inherited

Two prior codebases were surveyed before starting. The patterns worth
keeping — import-guarded heavy dependencies, warnings accumulated on a
result rather than raised, per-profile validation rather than one
monolithic schema — are in the code. The ones deliberately avoided: a
single schema that grows until it describes nothing precisely, and
domain mappings hardcoded in Python where they cannot be revised without
a release.

### Still open

**Units** are passed through as the file writes them. QUDT/UCUM
normalisation is intended and not done, so `NX_LENGTH` from a definition
and `mm` from a real file both appear as `schema:unitText`.

**An `NXsubentry` declaring a different definition from its parent** is
not handled; it is rare and was tabled.

**Per-entry acquisition times, in a multi-entry file.** When a
measurement happened is a fact about the measuring, so it goes on the
`prov:wasGeneratedBy` activity as `schema:startTime`/`schema:endTime` —
not on the dataset as `schema:temporalCoverage`, which says what period
the data is *about*, and a spectrum is not about a period. An absorption
edge is a property of a material.

There is one activity per document, so a file holding many entries gets
one span covering all of them. `FeXAS.nxs` has 26 entries measured over
three days; the document now says 2020-08-10T09:18:48 to
2020-08-12T22:12:09 and no longer says which entry was which. Giving
each part its own `prov:wasGeneratedBy` would keep the per-entry times,
at the cost of 26 activity nodes repeating one instrument set. Not
decided.

**Processed-data profiles** — `NXxasproc` and the EXAFS analysis chain —
are out of scope. Nothing in the NeXus ecosystem has moved on EXAFS
since 2008, and processed data warrants its own profile rather than
being folded into a raw-data one.

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
cdifnexmetadata scan.nxs
cdifnexmetadata data/*.nxs data/*.xdi -o metadata/
```

Mixed techniques and formats need no per-file configuration.

See what was extracted, and what was looked for and not found. The
report goes to stderr, so stdout stays pipeable:

```bash
cdifnexmetadata scan.nxs --report
```

### Validating

The profile's schema, frame and SHACL shapes are **not bundled** — they
belong to the CDIF profile repositories and are versioned there.

```bash
cdifnexmetadata scan.nxs --validate --profile-dir ../XAS-CDIF/release
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

### Units

Two properties carry two different claims, and the difference matters to
anything trying to load the numbers:

| property | means | comes from |
|---|---|---|
| `schema:unitText` | what this file recorded | the `units` attribute on the dataset |
| `schema:unitCode` | what the concept is, as a QUDT IRI | `data/cdifxas-units.tsv`, generated from the glossary |

`unitCode` is written only where the file is silent, so a file-recorded
unit is never overridden by a vocabulary claim. Neither is written when
neither source knows -- an empty `unitText` would assert that the unit is
the empty string.

Over the examples: 11 variables get a unit from their file, 2 from their
concept, and 18 from neither. Thirteen of those 18 are detector
intensities, which are arbitrary counts rather than dimensionless and
should not be given a unit. Four are `absorptioncoefficient`, which
cannot be given one until the glossary settles whether that concept is
mu (inverse length, as its definition says) or mu*t (dimensionless, as
every file stores).

### Normalising what producers actually wrote

XDI headers are free text, and files say things the dictionary does not
allow. Four values are read rather than passed through — see
`map/normalise.py`:

| header | written as | emitted as |
|---|---|---|
| `Sample.temperature` | `room temperature`, `RT`, `ambient` | `295.0 K`, plus a conversion note |
| `Sample.temperature` | `10K` | `10 K` |
| `Scan.edge_energy` | `7112.` | `7112. units not reported` |
| `Scan.start_time`, `Scan.end_time` | `2016/07/05 18:29:20` | `2016-07-05T18:29:20` |

Each leaves the value alone when it does not recognise it. `10 K
(nominal)` stays whole rather than being truncated to the part that
parsed, and a date nothing can read stays as written for validation to
report — a conversion that invented a plausible value would hide the
defect it was meant to surface.

The qualitative temperatures are the only case that asserts something
the file does not say, so they are the only case that leaves a note.
It goes on `schema:description`:

> … Conversion notes: temperature reported as "room temperature".

Nobody measured 295 K. Without the note the record claims an instrument
reading it never made, and a consumer comparing temperatures across a
corpus cannot tell the stand-ins from the measurements.

These were ported from the RML pipeline, where they were worked out
against the same 55-file corpus.

### Where concepts actually sit in real files

The crosswalk states NeXus locations in NXDL terms
(`/ENTRY:NXentry/INSTRUMENT:NXinstrument/monochromator:NXmonochromator/energy`)
because it is a statement about the standard, checked against the live
NXDL. No file on disk looks like that: `resolve_mapping` matches each
`name:NXclass` segment against groups by their `NX_class` attribute, so
one crosswalk row lands on `/FeFoil.001/instrument/...` in one file and
`/entry/instrument/...` in another.

So the question "what path does this concept have in real data?" can
only be answered by reading real data:

    python tools/observed_paths.py

writes `docs/observed-nexus-paths.tsv` — every concept found in
`exampleData/`, the entry-relative path it was found at, whether that
path came from the crosswalk or a legacy convention, and the NXDL path
stated for it. Currently 40 concepts over 47 concept/path pairs, of
which **7 concepts appear at more than one path** — `beamline` is at
`/instrument/name` in standards-conforming files and at
`/instrument/source/beamline_name` in Athena/GSECARS ones.

## Worked examples

Two sets, kept apart because they answer different questions.

**`exampleData/` → `exampleMetadata-NEXUS/`** — eight source files and
the document generated from each, with a README in both. They span what
the extractor actually meets rather than only what it handles well: a
26-entry XAS file that validates against a full profile, a real SAS
beamline file that departs from its own declared definition, an XDI
file, a deliberately thin file that *fails*, and two techniques no
crosswalk covers yet.

```bash
python exampleMetadata-NEXUS/generate.py --profile-dir ../XAS-CDIF/release
```

**`exampleMetadata-xdi/`** — the 55 XDI files in
[`XAS-CDIF/exampleData`](https://github.com/smrgeoinfo/XAS-CDIF) run
through this pipeline, so the output can be compared against what the
production RML pipeline makes of the same bytes. Both sets validate
55/55; the interesting part is what each says where a file is silent.
See its README.

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

- [`docs/DESIGN-2026-07-27.md`](./docs/DESIGN-2026-07-27.md) — the
  original design record, written before the code existed. Archived: its
  conclusions are folded in above, and it is kept because what was
  expected beforehand is worth being able to compare against
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
