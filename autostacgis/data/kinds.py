"""Asset format inspection for discovery adapters; explicit kinds take precedence."""

from pathlib import PurePosixPath
from urllib.parse import urlsplit


def asset_kind(asset, properties=None, default="raster"):
    media = asset.get("type", "").split(";")[0].lower()
    suffix = PurePosixPath(urlsplit(asset.get("href", "")).path).suffix.lower()
    if media in ("application/vnd.las", "application/vnd.laszip") or suffix in (".las", ".laz", ".copc"):
        return "point_cloud"
    if media in (
        "application/geo+json",
        "application/vnd.flatgeobuf",
        "application/geopackage+sqlite3",
    ) or suffix in (".geojson", ".gpkg", ".shp", ".fgb"):
        return "vector"
    if "pc:type" in (properties or {}):
        return "point_cloud"
    if suffix in (".parquet", ".arrow", ".feather"):
        return "vector" if asset.get("roles") and "vector" in asset["roles"] else default
    if media.startswith("image/") or suffix in (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".jp2", ".zarr"):
        return "raster"
    return default


def describe_kinds(record, explicit=None):
    record.runtime["asset_kinds"] = {
        key: explicit or asset_kind(asset, record.item.get("properties"), record.kind)
        for key, asset in record.item.get("assets", {}).items()
    }
    kinds = set(record.runtime["asset_kinds"].values())
    if len(kinds) == 1:
        record.kind = next(iter(kinds))
    return record
