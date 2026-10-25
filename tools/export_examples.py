"""Export small, self-contained Python workflow examples."""

from pathlib import Path

import yaml


def export(directory):
    directory = Path(directory)

    def write_example(name, plugins, goal, **overrides):
        folder = directory / name
        folder.mkdir(parents=True, exist_ok=True)
        config = {
            "catalog": {
                "version": "0.0.1",
                "plugins": [
                    {"use": p}
                    for p in ["stac-resolver", *plugins, "executor", "events", "geotiff", "stac-json"]
                ],
            },
            "request": {
                "sources": ["stac-resolver"],
                "goal": goal,
                **overrides,
            },
        }
        if name == "pansharpen":
            config["catalog"]["plugins"][-2] = {"use": "zarr"}

        saving = {
            "save": True,
            "encode": {"plugin": "zarr" if name == "pansharpen" else "geotiff"},
            "data_out": {"plugin": "filesystem"},
            "metadata_out": {"plugin": "stac-json"},
        }
        producer = (
            "mosaic"
            if name == "mosaic"
            else "pansharpen"
            if name == "pansharpen"
            else "global_radiometric_match"
        )
        entry = next(p for p in config["catalog"]["plugins"] if p["use"] == producer)
        entry["execution"] = {"outputs": {"result": saving}}
        (folder / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        for legacy in ("catalog.yaml", "request.yaml", "catalog.json", "request.json", "config.json"):
            (folder / legacy).unlink(missing_ok=True)

    def per_image(requirements):
        return {
            "for_each": {
                "kind": "raster",
                "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
                "require": requirements,
            }
        }

    write_example("pansharpen", ["pansharpen"], per_image({"assets.$asset.data_state:pansharpened": True}))
    normalization = ["pansharpen", "global_radiometric_match"]
    normalized = {
        "assets.$asset.data_state:relative_radiometric_normalization": True,
        "assets.$asset.data_state:pansharpened": True,
    }
    write_example("normalization", normalization, per_image(normalized))
    write_example(
        "grouped-normalization",
        [*normalization, "group-by"],
        per_image(normalized),
        sequence={
            "global_radiometric_match.images": {
                "plugin": "group-by",
                "settings": {"field": "properties.platform"},
            }
        },
    )
    import json

    from shapely.geometry import box, mapping

    write_example(
        "mosaic",
        ["extract_footprints", "clean_footprints", "markov_seamlines", "mask_by_polygons", "mosaic"],
        {
            "all_of": [
                {
                    "output": {
                        "name": "result",
                        "kind": "raster",
                        "require": {
                            "assets.$asset.data_state:mosaic": True,
                            "assets.$asset.cf:standard_name": "surface_bidirectional_reflectance",
                            "assets.$asset.unit": "1",
                        },
                        "coverage": {
                            "geometry": json.loads(json.dumps(mapping(box(-156, 19, -155, 20)))),
                            "mode": "footprint",
                        },
                    }
                }
            ]
        },
    )


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1] / "examples")
