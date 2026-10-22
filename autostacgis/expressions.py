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
        return deepcopy(expression.evaluate(deepcopy(data)))
    except Exception as exc:
        raise AutoSTACGISError(f"JSONata expression {source!r} failed: {exc}") from exc


def _unsupported(*args):
    raise AutoSTACGISError("Planning expressions cannot use time, randomness, or $eval")
