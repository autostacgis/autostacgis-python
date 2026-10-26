import json

import numpy as np
import pytest
from helpers import configured, scene, subset

from autostacgis import Application
from autostacgis.config import read
from autostacgis.data import Window
from autostacgis.errors import AutoSTACGISError, NoPlan


def goal(**requirements):
    return {"output": {"kind": "raster", "require": requirements}}


@pytest.mark.parametrize("extension", ["json", "yaml"])
def test_imports_at_arbitrary_depth_and_pointer(tmp_path, extension):
    import yaml

    def write(name, data):
        path = tmp_path / (name + "." + extension)
        path.write_text(json.dumps(data) if extension == "json" else yaml.safe_dump(data))
        return path.name

    source = write("source", {"nested": {"a/b": {"defaults": {"x": 1, "y": 2}, "list": [1, 2]}}})
    dest = write(
        "dest",
        {
            "settings": {"$import": source + "#/nested/a~1b", "defaults": {"x": 3}},
            "array": {"$import": source + "#/nested/a~1b/list"},
        },
    )
    assert read(tmp_path / dest) == {
        "settings": {"defaults": {"x": 3, "y": 2}, "list": [1, 2]},
        "array": [1, 2],
    }
    (tmp_path / ("cycle." + extension)).write_text(json.dumps({"$import": "cycle." + extension}))
    with pytest.raises(AutoSTACGISError, match="Recursive config import"):
        read(tmp_path / ("cycle." + extension))


def test_grouped_config_preserves_defaults(tmp_path):
    document = subset("statistics")
    document["goal"] = {
        "metadata-value": {"path": "assets.$asset.data_state:statistics_calculated", "value": True}
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(document))
    app = Application.from_file(path)
    assert [s.module for s in app.plan(inputs=[scene()]).steps] == ["statistics"]


def test_strict_steps_do_not_fill_missing_goals(tmp_path):
    app = Application(subset("align_grid", "statistics"), base_dir=tmp_path)
    request = {
        "goal": goal(**{"assets.$asset.data_state:statistics_calculated": True}),
        "steps": {"align": {"plugin": "align_grid"}},
    }
    with pytest.raises(NoPlan):
        configured(app, request).plan(inputs=[scene()])
    request["steps"]["finish"] = {"auto": True}
    plan = configured(app, request).plan(inputs=[scene()])
    assert [s.module for s in plan.steps] == ["align_grid", "statistics"]
    assert plan.steps[1].inputs["image"] == plan.steps[0].outputs["result"]


def test_explicit_repeated_plugin_settings_and_native_links_execute(tmp_path):
    app = Application(subset("align_grid", "statistics"), base_dir=tmp_path)
    request = {
        "goal": goal(**{"assets.$asset.data_state:statistics_calculated": True}),
        "steps": {
            "coarse": {"plugin": "align_grid", "param:resolution": 2},
            "fine": {
                "plugin": "align_grid",
                "param:resolution": 1,
                "inputs": {"image": "result:coarse:result"},
            },
            "measure": {"plugin": "statistics"},
        },
    }
    plan = configured(app, request).plan(inputs=[scene()])
    assert [s.name for s in plan.steps] == ["coarse", "fine", "measure"]
    assert [s.settings.get("resolution") for s in plan.steps[:2]] == [2, 1]
    with configured(app, request).run(inputs=[scene()]) as result:
        assert result.records[0].payload.grid.width == 4
        np.testing.assert_allclose(result.records[0].payload.read(Window(0, 0, 4, 4)).pixels, 10)


def test_metadata_parameters_and_explicit_persistence(tmp_path):
    app = Application(subset("align_grid", "statistics"), base_dir=tmp_path)
    request = {
        "goal": goal(**{"assets.$asset.data_state:statistics_calculated": True}),
        "steps": {
            "first": {"plugin": "align_grid", "param:resolution": 2},
            "second": {
                "plugin": "align_grid",
                "param:crs": "result:first:result#/assets/data/proj:code",
                "outputs": {
                    "result": {
                        "save": True,
                        "encode": {"plugin": "geotiff"},
                        "data_out": {"plugin": "filesystem", "settings": {"path": "saved.tif"}},
                    }
                },
            },
            "stats": {"plugin": "statistics"},
        },
    }
    with configured(app, request).run(inputs=[scene()]) as result:
        assert (tmp_path / "saved.tif").is_file()
        # Inherited durable hrefs remain visible to downstream processing.
        second = next(s for s in result.plan.steps if s.name == "second")
        assert second.settings["crs"] == "EPSG:4326"


