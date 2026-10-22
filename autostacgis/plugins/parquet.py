from ..encoding import encode, restore


def parquet(record, settings, context):
    return encode(record, settings, context, "parquet")


def open_artifact(saved, record, settings):
    return restore(saved, record, settings, "parquet")


parquet.restore = open_artifact


parquet.extension = ".parquet"
parquet.suffixes = [".parquet"]
parquet.input_types = ["batches"]
parquet.output_types = ["path"]
ID = "parquet"
REQUIRES = ()
IMPLEMENTATIONS = {"encode": parquet}
