"""Dotted paths, ordered inheritance, literal assignment, and removal."""

from copy import deepcopy

from .errors import AutoSTACGISError

MISSING = object()


def parts(path):
    result, current = [], []
    escaped = False
    for char in path:
        if escaped:
            if char not in (".", "\\"):
                raise AutoSTACGISError(f"Invalid path escape in {path!r}")
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            if not current:
                raise AutoSTACGISError(f"Empty path component in {path!r}")
            result.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped or not current:
        raise AutoSTACGISError(f"Invalid metadata path: {path!r}")
    return [*result, "".join(current)]


def get(data, path, default=MISSING):
    value = data
    for part in parts(path):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def assign(data, path, value):
    keys = parts(path)
    for key in keys[:-1]:
        if key not in data or data[key] is None:
            data[key] = {}
        if not isinstance(data[key], dict):
            raise AutoSTACGISError(f"Non-object parent in {path}")
        data = data[key]
    data[keys[-1]] = deepcopy(value)


def remove(data, path):
    """Remove a key/subtree; an absent path is already removed."""
    keys = parts(path)
    for key in keys[:-1]:
        if not isinstance(data, dict) or key not in data:
            return
        data = data[key]
    if isinstance(data, dict):
        data.pop(keys[-1], None)


def merge(left, right):
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = deepcopy(value)
    return left


def flatten(data, prefix=""):
    result = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            result.update(flatten(value, path))
        else:
            result[path] = value
    return result


def matches(item, expected):
    from .semantics import value_at

    unknown = False
    for path, expected_value in flatten(expected).items():
        actual = value_at(item, path)
        if actual is MISSING or actual is None:
            unknown = True
        elif actual != expected_value or (isinstance(actual, bool) != isinstance(expected_value, bool)):
            return "fail"
    return "unknown" if unknown else "pass"


def transform(inputs, output, settings=None):
    result = {}
    for reference in output.get("inherit_metadata", []):
        name, _, path = reference.partition(".")
        if name not in inputs:
            raise AutoSTACGISError(f"Unknown input {name}")
        value = get(inputs[name], path) if path else inputs[name]
        if value is MISSING:
            raise AutoSTACGISError(f"Missing inheritance path {reference}")
        if path:
            selected = {}
            assign(selected, path, value)
        else:
            selected = value
        merge(result, selected)
    for assignment in output.get("set", []):
        if len(assignment) != 1:
            raise AutoSTACGISError("set entries must contain exactly one path")
        path, value = next(iter(assignment.items()))
        assign(result, path, value)
    for assignment in output.get("append", []):
        path, value = next(iter(assignment.items()))
        if isinstance(value, dict) and set(value) == {"jsonata"}:
            from .expressions import evaluate

            value = evaluate(value["jsonata"], {"settings": settings or {}})
        previous = get(result, path, MISSING)
        keys = parts(path)
        asset_field = len(keys) == 3 and keys[0] == "assets"
        if previous is MISSING:
            previous = result.get("properties", {}).get(keys[2]) if asset_field else None
        values = value if isinstance(value, list) else [value]

        def appended(before):
            if before is not None and not isinstance(before, list):
                raise AutoSTACGISError(f"Cannot append to non-array metadata {path}")
            return [*(before or []), *values]

        assign(result, path, appended(previous))
        if asset_field:
            for band in result.get("assets", {}).get(keys[1], {}).get("bands", []):
                if keys[2] in band:
                    band[keys[2]] = appended(band[keys[2]])
    for path in output.get("remove", []):
        remove(result, path)
    return result


def output_metadata(inputs, output, index=0, settings=None):
    """Compose a named output from the selected members and asset metadata only."""
    each = output.get("each_from")
    selected = {
        name: records[output.get("member_index", index if name == each else 0)]
        for name, records in inputs.items()
    }
    result = {}
    destination = output.get("asset", "data")
    for reference in output.get("inherit_metadata", []):
        name = reference.partition(".")[0]
        inherited = transform(
            {key: record.item for key, record in selected.items()}, {"inherit_metadata": [reference]}
        )
        # Normalize only metadata the plugin actually inherited. Never restore an
        # input asset after a plugin starts empty or removes one of its fields.
        if "assets" in inherited:
            source = selected[name].runtime.get("selected_asset", "data")
            assets = inherited["assets"]
            inherited["assets"] = {destination: assets[source]} if source in assets else assets
        merge(result, inherited)
    return transform({"base": result}, {**output, "inherit_metadata": ["base"]}, settings)


def fulfills(actual, expected):
    """A prediction's null is unknown; execution may supply measured details."""
    if expected is None:
        return True
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and fulfills(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(fulfills(a, e) for a, e in zip(actual, expected, strict=True))
        )
    return type(actual) is type(expected) and actual == expected
