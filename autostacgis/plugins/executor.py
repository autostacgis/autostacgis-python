"""Select the shared execution backend without creating another worker pool."""

from ..errors import AutoSTACGISError


def executor(settings, services):
    backend = settings.get("backend", "threads")
    if backend == ID:
        raise AutoSTACGISError("The executor cannot select itself as its backend")
    return services.get(backend, settings={key: value for key, value in settings.items() if key != "backend"})


executor.settings_schema = {
    "type": "object",
    "properties": {
        "backend": {"type": "string"},
        "workers": {"type": "integer", "minimum": 1},
        "address": {"type": "string"},
        "scheduler_file": {"type": "string"},
    },
    "additionalProperties": False,
}

ID = "executor"
REQUIRES = ("threads", "multiprocessing", "sequential", "dask")
IMPLEMENTATIONS = {"services": executor}
DEFAULTS = {"services": {"backend": "threads", "workers": 4}}
RUNTIME_DEFAULT = True
