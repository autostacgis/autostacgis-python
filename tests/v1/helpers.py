import numpy as np

from autostacgis import Record
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


def subset(*names):
    from autostacgis_plugins import modules

    groups = {}
    for provider in modules():
        if provider.CAPABILITY in ("goal", "solver", "trigger", "process"):
            continue
        groups.setdefault(provider.CAPABILITY, {})[provider.ID] = {}
    groups.update(
        solver={"backward_chain": {}},
        goal={"metadata-value": {"jsonata": "true", "value": True}},
        process={name: {} for name in names},
        execution={"sources": []},
        metadata_structure={"strict": False},
    )
    groups["services"]["executor"] = {"workers": 2}
    return groups


def configured(app, parameters):
    """Build a grouped workflow for a solver's unit-test execution parameters."""
    from copy import deepcopy

    from autostacgis import Application

    document = app.snapshot()
    parameters = deepcopy(parameters)
    parameters.pop("version", None)
    goal = parameters.pop("goal")
    if "plugin" in goal:
        document["goal"] = {goal["plugin"]: goal.get("settings", {})}
    else:
        test_goal = SolverGoal(app.solver_name, app.solver_implementation.contract["goal_schema"])
        if "test-goal" not in app.registry.plugins:
            app.registry.register("test-goal", "goal", test_goal, requires=(app.solver_name,))
        document["goal"] = {"test-goal": {"value": goal}}
    if "modules" in parameters:
        if "steps" not in parameters:
            document.setdefault("execution", {}).pop("steps", None)
        document["process"] = {
            name: document.get("process", {}).get(name, {}) for name in parameters["modules"]
        }
    for name, settings in parameters.pop("module_settings", {}).items():
        document.setdefault("process", {}).setdefault(name, {}).update(settings)
    for name, definition in parameters.pop("runtime", {}).items():
        settings = deepcopy(definition.get("settings", {}))
        if definition.get("implementation", name) != name:
            settings["backend"] = definition["implementation"]
        document.setdefault("services", {})[name] = settings
    if "solver" in parameters:
        call = parameters.pop("solver")
        document["solver"] = {call["plugin"]: call.get("settings", {})}
    document.setdefault("execution", {}).update(parameters)
    return Application(document, registry=app.registry, base_dir=app.base_dir, events=app.events)


def add_plugins(document, definitions):
    """Select registrations and their parameters in a workflow fixture."""
    from copy import deepcopy

    for definition in definitions:
        for capability, entry in definition["capabilities"].items():
            assert entry["implementation"] == definition["id"]
            options = deepcopy(entry.get("settings", {}))
            if entry.get("execution"):
                options.update(entry["execution"])
            document.setdefault(capability, {})[definition["id"]] = options
    return document


def require_extra(module, distribution=None):
    """Run scientific integration tests only when their optional SDK is installed."""
    from importlib.metadata import PackageNotFoundError, version

    import pytest

    try:
        version(distribution or module)
    except PackageNotFoundError:
        pytest.skip(f"Optional integration requires {distribution or module}")
    return pytest.importorskip(module)


class SolverGoal:
    """Serializable goal fixture exposing a solver's native contract."""

    def __init__(self, solver, schema):
        self.contract = {
            "solver": solver,
            "settings_schema": {
                "type": "object",
                "$defs": schema.get("$defs", {}),
                "properties": {"value": schema},
                "required": ["value"],
                "additionalProperties": False,
            },
        }

    def __call__(self, settings, records, context):
        return settings["value"]
