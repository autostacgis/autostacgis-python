from ..data import FileRaster
from ..data.metadata import finish, resampling_history
from ..errors import AutoSTACGISError


def orthorectify_rpc(inputs, settings, context):
    from osgeo import gdal

    image, dem = inputs["image"][0], inputs["dem"][0]
    path = context.storage.path(image.payload, reason="GDAL RPC transformer requires a seekable input")
    dem_path = context.storage.path(dem.payload, reason="RPC terrain lookup requires random access")
    with gdal.Open(path) as source:
        if not source.GetMetadata("RPC"):
            raise AutoSTACGISError("RPC orthorectification requires a valid source RPC model")
    destination = context.directory / "orthorectified.vrt"
    options = dict(
        format="VRT",
        rpc=True,
        dstSRS=settings["crs"],
        transformerOptions=[f"RPC_DEM={dem_path}"],
        resampleAlg=settings.get("resampling", "bilinear"),
        multithread=False,
        warpOptions=["NUM_THREADS=1"],
    )
    if "resolution" in settings:
        options.update(xRes=settings["resolution"], yRes=settings["resolution"])
    result = gdal.Warp(str(destination), path, **options)
    if result is None:
        raise AutoSTACGISError("RPC orthorectification failed")
    result = None
    return {
        "result": [
            resampling_history(
                finish(
                    context, FileRaster(destination), image, {"assets.data.data_state:orthorectified": True}
                ),
                image,
                settings.get("resampling", "bilinear"),
            )
        ]
    }


orthorectify_rpc.settings_schema = {
    "type": "object",
    "required": ["crs"],
    "properties": {
        "crs": {"type": "string"},
        "resolution": {"type": "number", "exclusiveMinimum": 0},
        "resampling": {"enum": ["nearest", "bilinear", "cubic"]},
    },
    "additionalProperties": False,
}


ID = "orthorectify_rpc"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": orthorectify_rpc}


orthorectify_rpc.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:orthorectified": True},
                {"assets.data.data_state:georeferencing": "affine"},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "append": [{"assets.data.data_state:resampled": {"jsonata": "settings.resampling"}}],
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {"crs": "EPSG:4326", "resampling": "bilinear"},
    "argument_requirements": {
        "image": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "rpc"},
                    {"not": {"path": "assets.$asset.data_state:orthorectified", "equals": True}},
                ]
            },
        },
        "dem": {
            "kind": "raster",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.cf:standard_name", "equals": "surface_altitude"}]},
        },
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


orthorectify_rpc.contract["outputs"]["result"]["predict"] = predict_grid


# Exchange representations are independent of scientific metadata.
orthorectify_rpc.contract['outputs']['result']["data_types"] = ['raster']
orthorectify_rpc.contract['argument_requirements']['image']["data_types"] = ['raster']
orthorectify_rpc.contract['argument_requirements']['dem']["data_types"] = ['raster']
