from copy import deepcopy

import numpy as np
import pytest

from autostacgis import Application, Record, Registry
from autostacgis.builtins import catalog
from autostacgis.contracts import module
from autostacgis.data import ArrayRaster, Grid
from autostacgis.errors import AutoSTACGISError, NoPlan, SearchLimit
from autostacgis.goals import compile_goal, selected


def record(name="a", kind="raster", **properties):
    item = {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": name,
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "bbox": [0, 0, 1, 1],
        "properties": {"datetime": "2024-01-01T00:00:00Z", **properties},
        "links": [],
        "stac_extensions": [],
        "assets": {"data": {"href": "memory://" + name, "roles": ["data"], "proj:code": "EPSG:4326"}},
    }
    payload = (
        ArrayRaster(
            np.ones((1, 2, 2), dtype="float32"),
            Grid(2, 2, 1, "float32", (0.5, 0, 0, 0, -0.5, 1), "EPSG:4326"),
        )
        if kind == "raster"
        else None
    )
    return Record(item, kind, payload=payload)


def app_with(*functions):
    registry = Registry()
    config = catalog()
    for function in functions:
        registry.register(function.__name__, "process", function)
        config["plugins"].append(
            {"id": function.__name__, "capabilities": {"process": {"implementation": function.__name__}}}
        )
    return Application(config, registry=registry)


def operation(name, requirements, effects, cost=1, kind="raster"):
    @module(
        argument_requirements={"image": {"data_types": ["raster"], "kind": kind, "require": requirements}},
        outputs={
            "result": {
                "data_types": ["raster" if kind == "raster" else "batches"],
                "kind": kind,
                "inherit_metadata": ["image"],
                "set": [{p: v} for p, v in effects.items()],
            }
        },
        cost=cost,
    )
    def function(image, settings, context):
        return {"result": [context.output("result", image.payload, facts=effects)]}

    function.__name__ = name
    return function


def test_created_objects_feed_other_modules_and_execute(tmp_path):
    first = operation("first", {"properties.ready": False}, {"properties.ready": True})
    second = operation("second", {"properties.ready": True}, {"properties.done": True})
    app = app_with(first, second)
    app.base_dir = tmp_path
    req = {"goal": {"output": {"require": {"properties.done": True}}}, "modules": ["first", "second"]}
    plan = app.plan(req, [record(ready=False)])
    assert [s.module for s in plan.steps] == ["first", "second"]
    assert plan.steps[1].dependencies == (plan.steps[0].key,)
    with app.run(req, [record(ready=False)]) as result:
        assert result.records[0].item["properties"]["done"] is True
        assert result.records[0].payload.read(next(result.records[0].payload.windows())).pixels.mean() == 1


def test_cost_chooses_correction_before_resampling():
    correct = operation(
        "correct",
        {"properties.corrected": False},
        {"properties.corrected": True},
        cost={"parameters": {"history": "image.properties.history"}, "jsonata": "1 + $count(history)"},
    )
    resample = operation("resample", {"properties.resampled": False}, {"properties.resampled": True})
    resample.contract["outputs"]["result"]["append"] = [{"properties.history": "bilinear"}]
    app = app_with(correct, resample)
    req = {
        "goal": {"output": {"require": {"properties.corrected": True, "properties.resampled": True}}},
        "modules": ["correct", "resample"],
    }
    plan = app.plan(req, [record(corrected=False, resampled=False, history=[])])
    assert [s.module for s in plan.steps] == ["correct", "resample"]
    assert plan.cost == 2


def test_shared_dependencies_and_multiple_named_outputs():
    @module(
        argument_requirements={"image": {"data_types": ["raster"], "require": {"properties.ready": False}}},
        outputs={
            p: {
                "data_types": ["raster"],
                "kind": "raster",
                "inherit_metadata": ["image"],
                "set": [{f"properties.{p}": True}],
            }
            for p in ("left", "right")
        },
    )
    def branch_outputs(image, settings, context):
        pass

    app = app_with(branch_outputs)
    req = {
        "modules": ["branch_outputs"],
        "goal": {
            "all_of": [
                {"output": {"name": "a", "require": {"properties.left": True}}},
                {"output": {"name": "b", "require": {"properties.right": True}}},
            ]
        },
    }
    plan = app.plan(req, [record(ready=False)])
    assert len(plan.steps) == 1 and plan.cost == 1 and len(plan.outputs) == 2


