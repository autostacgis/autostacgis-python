"""Native format adapters produce explicit artifacts for any compatible destination."""

from uuid import uuid4

from .artifacts import Artifact
from .data.writers import write_parquet, write_raster, write_zarr


def encode(record, settings, context, format):
    suffix = {"geotiff": ".tif", "cog": ".tif", "zarr": ".zarr", "parquet": ".parquet"}[format]
    path = context.scratch / (uuid4().hex + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if format == "zarr":
        write_zarr(record.payload, path, tile_size=settings.get("tile_size", 512), stac=None)
        media = "application/vnd+zarr"
    elif format == "parquet":
        write_parquet(record.payload, path)
        media = "application/vnd.apache.parquet"
    else:
        write_raster(
            record.payload,
            path,
            driver="COG" if format == "cog" else "GTiff",
            stac=record.item,
            asset_key=record.runtime.get("selected_asset", "data"),
            embed_stac=False,
            tile_size=settings.get("tile_size", 512),
        )
        media = "image/tiff; application=geotiff" + ("; profile=cloud-optimized" if format == "cog" else "")
    return Artifact("path", path, media, {"plugin": format + "-metadata"} if format != "parquet" else None)


def embed(record, settings, context, format):
    from .io import is_local, local

    key = record.runtime.get("selected_asset", "data")
    href = record.runtime.get("local_assets", {}).get(key, record.item["assets"][key]["href"])
    if not is_local(href):
        return record.item
    path = local(str(href))
    if format == "zarr":
        import zarr

        zarr.open_group(str(path), mode="r+").attrs["stac"] = record.item
    else:
        import json

        import rasterio

        if format == "cog":
            from rasterio.shutil import copy

            temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tif")
            rebuilt = path.with_name("." + path.name + "." + uuid4().hex + ".tif")
            try:
                copy(str(path), str(temporary), driver="GTiff")
                with rasterio.open(temporary, "r+") as dataset:
                    dataset.update_tags(STAC=json.dumps(record.item, allow_nan=False))
                copy(str(temporary), str(rebuilt), driver="COG")
                rebuilt.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
                rebuilt.unlink(missing_ok=True)
        else:
            with rasterio.open(path, "r+") as dataset:
                dataset.update_tags(STAC=json.dumps(record.item, allow_nan=False))
    return record.item


def restore(saved, record, settings, format):
    from .data import FileBatches, FileRaster, ZarrRaster
    from .io import is_local, local

    if not is_local(saved.value):
        return record.payload
    path = local(str(saved.value))
    if format == "zarr":
        return ZarrRaster(path)
    if format == "parquet":
        return FileBatches(
            path, kind=record.kind, crs=settings.get("crs", getattr(record.payload, "crs", None))
        )
    return FileRaster(path)
