import pytest
from helpers import scene

from autostacgis import Application
from autostacgis.semantics import descriptor


def workflow_inputs():
    images = []
    for i in range(2):
        ms = scene(f"ms{i}")
        pan = scene(f"pan{i}", "panchromatic_imagery", count=1)
        for record in [ms, pan]:
            record.item["properties"]["datetime"] = f"2024-01-0{i + 1}T00:00:00Z"
            record.item["assets"]["data"].update(
                {
                    "data_state:orthorectified": False,
                    "data_state:georeferencing": "rpc",
                    "cf:standard_name": "toa_outgoing_radiance_per_unit_wavelength",
                    "unit": "W m-2 sr-1 um-1",
                }
            )
        images.extend([ms, pan])
        at = scene(f"at{i}", "atmosphere")
        at.item["properties"]["datetime"] = ms.item["properties"]["datetime"]
        at.item["assets"]["data"].update(descriptor("atmosphere"))
        at.kind = "table"
        at.payload = None
        images.append(at)
    dem = scene("dem", "digital_elevation_model", count=1)
    dem.item["assets"]["data"].update({"cf:standard_name": "surface_altitude", "unit": "m"})
    reference = scene("ref")
    reference.item["assets"]["data"]["roles"].append("registration-reference")
    reference.item["assets"]["data"].pop("data_state:spectral_sampling")
    images.extend([dem, reference])

    for record in images:
        record.item["assets"]["data"]["data_state:local_radiometric_adjustment"] = False
        record.item["assets"]["data"]["data_state:resampled"] = []

    return images


def test_multisensor_collection_contracts():
    images = workflow_inputs()
    from autostacgis_plugins._backward_chain_planner import Planner, inventory

    from autostacgis.contracts import requirements_hold
    from autostacgis.goals import compile_goal, select_outputs

    app = Application()
    req = {
        "goal": {
            "output": {
                "kind": "raster",
                "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                "require": {
                    "all": [
                        {
                            "assets.$asset.cf:standard_name": "surface_bidirectional_reflectance",
                            "assets.$asset.unit": "1",
                            "assets.$asset.data_state:orthorectified": True,
                            "assets.$asset.data_state:pansharpened": True,
                            "assets.$asset.data_state:relative_radiometric_normalization": True,
                            "assets.$asset.data_state:local_radiometric_adjustment": True,
                            "assets.$asset.data_state:masked.cloud": True,
                            "assets.$asset.data_state:masked.cloud_shadow": True,
                            "assets.$asset.data_state:masked.mosaic_seamline": True,
                            "assets.$asset.data_state:mosaic": True,
                        },
                        {"path": "assets.$asset.data_state:registration", "entry": {"kind": "reference"}},
                        {"path": "assets.$asset.data_state:registration", "entry": {"kind": "joint"}},
                        {},
                    ]
                },
            }
        },
        "module_settings": {"orthorectify_rpc": {"crs": "EPSG:32604"}},
    }
    p = Planner(app, req)
    p.initial = inventory(images)
    objects = {r.item["id"]: r for r in p.initial.values()}
    n = 0

    def perform(name, **inputs):
        nonlocal n
        c = p.modules[name]
        settings = c["settings"]
        if not requirements_hold(app, name, c, inputs, settings):
            raise RuntimeError("Unsatisfied " + name)
        result = p.predictions(name, c, inputs, settings, str(n))
        n += 1
        return next(iter(result.values()))

    processed = []
    for i in range(2):
        elevation = perform("sample_dem", image=[objects[f"ms{i}"]], dem=[objects["dem"]])
        image = perform(
            "atmospheric_correction",
            image=[objects[f"ms{i}"]],
            atmosphere=[objects[f"at{i}"]],
            elevation=elevation,
        )
        image = perform("orthorectify_rpc", image=image, dem=[objects["dem"]])
        pan = perform("orthorectify_rpc", image=[objects[f"pan{i}"]], dem=[objects["dem"]])
        image = perform("pansharpen", multispectral=image, panchromatic=pan)
        mask = perform("detect_clouds", image=image)
        image = perform("apply_mask", image=image, mask=mask)
        image = perform("register_to_reference", image=image, reference=[objects["ref"]])
        processed += image
    for name in ["joint_coregistration", "global_radiometric_match", "local_radiometric_match"]:
        processed = perform(name, images=processed)
    footprints = perform("extract_footprints", images=processed)
    footprints = perform("clean_footprints", vector=footprints)
    seamlines = perform("weighted_seamlines", images=processed, footprints=footprints)
    masked = []
    for image in processed:
        masked += perform("mask_by_polygons", image=[image], mask=seamlines)
    result = perform("mosaic", images=masked)
    goals = compile_goal(req["goal"], list(p.initial.values()), app)
    assert n == 25
    assert select_outputs(result, goals) is not None


