"""Tests for profile validation (stage 3b).

Uses tiny synthetic profiles rather than the real CDIF artifacts, so the
suite runs offline and a failure means this module changed rather than
that a profile was revised.
"""
from __future__ import annotations

import json

import pytest

from cdifnexmetadata.validate import (  # noqa: E402
    Issue,
    Profile,
    ValidationResult,
    _main_entity,
    _undo_framing_artifacts,
    find_profile,
    validate_document,
)

SCHEMA = {
    "type": "object",
    "required": ["@id", "schema:name"],
    "properties": {
        "@id": {"type": "string"},
        "schema:name": {"type": "string"},
        "schema:keywords": {"type": "array", "items": {"type": "string"}},
        "schema:identifier": {"type": "string"},
    },
}

FRAME = {
    "@context": {"schema": "http://schema.org/"},
    "@type": "schema:Dataset",
    "schema:name": {},
    "schema:keywords": {},
}

SHAPES = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix schema: <http://schema.org/> .

<http://example.org/DatasetShape> a sh:NodeShape ;
    sh:targetClass schema:Dataset ;
    sh:property [ sh:path schema:name ; sh:minCount 1 ;
                  sh:message "a dataset needs a name" ] ;
    sh:property [ sh:path schema:description ; sh:minCount 1 ;
                  sh:severity sh:Warning ;
                  sh:message "a description is recommended" ] .
