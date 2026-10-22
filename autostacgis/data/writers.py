"""Core-managed persistence consumes the same interfaces used between steps."""

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..errors import AutoSTACGISError
from .batches import crs_document


def write_raster(raster, path, *, tile_size=512, driver="GTiff", stac=None, asset_key="data", embed_stac=True, **options):
    import rasterio
    from affine import Affine
    from rasterio.windows import Window

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    grid = raster.grid
    profile = dict(
        driver=driver,
        width=grid.width,
        height=grid.height,
        count=grid.count,
        dtype=grid.dtype,
        transform=Affine(*grid.transform),
        crs=grid.crs,
        nodata=grid.nodata,
    )
    if driver == "GTiff":
        profile.update(tiled=True, blockxsize=256, blockysize=256, compress="deflate", BIGTIFF="IF_SAFER")
    profile.update(options)
    with rasterio.open(path, "w", **profile) as output:
        if stac is not None:
            if embed_stac:
                output.update_tags(STAC=json.dumps(stac, separators=(",", ":"), allow_nan=False))
            props = stac.get("properties", {})
            asset = stac.get("assets", {}).get(asset_key, {})
            output.update_tags(
                **{
                    key: json.dumps(value, allow_nan=False) if isinstance(value, (dict, list)) else str(value)
                    for key, value in {**props, **asset}.items()
                    if value is not None and key not in ("bands", "href")
                }
            )
            acquired = props.get("datetime")
            if acquired:
                from datetime import datetime

                output.update_tags(
                    TIFFTAG_DATETIME=datetime.fromisoformat(acquired.replace("Z", "+00:00")).strftime(
                        "%Y:%m:%d %H:%M:%S"
                    )
                )
            bands = asset.get("bands", [])
            scales, offsets = [], []
            for index in range(grid.count):
                band = bands[index] if index < len(bands) else {}
                if band.get("name"):
                    output.set_band_description(index + 1, band["name"])
                unit = band.get("unit", asset.get("unit"))
                if unit:
                    output.set_band_unit(index + 1, unit)
                scales.append(band.get("raster:scale", asset.get("raster:scale", 1)))
                offsets.append(band.get("raster:offset", asset.get("raster:offset", 0)))
                output.update_tags(
                    index + 1,
                    **{
                        k: json.dumps(v, allow_nan=False) if isinstance(v, (dict, list)) else str(v)
                        for k, v in band.items()
                        if v is not None
                    },
                )
            output.scales, output.offsets = scales, offsets
        if grid.rpc:
            output.update_tags(ns="RPC", **grid.rpc)
        for block in raster.blocks(tile_size=tile_size):
            w = block.window
            window = Window(w.x, w.y, w.width, w.height)
            pixels = block.pixels
            if block.valid is not None:
                mask = np.broadcast_to(block.valid, pixels.shape)
                if grid.nodata is not None:
                    pixels = np.where(mask, pixels, grid.nodata)
                # GDAL's shared dataset mask represents pixels valid in every band.
                output.write_mask(np.all(mask, axis=0).astype("uint8") * 255, window=window)
            output.write(pixels, window=window)
    return str(path)


def write_zarr(raster, path, *, tile_size=512, stac=None, executor=None):
    import zarr

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    group = zarr.open_group(str(path), mode="w")
    group.attrs["autostacgis:grid"] = asdict(raster.grid)
    chunks = (raster.grid.count, tile_size, tile_size)
    group.create_array("pixels", shape=raster.grid.shape, chunks=chunks, dtype=raster.grid.dtype)
    group.create_array("valid", shape=raster.grid.shape, chunks=chunks, dtype="bool", fill_value=True)
    jobs = ((str(path), raster, window) for window in raster.windows(tile_size=tile_size))
    if executor:
        for _ in executor.map(_write_chunk, jobs):
            pass
    else:
        for job in jobs:
            _write_chunk(job)
    if stac is not None:
        group.attrs["stac"] = stac
    return str(path)


def _write_chunk(job):
    import zarr

    path, raster, window = job
    block = raster.read(window)
    # Each task owns complete distinct chunks. No shared writable GDAL handle.
    group = zarr.open_group(path, mode="r+")
    w = block.window
    region = (..., slice(w.y, w.y + w.height), slice(w.x, w.x + w.width))
    group["pixels"][region] = block.pixels
    if block.valid is not None:
        group["valid"][region] = np.broadcast_to(block.valid, block.pixels.shape)


def write_parquet(data, path):
    import pyarrow.parquet as pq

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(data.schema.metadata or {})
    if data.kind == "vector":
        metadata[b"geo"] = json.dumps(
            {
                "version": "1.1.0",
                "primary_column": "geometry",
                "columns": {
                    "geometry": {"encoding": "WKB", "geometry_types": [], "crs": crs_document(data.crs)}
                },
            }
        ).encode()
    schema = data.schema.with_metadata(metadata)
    with pq.ParquetWriter(path, schema) as writer:
        for batch in data.batches():
            writer.write_batch(batch.replace_schema_metadata(metadata))
    return str(path)


def vrt_paths(rasters, storage, directory):
    """Stage seekable rasters with unique VRT basenames without duplicating file pixels."""
    from osgeo import gdal

    paths = []
    for index, raster in enumerate(rasters):
        source = storage.path(raster, reason="Algorithm requires seekable datasets")
        target = Path(directory) / f"input-{index}.vrt"
        dataset = gdal.Translate(str(target), source, format="VRT")
        if dataset is None:
            raise AutoSTACGISError("Could not create a raster input reference")
        dataset = None
        paths.append(str(target))
    return paths
