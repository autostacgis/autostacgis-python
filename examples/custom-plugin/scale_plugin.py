"""A complete module: declarative contract, native window kernel, and execution."""

from dataclasses import replace

import numpy as np

from autostacgis import module
from autostacgis.data import Block, TransformRaster


def scale_window(blocks, window, settings):
    block = blocks[0]
    pixels = np.multiply(block.pixels, settings["factor"], dtype="float32")
    return Block(pixels, window, block.valid)


@module(
    argument_requirements={"image": {"kind": "raster", "data_types": ["raster"]}},
    outputs={
        "result": {
            "kind": "raster",
            "data_types": ["raster"],
            "inherit_metadata": ["image"],
            "set": [{"properties.example:scaled": True}],
        }
    },
    settings={"factor": 2},
    cost=1,
)
def scale(image, settings, context):
    payload = TransformRaster(
        [image.payload],
        replace(image.payload.grid, dtype="float32"),
        scale_window,
        settings,
    )
    return {"result": [context.output("result", payload, facts={"properties.example:scaled": True})]}


ID = "scale"
REQUIRES = ()
IMPLEMENTATIONS = {"process": scale}
