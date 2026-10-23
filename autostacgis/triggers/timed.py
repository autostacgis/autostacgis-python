"""APScheduler 3.x triggers with native scheduler, trigger, and job options."""

from .common import ManagedTrigger, execute, launch


class TimedTrigger(ManagedTrigger):
    def __init__(self, app, settings, request=None, *, runner=None):
        from apscheduler.schedulers.background import BackgroundScheduler

        super().__init__()
        self.scheduler = BackgroundScheduler(**settings.get("scheduler", {}))
        self.scheduler.add_job(
            runner or execute,
            settings.get("trigger", "interval"),
            args=[app.snapshot(), str(app.base_dir), app.request(request or settings.get("request"))],
            **settings.get(
                "trigger_args", {"minutes": 15} if settings.get("trigger", "interval") == "interval" else {}
            ),
            **{"max_instances": 1, "coalesce": True, **settings.get("job", {})},
        )
        self.start_options = settings.get("start", {})

    def start(self):
        self.scheduler.start(**self.start_options)
        return self

    def stop(self):
        self.stopped.set()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    def join(self, timeout=None):
        return self.stopped.wait(timeout)


def timed_trigger(app, settings, request=None):
    return launch(TimedTrigger(app, settings, request), settings.get("background", False))


timed_trigger.settings_schema = {
    "type": "object",
    "properties": {
        "scheduler": {"type": "object", "description": "Raw BackgroundScheduler keyword arguments."},
        "trigger": {"type": "string", "description": "APScheduler trigger alias: date, interval, cron, etc."},
        "trigger_args": {"type": "object", "description": "Raw arguments for the selected trigger."},
        "job": {"type": "object", "description": "Raw add_job options, excluding func, args and trigger."},
        "start": {"type": "object", "description": "Raw scheduler.start arguments, including paused."},
        "background": {"type": "boolean", "default": False},
        "request": {"type": "object"},
    },
    "additionalProperties": False,
}
