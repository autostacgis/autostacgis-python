"""Runtime services and model extensions use registered, schema-checked handlers."""

from contextlib import ExitStack
from copy import deepcopy
from threading import RLock

from jsonschema import Draft202012Validator
from jsonschema.validators import extend

from ..dependencies import require, requirements, validate_dependencies
from ..errors import AutoSTACGISError


class ServiceContext:
    def __init__(self, services, owner, implementation):
        self._services = services
        self.owner = owner
        self.implementation = implementation

    @property
    def directory(self):
        return self._services.directory

    @property
    def events(self):
        return self._services.events

    def get(self, name, *, settings=None):
        # Runtime overrides may select another implementation for the same slot.
        allowed = requirements(self._services.plugins, self._services.registry, self.owner)
        allowed.update(self._services.registry.requirements.get(self.implementation, ()))
        if self.owner is not None and name != self.owner and name not in allowed:
            raise AutoSTACGISError(
                f"Plugin {self.owner} uses undeclared dependency {name}; add it to requires"
            )
        return self._services.get(name, settings=settings)


def validate_settings(schema, settings, *, partial=False):
    validator = (
        extend(Draft202012Validator, {"required": lambda *args: iter(())})
        if partial
        else Draft202012Validator
    )
    validator(schema).validate(settings)


def settings_for(implementation, settings, *, partial=False):
    schema = getattr(implementation, "settings_schema", None)
    if schema is not None:
        validate_settings(schema, settings, partial=partial)
    return settings


class Services:
    def __init__(self, registry, definitions, *, directory, events=None, plugins=None):
        self.registry = registry
        self.definitions = definitions
        self.directory = directory
        self.events = events
        self.plugins = plugins if plugins is not None else registry.service_plugins()
        configured_plugins = deepcopy(self.plugins)
        for name, definition in definitions.items():
            if name not in configured_plugins or "services" not in configured_plugins[name]["capabilities"]:
                raise AutoSTACGISError(f"Plugin {name} does not provide services")
            self.registry.resolve("services", definition["implementation"])
            configured_plugins[name]["capabilities"]["services"].update(definition)
        validate_dependencies(configured_plugins, registry)
        self.instances = {}
        self.identities = {}
        self.resources = {}
        self.stack = ExitStack()
        self.constructing = set()
        self.lock = RLock()

    def get(self, name, *, caller=None, settings=None):
        with self.lock:
            return self._get(name, caller=caller, settings=settings)

    def _get(self, name, *, caller=None, settings=None):
        require(self.plugins, self.registry, caller, name)
        if name in self.instances:
            if settings is not None and any(
                self.identities[name][1].get(k) != v for k, v in settings.items()
            ):
                raise AutoSTACGISError(f"Conflicting settings for shared service {name}")
            return self.instances[name]
        if name in self.constructing:
            raise AutoSTACGISError(f"Recursive runtime service dependency: {name}")
        try:
            base = self.plugins[name]["capabilities"]["services"]
        except KeyError:
            raise AutoSTACGISError(f"Plugin {name} does not provide services") from None
        overrides = self.definitions.get(name, {})
        definition = {**base, **overrides}
        configured = {
            **self.plugins[name].get("settings", {}),
            **base.get("settings", {}),
            **overrides.get("settings", {}),
            **(settings or {}),
        }
        if definition["implementation"] != base["implementation"]:
            configured = {
                **self.plugins[name].get("settings", {}),
                **overrides.get("settings", {}),
                **(settings or {}),
            }
        schema = self.plugins[name].get("settings_schema")
        if schema:
            Draft202012Validator(schema).validate(configured)
        factory = self.registry.resolve("services", definition["implementation"])
        settings = settings_for(factory, configured)
        identity = (definition["implementation"], deepcopy(settings))
        for other, previous in self.identities.items():
            if identity == previous:
                self.instances[name] = self.instances[other]
                self.identities[name] = identity
                return self.instances[name]
        group = getattr(factory, "resource_group", None)
        if group and group in self.resources:
            raise AutoSTACGISError(
                f"Execution backend {self.resources[group]} is already active; configure the global executor to use {name}"
            )
        self.constructing.add(name)
        try:
            instance = factory(settings, ServiceContext(self, name, definition["implementation"]))
            owned = any(instance is existing for existing in self.instances.values())
            if not owned and hasattr(instance, "__enter__"):
                instance = self.stack.enter_context(instance)
            elif not owned and hasattr(instance, "close"):
                self.stack.callback(instance.close)
            self.instances[name] = instance
            self.identities[name] = identity
            if group:
                self.resources[group] = name
            return instance
        finally:
            self.constructing.remove(name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)
