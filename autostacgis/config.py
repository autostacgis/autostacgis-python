"""One strict JSON data model for JSON and YAML documents."""

import json
import math
from copy import deepcopy
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .errors import AutoSTACGISError


class Loader(yaml.SafeLoader):
    pass


Loader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in values if tag != "tag:yaml.org,2002:timestamp"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise AutoSTACGISError(f"Duplicate or non-string key: {key!r}")
        result[key] = value
    return result


def construct(loader, node):
    return unique([(loader.construct_object(k), loader.construct_object(v)) for k, v in node.value])


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)


def json_value(value, active=None):
    active = set() if active is None else active
    if isinstance(value, (dict, list)):
        if id(value) in active:
            raise AutoSTACGISError("Recursive JSON/YAML value")
        active.add(id(value))
        if isinstance(value, dict):
            if any(not isinstance(k, str) for k in value):
                raise AutoSTACGISError("JSON keys must be strings")
            for child in value.values():
                json_value(child, active)
        else:
            for child in value:
                json_value(child, active)
        active.remove(id(value))
    elif type(value) not in (str, int, float, bool, type(None)):
        raise AutoSTACGISError(f"Non-JSON value: {type(value).__name__}")
    elif isinstance(value, float) and not math.isfinite(value):
        raise AutoSTACGISError("Non-finite JSON number")
    return value


def _read(path):
    path = Path(path)
    raw = path.read_text()
    data = (
        json.loads(raw, object_pairs_hook=unique)
        if path.suffix.lower() == ".json"
        else yaml.load(raw, Loader=Loader)
    )
    return json_value(data)


def read(path):
    """Expand local $import directives before interpreting either JSON or YAML."""

    def load(filename, active):
        filename = Path(filename).resolve()
        if filename in active:
            raise AutoSTACGISError(f"Recursive config import: {filename}")
        return expand(_read(filename), filename.parent, (*active, filename))

    def pointer(document, fragment):
        if not fragment:
            return document
        if not fragment.startswith("/"):
            raise AutoSTACGISError("Import fragment must be a JSON Pointer")
        try:
            for part in fragment[1:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                document = document[int(part)] if isinstance(document, list) else document[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise AutoSTACGISError(f"Unknown import pointer: {fragment}") from None
        return deepcopy(document)

    def expand(value, directory, active):
        if isinstance(value, list):
            return [expand(v, directory, active) for v in value]
        if not isinstance(value, dict):
            return value
        local = {k: expand(v, directory, active) for k, v in value.items() if k != "$import"}
        if "$import" not in value:
            return local
        refs = value["$import"]
        refs = refs if isinstance(refs, list) else [refs]
        if not refs:
            raise AutoSTACGISError("$import must not be empty")
        missing = object()
        imported = missing
        for ref in refs:
            if not isinstance(ref, str) or not ref.partition("#")[0]:
                raise AutoSTACGISError("$import requires local file paths with optional #/JSON/pointer")
            filename, _, fragment = ref.partition("#")
            selected = pointer(load(directory / filename, active), fragment)
            if imported is missing:
                imported = selected
            elif isinstance(imported, dict) and isinstance(selected, dict):
                imported = merge(imported, selected)
            else:
                raise AutoSTACGISError("Multiple imports must select objects")
        if local:
            if not isinstance(imported, dict):
                raise AutoSTACGISError("Import siblings require an imported object")
            imported = merge(imported, local)
        return imported

    return json_value(load(path, ()))


def merge(defaults, overrides):
    """Recursively override objects; arrays and scalar values replace completely."""
    if "plugin" in overrides and "plugin" in defaults and overrides["plugin"] != defaults["plugin"]:
        return deepcopy(overrides)
    result = deepcopy(defaults)
    for key, value in overrides.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else deepcopy(value)
        )
    return result


def schema(name):
    return read(Path(__file__).parent / "spec" / "schemas" / f"{name}.json")


def validate(data, name):
    json_value(data)
    resources = [read(path) for path in (Path(__file__).parent / "spec/schemas").glob("*.json")]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in resources)
    errors = sorted(
        Draft202012Validator(schema(name), registry=registry).iter_errors(data), key=lambda e: str(e.path)
    )
    if errors:
        raise AutoSTACGISError(f"{name}: {errors[0].json_path}: {errors[0].message}")
    return deepcopy(data)
