"""Return an updated in-memory Item without choosing a metadata file location."""

from copy import deepcopy

from ..metadata import assign


def stac_item(record, settings, context):
    item = deepcopy(record.item)
    for path, value in settings.get("set", {}).items():
        assign(item, path, value)
    context.stac.validate(item)
    return item


ID = "stac-item"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_out": stac_item}
