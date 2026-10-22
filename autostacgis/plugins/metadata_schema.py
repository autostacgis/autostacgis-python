def metadata_schema(inputs, settings, context):
    from jsonschema import ValidationError

    try:
        for records in inputs.values():
            for record in records:
                context.stac.validate_schema(record.item, settings["schema"])
    except ValidationError:
        return "fail"
    return "pass"


ID = "metadata-schema"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": metadata_schema}
