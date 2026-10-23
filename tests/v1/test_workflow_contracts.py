"""Regression tests for the real boundaries in the WorldView workflow."""

import json
from dataclasses import replace

import numpy as np
import pytest
from autostacgis_plugins.common_crs import common_crs
from autostacgis_plugins.register_to_reference import register_to_reference
from helpers import scene, subset
from shapely.geometry import box

from autostacgis import Application, Record
from autostacgis.data import ArrayRaster, Grid, Window, vectors
from autostacgis.errors import AutoSTACGISError, NoPlan


def test_rpc_survives_stac_py6s_and_lazy_orthorectification(context):
    import os

    from autostacgis_plugins.atmospheric_correction import atmospheric_correction
    from autostacgis_plugins.orthorectify_rpc import orthorectify_rpc

    executable = os.environ.get("AUTOSTACGIS_TEST_SIXS")
    if not executable:
        pytest.skip("native 6S binary required")

    def coefficients(at, value):
        return " ".join(str(value if i == at else 0) for i in range(20))

    rpc = {
        "LINE_OFF": "15.5",
        "SAMP_OFF": "15.5",
        "LAT_OFF": "21",
        "LONG_OFF": "-157",
        "HEIGHT_OFF": "0",
        "LINE_SCALE": "16",
        "SAMP_SCALE": "16",
        "LAT_SCALE": "0.001",
        "LONG_SCALE": "0.001",
        "HEIGHT_SCALE": "100",
        "LINE_NUM_COEFF": coefficients(2, -1),
        "LINE_DEN_COEFF": coefficients(0, 1),
        "SAMP_NUM_COEFF": coefficients(1, 1),
        "SAMP_DEN_COEFF": coefficients(0, 1),
    }
    image = scene(count=1)
    image.payload = ArrayRaster(
        np.full((1, 32, 32), 10, dtype="float32"),
        Grid(32, 32, 1, "float32", (1, 0, 0, 0, 1, 0), None, rpc=rpc, georeferencing="rpc"),
    )
    image.item["properties"].update(
        {"view:sun_elevation": 60, "view:sun_azimuth": 10, "view:off_nadir": 0, "view:azimuth": 0}
    )
    image.item["assets"]["data"]["bands"] = [{"name": "green", "eo:center_wavelength": 0.55}]
    elevation = image.clone()
    elevation.item["properties"]["autostacgis:mean_elevation_m"] = 0
    corrected = atmospheric_correction(
        {"image": [image], "elevation": [elevation]},
        {"water_vapor": 2, "ozone": 0.3, "aot550": 0.1, "sixs_executable": executable},
        context,
    )["result"][0]
    assert corrected.payload.grid.rpc == rpc
    assert corrected.item["geometry"] == image.item["geometry"]
    dem = scene("dem", "digital_elevation_model", count=1)
    dem.payload = ArrayRaster(
        np.zeros((1, 32, 32), dtype="float32"),
        Grid(32, 32, 1, "float32", (0.0625, 0, -158, 0, -0.0625, 22), "EPSG:4326"),
    )
    output = orthorectify_rpc(
        {"image": [corrected], "dem": [dem]}, {"crs": "EPSG:4326", "resolution": 0.00005}, context
    )["result"][0]
    assert output.payload.grid.georeferencing == "affine"
    assert output.payload.grid.rpc is None
    block = output.payload.read(Window(0, 0, output.payload.grid.width, output.payload.grid.height))
    assert block.valid.any() and np.isfinite(block.pixels).all()


def test_pansharpen_requires_affine_grids_but_not_projected_crs():
    ms, pan = scene(), scene("pan", "panchromatic_imagery", count=1)
    app = Application(subset("pansharpen"))
    assert (
        len(
            app.plan(
                {
                    "version": "1.0.0",
                    "goal": {
                        "for_each": {
                            "kind": "raster",
                            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                            "require": {
                                "all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]
                            },
                        }
                    },
                },
                [ms, pan],
            ).steps
        )
        == 1
    )
    for record in [ms, pan]:
        record.item["assets"]["data"]["data_state:georeferencing"] = "rpc"
    with pytest.raises(NoPlan):
        app.plan(
            {
                "version": "1.0.0",
                "goal": {
                    "for_each": {
                        "kind": "raster",
                        "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                        "require": {
                            "all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]
                        },
                    }
                },
            },
            [ms, pan],
        )


