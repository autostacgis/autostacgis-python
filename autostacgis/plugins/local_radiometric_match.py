import inspect
from pathlib import Path

import numpy as np

from ..data import FileRaster, nodata_raster, physical_raster
from ..data.metadata import finish
from ..data.writers import vrt_paths
from ..errors import AutoSTACGISError


def local_radiometric_match(inputs, settings, context):
    from spectralmatch import local_block_adjustment

    from .common_crs import require_common_crs

    require_common_crs(inputs, names=["images"])
    records = inputs["images"]
    rasters = [
        nodata_raster(physical_raster(record), nodata=float(np.finfo("float32").min)) for record in records
    ]
    paths = vrt_paths(rasters, context.storage, context.directory)
    outputs = [str(context.directory / f"output-{index}.tif") for index in range(len(paths))]
    options = dict(settings)
    signature = inspect.signature(local_block_adjustment)
    # Runtime ownership wins over implementation-specific pool defaults.
    for key, value in [
        ("executor", context.executor),
        ("tile_threads", 1),
        ("io_threads", 1),
        ("image_threads", None),
        ("concurrent_processing", 1),
        ("concurrent_processing_backend", "process_pool"),
    ]:
        if key in signature.parameters:
            options[key] = value
    local_block_adjustment(input_images=paths, output_images=outputs, **options)
    result = []
    for index, (path, source) in enumerate(zip(outputs, records, strict=True)):
        if not Path(path).is_file():
            raise AutoSTACGISError(f"local_block_adjustment did not create {path}")
        record = finish(
            context,
            FileRaster(path),
            source,
            {"assets.data.data_state:local_radiometric_adjustment": True},
            port="results",
            index=index,
            physical=True,
        )
        record.item["assets"]["data"]["autostacgis:normalization_group"] = [r.item["id"] for r in records]
        result.append(record)
    return {"results": result}


def bind_inputs(records, settings, context):
    return context.call("gather-all", "sequence", records, settings)


ID = "local_radiometric_match"
REQUIRES = ("common-crs", "storage", "executor", "gather-all")
IMPLEMENTATIONS = {"process": local_radiometric_match, "sequence": bind_inputs}


local_radiometric_match.contract = {
    "outputs": {
        "results": {
            "inherit_metadata": ["images"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:local_radiometric_adjustment": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "each_from": "images",
            "kind": "raster",
        }
    },
    "anchor": "images",
    "settings": {},
    "cost": {
        "parameters": {"registration": "images.assets.$asset.data_state:registration"},
        "jsonata": '($n := $type(registration) != "array" or $count(registration) = 0 ? 0 : '
        '($type(registration[0]) = "object" ? $count(registration) : '
        '$min($map(registration, function($h){$type($h) = "array" ? $count($h) : '
        "0}))); 1 + $max([0, 4 - 2 * $n]))",
    },
    "argument_requirements": {
        "images": {
            "kind": "raster",
            "cardinality": "many",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {
                        "not": {
                            "path": "assets.$asset.data_state:local_radiometric_adjustment",
                            "equals": True,
                        }
                    },
                    {"path": "assets.$asset.data_state:relative_radiometric_normalization", "equals": True},
                ]
            },
            "sequence": {"plugin": "gather-all"},
        }
    },
    "input_requirements": [
        {
            "plugin": "common-crs",
            "observes": ["assets.$asset.proj:code", "assets.$asset.proj:wkt2", "assets.$asset.proj:projjson"],
            "inputs": {"images": "images"},
        }
    ],
    "version": "1.0.0",
}

local_radiometric_match.contract["argument_requirements"]["images"]["sequence"]["settings"] = {
    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"}
}


# Preserve existing registration preferences and additionally favor masked imagery.
_previous_cost = local_radiometric_match.contract.get("cost", 1)
if not isinstance(_previous_cost, dict):
    _previous_cost = {"parameters": {}, "jsonata": str(_previous_cost)}
local_radiometric_match.contract["cost"] = {
    "parameters": {
        **_previous_cost.get("parameters", {}),
        "masked": "images.assets.$asset.data_state:masked",
    },
    "jsonata": "(" + _previous_cost["jsonata"] + ") + "
    '($m := $type(masked) = "array" ? masked : [masked]; '
    '$n := $min($map($m, function($v){ $type($v) = "object" ? '
    "$count($filter($each($v, function($x){$x}), function($x){$x = true})) : 0 })); "
    "4 / (1 + $n))",
}


# Seamline clipping partitions overlapping observations and removes the common
# pixels required by registration and relative radiometric estimation.
local_radiometric_match.contract["argument_requirements"]["images"]["require"]["all"].append(
    {
        "any": [
            {"path": "assets.$asset.data_state:masked.mosaic_seamline", "equals": False},
            {"path": "assets.$asset.data_state:masked.mosaic_seamline", "exists": False},
        ]
    }
)


# Exchange representations are independent of scientific metadata.
local_radiometric_match.contract['outputs']['results']["data_types"] = ['raster']
local_radiometric_match.contract['argument_requirements']['images']["data_types"] = ['raster']
