import numpy as np

from ..data import ArrayRaster, Grid
from ..data.metadata import finish
from ..errors import AutoSTACGISError
from ..semantics import descriptor_facts


def rasterize_point_attribute(inputs, settings, context):
    from affine import Affine

    cloud = inputs["points"][0]
    grid = Grid(**settings["grid"])
    if grid.count != 1:
        raise AutoSTACGISError("Point attribute rasterization produces one band")
    size = grid.width * grid.height
    required = size * (25 + np.dtype(grid.dtype).itemsize)

    def allocate(name, dtype, initial):
        if required > context.storage.limit:
            value = np.memmap(context.directory / (name + ".bin"), mode="w+", dtype=dtype, shape=(size,))
            value[:] = initial
            return value
        return np.full(size, initial, dtype=dtype)

    count = allocate("count", "int64", 0)
    total = allocate("total", "float64", 0)
    extremes = allocate(
        "extremes", "float64", np.inf if settings.get("statistic", "mean") == "min" else -np.inf
    )
    transformer = None
    if bool(cloud.payload.crs) != bool(grid.crs):
        raise AutoSTACGISError("Point and output CRS must both be known or both use a local coordinate system")
    if cloud.payload.crs and grid.crs:
        from pyproj import CRS, Transformer

        if CRS.from_user_input(cloud.payload.crs) != CRS.from_user_input(grid.crs):
            transformer = Transformer.from_crs(cloud.payload.crs, grid.crs, always_xy=True)
    inverse = ~Affine(*grid.transform)
    for batch in cloud.payload.batches():
        x = batch.column("X").to_numpy(zero_copy_only=False)
        y = batch.column("Y").to_numpy(zero_copy_only=False)
        if transformer:
            x, y = transformer.transform(x, y)
        values = batch.column(settings.get("attribute", "Intensity")).to_numpy(zero_copy_only=False)
        columns = np.floor(inverse.a * x + inverse.b * y + inverse.c).astype("int64")
        rows = np.floor(inverse.d * x + inverse.e * y + inverse.f).astype("int64")
        valid = (
            (columns >= 0) & (columns < grid.width) & (rows >= 0) & (rows < grid.height) & np.isfinite(values)
        )
        indices = rows[valid] * grid.width + columns[valid]
        np.add.at(count, indices, 1)
        np.add.at(total, indices, values[valid])
        if settings.get("statistic") == "min":
            np.minimum.at(extremes, indices, values[valid])
        elif settings.get("statistic") == "max":
            np.maximum.at(extremes, indices, values[valid])
    statistic = settings.get("statistic", "mean")
    values = {"sum": total, "count": count, "min": extremes, "max": extremes, "mean": total}.get(statistic)
    if values is None:
        raise AutoSTACGISError(f"Unsupported point statistic {statistic}")
    result = allocate("result", grid.dtype, 0)
    mask = allocate("valid", "bool", False)
    step = max(1, min(context.storage.tile_size**2, context.storage.limit // 32))
    for start in range(0, size, step):
        section = slice(start, start + step)
        valid = count[section] > 0
        result[section] = (
            np.divide(total[section], count[section], out=np.zeros(valid.shape), where=valid)
            if statistic == "mean"
            else values[section]
        )
        mask[section] = valid
    output = ArrayRaster(result.reshape(grid.shape), grid, mask.reshape(grid.shape))
    return {"result": [finish(context, output, cloud, descriptor_facts("lidar_return_intensity"))]}


rasterize_point_attribute.settings_schema = {
    "type": "object",
    "properties": {
        "grid": {"type": "object"},
        "attribute": {"type": "string"},
        "statistic": {"enum": ["mean", "sum", "count", "min", "max"]},
    },
    "required": ["grid"],
    "additionalProperties": False,
}


ID = "rasterize_point_attribute"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": rasterize_point_attribute}


rasterize_point_attribute.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["points"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.roles": ["data", "return-intensity"]},
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
    "anchor": "points",
    "settings": {"grid": {"width": 256, "height": 256, "count": 1, "dtype": "float32"}},
    "argument_requirements": {
        "points": {"kind": "point_cloud", "cardinality": "one", "require": {"all": []}}
    },
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
rasterize_point_attribute.contract['outputs']['result']["data_types"] = ['raster']
rasterize_point_attribute.contract['argument_requirements']['points']["data_types"] = ['batches']
