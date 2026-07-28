"""Access to the NeXus NXDL definitions.

Fetches definition files from a git repository, caches them on disk, and
discovers what definitions exist. Deliberately separate from parsing so
that the parser can be tested on local XML with no network at all.

Why this is more defensive than it looks
----------------------------------------

The XAS community's definitions are **actively being revised**, and have
already moved once: ``NXxas`` was deleted from ``applications/`` and
re-added under ``contributed_definitions/``, alongside six new
per-detection-mode definitions. So:

* **Every directory is searched.** Nothing hardcodes which of
  ``contributed_definitions/``, ``applications/`` or ``base_classes/``
  holds a given definition, because that has already changed.
* **The list of definitions is discovered, never hardcoded.** New
  definitions appear without a code change.
* **A specific commit is pinned by default.** A mid-flight upstream edit
  cannot silently alter our output. The resolved ref travels with every
  parsed definition so any result can be traced back to the revision it
  came from.
* **Absence is not an error.** A definition that cannot be found or
  fetched yields ``None``; callers degrade to a lower resolution tier
  rather than failing.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: The XAS community's working fork. Ahead of nexusformat/definitions and
#: the target of the NeXusOntology generation scripts -- see DESIGN.md.
DEFAULT_REPO = "XraySpectroscopy/nexus_definitions"

#: Pinned so results are reproducible while these definitions are in flux.
#: Bump deliberately; do not track a moving branch by default.
DEFAULT_REF = "89971f3922664b5c3fc5eb9929b2fbf5252dcefd"

#: Searched in order. Order matters only for shadowing, which should not
#: happen; membership is what counts.
DEFINITION_DIRS = ("contributed_definitions", "applications", "base_classes")

_RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
_API_TREE = "https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"


def default_cache_dir() -> Path:
    env = os.environ.get("HDF5METADATA_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "hdf5metadata" / "nxdl"


@dataclass
class DefinitionSource:
    """Where a definition's XML came from, for provenance."""

    name: str
    directory: str
    repo: str
    ref: str
    from_cache: bool = False

    @property
    def url(self) -> str:
        return _RAW.format(
            repo=self.repo, ref=self.ref,
            path=f"{self.directory}/{self.name}.nxdl.xml",
        )


@dataclass
class Repository:
    """A pinned NXDL definitions repository, with an on-disk cache.

    Parameters
    ----------
    repo, ref:
        Repository and git ref. The default ref is a pinned commit, not
        a branch -- see the module docstring.
    cache_dir:
        Where fetched XML is stored. Cache entries are keyed by ref, so
        changing the pin does not read stale files.
    offline:
        Never touch the network; serve only what is already cached.
    """

    repo: str = DEFAULT_REPO
    ref: str = DEFAULT_REF
    cache_dir: Path = field(default_factory=default_cache_dir)
    offline: bool = False
    timeout: float = 30.0

    warnings: list[str] = field(default_factory=list, repr=False)
    _index: list[str] | None = field(default=None, repr=False)

    # -- cache ---------------------------------------------------------------

    def _cache_path(self, directory: str, name: str) -> Path:
        return self.cache_dir / self.ref / directory / f"{name}.nxdl.xml"

    def _read_cache(self, directory: str, name: str) -> str | None:
        p = self._cache_path(directory, name)
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def _write_cache(self, directory: str, name: str, text: str) -> None:
        p = self._cache_path(directory, name)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        except OSError as e:
            self.warnings.append(f"could not cache {name}: {type(e).__name__}")

    # -- fetching ------------------------------------------------------------

    def _get(self, url: str) -> str | None:
        if self.offline:
            return None
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                self.warnings.append(f"HTTP {e.code} fetching {url}")
            return None
        except Exception as e:
            self.warnings.append(f"{type(e).__name__} fetching {url}")
            return None

    def fetch(self, name: str) -> tuple[str, DefinitionSource] | None:
        """XML text and provenance for a definition, or None if absent.

        Searches every definition directory. Cache is consulted first,
        including in online mode -- these files change rarely relative to
        how often a tool runs.
        """
        name = name.strip()
        if not name:
            return None

        for directory in DEFINITION_DIRS:
            cached = self._read_cache(directory, name)
            if cached is not None:
                return cached, DefinitionSource(
                    name=name, directory=directory, repo=self.repo,
                    ref=self.ref, from_cache=True,
                )

        if self.offline:
            self.warnings.append(f"{name}: not cached and offline")
            return None

        for directory in DEFINITION_DIRS:
            url = _RAW.format(
                repo=self.repo, ref=self.ref,
                path=f"{directory}/{name}.nxdl.xml",
            )
            text = self._get(url)
            if text is not None:
                self._write_cache(directory, name, text)
                return text, DefinitionSource(
                    name=name, directory=directory, repo=self.repo,
                    ref=self.ref,
                )

        self.warnings.append(
            f"{name}: not found in any of {', '.join(DEFINITION_DIRS)} "
            f"at {self.repo}@{self.ref[:8]}"
        )
        return None

    # -- discovery -----------------------------------------------------------

    def _index_cache_path(self) -> Path:
        return self.cache_dir / self.ref / "_index.json"

    def list_definitions(self) -> list[str]:
        """Every definition name in the repository, discovered not
        hardcoded, so new definitions need no code change."""
        if self._index is not None:
            return self._index

        p = self._index_cache_path()
        if p.is_file():
            try:
                self._index = json.loads(p.read_text(encoding="utf-8"))
                return self._index
            except Exception:
                pass

        if self.offline:
            self.warnings.append("definition index not cached and offline")
            return []

        text = self._get(_API_TREE.format(repo=self.repo, ref=self.ref))
        if text is None:
            return []
        try:
            tree = json.loads(text).get("tree", [])
        except Exception as e:
            self.warnings.append(f"could not parse tree listing: {e}")
            return []

        names: set[str] = set()
        for item in tree:
            path = item.get("path", "")
            if not path.endswith(".nxdl.xml"):
                continue
            directory = path.split("/", 1)[0]
            if directory not in DEFINITION_DIRS:
                continue
            names.add(path.rsplit("/", 1)[-1][: -len(".nxdl.xml")])

        self._index = sorted(names)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._index), encoding="utf-8")
        except OSError:
            pass
        return self._index

    def application_definitions(self) -> list[str]:
        """Definition names that look like application definitions.

        Category lives inside the XML, so this is a name-shape filter:
        the caller should confirm via the parsed definition's
        ``category``. Kept because it is a cheap way to enumerate
        candidate techniques without fetching ~280 files.
        """
        return [n for n in self.list_definitions() if not n.startswith("NXcs_")]