@pytest.mark.parametrize("tables_first", [False, True])
def test_full_workflow_is_synthesized_from_selected_plugins(tables_first):
    app = Application(
        {
            "version": "0.0.1",
            "plugins": [
                {"use": name}
                for name in [
                    "sample_dem",
                    "atmospheric_correction",
                    "orthorectify_rpc",
                    "pansharpen",
                    "detect_clouds",
                    "apply_mask",
                    "register_to_reference",
                    "joint_coregistration",
                    "global_radiometric_match",
                    "local_radiometric_match",
                    "extract_footprints",
                    "clean_footprints",
                    "markov_seamlines",
                    "mask_by_polygons",
                    "mosaic",
                ]
            ],
        }
    )
    images = workflow_inputs()
    if tables_first:
        images.sort(key=lambda record: record.kind != "table")
    plan = app.plan(
        {
            "goal": {
                "output": {
                    "kind": "raster",
                    "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                    "require": {
                        "all": [
                            {
                                "assets.$asset.cf:standard_name": "surface_bidirectional_reflectance",
                                "assets.$asset.unit": "1",
                                "assets.$asset.data_state:orthorectified": True,
                                "assets.$asset.data_state:pansharpened": True,
                                "assets.$asset.data_state:relative_radiometric_normalization": True,
                                "assets.$asset.data_state:local_radiometric_adjustment": True,
                                "assets.$asset.data_state:masked.cloud": True,
                                "assets.$asset.data_state:masked.cloud_shadow": True,
                                "assets.$asset.data_state:masked.mosaic_seamline": True,
                                "assets.$asset.data_state:mosaic": True,
                            },
                            {"path": "assets.$asset.data_state:registration", "entry": {"kind": "reference"}},
                            {"path": "assets.$asset.data_state:registration", "entry": {"kind": "joint"}},
                            {},
                        ]
                    },
                }
            },
            "module_settings": {"orthorectify_rpc": {"crs": "EPSG:32604"}},
            "solver": {
                "plugin": "backward_chain",
                "settings": {"timeout": 30, "max_states": 20000, "max_bindings": 2000},
            },
        },
        images,
    )
    modules = [s.module for s in plan.steps]
    assert len(modules) == 25 and modules.count("sample_dem") == 2
    assert "markov_seamlines" in modules and "weighted_seamlines" not in modules
    assert plan.report["engine"] == "backward_chain"
    for step in plan.steps:
        if step.module in (
            "register_to_reference",
            "joint_coregistration",
            "global_radiometric_match",
            "local_radiometric_match",
        ):
            port = "image" if step.module == "register_to_reference" else "images"
            for key in step.inputs[port]:
                masked = plan.predicted[key].item["assets"]["data"]["data_state:masked"]
                assert masked["cloud"] and masked["cloud_shadow"]

    for step in plan.steps:
        if step.module == "atmospheric_correction":
            source = plan.predicted[step.inputs["image"][0]]
            assert source.item["assets"]["data"].get("data_state:resampled", []) == []
        if step.module in ("global_radiometric_match", "local_radiometric_match"):
            for key in step.inputs["images"]:
                history = plan.predicted[key].item["assets"]["data"]["data_state:registration"]
                assert {entry["kind"] for entry in history} == {"reference", "joint"}

        if step.module == "pansharpen":
            for port in ("multispectral", "panchromatic"):
                record = plan.predicted[step.inputs[port][0]]
                assert record.item["assets"]["data"].get("data_state:registration", []) == []
