from dataclasses import replace

import numpy as np

from ..data import Block, TransformRaster, WarpedRaster
from ..data.metadata import finish


def _mask(blocks, window, settings):
    image, mask = blocks
    valid = np.broadcast_to(~np.isin(mask.pixels[0], settings["invalid_values"]), image.pixels.shape).copy()
    if image.valid is not None:
        valid &= image.valid
    if mask.valid is not None:
        valid &= np.broadcast_to(mask.valid, mask.pixels.shape)[0]
    return Block(image.pixels, window, valid)


def apply_mask(inputs, settings, context):
    image, mask = inputs["image"][0], inputs["mask"][0]
    raster, masking = image.payload, mask.payload
    from ..errors import AutoSTACGISError

    if masking.kind != "raster":
        raise AutoSTACGISError("Use mask_by_polygons for image-specific seamline polygons")
    invalid = settings.get("invalid_values", [1, 2, 3])
    if not {1, 2, 3} <= set(invalid):
        raise AutoSTACGISError("Cloud-and-shadow masking requires thick cloud, thin cloud and shadow classes")
    if (
        masking.grid.transform != raster.grid.transform
        or masking.grid.shape[1:] != raster.grid.shape[1:]
        or masking.grid.crs != raster.grid.crs
    ):
        masking = WarpedRaster(
            context.storage.path(masking, reason="Mask resampling requires random access"),
            replace(raster.grid, count=masking.grid.count, dtype=masking.grid.dtype),
            "nearest",
        )
    output = TransformRaster([raster, masking], raster.grid, _mask, {"invalid_values": invalid})
    return {
        "result": [
            finish(
                context,
                output,
                image,
                {
                    "assets.data.data_state:masked.cloud": True,
                    "assets.data.data_state:masked.cloud_shadow": True,
                },
            )
        ]
    }


apply_mask.settings_schema = {
    "type": "object",
    "properties": {"invalid_values": {"type": "array", "items": {"type": "number"}}},
    "additionalProperties": False,
}


ID = "apply_mask"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": apply_mask}


apply_mask.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:masked.cloud": True},
                {"assets.data.data_state:masked.cloud_shadow": True},
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
            "kind": "raster",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.roles", "contains": "cloud-mask"}]},
        },
    },
    "input_requirements": [{"relation": "same_sources", "ports": ["image", "mask"]}],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
apply_mask.contract['outputs']['result']["data_types"] = ['raster']
apply_mask.contract['argument_requirements']['image']["data_types"] = ['raster']
apply_mask.contract['argument_requirements']['mask']["data_types"] = ['raster']
