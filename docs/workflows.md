# Workflow configuration

A workflow is one JSON or YAML file. Plugin types are its top-level sections. Within a section, each globally unique plugin ID maps directly to its parameters. Installed plugin defaults fill omitted parameters; explicit values take precedence. There is no second copy of plugin settings in a request.

```json
{
  "solver": {"backward_chain": {}},
  "goal": {
    "metadata-value": {"path": "properties.ready", "value": true}
  }
}
```

This is the smallest explicit configuration. Supply input records through Python or a GUI, or add `metadata_in` providers to discover them. Add `process` plugins that can produce the requested state. The example goal is a placeholder to edit for your data, rather than a preconfigured processing operation.

```json
{
  "solver": {"backward_chain": {"timeout": 120}},
  "goal": {
    "metadata-value": {
      "jsonata": "asset.\"data_state:statistics_calculated\"",
      "value": true
    }
  },
  "metadata_in": {"stac-source": {"items": ["scene.json"]}},
  "process": {"statistics": {}},
  "trigger": {"now": {}},
  "services": {"storage": {"tile_size": 512}}
}
```

The capability sections are `trigger`, `data_in`, `metadata_in`, `checks`, `sequence`, `data_transfer`, `checkpoint`, `encode`, `process`, `data_out`, `metadata_out`, `goal`, `solver`, `services`, and `extensions`. Exactly one solver is required. At most one trigger may be configured; omission means run now. `now`, `apscheduler`, and `watchdog` use the same explicit selection pattern. Their scheduling and filesystem observer parameters retain their library-owned schemas. Python `app.trigger()` and CLI `run` use the configured trigger; `app.run()` remains the direct execution API.

A goal plugin names the solver it requires. Several instances of one goal can use an array of parameter objects; they are combined with `all_of`. A solver's settings schema lists its accepted parameters. Selecting two solvers, conflicting goal dependencies, a plugin under the wrong type, unknown workflow sections, invalid parameter types, or dependency cycles raises an error.

Dependencies are selected by unique plugin name. A plugin's optional `DEFAULT_CONFIG` is a literal dictionary of capability sections. Every name it references is automatically added to that plugin's `REQUIRES`. Its parameter objects fill missing configuration; explicit workflow values override them. Requirements are scoped to the caller, including automatically selected dependencies. Authors must still explicitly use a check or sequence in their processing contract: selecting a helper does not itself execute the helper.

Shared execution services and the default transfer adapter are supplied automatically. Configure their capability sections only when changing their defaults. `execution` is an optional section for orchestration such as explicit `steps`, source `query`, and work-directory settings. `metadata_structure` retains the shared STAC schema definitions described in [metadata-structures.md](metadata-structures.md).

Older `{catalog, request}` files and local Python request calls remain readable for compatibility. New GUI exports and published workflows use the grouped format. Contracts are installed code and cannot be replaced through configuration.

Local STAC assets and published metadata references use `file://` URIs, including Windows drive letters, network shares, spaces, and Unicode filenames. Use `autostacgis.io.local(href)` to obtain a native filesystem path from a local reference.

# Plugins and published workflows

The Python library owns online discovery and checksum verification for both registries. GUIs call the connected backend, which delegates to this library. A normal QGIS installation does not need to install the processing engine inside QGIS's Python interpreter.

```sh
autostacgis plugins list
autostacgis plugins available
autostacgis plugins install statistics --version 0.0.1
autostacgis plugins install-file ./my_check.py
autostacgis workflows available
autostacgis workflows download mosaic ./workflows
```

`plugins list` includes every installed standard and downloaded plugin, its version, description, dependencies, defaults, and parameter schema. Sources are Python files installed into the `autostacgis_plugins` namespace. Python package dependencies use `DEPENDENCIES`; plugin dependencies use `REQUIRES`. Every plugin file needs a meaningful module docstring. The library exposes it through `plugin_metadata.inventory()`. Explicit settings schemas take precedence over conservative reflection of simple annotated function parameters; complex annotations are left for text editing. Parameter descriptions come from the explicit schema or supported function docstrings.

Published sources use HTTPS, commit-pinned URLs and SHA-256 checksums. Downloading a workflow never imports or installs its plugins. Existing local files are preserved unless CLI `--overwrite` is set. Alternate registries use `--registry`.

Publish through a pull request to [autostacgis-plugins](https://github.com/autostacgis/autostacgis-plugins/blob/main/CONTRIBUTING.md) or [autostacgis-workflows](https://github.com/autostacgis/autostacgis-workflows/blob/main/CONTRIBUTING.md). Each entry has a unique ID, semantic version and description; changes require a version bump. GitHub Actions builds and deploys the registries.

In either GUI, activate a published or saved workflow to load its JSON into the editor. Published documents are copied before saving changes. Saved documents can be updated, copied under a new name, renamed, or deleted. Saved workflows live in the connected backend's database, including when that backend runs on your computer. This saved library is shared by web and QGIS. CLI integration deliberately stops at downloading JSON to a folder.

## Prerelease versions and result saving

All first-party versions remain `0.0.1` during prerelease development. Registry URLs pin commits and checksums, and installed `0.0.1` plugin revisions use content-addressed storage. Reinstalling a changed prerelease plugin activates its new checksum without requiring a version bump.

The published `mosaic` workflow aligns selected images, extracts and cleans their footprints, builds Voronoi seamlines, masks individual scenes and assembles a mosaic. Configure its CRS and resolution for the intended output. Its goal describes the mosaic state; saving is off by default. Set `process.<plugin>.outputs.<port>.save` independently for any final or intermediate result. See [data flow](data-flow.md#saving-named-results) for paths, JSONata naming and output defaults.
