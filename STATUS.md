# STATUS — consolidated state of play

**Last updated:** 2026-07-27

This file exists so that *any* surface — another Claude Code session, a
claude.ai chat, a human — can pick up cold without re-deriving what has
already been established. It is the canonical entry point.

Stable URL:
<https://github.com/usgin/cdifnexmetadata/blob/main/STATUS.md>
Raw:
<https://raw.githubusercontent.com/usgin/cdifnexmetadata/main/STATUS.md>

> **If you are an agent reading this:** the section
> [Established — do not re-derive](#established--do-not-re-derive) is the
> important one. Every item there was verified empirically and several
> contradict the obvious assumption.

---

## Architecture: concept-keyed, with XDI and NeXus as two bindings

The organising decision, and the reason the efforts below converge.

A **concept hub** — the CDIF XAS SKOS glossary at
`https://w3id.org/cdif/xas/` — is the semantic centre. XDI tokens and
NeXus/NXDL paths are two *bindings* onto it. CDIF JSON-LD is the
serialization of it. This collapses the "should the crosswalk be
XDI-keyed or NeXus-keyed" question: it is neither, it is concept-keyed.

Four layers:

| Layer | Artifact |
|---|---|
| 1. Concept register | CDIF XAS glossary + minted `xdi:` terms for genuine gaps |
| 2. Alignment | **SSSOM** TSV — `xdi:token → skos:exactMatch → xas:concept`, `xas:concept → nxs:path`. Carries `mapping_justification`, `confidence`, predicate precision (exact/close/broad), and makes gaps explicit |
| 3. Serialization | concept → CDIF JSON-LD path / pattern / cardinality |
| 4. Converters | parse a source into the concept-keyed intermediate, then one source-agnostic transform emits CDIF |

SSSOM maps term-to-term only — it cannot express nested JSON-LD
placement, cardinality, or transformation logic. So it is the
*correspondence* layer, not the whole crosswalk; layer 3 holds the rest.

**Consequence: adding a format is adding a parser, not a pipeline.**
`smrgeoinfo/cdif-xas`( a fork from https://github.com/UKDSResearch/cdif-xas) already runs this shape — a concept intermediate
(`resources/cdif_skos.json`) consumed by an RML transform
(`resources/mapping_dds.ttl`, ~40 TriplesMaps), then framed and
validated. Adding NeXus means writing a second parser that emits the
same intermediate; the RML, framing and validation stack downstream is
untouched.

**Known refactor:** that intermediate's keys are currently XDI-flavoured
(`cdi:Facility_name`, `cdi:Mono_d_spacing`) — concept and binding are
fused. For two clean bindings the keys should be canonical concept URIs,
with each parser doing source-field → concept-URI itself. That per-parser
lookup table *is* the SSSOM alignment.

---

## Efforts and how they relate

| | Effort A | Effort B | Effort C |
|---|---|---|---|
| **Goal** | NeXus HDF5 → CDIF | Align CDIF XAS vocabulary with NeXus | XDI → CDIF (Dataverse workflow) |
| **Repo** | `usgin/cdifnexmetadata` (`main`) | `smrgeoinfo/XAS-CDIF` (**`cdifxasRelease1.1`**) | `smrgeoinfo/cdif-xas` (`main`) |
| **State** | Design phase, no code | Analysis + enumeration import done | **Working — 37/37 valid** |
| **Head** | `0c02ebe` | `2fe64e1` | `b915c51` |
| **Layer** | 4 (NeXus binding) | 1–2 (concepts + alignment) | 3–4 (serialization + XDI binding) |

**A and C are the two bindings of the same architecture.** A is the
general-purpose NeXus extractor (any technique); C is XAS-specific with a
Dataverse round-trip. A should emit the concept-keyed intermediate so it
can feed C's existing transform rather than duplicating it — see
[`docs/DESIGN-2026-07-27.md`](./docs/DESIGN-2026-07-27.md).

B supplies the hub both depend on.

---

## Established — do not re-derive

Each verified against live sources on 2026-07-27. Several contradict the
obvious assumption, which is why they are listed rather than left
implicit.

### 1. Use the XAS fork of the NeXus definitions

`XraySpectroscopy/nexus_definitions@main`, **not**
`nexusformat/definitions`. The fork is 44 commits ahead / 1 behind,
actively worked (last push 2026-07-08), and the NeXusOntology generation
scripts are meant to run against it.

**NXxas there is restructured, not edited.**
`applications/NXxas.nxdl.xml` is **deleted**; eleven files are added
under `contributed_definitions/`: a thin abstract `NXxas` (145 lines),
per-detection-mode subclasses `NXxas_trans` / `_tey` / `_tfy` / `_pey` /
`_pfy` / `_herfd`, and four supporting base classes `NXelement`,
`NXabsorption_edge`, `NXemission_line`, `NXauger_line`. Three competing
designs were tried on branches; **inheritance won**.

Two consequences: **detection mode is now the application definition
itself** (read `NXentry/definition` and you have the technique — no
enumeration lookup), and **definitions can live in
`contributed_definitions/`**, so any resolver must search there too.

### 2. NeXusOntology is not usable as an identifier source

Tempting — an OWL rendering with ready-made concept IRIs. Rejected on
four independent grounds:

- **The PURLs are dead.** Every `http://purl.org/nexusformat/definitions/…`
  term IRI 404s; the domain was never registered. Open issue #6
  (2025-06-19), unanswered.
- **The IRIs are about to change.** Open PR #8 (mergeable) renames every
  IRI to a flat hash namespace.
- **No licence.** `license: null`, no LICENSE file — all rights reserved
  by default.
- **Two years stale** relative to the definitions it describes.

Still useful as corroboration: its 2119 `rdfs:seeAlso` values point at
the NeXus manual URLs we settled on instead.

### 3. `nxs:` prefix concatenation does not resolve

`nxs: https://manual.nexusformat.org/classes/` + `NXentry` →
`.../classes/NXentry` → **404**. Only the two-segment forms resolve:
`.../classes/base_classes/NXentry.html`,
`.../classes/applications/NXxas.html`.

The prefix is retained provisionally, but any *dereferenceable* position
(e.g. `schema:url`) must build the full two-segment URL. Both prior-art
codebases got this wrong, in different ways.

### 4. Crosswalk to NeXus base classes, not application definitions

An application definition says what a file of that technique *must*
contain; a base class defines what a thing of that kind *can* have. A
concept absent from `NXxas` is usually present on `NXsource`,
`NXcollimator`, `NXmirror`, `NXbeam`, `NXslit` or `NXsample`.

**This session got it wrong once** — a first pass compared the glossary
against `NXxas` alone and wrongly reported ~15 beamline/facility
concepts as having no NeXus home. Checking base classes reduced the
genuine gaps to eight. The correction is recorded in the analysis doc so
it is not repeated.

### 5. In-file `signal` / `axes` attributes cannot be relied on

Our primary test file `FeXAS.nxs` is valid NeXus by every other measure —
`NX_class` on every group, `definition = NXxas`, written by
`xraylarch xdi2nexus` — yet its `NXdata` group has **no `signal` and no
`axes` attribute**. The canonical convention is simply absent.

Resolution is therefore three-tier: in-file attributes when present →
the NXDL application definition's nested tree as a path map → shape and
name heuristics, with a `warning` recorded so output is honest about how
it was derived. Tier 2 is the reason to consume the definitions repo
rather than hardcode NeXus knowledge.

### 6. Multi-`NXentry` is normal, not an edge case

`FeXAS.nxs` holds **26** `NXentry` groups (a scan series). Any design
that assumes one entry per file is wrong.

### 7. The XAS uplift is finished, not in progress

`UPLIFT-INSTRUCTIONS.md` in `smrgeoinfo/cdif-xas` reads as a plan
addressed to a future session. It has been **executed** — tasks 1–16
applied, 37/37 of the test corpus fully valid against
`xasDocument/1.0` (JSON Schema + SHACL). Do not treat it as pending
work or assume a parallel session is mid-flight on it.

### 8. Corrections a from-first-principles reading tends to get wrong

Recorded because a parallel session independently made the first of
these, as did an earlier pass in this one:

- **"Beamline focusing, harmonic rejection, and sample physico-chemical
  conditions have no NXxas concept."** False, and it matters — acting on
  it means minting `xdi:` gap terms that duplicate existing NeXus
  vocabulary. These live on *base classes*: `focusing` →
  `NXmirror/bend_angle_x|y` (also `NXslit`, `NXaperture`);
  `harmonicrejection` → `NXmirror` (`type`, `coating_material`,
  `incident_angle`); sample conditions → `NXsample`. See
  [Established §4](#4-crosswalk-to-nexus-base-classes-not-application-definitions).
  Only **eight** concepts are genuinely unhomed (listed under Effort B).
- **"We must wait for official NeXus concept IRIs before finalising
  SSSOM subjects/objects."** Do not wait. The NeXusOntology PURLs are
  dead, an open PR renames every IRI, and the repo has no licence — see
  [Established §2](#2-nexusontology-is-not-usable-as-an-identifier-source).
  Use manual URLs now ([§3](#3-nxs-prefix-concatenation-does-not-resolve))
  and treat any future ontology IRI as a later `skos:exactMatch` row,
  which is exactly what SSSOM is for.
- **The XAS glossary changed on 2026-07-27** (`XAS_Glossary_SKOS_v2.json`,
  named `XAS_Glossary_SKOS.json` at the time) — 89 → 90
  concepts (added `emissionline`; it has since grown to **106**, and the
  released filename is now `XAS_Glossary_SKOS_v2.json`), and `edgeanalyzed` /
  `xasmeasurementmode` gained `dc:references` to three new value-list
  schemes. Anchor SSSOM on the current file, and use the imported
  enumerations (39 edges, 432 emission lines, 7 detection modes) as
  object IRIs rather than free text.

---

## Decisions

| Decision | Note |
|---|---|
| `cdif:LocatorMapping` + `cdif:locator` for HDF5 internal paths | A path is a locator, not a column index; `cdif:TextMapping` + `cdif:index` is the tabular analog and does not apply |
| Multi-entry file = **archive of parts**, one part per `NXentry` | Shared metadata by reference, not repeated; one `cdi:DataStructure` per distinct (signal, axes, shapes, dtypes) signature, referenced by every matching entry |
| Units → **QUDT/UCUM normalization attempted** | Source string always kept verbatim in `schema:unitText`; codes added only on confident match; unmatched recorded in warnings |
| **Detect** conformance, don't assert it | Emit a `dcterms:conformsTo` entry only when content satisfies that profile |
| Per-profile validation, never one monolithic schema | Convention inherited from the ADA project after their monolithic schema was deprecated |
| `NXsubentry` with a definition differing from its parent | **Tabled** — needs a concrete example first |
| EXAFS analysis products out of scope for the crosswalk | 17 CDIF concepts (k, χ, χ(R), Fourier-filtered, normalized) have zero NeXus counterpart; `NXxasproc` is byte-identical to upstream and untouched since 2008. Processed data belongs in a separate profile |

### Sentinel conventions (shared across all three repos)

- `"Missing"` — required text/name field the source did not supply
- `"unknown"` — required numeric/enumerated field needing domain input
- `<http://www.opengis.net/def/nil/OGC/0/missing>` — required URI-shape value

Prefer *omitting* an optional field over filling it with a sentinel.

### JSON-LD hygiene (learned the hard way in `cdif-xas`)

- Blank-node `@id`s (`_:b1`) are valid RDF but **fail plain-JSON
  validators** like Oxygen — materialize as real IRIs
- URI-shape values must be `{"@id": …}` objects, not bare strings — CDIF
  SHACL enforces this on `schema:propertyID`, `schema:additionalType`,
  `dcterms:conformsTo`, `cdif:isDefinedBy_RepresentedVariable`,
  `cdif:uses`
- `schema:` must expand to `http://schema.org/`, **not** `https://` —
  the `https` variant is a different IRI and silently breaks framing
- CDIF SHACL requires an InstanceVariable ↔ RepresentedVariable round
  trip: every RV referenced by a DataStructureComponent needs an IV
  pointing back via `cdif:uses`

---

## Effort A — `usgin/cdifnexmetadata`

Public, CC-BY-4.0, Python ≥3.11. **Stages 1, 1b and 2 implemented and
tested (189 tests), and it now reads **two input formats**. **The
pipeline runs end to end**: `cdifnexmetadata
FeXAS.nxs --validate --profile-dir ../XAS-CDIF/release` emits a CDIF
document, validates it against the strict xasDocument composite, and
reports `validation PASSED` with five advisories, exit 0.**

Present: `inspect/hdf5.py`, `inspect/nexus.py`, `nxdl/`, `map/`,
copied SSSOM crosswalks and the legacy path table under
`src/cdifnexmetadata/data/`, `docs/DESIGN-2026-07-27.md`,
`AGENTS.md`, `README.md`, `pyproject.toml`, licence files, `.gitignore`.

Planned pipeline, with a hard boundary between structure and semantics
so that CDIF vocabulary changes touch only one layer:

```
inspect/   walk the file -> plain structural dicts, NO CDIF vocabulary
map/       dicts -> CDIF JSON-LD fragments, ALL semantics here
emit +     assemble, detect satisfied profiles, write conformsTo,
validate   validate per profile (JSON Schema + SHACL)
```

All four stages are done. **Next steps** are no longer pipeline
plumbing: broaden beyond XAS (a second crosswalk, no code change), add
more writer conventions to the legacy table as they turn up, and settle
the QUDT/UCUM unit normalisation docs/DESIGN-2026-07-27.md still lists as open.

### validate.py — framing is where documents actually break

The profiles are written against the *framed* document, so framing is
part of validation rather than a prelude to it. That matters: the flat
`prov:used` fixed earlier passed raw JSON Schema and vanished on
framing, because a frame drops what it does not declare.

Framing also damages a document in four ways that say nothing about its
content. Three are decidable without the schema — inserted `null`s, a
single-element `@type` compacted to a string, and IRI values compacted
against the document's own context so a conformance URI stops matching
the `const` it is checked against. The fourth, collapsed single-element
arrays, is **asked of the validator rather than guessed**: guessing by
name fails in both directions, since `schema:identifier` is an array on
an instrument and a string on the dataset. A `contains` failure reports
only that nothing matched, so its sub-schema is run against each item
explicitly — otherwise a collapsed array inside a `contains` is
invisible and the constraint reads as unsatisfiable when it is one
wrapper away.

The repair is safe in one direction only, which is the direction that
matters: framing only ever *removes* a wrapper, so restoring one cannot
contradict what the document said.

**Profile artifacts are not bundled.** They belong to the CDIF profile
repositories and are versioned there; copying them in would pin this
package to a
snapshot and invites drift. They are located at run time from
`--profile-dir` or `HDF5METADATA_PROFILE_DIR`. Absent, validation
reports itself **skipped** — a run that checked nothing must not read
like a run that found nothing wrong. A missing optional dependency is
likewise a skip, never a pass.

### emit.py, and what validating it surfaced

Reads two inputs because CDIF needs two kinds of fact: core and
discovery are technique-independent and come from the NeXus structure
and the file on disk; the domain facts come from the concept records.
The concept-to-CDIF binding is a dict in code, not a fourth TSV — `map`
answers "what concept is this value", `emit` answers "where does it go",
and the second answer is the shape of a schema.org graph. A concept with
no binding still reaches the output as additionalProperty, with a
warning.

Arrays become `schema:variableMeasured` plus a DataStructure component;
scalars become instrument, sample or event context. HDF5 paths are
`cdif:LocatorMapping` with `cdif:locator`, never indices.

Validating against the real profile found five defects worth recording,
because each would silently produce a wrong or lossy document:

* **A flat `prov:used` disappears when framed.** It validates as raw
  JSON and then vanishes, because a frame drops what it does not
  declare. The profile frame declares `schema:instrument` beneath
  `prov:used`.
* **Beamline, source and monochromator are three peers, not one.** The
  profile distinguishes them and constrains each; folding source into
  beamline reads sensibly and satisfies neither.
* **propertyIDs must be the concept locals verbatim.** The profile
  enumerates the same tokens, so a tidier spelling
  (`xas:xray_source_type` for `xas:xraysourcetype`) yields a document
  that cannot conform. A test now keeps the two in step.
* **`schema:value` must be a string.** A d-spacing as a JSON float, or a
  reflection plane as `[3, 1, 1]`, fails the constraint that says the
  monochromator must report those values at all.
* **The catalog record must be an IRI.** SHACL targets it by identity,
  and nothing outside a document can refer to a blank node inside it.

### The one profile change this required

`xasCore`'s distribution `contains` demanded `dcterms:conformsTo`
include the **XDI specification**, which an HDF5 file cannot truthfully
claim. It also contradicted the `@type` constraint beside it, already
format-neutral. Relaxed to accept either the XDI spec or a NeXus
application definition — strictly widening, and the profile's own three
examples still validate. Committed to
`metadataBuildingBlocks/_sources/xasProperties/xasCore/schema.yaml` and
mirrored into `XAS-CDIF/release/`.

Remaining SHACL output on FeXAS is warnings only, for what a NeXus file
genuinely does not carry: a creator, a contact point, and an instrument
category from a controlled vocabulary.

### What the map layer settled

Two crosswalk path kinds have to be told apart, **structurally rather
than by name**: application-definition paths are rooted at the entry and
lead with an `NXentry` segment; base-class paths (`nxdl:NXsource/name`)
are relative to an instance of that class wherever it appears. An early
version conflated them and mapped zero concepts from a file full of
them. Detecting base classes by name prefix instead has the same failure
mode one level up — it classes every non-XAS definition as a base class,
so an `NXsas` path would be applied to an XAS file.

A literal name absent from the file is a **miss** when siblings share
its class: `i0`, `itrans`, `ifluor` and `iey` are all `NXdetector` and
only the name distinguishes them, so treating a missing `iey` as "any
detector" reports the incident-beam monitor as an electron-yield
measurement. Where the class has one instance the name is only a label
and is forgiven.

A file declaring the family base (`definition=NXxas`) may still be laid
out as one specific mode. Concepts are filled from more specific
definitions in the same family and noted as borrowed — but never for a
path that names different concepts in different definitions.
`/ENTRY/intensity` is the absorption coefficient in transmission and the
fluorescence-derived one in TFY; only the declaration disambiguates, so
an undeclared file gets neither rather than both.

### FeXAS.nxs, measured

26 entries, 9 concepts each, no false positives, resolving to **2**
distinct data structures — the reference foil carries an `itrans`
detector the 25 sample scans do not, which is exactly the archive-of-
parts case docs/DESIGN-2026-07-27.md calls for.

### Legacy paths are a separate table — settled

Several concepts were physically present but at paths the standard does
not name: element and edge at `scan:NXscan/xrayedge:NXxrayedge/{element,
edge}`, edge energy at `scan/edge_energy`, beamline name at
`source/beamline_name`, and the absorption coefficients at
`data/{mutrans,mufluor}`. `NXscan` and `NXxrayedge` are not NeXus base
classes — they are the Athena/GSECARS writer's convention.

These live in `data/legacy-paths.tsv`, **deliberately not SSSOM and
deliberately not upstream**. The crosswalk states what a concept
corresponds to *in the standard* and is worth publishing as an
alignment; the legacy table records local practice and will keep growing
as more conventions appear. Mixing them would make the crosswalk a
quirks list.

The rule that keeps it safe to extend: consulted only after the
crosswalk and its same-family fallback, and only for concepts still
missing. A legacy path fills a gap but **never displaces** a
standards-based value, whatever the confidences say. Values carry a
`convention` field so consumers filter on provenance rather than parsing
a note.

Adding a convention is appending rows. A test asserts every concept
named there exists in the crosswalk, so a typo fails the suite instead
of silently never matching.

**Open crosswalk question this surfaced.** In FeXAS `NXsource/name` is
`"APS, undulator 36mm, 66 poles, 13-ID-E"` — facility, insertion device
and beamline concatenated — while `facility_name` beside it is just
`"APS"`. The never-override rule means `cdifxas:facility` gets the
concatenation. That is a question about whether `cdifxas:facility`
should map to `NXsource/name` at all, and it belongs in the crosswalk,
not in an override switch.

### FeXAS.nxs with the legacy table

9 concepts per entry becomes **17**, and the entries now differ
correctly: `FeFoil.001` gets `absorptioncoefficient` from `data/mutrans`
and mode `Transmission`; the 25 sample scans get neither and read
`Fluorescence`. `data/mode` recovers the detection mode per entry — the
thing restructured NXxas gets from the application definition.

Still genuinely absent: `temperature`, `samplepreparation` (`NXsample`
is empty) and `intensityuncertainty`.

Resilience requirements — the XAS definitions are in flux, so the code
must: search all three definition directories and never hardcode which
holds what; discover the appdef list at load time; pin a commit SHA by
default with an override and record the resolved SHA in provenance;
treat an unresolvable definition as a degraded tier that still emits
core+discovery rather than failing; parse NXDL defensively; and make no
structural assumptions beyond the NXDL grammar (the new `NXxas` does
*not* put energy under `NXmonochromator`).

Full detail: [`docs/DESIGN-2026-07-27.md`](./docs/DESIGN-2026-07-27.md) · conventions and gotchas:
[`AGENTS.md`](./AGENTS.md)

---

### XDI — the second binding

The architecture claimed a second input format would be a parser rather
than a pipeline. `inspect/xdi.py` + `map/xdi.py` are that claim cashed:
they produce the same `ConceptRecord`, and emission, profile detection,
validation and the CLI are untouched shared code. XDI needed almost none
of the NeXus machinery, because XDI is a dictionary and HDF5 is a tree —
concepts come out by lookup, and `map/crosswalk.py` is not involved.

Dispatch is on what the file declares, not its extension. The space
after `#` is optional: 118 of the 272 files in the XAS Data Library
write `#XDI/1.0` closed up.

All 55 XDI files in the XAS-CDIF corpus validate clean against the
strict `xasDocument` composite.

### What the second binding exposed in shared code

Both were real defects that no NeXus example had reached:

- Variables were named from the source path — right for HDF5, where the
  path ends in the field name; wrong for XDI, where a column is located
  by position. Values now carry an explicit `label`.
- The sample object was typed `Thing`/`prov:Entity`; the profile wants
  `schema:Product` + `schema:Thing` with both `MaterialSample` and the
  iSamples IRI. No NeXus example carried sample properties, so nothing
  had exercised it.

### Structures belong on the distribution

`cdi:isStructuredBy` was written at dataset level. The JSON Schema
admits it only on a distribution item and the SHACL rule reaches it as
`schema:distribution/isStructuredBy`; documents validated only because
an unrecognised property at dataset level is ignored.

Dataset level was also wrong on its own terms — a file with 26 entries
and two layouts cannot say which entry has which. Both structures now
sit on the distribution, inline with their components, and each part
references the one it uses by `@id`. An `@id` reference is not a "bare"
reference in RDF: it denotes the same node, so the SHACL check passes.

That SHACL message said otherwise and has been corrected upstream in
`metadataBuildingBlocks`.

### Sentinels

Where the profile requires an instrument property the file omits, it is
emitted with a sentinel rather than left out — omitting it makes the
instrument undescribable and fails validation. A missing source type is
`Synchrotron X-ray Source` where the file declares XAS (by being XDI, or
by declaring an NXxas definition) and the OGC nil URI otherwise: an
NXtomo file may well have been measured at a synchrotron, but nothing in
it says so.

## Effort B — CDIF XAS vocabulary

In `smrgeoinfo/XAS-CDIF`, branch **`cdifxasRelease1.1`** (not `main`).

### Done

`XAS_Glossary_vs_NeXus_analysis.md` — full gap analysis: ~22 concepts
with clean NeXus counterparts, 10 beamline/facility concepts corrected
to base-class paths, 8 genuinely unhomed, candidate new concepts,
enumerations worth importing, and the reverse contribution CDIF could
make to NeXus.

Enumeration import (recommendation item 1), via the re-runnable
`tools/import_nexus_enumerations.py`:

| File | Concepts | Source |
|---|---:|---|
| `XAS_edges_SKOS.json` | 39 | `NXabsorption_edge/name` |
| `XAS_emissionlines_SKOS.json` | 432 | `NXemission_line/name` |
| `XAS_detectionmodes_SKOS.json` | 7 | union of upstream + fork |
| `XAS_Glossary_SKOS_v2.json` | 106 | 90 as of 2026-07-27; 106 as of 2026-08-25 |

Kept as **separate schemes** rather than folded into the glossary: 478
concepts against 89 would be a 6× inflation, and the publishing pipeline
emits one file per concept. Linked via `dc:references` out, `skos:broader`
back.

Detection modes **must** be the union — upstream has Auger Electron Yield
and one undivided Fluorescence Yield; the fork drops Auger, splits
fluorescence into total/partial, and adds HERFD. Either source alone
loses terms.

### Not done — remaining recommendations

2. Hand-write ~10 concepts for PFY/HERFD/PEY vocabulary CDIF lacks:
   `emissionenergy`, `emissionenergywindow`, `retardingvoltage`,
   `analyzercrystal`, `rowlandradius`, `bendingradius`, `deadtime`,
   `counttime`, `intensityuncertainty`
3. Add `skos:exactMatch` / `closeMatch` to the ~22 overlapping concepts,
   pointing at **base-class** paths. Flag `calculated` ↔
   `is_experimental` as `skos:related`, **not** `exactMatch` — the
   boolean polarity is inverted
4. Move the 8 genuinely unhomed XDI-derived concepts to an `xdi:`
   namespace: `installedoptions`, `scanmode`, `calibrationmethod`,
   `fluxmeasuremethod`, `website`, `monochromatorangle`, `monitormode`,
   `monitorpreset`
5. Adopt the NXxas subclass hierarchy as `skos:broader` — the glossary is
   currently entirely flat (all concepts `hasTopConcept`, no
   `broader`/`narrower`)

### Next artifact (proposed, not started)

An SSSOM alignment set for the **transmission slice** end-to-end —
element, edge, energy, i0/itrans, facility, mono/d-spacing — anchored on
the current `XAS_Glossary_SKOS.json`, with XDI tokens as one
binding and `NXxas_trans` concept paths as the other. Validate the XDI
side against the concept keys `mapping_dds.ttl` actually references, and
diff the emitted output against the existing reference JSON-LD.

Anchor on the **local** glossary file, not the published
`w3id.org/cdif/xas/` concepts — the local copy is ahead of what is
published (see Established §8).

### Worth proposing upstream

CDIF's 17 EXAFS analysis concepts and its operational beamline concepts
fill real holes in the XAS fork. Better raised **while their definitions
are still in flux** than after they stabilise.

---

## Canonical links

| What | Where |
|---|---|
| This file | `usgin/cdifnexmetadata` → `STATUS.md` (`main`) |
| Extractor design | `usgin/cdifnexmetadata` → `docs/DESIGN-2026-07-27.md` (`main`) |
| Extractor conventions | `usgin/cdifnexmetadata` → `AGENTS.md` (`main`) |
| XAS gap analysis | `smrgeoinfo/XAS-CDIF` → `XAS_Glossary_vs_NeXus_analysis.md` (**`cdifxasRelease1.1`**) |
| Enumeration importer | `smrgeoinfo/XAS-CDIF` → `tools/import_nexus_enumerations.py` (**`cdifxasRelease1.1`**) |
| XDI→CDIF pipeline | `smrgeoinfo/cdif-xas` (`main`) |
| CDIF profile schemas + SHACL | `Cross-Domain-Interoperability-Framework/metadataBuildingBlocks` → `_sources/profiles/` |
| NeXus definitions (use this) | `XraySpectroscopy/nexus_definitions` (`main`) |
| Test file | `FeXAS.nxs`, 2.7 MB, 26 `NXentry`, `definition = NXxas` |
| Golden reference | `Cross-Domain-Interoperability-Framework/profile-datastructure` → `examples/FeXAS/NEXUS-withDataStructureComponent.json` |

Note the XAS-CDIF items are on `cdifxasRelease1.1`, **not** `main`.
