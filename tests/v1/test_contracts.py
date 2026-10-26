import json
from pathlib import Path

import pytest
from helpers import scene

from autostacgis.config import read, validate
from autostacgis.errors import AutoSTACGISError
from autostacgis.metadata import assign, get, matches, transform
from autostacgis.stac import STATE, Stac


def item():
    return scene().item


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).parents[2] / "autostacgis/spec/conformance/metadata.json").read_text())
)
def test_metadata_conformance(case):
    if case.get("error"):
        with pytest.raises(AutoSTACGISError):
            transform(case["inputs"], case["output"])
    else:
        assert transform(case["inputs"], case["output"]) == case["expected"]


@pytest.mark.parametrize("text", ["a: 1\na: 2", "true: 1", "a: .nan", "a: !!set {x: null}", "a: &a [*a]"])
def test_yaml_rejects_non_json(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises((AutoSTACGISError, ValueError)):
        read(path)


def test_json_duplicate_and_yaml_json_parity(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"a":1,"a":2}')
    with pytest.raises(AutoSTACGISError):
        read(bad)
    y = tmp_path / "x.yml"
    j = tmp_path / "x.json"
    y.write_text("date: 2026-01-01\na: [true, null, 1]\n")
    j.write_text('{"date":"2026-01-01","a":[true,null,1]}')
    assert read(y) == read(j)


def test_paths_colons_escaping_and_null():
    data = {}
    assign(data, r"assets.my\.asset.eo:flag", True)
    assert data == {"assets": {"my.asset": {"eo:flag": True}}}
    assert get(data, r"assets.my\.asset.eo:flag") is True
    assert matches({"x": None}, {"x": True}) == "unknown"
    assert matches({"x": False}, {"x": True}) == "fail"
    assert matches({"x": 1}, {"x": True}) == "fail"


def test_optical_schema_and_core():
    stac = Stac()
    document = item()
    document["assets"]["data"]["data_state:pansharpened"] = None
    stac.extensions(document)
    assert STATE in document["stac_extensions"]
    stac.validate(document)
    document["assets"]["data"]["data_state:pansharpened"] = "invalidated"
    with pytest.raises(Exception):
        stac.validate(document)
    document = item()
    document["properties"].pop("datetime")
    with pytest.raises(Exception):
        stac.validate(document)


def test_schema_rejects_invalid_shapes():
    with pytest.raises(AutoSTACGISError):
        validate({"sources": []}, "config")
    with pytest.raises(AutoSTACGISError):
        validate({"version": "0.0.1", "plugins": {"processors": []}}, "config")


def test_path_escaped_backslash_before_separator():
    data = {}
    assign(data, r"properties.key\\.value", 42)
    assert data == {"properties": {"key\\": {"value": 42}}}
    assert get(data, r"properties.key\\.value") == 42
