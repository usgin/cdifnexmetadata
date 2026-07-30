"""Stage 3b: check an emitted document against the CDIF profiles.

Emitting a document that claims `dcterms:conformsTo` and never checking
the claim is worse than not claiming it. This module runs the same two
checks the CDIF project runs — JSON Schema and SHACL — so a conformance
assertion is tested rather than asserted.

Framing comes first, and is not optional
----------------------------------------

The profiles are written against the *framed* document, not the graph as
emitted. Framing collapses the graph into the tree the schema describes,
and it drops anything the frame does not declare — which is a real check
in itself: a property that vanishes on framing would never have reached
a consumer, however well-formed it looked on disk. A `prov:used` written
flat instead of nested survives JSON Schema and disappears here, so
validating the unframed document would have passed something broken.

Profile artifacts are not bundled
---------------------------------

The schemas, frames and shapes belong to the CDIF profile repositories
and are versioned there. Vendoring them would pin this package to a
snapshot and invite the copies to drift. So they are located at run time
from a directory the caller names, and when none is given validation is
*skipped and said to be skipped* rather than quietly passing.

Missing tools are skips, not failures
-------------------------------------

`jsonschema`, `pyshacl`, `rdflib` and `pyld` are optional extras. Absent,
the corresponding check reports itself skipped. A tool that cannot run a
check must not report that the check passed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Filename patterns for locating profile artifacts, mirroring the
#: convention the CDIF validation tools already use.
SCHEMA_PATTERNS = ("*ResolvedSchema*.json", "*Schema*.json")
FRAME_PATTERNS = ("*-frame.jsonld", "*frame*.jsonld")
SHAPES_PATTERNS = ("*.shacl", "*Rules*.ttl", "*shapes*.ttl")

#: SHACL severities that mean the document is wrong, as opposed to
#: merely improvable. Warnings and Info are reported but do not fail:
#: a NeXus file genuinely does not record a creator or a contact point,
#: and failing on that would make the check useless rather than strict.
FAILING_SEVERITIES = {"Violation"}


@dataclass
class Issue:
    source: str            # "schema" | "shacl" | "frame"
    severity: str          # "Violation" | "Warning" | "Info"
    message: str
    path: str = ""

    @property
    def is_failure(self) -> bool:
        return self.severity in FAILING_SEVERITIES

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        return f"[{self.source}/{self.severity}]{where} {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    #: Checks that could not run, and why. Never counted as passes.
    skipped: list[str] = field(default_factory=list)
    framed: dict[str, Any] | None = None

    @property
    def failures(self) -> list[Issue]:
        return [i for i in self.issues if i.is_failure]

    @property
    def valid(self) -> bool:
        """True only when every check that ran found no failure. A run
        where everything was skipped is not valid -- it is unchecked, and
        `skipped` says so."""
        return not self.failures and not self.ran_nothing

    @property
    def ran_nothing(self) -> bool:
        return len(self.skipped) >= 2

    def summary(self) -> str:
        if self.ran_nothing:
            return "not checked (" + "; ".join(self.skipped) + ")"
        bits = []
        fails = len(self.failures)
        others = len(self.issues) - fails
        bits.append("PASSED" if not fails else f"FAILED ({fails})")
        if others:
            bits.append(f"{others} advisory")
        if self.skipped:
            bits.append("skipped: " + "; ".join(self.skipped))
        return ", ".join(bits)


# ---------------------------------------------------------------------------
# locating profile artifacts
# ---------------------------------------------------------------------------

@dataclass
class Profile:
    """Where the artifacts for one profile live."""

    schema: Path | None = None
    frame: Path | None = None
    shapes: Path | None = None
    source: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.schema or self.frame or self.shapes)


def _first_match(directory: Path, patterns: tuple[str, ...]) -> Path | None:
    for pattern in patterns:
        hits = sorted(directory.glob(pattern))
        if hits:
            return hits[0]
    return None


def find_profile(directory: str | Path) -> Profile:
    """Locate schema, frame and shapes in a profile release directory.

    Deliberately pattern-based rather than name-based: the release
    directories name their artifacts after the profile
    (`cdifXASDocumentResolvedSchema.json`), so hardcoding a filename
    would tie this to one profile.
    """
    d = Path(directory)
    if not d.is_dir():
        return Profile(source=str(d))
    return Profile(
        schema=_first_match(d, SCHEMA_PATTERNS),
        frame=_first_match(d, FRAME_PATTERNS),
        shapes=_first_match(d, SHAPES_PATTERNS),
        source=str(d),
    )


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def _restore_compacted_arrays(
    document: dict[str, Any], schema: dict[str, Any], rounds: int = 4
) -> tuple[dict[str, Any], int]:
    """Put back the array wrappers framing removed.

    JSON-LD framing compacts a single-element array to a bare value, so a
    document that correctly emitted `[{...}]` arrives as `{...}` and
    fails a `type: array` check it never actually violated.

    Which properties those are is asked of the validator rather than
    guessed from the schema. Guessing by name does not work in either
    direction: `schema:identifier` is an array on an instrument and a
    string on the dataset, so wrapping every name that is ever an array
    breaks the places it was already right, and skipping every name that
    is ever something else leaves `schema:additionalType` and
    `schema:encodingFormat` broken. The validator knows which position it
    is looking at; nothing here has to.

    The repair is safe in one direction only, which is the direction that
    matters: framing only ever removes a wrapper, so restoring one can
    never contradict what the document said. Anything still failing
    afterwards is a real finding and is reported.
    """
    import jsonschema

    validator = jsonschema.Draft202012Validator(schema)
    repaired = 0

    for _ in range(rounds):
        targets: list[tuple] = []

        def collect(error, prefix: tuple = ()) -> None:
            path = prefix + tuple(error.absolute_path)
            if error.validator == "contains" and isinstance(
                    error.instance, list):
                # A `contains` failure reports only that nothing matched,
                # never why any particular item did not. So the sub-schema
                # is run against each item here: without this, a collapsed
                # array *inside* a contains is invisible and the whole
                # constraint reads as unsatisfiable when it is one wrapper
                # away from being satisfied.
                sub_schema = (error.schema or {}).get("contains")
                if isinstance(sub_schema, dict):
                    # evolve, not a fresh validator: the sub-schema is
                    # full of `$ref`s into the root's `$defs`, which only
                    # resolve while the root's scope is still in hand.
                    item_validator = validator.evolve(schema=sub_schema)
                    for i, item in enumerate(error.instance):
                        try:
                            sub_errors = list(item_validator.iter_errors(item))
                        except Exception:            # noqa: BLE001
                            continue                 # unrepairable, not fatal
                        for sub_error in sub_errors:
                            collect(sub_error, path + (i,))
                return
            if error.context:
                for sub_error in error.context:
                    collect(sub_error, prefix)
                return
            if error.validator == "type" and error.validator_value == "array" \
                    and not isinstance(error.instance, list):
                targets.append(path)

        for e in validator.iter_errors(document):
            collect(e)

        targets = [p for p in dict.fromkeys(targets) if p]
        if not targets:
            break
        for path in targets:
            node: Any = document
            try:
                for step in path[:-1]:
                    node = node[step]
                current = node[path[-1]]
            except (KeyError, IndexError, TypeError):
                continue
            if not isinstance(current, list):
                node[path[-1]] = [current]
                repaired += 1

    return document, repaired


def frame_document(
    document: dict[str, Any], frame: Path
) -> tuple[dict[str, Any] | None, str | None]:
    """Frame a document. Returns (framed, error)."""
    try:
        from pyld import jsonld
    except ImportError:
        return None, "pyld not installed (pip install cdifnexmetadata[validate])"
    import json

    try:
        frame_doc = json.loads(Path(frame).read_text(encoding="utf-8"))
        framed = jsonld.frame(document, frame_doc)
    except Exception as e:                       # noqa: BLE001
        return None, f"framing failed: {type(e).__name__}: {e}"

    # The framed result is a @graph whenever more than one node matches
    # the frame at top level -- which is normal here, because the catalog
    # record is an IRI and so stands as a node of its own. The profile
    # schema describes the dataset, so the dataset is what gets checked.
    #
    # The @context is the frame's, deliberately: the keys were compacted
    # against it, and swapping in the document's own context afterwards
    # would leave the two disagreeing about what those keys mean.
    if isinstance(framed.get("@graph"), list):
        main = _main_entity(framed["@graph"])
        if main is None:
            return None, "framing produced no dataset node"
        framed = {"@context": framed.get("@context"), **main}
    return _undo_framing_artifacts(framed), None


def _undo_framing_artifacts(node: Any) -> Any:
    """Repair what framing does to a document on the way through.

    Three effects, all artifacts of the process rather than anything the
    document said, and all decidable without the schema:

    * A frame inserts `null` for every property it declares and the
      document lacks, so an absent optional property arrives looking like
      an explicitly empty one.
    * It compacts a single-element `@type` array to a bare string, which
      every CDIF schema rejects.
    * It compacts IRI *values* against the document's own context, so a
      conformance URI written in full comes back as `cdif:core/1.1` and
      no longer matches the `const` it is checked against. Only `@id`
      values are re-expanded; `cdif:` property *keys* are meant to stay
      compact.
    The fourth -- collapsed single-element arrays -- needs the schema to
    say where it happened, so it is handled separately by
    :func:`_restore_compacted_arrays`.
    """
    if isinstance(node, list):
        return [_undo_framing_artifacts(v) for v in node if v is not None]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if value is None:
            continue
        if key == "@context":
            out[key] = value
            continue
        value = _undo_framing_artifacts(value)
        if key == "@type" and isinstance(value, str):
            value = [value]
        if key == "@id" and isinstance(value, str) and value.startswith(
                "cdif:"):
            value = "https://w3id.org/cdif/" + value[len("cdif:"):]
        out[key] = value
    return out


def _is_catalog_record(node: dict[str, Any]) -> bool:
    types = node.get("schema:additionalType") or []
    if isinstance(types, dict):
        types = [types]
    return any(
        (t.get("@id") if isinstance(t, dict) else t) == "dcat:CatalogRecord"
        for t in (types if isinstance(types, list) else [])
    )


def _main_entity(graph: list[Any]) -> dict[str, Any] | None:
    """The dataset the document is about.

    Same rule the CDIF validation tool applies, and profile-agnostic for
    the same reason: prefer a node carrying a distribution, then one
    carrying a url, then the first node that is not a catalog record. A
    bare related Dataset has neither, so it never wins.
    """
    candidates = [
        n for n in graph
        if isinstance(n, dict) and not _is_catalog_record(n)
    ]
    if not candidates:
        return None
    for key in ("schema:distribution", "schema:url"):
        for node in candidates:
            if node.get(key) is not None:
                return node
    return candidates[0]


def check_schema(
    document: dict[str, Any], schema: dict[str, Any] | Path
) -> list[Issue]:
    try:
        import jsonschema
    except ImportError:
        raise RuntimeError(
            "jsonschema not installed (pip install cdifnexmetadata[validate])")
    import json

    s = schema if isinstance(schema, dict) else json.loads(
        Path(schema).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(s)
    issues: list[Issue] = []
    for e in validator.iter_errors(document):
        # The schema's own description says what the constraint is for,
        # and is far more use than "does not contain items matching the
        # given schema" pointing at a 40-line subschema.
        message = (e.schema or {}).get("description") if isinstance(
            e.schema, dict) else None
        issues.append(Issue(
            source="schema",
            severity="Violation",
            message=(message or e.message)[:400],
            path="/" + "/".join(str(p) for p in e.absolute_path),
        ))
    return issues


def check_shacl(document: dict[str, Any], shapes: Path) -> list[Issue]:
    try:
        import pyshacl
        import rdflib
    except ImportError:
        raise RuntimeError(
            "pyshacl/rdflib not installed "
            "(pip install cdifnexmetadata[validate])")
    import json

    data = rdflib.Graph().parse(
        data=json.dumps(document), format="json-ld")
    shape_graph = rdflib.Graph().parse(str(shapes), format="turtle")
    _ok, results, _text = pyshacl.validate(
        data, shacl_graph=shape_graph, advanced=True, inference="none")

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    issues: list[Issue] = []
    for node in results.subjects(rdflib.RDF.type, SH.ValidationResult):
        severity = str(results.value(node, SH.resultSeverity)).rsplit(
            "#", 1)[-1] or "Violation"
        focus = results.value(node, SH.focusNode)
        issues.append(Issue(
            source="shacl",
            severity=severity,
            message=str(results.value(node, SH.resultMessage) or "")[:400],
            path=str(focus) if focus else "",
        ))
    return issues


def validate_document(
    document: dict[str, Any],
    profile: Profile | None = None,
    frame_first: bool = True,
) -> ValidationResult:
    """Run every check the profile makes available.

    Each check that cannot run is recorded in `skipped`; none is ever
    silently treated as a pass.
    """
    import json

    result = ValidationResult()
    profile = profile or Profile()

    schema_doc: dict[str, Any] | None = None
    if profile.schema:
        try:
            schema_doc = json.loads(
                Path(profile.schema).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            result.skipped.append(f"JSON Schema: unreadable ({e})")

    target = document
    if frame_first and profile.frame:
        framed, error = frame_document(document, profile.frame)
        if error:
            result.skipped.append(f"framing: {error}")
        else:
            target = framed
            if schema_doc is not None:
                try:
                    target, _n = _restore_compacted_arrays(target, schema_doc)
                except ImportError:
                    pass
            result.framed = target
    elif frame_first:
        result.skipped.append("framing: no frame found in the profile "
                              "directory")

    if schema_doc is not None:
        try:
            result.issues.extend(check_schema(target, schema_doc))
        except RuntimeError as e:
            result.skipped.append(f"JSON Schema: {e}")
    elif not profile.schema:
        result.skipped.append("JSON Schema: no schema found")

    if profile.shapes:
        try:
            result.issues.extend(check_shacl(target, profile.shapes))
        except RuntimeError as e:
            result.skipped.append(f"SHACL: {e}")
        except Exception as e:                   # noqa: BLE001
            result.skipped.append(f"SHACL: {type(e).__name__}: {e}")
    else:
        result.skipped.append("SHACL: no shapes found")

    return result
