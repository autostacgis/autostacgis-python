from dataclasses import replace

import numpy as np
import pyarrow as pa
import pytest
from autostacgis_plugins.align_grid import align_grid
from autostacgis_plugins.atmospheric_correction import atmospheric_correction
from autostacgis_plugins.band_math import band_math
from autostacgis_plugins.clean_footprints import clean_footprints
from autostacgis_plugins.extract_footprints import extract_footprints
from autostacgis_plugins.mask_by_polygons import mask_by_polygons
from autostacgis_plugins.polygonize import polygonize
from autostacgis_plugins.rasterize_point_attribute import rasterize_point_attribute
from autostacgis_plugins.statistics import statistics
from helpers import scene
from shapely.geometry import box

from autostacgis import Record
from autostacgis.data import ArrayRaster, Batches, Grid, Window, vectors
from autostacgis.data.writers import write_parquet


def test_band_math_and_statistics(context):
    image = scene()
    output = band_math({"image": [image]}, {"expressions": ["b1+b2", "b3/b1"]}, context)["result"][0]
    np.testing.assert_allclose(output.payload.read(Window(0, 0, 4, 4)).pixels[:, 0, 0], [20, 1])
    stats = [
        band["statistics"]
        for band in statistics({"image": [output]}, {}, context)["result"][0].item["assets"]["data"]["bands"]
    ]
    assert [s["mean"] for s in stats] == [20, 1]
    assert all(s["valid_percent"] == 100 and s["stddev"] == 0 for s in stats)


def test_grid_alignment_native(context):
    output = align_grid({"image": [scene()]}, {"resolution": 2}, context)["result"][0]
    assert output.payload.grid.width == 2
    np.testing.assert_allclose(output.payload.read(Window(0, 0, 2, 2)).pixels, 10)


def test_mask_polygonize_and_clean_preserves_attributes(context):
    mask = Record(
        scene().item,
        kind="vector",
        payload=vectors([box(0, 0, 2, 4)], crs="EPSG:32604", attributes={"image": ["ms"]}),
    )
    cleaned = clean_footprints(
        {"vector": [mask]},
        {
            "hole_edge_distance": 0,
            "hole_to_hole_distance": 0,
            "simplify_smoothing_radius": 0,
            "simplify_tolerance": 0,
        },
        context,
    )["result"][0]
    assert next(cleaned.payload.batches()).column("image").to_pylist() == ["ms"]
    from dataclasses import replace

    source = scene()
    source.payload.grid = replace(source.payload.grid, crs="EPSG:32604")
    image = mask_by_polygons({"image": [source], "mask": [cleaned]}, {}, context)["result"][0]
    block = image.payload.read(Window(0, 0, 4, 4))
    assert block.valid.any() and not block.valid.all()
    result = polygonize({"image": [image]}, {}, context)["result"][0]
    assert result.kind == "vector"
    from shapely import from_wkb

    geometry = from_wkb(next(result.payload.batches()).column("geometry")[0].as_py())
    assert geometry.area == 8


def test_arrow_geoparquet_crs_roundtrip(context, tmp_path):
    from autostacgis.data import FileBatches

    data = vectors([box(0, 0, 1, 1)], crs="EPSG:4326", attributes={"id": [1]})
    path = tmp_path / "vectors.parquet"
    write_parquet(data, path)
    result = FileBatches(path, kind="vector")
    assert result.crs["type"] == "GeographicCRS"
    assert pa.RecordBatchReader.from_stream(result).read_all().num_rows == 1


def test_point_cloud_native_reduction(context):
    points = Batches(
        [
            pa.RecordBatch.from_pydict(
                {
                    "X": [0.5, 0.5, 1.5],
                    "Y": [3.5, 3.5, 2.5],
                    "Z": [0.0, 1.0, 2.0],
                    "Intensity": [2.0, 4.0, 9.0],
                }
            )
        ],
        kind="point_cloud",
        crs="EPSG:4326",
    )
    record = Record(scene().item, kind="point_cloud", payload=points)
    grid = {
        "width": 4,
        "height": 4,
        "count": 1,
        "dtype": "float32",
        "transform": (1, 0, 0, 0, -1, 4),
        "crs": "EPSG:4326",
    }
    output = rasterize_point_attribute({"points": [record]}, {"grid": grid}, context)["result"][0]
    block = output.payload.read(Window(0, 0, 4, 4))
    assert block.pixels[0, 0, 0] == 3 and block.pixels[0, 1, 1] == 9
    assert block.valid.sum() == 2


