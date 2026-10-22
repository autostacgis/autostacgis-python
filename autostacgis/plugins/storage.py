import shutil
from multiprocessing import shared_memory
from pathlib import Path
from uuid import uuid4

import numpy as np

from ..data import ArrayRaster, FileRaster, SharedRaster
from ..errors import AutoSTACGISError


class Storage:
    settings_schema = {
        "type": "object",
        "properties": {
            "memory_bytes": {"type": "integer", "minimum": 1},
            "tile_size": {"type": "integer", "minimum": 1},
            "keep_scratch": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    def __init__(self, settings, services):
        from threading import Lock

        self.limit = settings.get("memory_bytes", 512 * 1024 * 1024)
        self.tile_size = settings.get("tile_size", 512)
        self.keep = settings.get("keep_scratch", False)
        self.directory = Path(services.directory) / "scratch"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.used = 0
        self.allocations = []
        self.lock = Lock()

    def materialize(self, raster, *, shared=False):
        size = int(np.prod(raster.grid.shape)) * (np.dtype(raster.grid.dtype).itemsize + 1)
        with self.lock:
            memory = self.used + size <= self.limit
            if memory:
                self.used += size
        if not memory:
            from ..data.writers import write_raster

            path = self.directory / f"{uuid4().hex}.tif"
            write_raster(raster, path, tile_size=self.tile_size)
            return FileRaster(path)
        if shared:
            pixels_owner = shared_memory.SharedMemory(
                create=True, size=int(np.prod(raster.grid.shape)) * np.dtype(raster.grid.dtype).itemsize
            )
            self.allocations.append(pixels_owner)
            mask_owner = shared_memory.SharedMemory(create=True, size=int(np.prod(raster.grid.shape)))
            self.allocations.append(mask_owner)
            pixels = np.ndarray(raster.grid.shape, dtype=raster.grid.dtype, buffer=pixels_owner.buf)
            valid = np.ndarray(raster.grid.shape, dtype=bool, buffer=mask_owner.buf)
        else:
            pixels = np.empty(raster.grid.shape, dtype=raster.grid.dtype)
            valid = np.empty(raster.grid.shape, dtype=bool)
        for block in raster.blocks(tile_size=self.tile_size):
            w = block.window
            region = (..., slice(w.y, w.y + w.height), slice(w.x, w.x + w.width))
            pixels[region] = block.pixels
            valid[region] = True if block.valid is None else block.valid
        if shared:
            return SharedRaster(pixels_owner.name, raster.grid, mask_owner.name)
        return ArrayRaster(pixels, raster.grid, valid)

    def path(self, data, *, reason):
        if not reason:
            raise AutoSTACGISError("A file-only adapter must declare its materialization reason")
        if isinstance(data, FileRaster):
            return data.path
        from ..data.writers import write_raster

        path = self.directory / f"{uuid4().hex}.tif"
        write_raster(data, path, tile_size=self.tile_size)
        return str(path)

    def close(self):
        for allocation in self.allocations:
            allocation.close()
            allocation.unlink()
        if not self.keep:
            shutil.rmtree(self.directory, ignore_errors=True)


ID = "storage"
REQUIRES = ()
IMPLEMENTATIONS = {"services": Storage}

RUNTIME_DEFAULT = True

DEFAULTS = {"services": {"memory_bytes": 536870912, "tile_size": 512}}
