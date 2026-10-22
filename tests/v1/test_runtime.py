import numpy as np
import pytest
from helpers import capabilities, scene, subset

from autostacgis import Application, Registry
from autostacgis.data import ArrayRaster, FileRaster, Window, ZarrRaster
from autostacgis.data.writers import write_raster
from autostacgis.errors import AutoSTACGISError
from autostacgis.plugins.multiprocessing import Processes
from autostacgis.plugins.sequential import Sequential
from autostacgis.plugins.threads import Threads as Executor
from autostacgis.runtime.registry import Services


def square(x):
    return x * x


def nested(argument):
    executor, depth = argument
    if depth == 0:
        return 1
    return sum(executor.map(nested, [(executor, depth - 1)] * 2))


@pytest.mark.parametrize("factory", [Executor, Processes, Sequential])
def test_arbitrary_nested_work_cannot_deadlock(factory):
    with factory({"workers": 2}) as executor:
        result = executor.submit(nested, (executor, 4)).result(timeout=30)
        assert result == 16
        assert list(executor.scope(preferred_workers=1).map(square, range(5))) == [0, 1, 4, 9, 16]


def test_window_is_readonly_shared_view():
    raster = scene().payload
    block = raster.read(Window(0, 0, 2, 2))
    assert np.shares_memory(raster.pixels, block.pixels)
    assert not block.pixels.flags.writeable
    with pytest.raises(ValueError):
        block.pixels[0, 0, 0] = 99


def test_shared_transport_and_budget_spill(tmp_path):
    with Services(
        Registry(),
        {"storage": {"implementation": "storage", "settings": {"memory_bytes": 1024}}},
        directory=tmp_path,
    ) as services:
        storage = services.get("storage")
        raster = scene().payload
        shared = storage.materialize(raster, shared=True)
        with Processes({"workers": 2}) as executor:
            assert executor.submit(read_sum, shared).result(timeout=30) == 480
        large = ArrayRaster(np.ones((1, 64, 64), dtype="float32"))
        spilled = storage.materialize(large)
        assert isinstance(spilled, FileRaster)
        assert read_sum(spilled) == 4096


def read_sum(raster):
    return float(sum(block.pixels.sum() for block in raster.blocks(tile_size=8)))


def test_native_processing_and_stac_validation(tmp_path):
    config = subset("pansharpen")
    app = Application(config, base_dir=tmp_path)
    with app.run(
        {"version": "1.0.0", "goal": {"plugin": "pansharpened"}},
        [scene(), scene("pan", "panchromatic_imagery", value=20, count=1)],
    ) as result:
        assert len(result.records) == 1
        record = result.records[0]
        np.testing.assert_allclose(record.payload.read(Window(0, 0, 4, 4)).pixels, 10)
        app.stac.validate(record.item)
        assert record.item["assets"]["data"]["data_state:pansharpened"] is True


def test_discovery_never_executes(tmp_path):
    registry = Registry()
    registry.register(
        "must-not-run", {"process": lambda *args: (_ for _ in ()).throw(AssertionError("executed"))}
    )
    registry.resolve("process", "must-not-run").contract = registry.resolve("process", "pansharpen").contract
    config = subset("pansharpen")
    capabilities(config, "process")[0]["implementation"] = "must-not-run"
    app = Application(config, base_dir=tmp_path, registry=registry)
    plan = app.run(
        {"version": "1.0.0", "goal": {"plugin": "pansharpened"}, "discovery": True},
        [scene(), scene("pan", "panchromatic_imagery", count=1)],
    )
    assert len(plan.steps) == 1
    assert not (tmp_path / "work").exists()


def test_false_success_is_rejected(tmp_path):
    registry = Registry()
    registry.register(
        "bad", {"process": lambda inputs, settings, context: {"result": [inputs["multispectral"][0]]}}
    )
    registry.resolve("process", "bad").contract = registry.resolve("process", "pansharpen").contract
    config = subset("pansharpen")
    capabilities(config, "process")[0]["implementation"] = "bad"
    with pytest.raises(AutoSTACGISError, match="violates promised"):
        Application(config, base_dir=tmp_path, registry=registry).run(
            {"version": "1.0.0", "goal": {"plugin": "pansharpened"}},
            [scene(), scene("pan", "panchromatic_imagery", count=1)],
        )


