from .errors import AutoSTACGISError
from .model import Record, digest
from .semantics import descriptor as asset_descriptor


def descriptor(settings, query, content, kind):
    area = query.get("area", settings.get("area"))
    if (
        not area
        or len(area) != 4
        or not (-180 <= area[0] < area[2] <= 180 and -90 <= area[1] < area[3] <= 90)
    ):
        raise AutoSTACGISError("Ancillary discovery needs a WGS84 area [west,south,east,north]")
    time = query.get("time_range", settings.get("time_range"))
    if not time or len(time) != 2:
        raise AutoSTACGISError("Ancillary discovery needs an explicit time_range")
    import json

    from shapely.geometry import box, mapping

    properties = {"datetime": None, "start_datetime": time[0], "end_datetime": time[1]}
    identifier = digest([content, area, time, settings])[:24]
    item = {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": identifier,
        "geometry": json.loads(json.dumps(mapping(box(*area)))),
        "bbox": area,
        "properties": properties,
        "links": [],
        "stac_extensions": [],
        "assets": {"data": {"href": f"autostacgis://discovery/{identifier}", **asset_descriptor(content)}},
    }
    return Record(item, kind, runtime={"area": area, "time_range": time})
