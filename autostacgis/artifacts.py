"""Explicit exchange types, independent of scientific kind and storage destination."""

from dataclasses import dataclass
from pathlib import Path

from .errors import AutoSTACGISError

# Plugins may introduce namespaced types; compatibility is always exact.
TYPES = frozenset({"raster", "batches", "bytes", "byte-stream", "path", "json"})


@dataclass(frozen=True)
class Artifact:
    """Typed value. A byte-stream is a fresh iterator factory, not a consumed iterator."""

    type: str
    value: object
    media_type: str | None = None
    metadata_out: dict | None = None
    kind: str | None = None

    def __post_init__(self):
        if not isinstance(self.type, str) or not self.type:
            raise AutoSTACGISError("Artifact type must be a nonempty string")
        if self.type == "bytes" and not isinstance(self.value, bytes):
            raise AutoSTACGISError("bytes artifacts require bytes values")
        if self.type == "byte-stream" and not callable(self.value):
            raise AutoSTACGISError("byte-stream artifacts require a replayable iterator factory")
        if self.type == "path" and not isinstance(self.value, (str, Path)):
            raise AutoSTACGISError("path artifacts require a string or Path")
        if self.type == "json":
            from .config import json_value

            json_value(self.value)

    def chunks(self, chunk_size=1024 * 1024):
        if self.type == "bytes":
            yield self.value
        elif self.type == "byte-stream":
            for chunk in self.value():
                if not isinstance(chunk, bytes):
                    raise AutoSTACGISError("byte-stream chunks must be bytes")
                yield chunk
        elif self.type == "path":
            from .io import local

            with local(str(self.value)).open("rb") as stream:
                while chunk := stream.read(chunk_size):
                    yield chunk
        else:
            raise AutoSTACGISError(f"{self.type} is not a byte source")


@dataclass(frozen=True)
class ByteChunks:
    data: bytes

    def __call__(self):
        yield self.data


@dataclass(frozen=True)
class FileChunks:
    path: object

    def __call__(self):
        yield from Artifact("path", self.path).chunks()


def artifact(value):
    from .data import Batches, Raster

    if isinstance(value, Artifact):
        return value
    if isinstance(value, Raster):
        return Artifact("raster", value, kind=value.kind)
    if isinstance(value, Batches):
        return Artifact("batches", value, kind=value.kind)
    if isinstance(value, bytes):
        return Artifact("bytes", value)
    if isinstance(value, Path):
        return Artifact("path", value)
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return Artifact("json", value)
    raise AutoSTACGISError(f"Payload must declare its exchange type: {type(value).__name__}")


def accepted(value, types, *, owner):
    typed = artifact(value)
    if not types or typed.type not in types:
        raise AutoSTACGISError(f"{owner}: unsupported exchange type {typed.type}; accepts {types}")
    return typed
