"""Durable checkpoint manifests with pluggable encoding, storage and validation."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

from ..artifacts import Artifact, FileChunks, artifact
from ..errors import AutoSTACGISError
from ..io import atomic_json
from ..metadata import MISSING, get
from ..model import Record, digest


class FileCheckpoint:
    settings_schema = {
        "type": "object",
        "properties": {
            "directory": {"type": "string"},
            "relative_to": {"enum": ["config", "cwd", "item"]},
            "item_path": {"type": "string"},
            "encoders": {"type": "object", "additionalProperties": {"type": "string"}},
            "data_out": {"type": "string"},
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "plugin": {"type": "string"},
                        "settings": {"type": "object"},
                        "inputs": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                    "required": ["plugin"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, settings, context):
        self.settings, self.context = settings, context

    def directory(self, inputs):
        settings = self.settings
        directory = Path(settings.get("directory", "work/checkpoints"))
        if directory.is_absolute():
            return directory.resolve()
        base = settings.get("relative_to", "config")
        if base == "config":
            root = self.context.base_dir
        elif base == "cwd":
            root = Path.cwd()
        elif base == "item":
            roots = {
                get(r.item, settings.get("item_path", "properties.data_state:directory_root"))
                for records in inputs.values()
                for r in records
            }
            if len(roots) != 1 or next(iter(roots)) in (MISSING, None):
                raise AutoSTACGISError("Checkpoint item location must be one shared, known local directory")
            from urllib.parse import urlsplit

            from ..io import local

            location = str(next(iter(roots)))
            if urlsplit(location).scheme not in ("", "file"):
                raise AutoSTACGISError("file-checkpoint needs a local item directory")
            root = self.context.path(local(location))
        else:
            raise AutoSTACGISError(f"Unknown checkpoint relative_to: {base}")
        return (root / directory).resolve()

    def load(self, key, inputs):
        self.validate_key(key)
        directory = self.directory(inputs) / key
        manifest = directory / "manifest.json"
        if not manifest.exists():
            return None
        try:
            values = json.loads(manifest.read_text())
            if values["key"] != key:
                raise ValueError("Checkpoint identity mismatch")
            outputs = {}

            for port, records in values["outputs"].items():
                outputs[port] = []
                for entry in records:
                    path = Path(entry["path"])
                    if not path.resolve().is_relative_to(directory.resolve()) or not path.exists():
                        raise ValueError("Missing checkpoint asset")
                    # Reject corrupt bytes even if metadata/header checks pass.
                    if self.checksum(path) != entry["checksum"]:
                        raise ValueError("Checkpoint checksum mismatch")
                    kind, type_ = entry["kind"], entry["type"]
                    record = Record(
                        entry["item"], kind, runtime=entry.get("runtime", {}), payload=Artifact("path", path)
                    )
                    encoder = entry.get("encoder")
                    if encoder:
                        definition, options = self.context._app.resolve_call("encode", {"plugin": encoder})
                        function = self.context._app.registry.resolve("encode", definition["implementation"])
                        record.payload = function.restore(
                            Artifact("path", path), record, {**options, "crs": entry.get("crs")}
                        )
                    elif type_ == "json":
                        record.payload = Artifact("json", json.loads(path.read_text()))
                    elif type_ == "bytes":
                        record.payload = Artifact("bytes", path.read_bytes())
                    elif type_ == "byte-stream":
                        record.payload = Artifact("byte-stream", FileChunks(path))
                    elif type_ != "path":
                        raise ValueError("Checkpoint needs its original encoder to restore this type")
                    outputs[port].append(record)
            for check in self.settings.get("checks", []):
                if check.get("inputs"):
                    if any(port not in outputs for port in check["inputs"].values()):
                        raise AutoSTACGISError("Checkpoint check refers to an unknown output port")
                    mapped = {name: outputs[port] for name, port in check["inputs"].items()}
                else:
                    mapped = {"data": [r for group in outputs.values() for r in group]}
                result = self.context.call(check["plugin"], "checks", mapped, check.get("settings", {}))
                if result != "pass":
                    raise ValueError("Checkpoint check failed or unknown")
            for records in outputs.values():
                for record in records:
                    record.runtime["checkpoint"] = str(manifest)
            return outputs
        except (OSError, ValueError, KeyError, TypeError):
            # Only remove this provider's key directory; never follow manifest paths when deleting.
            if directory.is_symlink():
                directory.unlink()
            else:
                shutil.rmtree(directory, ignore_errors=True)
            return None

    @staticmethod
    def checksum(path):
        import hashlib

        checksum = hashlib.sha256()
        for child in sorted(path.rglob("*")) if path.is_dir() else [path]:
            if child.is_file():
                if path.is_dir():
                    checksum.update(str(child.relative_to(path)).encode())
                with child.open("rb") as stream:
                    checksum.update(hashlib.file_digest(stream, "sha256").digest())
        return checksum.hexdigest()

    @staticmethod
    def validate_key(key):
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise AutoSTACGISError("Checkpoint key must be a single safe directory name")

    def save(self, key, outputs, inputs):
        from ..publication import publish

        self.validate_key(key)
        root = self.directory(inputs)
        root.mkdir(parents=True, exist_ok=True)
        target = root / key
        temporary = root / ("." + key + "-" + uuid4().hex)
        temporary.mkdir()
        manifest = {"key": key, "outputs": {}}
        live = {}
        try:
            for port, records in outputs.items():
                manifest["outputs"][port] = []
                live[port] = []
                for i, record in enumerate(records):
                    typed = artifact(record.payload)
                    encoder = self.settings.get("encoders", {}).get(typed.type)
                    suffix = ".json" if typed.type == "json" else ".bin"
                    if encoder:
                        definition, _ = self.context._app.resolve_call("encode", {"plugin": encoder})
                        function = self.context._app.registry.resolve("encode", definition["implementation"])
                        if not hasattr(function, "restore"):
                            raise AutoSTACGISError(f"Checkpoint encoder {encoder} must implement restore")
                        suffix = getattr(function, "extension", ".bin")
                    name = digest([port, i])[:24] + suffix
                    path = temporary / name
                    policy = {
                        "data_out": {
                            "plugin": self.settings.get("data_out", "filesystem"),
                            "settings": {"path": str(path)},
                        }
                    }
                    if encoder:
                        policy["encode"] = {"plugin": encoder}
                    saved_record = record.clone()
                    if typed.type == "json":
                        saved_record.payload = Artifact("bytes", json.dumps(typed.value).encode())
                    saved_record = publish(self.context._app, saved_record, policy)
                    saved_record.item["assets"][saved_record.runtime.get("selected_asset", "data")][
                        "href"
                    ] = str(target / name)
                    self.context.stac.extensions(saved_record.item)
                    # The temporary physical file embeds the final durable href.
                    hook = saved_record.runtime.get("format_metadata_out")
                    if hook:
                        self.context._app.invoke(
                            hook["plugin"], "metadata_out", saved_record, hook.get("settings", {})
                        )
                    saved_record.runtime.setdefault("local_assets", {})[
                        saved_record.runtime.get("selected_asset", "data")
                    ] = str(target / name)
                    saved_record.runtime["checkpoint"] = str(target / "manifest.json")
                    saved_record.payload = record.payload
                    live[port].append(saved_record)
                    manifest["outputs"][port].append(
                        {
                            "path": str(target / name),
                            "item": saved_record.item,
                            "kind": record.kind,
                            "type": typed.type,
                            "encoder": encoder,
                            "checksum": self.checksum(path),
                            "crs": getattr(record.payload, "crs", None),
                            "runtime": {
                                k: v
                                for k, v in record.runtime.items()
                                if k in ("selected_asset", "source_id")
                            },
                        }
                    )
            atomic_json(temporary / "manifest.json", manifest)
            try:
                temporary.rename(target)
            except OSError:
                if not (target / "manifest.json").exists():
                    raise
            from ..checkpoint import CheckpointReference

            return CheckpointReference(str(target / "manifest.json"), live)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


ID = "file-checkpoint"
REQUIRES = ("filesystem", "geotiff", "parquet", "valid-data")
IMPLEMENTATIONS = {"checkpoint": FileCheckpoint}
DEFAULTS = {
    "checkpoint": {
        "directory": "work/checkpoints",
        "relative_to": "config",
        "encoders": {"raster": "geotiff", "batches": "parquet"},
        "checks": [{"plugin": "valid-data"}],
    }
}
