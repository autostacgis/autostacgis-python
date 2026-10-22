"""Save a whole Item or selected STAC value, retaining the destination's actual reference."""

import json
from copy import deepcopy
from urllib.parse import urlsplit

from ..artifacts import Artifact
from ..errors import AutoSTACGISError
from ..metadata import MISSING, assign, get
from ..model import digest


def json_writer(record, settings, context):
    item = deepcopy(record.item)
    if any(a.get("href", "").startswith("autostacgis:") for a in item.get("assets", {}).values()):
        raise AutoSTACGISError("Save runtime data before publishing metadata")
    context.stac.validate(item)
    path = context.path(
        settings.get(
            "path",
            str(
                context.path(settings.get("directory", "output/stac"))
                / (digest([item.get("collection"), item["id"]])[:24] + ".json")
            ),
        )
    )
    destination = settings.get("data_out", {"plugin": "filesystem"})
    options = {"path": str(path), "overwrite": True, **destination.get("settings", {})}

    def encode():
        value = get(item, settings["value_path"]) if settings.get("value_path") else item
        if value is MISSING:
            raise AutoSTACGISError("Metadata value_path does not exist")
        return Artifact(
            "bytes", (json.dumps(value, indent=2, allow_nan=False) + "\n").encode(), "application/json"
        )

    saved = context.call(destination["plugin"], "data_out", encode(), options)
    if settings.get("href_field"):
        assign(item, settings["href_field"], str(saved.value))
        # Local JSON can retain its own reference. For remote sinks the returned Item
        # carries the actual saved reference, without inventing a destination URL.
        if saved.type == "path" and urlsplit(str(saved.value)).scheme in ("", "file"):
            context.call(
                destination["plugin"],
                "data_out",
                encode(),
                {**options, "path": str(saved.value), "overwrite": True},
            )
    return item


ID = "stac-json"
REQUIRES = ("filesystem",)
IMPLEMENTATIONS = {"metadata_out": json_writer}
DEFAULTS = {"metadata_out": {"directory": "output/stac"}}
