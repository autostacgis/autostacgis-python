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
                "version": "1.0.0",
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

        publish = {"encode": {"plugin": "zarr" if name == "pansharpen" else "geotiff"},
                   "data_out": {"plugin": "filesystem"}, "metadata_out": {"plugin": "stac-json"}}
        if "plugin" in goal:
            goal.setdefault("settings", {})["publish"] = publish
        else:
            goal["all_of"].append({"plugin": "output", "settings": {"name": "result", "publish": publish}})
        (folder / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        for legacy in ("catalog.yaml", "request.yaml", "catalog.json", "request.json", "config.json"):
            (folder / legacy).unlink(missing_ok=True)

    write_example("pansharpen", ["pansharpen", "pansharpened"], {"plugin": "pansharpened"})
    normalization = ["pansharpen", "global_radiometric_match", "normalized-pansharpened"]
    write_example("normalization", normalization, {"plugin": "normalized-pansharpened"})
    write_example(
        "grouped-normalization",
        [*normalization, "group-by"],
        {"plugin": "normalized-pansharpened"},
        sequence={
            "global_radiometric_match.images": {
                "plugin": "group-by",
                "settings": {"field": "properties.platform"},
            }
        },
    )
    write_example(
        "mosaic",
        [
            "extract_footprints",
            "clean_footprints",
            "markov_seamlines",
            "mask_by_polygons",
            "mosaic",
            "output",
        ],
        {
            "all_of": [
                {"plugin": "mosaic", "settings": {"name": "result", "area": [-156, 19, -155, 20]}},
                {
                    "plugin": "output",
                    "settings": {
                        "name": "result",
                        "require": {
                            "assets.$asset.cf:standard_name": "surface_bidirectional_reflectance",
                            "assets.$asset.unit": "1",
                        },
                    },
                },
            ]
        },
    )


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1] / "examples")
