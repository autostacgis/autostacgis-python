"""Named result saving, separate from scientific goals and plugin parameters."""

from copy import deepcopy

from .config import merge, schema
from .dependencies import configured_plugins
from .errors import AutoSTACGISError
from .expressions import evaluate


def result_options(contract, overrides=None):
    """Validate user overrides without exposing scientific output claims as settings."""
    from jsonschema import Draft202012Validator

    outputs = contract.get("outputs", {})
    overrides = overrides or {}
    unknown = set(overrides) - set(outputs)
    if unknown:
        raise AutoSTACGISError("Unknown result ports: " + ", ".join(sorted(unknown)))
    validator = Draft202012Validator(schema("result"))
    for options in overrides.values():
        validator.validate(options)
    return {port: merge(policy, overrides.get(port, {})) for port, policy in outputs.items()}


def saving_plugins(outputs):
    """Load destination dependencies only when saving that result is enabled."""
    return set().union(
        *(configured_plugins(policy) for policy in outputs.values() if policy.get("save", False))
    )


def has_expression(value):
    if isinstance(value, dict):
        return set(value) == {"jsonata"} or any(has_expression(child) for child in value.values())
    return isinstance(value, list) and any(has_expression(child) for child in value)


def evaluate_policy(policy, record):
    """JSONata settings see the completed Item and its selected asset."""
    key = record.runtime.get("selected_asset", "data")
    variables = {
        "item": record.item,
        "asset": record.item.get("assets", {}).get(key, {}),
        "asset_key": key,
        "kind": record.kind,
        "source_id": record.runtime.get("source_id", record.item["id"]),
        "source_asset": record.runtime.get("source_asset", key),
    }

    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {"jsonata"}:
                return evaluate(value["jsonata"], variables)
            return {key: resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child) for child in value]
        return deepcopy(value)

    return {
        key: resolve(value) for key, value in policy.items() if key in ("encode", "data_out", "metadata_out")
    }


def save_result(app, record, policy):
    if not policy.get("save", False):
        return record
    from .publication import publish

    saved = publish(app, record, evaluate_policy(policy, record))
    if policy.get("data_out"):
        saved.runtime["saved"] = True
    return saved
