import numpy as np

from autostacgis import Record
from autostacgis.builtins import catalog
from autostacgis.data import ArrayRaster, Grid
from autostacgis.semantics import descriptor


def scene(identifier="ms", content="multispectral_imagery", *, value=10, count=3):
    item = {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": identifier,
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]},
        "bbox": [0, 0, 4, 4],
        "properties": {"datetime": "2024-01-01T00:00:00Z", "platform": "test"},
        "links": [],
        "stac_extensions": [],
        "assets": {
            "data": {
                "href": f"autostacgis://test/{identifier}",
                "roles": ["data"],
                **descriptor(content),
                "unit": "1",
                "proj:code": "EPSG:4326",
                "cf:standard_name": "toa_bidirectional_reflectance",
                "data_state:pansharpened": False,
                "data_state:georeferencing": "affine",
                "data_state:registration": [],
                "data_state:masked": {"cloud": False, "cloud_shadow": False, "mosaic_seamline": False},
                "data_state:orthorectified": True,
                "data_state:relative_radiometric_normalization": False,
            }
        },
    }
    grid = Grid(4, 4, count, "float32", (1, 0, 0, 0, -1, 4), "EPSG:4326")
    return Record(item, payload=ArrayRaster(np.full(grid.shape, value, dtype="float32"), grid))


def capabilities(config, name):
    return [p["capabilities"][name] for p in config["plugins"] if name in p["capabilities"]]


def subset(*names):
    config = catalog()
    by_id = {p["id"]: p for p in config["plugins"]}
    wanted = {p["id"] for p in config["plugins"] if "process" not in p["capabilities"]} | set(names)
    while True:
        dependencies = {d for name in wanted for d in by_id[name].get("requires", [])}
        if dependencies <= wanted:
            break
        wanted |= dependencies
    config["plugins"] = [p for p in config["plugins"] if p["id"] in wanted]
    config["runtime"]["executor"]["settings"]["workers"] = 2
    return config


def require_extra(module, distribution=None):
    """Run scientific integration tests only when their optional SDK is installed."""
    from importlib.metadata import PackageNotFoundError, version

    import pytest

    try:
        version(distribution or module)
    except PackageNotFoundError:
        pytest.skip(f"Optional integration requires {distribution or module}")
    return pytest.importorskip(module)
