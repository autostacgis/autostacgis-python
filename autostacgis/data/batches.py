"""Arrow record batches for vector, table and point-cloud exchange."""

import json

from ..errors import AutoSTACGISError


class Batches:
    replayable = True

    def __init__(self, batches, *, kind="table", schema=None, crs=None):
        import pyarrow as pa

        if kind not in ("vector", "point_cloud", "table"):
            raise AutoSTACGISError(f"Invalid Arrow artifact kind: {kind}")
        self.kind, self.crs = kind, crs
        self._batches = tuple(batches)
        self.schema = schema or (self._batches[0].schema if self._batches else pa.schema([]))
        if any(batch.schema != self.schema for batch in self._batches):
            raise AutoSTACGISError("All Arrow batches must have the same schema")
        if kind == "point_cloud" and not {"X", "Y", "Z"} <= set(self.schema.names):
            raise AutoSTACGISError("Point-cloud batches require X, Y and Z columns")

    def batches(self):
        yield from self._batches

    def __arrow_c_stream__(self, requested_schema=None):
        import pyarrow as pa

        return pa.RecordBatchReader.from_batches(self.schema, self.batches()).__arrow_c_stream__(
            requested_schema
        )


class FileBatches(Batches):
    def __init__(self, path, *, kind="table", crs=None, batch_size=65536):
        import pyarrow.parquet as pq

        self.path, self.kind, self.crs, self.batch_size = str(path), kind, crs, batch_size
        self.schema = pq.ParquetFile(self.path).schema_arrow
        geo = json.loads((self.schema.metadata or {}).get(b"geo", b"{}"))
        if kind == "vector" and self.crs is None and geo:
            self.crs = geo["columns"][geo["primary_column"]].get("crs")
        if kind == "point_cloud" and not {"X", "Y", "Z"} <= set(self.schema.names):
            raise AutoSTACGISError("Point-cloud batches require X, Y and Z columns")

    def batches(self):
        import pyarrow.parquet as pq

        yield from pq.ParquetFile(self.path).iter_batches(batch_size=self.batch_size)


def vectors(geometries, *, crs=None, attributes=None):
    import pyarrow as pa
    from shapely import to_wkb

    metadata = {
        b"ARROW:extension:name": b"geoarrow.wkb",
        b"ARROW:extension:metadata": json.dumps({"crs": crs_document(crs)}).encode(),
    }
    fields = [pa.field("geometry", pa.binary(), metadata=metadata)]
    arrays = [pa.array(to_wkb(geometries), type=pa.binary())]
    for name, values in (attributes or {}).items():
        array = pa.array(values)
        fields.append(pa.field(name, array.type))
        arrays.append(array)
    batch = pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))
    return Batches([batch], kind="vector", crs=crs)


def crs_document(crs):
    if crs is None or isinstance(crs, dict):
        return crs
    from pyproj import CRS

    return CRS.from_user_input(crs).to_json_dict()


class VectorFile(Batches):
    """GDAL/OGR Arrow stream, reopened for each iteration."""

    def __init__(self, path, *, batch_size=65536):
        import pyarrow as pa
        from osgeo import ogr

        self.path, self.kind, self.batch_size = str(path), "vector", batch_size
        dataset = ogr.Open(self.path)
        if dataset is None:
            raise AutoSTACGISError(f"Cannot open vector dataset {path}")
        layer = dataset.GetLayer(0)
        self.crs = layer.GetSpatialRef().ExportToWkt() if layer.GetSpatialRef() else None
        stream = pa.RecordBatchReader.from_stream(
            layer.GetArrowStreamAsPyArrow(options=[f"MAX_FEATURES_IN_BATCH={batch_size}"])
        )
        original = stream.schema
        self.geometry_name = next(
            (
                f.name
                for f in original
                if (f.metadata or {}).get(b"ARROW:extension:name") in (b"ogc.wkb", b"geoarrow.wkb")
            ),
            None,
        )
        if not self.geometry_name:
            raise AutoSTACGISError("Vector Arrow stream has no WKB geometry")
        self.schema = pa.schema(
            [
                pa.field("geometry" if f.name == self.geometry_name else f.name, f.type, metadata=f.metadata)
                for f in original
            ]
        )

    def batches(self):
        import pyarrow as pa
        from osgeo import ogr

        dataset = ogr.Open(self.path)
        layer = dataset.GetLayer(0)
        stream = pa.RecordBatchReader.from_stream(
            layer.GetArrowStreamAsPyArrow(options=[f"MAX_FEATURES_IN_BATCH={self.batch_size}"])
        )
        for batch in stream:
            yield pa.RecordBatch.from_arrays(batch.columns, schema=self.schema)


class LasBatches(Batches):
    """Chunked LAS/LAZ decoding into native Arrow columns."""

    def __init__(self, path, *, batch_size=65536):
        import laspy

        self.path, self.kind, self.batch_size = str(path), "point_cloud", batch_size
        with laspy.open(self.path) as reader:
            crs = reader.header.parse_crs()
        self.crs = crs.to_json_dict() if crs else None
        with laspy.open(self.path) as reader:
            empty = reader.read_points(0)
            import pyarrow as pa

            self.schema = pa.schema(
                [
                    pa.field("X", pa.float64()),
                    pa.field("Y", pa.float64()),
                    pa.field("Z", pa.float64()),
                    *[
                        pa.field(
                            "Intensity" if name == "intensity" else name,
                            pa.from_numpy_dtype(empty[name].dtype),
                        )
                        for name in empty.point_format.dimension_names
                        if name not in ("X", "Y", "Z")
                    ],
                ]
            )

    def batches(self):
        import laspy
        import pyarrow as pa

        with laspy.open(self.path) as reader:
            for chunk in reader.chunk_iterator(self.batch_size):
                columns = {"X": pa.array(chunk.x), "Y": pa.array(chunk.y), "Z": pa.array(chunk.z)}
                for name in chunk.point_format.dimension_names:
                    if name not in ("X", "Y", "Z"):
                        columns["Intensity" if name == "intensity" else name] = pa.array(chunk[name])
                yield pa.RecordBatch.from_pydict(columns)