def test_zarr_concurrent_chunks_and_stac_delivery(tmp_path):
    config = subset("pansharpen")
    app = Application(config, base_dir=tmp_path)
    request = {
        "version": "1.0.0",
        "goal": {
            "plugin": "pansharpened",
            "settings": {
                "publish": {
                    "encode": {"plugin": "zarr"},
                    "data_out": {"plugin": "filesystem"},
                    "metadata_out": {"plugin": "stac-json"},
                }
            },
        },
    }
    with app.run(request, [scene(), scene("pan", "panchromatic_imagery", value=20, count=1)]) as result:
        import zarr

        record = result.records[0]
        path = record.item["assets"]["data"]["href"]
        assert zarr.open_group(path, mode="r").attrs["stac"] == record.item
        np.testing.assert_allclose(ZarrRaster(path).read(Window(0, 0, 4, 4)).pixels, 10)
    assert len(list((tmp_path / "output/stac").glob("*.json"))) == 1


def test_raster_mask_roundtrip(tmp_path):
    raster = scene().payload
    mask = np.ones(raster.grid.shape, dtype=bool)
    mask[:, 1, 1] = False
    raster = ArrayRaster(raster.pixels, raster.grid, mask)
    path = tmp_path / "image.tif"
    write_raster(raster, path, tile_size=2)
    block = FileRaster(path).read(Window(0, 0, 4, 4))
    assert not block.valid[:, 1, 1].any()


def test_multiprocessing_executes_native_pipeline(tmp_path):
    config = subset("pansharpen")
    config["runtime"]["executor"] = {"implementation": "multiprocessing", "settings": {"workers": 2}}
    app = Application(config, base_dir=tmp_path)
    with app.run(
        {"version": "1.0.0", "goal": {"plugin": "pansharpened"}},
        [scene(), scene("pan", "panchromatic_imagery", count=1, value=20)],
    ) as result:
        np.testing.assert_allclose(result.records[0].payload.read(Window(0, 0, 4, 4)).pixels, 10)


def test_shared_view_keeps_mapping_alive(tmp_path):
    import gc

    with Services(
        Registry(), {"storage": {"implementation": "storage", "settings": {}}}, directory=tmp_path
    ) as services:
        shared = services.get("storage").materialize(scene().payload, shared=True)
        block = shared.read(Window(0, 0, 4, 4))
        view = block.pixels[:, 1:3, 1:3]
        del block
        gc.collect()
        assert view.sum() == 120


def test_dask_executor_uses_existing_cluster(tmp_path):
    distributed = pytest.importorskip("distributed")
    from autostacgis.plugins.dask import Dask

    with distributed.LocalCluster(
        n_workers=2, threads_per_worker=1, processes=False, dashboard_address=None
    ) as cluster:
        with Dask({"address": cluster.scheduler_address, "workers": 2}) as executor:
            assert list(executor.map(abs, [-1, -2, -3])) == [1, 2, 3]


def test_process_executor_integrates_transfer_and_checkpoint(tmp_path):
    config = subset("pansharpen")
    config["runtime"]["executor"] = {
        "implementation": "executor",
        "settings": {"backend": "multiprocessing", "workers": 2},
    }
    app = Application(config, base_dir=tmp_path)
    request = {"goal": {"plugin": "pansharpened"}, "checkpoint": {"plugin": "file-checkpoint"}}
    for _ in range(2):
        with app.run(request, [scene(), scene("pan", "panchromatic_imagery", value=20, count=1)]) as result:
            np.testing.assert_allclose(result.records[0].payload.read(Window(0, 0, 4, 4)).pixels, 10)
            assert result.records[0].runtime["checkpoint"].endswith("manifest.json")


def make_byte_stream(inputs, settings, context):
    from autostacgis import Artifact

    return {
        "result": [
            context.output(
                "result",
                Artifact("byte-stream", lambda: iter([b"worker", b" stream"])),
                facts={"properties.example:streamed": True},
            )
        ]
    }


make_byte_stream.contract = {
    "argument_requirements": {"image": {"kind": "raster", "data_types": ["raster"]}},
    "outputs": {
        "result": {
            "kind": "blob",
            "data_types": ["byte-stream"],
            "inherit_metadata": ["image"],
            "set": [{"properties.example:streamed": True}],
        }
    },
}


def test_worker_byte_stream_is_transferred_before_serialization(tmp_path):
    registry = Registry()
    registry.register("worker-stream", {"process": make_byte_stream})
    config = subset()
    config["plugins"].append(registry.definition("worker-stream"))
    config["runtime"]["executor"] = {
        "implementation": "executor",
        "settings": {"backend": "multiprocessing", "workers": 2},
    }
    app = Application(config, base_dir=tmp_path, registry=registry)
    request = {
        "goal": {
            "output": {
                "kind": "blob",
                "require": {"properties.example:streamed": True},
                "publish": {"data_out": {"plugin": "filesystem", "settings": {"path": "stream.bin"}}},
            }
        }
    }
    with app.run(request, [scene()]):
        assert (tmp_path / "stream.bin").read_bytes() == b"worker stream"
