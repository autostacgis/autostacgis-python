"""Watchdog triggers with filtering, per-path settling, and serialized invocations."""

import logging
from copy import deepcopy
from os import fsdecode
from threading import Condition, Thread
from time import monotonic

from ..errors import AutoSTACGISError
from ..metadata import assign
from .common import ManagedTrigger, execute, launch

LOG = logging.getLogger(__name__)


class FileTrigger(ManagedTrigger):
    def __init__(self, app, settings, *, runner=None):
        from watchdog.events import PatternMatchingEventHandler
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver

        super().__init__()
        self.configuration, self.base_dir = app.snapshot(), str(app.base_dir)
        self.event_path = settings.get("event_path", "execution.query.trigger_event")
        self.files_path = settings.get("files_path", "execution.query.files")
        self.runner = runner or execute
        self.events = settings.get("events", ["created", "moved", "closed"])
        self.debounce = settings.get("debounce_seconds", 1.0)
        self.pending = {}
        self.condition = Condition()
        self.last_error = None
        self.handler = PatternMatchingEventHandler(**settings.get("handler", {"ignore_directories": True}))
        self.handler.on_any_event = self.enqueue
        backend = settings.get("backend", "native")
        if backend not in ("native", "polling"):
            raise AutoSTACGISError("watchdog.backend must be native or polling")
        self.observer = (Observer if backend == "native" else PollingObserver)(**settings.get("observer", {}))
        for watch in settings["watches"]:
            options = deepcopy(watch)
            options["path"] = str(app.path(options["path"]))
            self.observer.schedule(self.handler, **options)
        self.worker = Thread(target=self._consume, name="autostacgis-file-triggers", daemon=True)

    def enqueue(self, event):
        if event.event_type not in self.events or self.stopped.is_set():
            return
        value = {
            "event_type": event.event_type,
            "src_path": fsdecode(event.src_path),
            "dest_path": fsdecode(getattr(event, "dest_path", "")),
            "is_directory": event.is_directory,
        }
        key = value["dest_path"] or value["src_path"]
        with self.condition:
            self.pending[key] = (monotonic() + self.debounce, value)
            self.condition.notify()

    def _consume(self):
        while not self.stopped.is_set():
            with self.condition:
                if not self.pending:
                    self.condition.wait(0.5)
                    continue
                key = min(self.pending, key=lambda name: self.pending[name][0])
                due, event = self.pending[key]
                delay = due - monotonic()
                if delay > 0:
                    self.condition.wait(delay)
                    continue
                del self.pending[key]
            configuration = deepcopy(self.configuration)
            if self.event_path:
                assign(configuration, self.event_path, event)
            if self.files_path:
                assign(configuration, self.files_path, [event["dest_path"] or event["src_path"]])
            try:
                self.runner(configuration, self.base_dir)
            except Exception as exc:
                self.last_error = exc
                LOG.exception("File-triggered workflow failed for %s", key)

    def start(self):
        self.observer.start()
        self.worker.start()
        return self

    def stop(self):
        self.stopped.set()
        self.observer.stop()
        with self.condition:
            self.pending.clear()
            self.condition.notify_all()

    def join(self, timeout=None):
        if self.observer.ident is not None:
            self.observer.join(timeout)
        if self.worker.ident is not None:
            self.worker.join(timeout)


def file_trigger(app, settings):
    return launch(FileTrigger(app, settings), settings.get("background", False))


file_trigger.settings_schema = {
    "type": "object",
    "properties": {
        "backend": {"enum": ["native", "polling"], "default": "native"},
        "watches": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        "observer": {"type": "object", "description": "Raw Observer or PollingObserver keyword arguments."},
        "handler": {"type": "object", "description": "Raw PatternMatchingEventHandler keyword arguments."},
        "events": {
            "type": "array",
            "items": {
                "enum": ["created", "modified", "moved", "deleted", "closed", "opened", "closed_no_write"]
            },
        },
        "debounce_seconds": {"type": "number", "minimum": 0, "default": 1},
        "event_path": {"type": ["string", "null"], "default": "execution.query.trigger_event"},
        "files_path": {"type": ["string", "null"], "default": "execution.query.files"},
        "background": {"type": "boolean", "default": False},
    },
    "required": ["watches"],
    "additionalProperties": False,
}
