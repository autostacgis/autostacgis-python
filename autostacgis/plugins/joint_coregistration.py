"""SpectralMatch joint solve, with the fitted models retained in STAC history.

Uses the solver API of the pinned SpectralMatch revision: the public file-only
entry point discards these models. No solver code is copied into AutoSTACGIS.
"""

import inspect
from importlib import import_module
from pathlib import Path

import numpy as np

from ..data import FileRaster, nodata_raster, physical_raster
from ..data.metadata import finish
from ..data.writers import vrt_paths
from ..errors import AutoSTACGISError
from .common_crs import require_common_crs
from .register_to_reference import append_registration, identity


def _apply(args):
    backend = import_module("spectralmatch.joint_coregistration.joint_coregistration")
    return backend._apply_alignment_process_image(*args)


def joint_coregistration(inputs, settings, context):
    import networkx as nx

    backend = import_module("spectralmatch.joint_coregistration.joint_coregistration")
    import cv2

    cv2.setNumThreads(1)
    backend._set_gdal_workers(1, False)
    records = inputs["images"]
    if len(records) < 2:
        raise AutoSTACGISError("Joint registration needs at least two images")
    require_common_crs(inputs, names=["images"])
    options = {
        k: p.default
        for k, p in inspect.signature(backend.joint_coregistration).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    unknown = set(settings) - set(options)
    if unknown:
        raise AutoSTACGISError(f"Unsupported SpectralMatch joint registration settings: {sorted(unknown)}")
    options.update(settings)
    o = options
    if o["global_model"] == o["local_model"] == "none":
        raise AutoSTACGISError("Joint registration must fit a global or local model")
    rasters = [
        nodata_raster(physical_raster(record), nodata=float(np.finfo("float32").min)) for record in records
    ]
    paths = vrt_paths(rasters, context.storage, context.directory)
    names = [Path(path).stem for path in paths]
    outputs = [str(context.directory / f"output-{i}.tif") for i in range(len(paths))]
    infos = backend._use_common_coordinate_frame(
        {name: backend._read_image_info(name, path) for name, path in zip(names, paths, strict=True)}
    )
    overlaps = backend._find_overlaps({name: info.bounds for name, info in infos.items()})
    loaded = backend._load_ties(o["tie_load_path"]) if o["tie_load_path"] else {}
    ties, thresholds = backend._collect_ties(
        overlaps,
        infos,
        loaded,
        o["tie_feature_method"],
        o["tie_maximum_displacement"],
        o["tie_ransac_reprojection_threshold"],
        None,
        o["debug_logs"],
        grid_spacing=o["tie_grid_spacing"],
        search_radius=o["tie_search_radius"],
        tie_orb_max_features=o["tie_orb_max_features"],
        tie_max_matches_per_window=o["tie_max_matches_per_window"],
    )
    graph = nx.Graph()
    graph.add_nodes_from(names)
    graph.add_edges_from(pair for pair, points in ties.items() if len(points))
    if not nx.is_connected(graph):
        raise AutoSTACGISError(
            "Joint registration tie-point graph is disconnected; split groups or supply usable tie points"
        )
    scale = o["tie_robust_loss_scale"] or (float(np.median(thresholds)) if thresholds else 1.0)
    global_weights = backend._normalize_image_weights(
        backend._resolve_image_values(o["global_image_movement_penalty_weights"], names)
    )
    local_weights = backend._normalize_image_weights(
        backend._resolve_image_values(o["local_image_movement_penalty_weights"], names)
    )
    import json

    def strength(value):
        return backend._resolve_image_values(json.loads(value) if isinstance(value, str) else value, names)

    parameters = backend._solve_global_alignment(
        infos,
        ties,
        o["global_model"],
        global_weights,
        strength(o["global_tie_alignment_strength"]),
        o["tie_robust_loss"],
        scale,
        o["debug_logs"],
    )
    meshes = backend._solve_local_alignment(
        infos,
        ties,
        o["global_model"],
        parameters,
        o["local_model"],
        local_weights,
        strength(o["local_tie_alignment_strength"]),
        o["local_grid_spacing"],
        o["local_smoothness_weight"],
        o["local_bending_weight"],
        o["local_anchor_falloff_distance"],
        o["tie_robust_loss"],
        scale,
        o["debug_logs"],
    )
    resolution = backend.compute_resolution(paths, o["resolution"])
    args = [
        (
            infos[name],
            path,
            o["global_model"],
            parameters[name],
            o["local_model"],
            meshes.get(name),
            o["resampling_method"],
            resolution,
            o["tap"],
            backend._resolve_gdal_dtype(o["output_dtype"], infos[name].path, False),
            o["custom_nodata_value"],
            o["window_size"] or context.storage.tile_size,
            o["save_as_cog"],
            False,
            1,
            o["debug_logs"],
            "no",
        )
        for name, path in zip(names, outputs, strict=True)
    ]
    list(context.executor.map(_apply, args))
    if o["tie_save_path"]:
        backend._save_ties(o["tie_save_path"], ties)
    if o["tie_save_crs_path"]:
        backend._save_ties_crs(o["tie_save_crs_path"], ties, infos)
    if o["build_overviews"] and o["window_scales"]:
        backend.compute_overviews(
            input_images_paths=outputs,
            window_scales=o["window_scales"],
            cache=None,
            image_threads=None,
            io_threads=1,
            tile_threads=None,
            debug_logs=False,
        )
    results = []
    identities = dict(zip(names, map(identity, records), strict=True))
    for index, (name, path, record) in enumerate(zip(names, outputs, records, strict=True)):
        if not Path(path).is_file():
            raise AutoSTACGISError(f"joint_coregistration did not create {path}")
        model = {
            "global_model": o["global_model"],
            "global_parameters": parameters[name].tolist(),
            "coordinate_center": list(infos[name].center),
            "coordinate_scale": infos[name].coordinate_scale,
            "source_geotransform": list(infos[name].transform),
            "source_shape": [infos[name].height, infos[name].width],
            "crs": record.payload.grid.crs,
            "local_model": o["local_model"],
        }
        if name in meshes:
            mesh = meshes[name]
            model["mesh"] = {
                "x": mesh.xs.tolist(),
                "y": mesh.ys.tolist(),
                "displacement": mesh.displacement.tolist(),
            }
        tie_points = []
        for (a, b), points in ties.items():
            if name not in (a, b):
                continue
            for values in points:
                # SpectralMatch's tie arrays contain source/target pixel-center coordinates.
                source, target = (values[:2], values[2:]) if name == a else (values[2:], values[:2])
                tie_points.append(
                    {
                        "source": source.tolist(),
                        "target": target.tolist(),
                        "target_item": identities[b if name == a else a],
                        "coordinate_space": "pixel_centers",
                    }
                )
        entry = {
            "kind": "joint",
            "target": [identities[n] for n in names if n != name],
            "model_type": "+".join(v for v in [o["global_model"], o["local_model"]] if v != "none"),
            "model": model,
            "tie_points": tie_points,
        }
        result = finish(context, FileRaster(path), record, port="results", index=index, physical=True)
        append_registration(result, record, entry, o["resampling_method"])
        results.append(result)
    return {"results": results}


def bind_inputs(records, settings, context):
    return context.call("gather-all", "sequence", records, settings)


ID = "joint_coregistration"
REQUIRES = ("storage", "executor", "gather-all", "common-crs", "register_to_reference")
IMPLEMENTATIONS = {"process": joint_coregistration, "sequence": bind_inputs}


joint_coregistration.contract = {
    "outputs": {
        "results": {
            "inherit_metadata": ["images"],
            "set": [{"assets.data.data_state:statistics_calculated": None}],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "append": [
                {
                    "assets.data.data_state:registration": {
                        "kind": "joint",
                        "target": None,
                        "model_type": None,
                        "model": None,
                        "tie_points": None,
                    }
                },
                {"assets.data.data_state:resampled": {"jsonata": "settings.resampling_method"}},
            ],
            "each_from": "images",
            "kind": "raster",
        }
    },
    "anchor": "images",
    "settings": {"resampling_method": "bilinear"},
    "cost": {
        "parameters": {"registration": "images.assets.$asset.data_state:registration"},
        "jsonata": '($n := $type(registration) != "array" or $count(registration) = 0 ? 0 : '
        '($type(registration[0]) = "object" ? $count(registration) : '
        '$min($map(registration, function($h){$type($h) = "array" ? $count($h) : '
        "0}))); 1 + ($n > 0 ? 0 : 2))",
    },
    "argument_requirements": {
        "images": {
            "kind": "raster",
            "cardinality": "many",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {"not": {"path": "assets.$asset.data_state:registration", "entry": {"kind": "joint"}}},
                ]
            },
            "sequence": {"plugin": "gather-all"},
        }
    },
    "input_requirements": [
        {
            "plugin": "common-crs",
            "observes": ["assets.$asset.proj:code", "assets.$asset.proj:wkt2", "assets.$asset.proj:projjson"],
            "inputs": {"images": "images"},
        }
    ],
    "version": "1.0.0",
}

