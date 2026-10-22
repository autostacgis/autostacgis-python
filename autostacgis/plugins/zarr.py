from ..encoding import embed, encode, restore


def zarr_writer(record, settings, context):
    return encode(record, settings, context, "zarr")


def metadata(record, settings, context):
    return embed(record, settings, context, "zarr")


def open_artifact(saved, record, settings):
    return restore(saved, record, settings, "zarr")


zarr_writer.restore = open_artifact


zarr_writer.extension = ".zarr"
zarr_writer.suffixes = [".zarr"]
zarr_writer.input_types = ["raster"]
zarr_writer.output_types = ["path"]
ID = "zarr"
REQUIRES = ()
IMPLEMENTATIONS = {"encode": zarr_writer, "metadata_out": metadata}
