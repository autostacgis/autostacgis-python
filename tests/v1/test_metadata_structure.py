from copy import deepcopy

import pytest

from autostacgis.errors import AutoSTACGISError
from autostacgis.stac import Stac


def item():
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "stac_extensions": [],
        "id": "test",
        "geometry": None,
        "properties": {"datetime": "2026-01-01T00:00:00Z"},
        "links": [],
        "assets": {"data": {"href": "https://example.com/a.tif", "type": "image/tiff"}},
    }


def test_strict_structure_includes_core_and_nested_inline_declarations():
    configuration = {
        "inline": {
            "properties": {
                "properties": {
                    "properties": {"quality": {"type": "object", "properties": {"score": {"type": "number"}}}}
                }
            }
        }
    }
    stac = Stac(metadata_structure=configuration)
    stac.validate(item())
    value = item()
    value["properties"]["quality"] = {"score": 0.9}
    stac.validate(value)
    assert "quality" in stac.metadata_structure.fields(("properties",))
    value["properties"]["quality"]["unknown"] = 1
    with pytest.raises(AutoSTACGISError, match="Undeclared metadata field.*unknown"):
        stac.validate(value)
    value = item()
    value["assets"]["data"]["unknown"] = 1
    with pytest.raises(AutoSTACGISError, match="Undeclared metadata field.*unknown"):
        stac.validate(value)


def test_url_and_inline_schemas_are_combined_and_types_validated():
    stac = Stac(
        metadata_structure={
            "schemas": ["https://example.com/schema.json"],
            "inline": {"properties": {"properties": {"properties": {"other": {"type": "boolean"}}}}},
        }
    )
    stac.schemas["https://example.com/schema.json"] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": {"properties": {"properties": {"score": {"type": "number"}}}},
    }
    value = item()
    value["properties"].update(score=1, other=True)
    stac.validate(value)
    value["properties"]["score"] = "bad"
    with pytest.raises(Exception, match="not of type"):
        stac.validate(value)


def test_inline_local_refs_and_legacy_opt_out():
    stac = Stac(
        metadata_structure={
            "inline": {
                "$defs": {"quality": {"type": "number"}},
                "properties": {"properties": {"properties": {"quality": {"$ref": "#/$defs/quality"}}}},
            }
        }
    )
    value = item()
    value["properties"]["quality"] = 1
    stac.validate(value)
    legacy = deepcopy(value)
    legacy["properties"]["anything"] = True
    Stac().validate(legacy)
    with pytest.raises(AutoSTACGISError, match="anything"):
        stac.validate(legacy)


def test_extension_pass_through_patterns_do_not_declare_unknown_fields():
    from autostacgis.stac import EXTENSIONS, STATE

    stac = Stac(metadata_structure={"schemas": [STATE, EXTENSIONS["proj:"]]})
    value = item()
    value["stac_extensions"] = [STATE, EXTENSIONS["proj:"]]
    value["assets"]["data"].update({"proj:code": "EPSG:4326", "data_state:georeferencing": "affine"})
    stac.validate(value)
    value["properties"]["unknown"] = True
    with pytest.raises(AutoSTACGISError, match="Undeclared metadata field.*unknown"):
        stac.validate(value)

    value["properties"].pop("unknown")
    value["assets"]["data"]["unknown"] = True
    with pytest.raises(AutoSTACGISError, match="Undeclared metadata field.*unknown"):
        stac.validate(value)
