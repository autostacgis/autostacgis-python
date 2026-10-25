# Practical examples

The standard way to run a workflow is one self-contained `config.yaml` (or
`config.json`). Each example uses a `config.yaml` containing its catalog and
request. These examples select the plugins needed for the task and set only useful
workflow overrides. Other settings retain their installed defaults. Relative data,
output, and work paths resolve from the example's folder.

| Folder | Purpose | Inputs |
| --- | --- | --- |
| [pansharpen](pansharpen/config.yaml) | Pansharpen local imagery and publish Zarr plus STAC. | Run `create_demo.py` for synthetic MUL/PAN inputs. |
| [normalization](normalization/config.yaml) | Pansharpen and normalize a collection; includes a Python runner. | Calibrated, georeferenced MUL/PAN imagery and STAC sidecars in `data/`. |
| [grouped-normalization](grouped-normalization/config.yaml) | Normalize separately by platform. | Calibrated, georeferenced MUL/PAN imagery and STAC sidecars in `data/`. |
| [mosaic](mosaic/config.yaml) | Build seamlines and a surface-reflectance mosaic covering the requested area. | Projected multispectral surface-reflectance imagery on a common CRS, with STAC sidecars in `data/`; adjust the area. Install SpectralMatch for footprint cleanup and seamlines. |
| [mosaic-explicit](mosaic-explicit/config.yaml) | Declare alignment, footprints, cleanup, seamlines, masking, and mosaicking in a strict sequence with native input links. | Affine-georeferenced multispectral surface-reflectance imagery and STAC sidecars in `data/`; adjust the area. Install SpectralMatch. |
| [worldview](worldview/config.yaml) | Process raw WorldView imagery through correction, registration, normalization, and mosaicking. | Set imagery, DEM, and reference paths in `config.yaml`; matching local MUL/PAN products become two assets in one Item. Install the native dependencies. |
| [custom plugin](https://github.com/autostacgis/autostacgis-plugins/blob/main/examples/scale_plugin.py) | Author a processing plugin with a streaming scale kernel. | Read or import `scale_plugin.py`. |

Run from the repository root after installing AutoSTACGIS:

```sh
python examples/pansharpen/create_demo.py
autostacgis validate examples/pansharpen/config.yaml
autostacgis plan examples/pansharpen/config.yaml
autostacgis run examples/pansharpen/config.yaml
```

Use the same commands with another folder's `config.yaml` for the other workflows.
`python examples/normalization/run.py` also plans and runs normalization. Only
pansharpen generates demo data; the other workflows need your own inputs.

Generated `data/`, `output/`, and `work/` folders are ignored by Git.
`tools/export_examples.py` regenerates the four small workflow configurations.
WorldView, the explicit mosaic configuration, and the custom plugin are maintained directly.

Comprehensive configuration-field references live in the
[autostacgis specification repository](https://github.com/autostacgis/autostacgis/tree/main/examples).
They are reference documents, rather than copies of these practical workflows.

EOReader searches product folders itself; ordinary local examples use `stac-resolver`
to discover TIFFs and their sidecars. Producing plugin outputs declare saving policies for
format encoding, filesystem storage, and STAC metadata. See [data flow](../docs/data-flow.md)
for optional transfer budgets, sequence routing, and checkpoints.
