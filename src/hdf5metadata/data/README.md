# Bundled data

Two kinds of file live here, maintained differently.

| File | Kind |
|------|------|
| `cdifxas-to-nexus.sssom.tsv` | vendored copy — **do not edit here** |
| `xdi-to-cdifxas.sssom.tsv` | vendored copy — **do not edit here** |
| `legacy-paths.tsv` | authored in this repo — edit here |

## Vendored crosswalks

**These files are copies. Do not edit them here.**

Upstream: <https://github.com/smrgeoinfo/XAS-CDIF/tree/cdifxasRelease/crosswalk>
Curated and validated by `crosswalk/build_crosswalk.py` in that repo,
which checks every subject against the CDIF XAS glossary, every NeXus
path against the live NXDL, and every XDI key against the concept keys
the production RML mapping actually reads.

They are vendored so this package works offline and so a given release
pins a known crosswalk revision. Refresh with:

    python -m hdf5metadata.map.crosswalk --refresh

| File | Direction |
|------|-----------|
| `cdifxas-to-nexus.sssom.tsv` | CDIF XAS concept -> NeXus path. Used by the mapper. |
| `xdi-to-cdifxas.sssom.tsv` | XDI token -> CDIF XAS concept. The other binding; here for reference. |

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
