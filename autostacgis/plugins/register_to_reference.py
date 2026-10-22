"""Coregix registration with shared-budget scheduling and fitted-model provenance."""

from ..data import physical_raster
from ..data.metadata import finish, resampling_history
from ..errors import AutoSTACGISError


def identity(record):
    asset = record.item["assets"][record.runtime.get("selected_asset", "data")]
    result = {
        "id": record.runtime.get(
            "source_id",
            record.runtime.get(
                "original_id", record.item.get("properties", {}).get("autostacgis:source_id", record.item["id"])
            ),
        )
    }
    if asset.get("href") and not asset["href"].startswith("autostacgis://"):
        result["href"] = asset["href"]
    value = record.item.get("properties", {}).get("uuid")
    if value:
        result["uuid"] = value
    return result


def append_registration(output, source, entry, resampling):
    """Fill the predicted history entry with the model actually fitted by this plugin."""
    original = source.item["assets"][source.runtime.get("selected_asset", "data")]
    asset = output.item["assets"]["data"]
    asset["data_state:registration"] = [*(original.get("data_state:registration") or []), entry]
    for index, band in enumerate(asset.get("bands", [])):
        previous_bands = original.get("bands", [])
        if index < len(previous_bands) and "data_state:registration" in previous_bands[index]:
            band["data_state:registration"] = [
                *(previous_bands[index]["data_state:registration"] or []),
                entry,
            ]
    resampling_history(output, source, resampling)


def registration_metadata(kind):
    return {
        "append": [
            {
                "assets.data.data_state:registration": {
                    "kind": kind,
                    "target": None,
                    "model_type": None,
                    "model": None,
                    "tie_points": None,
                }
            },
            {"assets.data.data_state:resampled": {"jsonata": "settings.resampling_method"}},
        ]
    }


def register_to_reference(inputs, settings, context):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from ..data import FileRaster

    moving, reference = inputs["image"][0], inputs["reference"][0]
    for record in (moving, reference):
        if not record.payload.grid.crs or record.payload.grid.georeferencing in ("rpc", "none"):
            raise AutoSTACGISError("Coregix registration requires affine georeferenced inputs")
    context.directory.mkdir(parents=True, exist_ok=True)
    (context.directory / "coregix").mkdir(exist_ok=True)
    target = context.directory / "registered.tif"
    job = context.directory / "coregix.json"
    report = context.directory / "coregix-model.json"
    options = dict(settings)
    options.pop("resampling_method", None)
    options.update(
        moving_image_path=context.storage.path(
            physical_raster(moving), reason="Coregix requires random access"
        ),
        fixed_image_path=context.storage.path(
            physical_raster(reference), reason="Coregix requires random access"
        ),
        output_image_path=str(target),
        temp_dir=str(context.directory / "coregix"),
        keep_temp_dir=False,
    )
    job.write_text(json.dumps({"options": options, "report": str(report)}))
    environment = {**os.environ, "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS": "1", "OMP_NUM_THREADS": "1"}
    environment["PYTHONPATH"] = (
        str(Path(__file__).resolve().parents[2]) + os.pathsep + environment.get("PYTHONPATH", "")
    )
    completed = subprocess.run(
        [sys.executable, "-m", "autostacgis.plugins.register_to_reference", str(job)],
        cwd=context.directory,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise AutoSTACGISError("Coregix registration failed: " + completed.stderr[-4000:])
    models = json.loads(report.read_text())
    if not models or not Path(target).is_file():
        raise AutoSTACGISError("Coregix produced no fitted registration model")
    result = finish(context, FileRaster(target), moving, physical=True)
    entry = {
        "kind": "reference",
        "target": [identity(reference)],
        "model_type": "elastix",
        "model": {"backend": "coregix", "fits": models},
        "tie_points": None,
    }
    append_registration(result, moving, entry, "bilinear")
    return {"result": [result]}


def _worker(path):
    import importlib
    import json
    from pathlib import Path

    import rasterio
    from coregix import align_image_pair

    request = json.loads(Path(path).read_text())
    models = []

    def capture(function):
        def estimate(*args, **kwargs):
            result = function(*args, **kwargs)
            fixed = kwargs.get("fixed_image_path", args[0] if args else None)
            with rasterio.open(fixed) as raster:
                grid = {
                    "crs": str(raster.crs),
                    "transform": list(raster.transform)[:6],
                    "shape": list(raster.shape),
                }
            maps = [
                {key: list(values) for key, values in result.GetParameterMap(i).items()}
                for i in range(result.GetNumberOfParameterMaps())
            ]
            models.append({"parameter_maps": maps, "solve_grid": grid})
            return result

        return estimate

    for module in ("coregix.pipelines.alignment", "coregix.pipelines.alignment_large_main"):
        backend = importlib.import_module(module)
        backend.estimate_elastix_transform = capture(backend.estimate_elastix_transform)
    align_image_pair(**request["options"])
    Path(request["report"]).write_text(json.dumps(models))


register_to_reference.settings_schema = {
    "type": "object",
    "properties": {
        **{
            key: {"type": "boolean"}
            for key in (
                "clip_fixed_to_moving",
                "output_on_moving_grid",
                "trim_edge_invalid",
                "enforce_mutual_valid_mask",
                "use_edge_proxies",
                "log_to_console",
            )
        },
        **{
            key: {"type": "integer", "minimum": 0}
            for key in (
                "band_index",
                "moving_band_index",
                "fixed_band_index",
                "split_factor",
                "edge_trim_detection_band_index",
            )
        },
        "edge_trim_depth": {"type": "integer", "minimum": 1},
        "min_valid_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        **{
            key: {"type": ["number", "null"]}
            for key in (
                "moving_nodata",
                "fixed_nodata",
                "output_nodata",
                "edge_trim_invalid_below",
                "edge_trim_invalid_above",
                "solve_resolution",
            )
        },
        "solve_resolutions": {"type": "array", "minItems": 1, "items": {"type": ["number", "null"]}},
        "resampling_method": {"const": "bilinear"},
    },
    "additionalProperties": False,
}