"""


def _profile_dir(tmp_path, schema=SCHEMA, frame=FRAME, shapes=SHAPES):
    d = tmp_path / "profile"
    d.mkdir(exist_ok=True)
    if schema is not None:
        (d / "thingResolvedSchema.json").write_text(
            json.dumps(schema), encoding="utf-8")
    if frame is not None:
        (d / "thing-frame.jsonld").write_text(
            json.dumps(frame), encoding="utf-8")
    if shapes is not None:
        (d / "thingRules.shacl").write_text(shapes, encoding="utf-8")
    return d


def _doc(**extra):
    return {
        "@context": {"schema": "http://schema.org/"},
        "@id": "http://example.org/d1",
        "@type": ["schema:Dataset"],
        "schema:name": "a dataset",
        **extra,
    }


# ---------------------------------------------------------------------------
# locating artifacts
# ---------------------------------------------------------------------------

def test_artifacts_are_found_by_pattern_not_by_name(tmp_path):
    """Release directories name artifacts after the profile, so a
    hardcoded filename would tie this to one profile."""
    p = find_profile(_profile_dir(tmp_path))
    assert p.schema and p.schema.name.endswith("ResolvedSchema.json")
    assert p.frame and p.frame.name.endswith("-frame.jsonld")
    assert p.shapes and p.shapes.suffix == ".shacl"
    assert not p.is_empty


def test_a_missing_directory_is_an_empty_profile_not_an_error(tmp_path):
    p = find_profile(tmp_path / "nope")
    assert p.is_empty and p.source


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def test_a_good_document_passes_both_checks(tmp_path):
    pytest.importorskip("jsonschema")
    pytest.importorskip("pyld")
    result = validate_document(
        _doc(**{"schema:description": "present"}),
        find_profile(_profile_dir(tmp_path)))
    assert result.valid, [str(i) for i in result.issues]
    assert not result.skipped


def test_a_schema_violation_fails_and_says_where(tmp_path):
    pytest.importorskip("jsonschema")
    pytest.importorskip("pyld")
    bad = _doc()
    bad["schema:identifier"] = ["not", "a", "string"]
    result = validate_document(bad, find_profile(_profile_dir(tmp_path)))
    assert not result.valid
    assert any(i.source == "schema" and "identifier" in i.path
               for i in result.failures)


def test_shacl_warnings_are_reported_but_do_not_fail(tmp_path):
    """A NeXus file genuinely does not record a creator or a contact
    point. Failing on that would make the check useless rather than
    strict."""
    pytest.importorskip("pyshacl")
    pytest.importorskip("pyld")
    result = validate_document(_doc(), find_profile(_profile_dir(tmp_path)))
    advisories = [i for i in result.issues if not i.is_failure]
    assert any("recommended" in i.message for i in advisories)
    assert result.valid


# ---------------------------------------------------------------------------
# never a silent pass
# ---------------------------------------------------------------------------

def test_no_profile_is_reported_as_unchecked_not_as_valid(tmp_path):
    """The failure this guards against: a run that checked nothing
    looking exactly like a run that found nothing wrong."""
    result = validate_document(_doc(), Profile())
    assert not result.valid
    assert result.ran_nothing
    assert "not checked" in result.summary()
    assert not result.failures        # and not by pretending there were


def test_a_missing_check_is_skipped_not_passed(tmp_path):
    pytest.importorskip("pyld")
    # Frame and shapes present, no schema.
    d = _profile_dir(tmp_path, schema=None)
    result = validate_document(_doc(), find_profile(d))
    assert any("no schema" in s for s in result.skipped)
    assert "skipped" in result.summary()


def test_summary_distinguishes_failures_from_advisories():
    r = ValidationResult(issues=[
        Issue("schema", "Violation", "broken"),
        Issue("shacl", "Warning", "improvable"),
    ])
    assert r.summary().startswith("FAILED (1)")
    assert "1 advisory" in r.summary()
    assert not r.valid


# ---------------------------------------------------------------------------
# framing artifacts
# ---------------------------------------------------------------------------

def test_nulls_inserted_by_framing_are_removed():
    """A frame inserts null for every property it declares and the
    document lacks, so an absent optional property arrives looking like
    an explicitly empty one."""
    cleaned = _undo_framing_artifacts(
        {"a": 1, "b": None, "c": {"d": None, "e": 2}, "f": [1, None, 2]})
    assert cleaned == {"a": 1, "c": {"e": 2}, "f": [1, 2]}


def test_single_element_type_is_restored_to_an_array():
    cleaned = _undo_framing_artifacts({"@type": "schema:Dataset"})
    assert cleaned["@type"] == ["schema:Dataset"]


def test_compacted_conformance_uris_are_re_expanded():
    """Framing compacts IRI values against the document's own context, so
    a conformance URI written in full comes back as cdif:core/1.1 and no
    longer matches the const it is checked against."""
    cleaned = _undo_framing_artifacts(
        {"dcterms:conformsTo": [{"@id": "cdif:core/1.1"}]})
    assert cleaned["dcterms:conformsTo"][0]["@id"] == (
        "https://w3id.org/cdif/core/1.1")


def test_property_keys_keep_their_prefix():
    """Only @id *values* are re-expanded; cdif:-prefixed property keys
    are meant to stay compact."""
    cleaned = _undo_framing_artifacts({"cdif:hasPhysicalMapping": {"a": 1}})
    assert "cdif:hasPhysicalMapping" in cleaned


def test_the_dataset_is_picked_out_of_a_framed_graph():
    """The catalog record is an IRI and so stands as a node of its own.
    The profile schema describes the dataset, so that is what is checked.
    """
    record = {"@id": "x/metadata",
              "schema:additionalType": [{"@id": "dcat:CatalogRecord"}]}
    dataset = {"@id": "x", "schema:distribution": [{}]}
    assert _main_entity([record, dataset]) is dataset
    assert _main_entity([record]) is None


def test_collapsed_arrays_are_restored_where_the_schema_wants_them(tmp_path):
    """Framing compacts a single-element array to a bare value, so a
    document that correctly emitted [x] arrives as x and fails a
    type: array check it never actually violated."""
    pytest.importorskip("jsonschema")
    pytest.importorskip("pyld")
    doc = _doc(**{"schema:keywords": ["one"]})
    result = validate_document(doc, find_profile(_profile_dir(tmp_path)))
    assert result.valid, [str(i) for i in result.issues]
    assert result.framed["schema:keywords"] == ["one"]


def test_repair_never_hides_a_real_type_error(tmp_path):
    """The repair only ever adds a wrapper, which framing only ever
    removes. A value of the wrong type inside the array is still a
    finding."""
    pytest.importorskip("jsonschema")
    pytest.importorskip("pyld")
    doc = _doc(**{"schema:keywords": [1, 2]})
    result = validate_document(doc, find_profile(_profile_dir(tmp_path)))
    assert not result.valid
