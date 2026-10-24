"""Discovery, composable goals, workflow synthesis and streaming execution."""

from copy import deepcopy
from pathlib import Path

from .config import merge, read, validate
from .dependencies import PluginContext, requirements, validate_dependencies
from .errors import AutoSTACGISError, NoPlan, SearchLimit
from .executor import Executor
from .model import digest
from .registry import CAPABILITIES, Registry
from .runtime.registry import settings_for, validate_settings
from .solvers import expand_goal, validate_parameters
from .stac import Stac


class Application:
    def __init__(self, catalog=None, *, base_dir=".", registry=None, events=None):
        from .workflow_config import is_workflow, normalize

        self.workflow = deepcopy(catalog) if is_workflow(catalog) else None
        self.trigger_name = None
        if self.workflow is not None:
            registry = registry or Registry(builtins=False)
            catalog = normalize(self.workflow, registry)
            self.trigger_name = next(iter(self.workflow.get("trigger", {})), "now")
        if catalog is None:
            from .builtins.catalog import catalog as builtin_catalog

            catalog = builtin_catalog()
        self.default_request = None
        structure = catalog.get("metadata_structure")
        if "catalog" in catalog or "request" in catalog:
            config = validate(catalog, "config")
            self.default_request = config["request"]
            catalog = config.get("catalog")
            if catalog is None:
                from .builtins.catalog import catalog as builtin_catalog

                catalog = builtin_catalog()
        self.catalog = validate(catalog, "catalog")
        self.base_dir = Path(base_dir).resolve()
        compact = any("use" in entry for entry in catalog["plugins"])
        self.registry = registry or Registry(builtins=not compact)
        if compact:
            explicit = {}
            for entry in self.catalog["plugins"]:
                if "use" in entry:
                    name = self.registry.load(entry["use"])
                    definition = self.registry.definition(name)
                    configured = entry.get("settings", {})
                    targets = (
                        ["process"]
                        if "process" in definition["capabilities"]
                        else list(definition["capabilities"])
                    )
                    if entry.get("execution") and "process" not in definition["capabilities"]:
                        raise AutoSTACGISError(f"{name}: execution options require a process capability")
                    if "execution" in entry:
                        definition["capabilities"]["process"]["execution"] = entry["execution"]
                    for target in targets:
                        capability = definition["capabilities"][target]
                        capability["settings"] = merge(capability.get("settings", {}), configured)
                else:
                    name, definition = entry["id"], entry
                    for capability in definition["capabilities"].values():
                        self.registry.load(capability["implementation"])
                if name in explicit:
                    raise AutoSTACGISError(f"Duplicate selected plugin {name}")
                explicit[name] = definition
            resolved = {}

            def select(name, active=()):
                if name in active:
                    raise AutoSTACGISError("Recursive plugin dependency: " + " -> ".join((*active, name)))
                if name in resolved:
                    return
                definition = explicit[name] if name in explicit else self.registry.definition(name)
                for dependency in sorted(requirements({name: definition}, self.registry, name)):
                    self.registry.load(dependency)
                    select(dependency, (*active, name))
                resolved[name] = definition

            for name in explicit:
                select(name)
            self.catalog["plugins"] = list(resolved.values())
            self.catalog.setdefault("runtime", {})
            for name in ("executor", "storage", "events"):
                if name in resolved and "services" in self.registry.plugins.get(name, {}):
                    self.catalog["runtime"].setdefault(
                        name,
                        deepcopy(
                            explicit.get(name, self.registry.definition(name))["capabilities"]["services"]
                        ),
                    )
        from .builtins.catalog import DEFAULTS

        self.catalog["defaults"] = merge(DEFAULTS, self.catalog.get("defaults", {}))
        selected = {p["id"] for p in self.catalog["plugins"]}

        def include(name):
            self.registry.load(name)
            for dependency in self.registry.requirements[name]:
                include(dependency)
            if name not in selected:
                self.catalog["plugins"].append(self.registry.definition(name))
                selected.add(name)

        for call in self.catalog["defaults"].values():
            include(call["plugin"])
        if self.workflow is not None:
            for name in ("executor", "storage", "events"):
                include(name)
                definition = next(p for p in self.catalog["plugins"] if p["id"] == name)
                self.catalog.setdefault("runtime", {}).setdefault(
                    name, deepcopy(definition["capabilities"]["services"])
                )
        solvers = [p["id"] for p in self.catalog["plugins"] if "solver" in p["capabilities"]]
        if self.catalog.get("solver"):
            include(self.catalog["solver"]["plugin"])
        elif not solvers:
            include("backward_chain")
        self.events = events
        self.active_services = None
        self.active_transfers = None
        self.plugins = {}
        self.capabilities = {c: {} for c in CAPABILITIES}
        for plugin in self.catalog["plugins"]:
            name = plugin["id"]
            if name in self.plugins:
                raise AutoSTACGISError(f"Duplicate plugin {name}")
            self.plugins[name] = plugin
            for capability, definition in plugin["capabilities"].items():
                entry = {"id": name, **deepcopy(definition)}
                self.registry.resolve(capability, entry["implementation"])
                function = self.registry.resolve(capability, entry["implementation"])
                if (
                    capability in ("goal", "solver")
                    and entry.get("contract", function.contract) != function.contract
                ):
                    raise AutoSTACGISError(f"{name}: installed plugin contracts cannot be overridden")
                if capability not in ("services", "extensions"):
                    entry["settings"] = self.implementation_settings(
                        name,
                        capability,
                        entry["implementation"],
                        merge(
                            getattr(function, "contract", {}).get("settings", {}),
                            entry.get("settings", {}),
                        ),
                        partial=capability in ("process", "data_out", "goal", "trigger"),
                    )
                self.capabilities[capability][name] = entry
        validate_dependencies(self.plugins, self.registry)
        solvers = self.capabilities["solver"]
        if len(solvers) != 1:
            raise AutoSTACGISError("Exactly one solver plugin must be selected; found: " + ", ".join(solvers))
        self.solver_name = next(iter(solvers))
        if self.workflow is not None:
            self.trigger_name = next(iter(self.capabilities["trigger"]), "now")
        self.solver_implementation = self.registry.resolve(
            "solver", solvers[self.solver_name]["implementation"]
        )
        self.solver_settings({})
        for name, definition in self.capabilities["goal"].items():
            implementation = self.registry.resolve("goal", definition["implementation"])
            required = implementation.contract["solver"]
            if required != self.solver_name:
                raise AutoSTACGISError(
                    f"Goal {name} requires solver {required}; active solver is {self.solver_name}"
                )

        self.stac = Stac(
            {uri: self.path(p) for uri, p in catalog.get("extension_schemas", {}).items()},
            metadata_structure=structure,
        )
        self.metadata_structure = self.stac.metadata_structure
        self.sources = {}
        self.acquirers = {}
        for capability, collection in (("metadata_in", self.sources), ("data_in", self.acquirers)):
            for name, definition in self.capabilities[capability].items():
                implementation = self.registry.resolve(capability, definition["implementation"])
                collection[name] = implementation()
        self.work = self.path("work")

    def snapshot(self):
        if self.workflow is not None:
            return deepcopy(self.workflow)
        result = {
            "catalog": deepcopy(self.catalog),
            "request": deepcopy(self.default_request or {"goal": {"output": {}}}),
        }
        if self.metadata_structure.enabled:
            result["metadata_structure"] = self.metadata_structure.definition()
        return result

    @classmethod
    def from_file(cls, path, **kwargs):
        path = Path(path).resolve()
        return cls(read(path), base_dir=path.parent, **kwargs)

    def path(self, value):
        return (self.base_dir / Path(value).expanduser()).resolve()

    def implementation_settings(self, plugin, capability, implementation, overrides, *, partial=False):
        definition = self.plugins[plugin]
        settings = merge(definition.get("settings", {}), overrides)
        if "settings_schema" in definition:
            validate_settings(definition["settings_schema"], settings, partial=partial)
        function = self.registry.resolve(capability, implementation)
        if capability in ("goal", "solver"):
            validate_parameters(function.contract["settings_schema"], settings, plugin, partial=partial)
        return settings_for(function, settings, partial=partial)

    def solver_settings(self, request):
        settings = deepcopy(self.capabilities["solver"][self.solver_name].get("settings", {}))
        for call in (self.catalog.get("solver"), request.get("solver")):
            if call is None:
                continue
            if call["plugin"] != self.solver_name:
                raise AutoSTACGISError(
                    f"Only solver {self.solver_name} is active; cannot select {call['plugin']}"
                )
            settings = merge(settings, call.get("settings", {}))
        return validate_parameters(
            self.solver_implementation.contract["settings_schema"], settings, self.solver_name
        )

    def make_solver(self, request, records):
        prepared = deepcopy(request)
        prepared["goal"] = expand_goal(request["goal"], records, self)
        validate_parameters(
            self.solver_implementation.contract["goal_schema"], prepared["goal"], self.solver_name + " goal"
        )
        solver = self.solver_implementation(self, prepared, self.solver_settings(request))
        if not all(callable(getattr(solver, method, None)) for method in ("plan", "validate_outputs")):
            raise AutoSTACGISError(f"{self.solver_name}: solver must implement plan and validate_outputs")
        return solver, prepared

    def extension(self, name):
        try:
            definition = self.capabilities["extensions"][name]
        except KeyError:
            raise AutoSTACGISError(f"Unavailable extension plugin: {name}") from None
        return self.registry.resolve("extensions", definition["implementation"])

    def scope(self, name, options=None):
        return PluginContext(self, name, options)

    def invoke(self, name, capability, value, settings=None):
        definition, options = self.resolve_call(capability, {"plugin": name, "settings": settings or {}})
        implementation = self.registry.resolve(capability, definition["implementation"])
        if self.active_transfers and capability in ("data_out", "encode"):
            if capability == "encode":
                value = self.active_transfers.record(value, {"data_types": implementation.input_types})
            else:
                value = self.active_transfers.value(value, implementation.input_types)
        return implementation(value, options, self.scope(name, options))

    def resolve_call(self, capability, call):
        try:
            definition = self.capabilities[capability][call["plugin"]]
        except KeyError:
            raise AutoSTACGISError(f"Unavailable {capability} plugin: {call.get('plugin')}") from None
        settings = merge(definition.get("settings", {}), call.get("settings", {}))
        settings = self.implementation_settings(
            definition["id"], capability, definition["implementation"], settings
        )
        return definition, settings

    def modules(self, request):
        result = {}
        allowed = request.get("modules", list(self.capabilities["process"]))
        for name in allowed:
            if name not in self.capabilities["process"]:
                raise AutoSTACGISError(f"Unknown processing module {name}")
            definition = self.capabilities["process"][name]
            function = self.registry.resolve("process", definition["implementation"])
            if not hasattr(function, "contract"):
                raise AutoSTACGISError(f"{name}: processing function must declare a contract")
            contract = merge(deepcopy(function.contract), definition.get("execution", {}))
            if not contract.get("argument_requirements") or not contract.get("outputs"):
                raise AutoSTACGISError(f"{name}: named input requirements and outputs are required")
            contract["settings"] = settings_for(
                function,
                merge(
                    merge(contract.get("settings", {}), definition.get("settings", {})),
                    request.get("module_settings", {}).get(name, {}),
                ),
                partial=any(step.get("plugin") == name for step in request.get("steps", {}).values()),
            )
            for port, declaration in contract["argument_requirements"].items():
                if not isinstance(port, str) or declaration.get("cardinality", "one") not in ("one", "many"):
                    raise AutoSTACGISError(f"{name}: invalid argument declaration {port}")
                types = declaration.get("data_types")
                if (
                    not isinstance(types, list)
                    or not types
                    or any(not isinstance(t, str) or not t for t in types)
                ):
                    raise AutoSTACGISError(f"{name}: input {port} must declare data_types")
            for port, policy in contract["outputs"].items():
                types = policy.get("data_types")
                if (
                    not isinstance(types, list)
                    or not types
                    or any(not isinstance(t, str) or not t for t in types)
                ):
                    raise AutoSTACGISError(f"{name}: output {port} must declare data_types")
                if "kind" not in policy:
                    raise AutoSTACGISError(f"{name}: output kind is required")
                if policy.get("each_from") and policy["each_from"] not in contract["argument_requirements"]:
                    raise AutoSTACGISError(f"{name}: unknown each_from input")
                for reference in policy.get("inherit_metadata", []):
                    if reference.partition(".")[0] not in contract["argument_requirements"]:
                        raise AutoSTACGISError(f"{name}: unknown inheritance input {reference}")
                self.validate_publication(policy["data_types"], policy.get("publish"))
            result[name] = contract
        return result

    def transfer_supports(self, record, declaration, request):
        from .artifacts import artifact

        types = record.runtime.get("data_types")
        if types is None and record.payload is not None:
            types = [artifact(record.payload).type]
        if types is None:
            return True  # The metadata producer has not advertised a representation yet.
        definition, _ = self.resolve_call(
            "data_transfer", request.get("data_transfer", self.catalog["defaults"]["data_transfer"])
        )
        provider = self.registry.resolve("data_transfer", definition["implementation"])
        receiver = declaration["data_types"]
        source = [type_ for type_ in types if type_ in provider.input_types]
        if hasattr(provider, "can_transfer"):
            return provider.can_transfer(source, receiver)
        return bool(set(source) & set(receiver) & set(provider.output_types))

    def validate_publication(self, types, policy):
        if not policy:
            return
        if policy.get("encode"):
            definition, _ = self.resolve_call("encode", policy["encode"])
            function = self.registry.resolve("encode", definition["implementation"])
            if not set(types) & set(function.input_types):
                raise AutoSTACGISError(f"{definition['id']}: cannot encode {types}")
            types = function.output_types
        if policy.get("data_out"):
            definition, _ = self.resolve_call("data_out", policy["data_out"])
            function = self.registry.resolve("data_out", definition["implementation"])
            if not set(types) & set(function.input_types):
                raise AutoSTACGISError(f"{definition['id']}: unsupported output types {types}")
        elif policy.get("encode"):
            raise AutoSTACGISError("An encoder requires a data_out destination")
        metadata = policy.get("metadata_out", [])
        for call in metadata if isinstance(metadata, list) else [metadata]:
            self.resolve_call("metadata_out", call)

    def request(self, value=None):
        if value is None:
            value = self.default_request
        if isinstance(value, (str, Path)):
            value = read(value)
        if isinstance(value, dict) and "request" in value:
            value = value["request"]
        request = validate(value, "request")
        self.solver_settings(request)
        self.work = self.path(request.get("work_directory", "work"))
        for capability, field in [("metadata_in", "sources")]:
            for name in request.get(field, []):
                if name not in self.capabilities[capability]:
                    raise AutoSTACGISError(f"Unknown {capability} plugin {name}")
        if self.capabilities["data_transfer"]:
            self.resolve_call(
                "data_transfer", request.get("data_transfer", self.catalog["defaults"]["data_transfer"])
            )
        if request.get("checkpoint"):
            self.resolve_call("checkpoint", request["checkpoint"])
        for name in request.get("module_settings", {}):
            if name not in self.capabilities["process"]:
                raise AutoSTACGISError(f"Unknown module settings target: {name}")
        modules = self.modules(request)
        from .workflow import validate_steps

        validate_steps(self, request, modules)
        for target, call in request.get("sequence", {}).items():
            name, separator, port = target.rpartition(".")
            if not separator or name not in modules or port not in modules[name]["argument_requirements"]:
                raise AutoSTACGISError(f"Unknown grouping input: {target}")
            if call is not None:
                for entry in call if isinstance(call, list) else [call]:
                    self.resolve_call("sequence", entry)
        return request

    def discover(self, request=None):
        request = self.request(request)
        records, seen = [], {}
        sources = request.get("sources", [])
        # Inventory-dependent sources are discovered after ordinary sources,
        # independently of the order in the request. They still perform no acquisition.
        ordered = sorted(sources, key=lambda name: hasattr(self.sources[name], "discover_from"))
        for name in ordered:
            definition = self.capabilities["metadata_in"][name]
            source = self.sources[name]
            arguments = (
                definition.get("settings", {}),
                request.get("query", {}),
                self.scope(name, definition.get("settings", {})),
            )
            discovered = (
                source.discover_from(*arguments, tuple(r.clone() for r in records))
                if hasattr(source, "discover_from")
                else source.discover(*arguments)
            )
            for record in discovered:
                record.source = name
                acquisition = record.runtime.get("data_in", getattr(source, "data_in", None))
                if acquisition is not None:
                    self.scope(name).acquirer(acquisition)
                    record.runtime.setdefault("data_in", acquisition)
                self.stac.extensions(record.item)
                if record.key in seen:
                    if digest(record.item) != seen[record.key]:
                        raise AutoSTACGISError("Conflicting duplicate STAC item")
                    continue
                seen[record.key] = digest(record.item)
                records.append(record)
        return records

    def plan(self, request=None, inputs=None):
        request = self.request(request)
        records = self.discover(request) if inputs is None else inputs
        solver, _ = self.make_solver(request, records)
        return solver.plan(records)

    def available(self, request=None, inputs=None):
        request = self.request(request)
        records = self.discover(request) if inputs is None else inputs
        try:
            return {"requested": self.plan(request, records).to_dict()}
        except (NoPlan, SearchLimit) as exc:
            return {"requested": {"status": type(exc).__name__, "reason": str(exc)}}

    def run(self, request=None, inputs=None):
        request = self.request(request)
        records = self.discover(request) if inputs is None else [r.clone() for r in inputs]
        if request.get("discovery", False):
            return self.plan(request, records)
        inspected = set()
        while True:
            solver, prepared = self.make_solver(request, records)
            plan = solver.plan(records)
            used = {key for step in plan.steps for keys in step.inputs.values() for key in keys}
            used.update(key for keys in plan.outputs.values() for key in keys)
            origins = {r.runtime["origin"] for key, r in plan.initial.items() if key in used}
            required = [r for r in records if r.key in origins and r.key not in inspected]
            if not required:
                return Executor(self, prepared).run(plan, solver)
            for record in required:
                key = record.key
                index = next(i for i, r in enumerate(records) if r.key == key)
                name = record.runtime.get("data_in", record.source)
                if "data_in" in record.runtime and name not in self.acquirers:
                    raise AutoSTACGISError(f"Unavailable data_in plugin: {name}")
                if name in self.acquirers:
                    definition = self.capabilities["data_in"][name]
                    record = self.acquirers[name].acquire(
                        record,
                        definition.get("settings", {}),
                        self.scope(name, definition.get("settings", {})),
                    )
                self.stac.extensions(record.item)
                records[index] = record
                records[index].key = key
                inspected.add(key)

    def trigger(self, name=None, request=None):
        name = name or self.trigger_name or "now"
        if name == "now" and name not in self.capabilities["trigger"]:
            return self.run(request)
        definition, settings = self.resolve_call("trigger", {"plugin": name})
        return self.registry.resolve("trigger", definition["implementation"])(
            self.scope(name), settings, request
        )
