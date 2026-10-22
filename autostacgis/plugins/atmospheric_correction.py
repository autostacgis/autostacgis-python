import shutil
from dataclasses import replace

import numpy as np

from ..data import Block, TransformRaster, physical_raster
from ..data.metadata import finish
from ..errors import AutoSTACGISError


def coefficients(parameters):
    from Py6S import AeroProfile, AtmosCorr, AtmosProfile, Geometry, SixS, Wavelength

    executable = parameters.get("sixs_executable") or shutil.which("sixs") or shutil.which("sixsV1.1")
    if not executable:
        raise AutoSTACGISError("Py6S requires the native 6S executable; set sixs_executable or install sixs")
    model = SixS(path=executable)
    model.geometry = Geometry.User()
    for source, target in [
        ("solar_zenith", "solar_z"),
        ("solar_azimuth", "solar_a"),
        ("view_zenith", "view_z"),
        ("view_azimuth", "view_a"),
        ("month", "month"),
        ("day", "day"),
    ]:
        setattr(model.geometry, target, parameters[source])
    model.altitudes.set_sensor_satellite_level()
    model.altitudes.set_target_custom_altitude(parameters["elevation_km"])
    model.atmos_profile = AtmosProfile.UserWaterAndOzone(parameters["water_vapor"], parameters["ozone"])
    aerosol = parameters.get("aerosol_profile", "Continental")
    if aerosol not in ("Continental", "Maritime", "Urban", "Desert", "BiomassBurning", "Stratospheric"):
        raise AutoSTACGISError(f"Unsupported Py6S aerosol profile {aerosol}")
    model.aero_profile = AeroProfile.PredefinedType(getattr(AeroProfile, aerosol))
    model.aot550 = parameters["aot550"]
    model.atmos_corr = AtmosCorr.AtmosCorrLambertianFromRadiance(1)
    result = []
    for wavelength in parameters["wavelengths_um"]:
        model.wavelength = Wavelength(wavelength)
        model.run()
        result.append([model.outputs.coef_xa, model.outputs.coef_xb, model.outputs.coef_xc])
    return result


def _apply(blocks, window, settings):
    block = blocks[0]
    values = block.pixels.astype("float32", copy=False)
    factors = np.asarray(settings["factors"], dtype="float32")[:, None, None]
    offsets = np.asarray(settings["offsets"], dtype="float32")[:, None, None]
    coeff = np.asarray(settings["coefficients"], dtype="float32")
    y = coeff[:, 0, None, None] * (values * factors + offsets) - coeff[:, 1, None, None]
    denominator = 1 + coeff[:, 2, None, None] * y
    valid = np.isfinite(values) & (denominator != 0)
    if block.valid is not None:
        valid &= block.valid
    corrected = np.divide(y, denominator, out=np.zeros_like(y), where=valid)
    if settings.get("clip", False):
        corrected = np.clip(corrected, 0, 1)
    return Block(corrected, window, valid)


def atmospheric_correction(inputs, settings, context):
    image = inputs["image"][0]
    from datetime import datetime

    asset = image.item["assets"][image.runtime.get("selected_asset", "data")]
    metadata = {**image.item.get("properties", {}), **asset}
    parameters = {}
    for source, target in [
        ("view:sun_azimuth", "solar_azimuth"),
        ("view:off_nadir", "view_zenith"),
        ("view:azimuth", "view_azimuth"),
    ]:
        if metadata.get(source) is not None:
            parameters[target] = metadata[source]
    if metadata.get("view:sun_elevation") is not None:
        parameters["solar_zenith"] = 90 - metadata["view:sun_elevation"]
    if "view_zenith" not in parameters and metadata.get("view:incidence_angle") is not None:
        parameters["view_zenith"] = metadata["view:incidence_angle"]
    if metadata.get("datetime"):
        acquired = datetime.fromisoformat(metadata["datetime"].replace("Z", "+00:00"))
        parameters.update(month=acquired.month, day=acquired.day)
    bands = metadata.get("bands", metadata.get("eo:bands", []))
    wavelengths = [band.get("eo:center_wavelength", band.get("center_wavelength")) for band in bands]
    if wavelengths and all(value is not None for value in wavelengths):
        parameters["wavelengths_um"] = wavelengths
    parameters.update(settings)
    if "elevation" not in inputs:
        raise AutoSTACGISError(
            "Py6S requires an elevation input; load sample_dem or supply an elevation-summary table"
        )
    elevation = inputs["elevation"][0].item.get("properties", {}).get("autostacgis:mean_elevation_m")
    if type(elevation) not in (int, float) or not np.isfinite(elevation):
        raise AutoSTACGISError("Py6S requires a finite measured mean elevation in meters")
    parameters["elevation_km"] = elevation / 1000
    if "atmosphere" in inputs:
        atmosphere = inputs["atmosphere"][0]
        parameters = {**atmosphere.item.get("properties", {}).get("autostacgis:atmosphere", {}), **parameters}
        if atmosphere.payload is not None:
            for batch in atmosphere.payload.batches():
                for row in batch.to_pylist():
                    parameters = {**row, **parameters}
    required = [
        "solar_zenith",
        "solar_azimuth",
        "view_zenith",
        "view_azimuth",
        "month",
        "day",
        "water_vapor",
        "ozone",
        "aot550",
        "wavelengths_um",
    ]
    properties = image.item.get("properties", {})
    for name in required:
        if name not in parameters and name in properties:
            parameters[name] = properties[name]
    missing = [name for name in required if parameters.get(name) is None]
    if missing:
        raise AutoSTACGISError(f"Py6S needs explicit calibrated geometry and atmosphere: {missing}")
    count = image.payload.grid.count
    if len(parameters["wavelengths_um"]) != count:
        raise AutoSTACGISError("Py6S wavelength count must equal the input band count")
    coefficients_ = coefficients(parameters)
    factors, offsets = (
        parameters.get("radiance_factors", [1] * count),
        parameters.get("radiance_offsets", [0] * count),
    )
    if len(factors) != count or len(offsets) != count:
        raise AutoSTACGISError("Radiance calibration must have one factor and offset per band")
    result = TransformRaster(
        [physical_raster(image)],
        replace(image.payload.grid, dtype="float32"),
        _apply,
        {
            "coefficients": coefficients_,
            "factors": factors,
            "offsets": offsets,
            "clip": parameters.get("clip", False),
        },
    )
    output = finish(
        context,
        result,
        image,
        {
            "assets.data.cf:standard_name": "surface_bidirectional_reflectance",
            "assets.data.unit": "1",
            "assets.data.data_state:atmospheric_correction": "full",
            "assets.data.data_state:radiometry": "calibrated",
            "assets.data.processing:lineage": "Py6S atmospheric correction",
        },
        physical=True,
    )
    for band in output.item["assets"]["data"]["bands"]:
        band.update(
            {
                "unit": "1",
                "cf:standard_name": "surface_bidirectional_reflectance",
                "raster:scale": 1,
                "raster:offset": 0,
            }
        )
    return {"result": [output]}


