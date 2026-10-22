from ..data import vectors
from ..data.metadata import finish


def clean_footprints(inputs, settings, context):
    import inspect

    import geopandas as gpd
    from shapely import from_wkb
    from spectralmatch import postprocess_footprints

    record = inputs["vector"][0]
    rows = [row for batch in record.payload.batches() for row in batch.to_pylist()]
    geometries = from_wkb([row.pop("geometry") for row in rows])
    context.directory.mkdir(parents=True, exist_ok=True)
    source, target = context.directory / "footprints.gpkg", context.directory / "cleaned.gpkg"
    gpd.GeoDataFrame(rows, geometry=geometries, crs=record.payload.crs).to_file(
        source, layer="footprints", driver="GPKG"
    )
    options = {
        **settings,
        "input_layer": "footprints",
        "output_layer": "footprints",
        "resume_from_outputs": "no",
    }
    signature = inspect.signature(postprocess_footprints)
    if "executor" in signature.parameters:
        options["executor"] = context.executor
    if "image_threads" in signature.parameters:
        options["image_threads"] = None
    postprocess_footprints(input_polygons=str(source), output_polygons=str(target), **options)
    frame = gpd.read_file(target, layer="footprints")
    output = vectors(
        list(frame.geometry),
        crs=frame.crs.to_string(),
        attributes={key: frame[key].tolist() for key in frame.columns if key != "geometry"},
    )
    return {"result": [finish(context, output, record, {"assets.data.data_state:cleaned": True})]}


ID = "clean_footprints"
REQUIRES = ("executor", "common-crs")
IMPLEMENTATIONS = {"process": clean_footprints}


clean_footprints.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["vector"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:cleaned": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "kind": "vector",
        }
    },
    "anchor": "vector",
    "settings": {},
    "argument_requirements": {
        "vector": {
            "kind": "vector",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.roles", "contains": "image-footprints"}]},
        }
    },
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


clean_footprints.settings_schema = {
    "type": "object",
    "properties": {
        **{
            key: {"type": "number", "minimum": 0}
            for key in (
                "hole_edge_distance",
                "hole_to_hole_distance",
                "simplify_smoothing_radius",
                "simplify_tolerance",
            )
        },
        **{
            key: {"type": ["number", "null"], "minimum": 0}
            for key in ("hole_relative_edge_distance", "filter_area_size")
        },
        "hole_cut_width": {
            "oneOf": [{"type": "integer", "minimum": 1}, {"enum": ["hole_size", "maximum_inscribed_circle"]}]
        },
        "hole_cut_method": {"enum": ["corridor", "buffer"]},
        "simplify_area_weight": {"type": "number", "minimum": 0, "maximum": 1},
        "filter_area_rank": {"type": ["integer", "null"]},
        "debug_logs": {"type": "boolean"},
    },
    "additionalProperties": False,
}


clean_footprints.contract["input_requirements"] = [
    {
        "plugin": "common-crs",
        "inputs": {"vector": "vector"},
        "settings": {"projected": True},
        "observes": ["assets.$asset.proj:code", "assets.$asset.proj:wkt2", "assets.$asset.proj:projjson"],
    }
]


# Exchange representations are independent of scientific metadata.
clean_footprints.contract['outputs']['result']["data_types"] = ['batches']
clean_footprints.contract['argument_requirements']['vector']["data_types"] = ['batches']
