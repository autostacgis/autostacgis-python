from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from helpers import require_extra, scene, subset

from autostacgis import Application
from autostacgis.data import Window
from autostacgis.errors import NoPlan
from autostacgis.metadata import MISSING
from autostacgis.semantics import value_at
from autostacgis.stac import Stac


def test_band_and_property_defaults_without_mutating_source():
    item = scene().item
    asset = item["assets"]["data"]
    item["properties"]["unit"] = asset.pop("unit")
    asset["bands"] = [{"name": "red"}, {"name": "nir"}]
    before = deepcopy(item)
    assert value_at(item, "assets.data.unit") == "1"
    assert item == before
    asset["bands"][1]["unit"] = "K"
    assert value_at(item, "assets.data.unit") is MISSING


@pytest.mark.parametrize("unit", [None, "K"])
def test_quantity_alone_does_not_satisfy_physical_product_goal(tmp_path, unit):
    image = scene()
    image.item["assets"]["data"].update(
        {"cf:standard_name": "surface_bidirectional_reflectance", "unit": unit}
    )
    with pytest.raises(NoPlan):
        Application(subset(), base_dir=tmp_path).plan(
            {
                "version": "1.0.0",
                "goal": {
                    "for_each": {
                        "kind": "raster",
                        "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                        "require": {
                            "all": [
                                {
                                    "path": "assets.$asset.cf:standard_name",
                                    "equals": "surface_bidirectional_reflectance",
                                },
                                {"path": "assets.$asset.unit", "equals": "1"},
                            ]
                        },
                    }
                },
            },
            [image],
        )


def test_pansharpen_decodes_scale_offset_and_updates_band_state(tmp_path):
    image, pan = scene(value=1000), scene("pan", "panchromatic_imagery", count=1, value=4000)
    image.item["assets"]["data"]["bands"] = [
        {"name": str(i), "raster:scale": 0.0001, "raster:offset": -0.05, "data_state:pansharpened": False}
        for i in range(3)
    ]
    pan.item["assets"]["data"].update({"raster:scale": 0.0001, "raster:offset": 0})
    before = deepcopy(image.item)
    with Application(subset("pansharpen"), base_dir=tmp_path).run(
        {
            "version": "1.0.0",
            "goal": {
                "for_each": {
                    "kind": "raster",
                    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                    "require": {"all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]},
                }
            },
        },
        [image, pan],
    ) as run:
        result = run.records[0]
        np.testing.assert_allclose(result.payload.read(Window(0, 0, 4, 4)).pixels, 0.05)
        asset = result.item["assets"]["data"]
        assert asset["raster:scale"] == 1 and asset["raster:offset"] == 0
        assert all(b["data_state:pansharpened"] is True and b["raster:offset"] == 0 for b in asset["bands"])
        Stac().validate(result.item)
    assert image.item == before


def test_local_header_uses_standard_fields_and_detects_conflicts(tmp_path):
    import rasterio
    from affine import Affine

    from autostacgis.errors import AutoSTACGISError
    from autostacgis.raster_metadata import inspect_raster

    path = tmp_path / "physical.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=3,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=Affine(1, 0, 0, 0, -1, 3),
    ) as dst:
        dst.update_tags(
            scale_factor="0.0001", add_offset="-0.1", standard_name="surface_bidirectional_reflectance"
        )
    record = scene(count=1)
    asset = record.item["assets"]["data"]
    asset.pop("cf:standard_name")
    asset["href"] = str(path)
    app = SimpleNamespace(stac=Stac())
    inspect_raster(record, {}, app)
    assert asset["proj:shape"] == [3, 2] and "autostacgis:grid" not in asset
    assert value_at(record.item, "assets.data.cf:standard_name") == "surface_bidirectional_reflectance"
    assert asset["bands"][0]["raster:offset"] == -0.1
    assert "statistics" not in asset["bands"][0]
    Stac().validate(record.item)
    asset["bands"][0]["raster:scale"] = 1
    with pytest.raises(AutoSTACGISError, match="conflict"):
        inspect_raster(record, {}, app)


def test_eodag_rules_use_same_standard_fields_and_retain_provider_metadata():
    pytest.importorskip("eodag.autostacgis")
    from eodag.autostacgis import describe_product

    source = scene().item
    source["properties"]["product:type"] = "L2A"
    source["assets"]["data"].pop("cf:standard_name")
    product = SimpleNamespace(as_dict=lambda: source)
    rules = [
        {
            "when": {"product:type": "L2A"},
            "assets": ["data"],
            "set": {
                "cf:standard_name": "surface_bidirectional_reflectance",
                "unit": "1",
                "data_state:atmospheric_correction": "full",
            },
        }
    ]
    result = describe_product(product, rules)
    assert "cf:standard_name" not in source["assets"]["data"]
    assert result["properties"] == source["properties"]
    Stac().validate(result)
    with pytest.raises(ValueError, match="Conflicting"):
        describe_product(product, [{"assets": ["data"], "set": {"unit": "K"}}])


def test_eoreader_enrichment_reconciles_existing_asset_identity(tmp_path, monkeypatch):
    require_extra("eoreader")
    from autostacgis_plugins.eoreader import eoreader_metadata
    from eoreader.reader import Reader

    path = tmp_path / "scene.tif"
    path.touch()
    record = scene()
    record.item["assets"]["data"]["href"] = path.as_uri()
    record.item["assets"]["data"].pop("cf:standard_name")
    record.runtime["product_path"] = str(path)
    exported = deepcopy(record.item)
    exported["assets"]["RED"] = exported["assets"].pop("data")
    exported["assets"]["RED"]["href"] = str(path)
    exported["assets"]["RED"]["cf:standard_name"] = "toa_bidirectional_reflectance"
    fake = SimpleNamespace(
        stac=SimpleNamespace(create_item=lambda: SimpleNamespace(to_dict=lambda: deepcopy(exported))),
        close=lambda: None,
    )
    monkeypatch.setattr(Reader, "open", lambda *args, **kwargs: fake)
    app = SimpleNamespace(work=tmp_path / "work", stac=Stac())
    result = eoreader_metadata(record, {}, app)
    assert list(result.item["assets"]) == ["data"]
    assert result.item["assets"]["data"]["cf:standard_name"] == "toa_bidirectional_reflectance"
    assert result.runtime["local_assets"]["data"] == str(path)
