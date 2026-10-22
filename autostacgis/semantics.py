"""Independent STAC descriptors and CF quantities used by the bundled EO domain."""

QUANTITIES = {
    "radiance": "toa_outgoing_radiance_per_unit_wavelength",
    "toa_reflectance": "toa_bidirectional_reflectance",
    "surface_reflectance": "surface_bidirectional_reflectance",
}
DESCRIPTORS = {
    "registration_reference": ("roles", "contains", "registration-reference"),
    "multispectral_imagery": ("data_state:spectral_sampling", "equals", "multispectral"),
    "panchromatic_imagery": ("data_state:spectral_sampling", "equals", "panchromatic"),
    "digital_elevation_model": ("cf:standard_name", "equals", "surface_altitude"),
    **{
        name: ("roles", "contains", role)
        for name, role in {
            "cloud_mask": "cloud-mask",
            "image_footprints": "image-footprints",
            "mosaic_seamline_polygons": "mosaic-seamline-polygons",
            "spectral_index": "spectral-index",
            "classification_polygons": "classification",
            "atmosphere": "atmosphere",
            "point_cloud": "point-cloud",
            "lidar_return_intensity": "return-intensity",
        }.items()
    },
}


def descriptor(name):
    field, comparison, value = DESCRIPTORS[name]
    result = {"roles": ["data"]}
    if comparison == "contains":
        result["roles"].append(value)
    else:
        result[field] = value
    if name in ("multispectral_imagery", "panchromatic_imagery"):
        result["data_state:modality"] = "optical"
    if name == "digital_elevation_model":
        result["unit"] = "m"
    return result


def descriptor_facts(name):
    return {"assets.data." + key: value for key, value in descriptor(name).items()}


def value_at(item, path):
    """Read effective STAC values; heterogeneous bands are unknown to an asset goal."""
    from .metadata import MISSING, get, parts

    keys = parts(path)
    if len(keys) < 3 or keys[0] != "assets":
        return get(item, path)
    asset = item.get("assets", {}).get(keys[1], {})
    key = keys[2]
    if key not in ("cf:standard_name", "unit", "raster:scale", "raster:offset") and not key.startswith(
        "data_state:"
    ):
        return get(item, path)
    default = asset.get(key, item.get("properties", {}).get(key, MISSING))

    def nested(value):
        for part in keys[3:]:
            if not isinstance(value, dict):
                return MISSING
            value = value.get(part, MISSING)
        return value

    values = [nested(band.get(key, default)) for band in asset.get("bands", [])]
    if not values:
        return nested(default)
    return values[0] if all(value == values[0] for value in values) else MISSING


def field_value(item, field, path):
    value = value_at(item, path)
    if "entry" in field:
        return isinstance(value, list) and any(
            isinstance(entry, dict) and all(entry.get(k) == v for k, v in field["entry"].items())
            for entry in value
        )
    return value
