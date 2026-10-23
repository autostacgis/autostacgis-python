# Timed and file triggers

The Python library implements `TimedTrigger` (APScheduler 3.x) and `FileTrigger`
(watchdog 6.x), exposed by the standard `apscheduler` and `watchdog` trigger
plugins. The former `scheduler` registration is removed. Install
`autostacgis[triggers]` or `autostacgis-plugins[triggers]`.

```yaml
catalog:
  version: 1.0.0
  plugins:
    - use: stac-source
      settings: {api_url: 'http://127.0.0.1:8080'}
    - use: executor
    - use: storage
    - use: events
    - use: apscheduler
      settings:
        scheduler:
          timezone: Pacific/Honolulu
          job_defaults: {max_instances: 1, coalesce: true}
        trigger: interval
        trigger_args: {minutes: 15, jitter: 30}
        job: {id: refresh-catalog, misfire_grace_time: 60}
        start: {paused: false}
request:
  sources: [stac-source]
  goal: {output: {}}
```

Run `autostacgis trigger config.yaml apscheduler`. `scheduler` is a raw mapping
of `BackgroundScheduler` keyword arguments, including timezone, executors,
jobstores, and job_defaults. `trigger` is an upstream alias (`date`, `interval`,
`cron`, or an installed APScheduler trigger); `trigger_args` is passed unchanged
to it. `job` passes `add_job` options such as id, name, replace_existing,
misfire_grace_time, coalesce, max_instances and executor. The library owns the
callback, args and trigger. `start` passes scheduler startup arguments. Optional
job stores and upstream executor integrations require their own dependencies.
The default is an interval of 15 minutes, one concurrent invocation, and coalescing.
Persistent jobs reference a serializable library callback with catalog, base
directory and request snapshots; installed plugin entry points must be available
when restored.

For file events, configure `watchdog` instead:

```yaml
- use: watchdog
  settings:
    backend: native
    watches:
      - {path: incoming, recursive: true}
    observer: {timeout: 1}
    handler:
      patterns: ['*.tif', '*.tiff']
      ignore_patterns: ['*.part', '*.tmp']
      ignore_directories: true
      case_sensitive: false
    events: [created, modified, moved, closed]
    debounce_seconds: 2
    request_path: query.trigger_event
    files_path: query.files
```

`observer` passes raw constructor options to the native Observer or, with
`backend: polling`, PollingObserver. `handler` passes raw
PatternMatchingEventHandler options. Each watch specifies path and recursive.
Events are filtered before processing; repeated events for the same destination
settle for `debounce_seconds`. A dedicated consumer runs workflows serially,
leaving filesystem notification callbacks responsive. Moved files use their
destination path. Errors are logged and exposed as `controller.last_error`;
the watcher continues receiving subsequent events.

The complete event is injected at `request_path`; `files_path` receives the changed
file path in an array. The local file providers honor `query.files` while also
applying their configured search glob, so each run processes the changed file.
Set `files_path: null` to rescan all eligible inputs. Other providers can use the
event/query in their own discovery implementation. The default events are
created, moved and closed. Modified events are opt-in because a file may still be
being written; use settling or atomic rename conventions appropriate to the source.

Both plugins block until stopped by default. In a native Python application,
set `background: true` and retain the returned controller:

```python
controller = application.trigger('watchdog')
try:
    # The application's event loop remains in control.
    ...
finally:
    controller.stop()
    controller.join()
```

Direct APIs are `TimedTrigger(application, settings, request=None, runner=None)`
and `FileTrigger(...)`; use them as context managers. `runner`, a keyword-only
callable receiving `(catalog, base_dir, request)`, lets a host integrate durable
publication. The PostgreSQL worker uses it to ingest each triggered run's outputs
back into the STAC catalog. Trigger jobs can be started from either client and
cancelled from Jobs. Scheduling coordinates invocations; numerical processing
inside each invocation continues using the library's shared execution service.
