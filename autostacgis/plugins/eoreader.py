from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import AutoSTACGISError
from ..io import local, merge_verified


def eoreader_metadata(record, settings, app):
    from eoreader.reader import Reader
    from eoreader.stac.data_state import describe_product

    path = record.runtime.get("product_path")
    if not path or not Path(path).exists():
        return record
    work = app.work / "eoreader" / record.key
    work.mkdir(parents=True, exist_ok=True)
    product = Reader().open(path, output_path=work, remove_tmp=False, **settings.get("reader_kwargs", {}))
    if product is None:
        raise AutoSTACGISError(f"EOReader did not recognize {path}")
    try:
        item = describe_product(product)
    finally:
        product.close()
    # Reconcile by physical file as well as by name: EOReader and provider STAC
    # often use different names for the very same asset.
    previous = deepcopy(record.item)
    item["id"] = previous["id"]
    previous_properties = {
        k: v for k, v in previous.get("properties", {}).items() if k != "data_state:directory_root"
    }
    grouped = {"multispectral", "panchromatic"} <= {
        a.get("data_state:spectral_sampling") for a in item.get("assets", {}).values()
    }
    previous_grouped = {"multispectral", "panchromatic"} <= {
        a.get("data_state:spectral_sampling") for a in previous.get("assets", {}).values()
    }
    component_context = grouped and not previous_grouped
    # Existing metadata may describe only the component being inspected. Do
    # not promote its differing viewing/grid defaults to the combined Item.
    common = {
        k: v
        for k, v in previous_properties.items()
        if k in item["properties"] and k not in ("datetime", "start_datetime", "end_datetime")
    }
    merge_verified(item["properties"], common if component_context else previous_properties)
    assets = {}
    consumed = set()
    local_assets = record.runtime.setdefault("local_assets", {})

    def same_location(left, right):
        if all(urlsplit(str(value)).scheme in ("", "file") for value in (left, right)):
            return local(str(left)).resolve() == local(str(right)).resolve()
        return str(left) == str(right)

    for name, asset in item["assets"].items():
        matches = [
            key
            for key, old in previous.get("assets", {}).items()
            if key == name or same_location(local_assets.get(key, old["href"]), asset["href"])
        ]
        if len(matches) > 1:
            raise AutoSTACGISError(f"Ambiguous existing STAC assets for EOReader file: {asset['href']}")
        key = matches[0] if matches else name
        local_assets[key] = asset["href"]
        if matches:
            consumed.add(key)
            original = previous["assets"][key]
            if original.get("bands") and asset.get("bands"):
                if len(original["bands"]) != len(asset["bands"]):
                    raise AutoSTACGISError(f"Conflicting band counts for {key}")
                for actual, known in zip(asset["bands"], original["bands"], strict=True):
                    # Band names may be provider aliases; physical order is authoritative.
                    if "name" in known:
                        actual["name"] = known["name"]
                    merge_verified(actual, known)
            if component_context:
                merge_verified(asset, previous_properties)
            annotations = {k: v for k, v in original.items() if k not in ("href", "type", "roles", "bands")}
            merge_verified(asset, annotations)
            asset["href"] = original["href"]
        assets[key] = asset
    for name, asset in previous.get("assets", {}).items():
        if name in consumed:
            continue
        # Replace a provisional package/directory descriptor with its actual files.
        if (
            not asset.get("bands")
            and str(asset["href"]) == str(path)
            and (Path(path).is_dir() or Path(path).suffix.lower() in (".zip", ".tar", ".gz"))
        ):
            continue
        if name in assets:
            raise AutoSTACGISError(f"Conflicting STAC asset identity: {name}")
        assets[name] = asset
    item["assets"] = assets
    item["stac_extensions"] = list(
        dict.fromkeys([*item.get("stac_extensions", []), *previous.get("stac_extensions", [])])
    )
    if previous.get("geometry") is not None and not component_context:
        item["geometry"] = previous["geometry"]
        if "bbox" in previous:
            item["bbox"] = previous["bbox"]
    item["links"] = previous.get("links", item.get("links", []))
    if "collection" in previous:
        item["collection"] = previous["collection"]
    record.item = item
    app.stac.extensions(record.item)
    return record


class EoreaderSource:
    def discover(self, settings, query, app):
        from ..data.kinds import describe_kinds
        from ..model import Record, digest
        from ..product_discovery import DEFAULT_SEARCH_GLOB, discover_products

        for path in discover_products(settings.get("search_glob", DEFAULT_SEARCH_GLOB),
                                      root_dir=app.path(settings.get("root_directory", ".")),
                                      constellations=settings.get("constellations")):
            item = {"type": "Feature", "stac_version": "1.1.0", "id": digest(str(path))[:24],
                    "geometry": None, "properties": {}, "links": [], "stac_extensions": [],
                    "assets": {"data": {"href": str(path), "roles": ["data"]}}}
            record = Record(item, runtime={"product_path": str(path)})
            record = eoreader_metadata(record, settings, app)
            yield describe_kinds(record)


ID = "eoreader"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_in": EoreaderSource}
