import json
import logging
from pathlib import Path
from threading import Lock
from time import time


class Events:
    settings_schema = {
        "type": "object",
        "properties": {
            "level": {"enum": ["DEBUG", "INFO", "WARNING", "ERROR"]},
            "path": {"type": "string"},
            "console": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    def __init__(self, settings, services):
        self.path = Path(settings.get("path", str(services.directory / "events.jsonl")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.callback = services.events
        self.lock = Lock()
        self.logger = logging.getLogger("autostacgis." + str(id(self)))
        self.logger.setLevel(settings.get("level", "INFO"))
        self.logger.propagate = False
        if settings.get("console"):
            self.logger.addHandler(logging.StreamHandler())

    def emit(self, event, **fields):
        value = {"event": event, "time": time(), **fields}
        with self.lock, self.path.open("a") as stream:
            stream.write(json.dumps(value, allow_nan=False) + "\n")
        if self.callback:
            self.callback(value)
        self.logger.info("%s %s", event, fields)

    def close(self):
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()


ID = "events"
REQUIRES = ()
IMPLEMENTATIONS = {"services": Events}

RUNTIME_DEFAULT = True
