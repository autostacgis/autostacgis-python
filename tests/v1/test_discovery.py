import json
from pathlib import Path

import pytest
from autostacgis_plugins.atmosphere import AtmosphereSource
from autostacgis_plugins.dem import DemSource
from autostacgis_plugins.table import TableSource
from helpers import scene, subset

from autostacgis import Application
from autostacgis.data.writers import write_raster
from autostacgis.errors import AutoSTACGISError
from autostacgis.stac import Stac


def test_existing_sidecar_is_reused(tmp_path):
    image = scene()
    path = tmp_path / "scene.tif"
    write_raster(image.payload, path)
    image.item["assets"]["data"]["href"] = path.as_uri()
    Stac.extensions(image.item)
    Path(str(path) + ".stac.json").write_text(json.dumps(image.item))
    config = subset("pansharpen")
    next(p["capabilities"]["metadata_in"] for p in config["plugins"] if p["id"] == "stac-resolver")[
        "settings"
    ] = {"search_glob": "*.tif"}
    result = Application(config, base_dir=tmp_path).discover(
        {
            "version": "0.0.1",
            "goal": {
                "for_each": {
                    "kind": "raster",
                    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                    "require": {"all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]},
                }
            },
            "sources": ["stac-resolver"],
        }
    )
    assert result[0].item == image.item


def test_glob_metadata_rules_and_conflict(tmp_path):
    image = scene()
    path = tmp_path / "ms.tif"
    write_raster(image.payload, path)
    config = subset("pansharpen")
    next(p["capabilities"]["metadata_in"] for p in config["plugins"] if p["id"] == "stac-resolver")[
        "settings"
    ] = {
        "search_glob": "*.tif",
        "metadata_rules": [
            {
                "match": "ms.*",
                "set": [
                    {"assets.data.data_state:spectral_sampling": "multispectral"},
                    {"assets.data.data_state:pansharpened": False},
                ],
            }
        ],
    }
    app = Application(config, base_dir=tmp_path)
    found = app.discover(
        {
            "version": "0.0.1",
            "goal": {
                "for_each": {
                    "kind": "raster",
                    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                    "require": {"all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]},
                }
            },
            "sources": ["stac-resolver"],
        }
    )
    assert found[0].item["assets"]["data"]["data_state:pansharpened"] is False
    image.item["assets"]["data"]["href"] = str(path)
    image.item["assets"]["data"]["data_state:pansharpened"] = True
    Path(str(path) + ".stac.json").write_text(json.dumps(image.item))
    with pytest.raises(AutoSTACGISError, match="conflicts"):
        app.discover(
            {
                "version": "0.0.1",
                "goal": {
                    "for_each": {
                        "kind": "raster",
                        "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                        "require": {
                            "all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]
                        },
                    }
                },
                "sources": ["stac-resolver"],
            }
        )


def test_ancillary_discovery_defers_network(tmp_path, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: pytest.fail("Network during discovery"))
    query = {"area": [0, 0, 1, 1], "time_range": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]}
    for source in (DemSource(), AtmosphereSource()):
        record = list(source.discover({}, query, None))[0]
        assert record.payload is None and record.item["assets"]["data"]["href"].startswith("autostacgis:")
    record = list(TableSource().discover({"values": {"aot550": 0.1}}, query, None))[0]
    assert next(record.payload.batches()).column("aot550")[0].as_py() == 0.1


def test_native_product_discovery_fixtures(tmp_path):
    pytest.importorskip("eoreader.reader")
    from autostacgis.product_discovery import discover_products

    fixtures = json.loads((Path(__file__).parents[1] / "fixtures/eoreader_products.json").read_text())
    for name, markers in fixtures.items():
        root = tmp_path / (name + "_case")
        product = root / name
        for marker in markers:
            path = product / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}" if path.suffix == ".json" else "fixture")
        assert discover_products(root_dir=root, constellations=[name]) == [product]


