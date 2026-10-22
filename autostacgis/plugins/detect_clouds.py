"""Lazy OmniCloudMask inference on fixed tiles with context halos."""

from collections import OrderedDict
from dataclasses import replace

import numpy as np

from ..data import Block, Window, physical_raster
from ..data.metadata import finish
from ..data.raster import Raster
from ..errors import AutoSTACGISError
from ..semantics import descriptor_facts


class CloudRaster(Raster):
    def __init__(self, source, indices, tile_size, halo, options, memory_bytes):
        self.source, self.indices = source, indices
        self.tile_size, self.halo, self.options = tile_size, halo, options
        self.grid = replace(source.grid, count=1, dtype="uint8", nodata=255)
        side = max(tile_size + 2 * halo, options.get("patch_size", 1000))
        if side * side * 3 * 4 * 8 > memory_bytes:
            raise AutoSTACGISError("Cloud inference tile buffers exceed the memory budget; reduce tile/patch size")
        self._cache = OrderedDict()

    def tile(self, x, y):
        key = (x, y)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        import torch
        from omnicloudmask import predict_from_array

        torch.set_num_threads(1)
        left, top = max(0, x - self.halo), max(0, y - self.halo)
        right = min(self.grid.width, x + self.tile_size + self.halo)
        bottom = min(self.grid.height, y + self.tile_size + self.halo)
        block = self.source.read(Window(left, top, right - left, bottom - top))
        data = block.pixels[self.indices].astype("float32")
        valid = np.all(np.isfinite(data), axis=0)
        if block.valid is not None:
            valid &= np.all(np.broadcast_to(block.valid, block.pixels.shape)[self.indices], axis=0)
        data[:, ~valid] = self.options.get("no_data_value", 0)
        patch = self.options.get("patch_size", 1000)
        data = np.pad(
            data,
            ((0, 0), (0, max(0, patch - data.shape[1])), (0, max(0, patch - data.shape[2]))),
            mode="edge",
        )
        options = {**self.options, "batch_size": 1, "mosaic_device": "cpu"}
        prediction = np.asarray(predict_from_array(data, **options)).squeeze()
        if prediction.shape != data.shape[1:]:
            raise AutoSTACGISError("Cloud model returned an unexpected mask shape")
        width, height = min(self.tile_size, self.grid.width - x), min(self.tile_size, self.grid.height - y)
        region = (slice(y - top, y - top + height), slice(x - left, x - left + width))
        pixels = prediction[region].astype("uint8")
        mask = valid[region]
        pixels[~mask] = 255
        result = pixels, mask
        self._cache[key] = result
        while len(self._cache) > 2:
            self._cache.popitem(last=False)
        return result

    def read(self, window):
        self.validate_window(window)
        pixels = np.empty((1, window.height, window.width), dtype="uint8")
        valid = np.empty_like(pixels, dtype=bool)
        size = self.tile_size
        for y in range(window.y // size * size, window.y + window.height, size):
            for x in range(window.x // size * size, window.x + window.width, size):
                tile, mask = self.tile(x, y)
                left, top = max(x, window.x), max(y, window.y)
                right, bottom = (
                    min(x + tile.shape[1], window.x + window.width),
                    min(y + tile.shape[0], window.y + window.height),
                )
                source = (slice(top - y, bottom - y), slice(left - x, right - x))
                target = (
                    0,
                    slice(top - window.y, bottom - window.y),
                    slice(left - window.x, right - window.x),
                )
                pixels[target], valid[target] = tile[source], mask[source]
        return Block(pixels, window, valid)


def detect_clouds(inputs, settings, context):
    image = inputs["image"][0]
    asset = image.item["assets"][image.runtime.get("selected_asset", "data")]
    bands = asset.get("bands", [])
    indices = settings.get("bands")
    if indices is None:
        common = {band.get("eo:common_name", band.get("common_name")): i + 1 for i, band in enumerate(bands)}
        indices = [common.get(name) for name in ("red", "green", "nir")]
    raster = physical_raster(image)
    if len(indices) != 3 or any(i is None or i < 1 or i > raster.grid.count for i in indices):
        raise AutoSTACGISError("OmniCloudMask needs red, green, NIR band metadata or three explicit band indices")
    # Its models operate at a coarser metric inference grid, not at native VHR resolution.
    resolution = settings.get("resolution", 10)
    if resolution is not None:
        from pyproj import CRS

        crs = CRS.from_user_input(raster.grid.crs)
        destination = settings.get("crs")
        if destination is None and (not crs.is_projected or crs.axis_info[0].unit_conversion_factor != 1):
            from affine import Affine
            from rasterio.transform import array_bounds
            from rasterio.warp import transform_bounds

            left, bottom, right, top = transform_bounds(
                crs,
                "EPSG:4326",
                *array_bounds(raster.grid.height, raster.grid.width, Affine(*raster.grid.transform)),
            )
            zone = min(60, max(1, int(((left + right) / 2 + 180) // 6) + 1))
            destination = f"EPSG:{32600 + zone if (top + bottom) / 2 >= 0 else 32700 + zone}"
        proxy = image.clone()
        proxy.payload = raster
        raster = context.call(
            "align_grid",
            "process",
            {"image": [proxy]},
            {"resolution": resolution, "crs": destination or crs.to_string(), "resampling": "bilinear"},
        )["result"][0].payload
    output = CloudRaster(
        raster,
        [i - 1 for i in indices],
        settings.get("tile_size", context.storage.tile_size),
        settings.get("halo", 150),
        dict(settings.get("model", {})),
        context.storage.limit,
    )
    return {"result": [finish(context, output, image, descriptor_facts("cloud_mask"))]}


detect_clouds.settings_schema = {
    "type": "object",
    "properties": {
        "bands": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "integer", "minimum": 1}},
        "resolution": {"type": ["number", "null"], "exclusiveMinimum": 0},
        "crs": {"type": "string"},
        "tile_size": {"type": "integer", "minimum": 1},
        "halo": {"type": "integer", "minimum": 0},
        "model": {"type": "object"},
    },
    "additionalProperties": False,
}

ID = "detect_clouds"
REQUIRES = ("storage", "align_grid")
IMPLEMENTATIONS = {"process": detect_clouds}


detect_clouds.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.roles": ["data", "cloud-mask"]},
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
    "settings": {},
    "argument_requirements": {
        "image": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:spectral_sampling", "equals": "multispectral"},
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                ]
            },
        }
    },
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
detect_clouds.contract['outputs']['result']["data_types"] = ['raster']
detect_clouds.contract['argument_requirements']['image']["data_types"] = ['raster']
