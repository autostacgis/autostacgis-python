"""Validate actual saved payloads, including reading every raster block or Arrow batch."""

from ..artifacts import artifact


def valid_data(inputs, settings, context):
    try:
        for records in inputs.values():
            for record in records:
                value = artifact(record.payload)
                if value.type == "raster":
                    for block in value.value.blocks():
                        if block.pixels.size == 0:
                            return "fail"
                elif value.type == "batches":
                    for batch in value.value.batches():
                        batch.validate(full=True)
                elif value.type == "path":
                    if not value.value.exists():
                        return "fail"
        return "pass"
    except (OSError, ValueError):
        return "fail"


ID = "valid-data"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": valid_data}