def test_py6s_real_native_executable(context):
    import os
    import shutil

    executable = os.environ.get("AUTOSTACGIS_TEST_SIXS") or shutil.which("sixs")
    if not executable:
        pytest.skip("6S binary not installed")
    image = scene(count=1, value=10)
    settings = {
        "solar_zenith": 30,
        "solar_azimuth": 0,
        "view_zenith": 0,
        "view_azimuth": 0,
        "month": 6,
        "day": 1,
        "water_vapor": 2,
        "ozone": 0.3,
        "aot550": 0.1,
        "wavelengths_um": [0.55],
        "sixs_executable": executable,
    }
    elevation = image.clone()
    elevation.item["properties"]["autostacgis:mean_elevation_m"] = 0
    result = atmospheric_correction({"image": [image], "elevation": [elevation]}, settings, context)[
        "result"
    ][0]
    block = result.payload.read(Window(0, 0, 4, 4))
    assert block.valid.all() and np.isfinite(block.pixels).all()
    assert result.item["assets"]["data"]["data_state:atmospheric_correction"] == "full"


def test_real_global_normalization(context):
    pytest.importorskip("osgeo")
    pytest.importorskip("spectralmatch")
    from autostacgis_plugins.global_radiometric_match import global_radiometric_match

    grid = Grid(32, 32, 3, "float32", (1, 0, 500000, 0, -1, 2400000), "EPSG:32604")
    values = np.stack([np.arange(1024).reshape(32, 32) + 100 + i for i in range(3)]).astype("float32")
    one, two = scene("one"), scene("two")
    one.payload = ArrayRaster(values, grid)
    two.payload = ArrayRaster(values * 2 + 10, grid)
    result = global_radiometric_match({"images": [one, two]}, {"pif_method": "entire"}, context)["results"]
    assert len(result) == 2
    for output in result:
        assert output.payload.read(Window(0, 0, 32, 32)).pixels.shape == (3, 32, 32)
        assert output.item["assets"]["data"]["data_state:relative_radiometric_normalization"] is True


def test_real_seamline_adapters(context):
    pytest.importorskip("osgeo")
    pytest.importorskip("spectralmatch")
    from autostacgis_plugins.voronoi_seamlines import voronoi_seamlines
    from autostacgis_plugins.weighted_seamlines import weighted_seamlines

    images = [scene("one"), scene("two")]
    images[1].payload = ArrayRaster(
        images[1].payload.pixels, replace(images[1].payload.grid, transform=(1, 0, 2, 0, -1, 4))
    )
    footprints = extract_footprints({"images": images}, {}, context)["result"][0]
    for function, settings in [
        (weighted_seamlines, {"rank_function": "{area}"}),
        (voronoi_seamlines, {"min_point_spacing": 1}),
    ]:
        result = function({"footprints": [footprints], "images": images}, settings, context)["result"][0]
        assert result.kind == "vector"
        assert sum(batch.num_rows for batch in result.payload.batches()) > 0


