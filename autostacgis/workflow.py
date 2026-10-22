"""Explicit workflow stages around the ordinary backward solver."""

from copy import deepcopy
from itertools import product

from jsonschema import ValidationError

from .config import merge
from .contracts import cost_of, requirements_hold, test
from .errors import AutoSTACGISError, NoPlan, SearchLimit
from .model import Step, digest


def reference(value, results, objects):
    """Resolve named native output groups, or a JSON Pointer into one Item."""
    try:
        _, name, selector = value.split(":", 2)
    except ValueError:
        raise AutoSTACGISError(f"Invalid result reference: {value}") from None
    if not name or not selector:
        raise AutoSTACGISError(f"Invalid result reference: {value}")
    port, _, pointer = selector.partition("#")
    try:
        group = [objects[k] for k in results[name][port]]
    except KeyError:
        raise AutoSTACGISError(f"Unknown or forward result reference: {value}") from None
    if "#" not in selector:
        return group
    if len(group) != 1 or (pointer and not pointer.startswith("/")):
        raise AutoSTACGISError(f"Scalar result reference needs one Item and a JSON Pointer: {value}")
    current = group[0].item
    try:
        for key in pointer[1:].split("/") if pointer else []:
            key = key.replace("~1", "/").replace("~0", "~")
            current = current[int(key)] if isinstance(current, list) else current[key]
    except (KeyError, IndexError, TypeError, ValueError):
        raise AutoSTACGISError(f"Result metadata is unavailable: {value}") from None
    return deepcopy(current)


def parameters(value, results, objects, app):
    if isinstance(value, dict):
        return {k: parameters(v, results, objects, app) for k, v in value.items()}
    if isinstance(value, list):
        return [parameters(v, results, objects, app) for v in value]
    if isinstance(value, str) and value.startswith("result:"):
        if "#" not in value:
            raise AutoSTACGISError(
                "Parameters reference Item metadata with result:step:port#/pointer; inputs use native ports"
            )
        return reference(value, results, objects)
    if isinstance(value, str) and value.startswith("path:"):
        return str(app.path(value[5:]))
    return value


