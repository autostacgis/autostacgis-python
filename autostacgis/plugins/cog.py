from ..encoding import embed, encode, restore


def cog(record, settings, context):
    return encode(record, settings, context, "cog")


def metadata(record, settings, context):
    return embed(record, settings, context, "cog")


def open_artifact(saved, record, settings):
    return restore(saved, record, settings, "cog")


cog.restore = open_artifact


cog.extension = ".tif"
cog.suffixes = []
cog.input_types = ["raster"]
cog.output_types = ["path"]
ID = "cog"
REQUIRES = ()
IMPLEMENTATIONS = {"encode": cog, "metadata_out": metadata}
