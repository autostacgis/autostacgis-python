import pytest
from helpers import scene

from autostacgis import Registry
from autostacgis.executor import Context
from autostacgis.runtime.registry import Services


@pytest.fixture
def context(tmp_path):
    definitions = {
        "executor": {"implementation": "threads", "settings": {"workers": 2}},
        "storage": {"implementation": "storage", "settings": {"tile_size": 2}},
        "events": {"implementation": "events", "settings": {}},
    }
    with Services(Registry(), definitions, directory=tmp_path) as services:
        yield Context(
            services,
            tmp_path / "step",
            {"result": [scene()], "results": [scene("one"), scene("two")]},
            "native",
        )
