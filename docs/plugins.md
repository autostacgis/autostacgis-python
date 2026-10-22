# Plugins

One plugin can provide any combination of `trigger`, `data_in`, `metadata_in`, `process`, `goal`, `checks`, `sequence`, `encode`, `data_transfer`, `checkpoint`, `data_out`, `metadata_out`, `services`, and execution `extensions`. These are interfaces of one registry, not exclusive plugin classes. Each built-in lives in its own Python file with `ID`, `REQUIRES`, and `IMPLEMENTATIONS`. Entry points use the `autostacgis.plugins` group.

Catalogs select installed registrations with `plugins: [{use: plugin_name, settings: {...}}]`. The namespace is the Python entry-point group `autostacgis.plugins`; unselected third-party entry points are not imported. Built-ins are also available by full module name, such as `autostacgis.plugins.markov_seamlines`. Declared dependencies load recursively; missing registrations and dependency cycles are errors. Compact settings configure the processing capability when present, or the non-processing capabilities otherwise. Fully expanded capability definitions are also accepted for explicit aliasing and configuration. JSON and YAML use the same strict JSON data model; duplicate keys, recursive YAML values, non-string keys, and nonfinite numbers are rejected. Processing contracts live with the Python implementation, attached by `@autostacgis.module(...)` or `function.contract`.

| Contract field | Meaning |
| --- | --- |
| `argument_requirements` | Named inputs, optional kind, `one` or `many` cardinality, metadata predicates, and sequence policy |
| `input_requirements` | Cross-input relationships, named check plugins, or pure Python callbacks |
| `outputs` | Named output kinds, required exchange `data_types`, and metadata construction rules |
| `cost` | Nonnegative number, JSONata expression, or pure Python function |
| `settings` | Default function settings; request `module_settings` overrides them |
| `version` | Implementation version included in generated identities |
| `extensions` | Declared execution plugins for resources and logging |

A function may accept `(inputs, settings, context)` with a dictionary of lists, or named arguments followed by `settings` and `context`. Named `one` arguments receive a `Record`; named `many` arguments receive a tuple. Both forms return `{output_name: [Record, ...]}`. Use `context.output(name, payload, facts=...)` to attach payloads and apply the output metadata policy. The complete example is in `examples/custom-plugin/scale_plugin.py`.

Requirements support `all`, `any`, `not`, and atomic `{path, equals}`, `{path, in}`, `{path, contains}`, `{path, entry}`, and `{path, exists}` expressions. A shorthand mapping means conjunction of exact path/value matches. `$asset` selects the bound asset. Missing or null evidence is unknown; negating an unknown stays unknown. Python predicates return `True`, `False`, or `None`. JSONata predicates receive `item` and `kind`.

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

Goal plugins accept `(settings, initial_records, application)` and return a declarative goal. `all_of` combines goals, `any_of` chooses an alternative, and repeated output names constrain the same result. `for_each` binds the initial selection, not every object subsequently created. The `filter` and `for-each` goal plugins expose this operation. Output goals support `kind`, `require`, `count`, source `select`, and spatial `coverage`. A `select` on an output requires ancestry from every selected source; omit it when the solver may choose a subset.

Predicted outputs are planning descriptions, not published STAC products. Actual execution verifies declared effects and final goals, including geometry coverage. Requirements involving unknown pixel-derived metadata need a processing module with a truthful prediction or a supplied measured value; unknown evidence cannot certify success.

Third-party registration:

```toml
[project.entry-points."autostacgis.plugins"]
my_processing = "my_package.my_processing"
```

The module exports `ID = "my_processing"`, `REQUIRES`, and `IMPLEMENTATIONS`. Registering advertises a plugin; selecting `use: my_processing` loads it. Multiple algorithms establishing the same requested state are an error, rather than a cost tie. Costs still choose data bindings and guide the ordering of different required transformations.

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
