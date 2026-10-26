"""Only one public workflow format and one source of configuration settings."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from autostacgis import Application
from autostacgis.cli import main
from autostacgis.errors import AutoSTACGISError


@pytest.mark.parametrize(
    "document",
    [
        {"version": "0.0.1", "plugins": []},
        {"catalog": {"version": "0.0.1", "plugins": []}, "request": {"goal": {"output": {}}}},
        {"request": {"goal": {"output": {}}}},
        {"solver": {"backward_chain": {}}, "goal": {"output": {}}},
    ],
)
def test_old_configuration_forms_are_rejected(document):
    with pytest.raises(AutoSTACGISError):
        Application(document)


def test_python_and_cli_have_no_separate_request_overlay(tmp_path):
    path = tmp_path / "workflow.yaml"
    path.write_text('solver: {backward_chain: {}}\ngoal: {metadata-value: {jsonata: "true", value: true}}\n')
    app = Application.from_file(path)
    with pytest.raises(TypeError):
        app.run({"goal": {"output": {}}})
    with pytest.raises(TypeError):
        app.plan(request={"goal": {"output": {}}})
    runner = CliRunner()
    assert runner.invoke(main, ["validate", str(path)]).exit_code == 0
    assert runner.invoke(main, ["validate", str(path), "--request", str(path)]).exit_code == 2
    assert runner.invoke(main, ["run", str(path), str(path)]).exit_code == 2


def test_every_shipped_workflow_uses_the_current_format_and_valid_result_names():
    for path in sorted((Path(__file__).parents[2] / "examples").rglob("config.yaml")):
        app = Application.from_file(path)
        assert {"solver", "goal"} <= app.snapshot().keys(), str(path)
        app._execution()