def test_worldview_components_discover_as_one_product(tmp_path):
    pytest.importorskip("eoreader.reader")
    from autostacgis.product_discovery import discover_products

    for mode, folder in (("M", "MUL"), ("P", "PAN")):
        directory = tmp_path / folder
        directory.mkdir()
        (directory / f"17DEC08211835-{mode}1BS-012345678901_01_P001.TIL").touch()
    assert discover_products("**/*.TIL", root_dir=tmp_path, constellations=["WV03"]) == [tmp_path / "MUL"]
    assert discover_products("PAN/*.TIL", root_dir=tmp_path, constellations=["WV03"]) == [tmp_path / "MUL"]


def test_directory_root_recomputed_after_publication(tmp_path):
    image = scene()
    image.item["properties"]["data_state:directory_root"] = "/old/input"
    Stac.extensions(image.item)
    from autostacgis.publication import publish

    app = Application(subset(), base_dir=tmp_path)
    published = publish(app, image, {"encode": {"plugin": "geotiff"}, "data_out": {"plugin": "filesystem"}})
    href = published.item["assets"]["data"]["href"]
    assert published.item["properties"]["data_state:directory_root"] == href.rsplit("/", 1)[0]
    import rasterio

    with rasterio.open(href) as source:
        embedded = json.loads(source.tags()["STAC"])
        assert embedded["properties"]["data_state:directory_root"] == href.rsplit("/", 1)[0]


def test_grouped_worldview_enters_inventory_with_both_assets(tmp_path, monkeypatch):
    from copy import deepcopy

    pytest.importorskip("eoreader.reader")
    from types import SimpleNamespace

    from eoreader.reader import Reader

    mul, pan = scene("mul"), scene("pan", "panchromatic_imagery", count=1)
    for mode, folder in (("M", "MUL"), ("P", "PAN")):
        directory = tmp_path / folder
        directory.mkdir()
        (directory / f"17DEC08211835-{mode}1BS-012345678901_01_P001.TIL").touch()
    combined = mul.item
    combined["id"] = "worldview-pair"
    combined["assets"] = {"mul": combined["assets"]["data"], "pan": pan.item["assets"]["data"]}
    for name in ("mul", "pan"):
        combined["assets"][name]["href"] = str(next((tmp_path / name.upper()).glob("*.TIL")))
    combined["properties"]["data_state:directory_root"] = str(tmp_path)
    combined["assets"]["pan"]["datetime"] = "2024-01-01T00:00:00.2Z"
    Stac.extensions(combined)
    product = SimpleNamespace(
        stac=SimpleNamespace(
            create_item=lambda **kwargs: SimpleNamespace(to_dict=lambda: deepcopy(combined))
        ),
        close=lambda: None,
    )
    monkeypatch.setattr(Reader, "open", lambda self, path, **kwargs: product)
    app = Application(
        {
            "version": "0.0.1",
            "plugins": [
                {"use": "eoreader", "settings": {"search_glob": "**/*.TIL"}},
                {"use": "pansharpen"},
            ],
        },
        base_dir=tmp_path,
    )
    request = {
        "sources": ["eoreader"],
        "goal": {
            "for_each": {
                "kind": "raster",
                "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                "require": {"all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]},
            }
        },
    }
    records = app.discover(request)
    assert len(records) == 1
    assert set(records[0].item["assets"]) == {"mul", "pan"}
    # Acquisition reinspection must not compare the PAN's actual sensing time
    # against the combined Item's representative datetime.
    from autostacgis_plugins.eoreader import eoreader_metadata

    again = eoreader_metadata(records[0].clone(), {}, app.scope("eoreader"))
    assert again.item["assets"]["pan"]["datetime"] == "2024-01-01T00:00:00.2Z"
    component = pan.clone()
    component.item["properties"]["datetime"] = "2024-01-01T00:00:00.2Z"
    component.item["properties"]["data_state:directory_root"] = str(tmp_path / "PAN")
    component.item["assets"]["data"]["href"] = combined["assets"]["pan"]["href"]
    component.runtime["product_path"] = str(tmp_path / "PAN")
    paired = eoreader_metadata(component, {}, app.scope("eoreader"))
    assert paired.item["properties"]["data_state:directory_root"] == str(tmp_path)
    assert paired.item["properties"]["datetime"] == combined["properties"]["datetime"]
    plan = app.plan(request, records)
    assert len(plan.initial) == 2
    assert [step.module for step in plan.steps] == ["pansharpen"]
