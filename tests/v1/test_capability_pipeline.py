"""Integration coverage for discovery, typed edges, routing, publication, and resume."""

import json
from pathlib import Path

import numpy as np
import pytest
from helpers import scene, subset

from autostacgis import Application, Registry
from autostacgis.artifacts import Artifact, artifact
from autostacgis.checkpoint import Checkpoint
from autostacgis.data import ArrayRaster, FileRaster, Window
from autostacgis.errors import AutoSTACGISError
from autostacgis.plugins.buffered_transfer import BufferedTransfer
from autostacgis.plugins.file_checkpoint import FileCheckpoint
from autostacgis.publication import publish
from autostacgis.sequence import route


def app_for(tmp_path, function, *, extra=None):
    registry = Registry()
    registry.register("test-process", {"process": function})
    config = subset()
    config["plugins"].append(
        {"id": "test-process", "capabilities": {"process": {"implementation": "test-process"}}}
    )
    if extra:
        config["plugins"].extend(extra)
    return Application(config, registry=registry, base_dir=tmp_path)


def declaration(*, type_="raster", kind="raster", many=False):
    return {
        "argument_requirements": {
            "images": {
                "kind": "raster",
                "data_types": ["raster"],
                "cardinality": "many" if many else "one",
                "sequence": {"plugin": "gather-all"},
            }
        },
        "outputs": {
            "result": {
                "kind": kind,
                "data_types": [type_],
                "inherit_metadata": ["images"],
                "set": [{"properties.processed": True}],
                **({"each_from": "images"} if many else {}),
            }
        },
        "anchor": "images",
        "cost": 1,
    }


def request(**options):
    return {"goal": {"output": {"require": {"properties.processed": True}}}, **options}


def test_metadata_discovery_is_separate_from_selected_acquisition(tmp_path):
    calls = []

    class Source:
        def discover(self, settings, query, context):
            calls.append(("discover", query))
            yield scene()

        def acquire(self, record, settings, context):
            calls.append(("acquire", record.key))
            return record

    registry = Registry()
    registry.register("test-source", {"metadata_in": Source, "data_in": Source})
    config = subset()
    config["plugins"].append(registry.definition("test-source"))
    app = Application(config, registry=registry, base_dir=tmp_path)
    req = {"sources": ["test-source"], "query": {"area": [0, 0, 1, 1]}, "goal": {"output": {}}}
    assert len(app.plan(req).initial) == 1
    assert [c[0] for c in calls] == ["discover"]
    with app.run(req):
        pass
    assert [c[0] for c in calls] == ["discover", "discover", "acquire"]
    assert app.sources["test-source"] is app.acquirers["test-source"]


def test_bounded_native_and_byte_transfers(tmp_path):
    from types import SimpleNamespace

    context = SimpleNamespace(work=tmp_path)
    provider = BufferedTransfer({"memory_bytes": 8}, context)
    try:
        small = provider.transfer(Artifact("bytes", b"abc"), ["bytes", "path"], mode="optimized")
        assert small.type == "bytes"
        large = provider.transfer(
            Artifact("byte-stream", lambda: iter([b"01234", b"56789"])), ["bytes", "path"], mode="optimized"
        )
        assert large.type == "path" and large.value.read_bytes() == b"0123456789"
        streamed = provider.transfer(Artifact("bytes", b"abcdefghijk"), ["byte-stream"], mode="optimized")
        assert b"".join(streamed.chunks()) == b"abcdefghijk"
        native = provider.transfer(artifact(scene().payload), ["raster"], mode="optimized")
        assert isinstance(native.value, FileRaster)
        memory = provider.transfer(native, ["raster"], mode="memory")
        assert isinstance(memory.value, ArrayRaster)
        np.testing.assert_array_equal(memory.value.read(Window(0, 0, 4, 4)).pixels, 10)
        with pytest.raises(AutoSTACGISError, match="requires a receiving"):
            provider.transfer(Artifact("bytes", b"abc"), ["path"], mode="memory")
    finally:
        root = provider.root
        provider.close()
    assert not root.exists()