atmospheric_correction.settings_schema = {
    "type": "object",
    "properties": {
        **{
            name: {"type": "number"}
            for name in [
                "solar_zenith",
                "solar_azimuth",
                "view_zenith",
                "view_azimuth",
                "water_vapor",
                "ozone",
                "aot550",
            ]
        },
        "month": {"type": "integer", "minimum": 1, "maximum": 12},
        "day": {"type": "integer", "minimum": 1, "maximum": 31},
        **{
            name: {"type": "array", "minItems": 1, "items": {"type": "number"}}
            for name in ["wavelengths_um", "radiance_factors", "radiance_offsets"]
        },
        "sixs_executable": {"type": "string"},
        "aerosol_profile": {"type": "string"},
        "clip": {"type": "boolean"},
    },
    "additionalProperties": False,
}


ID = "atmospheric_correction"
REQUIRES = ("atmosphere-for-image",)
IMPLEMENTATIONS = {"process": atmospheric_correction}


atmospheric_correction.contract = {
    "outputs": {
        "result": {
            "inherit_metadata": ["image"],
            "set": [
                {"assets.data.data_state:statistics_calculated": None},
                {"assets.data.cf:standard_name": "surface_bidirectional_reflectance"},
                {"assets.data.unit": "1"},
                {"assets.data.data_state:atmospheric_correction": "full"},
                {"assets.data.data_state:radiometry": "calibrated"},
            ],
            "remove": ["properties.product:type", "assets.data.product:type"],
            "kind": "raster",
        }
    },
    "anchor": "image",
    "settings": {},
    "cost": {
        "parameters": {"resampled": "image.assets.$asset.data_state:resampled"},
        "jsonata": '1 + ($type(resampled) = "array" ? $count(resampled) : 0)',
    },
    "argument_requirements": {
        "image": {
            "kind": "raster",
            "cardinality": "one",
            "require": {
                "all": [
                    {
                        "path": "assets.$asset.cf:standard_name",
                        "equals": "toa_outgoing_radiance_per_unit_wavelength",
                    },
                    {"path": "assets.$asset.unit", "equals": "W m-2 sr-1 um-1"},
                ]
            },
        },
        "atmosphere": {
            "kind": "table",
            "cardinality": "one",
            "require": {"all": [{"path": "assets.$asset.roles", "contains": "atmosphere"}]},
        },
    },
    "input_requirements": [
        {
            "plugin": "atmosphere-for-image",
            "observes": ["properties.datetime", "geometry"],
            "inputs": {"image": "image", "atmosphere": "atmosphere"},
        }
    ],
    "version": "1.0.0",
}


atmospheric_correction.contract["argument_requirements"]["elevation"] = {
    "kind": "table",
    "require": {
        "all": [
            {"path": "assets.$asset.roles", "contains": "elevation-summary"},
            {"assets.$asset.unit": "m"},
        ]
    },
}
atmospheric_correction.contract["input_requirements"].append(
    {"relation": "same_sources", "ports": ["image", "elevation"]}
)


# Exchange representations are independent of scientific metadata.
atmospheric_correction.contract['outputs']['result']["data_types"] = ['raster']
atmospheric_correction.contract['argument_requirements']['image']["data_types"] = ['raster']
atmospheric_correction.contract['argument_requirements']['atmosphere']["data_types"] = ['batches']

atmospheric_correction.contract["argument_requirements"]["elevation"]["data_types"] = ["batches"]
