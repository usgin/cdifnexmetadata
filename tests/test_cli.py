"""Tests for the command line entry point."""
from __future__ import annotations

import json

import pytest

h5py = pytest.importorskip("h5py")
import numpy as np  # noqa: E402

from cdifnexmetadata.cli import main  # noqa: E402

from tests.test_emit import _scan  # noqa: E402


def _file(tmp_path, name="scan.nxs", entries=("scan1",)):
    p = tmp_path / name
    with h5py.File(p, "w") as f:
        for e in entries:
            _scan(f, e, title=f"title {e}")
    return p


def test_a_document_goes_to_stdout_by_default(tmp_path, capsys):
    assert main([str(_file(tmp_path)), "-q"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["@type"] == ["schema:Dataset"]
    assert doc["schema:distribution"]


def test_output_to_a_named_file(tmp_path, capsys):
    out = tmp_path / "result.jsonld"
    assert main([str(_file(tmp_path)), "-o", str(out), "-q"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema:name"]
    assert capsys.readouterr().out == ""       # nothing on stdout


def test_several_files_go_to_a_directory(tmp_path, capsys):
    a = _file(tmp_path, "a.nxs")
    b = _file(tmp_path, "b.nxs")
    out = tmp_path / "out"
    assert main([str(a), str(b), "-o", str(out), "-q"]) == 0
    assert {p.name for p in out.iterdir()} == {"a.jsonld", "b.jsonld"}


def test_several_files_with_a_file_output_is_refused(tmp_path, capsys):
    """Writing two documents to one path would silently keep only the
    second."""
    a, b = _file(tmp_path, "a.nxs"), _file(tmp_path, "b.nxs")
    assert main([str(a), str(b), "-o", str(tmp_path / "one.jsonld")]) == 2
    assert "must be a directory" in capsys.readouterr().err


def test_a_missing_file_is_reported_and_does_not_stop_the_rest(
    tmp_path, capsys
):
    good = _file(tmp_path, "good.nxs")
    out = tmp_path / "out"
    status = main([str(tmp_path / "nope.nxs"), str(good), "-o", str(out), "-q"])
    assert status == 2
    assert "no such file" in capsys.readouterr().err
    assert (out / "good.jsonld").is_file()      # the good one still ran


def test_base_uri_is_configurable(tmp_path, capsys):
    main([str(_file(tmp_path)), "--base", "https://example.org/data", "-q"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["@id"].startswith("https://example.org/data/")


def test_source_url_is_used_for_the_distribution(tmp_path, capsys):
    url = "https://example.org/files/scan.nxs"
    main([str(_file(tmp_path)), "--source-url", url, "-q"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema:distribution"][0]["schema:contentUrl"] == url


def test_no_legacy_suppresses_the_legacy_table(tmp_path, capsys):
    p = tmp_path / "legacy.nxs"
    with h5py.File(p, "w") as f:
        e = _scan(f, "scan1")
        scan = e.create_group("scan")
        scan.attrs["NX_class"] = "NXscan"
        xe = scan.create_group("xrayedge")
        xe.attrs["NX_class"] = "NXxrayedge"
        xe["element"] = "Fe"
        xe["edge"] = "K"

    main([str(p), "-q"])
    with_legacy = json.loads(capsys.readouterr().out)
    main([str(p), "--no-legacy", "-q"])
    without = json.loads(capsys.readouterr().out)

    assert "schema:keywords" in with_legacy      # element and edge recovered
    assert "schema:keywords" not in without


def test_report_goes_to_stderr_so_stdout_stays_a_document(tmp_path, capsys):
    """The document must remain pipeable even when diagnostics are on."""
    main([str(_file(tmp_path, entries=("s1", "s2"))), "--report"])
    captured = capsys.readouterr()
    json.loads(captured.out)                     # stdout is still valid JSON
    assert "crosswalk:" in captured.err
    assert "concepts" in captured.err


def test_the_claimed_profiles_are_announced(tmp_path, capsys):
    main([str(_file(tmp_path))])
    assert "claims core/1.1" in capsys.readouterr().err


def test_validate_without_a_profile_warns_rather_than_passing(
    tmp_path, capsys
):
    """A run that checked nothing must not read like a run that found
    nothing wrong."""
    status = main([str(_file(tmp_path)), "--validate"])
    err = capsys.readouterr().err
    assert "no profile artifacts found" in err
    assert "not checked" in err
    assert status == 0            # nothing was found wrong, either


def test_validation_failure_sets_the_exit_code(tmp_path, capsys):
    """Exit codes are meant to be usable in a pipeline."""
    pytest.importorskip("jsonschema")
    pytest.importorskip("pyld")
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "xResolvedSchema.json").write_text(json.dumps(
        {"type": "object", "required": ["schema:citation"]}), encoding="utf-8")
    (profile / "x-frame.jsonld").write_text(json.dumps(
        {"@context": {"schema": "http://schema.org/"},
         "@type": "schema:Dataset", "schema:name": {}}), encoding="utf-8")

    status = main([str(_file(tmp_path)), "--validate",
                   "--profile-dir", str(profile), "-o",
                   str(tmp_path / "o.jsonld")])
    assert status == 1
    assert "validation FAILED" in capsys.readouterr().err


def test_a_plain_hdf5_file_still_produces_a_document(tmp_path, capsys):
    p = tmp_path / "plain.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("x", data=np.arange(10))
    assert main([str(p)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["schema:name"] == "plain"
    assert "no NeXus markers" in captured.err
