import json
from hashlib import sha256
from types import ModuleType

import httpx
import pytest
from click.testing import CliRunner
from helpers import scene

from autostacgis import Application, Registry
from autostacgis.cli import main
from autostacgis.errors import AutoSTACGISError
from autostacgis.plugin_manager import metadata
from autostacgis.plugin_metadata import inventory, parameters
from autostacgis.workflow_config import default_workflow
from autostacgis.workflow_manager import WorkflowManager


def test_mosaic_starter_plans_the_complete_processing_workflow():
    app = Application(default_workflow())
    plan = app.plan(inputs=[scene("west"), scene("east")])
    assert [step.module for step in plan.steps] == [
        "align_grid",
        "align_grid",
        "extract_footprints",
        "clean_footprints",
        "voronoi_seamlines",
        "mask_by_polygons",
        "mask_by_polygons",
        "mosaic",
    ]
    assert "filesystem" not in app.plugins


def test_minimal_workflow_plans_and_runs_without_repeating_settings(tmp_path):
    document = default_workflow()
    document.pop("execution")
    document["goal"]["metadata-value"] = {
        "path": "assets.$asset.data_state:statistics_calculated",
        "value": True,
    }
    document["process"] = {"statistics": {}}
    app = Application(document, base_dir=tmp_path)
    assert app.trigger_name == "now"
    assert app.snapshot() == document
    assert [step.module for step in app.plan(inputs=[scene()]).steps] == ["statistics"]
    with app.run(inputs=[scene()]) as result:
        assert (
            next(iter(result.records[0].item["assets"].values()))["data_state:statistics_calculated"] is True
        )


def test_multiple_goals_and_named_default_fragments_keep_explicit_values():
    registry = Registry()
    module = ModuleType("example")
    module.__doc__ = "Require a reusable metadata check with useful default parameters."
    module.ID, module.VERSION, module.CAPABILITY = "example", "1.0.0", "checks"
    module.IMPLEMENTATION, module.REQUIRES = lambda *args: True, ()
    module.DEFAULT_CONFIG = {"checks": {"metadata-matches": {"path": "properties.ready", "value": True}}}
    registry.register_module(module)
    assert registry.requirements["example"] == ("metadata-matches",)
    document = default_workflow()
    document["checks"] = {"example": {}, "metadata-matches": {"value": False}}
    document["goal"]["metadata-value"] = [
        {"path": "properties.ready", "value": True},
        {"jsonata": "item.bbox[0]", "value": 0},
    ]
    app = Application(document, registry=registry)
    assert app.capabilities["checks"]["metadata-matches"]["settings"]["value"] is False
    assert len(app.make_solver(app._execution(), [])[1]["goal"]["all_of"]) == 2


@pytest.mark.parametrize(
    "section,value",
    [("solver", {}), ("trigger", {"now": {}, "watchdog": {}}), ("process", {"gather-all": {}})],
)
def test_invalid_capability_selection_is_rejected(section, value):
    document = default_workflow()
    document[section] = value
    with pytest.raises(AutoSTACGISError):
        Application(document)


def test_plugin_descriptions_and_simple_signature_parameters_are_shared():
    def sample(window: int = 32, method: str = "mean", complicated: object = None):
        """Summarize a window.

        Args:
            window (int): Number of pixels per window.
            method (str): Summary operation.
        """
        return window

    defaults, schema = parameters(sample)
    assert defaults == {"window": 32, "method": "mean"}
    assert schema["properties"]["window"] == {
        "type": "integer",
        "default": 32,
        "description": "Number of pixels per window.",
    }
    assert "complicated" not in schema["properties"]
    assert all(row["description"] for row in inventory(Registry()))
    with pytest.raises(AutoSTACGISError, match="docstring"):
        metadata('ID="empty"\nVERSION="1.0.0"\nCAPABILITY="checks"\nREQUIRES=[]')
    result = CliRunner().invoke(main, ["plugins", "list"])
    assert result.exit_code == 0
    assert any(row["id"] == "now" and row["description"] for row in json.loads(result.output))


def test_workflow_download_checks_integrity_and_preserves_existing_files(tmp_path, monkeypatch):
    source = json.dumps(default_workflow())
    row = {
        "id": "example",
        "version": "0.0.1",
        "description": "A metadata goal.",
        "source": {
            "url": "https://example.test/commit/workflow.json",
            "sha256": sha256(source.encode()).hexdigest(),
        },
    }

    def get(url, **kwargs):
        return (
            httpx.Response(
                200, json={"format_version": 1, "workflows": [row]}, request=httpx.Request("GET", url)
            )
            if url.endswith("workflows.json")
            else httpx.Response(200, text=source, request=httpx.Request("GET", url))
        )

    monkeypatch.setattr(httpx, "get", get)
    manager = WorkflowManager("https://example.test/workflows.json")
    path = manager.download("example", tmp_path)
    assert json.loads(path.read_text()) == default_workflow()
    with pytest.raises(FileExistsError):
        manager.download("example", tmp_path)
    row["source"]["sha256"] = "0" * 64
    with pytest.raises(AutoSTACGISError, match="checksum"):
        manager.get("example")
    assert json.loads(path.read_text()) == default_workflow()