@pytest.mark.parametrize(
    "plugin,backend,state",
    [
        ("global_radiometric_match", "global_regression", "relative_radiometric_normalization"),
        ("local_radiometric_match", "local_block_adjustment", "local_radiometric_adjustment"),
    ],
)
@pytest.mark.parametrize("produce_outputs", [True, False])
def test_collection_backend_contract(context, monkeypatch, plugin, backend, state, produce_outputs):
    from importlib import import_module
    from pathlib import Path

    from autostacgis.errors import AutoSTACGISError

    gdal = pytest.importorskip("osgeo.gdal")
    spectralmatch = pytest.importorskip("spectralmatch")
    images = [scene("one", value=10), scene("two", value=20)]
    for image in images:
        image.item["assets"]["data"].update({"raster:scale": 2, "raster:offset": 3})
        pixels = image.payload.pixels.copy()
        pixels[:, 0, 0] = np.nan
        valid = np.ones(pixels.shape, dtype=bool)
        valid[:, 0, 1] = False
        image.payload = ArrayRaster(pixels, image.payload.grid, valid)

    def run_backend(
        *,
        input_images,
        output_images,
        executor,
        tile_threads,
        io_threads,
        image_threads,
        concurrent_processing,
        concurrent_processing_backend,
        threshold,
    ):
        assert executor is context.executor
        assert (tile_threads, io_threads, image_threads, concurrent_processing) == (1, 1, None, 1)
        assert concurrent_processing_backend == "process_pool"
        assert threshold == 7
        assert [Path(path).stem for path in input_images] == ["input-0", "input-1"]
        for index, (source, target) in enumerate(zip(input_images, output_images, strict=True)):
            dataset = gdal.Open(source)
            band = dataset.GetRasterBand(1)
            values = band.ReadAsArray()
            sentinel = float(np.finfo("float32").min)
            assert band.GetNoDataValue() == sentinel
            np.testing.assert_array_equal(values[0, :2], sentinel)
            np.testing.assert_allclose(values[1:, :], 23 + 20 * index)
            if produce_outputs:
                result = gdal.Translate(target, dataset, format="GTiff")
                assert result is not None
                result = None

    monkeypatch.setattr(spectralmatch, backend, run_backend)
    process = getattr(import_module(f"autostacgis_plugins.{plugin}"), plugin)
    settings = {"threshold": 7, "tile_threads": 99, "io_threads": 99, "concurrent_processing": 99}
    if not produce_outputs:
        with pytest.raises(AutoSTACGISError, match=f"{backend} did not create"):
            process({"images": images}, settings, context)
        return
    outputs = process({"images": images}, settings, context)["results"]
    assert len(outputs) == 2
    for index, output in enumerate(outputs):
        asset = output.item["assets"]["data"]
        assert asset[f"data_state:{state}"] is True
        assert asset["autostacgis:normalization_group"] == ["one", "two"]
        assert (asset["raster:scale"], asset["raster:offset"]) == (1, 0)
        block = output.payload.read(Window(0, 0, 4, 4))
        assert not block.valid[:, 0, :2].any()
        np.testing.assert_allclose(block.pixels[:, 1:, :], 23 + 20 * index)


@pytest.mark.parametrize(
    "plugin,backend",
    [
        ("voronoi_seamlines", "voronoi_center_seamline"),
        ("weighted_seamlines", "weighted_seamline"),
        ("markov_seamlines", "markov_triangles"),
    ],
)
def test_seamline_backend_contract(context, monkeypatch, plugin, backend):
    from importlib import import_module
    from pathlib import Path

    pytest.importorskip("osgeo")
    gpd = pytest.importorskip("geopandas")
    spectralmatch = pytest.importorskip("spectralmatch")
    images = [scene("one"), scene("two")]
    footprints = extract_footprints({"images": images}, {}, context)["result"][0]

    def run_backend(
        *,
        input_polygons,
        output_mask,
        input_layer,
        input_images,
        executor,
        image_threads,
        io_threads,
        tile_threads,
        custom_option,
    ):
        assert executor is context.executor
        assert (image_threads, io_threads, tile_threads) == (None, 1, 1)
        assert input_layer is None and custom_option == 7
        frame = gpd.read_file(input_polygons)
        assert frame["image"].tolist() == [Path(path).stem for path in input_images]
        assert frame["image"].tolist() == ["input-0", "input-1"]
        frame.to_file(output_mask, driver="GPKG")

    monkeypatch.setattr(spectralmatch, backend, run_backend)
    process = getattr(import_module(f"autostacgis_plugins.{plugin}"), plugin)
    output = process(
        {"footprints": [footprints], "images": images},
        {"custom_option": 7, "image_threads": 99, "io_threads": 99, "tile_threads": 99},
        context,
    )["result"][0]
    assert output.kind == "vector"
    batch = next(output.payload.batches())
    assert batch.num_rows == 2
    assert batch.column("image").to_pylist() == ["one", "two"]