def test_split_combine_routes_drive_real_core_bindings(tmp_path):
    calls = []

    def process(inputs, settings, context):
        calls.append(len(inputs["images"]))
        return {
            "result": [
                context.output("result", r.payload, index=i, facts={"properties.processed": True})
                for i, r in enumerate(inputs["images"])
            ]
        }

    process.contract = declaration(many=True)
    app = app_for(tmp_path, process)
    records = [scene(str(i)) for i in range(4)]
    assert [len(g) for g in route(app, [{"plugin": "split", "settings": {"size": 2}}], records)] == [2, 2]
    assert [len(g) for g in route(app, [{"plugin": "split"}, {"plugin": "combine"}], records)] == [4]
    req = {
        "goal": {"for_each": {"require": {"properties.processed": True}}},
        "sequence": {"test-process.images": {"plugin": "split", "settings": {"size": 2}}},
    }
    with app.run(req, records) as result:
        assert len(result.records) == 4
        assert len(result.plan.steps) == 2
    assert sorted(calls) == [2, 2]


def test_byte_stream_processing_publishes_with_filesystem_and_stac(tmp_path):
    def process(inputs, settings, context):
        return {
            "result": [
                context.output(
                    "result",
                    Artifact("byte-stream", lambda: iter([b"hello", b" world"])),
                    facts={"properties.processed": True},
                )
            ]
        }

    process.contract = declaration(type_="byte-stream", kind="blob")
    app = app_for(tmp_path, process)
    req = request(
        steps={
            "bytes": {
                "plugin": "test-process",
                "transfer": "file",
                "outputs": {
                    "result": {
                        "publish": {
                            "data_out": {"plugin": "filesystem", "settings": {"path": "result.bin"}},
                            "metadata_out": {
                                "plugin": "stac-json",
                                "settings": {
                                    "path": "result.json",
                                    "href_field": "properties.example:metadata",
                                },
                            },
                        }
                    }
                },
            }
        }
    )
    with app.run(req, [scene()]) as result:
        assert (tmp_path / "result.bin").read_bytes() == b"hello world"
        item = json.loads((tmp_path / "result.json").read_text())
        assert item["assets"]["data"]["href"] == str(tmp_path / "result.bin")
        assert result.records[0].item["properties"]["example:metadata"] == str(tmp_path / "result.json")


def test_incompatible_publication_rejected_before_processing(tmp_path):
    def process(inputs, settings, context):
        pytest.fail("Must reject incompatible types before execution")

    process.contract = declaration(type_="bytes", kind="blob")
    app = app_for(tmp_path, process)
    req = request(
        steps={
            "bad": {
                "plugin": "test-process",
                "outputs": {
                    "result": {
                        "publish": {"encode": {"plugin": "geotiff"}, "data_out": {"plugin": "filesystem"}}
                    }
                },
            }
        }
    )
    with pytest.raises(AutoSTACGISError, match="cannot encode"):
        app.plan(req, [scene()])


@pytest.mark.parametrize("encoder", ["geotiff", "zarr"])
def test_checkpoint_reuse_content_invalidation_and_corruption(tmp_path, monkeypatch, encoder):
    monkeypatch.setattr("autostacgis.runtime.storage.implementation_identity", lambda function: ["test-code"])
    calls = []

    def process(inputs, settings, context):
        calls.append(1)
        return {
            "result": [
                context.output("result", inputs["images"][0].payload, facts={"properties.processed": True})
            ]
        }

    process.contract = declaration()
    app = app_for(tmp_path, process)
    req = request(checkpoint={"plugin": "file-checkpoint", "settings": {"encoders": {"raster": encoder}}})
    for _ in range(2):
        with app.run(req, [scene()]) as result:
            assert result.records[0].payload.read(Window(0, 0, 4, 4)).pixels.mean() == 10
    assert len(calls) == 1
    manifests = list((tmp_path / "work/checkpoints").glob("*/manifest.json"))
    assert len(manifests) == 1
    saved = json.loads(manifests[0].read_text())["outputs"]["result"][0]
    stored = Path(saved["path"])
    damaged = next(p for p in stored.rglob("*") if p.is_file()) if stored.is_dir() else stored
    damaged.write_bytes(b"corrupt raster")
    with app.run(req, [scene()]):
        pass
    assert len(calls) == 2
    with app.run(req, [scene(value=20)]):
        pass
    assert len(calls) == 3


