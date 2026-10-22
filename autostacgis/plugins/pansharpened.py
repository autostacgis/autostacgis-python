"""Compile the pansharpened product request into output constraints."""

ID = "pansharpened"
REQUIRES = ()


def goal(settings, records, context):
    return {
        "for_each": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            "require": {"all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]},
            **settings,
        }
    }


IMPLEMENTATIONS = {"goal": goal}
