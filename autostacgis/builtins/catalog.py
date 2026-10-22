"""Built-in processing, discovery, goal and runtime capabilities."""

from ..plugins import definition, modules

DEFAULTS = {"data_transfer": {"plugin": "buffered-transfer"}}


def catalog():
    providers = modules()
    return {
        "version": "1.0.0",
        "defaults": DEFAULTS,
        "plugins": [definition(m) for m in providers],
        "runtime": {
            m.ID: definition(m)["capabilities"]["services"]
            for m in providers
            if getattr(m, "RUNTIME_DEFAULT", False)
        },
        "solver": {"max_steps": 64, "max_states": 100000, "timeout": 120, "max_bindings": 100000},
    }
