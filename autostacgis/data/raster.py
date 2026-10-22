"""Replayable raster windows over native arrays. No mandatory intermediate encoding."""

from dataclasses import dataclass, replace
from multiprocessing import shared_memory

import numpy as np

from ..errors import AutoSTACGISError


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    count: int
    dtype: str
    transform: tuple = (1, 0, 0, 0, -1, 0)
    crs: str | None = None
    nodata: float | int | None = None
    rpc: dict | None = None
    georeferencing: str | None = None

    @property
    def shape(self):
        return self.count, self.height, self.width


@dataclass(frozen=True)
class Window:
    x: int
    y: int
    width: int
    height: int


@dataclass
class Block:
    pixels: np.ndarray
    window: Window
    valid: np.ndarray | None = None

    def __post_init__(self):
        if self.pixels.ndim != 3 or self.pixels.shape[1:] != (self.window.height, self.window.width):
            raise AutoSTACGISError("Raster blocks use (band, row, column) native arrays")
        self.pixels.flags.writeable = False
        if self.valid is not None:
            if self.valid.shape not in (self.pixels.shape, self.pixels.shape[1:]):
                raise AutoSTACGISError("Raster mask shape does not match pixels")
            self.valid.flags.writeable = False


class Raster:
    kind = "raster"
    replayable = True
    random_access = True

    def read(self, window):
        raise NotImplementedError

    def windows(self, *, tile_size=512, halo=0):
        if tile_size < 1 or halo < 0:
            raise AutoSTACGISError("Tile size must be positive and halo nonnegative")
        for y in range(0, self.grid.height, tile_size):
            for x in range(0, self.grid.width, tile_size):
                left, top = max(0, x - halo), max(0, y - halo)
                yield Window(
                    left,
                    top,
                    min(self.grid.width, x + tile_size + halo) - left,
                    min(self.grid.height, y + tile_size + halo) - top,
                )

    def blocks(self, *, tile_size=512):
        for window in self.windows(tile_size=tile_size):
            yield self.read(window)

    def validate_window(self, window):
        if (
            window.x < 0
            or window.y < 0
            or window.width < 1
            or window.height < 1
            or window.x + window.width > self.grid.width
            or window.y + window.height > self.grid.height
        ):
            raise AutoSTACGISError(f"Window outside raster: {window}")


class ArrayRaster(Raster):
    def __init__(self, pixels, grid=None, valid=None):
        pixels = np.asarray(pixels)
        if pixels.ndim == 2:
            pixels = pixels[None, ...]
        if pixels.ndim != 3 or pixels.dtype.hasobject:
            raise AutoSTACGISError("Raster pixels must be a numeric (band, row, column) array")
        self.pixels = pixels.view()
        self.pixels.flags.writeable = False
        self.valid = valid
        self.grid = grid or Grid(pixels.shape[2], pixels.shape[1], pixels.shape[0], str(pixels.dtype))
        if self.grid.shape != pixels.shape or np.dtype(self.grid.dtype) != pixels.dtype:
            raise AutoSTACGISError("Raster grid and buffer disagree")

    def read(self, window):
        self.validate_window(window)
        selection = (..., slice(window.y, window.y + window.height), slice(window.x, window.x + window.width))
        valid = self.valid[selection] if self.valid is not None else None
        return Block(self.pixels[selection], window, valid)


class FileRaster(Raster):
    def __init__(self, path):
        import rasterio

        self.path = str(path)
        with rasterio.open(self.path) as dataset:
            self.grid = Grid(
                dataset.width,
                dataset.height,
                dataset.count,
                dataset.dtypes[0],
                tuple(dataset.transform)[:6],
                dataset.crs.to_string() if dataset.crs else None,
                dataset.nodata,
                dataset.tags(ns="RPC") or None,
                "affine"
                if dataset.crs and not dataset.transform.is_identity
                else ("rpc" if dataset.tags(ns="RPC") else "none"),
            )

    def read(self, window):
        import rasterio
        from rasterio.windows import Window as RioWindow

        self.validate_window(window)
        # Handles are local to each call; concurrent readers do not share a GDAL handle.
        with rasterio.open(self.path) as dataset:
            region = RioWindow(window.x, window.y, window.width, window.height)
            return Block(dataset.read(window=region), window, dataset.read_masks(window=region) > 0)


class TransformRaster(Raster):
    def __init__(self, sources, grid, kernel, settings=None):
        self.sources = tuple(sources)
        self.grid = grid
        self.kernel = kernel
        self.settings = settings or {}

    def read(self, window):
        self.validate_window(window)
        return self.kernel([source.read(window) for source in self.sources], window, self.settings)


