def compatible_grids(inputs, settings, context):
    grids = []
    for records in inputs.values():
        for record in records:
            asset = record.item.get("assets", {}).get(
                settings.get("asset", record.runtime.get("selected_asset", "data")), {}
            )
            metadata = {**record.item.get("properties", {}), **asset}
            grids.append(
                {
                    "crs": metadata.get("proj:code", metadata.get("proj:wkt2")),
                    "shape": metadata.get("proj:shape"),
                    "transform": metadata.get("proj:transform"),
                }
            )
    if not grids or any(g.get("crs") is None or g.get("transform") is None for g in grids):
        return "unknown"
    if any(g["crs"] != grids[0]["crs"] for g in grids):
        return "fail"
    if settings.get("same_grid", False):
        return "pass" if all(g == grids[0] for g in grids) else "fail"
    # GDAL pansharpen resamples spectral pixels; reject rotated or disjoint grids.
    from shapely.geometry import shape

    records = [r for rs in inputs.values() for r in rs]
    if any(not r.item.get("geometry") for r in records):
        return "unknown"
    if any(g["transform"][1] != 0 or g["transform"][3] != 0 for g in grids):
        return "fail"
    return (
        "pass"
        if all(shape(records[0].item["geometry"]).intersects(shape(r.item["geometry"])) for r in records[1:])
        else "fail"
    )


ID = "compatible-grids"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": compatible_grids}
