"""Versioned, checksum verified single-file plugins in the shared Python namespace."""

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

import httpx
from packaging.requirements import Requirement
from packaging.version import Version

from .errors import AutoSTACGISError
from .registry import CAPABILITIES

REGISTRY_URL = "https://autostacgis.github.io/autostacgis-plugins/plugins.json"


def metadata(source):
    """Read publication metadata without executing a submitted Python file."""
    tree = ast.parse(source)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for name in node.targets:
                if isinstance(name, ast.Name) and name.id == "IMPLEMENTATIONS":
                    raise AutoSTACGISError("Plural IMPLEMENTATIONS is forbidden; declare one CAPABILITY")
                if isinstance(name, ast.Name) and name.id in (
                    "ID",
                    "VERSION",
                    "CAPABILITY",
                    "REQUIRES",
                    "DEPENDENCIES",
                    "DESCRIPTION",
                ):
                    values[name.id] = ast.literal_eval(node.value)
    for name in ("ID", "VERSION", "CAPABILITY", "REQUIRES"):
        if name not in values:
            raise AutoSTACGISError(f"Plugin must declare literal {name}")
    if not isinstance(values["ID"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", values["ID"]):
        raise AutoSTACGISError("Invalid unique plugin ID")
    if not isinstance(values["VERSION"], str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-+][\w.-]+)?", values["VERSION"]
    ):
        raise AutoSTACGISError("VERSION must be a semantic version string")
    Version(values["VERSION"])
    if values["CAPABILITY"] not in CAPABILITIES:
        raise AutoSTACGISError("Plugin must declare exactly one valid CAPABILITY")
    for key in ("REQUIRES", "DEPENDENCIES"):
        value = values.setdefault(key, [])
        if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v for v in value):
            raise AutoSTACGISError(f"Invalid {key}")
        if len(set(value)) != len(value):
            raise AutoSTACGISError(f"Duplicate {key}")
    for dependency in values["DEPENDENCIES"]:
        Requirement(dependency)
    return values