def test_processing_generator_resumes_an_intermediate_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("autostacgis.runtime.storage.implementation_identity", lambda function: ["test-generator"])
    calls = []

    def process(inputs, settings, context):
        saved = context.restore_checkpoint("part")
        if saved:
            return saved
        calls.append(1)
        outputs = {
            "result": [
                context.output("result", inputs["images"][0].payload, facts={"properties.processed": True})
            ]
        }
        yield Checkpoint("part", outputs)
        raise RuntimeError("interrupted after saving")

    process.contract = declaration()
    app = app_for(tmp_path, process)
    req = request(checkpoint={"plugin": "file-checkpoint"})
    with pytest.raises(RuntimeError, match="interrupted"):
        app.run(req, [scene()])
    with app.run(req, [scene()]) as result:
        assert result.records[0].item["properties"]["processed"] is True
    assert len(calls) == 1


def test_checkpoint_locations_and_checks_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr("autostacgis.runtime.storage.implementation_identity", lambda function: ["locations"])
    app = Application(subset(), base_dir=tmp_path)
    image = scene()
    image.item["properties"]["data_state:directory_root"] = str(tmp_path / "product")
    for relative, root in [("config", tmp_path), ("cwd", Path.cwd()), ("item", tmp_path / "product")]:
        provider = FileCheckpoint(
            {"relative_to": relative, "directory": "saved"}, app.scope("file-checkpoint")
        )
        assert provider.directory({"image": [image]}) == root / "saved"
    # A configured check rejects an otherwise intact checkpoint and causes a fresh execution.
    checks = []

    def reject(inputs, settings, context):
        checks.append(1)
        return "fail"

    app.registry.register("reject-check", {"checks": reject})
    config = app.catalog.copy()
    config["plugins"] = [*app.catalog["plugins"], app.registry.definition("reject-check")]
    app = Application(config, registry=app.registry, base_dir=tmp_path)
    provider = FileCheckpoint(
        {"directory": "cache", "encoders": {"raster": "geotiff"}, "checks": [{"plugin": "reject-check"}]},
        app.scope("file-checkpoint", {"checks": [{"plugin": "reject-check"}]}),
    )
    provider.save("abc", {"result": [image]}, {"image": [image]})
    assert provider.load("abc", {"image": [image]}) is None
    assert checks and not (tmp_path / "cache/abc").exists()


def test_metadata_can_stay_in_memory_or_save_selected_value(tmp_path):
    app = Application(subset(), base_dir=tmp_path)
    image = scene()
    updated = publish(
        app,
        image,
        {
            "metadata_out": {
                "plugin": "stac-item",
                "settings": {"set": {"properties.example:status": "ready"}},
            }
        },
    )
    assert updated.item["properties"]["example:status"] == "ready"
    image.item["assets"]["data"]["href"] = str(tmp_path / "data.tif")
    updated = publish(
        app,
        image,
        {
            "metadata_out": {
                "plugin": "stac-json",
                "settings": {
                    "path": "properties.json",
                    "value_path": "properties",
                    "href_field": "properties.example:metadata",
                },
            }
        },
    )
    assert json.loads((tmp_path / "properties.json").read_text()) == updated.item["properties"]