def test_surface_reflectance_pan_radiance_normalization(context):
    from autostacgis_plugins.pansharpen import pansharpen

    ms, pan = scene(value=0.2), scene("pan", "panchromatic_imagery", count=1, value=100)
    ms.item["assets"]["data"]["cf:standard_name"] = "surface_bidirectional_reflectance"
    pan.item["assets"]["data"].update(
        {"cf:standard_name": "toa_outgoing_radiance_per_unit_wavelength", "unit": "W m-2 sr-1 um-1"}
    )
    output = pansharpen({"multispectral": [ms], "panchromatic": [pan]}, {}, context)["result"][0]
    np.testing.assert_allclose(output.payload.read(Window(0, 0, 4, 4)).pixels, 0.2)
    with pytest.raises(AutoSTACGISError, match="matching known"):
        pansharpen({"multispectral": [ms], "panchromatic": [pan]}, {"pan_normalization": "none"}, context)


def test_cloud_inference_is_lazy_tiled_and_uses_standard_band_order(context, monkeypatch):
    import omnicloudmask
    from autostacgis_plugins.detect_clouds import detect_clouds

    calls = []

    def infer(array, **options):
        calls.append(array.shape)
        assert array.shape[1] <= 32 and array.shape[2] <= 32
        np.testing.assert_allclose(array[:, 0, 0], [5, 3, 7])
        assert options["batch_size"] == 1
        return np.full((1, *array.shape[1:]), 3, dtype="uint8")

    monkeypatch.setattr(omnicloudmask, "predict_from_array", infer)
    image = scene(count=8)
    image.payload = ArrayRaster(
        np.stack([np.full((100, 100), i + 1, dtype="float32") for i in range(8)]),
        Grid(100, 100, 8, "float32", (1, 0, 500000, 0, -1, 2400000), "EPSG:32604"),
    )
    image.item["assets"]["data"]["bands"] = [
        {"name": str(i), "eo:common_name": name}
        for i, name in enumerate(["coastal", "blue", "green", "yellow", "red", "rededge", "nir", "nir08"])
    ]
    output = detect_clouds(
        {"image": [image]},
        {"resolution": None, "tile_size": 16, "halo": 4, "model": {"patch_size": 32, "patch_overlap": 8}},
        context,
    )["result"][0]
    assert not calls
    block = output.payload.read(Window(10, 10, 30, 30))
    assert len(calls) == 9 and np.all(block.pixels == 3)
    assert output.payload.grid.nodata == 255


def test_installed_omnicloudmask_array_api_accepts_streamed_tiles():
    import torch
    from autostacgis_plugins.detect_clouds import CloudRaster

    class ShadowModel(torch.nn.Module):
        def forward(self, values):
            result = torch.zeros((values.shape[0], 4, *values.shape[2:]), device=values.device)
            result[:, 3] = 1
            return result

    source = ArrayRaster(np.ones((3, 35, 37), dtype="float32"), Grid(37, 35, 3, "float32"))
    output = CloudRaster(
        source,
        [0, 1, 2],
        16,
        4,
        {"patch_size": 32, "patch_overlap": 8, "inference_device": "cpu", "custom_models": ShadowModel()},
        64 * 1024 * 1024,
    )
    block = output.read(Window(12, 12, 20, 20))
    assert block.pixels.shape == (1, 20, 20)
    assert np.all(block.pixels == 3) and block.valid.all()


