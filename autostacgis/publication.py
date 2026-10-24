"""Compose encoders, destinations and metadata writers through declared capabilities."""

from .artifacts import accepted, artifact
from .errors import AutoSTACGISError
from .io import file_reference, is_local


def call(value):
    return {"plugin": value} if isinstance(value, str) else value


def publish(app, record, policy):
    if app.active_transfers is not None:
        return _publish(app, record, policy)
    from uuid import uuid4

    from .transfer import Transfers

    transfers = Transfers(
        app, app.catalog["defaults"]["data_transfer"], app.work / "publication" / uuid4().hex
    )
    app.active_transfers = transfers
    try:
        return _publish(app, record, policy)
    finally:
        app.active_transfers = None
        transfers.close()
        import shutil

        shutil.rmtree(transfers.directory, ignore_errors=True)


def _publish(app, record, policy):
    result = record.clone()
    policy = policy or {}
    data = policy.get("data_out")
    if data:
        destination = call(data)
        definition, settings = app.resolve_call("data_out", destination)
        writer = app.registry.resolve("data_out", definition["implementation"])
        asset_key = result.runtime.get("selected_asset", "data")
        encoded = artifact(result.payload)
        encoder = policy.get("encode")
        if encoder:
            enc_def, enc_settings = app.resolve_call("encode", call(encoder))
            function = app.registry.resolve("encode", enc_def["implementation"])
            accepted(encoded, function.input_types, owner=enc_def["id"])
            if app.active_transfers:
                result = app.active_transfers.record(result, {"data_types": function.input_types})
            encoded = function(result, enc_settings, app.scope(enc_def["id"], enc_settings))
            accepted(encoded, function.output_types, owner=enc_def["id"])
        accepted(encoded, writer.input_types, owner=definition["id"])
        metadata_hook = encoded.metadata_out
        if app.active_transfers:
            encoded = app.active_transfers.value(encoded, writer.input_types)
        saved = writer(encoded, settings, app.scope(definition["id"], settings))
        accepted(saved, writer.output_types, owner=definition["id"])
        href = str(saved.value)
        if saved.type == "path" and is_local(href):
            href = file_reference(href)
        result.item["assets"][asset_key]["href"] = href
        if encoded.media_type:
            result.item["assets"][asset_key]["type"] = encoded.media_type
        if is_local(href):
            result.runtime.setdefault("local_assets", {})[asset_key] = href
        else:
            result.runtime.get("local_assets", {}).pop(asset_key, None)
        if encoder and hasattr(function, "restore"):
            result.payload = function.restore(saved, result, enc_settings)
        app.stac.extensions(result.item)
        if metadata_hook:
            result.runtime["format_metadata_out"] = metadata_hook
            hook_definition, hook_settings = app.resolve_call("metadata_out", metadata_hook)
            hook = app.registry.resolve("metadata_out", hook_definition["implementation"])
            result.item = hook(result, hook_settings, app.scope(hook_definition["id"], hook_settings))
    for metadata in (
        policy.get("metadata_out", [])
        if isinstance(policy.get("metadata_out"), list)
        else [policy.get("metadata_out")]
    ):
        if not metadata:
            continue
        definition, settings = app.resolve_call("metadata_out", call(metadata))
        function = app.registry.resolve("metadata_out", definition["implementation"])
        updated = function(result, settings, app.scope(definition["id"], settings))
        if updated is not None:
            if not isinstance(updated, dict) or updated.get("type") != "Feature":
                raise AutoSTACGISError("metadata_out must return an updated STAC Item or None")
            result.item = updated
        app.stac.extensions(result.item)
        app.stac.validate(result.item)
    return result