def test_same_output_binding_and_for_each_and_extra_inventory():
    op = operation("fix", {"properties.ready": False}, {"properties.ready": True})
    app = app_with(op)
    req = {
        "modules": ["fix"],
        "goal": {
            "for_each": {
                "select": {"kind": "raster", "require": {"properties.selected": True}},
                "require": {"properties.ready": True},
            }
        },
    }
    plan = app.plan(
        req,
        [
            record("a", ready=False, selected=True),
            record("b", ready=False, selected=True),
            record("unused", ready=False, selected=False),
        ],
    )
    assert len(plan.steps) == 2 and len(plan.outputs) == 2
    req["goal"] = {
        "all_of": [
            {"output": {"name": "same", "require": {"properties.ready": True}}},
            {"output": {"name": "same", "require": {"properties.ready": False}}},
        ]
    }
    with pytest.raises(NoPlan):
        app.plan(
            {**req, "solver": {"plugin": "backward_chain", "settings": {"max_steps": 2}}},
            [record(ready=False)],
        )


def test_unknown_is_not_false():
    op = operation("fix", {"not": {"path": "properties.ready", "equals": True}}, {"properties.done": True})
    with pytest.raises(NoPlan):
        app_with(op).plan(
            {"modules": ["fix"], "goal": {"output": {"require": {"properties.done": True}}}}, [record()]
        )


def test_raw_vector_and_pointcloud_selectors():
    raw = record()
    raw.item["geometry"] = None
    raw.item.pop("bbox")
    raw.item["assets"]["data"].pop("proj:code")
    assert not selected(raw, {"projected": True})
    assert not selected(record(), {"projected": True})
    projected = record()
    projected.item["assets"]["data"]["proj:code"] = "EPSG:32604"
    assert selected(projected, {"projected": True})
    for kind in ("raster", "vector", "point_cloud"):
        app = app_with()
        result = app.plan(
            {"modules": [], "goal": {"for_each": {"select": {"kind": kind}}}}, [record(kind=kind)]
        )
        assert result.steps == []


def test_grouping_collects_all_and_can_be_overridden():
    @module(
        argument_requirements={
            "images": {
                "data_types": ["raster"],
                "kind": "raster",
                "cardinality": "many",
                "sequence": {"plugin": "gather-all"},
                "require": {"properties.done": False},
            }
        },
        outputs={
            "result": {
                "data_types": ["raster"],
                "kind": "raster",
                "inherit_metadata": ["images"],
                "geometry": "union",
                "set": [{"properties.done": True}],
            }
        },
    )
    def collect(images, settings, context):
        pass

    app = app_with(collect)
    app.plugins["collect"]["requires"] = ["gather-all"]
    req = {
        "modules": ["collect"],
        "goal": {
            "output": {
                "require": {"properties.done": True},
                "coverage": {"geometry": record().item["geometry"]},
            }
        },
    }
    plan = app.plan(req, [record("a", done=False), record("b", done=False)])
    assert len(plan.steps[0].inputs["images"]) == 2
    plan = app.plan(
        {**req, "sequence": {"collect.images": None}}, [record("a", done=False), record("b", done=False)]
    )
    assert len(plan.steps[0].inputs["images"]) == 1


