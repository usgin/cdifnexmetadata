# Bundled data

Two kinds of file live here, maintained differently.

| File | Kind |
|------|------|
| `cdifxas-to-nexus.sssom.tsv` | copy of an upstream file — **do not edit here** |
| `xdi-to-cdifxas.sssom.tsv` | copy of an upstream file — **do not edit here** |
| `xdi-to-cdif.sssom.tsv` | copy of an upstream file — **do not edit here** |
| `cdifsas-to-nexus.sssom.tsv` | authored in this repo — edit here |
| `legacy-paths.tsv` | authored in this repo — edit here |
| `cdifxas-units.tsv` | copy of an upstream file — **do not edit here** |

## Crosswalks copied from upstream

**These files are copies. Do not edit them here.**

Upstream: <https://github.com/smrgeoinfo/XAS-CDIF/tree/cdifxasRelease1.1/crosswalk>
Curated and validated by `crosswalk/build_crosswalk.py` in that repo,
which checks every subject against the CDIF XAS glossary, every NeXus
path against the live NXDL, and every XDI key against the concept keys
the production RML mapping actually reads.

They are copied in rather than fetched, so this package works offline
and so a given release is pinned to a known crosswalk revision. The cost
is that these copies fall behind when the originals change. Refresh all
four with:

    python -m cdifnexmetadata.map.crosswalk --refresh

That downloads from the `cdifxasRelease1.1` branch on GitHub, so it sees
only what has been pushed there. **None of them is edited by hand at
either end** — all are output from `crosswalk/build_crosswalk.py`
upstream, which curates the rows as Python tables and validates them
against the glossary, the live NXDL, and the concept keys the RML
converter reads. Changing a mapping means editing that script and
re-running it. To pick up an upstream edit that has not been pushed
yet, copy them across directly rather than refreshing.

| File | Direction |
|------|-----------|
| `cdifxas-units.tsv` | CDIF XAS concept -> QUDT unit. Not a mapping between vocabularies but a fact the glossary asserts about a concept, so not SSSOM. Read where a file records no unit. |
| `cdifxas-to-nexus.sssom.tsv` | CDIF XAS concept -> NeXus path. Used by the mapper. |
| `xdi-to-cdifxas.sssom.tsv` | XDI token -> CDIF XAS concept. The other binding; here for reference. |
| `xdi-to-cdif.sssom.tsv` | XDI extension header -> **schema.org** property. Bibliographic and rights headers that CDIF models with schema.org rather than with an XAS concept. Here for reference; see below. |

### `xdi-to-cdif.sssom.tsv` and the two headers it cannot place

Upstream splits the XDI mappings into two sets because SSSOM requires a
set to declare one `object_source`, and these four rows point at
`http://schema.org/` while the other thirty-one point at the CDIF XAS
glossary. Folding them together would falsify the declaration that
consumers rely on.

`map/xdi.py` reads it into `ConceptRecord.bibliographic`, kept apart
from `values` because a schema.org property is a serialization target
and not a concept. Two of the four reach the document:

- `Spectrum.license` -> `schema:license`, replacing the "looked, absent"
  sentinel with what the file states.
- `Publication.DOI` -> a `schema:relatedLink` whose target is
  `https://doi.org/{doi}`, since the profile types a target as a URI and
  a bare DOI is an identifier rather than a location.

**`Publication.authors` and `Publication.affiliation` are mapped and
then deliberately not emitted.** The CDIF profile has no slot for the
authorship of a *related publication*: `schema:relatedLink` is a
LinkRole pointing at an EntryPoint, which holds a url, a name and an
encoding format and nothing else. The schema.org answer would be
`schema:citation`, which is not in the CDIF schema and is not
recommended by CDIF -- a document using it would carry a node no CDIF
consumer looks for. The other way out, folding the paper's authors into
`schema:creator`, asserts that they created the dataset, which the file
does not say.

So the emitter reports them instead. They are visible in the concept
dump and named in a warning. Mapping a header and then losing it without
saying so is worse than never mapping it, because the crosswalk reports
success and the document simply lacks the value.

None of the four occurs in the 55-file example corpus.

The XAS crosswalk is domain-specific. The mapper takes any SSSOM set, so
other techniques need a crosswalk, not a code change.

## `legacy-paths.tsv`

Where writers that predate or diverge from the standard actually put
things. **Deliberately not SSSOM**, and deliberately not upstream: the
crosswalk states what a concept corresponds to *in the standard* and is
worth publishing as an alignment; this table records local practice and
will keep growing as more conventions turn up. Folding the second into
the first would make the crosswalk a quirks list.

Consulted only after the crosswalk, and only for concepts still missing
— a legacy path can fill a gap but never displaces a standards-based
value. That is what makes it safe to add a convention without
re-testing what came before.

Adding a convention is a matter of appending rows: give the concept, a
`convention` slug, and an entry-relative path in the same NXDL syntax
the crosswalk uses. `tests/test_map.py` checks that every concept named
here exists in the crosswalk, so a typo fails the suite rather than
silently never matching.

Currently covers `gsecars-athena` — the Athena/GSECARS HDF5 writer,
which uses `NXscan` and `NXxrayedge` (not NeXus base classes) and puts
~20 `beamline_*` and `facility_*` fields on `NXsource` rather than
`NXinstrument`.

## `cdifsas-to-nexus.sssom.tsv`

Small-angle scattering, mapped to `NXsas`. Written as the **second**
binding onto the concept hub, to test whether adding a technique really
is a crosswalk rather than a code change. It is: pass it with
`--crosswalk` and an `NXsas` file yields concepts, variables and a data
structure with nothing else touched.

Authored here rather than copied, because there is no CDIF SAS glossary
to copy from; the concepts follow the same mint-now-redirect-later
pattern under `https://w3id.org/cdif/sas/`.

See [`docs/NXsas.md`](../../../docs/NXsas.md) for what `NXsas` is, and
the file's own header for why four technique-neutral concepts
(facility, beamline, probe, source type) still carry `cdifxas:` CURIEs.
