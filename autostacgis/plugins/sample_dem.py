"""Windowed mean elevation inside an image's WGS84 footprint."""

import numpy as np

from ..data import Batches, physical_raster
from ..data.metadata import finish
from ..errors import AutoSTACGISError


def sample_dem(inputs, settings, context):
    import pyarrow as pa
    from affine import Affine
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from shapely.geometry import Polygon, mapping, shape
    from shapely.ops import transform

    image, dem = inputs["image"][0], inputs["dem"][0]
    if not image.item.get("geometry"):
        raise AutoSTACGISError("DEM sampling requires an image footprint")
    raster = physical_raster(dem)
    if not raster.grid.crs or raster.grid.georeferencing in ("rpc", "none"):
        raise AutoSTACGISError("DEM sampling requires an affine georeferenced DEM")
    if raster.grid.count != 1:
        raise AutoSTACGISError("DEM sampling requires one elevation band")
    geometry = transform(
        Transformer.from_crs("EPSG:4326", raster.grid.crs, always_xy=True).transform,
        shape(image.item["geometry"]),
    )
    total, count = 0.0, 0
    affine = Affine(*raster.grid.transform)
    for window in raster.windows(tile_size=context.storage.tile_size):
        corners = [
            (window.x, window.y),
            (window.x + window.width, window.y),
            (window.x + window.width, window.y + window.height),
            (window.x, window.y + window.height),
        ]
        if not geometry.intersects(Polygon([affine * point for point in corners])):
            continue
        block = raster.read(window)
        inside = geometry_mask(
            [mapping(geometry)],
            out_shape=block.pixels.shape[1:],
            transform=Affine(*raster.grid.transform) * Affine.translation(block.window.x, block.window.y),
            invert=True,
        )
        valid = inside & np.isfinite(block.pixels[0])
        if block.valid is not None:
            valid &= np.broadcast_to(block.valid, block.pixels.shape)[0]
        values = block.pixels[0][valid]
        total += values.sum(dtype="float64")
        count += values.size
    if not count:
        raise AutoSTACGISError("DEM has no valid elevation samples within the image footprint")
    mean = float(total / count)
    payload = Batches(
        pa.Table.from_pylist([{"mean_elevation_m": mean, "sample_count": count}]).to_batches(), kind="table"
    )
    output = finish(
        context,
        payload,
        image,
        {
            "assets.data.roles": ["data", "elevation-summary"],
            "properties.autostacgis:mean_elevation_m": mean,
            "properties.autostacgis:elevation_sample_count": int(count),
        },
    )
    output.item["assets"]["data"]["unit"] = "m"
    return {"result": [output]}


ID = "sample_dem"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": sample_dem}
sample_dem.contract = {
    "argument_requirements": {
        "image": {"kind": "raster", "require": {"path": "geometry", "exists": True}},
        "dem": {
            "kind": "raster",
            "require": {
                "assets.$asset.cf:standard_name": "surface_altitude",
                "assets.$asset.unit": "m",
                "assets.$asset.data_state:georeferencing": "affine",
            },
        },
    },
    "anchor": "image",
    "outputs": {
        "result": {
            "kind": "table",
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.roles": ["data", "elevation-summary"]},
                {"assets.data.unit": "m"},
                {"properties.autostacgis:mean_elevation_m": None},
            ],
            "remove": [
                "assets.data.bands",
                "assets.data.cf:standard_name",
                "assets.data.data_state:spectral_sampling",
            ],
        }
    },
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
sample_dem.contract['argument_requirements']['image']["data_types"] = ['raster']
sample_dem.contract['argument_requirements']['dem']["data_types"] = ['raster']
sample_dem.contract['outputs']['result']["data_types"] = ['batches']
