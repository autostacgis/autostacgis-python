import json
from pathlib import Path

from .io import asset_path


def inspect_raster(record, settings, app):
    if record.kind != "raster":
        return record
    import rasterio
    from rasterio.warp import transform_bounds
    from shapely.geometry import box, mapping

    from .io import merge_verified

    overwrite = settings.get("overwrite", False)
    bounds_all = []
    for key, asset in record.item.get("assets", {}).items():
        if record.runtime.get("asset_kinds", {}).get(key, "raster") != "raster":
            continue
        if "metadata" in asset.get("roles", []) or "thumbnail" in asset.get("roles", []):
            continue
        path = asset_path(record, key)
        if str(path).startswith("autostacgis:"):
            continue
        if str(path).startswith(("https://", "http://", "s3://")) and not settings.get("allow_remote", False):
            continue
        if Path(str(path)).suffix.lower() in (".json", ".xml", ".txt", ".html"):
            continue
        if Path(str(path)).is_dir():
            continue
        with rasterio.open(path) as src:
            fields = {"proj:shape": [src.height, src.width], "proj:transform": list(src.transform)[:6]}
            fields["data_state:georeferencing"] = (
                "affine"
                if src.crs and not src.transform.is_identity
                else "rpc"
                if src.tags(ns="RPC")
                else "none"
            )
            if src.crs:
                authority = src.crs.to_authority()
                if authority:
                    fields["proj:code"] = ":".join(authority)
                else:
                    fields["proj:wkt2"] = src.crs.to_wkt(version="WKT2_2019")
            if overwrite:
                asset.update(fields)
            else:
                merge_verified(asset, fields)
            existing = asset.get("bands", [])
            if existing and len(existing) != src.count:
                raise ValueError("STAC bands do not match the physical raster band count")
            bands = []
            for i in range(src.count):
                import math
                from copy import deepcopy

                band = deepcopy(existing[i]) if existing else {"name": src.descriptions[i] or f"band_{i + 1}"}
                evidence = {"data_type": src.dtypes[i]}
                tags = {**src.tags(), **src.tags(i + 1)}
                if src.nodatavals[i] is not None:
                    value = src.nodatavals[i]
                    evidence["nodata"] = str(value) if not math.isfinite(value) else value
                if src.units[i]:
                    evidence["unit"] = src.units[i]
                if tags.get("standard_name"):
                    evidence["cf:standard_name"] = tags["standard_name"]
                if (
                    any(k in tags for k in ("scale_factor", "add_offset", "SCALE", "OFFSET"))
                    or src.scales[i] != 1
                    or src.offsets[i] != 0
                ):
                    evidence["raster:scale"] = float(
                        tags.get("scale_factor", tags.get("SCALE", src.scales[i]))
                    )
                    evidence["raster:offset"] = float(
                        tags.get("add_offset", tags.get("OFFSET", src.offsets[i]))
                    )
                if overwrite:
                    band.update(evidence)
                else:
                    merge_verified(band, evidence)
                bands.append(band)
            asset["bands"] = bands
            if src.crs:
                bounds = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
                bounds_all.append(bounds)
    if bounds_all and (overwrite or record.item.get("geometry") is None):
        bounds = [
            min(b[0] for b in bounds_all),
            min(b[1] for b in bounds_all),
            max(b[2] for b in bounds_all),
            max(b[3] for b in bounds_all),
        ]
        record.item["bbox"] = bounds
        record.item["geometry"] = json.loads(json.dumps(mapping(box(*bounds))))
    app.stac.extensions(record.item)
    # Raster formats do not reliably encode acquisition time: never invent one.
    return record
