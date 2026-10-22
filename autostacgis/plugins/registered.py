"""Compile the registered product request into output constraints."""

ID = "registered"
REQUIRES = ()


def goal(settings, records, context):
    return {
        "for_each": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            "require": {
                "all": [{"path": "assets.$asset.data_state:registration", "entry": {"kind": "reference"}}]
            },
            **settings,
        }
    }


IMPLEMENTATIONS = {"goal": goal}
