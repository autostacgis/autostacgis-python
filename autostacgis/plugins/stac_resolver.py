"""Discover ordinary local files and their sidecars as complete planning records."""

from ..local_discovery import LocalFiles
from ..raster_metadata import inspect_raster


class LocalStac:
    def discover(self, settings, query, context):
        from jsonschema import ValidationError

        from ..metadata import MISSING, get

        for record in LocalFiles().discover(settings, query, context):
            try:
                context.stac.validate(record.item)
                complete = all(
                    get(record.item, field) not in (MISSING, None)
                    for field in settings.get("required_fields", [])
                )
            except ValidationError:
                complete = False
            if not complete:
                record = inspect_raster(record, settings, context)
            yield record


ID = "stac-resolver"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_in": LocalStac}
DEFAULTS = {"metadata_in": {"search_glob": "data/*.tif"}}
