"""Match an atmosphere observation to an image's acquisition day and footprint."""

from datetime import datetime


def atmosphere_for_image(inputs, settings, context):
    from shapely.geometry import shape

    image, atmosphere = inputs["image"][0], inputs["atmosphere"][0]
    acquired = image.item.get("properties", {}).get("datetime")
    properties = atmosphere.item.get("properties", {})
    sampled = properties.get("datetime") or properties.get("start_datetime")
    if not acquired or not sampled or not image.item.get("geometry") or not atmosphere.item.get("geometry"):
        return "unknown"

    def day(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()

    return (
        "pass"
        if day(acquired) == day(sampled)
        and shape(atmosphere.item["geometry"]).covers(shape(image.item["geometry"]))
        else "fail"
    )


ID = "atmosphere-for-image"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": atmosphere_for_image}
