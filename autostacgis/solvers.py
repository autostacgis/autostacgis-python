"""Solver contracts and solver-specific goal expansion through named dependencies."""

from copy import deepcopy

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .config import json_value
from .dependencies import require
from .errors import AutoSTACGISError


def validate_parameters(schema, value, owner, *, partial=False):
    json_value(value)
    if partial:
        # Catalog defaults may omit arguments supplied by an invocation. Validate
        # individual fields now, and apply cross-parameter rules at that invocation.
        schema = {
            key: definition
            for key, definition in schema.items()
            if key
            not in ("required", "oneOf", "anyOf", "allOf", "not", "dependentRequired", "dependentSchemas")
        }
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        raise AutoSTACGISError(f"{owner}: {exc.json_path}: {exc.message}") from None
    return value


def validate_contract(name, capability, implementation, requires):
    contract = getattr(implementation, "contract", None)
    if not isinstance(contract, dict):
        raise AutoSTACGISError(f"{name}: {capability} plugins must declare an explicit contract")
    json_value(contract)
    schema = contract.get("settings_schema")
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
        or schema.get("additionalProperties") is not False
    ):
        raise AutoSTACGISError(
            f"{name}: settings_schema must explicitly name parameters and reject unknown ones"
        )
    try:
        Draft202012Validator.check_schema(schema)
        if not set(schema.get("required", ())) <= set(schema["properties"]):
            raise AutoSTACGISError(f"{name}: every required parameter must be explicitly declared")
        if capability == "solver":
            if not isinstance(contract.get("goal_schema"), dict) or not any(
                key in contract["goal_schema"] for key in ("properties", "$ref", "oneOf", "anyOf", "allOf")
            ):
                raise AutoSTACGISError(f"{name}: solver contract must declare goal_schema")
            Draft202012Validator.check_schema(contract["goal_schema"])
        else:
            solver = contract.get("solver")
            if not isinstance(solver, str) or not solver or solver not in requires:
                raise AutoSTACGISError(f"{name}: goal contract must name its solver in REQUIRES")
    except SchemaError as exc:
        raise AutoSTACGISError(f"{name}: invalid contract schema: {exc.message}") from None
    validate_parameters(schema, contract.get("settings", {}), name, partial=True)


def expand_goal(goal, records, app, active=()):
    """Expand only explicitly selected goal plugins; solver parameters remain solver-owned."""
    if not isinstance(goal, dict):
        raise AutoSTACGISError("Goal parameters must be an object")
    if "plugin" in goal:
        if set(goal) - {"plugin", "settings"}:
            raise AutoSTACGISError("Goal plugin calls accept only plugin and settings")
        name = goal["plugin"]
        if not isinstance(name, str) or not name or not isinstance(goal.get("settings", {}), dict):
            raise AutoSTACGISError("Goal plugin calls require a plugin name and an object of settings")
        if name in active:
            raise AutoSTACGISError("Recursive goal plugin: " + " -> ".join((*active, name)))
        if active:
            require(app.plugins, app.registry, active[-1], name)
        definition, settings = app.resolve_call("goal", goal)
        implementation = app.registry.resolve("goal", definition["implementation"])
        solver = implementation.contract["solver"]
        if solver != app.solver_name:
            raise AutoSTACGISError(
                f"Goal {name} requires solver {solver}; active solver is {app.solver_name}"
            )
        require(app.plugins, app.registry, name, solver)
        expanded = implementation(settings, records, app.scope(name, settings))
        json_value(expanded)
        return expand_goal(expanded, records, app, (*active, name))
    result = deepcopy(goal)
    for operation in ("all_of", "any_of"):
        if operation in result:
            if not isinstance(result[operation], list) or not result[operation]:
                raise AutoSTACGISError(f"{operation} requires a nonempty array of goals")
            result[operation] = [expand_goal(child, records, app, active) for child in result[operation]]
    return result
