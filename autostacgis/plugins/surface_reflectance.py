"""Compile the surface-reflectance product request into output constraints."""

ID = "surface-reflectance"
REQUIRES = ()


def goal(settings, records, context):
    return {
        "for_each": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            "require": {
                "all": [
                    {"path": "assets.$asset.cf:standard_name", "equals": "surface_bidirectional_reflectance"},
                    {"path": "assets.$asset.unit", "equals": "1"},
                ]
            },
            **settings,
        }
    }


IMPLEMENTATIONS = {"goal": goal}
