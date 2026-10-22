"""Rasterize only the seamline polygons assigned to this image, window by window."""

from dataclasses import replace

import numpy as np

from ..data import Block, TransformRaster
from ..data.metadata import finish
from ..data.raster import Raster
from ..errors import AutoSTACGISError
from .apply_mask import _mask


class PolygonMask(Raster):
    def __init__(self, geometries, grid):
        self.geometries = geometries
        self.grid = replace(grid, count=1, dtype="uint8", nodata=None)

    def read(self, window):
        from affine import Affine
        from rasterio.features import rasterize
        from shapely.geometry import box

        self.validate_window(window)
        transform = Affine(*self.grid.transform) * Affine.translation(window.x, window.y)
        corners = [
            transform * point
            for point in [(0, 0), (window.width, 0), (0, window.height), (window.width, window.height)]
        ]
        bounds = box(
            min(x for x, y in corners),
            min(y for x, y in corners),
            max(x for x, y in corners),
            max(y for x, y in corners),
        )
        shapes = [(g, 1) for g in self.geometries if g.intersects(bounds) and not g.is_empty]
        values = (
            rasterize(shapes, out_shape=(window.height, window.width), transform=transform, dtype="uint8")
            if shapes
            else np.zeros((window.height, window.width), dtype="uint8")
        )
        return Block(values[None], window)


def mask_by_polygons(inputs, settings, context):
    from pyproj import CRS, Transformer
    from shapely import from_wkb
    from shapely.ops import transform

    image, mask = inputs["image"][0], inputs["mask"][0]
    identity = image.runtime.get(
        "source_id",
        image.runtime.get(
            "original_id", image.item.get("properties", {}).get("autostacgis:source_id", image.item["id"])
        ),
    )
    geometries = []
    for batch in mask.payload.batches():
        if "image" not in batch.schema.names:
            raise AutoSTACGISError("Seamline features require an image identity column")
        geometries.extend(
            g
            for g, name in zip(
                from_wkb(batch.column("geometry").to_pylist()), batch.column("image").to_pylist(), strict=True
            )
            if name == identity and g is not None
        )
    if not geometries:
        asset = mask.item["assets"][mask.runtime.get("selected_asset", "data")]
        if identity not in asset.get("autostacgis:seamline_sources", []):
            raise AutoSTACGISError(f"No seamline assignment exists for image {identity}")
        # A participating image may own no pixels after seamline competition.

    if not mask.payload.crs or not image.payload.grid.crs:
        raise AutoSTACGISError("Seamline masking requires known vector and raster CRS")
    if CRS.from_user_input(mask.payload.crs) != CRS.from_user_input(image.payload.grid.crs):
        project = Transformer.from_crs(mask.payload.crs, image.payload.grid.crs, always_xy=True).transform
        geometries = [transform(project, geometry) for geometry in geometries]
    raster = image.payload
    output = TransformRaster(
        [raster, PolygonMask(geometries, raster.grid)], raster.grid, _mask, {"invalid_values": [0]}
    )
    return {
        "result": [finish(context, output, image, {"assets.data.data_state:masked.mosaic_seamline": True})]
    }


ID = "mask_by_polygons"
REQUIRES = ("apply_mask", "source-member")
IMPLEMENTATIONS = {"process": mask_by_polygons}


mask_by_polygons.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:masked.mosaic_seamline": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {},
    "argument_requirements": {
        "image": {
            "kind": "raster",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.data_state:georeferencing", "equals": "affine"}]},
        },
        "mask": {
            "kind": "vector",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.roles", "contains": "mosaic-seamline-polygons"}]},
        },
    },
    "input_requirements": [
        {"plugin": "source-member", "observes": [], "inputs": {"member": "image", "collection": "mask"}},
        {"plugin": "source-member", "observes": [], "inputs": {"member": "image", "collection": "mask"}},
    ],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
mask_by_polygons.contract['outputs']['result']["data_types"] = ['raster']
mask_by_polygons.contract['argument_requirements']['image']["data_types"] = ['raster']
mask_by_polygons.contract['argument_requirements']['mask']["data_types"] = ['batches']
