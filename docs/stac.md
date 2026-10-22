# STAC data descriptions

A `Record` contains a STAC Item, a selected asset binding, its data kind, immutable workflow identity, and a separate payload handle. Source and generated records use the same model. One Item may contain several assets; the planner binds them independently. Paths such as `assets.$asset.unit` access the selected asset and supported Item/band defaults.

Reuse existing STAC Items through `stac-source`, `stac-api`, local JSON matches, or exact file sidecars (`scene.tif.stac.json`). EODAG discovers remote products without downloading them. EOReader inspects supported sensor products; it is not a universal reader for arbitrary STAC JSON, drone photos, vector formats, or point clouds. Raster and table inspectors and source plugins provide other metadata. Metadata plugins own discovery and any needed inspection. EOReader searches product directories internally. Acquisition downloads only assets used by the chosen plan; core replans when a provider updates their metadata.

Use standard STAC fields and extensions for physical quantities, units, spectral bands, CRS, raster shape, and processing lineage. Independent processing properties use the [Data State extension](https://github.com/autostacgis/stac-data-state). Plugin predicates can reference any metadata path; the planner contains no sensor-specific field vocabulary.

STAC Item geometry is WGS84. A raster asset's native grid can use a projected or geographic CRS. `proj:code` alone does not establish a projected grid; selectors inspect the CRS definition. RPC imagery and raw drone photographs require their own georeferencing information. Unknown spatial extent is `geometry: null` with no `bbox`. Camera position alone must not be represented as an image footprint.

Discovery adapters infer common raster, vector, and point-cloud asset formats. Configure `type` when the format alone is ambiguous, for example ordinary versus geospatial Parquet. Mixed Items can carry `runtime.asset_kinds` per asset. These bindings are execution information, not additions to STAC core.

A coverage goal checks an output geometry against the requested GeoJSON polygon. Footprint coverage does not establish valid or cloud-free pixel coverage. For measured valid coverage use `coverage: {mode: valid_data, path: ..., geometry: ...}` with a known geometry stored at that metadata path. Unknown geometry cannot satisfy coverage. Applications dealing with antimeridian-crossing regions should supply consistently split GeoJSON geometries.

STAC JSON stays separate from raster buffers and Arrow batches. Writers can embed it in Zarr attributes or publish ordinary STAC documents. Actual outputs are validated against pinned core and extension schemas before publication. Metadata cannot substitute for the underlying raster, features, or points.

EOReader exports sensor calibration provenance per band as
`data_state:radiance_calibration` (`gain`, `offset`, `source`). The effective
conversion is already represented by `raster:scale` and `raster:offset`; do not
apply the provenance coefficients twice. WorldView retains EOReader's installed
sensor calibration tables. Band centers use `eo:center_wavelength` and lower/upper
half-maximum bounds use `data_state:wavelength_bounds`, both in micrometers.
No workflow constant table is needed.

WorldView discovery groups matching local extracted MUL/PAN components into one
Item with `mul` and `pan` data assets. TIL basenames must match except for the
M/P component letter. Missing companions stay standalone; ambiguous matches fail.
The component assets retain their own grids, calibration and viewing metadata.
The inventory binds them independently for processing.

`properties.data_state:directory_root` is computed from the deepest common parent
of every asset href, including previews and metadata. It may be a directory path
or URI; incompatible locations yield null. The field is recalculated when core
changes the asset locations, including publication. It is not a relative-path base.
