# Dependencies and execution

A plugin declares dependencies in `REQUIRES`. `context.call(name, capability, value, settings)` and `context.service(name)` enforce scoped access. A dependency's own dependencies are not automatically available to its caller. Explicit `{plugin: ...}` references in an invocation's configured settings also authorize calls to those selected plugins for that invocation; this allows a checkpoint to use a configured check or a metadata writer to use a configured destination. Cycles and missing plugins are errors. Requests may explicitly replace a module's sequence policy with another installed sequence plugin.

The executor schedules a DAG from data dependencies. An operation waits for all named inputs. Independent operations may execute concurrently. Multi-output operations execute once even when several outputs are needed. Initial and predicted data share one object type; a new version does not overwrite its source.

Runtime services supply shared sequential, thread, process, and Dask executors, storage, staging, events and telemetry. `runtime.executor` selects the global backend and worker budget. Nested work uses scoped executors to avoid creating unbounded pools or deadlocking. The execution extension can set `preferred_workers`, `tile_size`, `memory_bytes`, and logging fields for an operation; it declares dependencies on the shared executor and storage.

Raster payloads expose native arrays through replayable windows. Vector, table, and point-cloud payloads expose Arrow batches. Python schedules work; native libraries implement pixel and geometry operations. Read-only buffer ownership is explicit. Process transport uses shared memory where appropriate; storage spills or creates seekable files when an algorithm requires them. GDAL, Py6S, spectralmatch, and OmniCloudMask integrations remain in their own processing modules.

Checkpoint providers reuse validated results when content checksums, implementation code/dependency identity, metadata, and settings agree. Output policies materialize durable results on request. Keep the returned `RunResult` open while consuming lazy payloads; closing it releases owned workers and scratch storage.

Search bounds are separate from worker settings. `max_steps` bounds workflow length, while `max_states`, `max_bindings`, and `timeout` bound synthesis resources. A resource exhaustion raises `SearchLimit`. Failure after complete bounded enumeration raises `NoPlan`, explicitly mentioning the step bound. Neither means an unbounded workflow has been proved impossible.

Typed transfer and checkpoints are capabilities with their own providers, not runtime
cache services. See [data flow](data-flow.md).
