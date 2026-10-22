from dataclasses import replace

from ..data import WarpedRaster
from ..data.metadata import finish, resampling_history
from ..errors import AutoSTACGISError


def align_grid(inputs, settings, context):
    from affine import Affine
    from rasterio.transform import array_bounds
    from rasterio.warp import calculate_default_transform

    source = inputs["image"][0]
    raster = source.payload
    crs = settings.get("crs", raster.grid.crs)
    if not raster.grid.crs or not crs:
        raise AutoSTACGISError("Grid alignment requires known source and destination CRS")
    bounds = array_bounds(raster.grid.height, raster.grid.width, Affine(*raster.grid.transform))
    transform, width, height = calculate_default_transform(
        raster.grid.crs,
        crs,
        raster.grid.width,
        raster.grid.height,
        *bounds,
        resolution=settings.get("resolution"),
    )
    grid = replace(
        raster.grid,
        crs=crs,
        transform=tuple(transform)[:6],
        width=width,
        height=height,
        rpc=None,
        georeferencing="affine",
    )
    path = context.storage.path(raster, reason="GDAL warp requires random access")
    result = WarpedRaster(path, grid, settings.get("resampling", "bilinear"))
    return {
        "result": [
            resampling_history(
                finish(context, result, source, {"assets.data.data_state:grid_aligned": True}),
                source,
                settings.get("resampling", "bilinear"),
            )
        ]
    }


align_grid.settings_schema = {
    "type": "object",
    "properties": {
        "crs": {"type": "string"},
        "resolution": {"type": "number", "exclusiveMinimum": 0},
        "resampling": {"enum": ["nearest", "bilinear", "cubic", "average"]},
    },
    "additionalProperties": False,
}


ID = "align_grid"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": align_grid}


align_grid.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:grid_aligned": True},
                {"assets.data.data_state:georeferencing": "affine"},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "append": [{"assets.data.data_state:resampled": {"jsonata": "settings.resampling"}}],
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {"resampling": "bilinear"},
    "argument_requirements": {
        "image": {
            "kind": "raster",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.data_state:georeferencing", "equals": "affine"}]},
        }
    },
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


def predict_grid(inputs, settings, index, item):
    from pyproj import CRS

    asset = item["assets"]["data"]
    if settings.get("crs"):
        crs = CRS.from_user_input(settings["crs"])
        for key in ("proj:code", "proj:wkt2", "proj:projjson", "proj:shape", "proj:transform"):
            asset.pop(key, None)
        authority = crs.to_authority()
        asset.update({"proj:code": ":".join(authority)} if authority else {"proj:wkt2": crs.to_wkt()})
    return item


align_grid.contract["outputs"]["result"]["predict"] = predict_grid


# Exchange representations are independent of scientific metadata.
align_grid.contract['outputs']['result']["data_types"] = ['raster']
align_grid.contract['argument_requirements']['image']["data_types"] = ['raster']
