from dataclasses import replace

import numpy as np

from ..data import Block, TransformRaster, WarpedRaster, physical_raster
from ..data.metadata import finish, resampling_history
from ..errors import AutoSTACGISError


def _brovey(blocks, window, settings):
    multispectral, pan = blocks
    pixels = multispectral.pixels.astype("float32", copy=False)
    weights = np.asarray(settings["weights"], dtype="float32")
    intensity = np.einsum("b,byx->yx", weights, pixels)
    valid = np.isfinite(intensity) & (intensity != 0)
    if multispectral.valid is not None:
        valid &= np.all(np.broadcast_to(multispectral.valid, pixels.shape), axis=0)
    if pan.valid is not None:
        valid &= np.all(np.broadcast_to(pan.valid, pan.pixels.shape), axis=0)
    high = pan.pixels[0] * settings.get("pan_gain", 1) + settings.get("pan_offset", 0)
    valid &= np.isfinite(high)
    ratio = np.divide(high, intensity, out=np.zeros_like(intensity), where=valid)
    return Block(pixels * ratio[None], window, np.broadcast_to(valid, pixels.shape))


def pansharpen(inputs, settings, context):
    multispectral, pan = inputs["multispectral"][0], inputs["panchromatic"][0]
    from .common_crs import require_common_crs

    require_common_crs(inputs, names=["multispectral", "panchromatic"])
    ms, high = physical_raster(multispectral), physical_raster(pan)
    if any(r.grid.georeferencing in ("rpc", "none") or not r.grid.crs for r in (ms, high)):
        raise AutoSTACGISError("Pansharpening requires affine georeferenced grids; orthorectify RPC inputs first")
    if high.grid.count != 1:
        raise AutoSTACGISError("Panchromatic input must have exactly one band")
    weights = settings.get("weights", [1 / ms.grid.count] * ms.grid.count)
    if len(weights) != ms.grid.count or any(v < 0 for v in weights) or not np.isclose(sum(weights), 1):
        raise AutoSTACGISError("Pansharpen weights must be nonnegative, one per band, and sum to one")
    target = replace(
        high.grid,
        count=ms.grid.count,
        dtype="float32",
        nodata=float("nan"),
        rpc=None,
        georeferencing="affine",
    )
    # Replayable windows allow a bounded statistics pass before the fused pixel kernel.
    path = context.storage.path(ms, reason="GDAL pansharpen resampling requires random access")
    ms = WarpedRaster(path, target, settings.get("resampling", "bilinear"))
    parameters = {"weights": weights}
    if settings.get("pan_normalization", "match") == "match":
        n = 0
        sums, squares = np.zeros(2), np.zeros(2)
        for window in ms.windows(tile_size=context.storage.tile_size):
            left, right = ms.read(window), high.read(window)
            intensity = np.einsum("b,byx->yx", weights, left.pixels)
            valid = np.isfinite(intensity) & np.isfinite(right.pixels[0])
            if left.valid is not None:
                valid &= np.all(np.broadcast_to(left.valid, left.pixels.shape), axis=0)
            if right.valid is not None:
                valid &= np.all(np.broadcast_to(right.valid, right.pixels.shape), axis=0)
            values = np.stack([intensity[valid], right.pixels[0][valid]]).astype("float64")
            n += values.shape[1]
            sums += values.sum(axis=1)
            squares += np.square(values).sum(axis=1)
        if n == 0:
            raise AutoSTACGISError("Pansharpening inputs have no valid overlapping pixels")
        means = sums / n
        deviations = np.sqrt(np.maximum(0, squares / n - means**2))
        gain = deviations[0] / deviations[1] if deviations[1] > 1e-12 else 0.0
        parameters.update(pan_gain=float(gain), pan_offset=float(means[0] - gain * means[1]))
    else:
        assets = [r.item["assets"][r.runtime.get("selected_asset", "data")] for r in (multispectral, pan)]
        for field in ("unit", "cf:standard_name"):
            if not assets[0].get(field) or assets[0].get(field) != assets[1].get(field):
                raise AutoSTACGISError(
                    "Unnormalized pansharpening requires matching known radiometric quantities and units"
                )
    result = TransformRaster([ms, high], target, _brovey, parameters)
    return {
        "result": [
            resampling_history(
                finish(
                    context,
                    result,
                    multispectral,
                    {
                        "assets.data.data_state:pansharpened": True,
                        "assets.data.processing:lineage": "Weighted Brovey pansharpening with PAN intensity normalization",
                        "assets.data.autostacgis:pan_normalization": parameters,
                    },
                    physical=True,
                ),
                multispectral,
                settings.get("resampling", "bilinear"),
            )
        ]
    }


pansharpen.settings_schema = {
    "type": "object",
    "properties": {
        "weights": {"type": "array", "items": {"type": "number"}},
        "pan_normalization": {"enum": ["match", "none"]},
        "resampling": {"enum": ["nearest", "bilinear", "cubic"]},
    },
    "additionalProperties": False,
}


ID = "pansharpen"
REQUIRES = ("storage", "common-crs", "compatible-optical")
IMPLEMENTATIONS = {"process": pansharpen}


pansharpen.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["multispectral"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:pansharpened": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "append": [{"assets.data.data_state:resampled": {"jsonata": "settings.resampling"}}],
            "kind": "raster",
        }
    },
    "anchor": "multispectral",
    "settings": {"pan_normalization": "match", "resampling": "bilinear"},
    "argument_requirements": {
        "multispectral": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {"path": "assets.$asset.data_state:spectral_sampling", "equals": "multispectral"},
                    {"path": "assets.$asset.data_state:pansharpened", "exists": True},
                    {"not": {"path": "assets.$asset.data_state:pansharpened", "equals": True}},
                ]
            },
        },
        "panchromatic": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {"path": "assets.$asset.data_state:spectral_sampling", "equals": "panchromatic"},
                ]
            },
        },
    },
    "input_requirements": [
        {
            "plugin": "common-crs",
            "observes": ["assets.$asset.proj:code", "assets.$asset.proj:wkt2", "assets.$asset.proj:projjson"],
            "inputs": {"multispectral": "multispectral", "panchromatic": "panchromatic"},
        },
        {
            "plugin": "compatible-optical",
            "observes": ["properties.platform", "properties.datetime"],
            "inputs": {"left": "multispectral", "right": "panchromatic"},
        },
    ],
    "cost": 1,
    "version": "1.0.0",
}


for _check in pansharpen.contract["input_requirements"]:
    if _check.get("plugin") == "compatible-optical":
        _check["observes"].append("assets.$asset.data_state:registration")


# Exchange representations are independent of scientific metadata.
pansharpen.contract['outputs']['result']["data_types"] = ['raster']
pansharpen.contract['argument_requirements']['multispectral']["data_types"] = ['raster']
pansharpen.contract['argument_requirements']['panchromatic']["data_types"] = ['raster']
