"""Saving is an opt-in property of each named function result."""

from copy import deepcopy

import pytest
from helpers import scene

from autostacgis import Application, Registry
from autostacgis.artifacts import Artifact
from autostacgis.errors import AutoSTACGISError
from autostacgis.io import local
from autostacgis.metadata import output_metadata
from autostacgis.plugin_metadata import inventory
from autostacgis.stac import Stac


def statistics_workflow(options=None):
    return {
        "solver": {"backward_chain": {}},
        "goal": {"metadata-value": {"path": "assets.$asset.data_state:statistics_calculated", "value": True}},
        "process": {"statistics": {**({"outputs": {"result": options}} if options else {})}},
    }


def test_standard_results_do_not_save_or_load_optional_destinations_by_default(tmp_path):
    app = Application(statistics_workflow(), base_dir=tmp_path)
    assert "filesystem" not in app.plugins and "geotiff" not in app.plugins
    with app.run(inputs=[scene()]) as result:
        asset = result.records[0].item["assets"]["data"]
        assert asset["data_state:filename_suffixes"] == ["statistics"]
        assert asset["href"].startswith("autostacgis:")
    assert not (tmp_path / "output").exists()
    rows = [row for row in inventory(Registry()) if row["capability"] == "process"]
    assert rows and all(
        not options["save"] for row in rows for options in row["defaults"]["outputs"].values()
    )


def test_opt_in_uses_plugin_jsonata_default_in_the_item_directory(tmp_path):
    image = scene("scene-one")
    directory = tmp_path / "original scene"
    image.item["assets"]["data"]["href"] = (directory / "input.tif").as_uri()
    image.item["properties"]["data_state:directory_root"] = str(directory)
    Stac.extensions(image.item)
    original = deepcopy(image.item)
    app = Application(statistics_workflow({"save": True}), base_dir=tmp_path)
    assert "filesystem" in app.plugins and "geotiff" in app.plugins
    with app.run(inputs=[image]) as result:
        record = result.records[0]
        assert local(record.item["assets"]["data"]["href"]) == directory / "scene-one_statistics.tif"
        assert local(record.item["properties"]["data_state:output_directory"]) == directory
        assert record.payload.read(next(record.payload.windows())).pixels.mean() == 10
        app.stac.validate(record.item)
    assert image.item == original


def test_destination_and_filename_tokens_are_overridable_per_result(tmp_path):
    options = {
        "save": True,
        "filename_suffixes": ["custom", "name"],
        "data_out": {
            "plugin": "filesystem",
            "settings": {
                "path": {"jsonata": '"exports/" & $join(asset.`data_state:filename_suffixes`, "_") & ".tif"'}
            },
        },
    }
    app = Application(statistics_workflow(options), base_dir=tmp_path)
    with app.run(inputs=[scene()]) as result:
        assert local(result.records[0].item["assets"]["data"]["href"]) == tmp_path / "exports/custom_name.tif"
    assert not (tmp_path / "output").exists()


def test_remote_items_use_a_plugin_defined_item_folder_when_no_local_root_is_known(tmp_path):
    image = scene("remote-scene")
    image.item["assets"]["data"]["href"] = "https://example.test/source.tif"
    image.item["properties"]["data_state:directory_root"] = "https://example.test"
    Stac.extensions(image.item)
    assert "data_state:output_directory" not in image.item["properties"]
    app = Application(statistics_workflow({"save": True}), base_dir=tmp_path)
    with app.run(inputs=[image]) as result:
        record = result.records[0]
        assert record.item["properties"]["data_state:output_directory"] == "output/remote-scene"
        assert (
            local(record.item["assets"]["data"]["href"])
            == tmp_path / "output/remote-scene/remote-scene_statistics.tif"
        )


def test_suffix_histories_belong_to_each_asset_and_preserve_repeated_operations():
    image = scene()
    image.item["assets"]["red"] = {**image.item["assets"]["data"], "data_state:filename_suffixes": ["red"]}
    image.item["assets"]["nir"] = {**image.item["assets"]["data"], "data_state:filename_suffixes": ["nir"]}
    for key in ("red", "nir"):
        bound = image.clone()
        bound.runtime["selected_asset"] = key
        policy = {"inherit_metadata": ["image"], "filename_suffixes": ["processed", "processed"]}
        output = output_metadata({"image": [bound]}, policy)
        assert output["assets"]["data"]["data_state:filename_suffixes"] == [key, "processed", "processed"]
        assert "data_state:filename_suffixes" not in output["properties"]
    assert image.item["assets"]["red"]["data_state:filename_suffixes"] == ["red"]


def test_any_named_stream_result_can_save_while_another_stays_unsaved(tmp_path):
    def streams(inputs, settings, context):
        return {
            port: [context.output(port, Artifact("byte-stream", lambda: iter([b"ab", b"cd"])))]
            for port in ("preview", "report")
        }

    streams.contract = {
        "argument_requirements": {"image": {"kind": "raster", "data_types": ["raster"]}},
        "outputs": {
            port: {
                "kind": "blob",
                "data_types": ["byte-stream"],
                "inherit_metadata": ["image"],
                "set": [{"properties.result_type": port}],
                "save": False,
                "data_out": {"plugin": "filesystem", "settings": {"path": port + ".bin"}},
            }
            for port in ("preview", "report")
        },
    }
    registry = Registry()
    registry.register("streams", "process", streams)
    document = {
        "solver": {"backward_chain": {}},
        "goal": {"metadata-value": {"path": "properties.result_type", "value": "report"}},
        "process": {"streams": {"outputs": {"preview": {"save": True}}}},
    }
    document["metadata_structure"] = {
        "inline": {"properties": {"properties": {"properties": {"result_type": {"type": "string"}}}}}
    }
    app = Application(document, registry=registry, base_dir=tmp_path)
    with app.run(inputs=[scene()]) as result:
        assert result.records[0].item["properties"]["result_type"] == "report"
        assert (tmp_path / "preview.bin").read_bytes() == b"abcd"
        assert not (tmp_path / "report.bin").exists()


def test_saving_is_rejected_on_goals_and_unknown_result_ports(tmp_path):
    document = statistics_workflow()
    document["goal"]["metadata-value"]["publish"] = {"data_out": {"plugin": "filesystem"}}
    with pytest.raises(AutoSTACGISError):
        Application(document, base_dir=tmp_path)

    document = statistics_workflow()
    document["process"]["statistics"]["outputs"] = {"unknown": {"save": True}}
    with pytest.raises(AutoSTACGISError, match="Unknown result ports"):
        Application(document, base_dir=tmp_path)


def test_filename_overrides_remain_consistent_when_checkpoints_are_enabled(tmp_path):
    for tokens in (["first"], ["second"]):
        document = statistics_workflow({"filename_suffixes": tokens})
        document["checkpoint"] = {"file-checkpoint": {}}
        document["execution"] = {"checkpoint": {"plugin": "file-checkpoint"}}
        app = Application(document, base_dir=tmp_path)
        with app.run(inputs=[scene()]) as result:
            assert result.records[0].item["assets"]["data"]["data_state:filename_suffixes"] == tokens
