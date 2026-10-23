"""Declared plugin dependencies and scoped access to their capabilities."""

from .errors import AutoSTACGISError


def requirements(plugins, registry, owner):
    plugin = plugins.get(owner, {})
    required = set(plugin.get("requires", ()))
    for capability, entry in plugin.get("capabilities", {}).items():
        entries = [entry]
        for definition in entries:
            required.update(registry.requirements.get(definition.get("implementation"), ()))
    required.update(registry.requirements.get(owner, ()))
    return required


def require(plugins, registry, owner, dependency):
    if owner is not None and dependency != owner and dependency not in requirements(plugins, registry, owner):
        raise AutoSTACGISError(f"Plugin {owner} uses undeclared dependency {dependency}; add it to requires")
    if dependency not in plugins:
        raise AutoSTACGISError(f"Required plugin is unavailable: {dependency}")


def dependency_order(plugins, registry):
    """Return dependencies before dependents; unrelated plugins have no ordering constraint."""
    checked = set()
    ordered = []

    def visit(name, active=()):
        if name in active:
            raise AutoSTACGISError(f"Recursive plugin dependency: {' -> '.join((*active, name))}")
        if name not in plugins:
            raise AutoSTACGISError(f"Required plugin is unavailable: {name}")
        if name not in checked:
            for dependency in sorted(requirements(plugins, registry, name)):
                visit(dependency, (*active, name))
            checked.add(name)
            ordered.append(name)

    for name in sorted(plugins):
        visit(name)
    return ordered


def validate_dependencies(plugins, registry):
    dependency_order(plugins, registry)


def configured_plugins(value):
    """Explicit plugin references authorize calls within that configured invocation."""
    names = set()
    if isinstance(value, dict):
        if isinstance(value.get("plugin"), str):
            names.add(value["plugin"])
        for child in value.values():
            names.update(configured_plugins(child))
    elif isinstance(value, list):
        for child in value:
            names.update(configured_plugins(child))
    return names


class PluginContext:
    """Application access for discovery, metadata, checks, sequence routing, and publication."""

    def __init__(self, app, owner, options=None):
        self._app = app
        self.owner = owner
        self.configured = configured_plugins(options or {})

    def __getattr__(self, name):
        if name in ("registry", "active_services", "sources", "acquirers", "invoke", "resolve_call"):
            raise AutoSTACGISError("Use context.call() or context.service() for declared plugin dependencies")
        return getattr(self._app, name)

    @property
    def scratch(self):
        """Core-owned artifact lifetime, shared by format and destination plugins."""
        if self._app.active_services is not None:
            root = self._app.active_services.directory
        elif self._app.active_transfers is not None:
            root = self._app.active_transfers.directory
        else:
            raise AutoSTACGISError("Managed scratch requires an active run or publication session")
        directory = root / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def call(self, name, capability, value, settings=None):
        if name not in self.configured:
            require(self._app.plugins, self._app.registry, self.owner, name)
        return self._app.invoke(name, capability, value, settings)

    def service(self, name):
        if self._app.active_services is None:
            raise AutoSTACGISError("Runtime services are only available during execution")
        return self._app.active_services.get(name, caller=self.owner)

    def acquirer(self, name):
        """Access a declared acquisition dependency, including its discovery session."""
        require(self._app.plugins, self._app.registry, self.owner, name)
        try:
            return self._app.acquirers[name]
        except KeyError:
            raise AutoSTACGISError(f"Unavailable data_in plugin: {name}") from None

    def fresh(self):
        return type(self._app)(self._app.snapshot(), base_dir=self._app.base_dir, registry=self._app.registry)
