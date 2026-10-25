"""Capability-grouped workflow documents, expanded once at the library boundary."""

from copy import deepcopy

from .config import json_value, merge, validate
from .errors import AutoSTACGISError
from .registry import CAPABILITIES


def is_workflow(document):
    return (
        isinstance(document, dict)
        and not set(document) & {"catalog", "request", "plugins"}
        and bool(set(document) & set(CAPABILITIES))
    )


def default_workflow():
    return {
        "solver": {"backward_chain": {}},
        "goal": {
            "metadata-value": {
                "name": "mosaic",
                "kind": "raster",
                "path": "assets.$asset.data_state:mosaic",
                "value": True,
                "select": {
                    "kind": "raster",
                    "require": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                },
            }
        },
        "process": {
            "align_grid": {"crs": "EPSG:6933"},
            "extract_footprints": {},
            "clean_footprints": {
                "hole_edge_distance": 0,
                "hole_to_hole_distance": 0,
                "simplify_smoothing_radius": 0,
                "simplify_tolerance": 0,
                "filter_area_rank": None,
            },
            "voronoi_seamlines": {},
            "mask_by_polygons": {},
            "mosaic": {},
        },
        "execution": {
            "steps": {
                "project": {"plugin": "align_grid"},
                "footprints": {"plugin": "extract_footprints", "inputs": {"images": "result:project:result"}},
                "clean": {"plugin": "clean_footprints", "inputs": {"vector": "result:footprints:result"}},
                "seamlines": {"plugin": "voronoi_seamlines", "inputs": {"footprints": "result:clean:result"}},
                "mask": {
                    "plugin": "mask_by_polygons",
                    "inputs": {"image": "result:project:result", "mask": "result:seamlines:result"},
                },
                "mosaic": {"plugin": "mosaic", "inputs": {"images": "result:mask:result"}},
            }
        },
    }


def referenced_plugins(document):
    """Names referenced by a plugin's DEFAULT_CONFIG, without importing code."""
    json_value(document)
    if not isinstance(document, dict) or set(document) - set(CAPABILITIES):
        raise AutoSTACGISError("DEFAULT_CONFIG must contain only capability sections")
    names = []
    for capability, entries in document.items():
        if not isinstance(entries, dict):
            raise AutoSTACGISError(f"{capability}: expected plugin names mapped to parameter objects")
        for name, parameters in entries.items():
            if not isinstance(name, str) or not isinstance(parameters, dict):
                raise AutoSTACGISError(f"{capability}: invalid plugin defaults")
            names.append(name)
    return tuple(names)


def normalize(document, registry):
    """Convert public grouped JSON to the executor's internal catalog/request model.

    Explicit settings override every plugin fragment; fragments fill missing values.
    Goals may contain a list of parameter objects to compose several instances.
    Execution contains orchestration only, never a second set of plugin settings.
    """
    validate(document, "workflow")
    unknown = set(document) - {*CAPABILITIES, "metadata_structure", "execution"}
    if unknown:
        raise AutoSTACGISError("Unknown workflow sections: " + ", ".join(sorted(unknown)))
    explicit = deepcopy(document)
    if not isinstance(explicit.get("solver"), dict) or len(explicit["solver"]) != 1:
        raise AutoSTACGISError("Exactly one solver must be configured")
    if not isinstance(explicit.get("goal"), dict) or not explicit["goal"]:
        raise AutoSTACGISError("At least one goal plugin must be configured")
    resolved = deepcopy(explicit)
    processed = set()
    while True:
        pending = [
            (cap, name) for cap in CAPABILITIES for name in resolved.get(cap, {}) if name not in processed
        ]
        if not pending:
            from .results import result_options, saving_plugins

            for name, parameters in resolved.get("process", {}).items():
                implementation = registry.plugins[name]["process"]
                options = result_options(implementation.contract, parameters.get("outputs"))
                saves = saving_plugins(options)
                for step in resolved.get("execution", {}).get("steps", {}).values():
                    if step.get("plugin") == name:
                        overrides = {
                            port: policy
                            for port, policy in step.get("outputs", {}).items()
                            if isinstance(policy, dict)
                        }
                        saves.update(saving_plugins(result_options({"outputs": options}, overrides)))
                for dependency in saves:
                    dependency = registry.load(dependency)
                    group = next(iter(registry.plugins[dependency]))
                    resolved.setdefault(group, {}).setdefault(dependency, {})
            pending = [
                (cap, name) for cap in CAPABILITIES for name in resolved.get(cap, {}) if name not in processed
            ]
            if not pending:
                break
        for capability, name in pending:
            registry.load(name)
            if capability not in registry.plugins[name]:
                raise AutoSTACGISError(
                    f"{name}: expected {capability}, installed capability is {next(iter(registry.plugins[name]))}"
                )
            processed.add(name)
            fragment = registry.config_defaults.get(name, {})
            resolved = merge(fragment, resolved)
            for dependency in registry.requirements[name]:
                group = next(iter(registry.plugins[dependency]))
                resolved.setdefault(group, {}).setdefault(dependency, {})
    for capability in ("solver", "trigger"):
        if len(resolved.get(capability, {})) > 1:
            raise AutoSTACGISError(f"Only one {capability} may be active")
    entries = []
    goals = []
    for capability in CAPABILITIES:
        group = resolved.get(capability, {})
        if not isinstance(group, dict):
            raise AutoSTACGISError(f"{capability}: expected an object of named plugins")
        for name, settings in group.items():
            if capability not in registry.plugins[name]:
                raise AutoSTACGISError(f"{name}: incorrect capability section {capability}")
            if capability == "goal":
                calls = settings if isinstance(settings, list) else [settings]
                if not calls or any(not isinstance(call, dict) for call in calls):
                    raise AutoSTACGISError(f"{name}: expected goal parameters or a nonempty list")
                from .solvers import validate_parameters

                for call in calls:
                    validate_parameters(
                        registry.plugins[name][capability].contract["settings_schema"], call, name
                    )
                goals.extend({"plugin": name, "settings": call} for call in calls)
                entries.append({"use": name})
            else:
                if not isinstance(settings, dict):
                    raise AutoSTACGISError(f"{name}: expected a parameter object")
                parameters = deepcopy(settings)
                outputs = parameters.pop("outputs", None) if capability == "process" else None
                entries.append(
                    {
                        "use": name,
                        "settings": parameters,
                        **({"execution": {"outputs": outputs}} if outputs is not None else {}),
                    }
                )
    execution = deepcopy(resolved.get("execution", {}))
    if not isinstance(execution, dict) or set(execution) & {"goal", "module_settings", "solver", "runtime"}:
        raise AutoSTACGISError(
            "execution accepts orchestration fields only; configure plugins in their capability sections"
        )
    request = {**execution, "goal": goals[0] if len(goals) == 1 else {"all_of": goals}}
    request.setdefault("sources", list(resolved.get("metadata_in", {})))
    result = {"catalog": {"version": "0.0.1", "plugins": entries}, "request": request}
    if "metadata_structure" in resolved:
        result["metadata_structure"] = resolved["metadata_structure"]
    return validate(result, "config")