def stages(planner):
    p = planner
    declarations = p.request.get("steps")
    p.named_results, p.required_steps = {}, set()
    p.parameter_templates = {}
    if declarations is None:
        return
    available = p.modules
    for label, declaration in declarations.items():
        if "plugin" in declaration and declaration["plugin"] not in available:
            raise AutoSTACGISError(f"{label}: unavailable processing plugin {declaration['plugin']}")
    automatic_modules = {}
    for name, contract in available.items():
        implementation = p.app.capabilities["process"][name]["implementation"]
        try:
            p.app.implementation_settings(name, "process", implementation, contract.get("settings", {}))
        except ValidationError:
            continue  # Required parameters may be supplied by a named step only.
        automatic_modules[name] = contract
    frontier = dict(p.leaves)
    automatic = False
    barrier = set()
    destinations = set()
    entries = list(declarations.items())
    for position, (label, declaration) in enumerate(entries):
        if declaration.get("auto"):
            automatic = True
            continue
        name = declaration["plugin"]
        if name not in available:
            raise AutoSTACGISError(f"{label}: unavailable processing plugin {name}")
        contract = deepcopy(available[name])
        template = merge(contract.get("settings", {}), declaration.get("settings", {}))
        template = merge(template, {k[6:]: v for k, v in declaration.items() if k.startswith("param:")})
        settings = parameters(template, p.named_results, p.objects, p.app)
        implementation = p.app.capabilities["process"][name]["implementation"]
        contract["settings"] = p.app.implementation_settings(name, "process", implementation, settings)
        for option in ("transfer", "checkpoint"):
            if option in declaration:
                contract[option] = declaration[option]
        for port, routing in declaration.get("sequence", {}).items():
            contract["argument_requirements"][port]["sequence"] = routing
            contract["argument_requirements"][port]["sequence_override"] = True
        for port, output in declaration.get("outputs", {}).items():
            if isinstance(output, dict):
                contract["outputs"][port].update(output)
                p.app.validate_publication(contract["outputs"][port]["data_types"], output.get("publish"))
        p.modules = automatic_modules if automatic else {}
        p.leaves = dict(frontier)
        p.memo.clear()
        ports, options = [], []
        anchor = contract.get("anchor", next(iter(contract["argument_requirements"])))
        for port, arg in contract["argument_requirements"].items():
            ports.append(port)
            if port in declaration.get("inputs", {}):
                ref = declaration["inputs"][port]
                if "#" in ref:
                    raise AutoSTACGISError(f"{label}.{port}: input must reference a native output port")
                values = reference(ref, p.named_results, p.objects)
                if not isinstance(values, list):
                    raise AutoSTACGISError(f"{label}.{port}: input must reference a native output port")
            elif automatic:
                desired = p._atoms(arg.get("require", {}))
                values = []
                # In a final open gap, goals preserved by the strict suffix must
                # be established before that suffix. Try every goal alternative.
                suffix = entries[position:]
                if port == anchor and not any(d.get("auto") for _, d in suffix):
                    from .goals import compile_goal

                    goals = compile_goal(p.request["goal"], list(p.initial.values()), p.app)
                    for constraints in goals:
                        extra = []
                        for constraint in constraints:
                            if constraint.get("kind", arg.get("kind")) != arg.get("kind"):
                                continue
                            for atom in p._atoms(constraint.get("require", {})):
                                if not any(
                                    p._establishes(d["plugin"], out, atom, available[d["plugin"]])
                                    for _, d in suffix
                                    for out in available[d["plugin"]]["outputs"]
                                ):
                                    extra.append(atom)
                        values.extend(p._derive(arg.get("kind"), desired + extra))
                values.extend(p._derive(arg.get("kind"), desired))
            else:
                values = list(frontier.values())
            if port in declaration.get("inputs", {}):
                eligible = [
                    r
                    for r in values
                    if arg.get("kind", r.kind) == r.kind
                    and test(r, arg.get("require", {})) is True
                    and p.app.transfer_supports(r, arg, p.request)
                    and (not arg.get("asset") or r.runtime["selected_asset"] == arg["asset"])
                ]
                if arg.get("cardinality", "one") == "many":
                    routing = (
                        arg.get("sequence")
                        if arg.get("sequence_override")
                        else p.request.get("sequence", {}).get(f"{name}.{port}", arg.get("sequence"))
                    )
                    if len(eligible) != len(values):
                        choices = []
                    elif routing:
                        from .sequence import route

                        choices = [
                            list(g)
                            for g in route(
                                p.app,
                                routing,
                                values,
                                None
                                if arg.get("sequence_override")
                                or f"{name}.{port}" in p.request.get("sequence", {})
                                else name,
                            )
                            if len(g) >= arg.get("min_items", 1)
                        ]
                    else:
                        choices = [values] if len(values) >= arg.get("min_items", 1) else []
                else:
                    choices = [[r] for r in eligible]
            else:
                choices = p.port_choices(name, port, arg, {r.key: r for r in values})
            options.append(choices)
        unknown = set(declaration.get("inputs", {})) - set(ports)
        if unknown:
            raise AutoSTACGISError(f"{label}: unknown input ports {sorted(unknown)}")
        unknown = set(declaration.get("outputs", {})) - set(contract["outputs"])
        if unknown:
            raise AutoSTACGISError(f"{label}: unknown output ports {sorted(unknown)}")
        produced, stage_keys, consumed = {}, set(), set()
        # Prefer the cheapest valid binding per anchor family. Multi-input
        # alternatives must not execute several times against the same member.
        anchor = contract.get("anchor", ports[0])
        combinations = []
        for groups in product(*options):
            p.check_limit()
            p.bindings += 1
            if p.bindings > p.options.get("max_bindings", 100000):
                raise SearchLimit("Workflow binding limit exceeded")
            inputs = dict(zip(ports, groups, strict=True))
            if requirements_hold(p.app, name, contract, inputs, settings):
                combinations.append((cost_of(contract, inputs, settings), inputs))
        bound = set()
        for cost, inputs in sorted(combinations, key=lambda pair: (-len(pair[1][anchor]), pair[0])):
            family = tuple(sorted(p._identity(r) for r in inputs[anchor]))
            if any(identity in bound for identity in family):
                continue
            bound.update(family)
            input_keys = {port: [r.key for r in group] for port, group in inputs.items()}
            key = digest([label, name, input_keys, settings])[:24]
            predicted = p.predictions(name, contract, inputs, settings, key)
            dependencies = barrier | {
                r.runtime["producer"] for g in inputs.values() for r in g if "producer" in r.runtime
            }
            output_keys = {port: [r.key for r in group] for port, group in predicted.items()}
            paths = {}
            for port, value in declaration.get("outputs", {}).items():
                if isinstance(value, dict):
                    continue
                if len(predicted[port]) != 1 or produced.get(port):
                    raise AutoSTACGISError(f"{label}.{port}: one explicit path requires exactly one output Item")
                path = str(p.app.path(value[5:]))
                if path in destinations:
                    raise AutoSTACGISError(f"Duplicate output path: {path}")
                destinations.add(path)
                paths[port] = path
                record = predicted[port][0]
                record.item["assets"][record.runtime["selected_asset"]]["href"] = path
                from .stac import Stac

                Stac.extensions(record.item)
            p.steps[key] = Step(
                key,
                name,
                input_keys,
                output_keys,
                settings,
                cost,
                tuple(sorted(dependencies)),
                label,
                paths,
                {
                    k: deepcopy(contract[k])
                    for k in ("outputs", "argument_requirements", "transfer", "checkpoint")
                    if k in contract
                },
            )
            p.parameter_templates[key] = (template, deepcopy(p.named_results))
            p.objects.update({r.key: r for g in predicted.values() for r in g})
            for port, keys in output_keys.items():
                produced.setdefault(port, []).extend(keys)
            consumed.update(r.key for r in inputs[anchor])
            stage_keys.add(key)
        if not stage_keys:
            raise NoPlan(f"Required step {label} ({name}) has no valid input binding")
        frontier = {k: r for k, r in frontier.items() if r.key not in consumed}
        for keys in produced.values():
            for key in keys:
                r = p.objects[key]
                frontier[(p._identity(r), r.kind, r.runtime["selected_asset"])] = r
        p.named_results[label] = produced
        p.required_steps.update(stage_keys)
        closure = p._closure([p.objects[k] for keys in produced.values() for k in keys])
        for key in closure - barrier - stage_keys:
            p.steps[key].dependencies = tuple(sorted(set(p.steps[key].dependencies) | barrier))
        barrier.update(closure)
        automatic = False
    p.workflow_barrier = barrier
    p.workflow_existing = set(barrier)
    p.modules = automatic_modules if automatic else {}
    p.leaves = frontier
    p.memo.clear()


