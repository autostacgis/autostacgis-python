from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from autostacgis_plugins import modules
from helpers import scene, subset

from autostacgis import Application, Registry
from autostacgis.errors import AutoSTACGISError
from autostacgis.executor import Context, _execute
from autostacgis.runtime.registry import Services


def entry(config, name):
    return next(plugin for plugin in config["plugins"] if plugin["id"] == name)


def test_builtin_sources_own_their_implementations_and_declarations():
    providers = modules()
    assert len({module.ID for module in providers}) == len(providers)
    for module in providers:
        assert module.IMPLEMENTATION.__module__ == module.__name__


def test_provider_dependencies_cannot_be_removed_by_catalog():
    config = subset("pansharpen")
    entry(config, "pansharpen")["requires"] = []
    config["plugins"].remove(entry(config, "storage"))
    with pytest.raises(AutoSTACGISError, match="unavailable: storage"):
        Application(config)


def test_declared_capability_calls_are_scoped_to_the_callee():
    config = subset("pansharpen")
    app = Application(config)
    image = scene()
    assert (
        app.scope("compatible-optical").call(
            "same-fields",
            "checks",
            {"left": [image], "right": [image]},
            {"fields": ["properties.platform"]},
        )
        == "pass"
    )
    with pytest.raises(AutoSTACGISError, match="undeclared dependency same-fields"):
        app.scope("pansharpen").call("same-fields", "checks", {}, {})
    # A dependency's dependencies are not automatically granted to its caller.
    entry(config, "pansharpen")["requires"] = list(
        set(entry(config, "pansharpen")["requires"]) | {"compatible-optical"}
    )
    app = Application(config)
    with pytest.raises(AutoSTACGISError, match="undeclared dependency same-fields"):
        app.scope("pansharpen").call("same-fields", "checks", {}, {})


def test_execution_context_enforces_service_access(tmp_path):
    app = Application(subset("pansharpen"))
    with Services(app.registry, app.catalog["runtime"], directory=tmp_path, plugins=app.plugins) as services:
        context = Context(services, tmp_path, {}, "pansharpen", owner="pansharpen")
        assert context.storage.limit > 0
        for get in (context.service, context.services.get):
            with pytest.raises(AutoSTACGISError, match="undeclared dependency multiprocessing"):
                get("multiprocessing")
        with pytest.raises(AutoSTACGISError, match="undeclared dependency executor"):
            _ = context.executor


def test_declared_multiprocessing_reuses_the_global_pool(tmp_path):
    config = subset("pansharpen")
    entry(config, "pansharpen")["requires"].append("multiprocessing")
    config["runtime"]["executor"] = {
        "implementation": "executor",
        "settings": {"backend": "multiprocessing", "workers": 2},
    }
    app = Application(config)
    with Services(app.registry, config["runtime"], directory=tmp_path, plugins=app.plugins) as services:
        executor = services.get("executor")
        context = Context(services, tmp_path, {}, "pansharpen", owner="pansharpen")
        assert context.service("multiprocessing") is executor
        assert list(context.service("multiprocessing").map(abs, [-1, -2, -3])) == [1, 2, 3]
        # Serialization into an occupied worker must not create another process pool.
        state = context.__getstate__()
        worker = Context.__new__(Context)
        worker.__setstate__(state)
        try:
            assert worker.service("multiprocessing").workers == 1
            assert worker.service("multiprocessing").pool is None
        finally:
            worker._services.__exit__(None, None, None)


def test_incompatible_backend_cannot_create_another_pool(tmp_path):
    with Services(Registry(), {}, directory=tmp_path) as services:
        services.get("executor")
        with pytest.raises(AutoSTACGISError, match="already active"):
            services.get("multiprocessing")


def test_service_dependencies_are_checked_and_instances_closed_once(tmp_path):
    registry = Registry(builtins=False)
    calls = []

    class Resource:
        def __init__(self, settings, services):
            calls.append("open")

        def close(self):
            calls.append("close")

    def consumer(settings, services):
        return services.get("resource")

    registry.register("resource", "services", Resource)
    registry.register("consumer", "services", consumer, requires=["resource"])
    registry.register("undeclared", "services", consumer)
    with Services(registry, {}, directory=tmp_path) as services:
        with pytest.raises(AutoSTACGISError, match="undeclared dependency resource"):
            services.get("undeclared")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: services.get("consumer"), range(8)))
        assert all(result is results[0] for result in results)
        assert results[0] is services.get("resource")
        assert calls == ["open"]
    assert calls == ["open", "close"]


def test_service_cycles_fail_before_construction(tmp_path):
    registry = Registry(builtins=False)
    registry.register("a", "services", lambda *_: pytest.fail("constructed"), requires=["b"])
    registry.register("b", "services", lambda *_: pytest.fail("constructed"), requires=["a"])
    with pytest.raises(AutoSTACGISError, match="Recursive plugin dependency"):
        Services(registry, {}, directory=tmp_path)


def test_module_entry_point_carries_dependencies(monkeypatch):
    import autostacgis.registry as module

    provider = SimpleNamespace(
        ID="example",
        VERSION="0.0.1",
        REQUIRES=("storage",),
        CAPABILITY="process",
        IMPLEMENTATION=lambda *_: None,
    )
    monkeypatch.setattr(
        module, "entry_points", lambda **_: [SimpleNamespace(name="example", load=lambda: provider)]
    )
    registry = Registry(builtins=False)
    assert "example" not in registry.plugins
    registry.load("example")
    assert registry.requirements["example"] == ("storage",)


def test_binding_extension_uses_its_own_dependencies(tmp_path):
    app = Application(subset("pansharpen"))
    with Services(app.registry, app.catalog["runtime"], directory=tmp_path, plugins=app.plugins) as services:
        context = Context(services, tmp_path, {}, "pansharpen", owner="pansharpen")
        context.extensions = {"autostacgis.execution/v1": {"tile_size": 32}}
        assert _execute(lambda inputs, settings, context: context.storage.tile_size, {}, {}, context) == 32
        with pytest.raises(AutoSTACGISError, match="undeclared dependency executor"):
            _ = context.executor


def test_declared_action_delegation(tmp_path):
    config = subset("apply_mask", "mask_by_polygons")
    registry = Registry()

    def apply(inputs, settings, context):
        assert context.owner == "apply_mask"
        return {"result": inputs["image"]}

    registry.register("test-mask", "process", apply)
    entry(config, "apply_mask")["capabilities"]["process"]["implementation"] = "test-mask"
    app = Application(config, registry=registry)
    with Services(registry, config["runtime"], directory=tmp_path, plugins=app.plugins) as services:
        context = Context(services, tmp_path, {}, "mask_by_polygons", owner="mask_by_polygons")
        records = [scene()]
        assert context.call("apply_mask", "process", {"image": records}) == {"result": records}


def test_no_sources_runs_without_implicit_discovery(tmp_path):
    app = Application(subset("pansharpen"), base_dir=tmp_path)
    assert (
        app.discover(
            {
                "version": "0.0.1",
                "goal": {
                    "for_each": {
                        "kind": "raster",
                        "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                        "require": {
                            "all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]
                        },
                    }
                },
            }
        )
        == []
    )