def test_seamline_mask_keeps_only_assigned_image(context):
    from autostacgis_plugins.mask_by_polygons import mask_by_polygons

    image = scene("derived")
    image.runtime["source_id"] = "one"
    mask = Record(
        scene().item,
        "vector",
        payload=vectors(
            [box(0, 0, 2, 4), box(2, 0, 4, 4)], crs="EPSG:4326", attributes={"image": ["one", "two"]}
        ),
    )
    output = mask_by_polygons({"image": [image], "mask": [mask]}, {}, context)["result"][0]
    block = output.payload.read(Window(0, 0, 4, 4))
    assert block.valid[0].sum() == 8
    assert block.valid[0, :, :2].all() and not block.valid[0, :, 2:].any()
    assert output.item["assets"]["data"]["data_state:masked"] == {
        "cloud": False,
        "cloud_shadow": False,
        "mosaic_seamline": True,
    }


def test_common_crs_compares_equivalent_crs_and_rejects_mixed():
    from pyproj import CRS

    a, b = scene("one"), scene("two")
    b.payload = ArrayRaster(b.payload.pixels, replace(b.payload.grid, crs=CRS("EPSG:4326").to_wkt()))
    assert common_crs({"images": [a, b]}, {}, None) == "pass"
    b.payload = ArrayRaster(b.payload.pixels, replace(b.payload.grid, crs="EPSG:32604"))
    assert common_crs({"images": [a, b]}, {}, None) == "fail"


def test_reference_registration_exports_actual_model_and_ties(context):
    rng = np.random.default_rng(19)
    pixels = rng.uniform(10, 200, (1, 256, 256)).astype("float32")
    moving, reference = scene("moving", count=1), scene("reference", count=1)
    grid = Grid(256, 256, 1, "float32", (1, 0, 500000, 0, -1, 2400000), "EPSG:32604")
    reference.payload = ArrayRaster(pixels, grid)
    moving.payload = ArrayRaster(np.roll(pixels, (2, 3), (1, 2)), grid)
    output = register_to_reference(
        {"image": [moving], "reference": [reference]},
        {"solve_resolution": 1, "use_edge_proxies": False},
        context,
    )["result"][0]
    entry = output.item["assets"]["data"]["data_state:registration"][-1]
    assert entry["target"][0]["id"] == "reference"
    assert entry["tie_points"] is None  # Intensity registration does not fit discrete tie points.
    assert entry["model"]["backend"] == "coregix"
    assert entry["model"]["fits"][0]["parameter_maps"]
    assert output.item["assets"]["data"]["data_state:resampled"] == ["bilinear"]
    predicted = output.payload.read(Window(16, 16, 200, 200)).pixels
    assert np.mean(abs(predicted - pixels[:, 16:216, 16:216])) < 8


def test_joint_registration_keeps_the_actual_spectralmatch_solution(context, tmp_path):
    from autostacgis_plugins.joint_coregistration import joint_coregistration

    records = [scene("one", count=1), scene("two", count=1)]
    grid = Grid(32, 32, 1, "float32", (1, 0, 500000, 0, -1, 2400000), "EPSG:32604")
    for record in records:
        record.payload = ArrayRaster(np.arange(1024, dtype="float32").reshape(1, 32, 32) + 10, grid)
    ties = tmp_path / "ties.json"
    ties.write_text(
        json.dumps(
            {
                "tie_points": [
                    {
                        "image_1": "input-0",
                        "image_2": "input-1",
                        "points": [[[5, 5], [7, 5]], [[10, 10], [12, 10]], [[20, 20], [22, 20]]],
                    }
                ]
            }
        )
    )
    outputs = joint_coregistration(
        {"images": records},
        {"tie_load_path": str(ties), "global_model": "translation", "local_model": "none", "window_size": 16},
        context,
    )["results"]
    for index, output in enumerate(outputs):
        entry = output.item["assets"]["data"]["data_state:registration"][-1]
        assert entry["kind"] == "joint" and entry["target"][0]["id"] == records[1 - index].item["id"]
        assert len(entry["tie_points"]) == 3 and entry["model"]["global_parameters"]
        assert abs(entry["model"]["global_parameters"][0]) > 0
        assert output.payload.read(Window(0, 0, 10, 10)).valid.any()


