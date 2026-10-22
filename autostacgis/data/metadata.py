"""Shared output metadata construction from runtime payloads."""

import json
import math
from copy import deepcopy


def finish(context, payload, source, changes=None, *, port="result", index=0, physical=False):
    # Context applies the plugin's inheritance policy to live input metadata.
    # Restoring source facts here would undo an empty start or explicit removal.
    facts = {"assets.data.data_state:statistics_calculated": False}
    facts.update(changes or {})
    roles = (changes or {}).get("assets.data.roles", [])
    if any(role != "data" for role in roles):
        for key in (
            "cf:standard_name",
            "unit",
            "data_state:spectral_sampling",
            "data_state:modality",
            "data_state:radiometry",
            "data_state:atmospheric_correction",
        ):
            facts["assets.data." + key] = None
        facts["assets.data.bands"] = []
        if payload.kind == "raster":
            facts.update({"assets.data.raster:scale": 1, "assets.data.raster:offset": 0})
    result = context.output(port, payload, index=index, facts=facts)
    asset = result.item["assets"][result.runtime.get("selected_asset", "data")]
    # Band overrides must agree with the state of newly computed pixels.
    for path, value in (changes or {}).items():
        key = path.removeprefix("assets.data.")
        if key.startswith(("cf:", "data_state:")) or key == "unit":
            for band in asset.get("bands", []):
                if key in band:
                    band[key] = value
    if physical:
        asset = result.item["assets"][result.runtime.get("selected_asset", "data")]
        asset.update({"raster:scale": 1, "raster:offset": 0})
        for band in asset.get("bands", []):
            band.update({"raster:scale": 1, "raster:offset": 0})
    return result


def refresh_metadata(record):
    payload = record.payload
    key = record.runtime.get("selected_asset", "data")
    old = record.item.get("assets", {}).get(key, {})
    record.item["assets"] = {
        key: {**deepcopy(old), "href": f"autostacgis://runtime/{record.key}", "roles": old.get("roles", ["data"])}
    }
    asset = record.item["assets"][key]
    asset.pop("type", None)
    asset.pop("statistics", None)
    asset.pop("raster:histogram", None)
    if record.kind == "raster":
        from affine import Affine
        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds
        from shapely.geometry import box, mapping

        grid = payload.grid
        asset["data_state:georeferencing"] = grid.georeferencing or (
            "affine" if grid.crs else "rpc" if grid.rpc else "none"
        )
        for container in (record.item["properties"], asset):
            for name in list(container):
                if name.startswith("proj:"):
                    container.pop(name)
        asset.update({"proj:shape": [grid.height, grid.width], "proj:transform": list(grid.transform)})
        if grid.crs:
            from pyproj import CRS

            crs = CRS.from_user_input(grid.crs)
            authority = crs.to_authority()
            if authority:
                asset["proj:code"] = ":".join(authority)
            else:
                asset["proj:wkt2"] = crs.to_wkt()
        old_bands = asset.get("bands", [])
        bands = []
        for index in range(grid.count):
            band = deepcopy(old_bands[index]) if index < len(old_bands) else {"name": f"band_{index + 1}"}
            band.pop("statistics", None)
            band.pop("raster:histogram", None)
            band["data_type"] = grid.dtype
            if grid.nodata is not None:
                band["nodata"] = grid.nodata if math.isfinite(grid.nodata) else str(grid.nodata)
            else:
                band.pop("nodata", None)
            bands.append(band)
        asset["bands"] = bands
        if grid.crs and asset["data_state:georeferencing"] == "affine":
            bounds = transform_bounds(
                grid.crs,
                "EPSG:4326",
                *array_bounds(grid.height, grid.width, Affine(*grid.transform)),
                densify_pts=21,
            )
            record.item["bbox"] = list(bounds)
            record.item["geometry"] = json.loads(json.dumps(mapping(box(*bounds))))
    elif record.kind == "vector":
        for container in (record.item["properties"], asset):
            for name in list(container):
                if name.startswith(("proj:", "raster:")) or name in (
                    "bands",
                    "statistics",
                    "data_type",
                    "nodata",
                ):
                    container.pop(name)
        from pyproj import Transformer
        from shapely import from_wkb, union_all
        from shapely.geometry import mapping
        from shapely.ops import transform

        geometries = [
            geometry
            for batch in payload.batches()
            for geometry in from_wkb(batch.column("geometry").to_pylist())
            if geometry is not None
        ]
        if geometries:
            geometry = union_all(geometries)
            if payload.crs:
                geometry = transform(
                    Transformer.from_crs(payload.crs, "EPSG:4326", always_xy=True).transform, geometry
                )
            record.item["geometry"] = json.loads(json.dumps(mapping(geometry)))
            record.item["bbox"] = list(geometry.bounds)
    return record


def resampling_history(result, source, method):
    """Report an actual pixel resampling, including direct Python plugin calls."""
    from ..metadata import MISSING
    from ..semantics import value_at

    selected = source.runtime.get("selected_asset", "data")
    previous = value_at(source.item, f"assets.{selected}.data_state:resampled")
    previous = [] if previous is MISSING or previous is None else previous
    asset = result.item["assets"][result.runtime.get("selected_asset", "data")]
    asset["data_state:resampled"] = [*previous, method]
    source_asset = source.item["assets"][selected]
    for index, band in enumerate(asset.get("bands", [])):
        source_bands = source_asset.get("bands", [])
        if index < len(source_bands) and "data_state:resampled" in source_bands[index]:
            band["data_state:resampled"] = [*(source_bands[index]["data_state:resampled"] or []), method]
    return result
