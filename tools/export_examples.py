"""Export the current capability-grouped workflow examples."""

from pathlib import Path

import yaml


def export(directory):
    directory = Path(directory)

    def write_example(name, processes, parameters, **execution):
        producer = (
            "mosaic"
            if name == "mosaic"
            else "pansharpen"
            if name == "pansharpen"
            else "global_radiometric_match"
        )
        document = {
            "solver": {"backward_chain": {}},
            "goal": {"metadata-value": parameters},
            "metadata_in": {"stac-resolver": {}},
            "process": {plugin: {} for plugin in processes},
        }
        port = "results" if producer == "global_radiometric_match" else "result"
        document["process"][producer]["outputs"] = {
            port: {
                "save": False,
                "encode": {"plugin": "zarr" if name == "pansharpen" else "geotiff"},
                "data_out": {"plugin": "filesystem"},
                "metadata_out": {"plugin": "stac-json"},
            }
        }
        if execution:
            document["execution"] = execution
        folder = directory / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.yaml").write_text(yaml.safe_dump(document, sort_keys=False, width=100))

    selection = {"kind": "raster", "require": {"assets.$asset.data_state:spectral_sampling": "multispectral"}}
    write_example(
        "pansharpen",
        ["pansharpen"],
        {
            "kind": "raster",
            "for_each": True,
            "select": selection,
            "path": "assets.$asset.data_state:pansharpened",
            "value": True,
        },
    )
    normalized = {
        "kind": "raster",
        "for_each": True,
        "select": selection,
        "jsonata": "asset.`data_state:relative_radiometric_normalization` = true and asset.`data_state:pansharpened` = true",
        "value": True,
    }
    processes = ["pansharpen", "global_radiometric_match"]
    write_example("normalization", processes, normalized)
    write_example(
        "grouped-normalization",
        processes,
        normalized,
        sequence={
            "global_radiometric_match.images": {
                "plugin": "group-by",
                "settings": {"field": "properties.platform"},
            }
        },
    )
    write_example(
        "mosaic",
        ["extract_footprints", "clean_footprints", "markov_seamlines", "mask_by_polygons", "mosaic"],
        {
            "name": "result",
            "kind": "raster",
            "jsonata": 'asset.`data_state:mosaic` = true and asset.`cf:standard_name` = "surface_bidirectional_reflectance" and asset.unit = "1"',
            "value": True,
            "coverage": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-156, 19], [-155, 19], [-155, 20], [-156, 20], [-156, 19]]],
                },
                "mode": "footprint",
            },
        },
    )


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1] / "examples")
