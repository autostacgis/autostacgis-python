from contextlib import contextmanager

from ..runtime.execution import StorageScope


class ExecutionExtension:
    binding_settings_schema = {
        "type": "object",
        "properties": {
            "preferred_workers": {"type": "integer", "minimum": 1},
            "tile_size": {"type": "integer", "minimum": 1},
            "memory_bytes": {"type": "integer", "minimum": 1},
            "log_fields": {"type": "object"},
        },
        "additionalProperties": False,
    }

    @contextmanager
    def execute(self, context, settings):
        context._executor = context.executor.scope(preferred_workers=settings.get("preferred_workers"))
        context._storage = StorageScope(context.storage, settings)
        context.log_fields = settings.get("log_fields", {})
        try:
            yield
        finally:
            context._executor = context._storage = None


ID = "autostacgis.execution/v1"
REQUIRES = ("executor", "storage")
IMPLEMENTATIONS = {"extensions": ExecutionExtension}
