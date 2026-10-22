import numpy as np

from ..data import vectors
from ..data.metadata import finish
from ..semantics import descriptor_facts


def polygonize(inputs, settings, context):
    from affine import Affine
    from rasterio.features import shapes
    from shapely import union_all
    from shapely.geometry import shape

    record = inputs["image"][0]
    by_value = {}
    for block in record.payload.blocks(tile_size=context.storage.tile_size):
        pixels = block.pixels[settings.get("band", 1) - 1]
        mask = (
            None
            if block.valid is None
            else np.broadcast_to(block.valid, block.pixels.shape)[settings.get("band", 1) - 1]
        )
        transform = Affine(*record.payload.grid.transform) * Affine.translation(
            block.window.x, block.window.y
        )
        for geometry, value in shapes(pixels.astype("float32"), mask=mask, transform=transform):
            by_value.setdefault(value, []).append(shape(geometry))
    output = vectors(
        [union_all(v) for v in by_value.values()],
        crs=record.payload.grid.crs,
        attributes={"value": list(by_value)},
    )
    return {"result": [finish(context, output, record, descriptor_facts("classification_polygons"))]}


ID = "polygonize"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": polygonize}


polygonize.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.roles": ["data", "classification"]},
            ],
            "remove": [
                "properties.product:type",
                "assets.data.product:type",
                "properties.cf:standard_name",
                "properties.unit",
                "properties.bands",
                "properties.data_type",
                "properties.nodata",
                "properties.statistics",
                "properties.raster:scale",
                "properties.raster:offset",
                "properties.raster:histogram",
                "properties.data_state:spectral_sampling",
                "properties.data_state:radiometry",
                "properties.data_state:atmospheric_correction",
                "assets.data.cf:standard_name",
                "assets.data.unit",
                "assets.data.bands",
                "assets.data.data_type",
                "assets.data.nodata",
                "assets.data.statistics",
                "assets.data.raster:scale",
                "assets.data.raster:offset",
                "assets.data.raster:histogram",
                "assets.data.data_state:spectral_sampling",
                "assets.data.data_state:radiometry",
                "assets.data.data_state:atmospheric_correction",
            ],
            "kind": "vector",
        }
    },
    "anchor": "image",
    "settings": {},
    "argument_requirements": {"image": {"kind": "raster", "cardinality": "one", "require": {"all": []}}},
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
polygonize.contract['outputs']['result']["data_types"] = ['batches']
polygonize.contract['argument_requirements']['image']["data_types"] = ['raster']
