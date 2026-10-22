"""A destination for typed bytes, replayable byte streams, and file/directory paths."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

from ..artifacts import Artifact, accepted
from ..errors import AutoSTACGISError


def filesystem(value, settings, context):
    value = accepted(value, filesystem.input_types, owner="filesystem")
    suffix = Path(str(value.value)).suffix if value.type == "path" else ".bin"
    destination = settings.get(
        "path", str(Path(settings.get("directory", "output/data")) / uuid4().hex / ("data" + suffix))
    )
    if destination.startswith("path:"):
        destination = destination[5:]
    from urllib.parse import urlsplit

    from ..io import local

    if urlsplit(destination).scheme not in ("", "file"):
        raise AutoSTACGISError("filesystem output requires a local path")
    target = context.path(local(destination))
    target.parent.mkdir(parents=True, exist_ok=True)
    source = local(str(value.value)).resolve() if value.type == "path" else None
    if value.type == "path" and urlsplit(str(value.value)).scheme not in ("", "file"):
        raise AutoSTACGISError("filesystem input paths must be local; acquire remote assets first")
    if source == target:
        return Artifact("path", target, value.media_type)
    if target.exists() and not settings.get("overwrite", False):
        raise AutoSTACGISError(f"Output already exists: {target}")
    temporary = target.with_name("." + target.name + "." + uuid4().hex)
    try:
        if source is not None and source.is_dir():
            shutil.copytree(source, temporary)
        else:
            with temporary.open("wb") as stream:
                for chunk in value.chunks():
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        # Directory replacement is deliberately refused; it cannot be atomic on all filesystems.
        if target.is_dir():
            raise AutoSTACGISError(f"Output directory already exists: {target}")
        if settings.get("overwrite", False):
            temporary.replace(target)
        elif temporary.is_dir():
            temporary.rename(target)
        else:
            os.link(temporary, target)  # Atomic create, including concurrent writers.
    finally:
        if temporary.is_dir():
            shutil.rmtree(temporary)
        else:
            temporary.unlink(missing_ok=True)
    return Artifact("path", target, value.media_type)


filesystem.input_types = ["bytes", "byte-stream", "path"]
filesystem.output_types = ["path"]
filesystem.settings_schema = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "directory": {"type": "string"},
        "overwrite": {"type": "boolean"},
    },
    "additionalProperties": False,
}
ID = "filesystem"
REQUIRES = ()
IMPLEMENTATIONS = {"data_out": filesystem}
