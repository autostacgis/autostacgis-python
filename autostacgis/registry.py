"""One implementation registry; each plugin exposes explicit capabilities."""

from collections.abc import Mapping
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
    "services",
    "extensions",
)
INTERFACES = CAPABILITIES


class Registry:
    def __init__(self, builtins=True):
        self.plugins = {}
        self.requirements = {}
        self.defaults = {}
        if builtins:
            from .plugins import install

            install(self)
        self.entries = {}
        for entry in entry_points(group="autostacgis.plugins"):
            if entry.name in self.entries:
                raise AutoSTACGISError(f"Duplicate registration in autostacgis.plugins: {entry.name}")
            self.entries[entry.name] = entry

    def load(self, name, active=()):
        if name in active:
            raise AutoSTACGISError("Recursive plugin dependency: " + " -> ".join((*active, name)))
        if name not in self.plugins:
            if name in self.entries:
                provider = self.entries[name].load()
            elif name.startswith("autostacgis.plugins."):
                provider = import_module(name)
            else:
                from .plugins import builtin_names

                try:
                    provider = import_module(builtin_names()[name])
                except KeyError:
                    raise AutoSTACGISError(f"Plugin {name!r} is not registered in autostacgis.plugins") from None
            if hasattr(provider, "IMPLEMENTATIONS"):
                registered = provider.ID
                if registered not in self.plugins:
                    self.register_module(provider)
                elif self.plugins[registered] != provider.IMPLEMENTATIONS or self.requirements[
                    registered
                ] != tuple(provider.REQUIRES):
                    raise AutoSTACGISError(f"Conflicting implementation plugin: {registered}")
            else:
                self.register(name, provider)
                registered = name
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
                    **{
                        field: getattr(implementation, field)
                        for field in ("input_types", "output_types")
                        if hasattr(implementation, field)
                    },
                }
                for capability, implementation in self.plugins[name].items()
            },
        }

    def register(self, name, capabilities, *, requires=(), defaults=None):
        if not isinstance(name, str) or not name or name in self.plugins:
            raise AutoSTACGISError(f"Duplicate or invalid implementation plugin: {name}")
        if not isinstance(capabilities, Mapping) or not capabilities:
            raise AutoSTACGISError(f"Plugin {name} must expose a capability mapping")
        for capability, implementation in capabilities.items():
            if capability not in INTERFACES or not callable(implementation):
                raise AutoSTACGISError(f"Invalid implementation capability: {name}/{capability}")
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
        if isinstance(requires, str) or any(not isinstance(dep, str) or not dep for dep in requires):
            raise AutoSTACGISError(f"Invalid plugin dependencies: {name}")
        if len(set(requires)) != len(requires):
            raise AutoSTACGISError(f"Duplicate plugin dependency: {name}")
        self.plugins[name] = dict(capabilities)
        self.requirements[name] = tuple(requires)
        self.defaults[name] = defaults or {}

    def register_module(self, module):
        self.register(
            module.ID,
            module.IMPLEMENTATIONS,
            requires=module.REQUIRES,
            defaults=getattr(module, "DEFAULTS", {}),
        )

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