def persist(record, destination, initial, app):
    """Materialize only explicitly named outputs and carry their durable STAC href."""
    from pathlib import Path

    path = Path(destination)
    from urllib.parse import unquote, urlsplit

    for source in initial.values():
        locations = [a.get("href", "") for a in source.item.get("assets", {}).values()]
        locations.extend(str(v) for v in source.runtime.get("local_assets", {}).values())
        for href in locations:
            uri = urlsplit(href)
            if uri.scheme not in ("", "file"):
                continue
            location = Path(unquote(uri.path) if uri.scheme == "file" else href).resolve()
            if location == path.resolve():
                raise AutoSTACGISError(f"Output path would overwrite an input: {path}")
    from .publication import publish

    suffix = path.suffix.lower()
    encoders = [
        name
        for name, definition in app.capabilities["encode"].items()
        if suffix in getattr(app.registry.resolve("encode", definition["implementation"]), "suffixes", [])
    ]
    if len(encoders) != 1:
        raise AutoSTACGISError(
            f"Explicit path requires exactly one encoder for {suffix}; use outputs.publish to select one"
        )
    encoder = encoders[0]
    return publish(
        app,
        record,
        {
            "encode": {"plugin": encoder},
            "data_out": {"plugin": "filesystem", "settings": {"path": str(path), "overwrite": True}},
        },
    )


def validate_steps(app, request, modules):
    """Check static step options before discovery; resolve metadata links in planning."""

    def has_reference(value):
        if isinstance(value, dict):
            return any(has_reference(v) for v in value.values())
        if isinstance(value, list):
            return any(has_reference(v) for v in value)
        return isinstance(value, str) and value.startswith("result:")

    for label, declaration in request.get("steps", {}).items():
        if declaration.get("auto"):
            continue
        name = declaration["plugin"]
        if name not in modules:
            raise AutoSTACGISError(f"{label}: unavailable processing plugin {name}")
        contract = modules[name]
        for field, allowed in (
            ("inputs", contract["argument_requirements"]),
            ("sequence", contract["argument_requirements"]),
            ("outputs", contract["outputs"]),
        ):
            unknown = set(declaration.get(field, {})) - set(allowed)
            if unknown:
                raise AutoSTACGISError(f"{label}: unknown {field} ports {sorted(unknown)}")
        for routing in declaration.get("sequence", {}).values():
            if routing is not None:
                for call in routing if isinstance(routing, list) else [routing]:
                    app.resolve_call("sequence", call)
        options = merge(contract.get("settings", {}), declaration.get("settings", {}))
        options = merge(options, {k[6:]: v for k, v in declaration.items() if k.startswith("param:")})
        if not has_reference(options):
            implementation = app.capabilities["process"][name]["implementation"]
            app.implementation_settings(name, "process", implementation, parameters(options, {}, {}, app))
