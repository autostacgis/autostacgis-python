import ast
from dataclasses import replace

import numpy as np

from ..data import Block, TransformRaster, physical_raster
from ..data.metadata import finish
from ..errors import AutoSTACGISError
from ..semantics import descriptor_facts


def _math(blocks, window, settings):
    import numexpr

    pixels = blocks[0].pixels
    values = {f"b{index + 1}": band for index, band in enumerate(pixels)}
    output = np.stack(
        [
            np.broadcast_to(numexpr.evaluate(expr, local_dict=values, global_dict={}), pixels.shape[1:])
            for expr in settings["expressions"]
        ]
    ).astype("float32")
    valid = (
        np.all(blocks[0].valid, axis=0)
        if blocks[0].valid is not None and blocks[0].valid.ndim == 3
        else blocks[0].valid
    )
    return Block(output, window, valid)


def band_math(inputs, settings, context):
    image = inputs["image"][0]
    allowed_names = {f"b{index + 1}" for index in range(image.payload.grid.count)} | {
        "where",
        "sqrt",
        "log",
        "exp",
        "abs",
        "sin",
        "cos",
    }
    for expression in settings["expressions"]:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.NamedExpr, ast.ListComp)):
                raise AutoSTACGISError("Band expressions only support scalar operations on named bands")
            if isinstance(node, ast.Name) and node.id not in allowed_names:
                raise AutoSTACGISError(f"Unknown band expression name {node.id}")
    grid = replace(image.payload.grid, count=len(settings["expressions"]), dtype="float32")
    output = TransformRaster([physical_raster(image)], grid, _math, settings)
    return {"result": [finish(context, output, image, descriptor_facts("spectral_index"), physical=True)]}


band_math.settings_schema = {
    "type": "object",
    "properties": {"expressions": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
    "required": ["expressions"],
    "additionalProperties": False,
}


ID = "band_math"
REQUIRES = ()
IMPLEMENTATIONS = {"process": band_math}


band_math.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.roles": ["data", "spectral-index"]},
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
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {"expressions": ["b1"]},
    "argument_requirements": {"image": {"kind": "raster", "cardinality": "one", "require": {"all": []}}},
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
band_math.contract['outputs']['result']["data_types"] = ['raster']
band_math.contract['argument_requirements']['image']["data_types"] = ['raster']
