from ..runtime.executors import ScopedExecutor, _depth, _invoke


class Dask(ScopedExecutor):
    resource_group = "executor"
    settings_schema = {
        "type": "object",
        "properties": {
            "address": {"type": "string"},
            "scheduler_file": {"type": "string"},
            "workers": {"type": "integer", "minimum": 1},
        },
        "oneOf": [{"required": ["address"]}, {"required": ["scheduler_file"]}],
        "additionalProperties": False,
    }

    def __init__(self, settings, services=None):
        self.services = services
        self.settings = settings
        self.workers = settings.get("workers", 1)
        self.executor = self
        self.jobs = []

    def __enter__(self):
        from distributed import Client

        self.client = (
            Client(scheduler_file=self.settings["scheduler_file"])
            if "scheduler_file" in self.settings
            else Client(self.settings["address"])
        )
        return self

    def submit(self, function, /, *args, **kwargs):
        if _depth.get():
            return self.services.get("sequential").submit(function, *args, **kwargs)
        future = self.client.submit(_invoke, function, args, kwargs, pure=False)
        self.jobs.append(future)
        return future

    def __exit__(self, *args):
        self.client.cancel(self.jobs)
        self.client.close()


ID = "dask"
REQUIRES = ("sequential",)
IMPLEMENTATIONS = {"services": Dask}