def test_configured_transfer_handles_every_step_edge_and_preferences(tmp_path):
    seen = []

    class AuditTransfer(BufferedTransfer):
        def transfer(self, value, types, *, mode="optimized", shared=False):
            seen.append((value.type, mode))
            return super().transfer(value, types, mode=mode, shared=shared)

    def process(inputs, settings, context):
        assert isinstance(inputs["images"][0].payload, FileRaster)
        return {
            "result": [
                context.output("result", inputs["images"][0].payload, facts={"properties.processed": True})
            ]
        }

    process.contract = declaration()
    registry = Registry()
    registry.register("audit-transfer", {"data_transfer": AuditTransfer})
    registry.register("test-process", {"process": process})
    config = subset()
    config["plugins"].extend([registry.definition("audit-transfer"), registry.definition("test-process")])
    app = Application(config, registry=registry, base_dir=tmp_path)
    with app.run(
        request(
            data_transfer={"plugin": "audit-transfer"},
            steps={"work": {"plugin": "test-process", "transfer": "file"}},
        ),
        [scene()],
    ):
        assert seen == [("raster", "file"), ("raster", "file")]


def test_invalid_checkpoint_manifest_cannot_delete_an_external_input(tmp_path):
    app = Application(subset(), base_dir=tmp_path)
    provider = FileCheckpoint({"directory": "checkpoints"}, app.scope("file-checkpoint"))
    outside = tmp_path / "source.tif"
    outside.write_bytes(b"source")
    entry = tmp_path / "checkpoints/key"
    entry.mkdir(parents=True)
    (entry / "manifest.json").write_text(
        json.dumps({"key": "key", "outputs": {"result": [{"path": str(outside)}]}})
    )
    assert provider.load("key", {}) is None
    assert outside.read_bytes() == b"source" and not entry.exists()


def test_large_declared_bytes_spill_before_publication(tmp_path):
    def process(inputs, settings, context):
        return {
            "result": [
                context.output("result", Artifact("bytes", b"a" * 50), facts={"properties.processed": True})
            ]
        }

    process.contract = declaration(type_="bytes", kind="blob")
    app = app_for(tmp_path, process)
    req = request(data_transfer={"plugin": "buffered-transfer", "settings": {"memory_bytes": 4}})
    req["goal"]["output"]["publish"] = {
        "data_out": {"plugin": "filesystem", "settings": {"path": "large.bin"}}
    }
    with app.run(req, [scene()]):
        assert (tmp_path / "large.bin").read_bytes() == b"a" * 50


