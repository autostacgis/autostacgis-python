# Plugins

Each plugin provides exactly one of `trigger`, `data_in`, `metadata_in`, `process`, `goal`, `solver`, `checks`, `sequence`, `encode`, `data_transfer`, `checkpoint`, `data_out`, `metadata_out`, `services`, or execution `extensions`. Modules explicitly export `ID`, `VERSION` (semantic version), `CAPABILITY` (one string), `IMPLEMENTATION` (one callable), and `REQUIRES`. `DEFAULTS`, when present, is the settings object for that single capability. Plural `IMPLEMENTATIONS` declarations are rejected. Standard plugins live in the separate [autostacgis-plugins repository](https://github.com/autostacgis/autostacgis-plugins/tree/main/plugins/autostacgis_plugins), with the complete [before-and-after inventory](https://github.com/autostacgis/autostacgis-plugins/blob/main/docs/plugin-migration.md).

Catalogs select installed registrations with `plugins: [{use: plugin_name, settings: {...}}]`. The namespace is the Python entry-point group `autostacgis.plugins`; unselected third-party entry points are not imported. Built-ins are also available by full module name, such as `autostacgis_plugins.markov_seamlines`. Declared dependencies load recursively; missing registrations and dependency cycles are errors. Compact settings configure the sole declared capability. Fully expanded definitions are accepted for aliasing and configuration; their `capabilities` object must contain exactly one entry. Empty or multi-capability definitions fail schema validation. JSON and YAML use the same strict JSON data model; duplicate keys, recursive YAML values, non-string keys, and nonfinite numbers are rejected. Processing contracts live with the Python implementation, attached by `@autostacgis.module(...)` or `function.contract`.

| Contract field | Meaning |
| --- | --- |
| `argument_requirements` | Named inputs, optional kind, `one` or `many` cardinality, metadata predicates, and sequence policy |
| `input_requirements` | Cross-input relationships, named check plugins, or pure Python callbacks |
| `outputs` | Named output kinds, required exchange `data_types`, and metadata construction rules |
| `cost` | Nonnegative number, JSONata expression, or pure Python function |
| `settings` | Default function settings; request `module_settings` overrides them |
| `version` | Implementation version included in generated identities |
| `extensions` | Declared execution plugins for resources and logging |

A function may accept `(inputs, settings, context)` with a dictionary of lists, or named arguments followed by `settings` and `context`. Named `one` arguments receive a `Record`; named `many` arguments receive a tuple. Both forms return `{output_name: [Record, ...]}`. Use `context.output(name, payload, facts=...)` to attach payloads and apply the output metadata policy. The complete example is in [scale_plugin.py](https://github.com/autostacgis/autostacgis-plugins/blob/main/examples/scale_plugin.py).

Requirements support `all`, `any`, `not`, and atomic `{path, equals}`, `{path, in}`, `{path, contains}`, `{path, entry}`, and `{path, exists}` expressions. A shorthand mapping means conjunction of exact path/value matches. `$asset` selects the bound asset. Missing or null evidence is unknown; negating an unknown stays unknown. Python predicates return `True`, `False`, or `None`. JSONata predicates receive `item`, `asset`, `asset_key`, `kind`, and explicit JSON `parameters`. A `{jsonata, equals}` requirement compares the expression's result to an arbitrary JSON value; without `equals`, it must return a boolean. Optional `observes` lists every metadata path read; omission means all metadata. Spatial functions `$intersects`, `$covers`, and `$within` accept GeoJSON or STAC bounding boxes, including a custom metadata geometry field. Missing or invalid geometry remains unknown.

Named checks use `{plugin, inputs: {check_argument: module_argument}, settings}`. Python cross-input checks receive `(inputs, settings)` and return a boolean. The planner evaluates declared output effects; it does not infer effects from execution code. Pure `predict(inputs, settings, index, item)` callbacks may supply additional metadata predictions. They must not read pixels, download data, mutate input records, or depend on time or randomness.

Output metadata starts with `inherit_metadata: []` or a list such as `[image, reference.properties.platform]`. Whole-object inheritance is recommended for most raster-to-raster and vector-to-vector operations. Later inputs win when merging. `set` is a list of single dotted-path assignments, `append` appends array elements, and `remove` removes paths. Literal null means unknown, not false. `each_from` creates one output for every bound member; without it a port creates one output. `geometry: union` predicts the combined input extent when all contributing geometries are known. Actual geometry and grid metadata are refreshed from output payloads.

Costs default to one. For example:

```python
cost = {
    'parameters': {'history': 'image.assets.$asset.data_state:resampled'},
    'jsonata': '1 + ($type(history) = "array" ? $count(history) : 0)',
}
```

Expressions also receive `inputs` (lists of Item JSON per port) and `settings`. A Python cost receives `(inputs, settings)`. It may declare `cost_dependencies`, a list of metadata paths read, to narrow backward relevance; omitted dependencies are treated conservatively. Check declarations can likewise declare `observes`. Costs must be deterministic, finite, and nonnegative. Cost previews guide backward construction; final costs use actual selected metadata. The solver keeps one successful cost-guided derivation per requirement and does not enumerate every operation permutation, so it makes no global optimality guarantee. Hard prerequisites belong in requirements. Declare effects with `set`/`append` so producer discovery can find them; predictions fill in additional metadata.

For a collection input, declare `cardinality: many` and `sequence: {plugin: gather-all}`. Default group membership is selected from the initial inventory before testing processing requirements. Thus a module waits for a suitable version of every selected member. A grouping's `settings.select` filters this inventory. A request may override `sequence['module.port']` with another sequence plugin or pipeline, or null to allow the planner to choose subsets. `scope: available` permits grouping generated objects. Members remain independent data objects, so grouped outputs can feed individual processing steps.

Include the selected sequence plugin directly in the processing plugin's `REQUIRES`, for example `REQUIRES = ("gather-all",)`. Reuse that grouping implementation across processing plugins. A separate sequence plugin should implement distinct grouping behavior; forwarding unchanged inputs to `gather-all` does not need another registration.

Product goals are declarative metadata constraints in `request.goal`; former product presets are [configuration fragments](https://github.com/autostacgis/autostacgis-plugins/tree/main/examples/goals). A generic goal adapter may provide executable translation for one solver: `metadata-value` requires exactly one of `path` or `jsonata`, plus an expected `value`. Its optional `parameters` can hold an AOI for spatial expressions in the goal and its source selection. It retains names, kinds, selection, per-image outputs, counts and coverage. Saving is configured on each process output, independently of its goal. Every goal implementation declares a JSON `contract` with `solver` and strict `settings_schema`, and includes that solver's exact plugin ID in `REQUIRES`. It accepts `(settings, initial_records, context)` and returns the required solver's goal parameters.

Exactly one solver may be selected. Solver implementations expose `contract.settings_schema`, default `contract.settings`, and `contract.goal_schema`; unknown or invalid parameters are rejected. `backward_chain` declares `max_steps`, `max_states`, `max_bindings`, and `timeout` and supports `output`, `for_each`, `all_of`, and `any_of`. It constructs workflows backward from output constraints and can accept other goal adapters targeting that contract. Select it with `solver: {plugin: backward_chain, settings: {...}}`; request overrides must name the same active solver. Goal compatibility and dependencies are checked before search. See [solver contracts, JSONata AOI examples, and execution interface](https://github.com/autostacgis/autostacgis-plugins/blob/main/docs/solvers.md).

Predicted outputs are planning descriptions, not published STAC products. Actual execution verifies declared effects and final goals, including geometry coverage. Requirements involving unknown pixel-derived metadata need a processing module with a truthful prediction or a supplied measured value; unknown evidence cannot certify success.

Third-party registration:

```toml
[project.entry-points."autostacgis.plugins"]
my_processing = "my_package.my_processing"
```

The module exports `ID = "my_processing"`, `VERSION = "0.0.1"`, `CAPABILITY = "process"`, `IMPLEMENTATION = my_processing`, and `REQUIRES = ()`. Programmatic registration is `registry.register("my_processing", "process", my_processing)`. Registering advertises a plugin; selecting `use: my_processing` loads it. Multiple algorithms establishing the same requested state are an error, rather than a cost tie. Costs still choose data bindings and guide the ordering of different required transformations.

Registration and normalization costs count the true entries in `data_state:masked` (false and null flags do not count); collection costs use the least-masked member. Registration-history and atmospheric-correction resampling penalties remain in place.

`sample_dem` accepts named `image` and `dem` inputs and produces a table with role `elevation-summary`, unit `m`, and `properties.autostacgis:mean_elevation_m`. Its numeric value is unknown during planning and measured during execution; empty or entirely invalid samples are errors. The Py6S `elevation` input must refer to the same scene and contain a finite measured elevation.

Coregix uses seekable rasters and a single isolated native worker per scheduled registration, with one ITK/OpenMP thread. Its fitted Elastix parameter maps and solve-grid coordinates are retained in registration metadata. Intensity registration has no discrete tie-point list, so `tie_points` is null. For large rasters configure Coregix `split_factor` and `solve_resolutions`; materialization does not imply constant-memory processing inside the backend. Insufficient overlap fails the registration requirement.

`clean_footprints` exposes SpectralMatch hole-cutting, inward smoothing, simplification, and component filtering settings. Distances are in projected CRS units; this and Markov seamlines require projected data. Set the RPC orthorectification CRS accordingly. The native backend may use temporary vector/raster files; outputs return through the same Arrow/raster interfaces.

Joint registration and relative radiometric matching reject inputs already clipped by mosaic seamlines: those operations need common valid pixels. Cloud/shadow masks are preferred through costs. Pansharpening also checks compatible registration histories; a corrected MUL and an uncorrected PAN do not become aligned merely because their CRS codes match. These are data prerequisites, not plugin ordering fields.

See [data flow and resume](data-flow.md) for metadata discovery, typed artifact
contracts, split/combine routing, destinations, publication, and checkpoints.

Named steps may override sequence policies by input port. Format encoders expose
`input_types` and `output_types`, optionally `extension`, `suffixes`, and
`restore(saved_artifact, record, settings)` for durable restoration. An artifact can
carry a `metadata_out` call to update embedded metadata after saving.


Discovery and acquisition use separate plugins. A metadata provider may declare a
`data_in` class attribute naming a declared acquisition dependency. The core records
that route on discovered records; acquisition runs only for selected records.
`context.acquirer(name)` grants scoped access to that declared downloader so stateful
adapters such as EODAG can share a gateway and selected products. Configure acquisition
settings on the `*-download` registration, separately from discovery settings.
Encoder plugins depend on separate `*-metadata` writers, which update embedded STAC
after publication. Product goals use declarative `output` and `for_each` constraints.

## Descriptions, defaults, and editors

Every plugin module must begin with a useful module docstring. It is the shared plugin description used by the CLI and GUIs. Declare a semantic `VERSION` and bump it when changing the file. `DEFAULTS` supplies the plugin's own parameters. An optional literal `DEFAULT_CONFIG = {"sequence": {"gather-all": {}}}` selects supporting plugins, supplies their default parameters, and adds those names to this plugin's required dependencies. Explicit workflow parameters win. Declare actual check/sequence use in the processing contract.

Provide `IMPLEMENTATION.settings_schema` (or the contract's `settings_schema`) with JSON types, parameter descriptions, optional defaults, and `additionalProperties: false` when the parameter set is closed. Both editors offer missing declared parameters with their defaults. Simple typed function parameters can also be reflected; unsupported annotations are left editable as JSON. Read [workflow configuration](workflows.md) for the grouped file format and registry CLI.
