from ..data.metadata import finish
from ..errors import AutoSTACGISError


def merge_vectors(inputs, settings, context):
    from ..data import Batches

    records = inputs["vectors"]
    if any(record.payload.crs != records[0].payload.crs for record in records):
        raise AutoSTACGISError("Vector merge requires a common CRS")
    output = Batches(
        [batch for record in records for batch in record.payload.batches()],
        kind="vector",
        crs=records[0].payload.crs,
    )
    return {"result": [finish(context, output, records[0], {"assets.data.data_state:merged": True})]}


ID = "merge_vectors"
REQUIRES = ("gather-all",)
IMPLEMENTATIONS = {"process": merge_vectors}


merge_vectors.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["vectors"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:merged": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "kind": "vector",
            "geometry": "union",
        }
    },
    "anchor": "vectors",
    "settings": {},
    "argument_requirements": {
        "vectors": {
            "kind": "vector",
            "cardinality": "many",
            "require": {"all": []},
            "sequence": {"plugin": "gather-all"},
        }
    },
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
merge_vectors.contract['outputs']['result']["data_types"] = ['batches']
merge_vectors.contract['argument_requirements']['vectors']["data_types"] = ['batches']
