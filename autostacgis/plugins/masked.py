"""Compile the masked product request into output constraints."""

ID = "masked"
REQUIRES = ()


def goal(settings, records, context):
    return {
        "for_each": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            "require": {
                "all": [
                    {"path": "assets.$asset.data_state:masked.cloud", "equals": True},
                    {"path": "assets.$asset.data_state:masked.cloud_shadow", "equals": True},
                ]
            },
            **settings,
        }
    }


IMPLEMENTATIONS = {"goal": goal}
