"""One implementation registry; each plugin declares one explicit capability."""

import re
from copy import deepcopy
from importlib import import_module
from importlib.metadata import entry_points

from .errors import AutoSTACGISError

CAPABILITIES = (
    "trigger",
    "data_in",
    "metadata_in",
    "checks",
    "sequence",
    "data_transfer",
    "checkpoint",
    "encode",
    "process",
    "data_out",
    "metadata_out",
    "goal",
    "solver",
    "services",
    "extensions",
)
INTERFACES = CAPABILITIES


class Registry:
    def __init__(self, builtins=True, *, plugin_directory=None):
        self.plugins = {}
        self.requirements = {}
        self.defaults = {}
        self.versions = {}
        if builtins:
            from autostacgis_plugins import install

            install(self)
        self.entries = {}
        for entry in entry_points(group="autostacgis.plugins"):
            if entry.name in self.entries:
                raise AutoSTACGISError(f"Duplicate registration in autostacgis.plugins: {entry.name}")
            if entry.name in self.plugins:
                implementation = next(iter(self.plugins[entry.name].values()))
                if getattr(entry, "value", None) != implementation.__module__:
                    raise AutoSTACGISError(f"Duplicate plugin name: {entry.name}")
            self.entries[entry.name] = entry

        from .plugin_manager import PluginManager

        PluginManager(plugin_directory).load_into(self)

    def load(self, name, active=()):
        if name in active:
            raise AutoSTACGISError("Recursive plugin dependency: " + " -> ".join((*active, name)))
        if name not in self.plugins:
            if name in self.entries:
                provider = self.entries[name].load()
            elif name.startswith("autostacgis_plugins."):
                provider = import_module(name)
            else:
                from autostacgis_plugins import builtin_names

                try:
                    provider = import_module(builtin_names()[name])
                except KeyError:
                    raise AutoSTACGISError(
                        f"Plugin {name!r} is not registered in autostacgis.plugins"
                    ) from None
            self.validate_module(provider)
            if name in self.entries and provider.ID != name:
                raise AutoSTACGISError(f"Registration {name} must use its unique plugin ID {provider.ID}")
            if hasattr(provider, "ID"):
                registered = provider.ID
                if registered not in self.plugins:
                    self.register_module(provider)
                elif self.plugins[registered] != {
                    provider.CAPABILITY: provider.IMPLEMENTATION
                } or self.requirements[registered] != tuple(provider.REQUIRES):
                    raise AutoSTACGISError(f"Conflicting implementation plugin: {registered}")
            else:
                raise AutoSTACGISError(f"Invalid plugin module: {name}")
        else:
            registered = name
        for dependency in self.requirements[registered]:
            self.load(dependency, (*active, name))
        return registered

    def definition(self, name):
        return {
            "id": name,
            "requires": list(self.requirements[name]),
            "capabilities": {
                capability: {
                    "implementation": name,
                    "settings": self.defaults[name].get(capability, {}),
                    **(
                        {"contract": deepcopy(implementation.contract)}
                        if capability in ("goal", "solver")
                        else {}
                    ),
                    **{
                        field: getattr(implementation, field)
                        for field in ("input_types", "output_types")
                        if hasattr(implementation, field)
                    },
                }
                for capability, implementation in self.plugins[name].items()
            },
        }

    def register(self, name, capability, implementation, *, requires=(), defaults=None):
        if not isinstance(name, str) or not name.strip() or name != name.strip() or name in self.plugins:
            raise AutoSTACGISError(f"Duplicate or invalid implementation plugin: {name}")
        if not isinstance(capability, str) or capability not in INTERFACES or not callable(implementation):
            raise AutoSTACGISError(
                f"Plugin {name} must declare exactly one valid capability and implementation"
            )
        if capability in ("data_out", "encode", "data_transfer"):
            for field in ("input_types", "output_types"):
                types = getattr(implementation, field, None)
                if (
                    not isinstance(types, (list, tuple))
                    or not types
                    or any(not isinstance(t, str) or not t for t in types)
                ):
                    raise AutoSTACGISError(
                        f"{name}/{capability}: {field} must declare supported exchange types"
                    )
        if not isinstance(requires, (list, tuple)) or any(
            not isinstance(dep, str) or not dep.strip() or dep != dep.strip() for dep in requires
        ):
            raise AutoSTACGISError(f"Invalid plugin dependencies: {name}")
        if len(set(requires)) != len(requires):
            raise AutoSTACGISError(f"Duplicate plugin dependency: {name}")
        if capability in ("goal", "solver"):
            from .solvers import validate_contract

            validate_contract(name, capability, implementation, requires)
        self.plugins[name] = {capability: implementation}
        self.requirements[name] = tuple(requires)
        self.defaults[name] = {capability: defaults or {}}

    @staticmethod
    def validate_module(module):
        if hasattr(module, "IMPLEMENTATIONS") or not all(
            hasattr(module, field) for field in ("ID", "VERSION", "CAPABILITY", "IMPLEMENTATION", "REQUIRES")
        ):
            raise AutoSTACGISError(
                "Plugin modules must explicitly declare ID, VERSION, CAPABILITY, IMPLEMENTATION, and REQUIRES; "
                "plural IMPLEMENTATIONS declarations are not supported"
            )

        if not isinstance(module.VERSION, str) or not re.fullmatch(
            r"\d+\.\d+\.\d+(?:[-+][\w.-]+)?", module.VERSION
        ):
            raise AutoSTACGISError("Plugin VERSION must be a semantic version string")

    def register_module(self, module):
        self.validate_module(module)
        self.register(
            module.ID,
            module.CAPABILITY,
            module.IMPLEMENTATION,
            requires=module.REQUIRES,
            defaults=getattr(module, "DEFAULTS", {}),
        )
        self.versions[module.ID] = module.VERSION

    def service_plugins(self):
        return {
            name: {
                "id": name,
                "requires": list(self.requirements[name]),
                "capabilities": {
                    "services": {"implementation": name, "settings": self.defaults[name].get("services", {})}
                },
            }
            for name, capabilities in self.plugins.items()
            if "services" in capabilities
        }

    def resolve(self, capability, name):
        try:
            return self.plugins[name][capability]
        except KeyError:
            raise AutoSTACGISError(f"Unavailable implementation: {name} capability {capability}") from None
