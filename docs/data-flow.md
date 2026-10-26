# Data flow, output, and resume

Metadata plugins find inputs and return STAC Items. EOReader owns filesystem search
(`root_directory`, `search_glob`, optional `constellations`) and product inspection.
Use `stac-resolver` for ordinary local rasters with exact `.tif.stac.json` sidecars,
`stac-source` for existing Items or STAC searches, and EODAG for provider searches.
`execution.sources` selects these `metadata_in` plugins. `execution.query` is passed to
them; EODAG maps `area` to its geographic search. EOReader's local search uses its
filesystem pattern. There is no separate glob or enrichment stage in configuration.

`data_in` acquires selected assets after planning. Discovery and acquisition are
separate plugin registrations: `eodag` uses `eodag-download`, `dem` uses
`dem-download`, `atmosphere` uses `atmosphere-download`, and `stac-source` uses
`stac-source-download`. Each discovery adapter declares its acquisition dependency
and explicitly routes discovered records to it. EODAG retains each product with its
discovery session through scoped access to its downloader. Acquisition settings
belong to the download registration. Available local assets need no download. Core
replans if acquisition changes the metadata.

## Routing collections

`sequence` replaces `input_binding`. A process can declare a collection's default
sequence policy; a request can replace it with one call or a pipeline:

```yaml
sequence:
  global_radiometric_match.images:
    - plugin: group-by
      settings: {field: properties.platform}
    - plugin: split
      settings: {size: 8}
```

Core binds a separate operation for each group. A later collection operation can
use `{plugin: combine}` to gather all resulting members. Combining changes
membership, not pixels or STAC asset packaging. `scope: available` includes generated
objects; the default inventory scope fixes source membership before transformations.
Every selected member must have a suitable version before its operation runs.
Explicit native input references also honor these policies. A named step can override
its own ports under `steps.<name>.sequence`, so repeated uses of the same processing
plugin can split outputs in one stage and combine them in another.

## Typed transfer

Process input and output declarations use `data_types`, independently of `kind`:

```python
outputs = {
    'result': {
        'kind': 'raster',
        'data_types': ['raster'],
        'inherit_metadata': ['image'],
        'transfer': 'optimized',
    },
}
```

The built-in exchange types are native `raster`, Arrow `batches`, `bytes`, replayable
`byte-stream`, `path`, and `json`. Use `Artifact(type, value)` for explicit values;
byte streams take a factory returning a fresh iterator of byte chunks. Every output
must declare its types. Custom namespaced types need a matching transfer provider.

Core routes processing inputs, outputs, and encoder/destination calls through the
selected `data_transfer` provider. The default `buffered-transfer` reserves a shared
managed-buffer budget and spills native rasters, batches, and bytes when necessary:

```yaml
data_transfer:
  plugin: buffered-transfer
  settings: {memory_bytes: 268435456}
```

`transfer: memory`, `file`, or `optimized` can be declared by a plugin or overridden
on a named step/output under `execution.steps`. For example, `execution.steps.measure: {plugin: statistics, transfer: file, checkpoint: null}`. Optimized is the default. Memory explicitly requests
buffering; file requests file backing. For bytes, receivers must accept `path` or
`byte-stream` to allow spilling. This budget does not constrain memory inside
numerical libraries. Managed scratch is valid while the `RunResult` remains open.

## Saving named results

Goals specify the data to produce. Saving is configured separately on each producing plugin's named output. Standard plugins declare defaults, including `save: false`, an encoder, filename suffix tokens, and a destination path. The same fields appear in both GUI JSON editors and in the Python plugin inventory.

```json
{
  "solver": {"backward_chain": {}},
  "goal": {"metadata-value": {"path": "assets.$asset.data_state:statistics_calculated", "value": true}},
  "process": {
    "statistics": {
      "outputs": {
        "result": {
          "save": true,
          "data_out": {"plugin": "filesystem", "settings": {"path": "exports/scene.tif"}}
        }
      }
    }
  }
}
```

