"""Shared-CRS check for operations that combine coordinates directly."""

from ..errors import AutoSTACGISError


def common_crs(inputs, settings, context):
    from pyproj import CRS

    values = []
    for records in inputs.values():
        for record in records:
            payload = record.payload
            if payload is not None:
                value = payload.grid.crs if payload.kind == "raster" else payload.crs
            else:
                asset = record.item.get("assets", {}).get(record.runtime.get("selected_asset", "data"), {})
                metadata = {**record.item.get("properties", {}), **asset}
                value = (
                    metadata.get("proj:code") or metadata.get("proj:wkt2") or metadata.get("proj:projjson")
                )
            if not value:
                return "unknown"
            crs = CRS.from_user_input(value)
            if settings.get("projected") and not crs.is_projected:
                return "fail"
            values.append(crs)
    return "unknown" if not values else "pass" if all(v == values[0] for v in values) else "fail"


def require_common_crs(inputs, *, names):
    status = common_crs({name: inputs[name] for name in names}, {}, None)
    if status != "pass":
        raise AutoSTACGISError(f"Common CRS required for {', '.join(names)} ({status}); align the inputs first")


ID = "common-crs"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": common_crs}