def test_mandatory_step_runs_when_goal_already_holds(tmp_path):
    app = Application(subset("align_grid"), base_dir=tmp_path)
    request = {
        "goal": goal(**{"assets.$asset.unit": "1"}),
        "steps": {"force": {"plugin": "align_grid"}, "finish": {"auto": True}},
    }
    assert [s.name for s in configured(app, request).plan(inputs=[scene()]).steps] == ["force"]


def test_unknown_and_forward_links_fail(tmp_path):
    app = Application(subset("statistics"), base_dir=tmp_path)
    request = {
        "goal": goal(),
        "steps": {"stats": {"plugin": "statistics", "inputs": {"image": "result:later:result"}}},
    }
    with pytest.raises(AutoSTACGISError, match="forward result reference"):
        configured(app, request).plan(inputs=[scene()])


def test_auto_before_strict_suffix_establishes_inherited_goals(tmp_path):
    app = Application(subset("align_grid", "statistics"), base_dir=tmp_path)
    request = {
        "goal": goal(**{"assets.$asset.data_state:grid_aligned": True}),
        "steps": {"prepare": {"auto": True}, "measure": {"plugin": "statistics"}},
    }
    plan = configured(app, request).plan(inputs=[scene()])
    assert [s.module for s in plan.steps] == ["align_grid", "statistics"]
    with configured(app, request).run(inputs=[scene()]) as result:
        assert result.records[0].item["assets"]["data"]["data_state:grid_aligned"] is True


def test_auto_fills_required_prerequisite(monkeypatch, tmp_path):
    from copy import deepcopy

    from autostacgis_plugins.statistics import statistics

    contract = deepcopy(statistics.contract)
    contract["argument_requirements"]["image"]["require"] = {"assets.$asset.data_state:grid_aligned": True}
    monkeypatch.setattr(statistics, "contract", contract)
    app = Application(subset("align_grid", "statistics"), base_dir=tmp_path)
    request = {
        "goal": goal(**{"assets.$asset.data_state:statistics_calculated": True}),
        "steps": {"measure": {"plugin": "statistics"}},
    }
    with pytest.raises(NoPlan, match="no valid input binding"):
        configured(app, request).plan(inputs=[scene()])
    request["steps"] = {"prepare": {"auto": True}, **request["steps"]}
    assert [s.module for s in configured(app, request).plan(inputs=[scene()]).steps] == [
        "align_grid",
        "statistics",
    ]


def test_output_path_shorthand_is_rejected():
    document = subset("align_grid")
    document["execution"]["steps"] = {
        "save": {"plugin": "align_grid", "outputs": {"result": "path:output.tif"}}
    }
    with pytest.raises(AutoSTACGISError):
        Application(document)


def test_required_parameters_can_be_supplied_only_by_named_step(tmp_path):
    app = Application(subset("band_math"), base_dir=tmp_path)
    request = {
        "goal": {"output": {"require": {"path": "assets.$asset.roles", "contains": "spectral-index"}}},
        "steps": {"calculate": {"plugin": "band_math", "param:expressions": ["b1+b2"]}},
    }
    with configured(app, request).run(inputs=[scene()]) as result:
        pixels = result.records[0].payload.read(Window(0, 0, 4, 4)).pixels
        np.testing.assert_allclose(pixels, 20)


def test_mosaic_coverage_is_a_declarative_goal():
    from pathlib import Path

    from autostacgis.config import read

    config = read(Path(__file__).parents[2] / "examples/mosaic/config.yaml")
    app = Application(config)
    request = app._execution()
    assert request["goal"]["settings"]["coverage"]["geometry"]["type"] == "Polygon"
