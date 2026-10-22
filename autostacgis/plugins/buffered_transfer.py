"""Bounded transfer for native windows, Arrow batches, bytes, paths, and JSON."""

import json
import shutil
from pathlib import Path
from threading import RLock
from uuid import uuid4

from ..artifacts import Artifact, ByteChunks, FileChunks, accepted
from ..data import ArrayRaster, Batches, FileBatches, FileRaster, ZarrRaster
from ..errors import AutoSTACGISError


class BufferedTransfer:
    input_types = ["raster", "batches", "bytes", "byte-stream", "path", "json"]
    output_types = input_types
    settings_schema = {
        "type": "object",
        "properties": {"memory_bytes": {"type": "integer", "minimum": 0}},
        "additionalProperties": False,
    }

    def __init__(self, settings, context):
        self.limit = settings.get("memory_bytes", 512 * 1024 * 1024)
        self.used = 0
        self.root = Path(getattr(context, "directory", context.work)) / "transfer" / uuid4().hex
        self.retain_files = getattr(context, "retain_files", False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.allocations = []
        self.managed = set()

    @staticmethod
    def worker_settings(settings):
        # The parent owns the shared exchange budget. Nested worker exchanges
        # spool to parent-owned scratch rather than allocating another full budget.
        return {**settings, "memory_bytes": 0}

    @staticmethod
    def can_transfer(source_types, target_types):
        conversions = {
            "raster": {"raster", "path"},
            "batches": {"batches", "path"},
            "bytes": {"bytes", "byte-stream", "path"},
            "byte-stream": {"bytes", "byte-stream", "path"},
            "path": {"path", "bytes", "byte-stream", "raster", "batches"},
            "json": {"json", "path"},
        }
        return any(conversions.get(type_, {type_}) & set(target_types) for type_ in source_types)

    def transfer(self, value, types, *, mode="optimized", shared=False):
        accepted(value, self.input_types, owner="buffered-transfer")
        # Native file wrappers retain their exchange type and can be read in windows.
        with self.lock:
            if value.type == "path" and "path" not in types:
                if "raster" in types:
                    location = str(value.value)
                    payload = ZarrRaster(location) if location.endswith(".zarr") else FileRaster(location)
                    value = Artifact("raster", payload, kind="raster")
                elif "batches" in types:
                    value = Artifact(
                        "batches", FileBatches(value.value, kind=value.kind or "table"), kind=value.kind
                    )
            if value.type == "raster":
                result = self._raster(value, "file" if "raster" not in types else mode, shared)
                if "raster" in types:
                    return result
                if "path" in types:
                    return Artifact("path", Path(result.value.path))
                raise AutoSTACGISError(f"No raster representation accepted by receiver: {types}")
            if value.type == "batches":
                result = self._batches(value, "file" if "batches" not in types else mode)
                if "batches" in types:
                    return result
                if "path" in types:
                    return Artifact("path", Path(result.value.path))
                raise AutoSTACGISError(f"No batch representation accepted by receiver: {types}")
            if value.type == "json":
                size = len(json.dumps(value.value).encode())
                if mode == "file" or (mode == "optimized" and self.used + size > self.limit):
                    if "path" not in types:
                        raise AutoSTACGISError("json file transfer requires a receiving path type")
                    path = self.root / f"{uuid4().hex}.json"
                    path.write_text(json.dumps(value.value))
                    return Artifact("path", path, "application/json")
                if mode == "optimized" and self.used + size > self.limit:
                    raise AutoSTACGISError("JSON exceeds transfer memory budget; accept path or use a byte-stream")
                self.used += size
                return value
            return self._bytes(value, types, mode)

    def _raster(self, value, mode, shared):
        import numpy as np

        from .storage import Storage

        raster = value.value
        if mode != "file" and id(raster) in self.managed and not shared:
            return value
        if mode != "memory" and isinstance(raster, (FileRaster, ZarrRaster)):
            return value
        size = int(np.prod(raster.grid.shape)) * (np.dtype(raster.grid.dtype).itemsize + 1)
        memory = mode == "memory" or (mode == "optimized" and self.used + size <= self.limit)
        # Reuse the native windowed materializer; no whole-image read is required for spilling.
        from types import SimpleNamespace

        storage = Storage(
            {"memory_bytes": max(1, size) if memory else 1}, SimpleNamespace(directory=self.root)
        )
        storage.directory = self.root
        payload = (
            (
                raster
                if isinstance(raster, ArrayRaster) and not shared
                else storage.materialize(raster, shared=shared)
            )
            if memory
            else None
        )
        if not memory:
            from ..data.writers import write_raster

            path = self.root / f"{uuid4().hex}.tif"
            write_raster(raster, path)
            payload = FileRaster(path)
        else:
            self.used += size
            self.managed.add(id(payload))
            self.allocations.extend(storage.allocations)
        return Artifact("raster", payload)

    def _batches(self, value, mode):
        if mode != "memory" and isinstance(value.value, FileBatches):
            return value
        from ..data.writers import write_parquet

        source = value.value
        if mode != "file" and id(source) in self.managed:
            return value
        # Arrow batches are replayable. Bound buffering while estimating a streaming source.
        buffered, size = [], 0
        for batch in source.batches():
            size += batch.nbytes
            if mode == "file" or (mode == "optimized" and self.used + size > self.limit):
                path = self.root / f"{uuid4().hex}.parquet"
                write_parquet(source, path)
                return Artifact("batches", FileBatches(path, kind=source.kind, crs=source.crs))
            buffered.append(batch)
        self.used += size
        payload = Batches(buffered, kind=source.kind, schema=source.schema, crs=source.crs)
        self.managed.add(id(payload))
        return Artifact("batches", payload)

    def _bytes(self, value, types, mode):
        if mode == "memory" and not ({"bytes", "byte-stream"} & set(types)):
            raise AutoSTACGISError("memory transfer requires a receiving bytes or byte-stream type")
        if value.type == "path" and mode != "memory" and "path" in types:
            return value
        if value.type == "path" and Path(value.value).is_dir():
            raise AutoSTACGISError("Directory paths cannot be transferred as bytes")
        path = self.root / uuid4().hex
        buffered, size, stream = [], 0, None
        try:
            for chunk in value.chunks():
                size += len(chunk)
                if stream is None and (
                    mode == "file" or (mode == "optimized" and self.used + size > self.limit)
                ):
                    stream = path.open("wb")
                    for part in buffered:
                        stream.write(part)
                    buffered.clear()
                if stream is None:
                    buffered.append(chunk)
                else:
                    stream.write(chunk)
        finally:
            if stream is not None:
                stream.close()
        if stream is not None:
            if "path" in types:
                return Artifact("path", path, value.media_type)
            if "byte-stream" in types:
                return Artifact("byte-stream", FileChunks(path), value.media_type)
            raise AutoSTACGISError("Transfer exceeds memory budget; receiver must accept path or byte-stream")
        data = b"".join(buffered)
        self.used += size
        if "bytes" in types:
            return Artifact("bytes", data, value.media_type)
        if "byte-stream" in types:
            return Artifact("byte-stream", ByteChunks(data), value.media_type)
        if "path" in types:
            path.write_bytes(data)
            return Artifact("path", path, value.media_type)
        raise AutoSTACGISError(f"No transfer representation supported by receiver: {types}")

    def close(self):
        for allocation in self.allocations:
            allocation.close()
            allocation.unlink()
        if not self.retain_files:
            shutil.rmtree(self.root, ignore_errors=True)


ID = "buffered-transfer"
REQUIRES = ()
IMPLEMENTATIONS = {"data_transfer": BufferedTransfer}
DEFAULTS = {"data_transfer": {"memory_bytes": 536870912}}
