import json
from types import SimpleNamespace

import numpy as np
import pytest
from helpers import require_extra, scene, subset

from autostacgis import Application
from autostacgis.data import ArrayRaster, Grid
from autostacgis.errors import AmbiguousProducer, AutoSTACGISError


def test_namespace_loads_only_selected_entrypoints(monkeypatch):
    import autostacgis.registry as module

    loaded = []

    def provider():
        loaded.append("selected")

        def goal(settings, records, context):
            return {"output": {}}

        goal.contract = {
            "solver": "backward_chain",
            "settings_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
        return SimpleNamespace(
            ID="custom", VERSION="1.0.0", REQUIRES=("backward_chain",), CAPABILITY="goal", IMPLEMENTATION=goal
        )

    def unused():
        raise AssertionError("Unselected plugin was imported")

    monkeypatch.setattr(
        module,
        "entry_points",
        lambda **kw: [
            SimpleNamespace(name="custom", load=provider),
            SimpleNamespace(name="unused", load=unused),
        ],
    )
    app = Application({"version": "1.0.0", "plugins": [{"use": "custom"}]})
    assert set(app.plugins) == {"custom", "backward_chain", "buffered-transfer"} and loaded == ["selected"]


def test_namespace_can_configure_dependency_by_qualified_module(tmp_path):
    app = Application(
        {
            "version": "1.0.0",
            "plugins": [
                {"use": "pansharpen"},
                {"use": "autostacgis_plugins.storage", "settings": {"tile_size": 256}},
            ],
        }
    )
    assert "pansharpen" in app.plugins
    assert app.plugins["storage"]["capabilities"]["services"]["settings"]["tile_size"] == 256
    from autostacgis.runtime.registry import Services

    with Services(app.registry, app.catalog["runtime"], directory=tmp_path, plugins=app.plugins) as services:
        assert services.get("storage").tile_size == 256


def test_competing_algorithms_require_explicit_selection():
    app = Application(
        subset("weighted_seamlines", "markov_seamlines", "extract_footprints", "clean_footprints")
    )
    with pytest.raises(AmbiguousProducer, match="only one plugin"):
        app.plan(
            {
                "goal": {
                    "output": {
                        "kind": "vector",
                        "require": {"path": "assets.$asset.roles", "contains": "mosaic-seamline-polygons"},
                    }
                }
            },
            [scene()],
        )


def test_dem_mean_ignores_invalid_pixels_and_supplies_py6s(context, monkeypatch):
    import autostacgis_plugins.atmospheric_correction as correction
    from autostacgis_plugins.atmospheric_correction import atmospheric_correction
    from autostacgis_plugins.sample_dem import sample_dem

    image, dem = scene(count=1), scene("dem", "digital_elevation_model", count=1)
    dem.payload = ArrayRaster(
        np.array([[[100, 200], [300, -9999]]], dtype="float32"),
        Grid(2, 2, 1, "float32", (2, 0, 0, 0, -2, 4), "EPSG:4326", nodata=-9999),
        valid=np.array([[[True, True], [True, False]]]),
    )
    result = sample_dem({"image": [image], "dem": [dem]}, {}, context)["result"][0]
    assert result.item["properties"]["autostacgis:mean_elevation_m"] == 200
    captured = []

    def coefficients(parameters):
        captured.append(parameters)
        return [[1, 0, 0]]

    monkeypatch.setattr(correction, "coefficients", coefficients)
    settings = {
        "solar_zenith": 30,
        "solar_azimuth": 10,
        "view_zenith": 0,
        "view_azimuth": 0,
        "water_vapor": 2,
        "ozone": 0.3,
        "aot550": 0.1,
        "wavelengths_um": [0.55],
    }
    atmospheric_correction({"image": [image], "elevation": [result]}, settings, context)
    assert captured[0]["elevation_km"] == 0.2
    with pytest.raises(AutoSTACGISError, match="elevation input"):
        atmospheric_correction({"image": [image]}, settings, context)
    dem.payload.valid[:] = False
    with pytest.raises(AutoSTACGISError, match="no valid elevation"):
        sample_dem({"image": [image], "dem": [dem]}, {}, context)


def test_geotiff_embeds_stac_and_maps_native_band_metadata(tmp_path):
    import rasterio

    from autostacgis.data.writers import write_raster

    record = scene(count=1)
    record.item["assets"]["data"]["bands"] = [
        {
            "name": "green",
            "unit": "1",
            "raster:scale": 0.001,
            "raster:offset": 2,
            "eo:center_wavelength": 0.55,
        }
    ]
    path = tmp_path / "output.tif"
    write_raster(record.payload, path, stac=record.item)
    with rasterio.open(path) as dataset:
        assert json.loads(dataset.tags()["STAC"]) == record.item
        assert dataset.descriptions == ("green",)
        assert dataset.scales == (0.001,) and dataset.offsets == (2,)
        assert dataset.tags()["TIFFTAG_DATETIME"] == "2024:01:01 00:00:00"
        assert dataset.units == ("1",)


@pytest.mark.parametrize(
    "name,port",
    [
        ("register_to_reference", "image"),
        ("joint_coregistration", "images"),
        ("global_radiometric_match", "images"),
        ("local_radiometric_match", "images"),
    ],
)
def test_masking_lowers_registration_and_normalization_costs(name, port):
    from autostacgis.contracts import cost_of

    contract = Application().modules({"modules": [name]})[name]
    raw, masked = scene(), scene()
    masked.item["assets"]["data"]["data_state:masked"].update(cloud=True, cloud_shadow=True)
    assert cost_of(contract, {port: [masked]}, {}) < cost_of(contract, {port: [raw]}, {})
    assert cost_of(contract, {port: [raw, masked]}, {}) == cost_of(contract, {port: [raw]}, {})


def test_planned_dem_summary_is_consumed_by_py6s(tmp_path, monkeypatch):
    import autostacgis_plugins.atmospheric_correction as correction
    import pyarrow as pa

    from autostacgis.data import Batches

    image, dem, atmosphere = scene(count=1), scene("dem", "digital_elevation_model", count=1), scene("air")
    image.item["assets"]["data"].update(
        {"cf:standard_name": "toa_outgoing_radiance_per_unit_wavelength", "unit": "W m-2 sr-1 um-1"}
    )
    dem.item["assets"]["data"].update({"cf:standard_name": "surface_altitude", "unit": "m"})
    dem.payload = ArrayRaster(np.full((1, 4, 4), 200, dtype="float32"), dem.payload.grid)
    atmosphere.kind = "table"
    atmosphere.item["assets"]["data"]["roles"] = ["data", "atmosphere"]
    atmosphere.payload = Batches(
        pa.Table.from_pylist([{"water_vapor": 2.0, "ozone": 0.3, "aot550": 0.1}]).to_batches(), kind="table"
    )
    measured = []

    def coefficients(parameters):
        measured.append(parameters["elevation_km"])
        return [[0.02, 0, 0]]

    monkeypatch.setattr(correction, "coefficients", coefficients)
    app = Application(
        {
            "version": "1.0.0",
            "plugins": [
                {"use": name} for name in ("atmospheric_correction", "sample_dem", "executor", "events")
            ],
        },
        base_dir=tmp_path,
    )
    request = {
        "goal": {"output": {"require": {"assets.$asset.data_state:atmospheric_correction": "full"}}},
        "module_settings": {
            "atmospheric_correction": {
                "solar_zenith": 30,
                "solar_azimuth": 0,
                "view_zenith": 0,
                "view_azimuth": 0,
                "wavelengths_um": [0.55],
            }
        },
    }
    plan = app.plan(request, [image, dem, atmosphere])
    assert [s.module for s in plan.steps] == ["sample_dem", "atmospheric_correction"]
    with app.run(request, [image, dem, atmosphere]) as result:
        assert result.records[0].item["assets"]["data"]["data_state:atmospheric_correction"] == "full"
        block = result.records[0].payload.read(next(result.records[0].payload.windows()))
        np.testing.assert_allclose(block.pixels, 0.2)
    assert measured == [0.2]


def test_automatically_planned_normalization_to_markov_mosaic_executes(tmp_path):
    require_extra("spectralmatch")
    require_extra("geopandas")
    from autostacgis.data import Window
    from autostacgis.data.metadata import refresh_metadata

    images = [scene("one"), scene("two")]
    values = (np.arange(4096).reshape(64, 64) % 311 + 100).astype("float32")
    for index, image in enumerate(images):
        image.payload = ArrayRaster(
            np.stack([values + i * 10 + index * 5 for i in range(3)]),
            Grid(64, 64, 3, "float32", (1, 0, 500000 + index * 20, 0, -1, 2400000), "EPSG:32604"),
        )
        image.item["assets"]["data"]["data_state:local_radiometric_adjustment"] = False
        refresh_metadata(image)
    app = Application(
        {
            "version": "1.0.0",
            "plugins": [
                {"use": name}
                for name in [
                    "global_radiometric_match",
                    "local_radiometric_match",
                    "extract_footprints",
                    "clean_footprints",
                    "markov_seamlines",
                    "mask_by_polygons",
                    "mosaic",
                    "executor",
                    "events",
                ]
            ],
        },
        base_dir=tmp_path,
    )
    request = {
        "goal": {
            "output": {
                "require": {
                    "assets.$asset.data_state:mosaic": True,
                    "assets.$asset.data_state:relative_radiometric_normalization": True,
                    "assets.$asset.data_state:local_radiometric_adjustment": True,
                }
            }
        },
        "module_settings": {
            "global_radiometric_match": {"pif_method": "entire", "window_size": 16},
            "local_radiometric_match": {"number_of_blocks": 4, "window_size": 16},
            "clean_footprints": {
                "hole_edge_distance": 0,
                "hole_to_hole_distance": 0,
                "simplify_smoothing_radius": 0,
                "simplify_tolerance": 0,
            },
            "markov_seamlines": {
                "mesh_spacing": 16,
                "solver_max_iterations": 10,
                "edge_variables_weights_gsds_bands": [["laplacian_difference", 1, ["native"], [1]]],
            },
        },
    }
    plan = app.plan(request, images)
    modules = [s.module for s in plan.steps]
    assert (
        modules.index("global_radiometric_match")
        < modules.index("local_radiometric_match")
        < modules.index("mask_by_polygons")
    )
    with app.run(request, images) as result:
        record = result.records[0]
        block = record.payload.read(Window(0, 0, 84, 64))
        assert block.valid.any() and np.isfinite(block.pixels[block.valid]).all()
        assert record.item["assets"]["data"]["data_state:mosaic"] is True
