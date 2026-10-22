"""Create a small georeferenced dataset and its truthful STAC sidecars."""

import json
from pathlib import Path

import numpy as np

from autostacgis.data import ArrayRaster, Grid
from autostacgis.data.metadata import refresh_metadata
from autostacgis.data.writers import write_raster
from autostacgis.model import Record
from autostacgis.semantics import descriptor
from autostacgis.stac import Stac

root = Path(__file__).resolve().parent / "data"
root.mkdir(exist_ok=True)
for name, count, value, content in [
    ("ms", 3, 10, "multispectral_imagery"),
    ("pan", 1, 20, "panchromatic_imagery"),
]:
    grid = Grid(32, 32, count, "float32", (10, 0, 500000, 0, -10, 2400000), "EPSG:32604")
    payload = ArrayRaster(np.full(grid.shape, value, dtype="float32"), grid)
    path = root / f"{name}.tif"
    write_raster(payload, path)
    record = Record(
        {
            "type": "Feature",
            "stac_version": "1.1.0",
            "id": name,
            "geometry": None,
            "properties": {"datetime": "2026-01-01T00:00:00Z", "platform": "synthetic"},
            "links": [],
            "stac_extensions": [],
            "assets": {
                "data": {
                    "href": str(path),
                    "roles": ["data"],
                    **descriptor(content),
                    "unit": "1",
                    "cf:standard_name": "toa_bidirectional_reflectance",
                    "data_state:pansharpened": False,
                    "data_state:orthorectified": True,
                }
            },
        },
        payload=payload,
    )
    refresh_metadata(record)
    record.item["assets"]["data"]["href"] = str(path)
    stac = Stac()
    stac.extensions(record.item)
    stac.validate(record.item)
    Path(str(path) + ".stac.json").write_text(json.dumps(record.item, indent=2) + "\n")
print("Demo inputs:", root)
