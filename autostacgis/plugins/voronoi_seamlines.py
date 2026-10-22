import inspect

from ..data import vectors
from ..data.metadata import finish
from ..data.writers import vrt_paths
from ..errors import AutoSTACGISError
from ..semantics import descriptor_facts


def voronoi_seamlines(inputs, settings, context):
    import geopandas as gpd
    from osgeo import ogr
    from shapely import from_wkb
    from spectralmatch import voronoi_center_seamline

    footprints = inputs["footprints"][0]
    path = context.directory / "footprints.gpkg"
    # Algorithm matching uses input basenames, while public metadata uses STAC ids.
    images = inputs.get("images", [])
    names = {
        record.runtime.get(
            "source_id",
            record.runtime.get(
                "original_id", record.item.get("properties", {}).get("autostacgis:source_id", record.item["id"])
            ),
        ): f"input-{index}"
        for index, record in enumerate(images)
    }
    original_names = {alias: source for source, alias in names.items()}
    geometries = []
    attributes = {name: [] for name in footprints.payload.schema.names if name != "geometry"}
    for batch in footprints.payload.batches():
        geometries.extend(from_wkb(batch.column("geometry").to_pylist()))
        for field in attributes:
            values = batch.column(field).to_pylist()
            attributes[field].extend(
                [names.get(value, value) for value in values] if field == "image" else values
            )
    source_names = [original_names.get(value, value) for value in attributes.get("image", [])]
    if images and set(source_names) != set(original_names.values()):
        raise AutoSTACGISError("Footprint image identities do not match the seamline input collection")
    gpd.GeoDataFrame(attributes, geometry=geometries, crs=footprints.payload.crs).to_file(
        path, layer="footprints", driver="GPKG"
    )
    target = context.directory / "seamlines.gpkg"
    options = {**settings, "input_polygons": str(path), "output_mask": str(target), "input_layer": None}
    signature = inspect.signature(voronoi_center_seamline)
    if "images" in inputs and "input_images" in signature.parameters:
        options["input_images"] = vrt_paths(
            (record.payload for record in images), context.storage, context.directory
        )
    if "executor" in signature.parameters:
        options["executor"] = context.executor
    for key, value in [("image_threads", None), ("io_threads", 1), ("tile_threads", 1)]:
        if key in signature.parameters:
            options[key] = value
    voronoi_center_seamline(**options)
    dataset = ogr.Open(str(target))
    if dataset is None:
        raise AutoSTACGISError("voronoi_center_seamline did not produce seamlines")
    layer = dataset.GetLayer(0)
    crs = layer.GetSpatialRef().ExportToWkt() if layer.GetSpatialRef() else footprints.payload.crs
    geometries, attributes = [], {}
    names = [
        layer.GetLayerDefn().GetFieldDefn(i).GetName() for i in range(layer.GetLayerDefn().GetFieldCount())
    ]
    attributes = {name: [] for name in names}
    for feature in layer:
        if feature.GetGeometryRef() is None:
            continue
        geometries.append(from_wkb(bytes(feature.GetGeometryRef().ExportToWkb())))
        for field in names:
            value = feature.GetField(field)
            attributes[field].append(original_names.get(value, value) if field == "image" else value)
    return {
        "result": [
            finish(
                context,
                vectors(geometries, crs=crs, attributes=attributes),
                footprints,
                {
                    **descriptor_facts("mosaic_seamline_polygons"),
                    "assets.data.autostacgis:seamline_sources": source_names,
                },
            )
        ]
    }


ID = "voronoi_seamlines"
REQUIRES = ("storage", "executor")
IMPLEMENTATIONS = {"process": voronoi_seamlines}


voronoi_seamlines.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["footprints"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.roles": ["data", "mosaic-seamline-polygons"]},
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
    "anchor": "footprints",
    "settings": {},
    "argument_requirements": {
        "footprints": {
            "kind": "vector",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.roles", "contains": "image-footprints"},
                    {"path": "assets.$asset.data_state:cleaned", "equals": True},
                ]
            },
        }
    },
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
voronoi_seamlines.contract['outputs']['result']["data_types"] = ['batches']
voronoi_seamlines.contract['argument_requirements']['footprints']["data_types"] = ['batches']
