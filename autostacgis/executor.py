"""Execute the planner's partial order with bounded services and verified outputs."""

from copy import copy, deepcopy
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .config import merge
from .data import FileBatches, FileRaster, ZarrRaster
from .dependencies import require
from .errors import AutoSTACGISError
from .metadata import get
from .model import digest
from .runtime.registry import Services, settings_for


@dataclass
class RunResult:
    records: list
    plan: object
    services: object
    directory: Path
    solver: object = None

    def close(self):
        self.services.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.services.__exit__(*args)

    def __iter__(self):
        return iter(self.records)


class Context:
    def __init__(self, services, directory, predicted, action, *, inputs=None, binding=None, owner=None):
        self._services, self.directory = services, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.predicted, self.action = predicted, action
        self.inputs = inputs
        self.binding = binding
        self.owner = owner
        self.parameters = {}
        self._owned = False
        self._executor = self._storage = None
        self.extensions = {}
        self.log_fields = {}

    @property
    def base_dir(self):
        return Path(getattr(self, "transfer_base", self.directory))

    @property
    def work(self):
        return Path(getattr(self, "transfer_work", self.directory))

    def path(self, value):
        return (self.base_dir / Path(value).expanduser()).resolve()

    @property
    def scratch(self):
        path = self.directory / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def stac(self):
        from .stac import Stac

        catalog = getattr(self, "transfer_catalog", {})
        catalog = catalog.get("catalog", catalog)
        return Stac(
            {uri: self.path(path) for uri, path in catalog.get("extension_schemas", {}).items()},
            metadata_structure=getattr(self, "structure_definition", None),
        )

    @property
    def metadata_structure(self):
        return self.stac.metadata_structure

    @property
    def services(self):
        from .runtime.registry import ServiceContext

        return ServiceContext(self._services, self.owner, self.owner)

    @property
    def executor(self):
        require(self._services.plugins, self._services.registry, self.owner, "executor")
        return self._executor or self.service("executor")

    @property
    def storage(self):
        require(self._services.plugins, self._services.registry, self.owner, "storage")
        if self._storage:
            return self._storage
        from .runtime.execution import StorageScope

        storage = self.service("storage")
        return StorageScope(
            storage, {"memory_bytes": max(1, storage.limit // self._services.get("executor").workers)}
        )

    @property
    def logger(self):
        return self.service("events").logger

    def progress(self, completed, total, **fields):
        self.service("events").emit(
            "progress", action=self.action, completed=completed, total=total, **self.log_fields, **fields
        )

    def service(self, name):
        return self._services.get(name, caller=self.owner)

    def _for_plugin(self, name):
        child = copy(self)
        child.owner = name
        return child

    def call(self, name, capability, inputs, settings=None):
        if name not in getattr(self, "configured", set()):
            require(self._services.plugins, self._services.registry, self.owner, name)
        plugin = self._services.plugins[name]
        try:
            definition = plugin["capabilities"][capability]
        except KeyError:
            raise AutoSTACGISError(f"Plugin {name} does not provide {capability}") from None
        implementation = self._services.registry.resolve(capability, definition["implementation"])
        options = settings_for(
            implementation,
            {**plugin.get("settings", {}), **definition.get("settings", {}), **(settings or {})},
        )
        if "settings_schema" in plugin:
            from jsonschema import Draft202012Validator

            Draft202012Validator(plugin["settings_schema"]).validate(options)
        transfers = getattr(self, "_transfers", None)
        if transfers and capability in ("encode", "data_out"):
            if capability == "encode":
                inputs = transfers.record(inputs, {"data_types": implementation.input_types})
            else:
                inputs = transfers.value(inputs, implementation.input_types)
        from .dependencies import configured_plugins

        child = self._for_plugin(name)
        child.configured = configured_plugins(options)
        return implementation(inputs, options, child)

    def output(self, port, payload, *, index=0, facts=None):
        record = self.predicted[port][index].clone()
        if self.binding is not None:
            from .metadata import output_metadata
            from .stac import Stac

            metadata = output_metadata(
                self.inputs, self.binding["outputs"][port], index, self.binding.get("settings", {})
            )
            policy = self.binding["outputs"][port]
            if policy.get("predict"):
                metadata = policy["predict"](self.inputs, self.binding.get("settings", {}), index, metadata)
            record.item = Stac.derived(metadata, record.item["id"], metadata.get("assets", {}))
        if self.inputs:
            anchor = (self.binding or {}).get("anchor", next(iter(self.inputs)))
            sources = self.inputs.get(anchor, [])
            if sources:
                member = (self.binding or {}).get("outputs", {}).get(port, {}).get("member_index", index)
                source = sources[min(member, len(sources) - 1)]
                record.runtime["source_id"] = source.runtime.get(
                    "source_id",
                    source.runtime.get(
                        "original_id",
                        source.item.get("properties", {}).get("autostacgis:source_id", source.item["id"]),
                    ),
                )
        if record.runtime.get("source_id"):
            record.item["properties"]["autostacgis:source_id"] = record.runtime["source_id"]
        record.payload = payload
        record.kind = getattr(payload, "kind", None) or record.kind
        # Implementations report scientific facts; declarations alone do not satisfy them.
        from .metadata import assign

        for path, value in (facts or {}).items():
            assign(record.item, path, value)
        if self.binding is not None:
            from .metadata import remove

            for path in self.binding["outputs"][port].get("remove", []):
                remove(record.item, path)
        from .data.metadata import refresh_metadata

        if hasattr(payload, "grid") or hasattr(payload, "batches"):
            refresh_metadata(record)
        else:
            from .artifacts import artifact

            value = artifact(payload)
            if value.type == "path":
                key = record.runtime.get("selected_asset", "data")
                record.item["assets"][key]["href"] = str(value.value)
                if value.media_type:
                    record.item["assets"][key]["type"] = value.media_type
                from .stac import Stac

                Stac.extensions(record.item)
        record.item["properties"].setdefault("processing:software", {})["autostacgis"] = "1.0.0"
        return record

    def save_checkpoint(self, name, outputs):
        if not getattr(self, "checkpoint_call", None):
            raise AutoSTACGISError("Configure a checkpoint provider to save intermediate outputs")
        provider = self._checkpoint_provider()
        return provider.save(digest([self.checkpoint_key, name]), outputs, self.inputs)

    def restore_checkpoint(self, name):
        if not getattr(self, "checkpoint_call", None):
            return None
        return self._checkpoint_provider().load(digest([self.checkpoint_key, name]), self.inputs)

    def _checkpoint_provider(self):
        from .app import Application

        app = Application(
            self.checkpoint_catalog, base_dir=self.checkpoint_base, registry=self._services.registry
        )
        app.work = self.work
        definition, settings = app.resolve_call("checkpoint", self.checkpoint_call)
        return app.registry.resolve("checkpoint", definition["implementation"])(
            settings, app.scope(definition["id"], settings)
        )

    def __getstate__(self):
        runtime = deepcopy(self._services.definitions)
        for name, plugin in self._services.plugins.items():
            definition = plugin.get("capabilities", {}).get("services")
            if not definition:
                continue
            provider = runtime.get(name, definition)["implementation"]
            factory = self._services.registry.resolve("services", provider)
            if name == "executor" or getattr(factory, "resource_group", None) == "executor":
                runtime[name] = {"implementation": "sequential", "settings": {"workers": 1}}
        memory = runtime.get("storage", {}).get("settings", {}).get("memory_bytes", 512 * 1024 * 1024)
        runtime.setdefault("storage", {"implementation": "storage"})["settings"] = {
            **runtime.get("storage", {}).get("settings", {}),
            "memory_bytes": max(1, memory // self._services.get("executor").workers),
            "keep_scratch": True,
        }
        # Worker scratch lives under the parent-owned run directory and is cleaned by its storage owner.
        return {
            "directory": self.directory,
            "predicted": self.predicted,
            "action": self.action,
            "runtime": runtime,
            "registry": self._services.registry,
            "extensions": self.extensions,
            "parameters": self.parameters,
            "inputs": self.inputs,
            "binding": self.binding,
            "owner": self.owner,
            "configured": getattr(self, "configured", set()),
            "plugins": self._services.plugins,
            **{
                k: getattr(self, k)
                for k in (
                    "checkpoint_call",
                    "checkpoint_key",
                    "checkpoint_catalog",
                    "checkpoint_base",
                    "transfer_call",
                    "transfer_catalog",
                    "transfer_base",
                    "transfer_work",
                    "structure_definition",
                )
                if hasattr(self, k)
            },
        }

    def __setstate__(self, state):

        self.__dict__.update({key: value for key, value in state.items() if key != "runtime"})
        self._services = Services(
            state["registry"], state["runtime"], directory=self.directory, plugins=state["plugins"]
        )
        self._services.__enter__()
        self._owned = True
        self._executor = self._storage = None
        self.log_fields = {}
        self._transfers = None
        if hasattr(self, "transfer_call"):
            from .app import Application
            from .transfer import Transfers

            app = Application(
                self.transfer_catalog, base_dir=self.transfer_base, registry=self._services.registry
            )
            definition, options = app.resolve_call("data_transfer", self.transfer_call)
            factory = app.registry.resolve("data_transfer", definition["implementation"])
            if hasattr(factory, "worker_settings"):
                options = factory.worker_settings(options)
            worker_call = {"plugin": definition["id"], "settings": options}
            self._transfers = Transfers(app, worker_call, self.directory, retain_files=True)
            self._services.stack.callback(self._transfers.close)


def _completed(result, context):
    # Normalize worker streams before serialization; user factories may be closures.
    if context._owned and context._transfers and isinstance(result, dict) and context.binding:
        from .artifacts import artifact

        for port, records in result.items():
            result[port] = [
                context._transfers.record(r, context.binding["outputs"][port])
                if artifact(r.payload).type == "byte-stream"
                else r
                for r in records
            ]
    return result


def _execute(function, inputs, settings, context):
    try:
        from contextlib import ExitStack

        with ExitStack() as stack:
            for name, options in context.extensions.items():
                provider = context._services.plugins[name]["capabilities"]["extensions"]["implementation"]
                extension = context._services.registry.resolve("extensions", provider)()
                if hasattr(extension, "execute"):
                    extension_context = context._for_plugin(name)
                    stack.enter_context(extension.execute(extension_context, options))
                    context._executor = extension_context._executor
                    context._storage = extension_context._storage
                    context.log_fields = extension_context.log_fields
            import inspect

            if "inputs" in inspect.signature(function).parameters:
                result = function(inputs, settings, context)
            else:
                arguments = {
                    name: values[0]
                    if context.binding["argument_requirements"][name].get("cardinality", "one") == "one"
                    else tuple(values)
                    for name, values in inputs.items()
                }
                result = function(**arguments, settings=settings, context=context)
            if inspect.isgenerator(result):
                from .checkpoint import Checkpoint

                while True:
                    try:
                        event = next(result)
                    except StopIteration as finished:
                        return _completed(finished.value, context)
                    if not isinstance(event, Checkpoint):
                        raise AutoSTACGISError("Processing generators must yield Checkpoint events")
                    context.save_checkpoint(event.name, event.outputs)
            return _completed(result, context)
    finally:
        if context._owned:
            context._services.__exit__(None, None, None)


def attach_payload(record):
    if record.payload is not None:
        return record
    from .io import asset_path

    path = asset_path(record)
    if record.kind == "raster":
        record.payload = ZarrRaster(path) if str(path).endswith(".zarr") else FileRaster(path)
    elif record.kind in ("vector", "point_cloud", "table"):
        if record.kind == "vector" and not str(path).endswith(".parquet"):
            from .data.batches import VectorFile

            record.payload = VectorFile(path)
        elif record.kind == "point_cloud" and str(path).lower().endswith((".las", ".laz")):
            from .data.batches import LasBatches

            record.payload = LasBatches(path)
        else:
            record.payload = FileBatches(path, kind=record.kind)
    else:
        raise AutoSTACGISError(f"No native reader for {record.kind}")
    return record


class Executor:
    def __init__(self, app, request):
        self.app, self.request = app, request

    def run(self, plan, solver):
        from concurrent.futures import FIRST_COMPLETED, wait

        from .contracts import cost_of, requirements_hold

        directory = self.app.work / "runs" / uuid4().hex
        directory.mkdir(parents=True)
        defaults = {
            "executor": {"implementation": "threads", "settings": {"workers": 1}},
            "storage": {"implementation": "storage", "settings": {}},
            "events": {"implementation": "events", "settings": {}},
        }
        services = Services(
            self.app.registry,
            merge(merge(defaults, self.app.catalog.get("runtime", {})), self.request.get("runtime", {})),
            directory=directory,
            events=self.app.events,
            plugins=self.app.plugins,
        )
        services.__enter__()
        self.app.active_services = services
        try:
            from .transfer import Transfers

            transfers = Transfers(
                self.app,
                self.request.get("data_transfer", self.app.catalog["defaults"]["data_transfer"]),
                directory,
            )
            services.stack.callback(transfers.close)
            import shutil

            services.stack.callback(shutil.rmtree, directory / "artifacts", True)
            self.app.active_transfers = transfers
            executor, storage, events = (
                services.get("executor"),
                services.get("storage"),
                services.get("events"),
            )
            records = {k: r.clone() for k, r in plan.initial.items()}
            pending = {s.key: s for s in plan.steps}
            running = {}
            done = set()
            events.emit("run_started", steps=len(pending), engine=plan.report["engine"])
            while pending or running:
                for key, step in list(pending.items()):
                    if not set(step.dependencies) <= done or len(running) >= executor.workers:
                        continue
                    contract = deepcopy(plan.module_contracts[step.module])
                    contract["settings"] = step.settings
                    contract.update(step.options)
                    for policy in contract["argument_requirements"].values():
                        policy.setdefault("transfer", contract.get("transfer", "optimized"))
                    if step.key in plan.parameter_templates:
                        from .workflow import parameters

                        template, named = plan.parameter_templates[step.key]
                        live = parameters(template, named, records, self.app)
                        if live != step.settings:
                            raise AutoSTACGISError(
                                f"{step.name}: result metadata changed after planning; replan required"
                            )
                    inputs = {
                        p: [
                            transfers.record(
                                records[k],
                                contract["argument_requirements"][p],
                                shared=getattr(executor, "backend", None) == "processes",
                            )
                            for k in keys
                        ]
                        for p, keys in step.inputs.items()
                    }
                    if not requirements_hold(self.app, step.module, contract, inputs, step.settings):
                        raise AutoSTACGISError(f"{step.module}: live inputs do not satisfy requirements")
                    if cost_of(contract, inputs, step.settings) != step.cost:
                        raise AutoSTACGISError(f"{step.module}: metadata changed the planned cost")
                    predicted = {p: [plan.predicted[k] for k in keys] for p, keys in step.outputs.items()}
                    context = Context(
                        services,
                        storage.directory / key,
                        predicted,
                        step.module,
                        inputs=inputs,
                        binding=contract,
                        owner=step.module,
                    )
                    context._transfers = transfers
                    context.transfer_call = self.request.get(
                        "data_transfer", self.app.catalog["defaults"]["data_transfer"]
                    )
                    context.structure_definition = (
                        self.app.metadata_structure.definition()
                        if self.app.metadata_structure.enabled
                        else None
                    )
                    context.transfer_catalog = self.app.snapshot()
                    context.transfer_base = self.app.base_dir
                    context.transfer_work = self.app.work
                    from .dependencies import configured_plugins

                    context.configured = configured_plugins(step.settings)
                    context.extensions = contract.get("extensions", {})
                    definition = self.app.capabilities["process"][step.module]
                    function = self.app.registry.resolve("process", definition["implementation"])
                    checkpoint_call = contract.get("checkpoint", self.request.get("checkpoint"))
                    cache_key, cached, checkpoint = None, None, None
                    if checkpoint_call:
                        from .checkpoint import identity

                        cache_key = identity(function, step, inputs)
                        if cache_key is None:
                            raise AutoSTACGISError("Checkpointing requires an identifiable implementation")
                        definition, options = self.app.resolve_call("checkpoint", checkpoint_call)
                        checkpoint = self.app.registry.resolve("checkpoint", definition["implementation"])(
                            options, self.app.scope(definition["id"], options)
                        )
                        cached = checkpoint.load(cache_key, inputs)
                        context.checkpoint_call = checkpoint_call
                        context.checkpoint_key = cache_key
                        context.checkpoint_catalog = self.app.snapshot()
                        context.checkpoint_base = self.app.base_dir
                    if cached is None:
                        future = executor.submit(_execute, function, inputs, step.settings, context)
                    else:
                        from concurrent.futures import Future

                        future = Future()
                        future.set_result(cached)
                    running[future] = (
                        step,
                        contract,
                        predicted,
                        cache_key,
                        cached is not None,
                        checkpoint,
                        inputs,
                    )
                    del pending[key]
                    events.emit("step_started", action=step.module, key=key)
                if not running:
                    raise AutoSTACGISError("Workflow contains a dependency cycle")
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    step, contract, predicted, cache_key, cached, checkpoint, inputs = running.pop(future)
                    result = future.result()
                    self.verify(contract, predicted, result)
                    if checkpoint and not cached:
                        result = checkpoint.save(cache_key, result, inputs).outputs
                    for port, group in result.items():
                        for key, record in zip(step.outputs[port], group, strict=True):
                            record.key = key
                            record.runtime.update(
                                {
                                    k: v
                                    for k, v in plan.predicted[key].runtime.items()
                                    if k in ("lineage", "member_sources", "producer")
                                }
                            )
                            if port in step.paths:
                                from .workflow import persist

                                record = persist(record, step.paths[port], plan.initial, self.app)
                            policy = contract["outputs"][port]
                            if policy.get("publish"):
                                from .publication import publish

                                record = publish(self.app, record, policy["publish"])
                            records[key] = transfers.record(
                                record,
                                {
                                    **policy,
                                    "transfer": policy.get("transfer", contract.get("transfer", "optimized")),
                                },
                                produced=True,
                            )
                    done.add(step.key)
                    events.emit("step_finished", action=step.module, key=step.key)
            chosen = [records[k] for keys in plan.outputs.values() for k in keys]
            if not solver.validate_outputs(plan, chosen):
                raise AutoSTACGISError("Actual outputs do not satisfy the requested goals")
            outputs = [attach_payload(r) for r in {r.key: r for r in chosen}.values()]
            from .publication import publish

            policies = plan.output_policies
            published = {}
            for name, keys in plan.outputs.items():
                for key in keys:
                    record = records[key]
                    if policies.get(name):
                        record = publish(self.app, attach_payload(record), policies[name])
                    published[key] = record
            outputs = list(published.values())
            if not solver.validate_outputs(plan, outputs):
                raise AutoSTACGISError("Published outputs do not satisfy the requested goals")
            events.emit("run_finished", outputs=len(outputs))
            return RunResult(outputs, plan, services, directory, solver)
        except BaseException as exc:
            # Join workers before releasing buffers, temporary files and services.
            for future in locals().get("running", {}):
                future.cancel()
            if locals().get("running"):
                wait(running)
            services.__exit__(type(exc), exc, exc.__traceback__)
            raise
        finally:
            self.app.active_services = None
            self.app.active_transfers = None

    def verify(self, contract, predicted, result):
        from .contracts import value
        from .metadata import fulfills

        if not isinstance(result, dict) or set(result) != set(predicted):
            raise AutoSTACGISError("Incorrect named outputs")
        for port, group in result.items():
            if len(group) != len(predicted[port]):
                raise AutoSTACGISError(f"{port}: output cardinality differs from plan")
            policy = contract["outputs"][port]
            for actual, expected in zip(group, predicted[port], strict=True):
                if actual.payload is None or actual.kind != expected.kind:
                    raise AutoSTACGISError(f"{port}: missing payload or wrong kind")
                from .artifacts import accepted

                accepted(actual.payload, policy["data_types"], owner=f"output {port}")
                for operation in ("set", "append"):
                    for assignment in policy.get(operation, []):
                        path = next(iter(assignment))
                        if not fulfills(value(actual, path), get(expected.item, path)):
                            raise AutoSTACGISError(f"{port}: metadata violates promised {path}")
                self.app.stac.extensions(actual.item)
                self.app.stac.validate(actual.item)