class PluginManager:
    def __init__(self, directory=None, registry_url=REGISTRY_URL):
        self.directory = Path(
            directory or os.environ.get("AUTOSTACGIS_PLUGIN_DIR", Path.home() / ".autostacgis/plugins")
        )
        self.registry_url = registry_url

    def installed(self):
        path = self.directory / "installed.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def catalog(self):
        if not self.registry_url.startswith("https://"):
            raise AutoSTACGISError("Plugin manifest must use HTTPS")
        response = httpx.get(self.registry_url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        result = response.json()
        if result.get("format_version") != 1 or not isinstance(result.get("plugins"), list):
            raise AutoSTACGISError("Unsupported plugin manifest")
        ids = [row["id"] for row in result["plugins"]]
        if len(ids) != len(set(ids)):
            raise AutoSTACGISError("Plugin manifest contains duplicate names")
        return result

    def install(self, name, version=None, active=()):
        if name in active:
            raise AutoSTACGISError("Recursive plugin installation dependency")
        row = next((p for p in self.catalog()["plugins"] if p["id"] == name), None)
        if row is None or (version and row["version"] != version):
            raise AutoSTACGISError(f"Plugin version is not published: {name} {version or ''}")
        from .registry import Registry

        known = Registry(builtins=True, plugin_directory=self.directory).plugins
        for dependency in row["requires"]:
            if dependency not in known:
                self.install(dependency, active=(*active, name))
        url = row["source"]["url"]
        if not url.startswith("https://"):
            raise AutoSTACGISError("Plugin source must use HTTPS")
        response = httpx.get(url, timeout=60, follow_redirects=True)
        response.raise_for_status()
        if sha256(response.content).hexdigest() != row["source"]["sha256"]:
            raise AutoSTACGISError("Plugin checksum does not match the published manifest")
        info = metadata(response.text)
        if (info["ID"], info["VERSION"], info["CAPABILITY"]) != (name, row["version"], row["capability"]):
            raise AutoSTACGISError("Plugin declarations do not match the manifest")
        return self.install_file(row["module"].split(".")[-1] + ".py", response.text, origin=url)

    def install_file(self, filename, source, origin="local file"):
        if Path(filename).name != filename or not re.fullmatch(r"[a-z][a-z0-9_]*\.py", filename):
            raise AutoSTACGISError("Use a single Python file named with lowercase letters and underscores")
        if len(source.encode()) > 2_000_000:
            raise AutoSTACGISError("Plugin exceeds the 2 MB file limit")
        info = metadata(source)
        previous = self.installed().get(info["ID"])
        if previous and previous["version"] == info["VERSION"]:
            if previous["sha256"] != sha256(source.encode()).hexdigest():
                raise AutoSTACGISError("Bump VERSION when changing an installed plugin")
            return previous
        from autostacgis_plugins import builtin_names

        for name, module_name in builtin_names().items():
            if module_name.rsplit(".", 1)[-1] == Path(filename).stem and name != info["ID"]:
                raise AutoSTACGISError("That module filename belongs to a different published plugin ID")
        from .registry import Registry

        registry = Registry(builtins=True, plugin_directory=self.directory)
        for dependency in info["REQUIRES"]:
            registry.load(dependency)
        if info["DEPENDENCIES"]:
            uv = os.environ.get("AUTOSTACGIS_UV")
            command = (
                [uv, "pip", "install", "--python", sys.executable]
                if uv
                else [sys.executable, "-m", "pip", "install"]
            )
            subprocess.run([*command, *info["DEPENDENCIES"]], check=True)
        digest = sha256(source.encode()).hexdigest()
        folder = self.directory / "versions" / digest / "autostacgis_plugins"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        path.write_text(source, encoding="utf-8", newline="\n")
        # Import under the real namespace; relative imports of shared plugin helpers work.
        name = "autostacgis_plugins." + path.stem
        previous_module = sys.modules.get(name)
        try:
            module = self._load(path)
            registry.validate_module(module)
            if (module.ID, module.VERSION, module.CAPABILITY, tuple(module.REQUIRES)) != (
                info["ID"],
                info["VERSION"],
                info["CAPABILITY"],
                tuple(info["REQUIRES"]),
            ):
                raise AutoSTACGISError(
                    "Runtime plugin declarations must match the literal publication metadata"
                )
            for collection in (registry.plugins, registry.requirements, registry.defaults, registry.versions):
                collection.pop(module.ID, None)
            registry.register_module(module)
            registry.load(module.ID)
            installed = self.installed()
            module_name = "autostacgis_plugins." + path.stem
            if any(row["module"] == module_name and name != info["ID"] for name, row in installed.items()):
                raise AutoSTACGISError("A module filename cannot be shared by different plugin names")
            row = {
                "id": info["ID"],
                "version": info["VERSION"],
                "capability": info["CAPABILITY"],
                "module": module_name,
                "path": str(path.relative_to(self.directory)),
                "sha256": digest,
                "origin": origin,
            }
            installed[info["ID"]] = row
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as stream:
                json.dump(installed, stream, indent=2)
                temporary = stream.name
            os.replace(temporary, self.directory / "installed.json")
            return row
        except BaseException:
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module
            raise

    @staticmethod
    def _load(path):
        import autostacgis_plugins

        folder = str(path.parent)
        if folder not in autostacgis_plugins.__path__:
            autostacgis_plugins.__path__.append(folder)
        name = "autostacgis_plugins." + path.stem
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def load_into(self, registry):
        for name, row in self.installed().items():
            path = (self.directory / row["path"]).resolve()
            if (
                not path.is_relative_to(self.directory.resolve())
                or sha256(path.read_bytes()).hexdigest() != row["sha256"]
            ):
                raise AutoSTACGISError(f"Installed plugin integrity check failed: {name}")
            module = self._load(path)
            if module.ID != name or module.VERSION != row["version"]:
                raise AutoSTACGISError(f"Installed plugin identity mismatch: {name}")
            for collection in (registry.plugins, registry.requirements, registry.defaults, registry.versions):
                collection.pop(name, None)
            registry.register_module(module)
