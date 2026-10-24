import pytest

from autostacgis import Registry
from autostacgis.errors import AutoSTACGISError
from autostacgis.plugin_manager import PluginManager, metadata

SOURCE = '''"""Check whether a record satisfies the example condition."""
ID = "example-local"
VERSION = "1.0.0"
CAPABILITY = "checks"
REQUIRES = []
DEPENDENCIES = []
class Check:
    def __call__(self, *args): return True
IMPLEMENTATION = Check
'''


def test_file_install_uses_namespace_and_versioned_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACGIS_PLUGIN_DIR", str(tmp_path))
    manager = PluginManager(tmp_path)
    row = manager.install_file("example_local.py", SOURCE)
    assert row["module"] == "autostacgis_plugins.example_local"
    registry = Registry()
    assert registry.load("example-local") == "example-local"
    assert registry.versions["example-local"] == "1.0.0"
    assert registry.resolve("checks", "example-local").__module__ == "autostacgis_plugins.example_local"
    import base64
    import pickle
    import subprocess
    import sys

    payload = base64.b64encode(pickle.dumps(registry.resolve("checks", "example-local")())).decode()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import base64,pickle,sys; assert pickle.loads(base64.b64decode(sys.argv[1]))() is True",
            payload,
        ],
        check=True,
    )
    (tmp_path / row["path"]).write_text(SOURCE + "# tampered")
    with pytest.raises(AutoSTACGISError, match="integrity"):
        Registry()


def test_install_rejects_paths_missing_version_and_multiple_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTACGIS_PLUGIN_DIR", str(tmp_path))
    manager = PluginManager(tmp_path)
    with pytest.raises(AutoSTACGISError):
        manager.install_file("../escape.py", SOURCE)
    with pytest.raises(AutoSTACGISError, match="VERSION"):
        metadata(SOURCE.replace('VERSION = "1.0.0"', ""))
    with pytest.raises((AutoSTACGISError, TypeError)):
        metadata(SOURCE.replace('CAPABILITY = "checks"', 'CAPABILITY = ["checks", "process"]'))
    assert not (tmp_path / "installed.json").exists()


def test_manifest_install_verifies_git_source_checksum(tmp_path, monkeypatch):
    from hashlib import sha256

    import httpx

    monkeypatch.setenv("AUTOSTACGIS_PLUGIN_DIR", str(tmp_path))
    manifest = {
        "format_version": 1,
        "plugins": [
            {
                "id": "example-local",
                "description": "Check whether a record satisfies the example condition.",
                "version": "1.0.0",
                "capability": "checks",
                "requires": [],
                "module": "autostacgis_plugins.example_local",
                "source": {
                    "url": "https://raw.githubusercontent.com/example/plugins/commit/example_local.py",
                    "sha256": sha256(SOURCE.encode()).hexdigest(),
                },
            }
        ],
    }

    def fetch(url, **kwargs):
        request = httpx.Request("GET", url)
        return (
            httpx.Response(200, json=manifest, request=request)
            if url.endswith("plugins.json")
            else httpx.Response(200, text=SOURCE, request=request)
        )

    monkeypatch.setattr(httpx, "get", fetch)
    manager = PluginManager(tmp_path)
    row = manager.install("example-local", "1.0.0")
    assert row["origin"] == manifest["plugins"][0]["source"]["url"]
    manifest["plugins"][0]["source"]["sha256"] = "0" * 64
    with pytest.raises(AutoSTACGISError, match="checksum"):
        manager.install("example-local", "1.0.0")


def test_upgrading_and_rejecting_a_broken_version_preserves_active_plugin(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("AUTOSTACGIS_PLUGIN_DIR", str(tmp_path))
    manager = PluginManager(tmp_path)
    manager.install_file("example_local.py", SOURCE)
    updated = SOURCE.replace('VERSION = "1.0.0"', 'VERSION = "1.1.0"')
    manager.install_file("example_local.py", updated)
    assert Registry().versions["example-local"] == "1.1.0"
    broken = SOURCE.replace('VERSION = "1.0.0"', 'VERSION = "2.0.0"').replace(
        "IMPLEMENTATION = Check", "IMPLEMENTATION = None"
    )
    with pytest.raises(AutoSTACGISError):
        manager.install_file("example_local.py", broken)
    assert sys.modules["autostacgis_plugins.example_local"].VERSION == "1.1.0"
    assert Registry().versions["example-local"] == "1.1.0"