def test_remote_destination_does_not_update_original_input_metadata(tmp_path):
    import hashlib

    from autostacgis.data.writers import write_raster

    def remote(value, settings, context):
        return Artifact("path", "s3://example/result.tif")

    remote.input_types = ["path"]
    remote.output_types = ["path"]
    registry = Registry()
    registry.register("test-remote", {"data_out": remote})
    config = subset()
    config["plugins"].append(registry.definition("test-remote"))
    app = Application(config, registry=registry, base_dir=tmp_path)
    image = scene()
    source = tmp_path / "source.tif"
    write_raster(image.payload, source)
    image.runtime["local_assets"] = {"data": str(source)}
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = publish(app, image, {"encode": {"plugin": "geotiff"}, "data_out": {"plugin": "test-remote"}})
    assert result.item["assets"]["data"]["href"] == "s3://example/result.tif"
    assert "data" not in result.runtime["local_assets"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_file_path_transfer_opens_native_raster_and_catalog_execution_defaults(tmp_path):
    from types import SimpleNamespace

    from autostacgis.data.writers import write_raster

    path = tmp_path / "input.tif"
    write_raster(scene().payload, path)
    provider = BufferedTransfer({"memory_bytes": 0}, SimpleNamespace(work=tmp_path))
    try:
        raster = provider.transfer(Artifact("path", path), ["raster"], mode="optimized")
        assert isinstance(raster.value, FileRaster)
    finally:
        provider.close()

    def process(inputs, settings, context):
        assert isinstance(inputs["images"][0].payload, FileRaster)
        return {
            "result": [
                context.output("result", inputs["images"][0].payload, facts={"properties.processed": True})
            ]
        }

    process.contract = declaration()
    app = app_for(tmp_path, process)
    config = app.catalog
    next(p for p in config["plugins"] if p["id"] == "test-process")["capabilities"]["process"][
        "execution"
    ] = {"transfer": "file"}
    app = Application(config, registry=app.registry, base_dir=tmp_path)
    with app.run(request(), [scene()]):
        pass


def test_cog_publication_preserves_layout_and_embeds_final_item(tmp_path):
    import rasterio

    app = Application(subset(), base_dir=tmp_path)
    image = scene()
    result = publish(
        app,
        image,
        {
            "encode": {"plugin": "cog"},
            "data_out": {"plugin": "filesystem", "settings": {"path": "result.tif"}},
        },
    )
    with rasterio.open(tmp_path / "result.tif") as dataset:
        assert dataset.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert json.loads(dataset.tags()["STAC"]) == result.item


def test_named_steps_split_outputs_then_combine_for_separate_processing(tmp_path):
    sizes = []

    def process(inputs, settings, context):
        sizes.append(len(inputs["images"]))
        return {
            "result": [
                context.output("result", r.payload, index=i, facts={"properties.processed": True})
                for i, r in enumerate(inputs["images"])
            ]
        }

    process.contract = declaration(many=True)
    app = app_for(tmp_path, process)
    req = {
        "goal": {"for_each": {"require": {"properties.processed": True}}},
        "steps": {
            "separately": {
                "plugin": "test-process",
                "sequence": {"images": {"plugin": "split", "settings": {"size": 2}}},
            },
            "together": {
                "plugin": "test-process",
                "inputs": {"images": "result:separately:result"},
                "sequence": {"images": {"plugin": "combine"}},
            },
        },
    }
    with app.run(req, [scene(str(i)) for i in range(4)]) as result:
        assert len(result.plan.steps) == 3 and len(result.records) == 4
    assert sorted(sizes) == [2, 2, 4]


def test_metadata_destination_uses_actual_remote_reference(tmp_path):
    documents = []

    def destination(value, settings, context):
        documents.append(json.loads(b"".join(value.chunks())))
        return Artifact("path", "s3://example/metadata/item.json")

    destination.input_types = ["bytes", "byte-stream", "path"]
    destination.output_types = ["path"]
    registry = Registry()
    registry.register("metadata-destination", {"data_out": destination})
    config = subset()
    config["plugins"].append(registry.definition("metadata-destination"))
    app = Application(config, registry=registry, base_dir=tmp_path)
    image = scene()
    image.item["assets"]["data"]["href"] = str(tmp_path / "source.tif")
    result = publish(
        app,
        image,
        {
            "metadata_out": {
                "plugin": "stac-json",
                "settings": {
                    "data_out": {"plugin": "metadata-destination"},
                    "href_field": "properties.example:metadata",
                },
            }
        },
    )
    assert result.item["properties"]["example:metadata"] == "s3://example/metadata/item.json"
    assert "example:metadata" not in documents[0]["properties"]


def test_checkpoint_checks_can_bind_named_output_collections(tmp_path):
    app = Application(subset(), base_dir=tmp_path)
    settings = {
        "directory": "checkpoints",
        "encoders": {"raster": "geotiff"},
        "checks": [
            {
                "plugin": "metadata-matches",
                "inputs": {"subject": "result"},
                "settings": {"equals": {"properties.platform": "test"}},
            }
        ],
    }
    provider = FileCheckpoint(settings, app.scope("file-checkpoint", settings))
    provider.save("example", {"result": [scene()]}, {})
    assert provider.load("example", {})["result"][0].item["properties"]["platform"] == "test"


def test_checkpoint_item_locations_handle_file_uris_and_reject_remote_roots(tmp_path):
    app = Application(subset(), base_dir=tmp_path)
    image = scene()
    image.item["properties"]["data_state:directory_root"] = tmp_path.as_uri()
    provider = FileCheckpoint({"relative_to": "item", "directory": "saved"}, app.scope("file-checkpoint"))
    assert provider.directory({"image": [image]}) == tmp_path / "saved"
    image.item["properties"]["data_state:directory_root"] = "s3://example/product"
    with pytest.raises(AutoSTACGISError, match="local item directory"):
        provider.directory({"image": [image]})
    provider = FileCheckpoint(
        {"relative_to": "item", "directory": str(tmp_path / "absolute")}, app.scope("file-checkpoint")
    )
    assert provider.directory({}) == tmp_path / "absolute"
