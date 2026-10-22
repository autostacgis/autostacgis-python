from ..data import FileRaster, physical_raster
from ..data.metadata import finish, resampling_history
from ..errors import AutoSTACGISError


def mosaic(inputs, settings, context):
    from osgeo import gdal

    from .common_crs import require_common_crs

    require_common_crs(inputs, names=["images"])
    records = inputs["images"]
    from ..metadata import MISSING
    from ..semantics import value_at

    if not records:
        raise AutoSTACGISError("Mosaic needs at least one image")
    if len({record.payload.grid.count for record in records}) != 1:
        raise AutoSTACGISError("Mosaic inputs need the same band count")
    for field in ("cf:standard_name", "unit"):
        values = [
            value_at(record.item, f"assets.{record.runtime.get('selected_asset', 'data')}.{field}")
            for record in records
        ]
        if any(value is MISSING or value is None for value in values) or any(
            value != values[0] for value in values
        ):
            raise AutoSTACGISError(f"Mosaic inputs need matching known {field}")
    band_names = [
        [
            band.get("eo:common_name", band.get("name"))
            for band in record.item["assets"][record.runtime.get("selected_asset", "data")].get("bands", [])
        ]
        for record in records
    ]
    if any(names != band_names[0] for names in band_names):
        raise AutoSTACGISError("Mosaic inputs need matching band order")
    paths = [
        context.storage.path(physical_raster(record), reason="GDAL virtual mosaic requires seekable inputs")
        for record in records
    ]
    target = context.directory / "mosaic.vrt"
    dataset = gdal.BuildVRT(
        str(target),
        paths,
        resolution=settings.get("resolution", "highest"),
        resampleAlg=settings.get("resampling", "nearest"),
    )
    if dataset is None:
        raise AutoSTACGISError("GDAL mosaic construction failed")
    dataset = None
    result = finish(
        context, FileRaster(target), records[0], {"assets.data.data_state:mosaic": True}, physical=True
    )
    resampling_history(result, records[0], settings.get("resampling", "nearest"))
    times = [r.item.get("properties", {}).get("datetime") for r in records]
    if all(times) and len(set(times)) > 1:
        result.item["properties"].update(datetime=None, start_datetime=min(times), end_datetime=max(times))
    return {"result": [result]}


def bind_inputs(records, settings, context):
    return context.call("gather-all", "sequence", records, settings)


ID = "mosaic"
REQUIRES = ("common-crs", "storage", "gather-all")
IMPLEMENTATIONS = {"process": mosaic, "sequence": bind_inputs}


mosaic.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["images"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:mosaic": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "append": [{"assets.data.data_state:resampled": {"jsonata": "settings.resampling"}}],
            "kind": "raster",
            "geometry": "union",
        }
    },
    "anchor": "images",
    "settings": {"resampling": "nearest"},
    "argument_requirements": {
        "images": {
            "kind": "raster",
            "cardinality": "many",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {"path": "assets.$asset.data_state:masked.mosaic_seamline", "equals": True},
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
    "cost": 1,
    "version": "1.0.0",
}


def goal(settings, records, context):
    from copy import deepcopy

    options = deepcopy(settings)
    area = options.pop("area", None)
    if area is not None:
        if isinstance(area, list):
            from shapely.geometry import box

            area = {
                "type": "Polygon",
                "coordinates": [[list(point) for point in box(*area).exterior.coords]],
            }
        options["coverage"] = {"geometry": area, "mode": options.pop("coverage_mode", "footprint")}
    options["require"] = {"all": [{"assets.$asset.data_state:mosaic": True}, options.get("require", {})]}
    options.setdefault("kind", "raster")
    return {"output": options}


IMPLEMENTATIONS["goal"] = goal

mosaic.contract["argument_requirements"]["images"]["sequence"]["settings"] = {
    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"}
}


def predict_mosaic(inputs, settings, index, item):
    """A mosaic only claims processing states shared by every contributing image."""
    from copy import deepcopy

    from ..contracts import value
    from ..metadata import MISSING

    destination = item["assets"]["data"]
    assets = [r.item["assets"][r.runtime.get("selected_asset", "data")] for r in inputs["images"]]
    fields = {"unit", "cf:standard_name"} | {
        key for asset in assets for key in asset if key.startswith("data_state:")
    }
    for key in fields:
        values = [value(r, "assets.$asset." + key) for r in inputs["images"]]
        if key == "data_state:registration":
            if any(not isinstance(v, list) for v in values):
                destination[key] = None
            else:
                common = set.intersection(*[{entry.get("kind") for entry in v} for v in values])
                destination[key] = [
                    deepcopy(entry) for v in values for entry in v if entry.get("kind") in common
                ]
        elif key == "data_state:masked":
            keys = {k for v in values if isinstance(v, dict) for k in v}
            destination[key] = {
                k: (
                    values[0].get(k)
                    if all(isinstance(v, dict) for v in values)
                    and all(v.get(k) == values[0].get(k) for v in values)
                    else None
                )
                for k in keys
            }

        elif key not in ("data_state:resampled", "data_state:mosaic"):
            destination[key] = (
                deepcopy(values[0]) if all(v is not MISSING and v == values[0] for v in values) else None
            )
    destination["data_state:mosaic"] = True
    return item


mosaic.contract["outputs"]["result"]["predict"] = predict_mosaic

REQUIRES = (*REQUIRES, "same-fields")
mosaic.contract["input_requirements"].append(
    {
        "plugin": "same-fields",
        "inputs": {"images": "images"},
        "settings": {"fields": ["assets.$asset.unit", "assets.$asset.cf:standard_name"]},
        "observes": ["assets.$asset.unit", "assets.$asset.cf:standard_name"],
    }
)


# Exchange representations are independent of scientific metadata.
mosaic.contract['outputs']['result']["data_types"] = ['raster']
mosaic.contract['argument_requirements']['images']["data_types"] = ['raster']