@pytest.mark.parametrize("seamline_method", ["weighted", "markov"])
def test_collection_normalize_seamline_mask_mosaic_io(context, tmp_path, seamline_method):
    """Real SpectralMatch -> Arrow polygons -> streamed masks -> GDAL mosaic."""
    from autostacgis_plugins.clean_footprints import clean_footprints
    from autostacgis_plugins.extract_footprints import extract_footprints
    from autostacgis_plugins.global_radiometric_match import global_radiometric_match
    from autostacgis_plugins.local_radiometric_match import local_radiometric_match
    from autostacgis_plugins.markov_seamlines import markov_seamlines
    from autostacgis_plugins.mask_by_polygons import mask_by_polygons
    from autostacgis_plugins.mosaic import mosaic
    from autostacgis_plugins.weighted_seamlines import weighted_seamlines

    records = [scene("one"), scene("two")]
    values = (np.arange(4096).reshape(64, 64) % 311 + 100).astype("float32")
    for index, record in enumerate(records):
        record.payload = ArrayRaster(
            np.stack([values + i * 10 + index * 5 for i in range(3)]),
            Grid(64, 64, 3, "float32", (1, 0, 500000 + index * 20, 0, -1, 2400000), "EPSG:32604"),
        )

    # Use a different owned directory at every stage, as the actual executor does.
    def step(name, function, inputs, settings):
        context.directory = tmp_path / name
        context.directory.mkdir()
        context.inputs = inputs
        outputs = function(inputs, settings, context)
        return next(iter(outputs.values()))

    normalized = step(
        "global", global_radiometric_match, {"images": records}, {"pif_method": "entire", "window_size": 16}
    )
    local = step(
        "local", local_radiometric_match, {"images": normalized}, {"number_of_blocks": 4, "window_size": 16}
    )
    footprints = step("footprints", extract_footprints, {"images": local}, {})
    cleaned = step(
        "clean",
        clean_footprints,
        {"vector": footprints},
        {
            "hole_edge_distance": 0,
            "hole_to_hole_distance": 0,
            "simplify_smoothing_radius": 0,
            "simplify_tolerance": 0,
        },
    )
    algorithm, options = (
        (weighted_seamlines, {"rank_function": "{area}"})
        if seamline_method == "weighted"
        else (
            markov_seamlines,
            {
                "mesh_spacing": 16,
                "solver_max_iterations": 10,
                "edge_variables_weights_gsds_bands": [["laplacian_difference", 1, ["native"], [1]]],
            },
        )
    )
    polygons = step("seamlines", algorithm, {"images": local, "footprints": cleaned}, options)
    masked = []
    for index, record in enumerate(local):
        masked.extend(step(f"mask{index}", mask_by_polygons, {"image": [record], "mask": polygons}, {}))
    result = step("mosaic", mosaic, {"images": masked}, {})[0]
    assert result.payload.grid.width == 84 and result.payload.grid.height == 64
    block = result.payload.read(Window(0, 0, 84, 64))
    assert block.valid.any() and np.isfinite(block.pixels[block.valid]).all()
    assert result.item["assets"]["data"]["data_state:mosaic"] is True


def test_atmosphere_inventory_is_per_scene_day_and_named_check_rejects_wrong_day():
    from autostacgis_plugins.atmosphere import AtmosphereSource
    from autostacgis_plugins.atmosphere_for_image import atmosphere_for_image

    images = [scene("one"), scene("two")]
    images[1].item["properties"]["datetime"] = "2024-01-02T12:00:00Z"
    records = list(AtmosphereSource().discover_from({}, {}, None, images))
    assert len(records) == 2
    assert atmosphere_for_image({"image": [images[0]], "atmosphere": [records[0]]}, {}, None) == "pass"
    assert atmosphere_for_image({"image": [images[1]], "atmosphere": [records[0]]}, {}, None) == "fail"