class SharedRaster(Raster):
    """Picklable shared-memory descriptor. The owning storage service unlinks it."""

    def __init__(self, name, grid, mask_name=None):
        self.name, self.grid, self.mask_name = name, grid, mask_name

    def read(self, window):
        self.validate_window(window)
        owner = shared_memory.SharedMemory(name=self.name)
        pixels = np.ndarray(self.grid.shape, dtype=self.grid.dtype, buffer=owner.buf)
        selection = (..., slice(window.y, window.y + window.height), slice(window.x, window.x + window.width))
        mask_owner = shared_memory.SharedMemory(name=self.mask_name) if self.mask_name else None
        mask = np.ndarray(self.grid.shape, dtype=bool, buffer=mask_owner.buf) if mask_owner else None
        result = Block(pixels[selection], window, mask[selection] if mask is not None else None)
        # Hold mappings for as long as the views exist, including memoryview consumers.
        import weakref

        weakref.finalize(pixels, owner.close)
        if mask_owner:
            weakref.finalize(mask, mask_owner.close)
        return result


class ZarrRaster(Raster):
    def __init__(self, path):
        import zarr

        self.path = str(path)
        group = zarr.open_group(self.path, mode="r")
        self.grid = Grid(**group.attrs["autostacgis:grid"])

    def read(self, window):
        import zarr

        self.validate_window(window)
        group = zarr.open_group(self.path, mode="r")
        region = (..., slice(window.y, window.y + window.height), slice(window.x, window.x + window.width))
        return Block(
            np.asarray(group["pixels"][region]),
            window,
            np.asarray(group["valid"][region]) if "valid" in group else None,
        )


class WarpedRaster(Raster):
    def __init__(self, path, grid, resampling="bilinear"):
        self.path, self.grid, self.resampling = str(path), grid, resampling

    def read(self, window):
        import rasterio
        from affine import Affine
        from rasterio.enums import Resampling
        from rasterio.vrt import WarpedVRT
        from rasterio.windows import Window as RioWindow

        self.validate_window(window)
        with (
            rasterio.open(self.path) as source,
            WarpedVRT(
                source,
                crs=self.grid.crs,
                transform=Affine(*self.grid.transform),
                width=self.grid.width,
                height=self.grid.height,
                dtype=self.grid.dtype,
                nodata=self.grid.nodata,
                resampling=Resampling[self.resampling],
            ) as warped,
        ):
            region = RioWindow(window.x, window.y, window.width, window.height)
            return Block(warped.read(window=region), window, warped.read_masks(window=region) > 0)


def physical_raster(record):
    """Lazy native decoding of the standard per-band scale and offset."""
    raster = record.payload
    asset = record.item["assets"][record.runtime.get("selected_asset", "data")]
    bands = asset.get("bands", [])
    scales = [
        bands[i].get("raster:scale", asset.get("raster:scale", 1))
        if i < len(bands)
        else asset.get("raster:scale", 1)
        for i in range(raster.grid.count)
    ]
    offsets = [
        bands[i].get("raster:offset", asset.get("raster:offset", 0))
        if i < len(bands)
        else asset.get("raster:offset", 0)
        for i in range(raster.grid.count)
    ]
    if all(value == 1 for value in scales) and all(value == 0 for value in offsets):
        return raster
    return TransformRaster(
        [raster],
        replace(raster.grid, dtype="float64", nodata=float("nan")),
        _decode_pixels,
        {"scales": scales, "offsets": offsets},
    )


def _decode_pixels(blocks, window, settings):
    block = blocks[0]
    values = (
        block.pixels.astype("float64") * np.asarray(settings["scales"])[:, None, None]
        + np.asarray(settings["offsets"])[:, None, None]
    )
    return Block(values, window, block.valid)


def nodata_raster(raster, *, nodata, dtype="float64"):
    """Encode invalid/nonfinite pixels with a finite nodata value for file-only consumers."""
    return TransformRaster(
        [raster],
        replace(raster.grid, dtype=dtype, nodata=nodata),
        _encode_nodata,
        {"dtype": dtype, "nodata": nodata},
    )


def _encode_nodata(blocks, window, settings):
    block = blocks[0]
    values = block.pixels.astype(settings["dtype"], copy=False)
    sentinel = settings["nodata"]
    valid = np.isfinite(values)
    if block.valid is not None:
        valid &= block.valid
    if np.any(valid & (values == sentinel)):
        raise AutoSTACGISError("Valid pixels collide with the adapter nodata sentinel")
    return Block(np.where(valid, values, sentinel), window, valid)
