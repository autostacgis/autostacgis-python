"""Descriptions and conservative JSON Schema reflection shared by every client."""

import inspect
import re
import types
import typing
from copy import deepcopy

from .config import json_value, merge


def simple_type(annotation):
    if isinstance(annotation, str):
        annotation = {"str": str, "int": int, "float": float, "bool": bool, "None": type(None)}.get(
            annotation
        )
    primitive = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}
    try:
        if annotation in primitive:
            return {"type": primitive[annotation]}
    except TypeError:
        return None
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal and args and all(type(v) in (str, int, float, bool) for v in args):
        return {"enum": list(args)}
    if origin in (typing.Union, types.UnionType):
        children = [simple_type(a) for a in args]
        return {"anyOf": children} if all(children) else None
    if origin is list and len(args) == 1 and (child := simple_type(args[0])):
        return {"type": "array", "items": child}
    if origin is dict and args and args[0] is str and (child := simple_type(args[1])):
        return {"type": "object", "additionalProperties": child}
    return None


def parameter_descriptions(implementation, names):
    """Read common Google, NumPy and Sphinx parameter docstring forms."""
    result, current = {}, None
    for line in (inspect.getdoc(implementation) or "").splitlines():
        match = re.match(r"\s*(?::param\s+)?([A-Za-z_]\w*)(?:\s*\([^)]*\))?\s*:\s*(.*)", line)
        if match and match[1] in names:
            current = match[1]
            result[current] = "" if match[2] in ("int", "str", "float", "bool", "dict", "list") else match[2]
        elif current and line.startswith((" ", "\t")) and line.strip():
            result[current] = (result[current] + " " + line.strip()).strip()
        else:
            current = None
    return result


def parameters(implementation, defaults=None):
    contract = getattr(implementation, "contract", {})
    explicit = getattr(implementation, "settings_schema", contract.get("settings_schema"))
    defaults = merge(contract.get("settings", {}), defaults or {})
    if explicit:
        schema = deepcopy(explicit)
    else:
        properties = {}
        required = []
        try:
            signature = inspect.signature(implementation)
            annotations = inspect.get_annotations(
                implementation.__init__ if inspect.isclass(implementation) else implementation, eval_str=False
            )
        except (TypeError, ValueError, NameError):
            signature, annotations = None, {}
        if signature:
            for name, parameter in signature.parameters.items():
                if name in {"inputs", "settings", "context", "app", "records", "request", "services"}:
                    continue
                definition = simple_type(annotations.get(name))
                if definition is None:
                    continue
                if parameter.default is not inspect.Parameter.empty:
                    try:
                        json_value(parameter.default)
                        definition["default"] = parameter.default
                    except ValueError:
                        pass
                else:
                    required.append(name)
                properties[name] = definition
        schema = {"type": "object", "properties": properties, "additionalProperties": True}
        if required:
            schema["required"] = required
    descriptions = parameter_descriptions(implementation, schema.get("properties", {}))
    for name, definition in schema.get("properties", {}).items():
        if descriptions.get(name):
            definition.setdefault("description", descriptions[name])
        if name in defaults:
            definition.setdefault("default", deepcopy(defaults[name]))
        elif "default" in definition:
            defaults[name] = deepcopy(definition["default"])
    return defaults, schema


def inventory(registry):
    rows = []
    for name, capabilities in sorted(registry.plugins.items()):
        capability, implementation = next(iter(capabilities.items()))
        defaults, schema = parameters(implementation, registry.defaults[name].get(capability))
        rows.append(
            {
                "id": name,
                "version": registry.versions.get(name),
                "capability": capability,
                "description": registry.descriptions.get(name, ""),
                "requires": list(registry.requirements[name]),
                "defaults": defaults,
                "default_config": deepcopy(registry.config_defaults.get(name, {})),
                "settings_schema": schema,
                "contract": describe(getattr(implementation, "contract", {})),
            }
        )
    return rows


def describe(value):
    if callable(value):
        return {"python": value.__module__ + "." + value.__qualname__}
    if isinstance(value, dict):
        return {key: describe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [describe(child) for child in value]
    return value
