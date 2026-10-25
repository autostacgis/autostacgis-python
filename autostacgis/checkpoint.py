"""Checkpoint protocol and resumable output events for ordinary processing generators."""

from dataclasses import dataclass


@dataclass
class Checkpoint:
    name: str
    outputs: dict


@dataclass
class CheckpointReference:
    reference: str
    outputs: dict


def identity(function, step, inputs, *, outputs=None):
    """Content-sensitive identity: implementation, settings, metadata and payload bytes."""
    import hashlib

    from .artifacts import artifact
    from .model import digest
    from .runtime.storage import implementation_identity

    code = implementation_identity(function)
    if code is None:
        return None
    values = []
    for port, group in inputs.items():
        for record in group:
            value = artifact(record.payload)
            checksum = hashlib.sha256()
            if value.type == "path":
                from pathlib import Path

                value = type(value)("path", Path(value.value))
            if value.type == "raster":
                for block in value.value.blocks():
                    checksum.update(block.pixels.tobytes())
                    if block.valid is not None:
                        checksum.update(block.valid.tobytes())
            elif value.type == "batches":
                for batch in value.value.batches():
                    checksum.update(batch.serialize().to_pybytes())
            elif value.type == "json":
                checksum.update(digest(value.value).encode())
            else:
                if value.type == "path" and value.value.is_dir():
                    for path in sorted(value.value.rglob("*")):
                        if path.is_file():
                            checksum.update(str(path.relative_to(value.value)).encode())
                            with path.open("rb") as stream:
                                checksum.update(hashlib.file_digest(stream, "sha256").digest())
                else:
                    for chunk in value.chunks():
                        checksum.update(chunk)
            values.append([port, record.kind, record.item, checksum.hexdigest()])
    naming = {
        port: {key: policy.get(key) for key in ("filename_suffixes", "output_directory")}
        for port, policy in (outputs or {}).items()
    }
    return digest([step.module, step.settings, code, values, naming])
