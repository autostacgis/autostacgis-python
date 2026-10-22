"""Core routing for every processing edge; providers own buffering and conversion."""

from .artifacts import accepted, artifact
from .errors import AutoSTACGISError


class Transfers:
    def __init__(self, app, call, directory, *, retain_files=False):
        self.app = app
        definition, settings = app.resolve_call("data_transfer", call)
        self.name = definition["id"]
        context = app.scope(self.name, settings)
        context.directory = directory
        context.retain_files = retain_files
        self.provider = app.registry.resolve("data_transfer", definition["implementation"])(settings, context)
        self.directory = directory
        self.settings = settings
        self.cache = {}

    def record(self, record, declaration=None, *, shared=False, produced=False):
        from .executor import attach_payload

        record = attach_payload(record.clone())
        declaration = declaration or {}
        types = declaration.get("data_types", [artifact(record.payload).type])
        if produced:
            type_ = artifact(record.payload).type
            # Function return types are checked before transfer. Native handles keep their
            # logical type; byte/JSON backing may change before the next consumer.
            representations = {
                "bytes": ["bytes", "byte-stream", "path"],
                "byte-stream": ["bytes", "byte-stream", "path"],
                "json": ["json", "path"],
            }.get(type_, [type_])
            types = [t for t in representations if t in self.provider.output_types]
        mode = declaration.get("transfer", "optimized")
        if isinstance(mode, dict):
            mode = mode.get("mode", "optimized")
        if mode not in ("memory", "file", "optimized"):
            raise AutoSTACGISError(f"Unknown transfer mode: {mode}")
        typed = accepted(record.payload, self.provider.input_types, owner=self.name)
        from dataclasses import replace

        typed = replace(typed, kind=record.kind)
        key = (record.key, id(record.payload), tuple(types), mode, shared)
        if key not in self.cache:
            result = self.provider.transfer(typed, types, mode=mode, shared=shared)
            accepted(result, types, owner="receiving port")
            self.cache[key] = result.value if result.type in ("raster", "batches") else result
        record.payload = self.cache[key]
        return record

    def value(self, value, types, *, mode="optimized"):
        typed = accepted(value, self.provider.input_types, owner=self.name)
        result = self.provider.transfer(typed, types, mode=mode)
        return accepted(result, types, owner="receiving plugin")

    def close(self):
        self.cache.clear()
        if hasattr(self.provider, "close"):
            self.provider.close()
