"""Inspectable processing contracts attached to ordinary Python functions."""

from math import isfinite

from .errors import AutoSTACGISError
from .expressions import evaluate
from .metadata import MISSING
from .semantics import value_at


def module(
    *,
    argument_requirements,
    outputs,
    input_requirements=(),
    cost=1,
    settings=None,
    version="1.0.0",
    cost_dependencies=(),
    **options,
):
    def decorate(function):
        function.contract = dict(
            argument_requirements=argument_requirements,
            outputs=outputs,
            input_requirements=list(input_requirements),
            cost=cost,
            settings=settings or {},
            version=version,
            cost_dependencies=list(cost_dependencies),
            **options,
        )
        return function

    return decorate


def value(record, path):
    if path == "$kind":
        return record.kind
    path = path.replace("$asset", record.runtime.get("selected_asset", "data"))
    return value_at(record.item, path)


def test(record, requirement):
    """Three-valued predicates: unknown evidence never satisfies a requirement."""
    if callable(requirement):
        answer = requirement(record)
        if answer not in (True, False, None):
            raise AutoSTACGISError("Custom predicates must return True, False, or None")
        return answer
    if not requirement:
        return True
    if "all" in requirement or "any" in requirement:
        op = "all" if "all" in requirement else "any"
        values = [test(record, r) for r in requirement[op]]
        if op == "all":
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    if "not" in requirement:
        answer = test(record, requirement["not"])
        return None if answer is None else not answer
    if "jsonata" in requirement:
        answer = evaluate(requirement["jsonata"], {"item": record.item, "kind": record.kind})
        return answer if type(answer) is bool else None
    if "path" not in requirement:
        return test(record, {"all": [{"path": p, "equals": v} for p, v in requirement.items()]})
    actual = value(record, requirement["path"])
    if "exists" in requirement:
        return (actual is not MISSING and actual is not None) == requirement["exists"]
    if actual is MISSING or actual is None:
        return None
    if "equals" in requirement:
        return type(actual) is type(requirement["equals"]) and actual == requirement["equals"]
    if "in" in requirement:
        return any(type(actual) is type(v) and actual == v for v in requirement["in"])
    if "contains" in requirement:
        return isinstance(actual, list) and requirement["contains"] in actual
    if "entry" in requirement:
        return isinstance(actual, list) and any(
            isinstance(v, dict) and all(v.get(k) == e for k, e in requirement["entry"].items())
            for v in actual
        )
    raise AutoSTACGISError(f"Unknown predicate: {requirement}")


def paths(requirement):
    if callable(requirement):
        return {"*"}
    if isinstance(requirement, list):
        return set().union(*(paths(r) for r in requirement)) if requirement else set()
    if not isinstance(requirement, dict):
        return set()
    if "path" in requirement:
        return {canonical(requirement["path"])}
    if "jsonata" in requirement:
        return {"*"}
    if set(requirement) & {"all", "any", "not"}:
        return set().union(*(paths(v) for v in requirement.values()))
    return {canonical(k) for k in requirement}


def canonical(path):
    parts = path.split(".")
    if len(parts) > 2 and parts[0] == "assets":
        parts[1] = "$asset"
    return ".".join(parts)


def cost_of(contract, inputs, settings):
    declaration = contract.get("cost", 1)
    if callable(declaration):
        result = declaration(inputs, settings)
    elif isinstance(declaration, dict):
        parameters = {}
        for key, reference in declaration.get("parameters", {}).items():
            port, _, path = reference.partition(".")
            values = [value(record, path) for record in inputs[port]]
            values = [None if v is MISSING else v for v in values]
            parameters[key] = values[0] if len(values) == 1 else values
        result = evaluate(
            declaration["jsonata"],
            {
                **parameters,
                "inputs": {k: [r.item for r in v] for k, v in inputs.items()},
                "settings": settings,
            },
        )
    else:
        result = declaration
    if type(result) not in (int, float) or not isfinite(result) or result < 0:
        raise AutoSTACGISError("Module costs must be finite nonnegative numbers")
    return result


def requirements_hold(app, owner, contract, inputs, settings):
    for port, declaration in contract["argument_requirements"].items():
        records = inputs[port]
        if not records or (declaration.get("cardinality", "one") == "one" and len(records) != 1):
            return False
        for record in records:
            if (
                declaration.get("kind", record.kind) != record.kind
                or test(record, declaration.get("require", {})) is not True
            ):
                return False
    for rule in contract.get("input_requirements", []):
        if callable(rule):
            if rule(inputs, settings) is not True:
                return False
        elif "plugin" in rule:
            mapped = {key: inputs[port] for key, port in rule["inputs"].items()}
            if app.scope(owner).call(rule["plugin"], "checks", mapped, rule.get("settings", {})) != "pass":
                return False
        elif "relation" in rule:
            groups = [inputs[p] for p in rule["ports"]]
            sets = [set().union(*(set(r.runtime.get("lineage", [r.key])) for r in group)) for group in groups]
            if rule["relation"] == "same_sources":
                # An asset lineage may include auxiliaries; source identities preserve acquisition membership.
                ids = [
                    {r.runtime.get("source_id", r.runtime.get("original_id", r.item["id"])) for r in group}
                    for group in groups
                ]
                if not (all(s == sets[0] for s in sets) or all(s == ids[0] for s in ids)):
                    return False
            elif rule["relation"] == "distinct":
                if any(
                    set(r.key for r in a) & set(r.key for r in b)
                    for i, a in enumerate(groups)
                    for b in groups[i + 1 :]
                ):
                    return False
            else:
                raise AutoSTACGISError(f"Unknown relation {rule['relation']}")
        else:
            raise AutoSTACGISError(f"Invalid input requirement: {rule}")
    return True