def test_goal_plugin_composition_and_cycle_rejection():
    app = app_with()
    r = record()
    r.item["properties"]["ready"] = True
    goal = {
        "all_of": [
            {"output": {"name": "answer", "require": {"properties.ready": True}}},
            {"output": {"name": "answer", "kind": "raster"}},
        ]
    }
    assert app.plan({"goal": goal, "modules": []}, [r]).cost == 0

    def loop(settings, records, context):
        return {"plugin": "loop"}

    loop.contract = {
        "solver": "backward_chain",
        "settings_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    app.registry.register("loop", "goal", loop, requires=("backward_chain",))
    app.capabilities["goal"]["loop"] = {"id": "loop", "implementation": "loop"}
    app.plugins["loop"] = {"id": "loop", "capabilities": {"goal": {"implementation": "loop"}}}
    with pytest.raises(AutoSTACGISError, match="Recursive goal"):
        compile_goal({"plugin": "loop"}, [r], app)


def test_native_statistics_run(tmp_path):
    app = Application(base_dir=tmp_path)
    request = {
        "goal": {"output": {"require": {"assets.$asset.data_state:statistics_calculated": True}}},
        "modules": ["statistics"],
    }
    with app.run(request, [record()]) as result:
        assert result.records[0].item["assets"]["data"]["bands"][0]["statistics"]["mean"] == 1


def test_negative_cost_rejected_and_bound_reported():
    op = operation("bad", {}, {"properties.done": True}, cost=-1)
    with pytest.raises(AutoSTACGISError, match="nonnegative"):
        app_with(op).plan(
            {"modules": ["bad"], "goal": {"output": {"require": {"properties.done": True}}}}, [record()]
        )
    op.contract["cost"] = 1
    with pytest.raises(NoPlan):
        app_with(op).plan(
            {
                "modules": ["bad"],
                "solver": {"plugin": "backward_chain", "settings": {"max_bindings": 1}},
                "goal": {"output": {"require": {"properties.unreachable": True, "properties.done": True}}},
            },
            [record()],
        )


def test_collection_barrier_waits_for_every_member():
    prepare = operation("prepare", {"properties.ready": False}, {"properties.ready": True})

    @module(
        argument_requirements={
            "images": {
                "data_types": ["raster"],
                "kind": "raster",
                "cardinality": "many",
                "sequence": {"plugin": "gather-all"},
                "require": {"properties.ready": True},
            }
        },
        outputs={
            "results": {
                "data_types": ["raster"],
                "kind": "raster",
                "each_from": "images",
                "inherit_metadata": ["images"],
                "set": [{"properties.done": True}],
            }
        },
    )
    def together(images, settings, context):
        pass

    app = app_with(prepare, together)
    app.plugins["together"]["requires"] = ["gather-all"]
    request = {
        "modules": ["prepare", "together"],
        "goal": {"for_each": {"require": {"properties.done": True}}},
    }
    plan = app.plan(request, [record("a", ready=True), record("b", ready=False)])
    assert [s.module for s in plan.steps] == ["prepare", "together"]
    assert len(plan.steps[-1].inputs["images"]) == 2
    assert len(plan.steps[-1].outputs["results"]) == 2


def test_group_outputs_can_feed_individual_steps():
    @module(
        argument_requirements={
            "images": {
                "data_types": ["raster"],
                "kind": "raster",
                "cardinality": "many",
                "sequence": {"plugin": "gather-all"},
            }
        },
        outputs={
            "results": {
                "data_types": ["raster"],
                "kind": "raster",
                "each_from": "images",
                "inherit_metadata": ["images"],
                "set": [{"properties.ready": True}],
            }
        },
    )
    def together(images, settings, context):
        pass

    finish = operation("finish_each", {"properties.ready": True}, {"properties.done": True})
    app = app_with(together, finish)
    app.plugins["together"]["requires"] = ["gather-all"]
    plan = app.plan(
        {
            "modules": ["together", "finish_each"],
            "goal": {"for_each": {"require": {"properties.done": True}}},
            "solver": {"plugin": "backward_chain", "settings": {"max_steps": 3}},
        },
        [record("a"), record("b")],
    )
    assert [s.module for s in plan.steps] == ["together", "finish_each", "finish_each"]
    assert all(s.dependencies == (plan.steps[0].key,) for s in plan.steps[1:])


def test_mosaic_cannot_replace_individual_outputs():
    @module(
        argument_requirements={
            "images": {
                "data_types": ["raster"],
                "kind": "raster",
                "cardinality": "many",
                "sequence": {"plugin": "gather-all"},
            }
        },
        outputs={
            "result": {
                "data_types": ["raster"],
                "kind": "raster",
                "inherit_metadata": ["images"],
                "set": [{"properties.done": True}],
            }
        },
    )
    def merge(images, settings, context):
        pass

    app = app_with(merge)
    app.plugins["merge"]["requires"] = ["gather-all"]
    with pytest.raises(NoPlan):
        app.plan(
            {
                "modules": ["merge"],
                "goal": {"for_each": {"require": {"properties.done": True}}},
                "solver": {"plugin": "backward_chain", "settings": {"max_steps": 1}},
            },
            [record("a"), record("b")],
        )


def test_mosaic_metadata_requires_all_contributors_to_agree():
    from autostacgis_plugins.mosaic import predict_mosaic

    a, b = record("a"), record("b")
    for r in (a, b):
        r.item["assets"]["data"]["data_state:masked"] = {"cloud": True}
    a.item["assets"]["data"]["data_state:pansharpened"] = True
    b.item["assets"]["data"]["data_state:pansharpened"] = False
    prediction = predict_mosaic({"images": [a, b]}, {}, 0, deepcopy(a.item))
    assert prediction["assets"]["data"]["data_state:pansharpened"] is None
    assert prediction["assets"]["data"]["data_state:masked"]["cloud"] is True


def test_any_of_can_skip_an_empty_input_selection():
    app = app_with()
    request = {
        "modules": [],
        "goal": {
            "any_of": [{"for_each": {"select": {"kind": "point_cloud"}}}, {"output": {"kind": "raster"}}]
        },
    }
    assert app.plan(request, [record()]).steps == []


def test_mixed_stac_assets_bind_independent_data_kinds():
    from autostacgis_plugins._backward_chain_planner import inventory

    from autostacgis.data.kinds import describe_kinds

    item = record()
    item.item["assets"] = {
        "photo": {"href": "photo.png", "roles": ["data"]},
        "points": {"href": "points.laz", "roles": ["data"]},
        "footprint": {"href": "footprint.geojson", "roles": ["data"]},
    }
    item = describe_kinds(item)
    bound = inventory([item])
    assert {r.runtime["selected_asset"]: r.kind for r in bound.values()} == {
        "photo": "raster",
        "points": "point_cloud",
        "footprint": "vector",
    }


def test_subproblem_limit_is_explicit():
    op = operation("fix", {}, {"properties.done": True})
    with pytest.raises(SearchLimit, match="subproblem"):
        app_with(op).plan(
            {
                "modules": ["fix"],
                "solver": {"plugin": "backward_chain", "settings": {"max_states": 1}},
                "goal": {"output": {"require": {"properties.done": True}}},
            },
            [record()],
        )


def test_named_step_disambiguates_available_producers():
    first = operation("first", {}, {"properties.finished": True})
    second = operation("second", {}, {"properties.finished": True})
    app = app_with(first, second)
    request = {
        "modules": ["first", "second"],
        "goal": {"output": {"require": {"properties.finished": True}}},
        "steps": {"chosen": {"plugin": "first"}, "rest": {"auto": True}},
    }
    plan = app.plan(request, [record()])
    assert [(s.name, s.module) for s in plan.steps] == [("chosen", "first")]


def test_automatic_gap_preserves_explicit_stage_barriers():
    first = operation("first", {}, {"properties.prepared": True})
    second = operation("second", {}, {"properties.finished": True})
    app = app_with(first, second)
    request = {
        "modules": ["first", "second"],
        "goal": {"output": {"require": {"properties.finished": True}}},
        "steps": {"required": {"plugin": "first"}, "rest": {"auto": True}},
    }
    plan = app.plan(request, [record()])
    assert len(plan.steps) == 2
    assert plan.steps[0].key in plan.steps[1].dependencies
    assert plan.cost == 2


def test_nested_step_overrides_preserve_unspecified_defaults():
    transform = operation("transform", {}, {"properties.finished": True})
    transform.contract["settings"] = {"calibration": {"gain": 1, "offset": 2}}
    app = app_with(transform)
    request = {
        "modules": ["transform"],
        "module_settings": {"transform": {"calibration": {"gain": 3}}},
        "goal": {"output": {"require": {"properties.finished": True}}},
        "steps": {"adjust": {"plugin": "transform", "param:calibration": {"offset": 4}}},
    }
    plan = app.plan(request, [record()])
    assert plan.steps[0].settings["calibration"] == {"gain": 3, "offset": 4}
