import numpy as np

from ..data import vectors
from ..data.metadata import finish
from ..semantics import descriptor_facts


def extract_footprints(inputs, settings, context):
    from affine import Affine
    from rasterio.features import shapes
    from shapely import union_all
    from shapely.geometry import shape

    from .common_crs import require_common_crs

    require_common_crs(inputs, names=["images"])
    records = inputs.get("images", inputs.get("image"))
    geometries, identifiers = [], []
    for record in records:
        parts = []
        raster = record.payload
        for block in raster.blocks(tile_size=context.storage.tile_size):
            valid = (
                np.ones(block.pixels.shape[1:], dtype="uint8")
                if block.valid is None
                else np.all(np.broadcast_to(block.valid, block.pixels.shape), axis=0).astype("uint8")
            )
            transform = Affine(*raster.grid.transform) * Affine.translation(block.window.x, block.window.y)
            parts.extend(
                shape(geometry)
                for geometry, value in shapes(valid, mask=valid > 0, transform=transform)
                if value
            )
        geometries.append(union_all(parts))
        identifiers.append(
            record.runtime.get(
                "source_id",
                record.runtime.get(
                    "original_id", record.item.get("properties", {}).get("autostacgis:source_id", record.item["id"])
                ),
            )
        )
    attributes = {"image": identifiers, "area": [geometry.area for geometry in geometries]}
    from ..metadata import get

    contexts = [
        {
            **record.item.get("properties", {}),
            **record.item.get("assets", {}).get(record.runtime.get("selected_asset", "data"), {}),
        }
        for record in records
    ]
    context_keys = sorted(
        {
            key
            for metadata in contexts
            for key in metadata
            if key.startswith("view:")
            or key
            in ("datetime", "start_datetime", "end_datetime", "platform", "instruments", "constellation")
        }
    )
    for name in context_keys:
        attributes[name] = [metadata.get(name) for metadata in contexts]
    for name, path in settings.get("fields", {}).items():
        attributes[name] = [get(record.item, path, None) for record in records]
    output = vectors(geometries, crs=records[0].payload.grid.crs, attributes=attributes)
    result = finish(context, output, records[0], descriptor_facts("image_footprints"))
    # Keep shared context at Item level; differing observations remain per feature.
    for name in context_keys:
        values = [metadata.get(name) for metadata in contexts]
        if any(value != values[0] for value in values[1:]):
            result.item["properties"].pop(name, None)
            for asset in result.item["assets"].values():
                asset.pop(name, None)
    if not result.item["properties"].get("datetime"):
        from datetime import datetime, timezone

        bounds = [
            (
                metadata.get("datetime") or metadata.get("start_datetime"),
                metadata.get("datetime") or metadata.get("end_datetime"),
            )
            for metadata in contexts
        ]
        if all(start and end for start, end in bounds):

            def utc(value):
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

            result.item["properties"].update(
                datetime=None,
                start_datetime=min(utc(start) for start, _ in bounds).isoformat(),
                end_datetime=max(utc(end) for _, end in bounds).isoformat(),
            )
    return {"result": [result]}


def bind_inputs(records, settings, context):
    return context.call("gather-all", "sequence", records, settings)


ID = "extract_footprints"
REQUIRES = ("common-crs", "storage", "gather-all")
IMPLEMENTATIONS = {"process": extract_footprints, "sequence": bind_inputs}


extract_footprints.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["images"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.roles": ["data", "image-footprints"]},
            ],
            "remove": [
                "properties.product:type",
                "assets.data.product:type",
                "properties.cf:standard_name",
                "properties.unit",
                "properties.bands",
                "properties.data_type",
                "properties.nodata",
                "properties.statistics",
                "properties.raster:scale",
                "properties.raster:offset",
                "properties.raster:histogram",
                "properties.data_state:spectral_sampling",
                "properties.data_state:radiometry",
                "properties.data_state:atmospheric_correction",
                "assets.data.cf:standard_name",
                "assets.data.unit",
                "assets.data.bands",
                "assets.data.data_type",
                "assets.data.nodata",
                "assets.data.statistics",
                "assets.data.raster:scale",
                "assets.data.raster:offset",
                "assets.data.raster:histogram",
                "assets.data.data_state:spectral_sampling",
                "assets.data.data_state:radiometry",
                "assets.data.data_state:atmospheric_correction",
            ],
            "kind": "vector",
            "geometry": "union",
        }
    },
    "anchor": "images",
    "settings": {},
    "argument_requirements": {
        "images": {
            "kind": "raster",
            "cardinality": "many",
            "require": {"all": [{"path": "assets.$asset.data_state:georeferencing", "equals": "affine"}]},
            "sequence": {"plugin": "gather-all"},
        }
    },
    "input_requirements": [
        {
            "plugin": "common-crs",
            "observes": ["assets.$asset.proj:code", "assets.$asset.proj:wkt2", "assets.$asset.proj:projjson"],
            "inputs": {"images": "images"},
        }
    ],
    "cost": 1,
    "version": "1.0.0",
}

extract_footprints.contract["argument_requirements"]["images"]["sequence"]["settings"] = {
    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"}
}


# Exchange representations are independent of scientific metadata.
extract_footprints.contract['outputs']['result']["data_types"] = ['batches']
extract_footprints.contract['argument_requirements']['images']["data_types"] = ['raster']
