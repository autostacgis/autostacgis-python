import contextvars
from concurrent.futures import Future
from contextlib import nullcontext
from threading import Event

from ..errors import AutoSTACGISError

_depth = contextvars.ContextVar("autostacgis_execution_depth", default=0)


def _invoke(function, args, kwargs):
    token = _depth.set(_depth.get() + 1)
    try:
        return function(*args, **kwargs)
    finally:
        _depth.reset(token)


def _process_init():
    # Each process receives one CPU slot. Nested work is serial within that slot.
    import os

    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "GDAL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS",
    ):
        os.environ[name] = "1"
    from threadpoolctl import threadpool_limits

    global _native_limit
    _native_limit = threadpool_limits(limits=1)


class ScopedExecutor:
    def __init__(self, executor, workers):
        self.executor = executor
        self.workers = workers

    def submit(self, function, /, *args, **kwargs):
        return self.executor.submit(function, *args, **kwargs)

    def map(self, function, *iterables):
        # Bounded submission provides backpressure for large, lazy block streams.
        arguments = iter(zip(*iterables, strict=True))
        pending = []
        try:
            for _ in range(self.workers):
                try:
                    pending.append(self.submit(function, *next(arguments)))
                except StopIteration:
                    break
            while pending:
                future = pending.pop(0)
                yield future.result()
                try:
                    pending.append(self.submit(function, *next(arguments)))
                except StopIteration:
                    pass
        finally:
            for future in pending:
                future.cancel()

    def scope(self, *, preferred_workers=None):
        return ScopedExecutor(self.executor, min(self.workers, preferred_workers or self.workers))


class Pool(ScopedExecutor):
    settings_schema = {
        "type": "object",
        "properties": {"workers": {"type": "integer", "minimum": 1}},
        "additionalProperties": False,
    }
    limit_native_threads = False

    def __init__(self, settings=None, services=None):
        settings = settings or {}
        self.workers = settings.get("workers", 1)
        self.executor = self
        self.cancelled = Event()
        self.pool = None

    def create_pool(self):
        return None

    def __enter__(self):
        self.pool = self.create_pool()
        # BLAS limits are process-wide: set them once, never race per-task settings.
        from threadpoolctl import threadpool_limits

        self.limit = threadpool_limits(limits=1) if self.limit_native_threads else nullcontext()
        self.limit.__enter__()
        return self

    def __exit__(self, kind, value, traceback):
        if kind:
            self.cancelled.set()
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
        self.limit.__exit__(kind, value, traceback)

    def submit(self, function, /, *args, **kwargs):
        if self.cancelled.is_set():
            raise AutoSTACGISError("Execution cancelled")
        if self.pool is None or _depth.get():
            future = Future()
            try:
                future.set_result(_invoke(function, args, kwargs))
            except BaseException as exc:
                future.set_exception(exc)
            return future
        return self.pool.submit(_invoke, function, args, kwargs)

    def __getstate__(self):
        # A scope crossing into a process becomes a serial worker-local executor.
        return {}

    def __setstate__(self, state):
        self.__init__({"workers": 1})
