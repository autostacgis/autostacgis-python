"""Deterministic, bounded JSONata expressions over declared JSON inputs."""

from copy import deepcopy

import jsonata

from .errors import AutoSTACGISError


def evaluate(source, data):
    try:
        expression = jsonata.Jsonata(source, timeout=1000, stack=100)
        # Planning expressions must be repeatable; no clock, randomness, or dynamic code.
        for name in ("now", "millis", "random", "eval"):
            expression.register_function(name, jsonata.Jsonata.JLambda(_unsupported))
        from .spatial import covers, intersects, within

        for name, function in (("covers", covers), ("intersects", intersects), ("within", within)):
            expression.register_function(name, jsonata.Jsonata.JLambda(function))
        return _json_arrays(expression.evaluate(_json_arrays(data)))
    except Exception as exc:
        raise AutoSTACGISError(f"JSONata expression {source!r} failed: {exc}") from exc


def _unsupported(*args):
    raise AutoSTACGISError("Planning expressions cannot use time, randomness, or $eval")


def _json_arrays(value):
    """GeoJSON producers may use tuples; JSONata accepts ordinary JSON arrays."""
    if isinstance(value, dict):
        return {key: _json_arrays(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_arrays(child) for child in value]
    return deepcopy(value)
