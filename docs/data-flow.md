# Data flow, output, and resume

Metadata plugins find inputs and return STAC Items. EOReader owns filesystem search
(`root_directory`, `search_glob`, optional `constellations`) and product inspection.
Use `stac-resolver` for ordinary local rasters with exact `.tif.stac.json` sidecars,
`stac-source` for existing Items or STAC searches, and EODAG for provider searches.
`request.sources` selects these `metadata_in` plugins. `request.query` is passed to
them; EODAG maps `area` to its geographic search. EOReader's local search uses its
filesystem pattern. There is no separate glob or enrichment stage in configuration.

`data_in` acquires selected assets after planning. EODAG and remote providers can
supply both capabilities and share their search/download session. Available local
assets need no download. Core replans if acquisition changes the metadata.

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
on a named step/output. Set `catalog.plugins[].execution` on a processing plugin
to override these defaults for automatically planned steps, for example
`{use: statistics, execution: {transfer: file, checkpoint: null}}`. Optimized is the default. Memory explicitly requests
buffering; file requests file backing. For bytes, receivers must accept `path` or
`byte-stream` to allow spilling. This budget does not constrain memory inside
numerical libraries. Managed scratch is valid while the `RunResult` remains open.

## Saving outputs

An output goal or a process output can declare `publish`. Named steps can override
it under `outputs.<port>.publish`:

```yaml
goal:
  plugin: output
  settings:
    require: {assets.$asset.data_state:mosaic: true}
    publish:
      encode: {plugin: geotiff}
      data_out:
        plugin: filesystem
        settings: {path: ./output/mosaic.tif}
      metadata_out: {plugin: stac-json}
```

GeoTIFF, COG, Zarr, and Parquet provide `encode`. Encoders return artifacts; they do
not choose the final storage destination. `data_out` destinations declare
`input_types`/`output_types`: filesystem and S3 accept bytes, streams, and paths.
A process already producing bytes can save them directly without an encoder.
Encoders can supply `restore(saved, record, settings)` to reopen their encoded
artifacts; checkpoints use that interface and the encoder's `extension` rather than
assuming a particular file format.
Filesystem writes atomically and refuses existing outputs unless `overwrite: true`.
With no explicit path it generates a unique location below `output/data`.

The saved location becomes the selected asset href and updates the directory-root
metadata. Native format plugins can update embedded STAC through their own
`metadata_out` capability after the location is known. Metadata plugins return an
updated Item or null. `stac-item` applies `settings.set` directly in memory;
`stac-json` can save the whole Item or a dotted `value_path`, select `path` or
`directory`, and retain the location at an optional dotted `href_field`. It writes
through a declared `data_out` destination. The returned Item records that
destination's actual reference. For remote destinations this reference is retained
in the returned Item; local JSON can also retain its own reference in the file. `stac-api` sends Items directly to an API.

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
