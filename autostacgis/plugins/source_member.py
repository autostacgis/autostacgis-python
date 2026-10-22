"""Check that an image belongs to a collection's immutable source lineage."""


def source_id(record):
    return record.runtime.get(
        "source_id",
        record.runtime.get(
            "original_id", record.item.get("properties", {}).get("autostacgis:source_id", record.item["id"])
        ),
    )


def source_member(inputs, settings, context):
    if all("lineage" in r.runtime for group in inputs.values() for r in group):
        members = set().union(*(set(r.runtime["lineage"]) for r in inputs["member"]))
        sources = set().union(*(set(r.runtime["lineage"]) for r in inputs["collection"]))
        return "pass" if members and members <= sources else "fail"
    members = {source_id(record) for record in inputs["member"]}
    sources = set()
    for record in inputs["collection"]:
        asset = record.item.get("assets", {}).get(record.runtime.get("selected_asset", "data"), {})
        sources.update(asset.get("autostacgis:seamline_sources", [source_id(record)]))
    return "pass" if members and members <= sources else "fail"


ID = "source-member"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": source_member}