Use just `{"save": true}` to accept that output's plugin defaults. Omit `save`, or set it to false, to keep the result in the managed runtime. The standard defaults do not write durable output files. Algorithms may still create temporary working files and transfer buffers according to their storage policies.

Any named result can be saved, including intermediate results and multiple ports of one function. An optional `encode` plugin converts native rasters or Arrow batches into a typed artifact. The `data_transfer` provider then supplies the bytes, byte stream or path accepted by the selected `data_out` plugin. Already encoded outputs can set `encode: null` and go directly to the destination. `metadata_out` can independently update or save the result's STAC metadata. A named stage can override the same fields at `execution.steps.<step>.outputs.<port>`.

Each standard processing plugin appends its configured `filename_suffixes` to `assets.<key>.data_state:filename_suffixes`. Tokens belong to the individual asset; repeated operations keep repeated entries. Override this output option with another list, or an empty list to append nothing. Scientific state fields remain distinct from this naming history.

Per-scene plugins default to the stable `properties.data_state:output_directory`, initialized from the input Item's shared directory root. Their default JSONata path joins the asset tokens with underscores and adds the appropriate extension. When no local Item root is known, per-scene plugins declare an `output/<source-id>` folder. Collective outputs default to the workflow's `output` directory. Set `outputs.<port>.output_directory` to override the preferred folder literally or with JSONata. These choices are declared in the plugins, not in the executor. Change the metadata directory or override `data_out.settings.path` to select another location.

Destination and encoder settings may use `{"jsonata": "..."}` expressions. Expressions receive `item`, `asset`, `asset_key`, `kind`, `source_id`, and `source_asset`. For example:

```json
{"jsonata": "item.properties.`data_state:output_directory` & '/' & source_id & '_' & $join(asset.`data_state:filename_suffixes`, '_') & '.tif'"}
```

All saving and naming defaults are overridable in configuration. Changing a referenced plugin replaces that call's defaults instead of carrying settings from the previous plugin into the new one. Encoders and destinations are loaded only when needed by an enabled save policy. Filesystem saves remain atomic and refuse existing files unless `overwrite: true` is configured.

The PostgreSQL service returns unsaved result metadata without automatically materializing payloads or indexing transient asset URLs. Explicitly saved files are served in place and durable result Items are indexed in pgSTAC. Metadata writers can publish elsewhere through their own configured destination.

## Checkpoints

Enable durable reuse with:

```yaml
checkpoint:
  plugin: file-checkpoint
  settings:
    directory: work/checkpoints
    relative_to: config
    checks: [{plugin: valid-data}]
```

The default is no checkpointing. Each process or named step can override the
provider, or set `checkpoint: null`. A checkpoint uses the implementation identity,
settings, input metadata, and input content. Its manifest retains named output
Items, types, checksums, and file references across runs. Checksums and configured
`checks` validate a restored entry; invalid entries are deleted and recomputed.
`valid-data` reads actual raster blocks or Arrow batches. Checks can bind named
output collections with `inputs: {subject: result}`, using the same check argument
convention as processing contracts. Existing cache services
are replaced by this capability.

`relative_to` accepts `config`, `cwd`, or `item`. Item mode reads the shared local
`properties.data_state:directory_root`, or the configured dotted `item_path`.
An absolute `directory` selects a location directly. Checkpoint `encoders` maps
exchange types to format plugins and `data_out` selects the destination plugin.
The file provider manages local manifests and requires its destination to write
local files; a remote checkpoint backend can implement the same capability.

Processing functions can use `context.restore_checkpoint('part')` and
`context.save_checkpoint('part', outputs)`. A processing generator may instead yield
`Checkpoint('part', outputs)` and later return its final named outputs. Core saves
those events through the configured provider. On resume the algorithm can restore
its partial outputs, while completed steps are restored automatically.
