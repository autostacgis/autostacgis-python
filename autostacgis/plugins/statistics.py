import numpy as np

from ..data.metadata import finish


def statistics(inputs, settings, context):
    """Stable per-band streaming moments, respecting validity and finite values."""
    record = inputs["image"][0]
    count = np.zeros(record.payload.grid.count, dtype="int64")
    mean = np.zeros(count.shape, dtype="float64")
    m2 = np.zeros_like(mean)
    minimum = np.full_like(mean, np.inf)
    maximum = np.full_like(mean, -np.inf)
    for block in record.payload.blocks(tile_size=context.storage.tile_size):
        valid = np.isfinite(block.pixels)
        if block.valid is not None:
            valid &= block.valid
        for index in range(len(count)):
            values = block.pixels[index][valid[index]].astype("float64")
            n = values.size
            if not n:
                continue
            local_mean = values.mean()
            delta = local_mean - mean[index]
            combined = count[index] + n
            m2[index] += np.square(values - local_mean).sum() + delta * delta * count[index] * n / combined
            mean[index] += delta * n / combined
            count[index] = combined
            minimum[index] = min(minimum[index], values.min())
            maximum[index] = max(maximum[index], values.max())
    stats = [
        {
            "valid_percent": float(100 * n / (record.payload.grid.width * record.payload.grid.height)),
            "mean": float(mean[i]) if n else None,
            "stddev": float(np.sqrt(m2[i] / n)) if n else None,
            "minimum": float(minimum[i]) if n else None,
            "maximum": float(maximum[i]) if n else None,
        }
        for i, n in enumerate(count)
    ]
    result = finish(
        context,
        record.payload,
        record,
        {"assets.data.data_state:statistics_calculated": True},
    )
    for band, values in zip(result.item["assets"]["data"]["bands"], stats, strict=True):
        band["statistics"] = {key: value for key, value in values.items() if value is not None}
    return {"result": [result]}


ID = "statistics"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": statistics}


statistics.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.data_state:statistics_calculated": True},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {},
    "argument_requirements": {"image": {"kind": "raster", "cardinality": "one", "require": {"all": []}}},
    "input_requirements": [],
    "cost": 1,
    "version": "1.0.0",
}


# Exchange representations are independent of scientific metadata.
statistics.contract['outputs']['result']["data_types"] = ['raster']
statistics.contract['argument_requirements']['image']["data_types"] = ['raster']
