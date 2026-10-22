"""Compile the harmonized-mosaic product request into output constraints."""

ID = "harmonized-mosaic"
REQUIRES = ()


def goal(settings, records, context):
    requirements = {
        "assets.$asset.cf:standard_name": "surface_bidirectional_reflectance",
        "assets.$asset.unit": "1",
        **{
            "assets.$asset.data_state:" + key: True
            for key in (
                "orthorectified",
                "pansharpened",
                "relative_radiometric_normalization",
                "local_radiometric_adjustment",
                "masked.cloud",
                "masked.cloud_shadow",
                "masked.mosaic_seamline",
                "mosaic",
            )
        },
    }
    require = {
        "all": [
            requirements,
            *[
                {"path": "assets.$asset.data_state:registration", "entry": {"kind": kind}}
                for kind in ("reference", "joint")
            ],
            settings.get("require", {}),
        ]
    }
    return {
        "output": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            **settings,
            "require": require,
        }
    }


IMPLEMENTATIONS = {"goal": goal}