ID = "register_to_reference"
REQUIRES = ("storage",)
IMPLEMENTATIONS = {"process": register_to_reference}


register_to_reference.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [{"assets.data.data_state:statistics_calculated": None}],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "append": [
                {
                    "assets.data.data_state:registration": {
                        "kind": "reference",
                        "target": None,
                        "model_type": None,
                        "model": None,
                        "tie_points": None,
                    }
                },
                {"assets.data.data_state:resampled": {"jsonata": "settings.resampling_method"}},
            ],
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {"resampling_method": "bilinear"},
    "argument_requirements": {
        "image": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {
                        "not": {
                            "path": "assets.$asset.data_state:registration",
                            "entry": {"kind": "reference"},
                        }
                    },
                ]
            },
        },
        "reference": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:georeferencing", "equals": "affine"},
                    {"path": "assets.$asset.roles", "contains": "registration-reference"},
                ]
            },
        },
    },
    "input_requirements": [{"relation": "distinct", "ports": ["image", "reference"]}],
    "cost": 1,
    "version": "1.0.0",
}


# Preserve existing registration preferences and additionally favor masked imagery.
_previous_cost = register_to_reference.contract.get("cost", 1)
if not isinstance(_previous_cost, dict):
    _previous_cost = {"parameters": {}, "jsonata": str(_previous_cost)}
register_to_reference.contract["cost"] = {
    "parameters": {**_previous_cost.get("parameters", {}), "masked": "image.assets.$asset.data_state:masked"},
    "jsonata": "(" + _previous_cost["jsonata"] + ") + "
    '($m := $type(masked) = "array" ? masked : [masked]; '
    '$n := $min($map($m, function($v){ $type($v) = "object" ? '
    "$count($filter($each($v, function($x){$x}), function($x){$x = true})) : 0 })); "
    "4 / (1 + $n))",
}


if __name__ == "__main__":
    import sys

    _worker(sys.argv[1])


# Exchange representations are independent of scientific metadata.
register_to_reference.contract['outputs']['result']["data_types"] = ['raster']
register_to_reference.contract['argument_requirements']['image']["data_types"] = ['raster']
register_to_reference.contract['argument_requirements']['reference']["data_types"] = ['raster']
