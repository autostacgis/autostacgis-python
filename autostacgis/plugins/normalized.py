"""Compile the normalized product request into output constraints."""

ID = "normalized"
REQUIRES = ()


def goal(settings, records, context):
    return {
        "for_each": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:relative_radiometric_normalization", "equals": True}
                ]
            },
            **settings,
        }
    }


IMPLEMENTATIONS = {"goal": goal}
