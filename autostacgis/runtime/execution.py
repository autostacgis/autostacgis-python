class StorageScope:
    def __init__(self, storage, settings):
        self.storage = storage
        self.limit = min(storage.limit, settings.get("memory_bytes", storage.limit))
        self.tile_size = settings.get("tile_size", storage.tile_size)
        self.directory = storage.directory

    def materialize(self, raster, *, shared=False):
        import numpy as np

        if int(np.prod(raster.grid.shape)) * (np.dtype(raster.grid.dtype).itemsize + 1) > self.limit:
            from ..data import FileRaster

            return FileRaster(self.path(raster, reason="Per-step memory limit"))
        return self.storage.materialize(raster, shared=shared)

    def path(self, data, *, reason):
        return self.storage.path(data, reason=reason)
