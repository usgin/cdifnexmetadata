# Bundled crosswalks

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