joint_coregistration.contract["argument_requirements"]["images"]["sequence"]["settings"] = {
    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"}
}


# Preserve existing registration preferences and additionally favor masked imagery.
_previous_cost = joint_coregistration.contract.get("cost", 1)
if not isinstance(_previous_cost, dict):
    _previous_cost = {"parameters": {}, "jsonata": str(_previous_cost)}
joint_coregistration.contract["cost"] = {
    "parameters": {
        **_previous_cost.get("parameters", {}),
        "masked": "images.assets.$asset.data_state:masked",
    },
    "jsonata": "(" + _previous_cost["jsonata"] + ") + "
    '($m := $type(masked) = "array" ? masked : [masked]; '
    '$n := $min($map($m, function($v){ $type($v) = "object" ? '
    "$count($filter($each($v, function($x){$x}), function($x){$x = true})) : 0 })); "
    "4 / (1 + $n))",
}


# Seamline clipping partitions overlapping observations and removes the common
# pixels required by registration and relative radiometric estimation.
joint_coregistration.contract["argument_requirements"]["images"]["require"]["all"].append(
    {
        "any": [
            {"path": "assets.$asset.data_state:masked.mosaic_seamline", "equals": False},
            {"path": "assets.$asset.data_state:masked.mosaic_seamline", "exists": False},
        ]
    }
)


# Exchange representations are independent of scientific metadata.
joint_coregistration.contract['outputs']['results']["data_types"] = ['raster']
joint_coregistration.contract['argument_requirements']['images']["data_types"] = ['raster']
