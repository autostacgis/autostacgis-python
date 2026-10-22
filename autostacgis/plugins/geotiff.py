from ..encoding import embed, encode, restore


def geotiff(record, settings, context):
    return encode(record, settings, context, "geotiff")


def metadata(record, settings, context):
    return embed(record, settings, context, "geotiff")


def open_artifact(saved, record, settings):
    return restore(saved, record, settings, "geotiff")


geotiff.restore = open_artifact


geotiff.extension = ".tif"
geotiff.suffixes = [".tif", ".tiff"]
geotiff.input_types = ["raster"]
geotiff.output_types = ["path"]
ID = "geotiff"
REQUIRES = ()
IMPLEMENTATIONS = {"encode": geotiff, "metadata_out": metadata}
