"""Shared STAC validation and derived Item construction."""

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft7Validator, FormatChecker
from pystac.validation.local_validator import get_local_schema_cache
from referencing import Registry, Resource

from .config import read
from .errors import AutoSTACGISError

STATE = "https://autostacgis.github.io/stac-data-state/v0.0.1/schema.json"
EXTENSIONS = {
    "data_state:": STATE,
    **{
        prefix + ":": f"https://stac-extensions.github.io/{name}/v{version}/schema.json"
        for prefix, name, version in [
            ("eo", "eo", "2.0.0"),
            ("raster", "raster", "2.0.0"),
            ("proj", "projection", "2.1.0"),
            ("view", "view", "1.1.0"),
            ("processing", "processing", "1.2.0"),
            ("cf", "cf", "1.0.0"),
            ("product", "product", "1.1.0"),
            ("sar", "sar", "1.3.2"),
            ("classification", "classification", "2.0.0"),
            ("sat", "sat", "1.2.0"),
            ("grid", "grid", "1.1.0"),
        ]
    },
}


class Stac:
    def __init__(self, extensions=None, metadata_structure=None):
        from .metadata_structure import MetadataStructure

        self.schemas = deepcopy(get_local_schema_cache())
        for path in (Path(__file__).parent / "spec" / "extensions").glob("*/schema.json"):
            data = read(path)
            self.schemas[data["$id"].rstrip("#")] = data
        for uri, path in (extensions or {}).items():
            self.schemas[uri] = read(path)
        self.metadata_structure = MetadataStructure(metadata_structure, self)

    def retrieve(self, uri):
        if uri not in self.schemas:
            if urlsplit(uri).scheme != "https":
                raise AutoSTACGISError(f"Extension schema must use HTTPS or be registered locally: {uri}")
            response = httpx.get(uri, timeout=30, follow_redirects=True)
            response.raise_for_status()
            self.schemas[uri] = response.json()
        return Resource.from_contents(self.schemas[uri])

    def validate_schema(self, item, uri):
        registry = Registry(retrieve=self.retrieve).with_resources(
            (key, Resource.from_contents(value)) for key, value in self.schemas.items()
        )
        Draft7Validator({"$ref": uri}, registry=registry, format_checker=FormatChecker()).validate(item)

    def validate(self, item):
        version = item.get("stac_version", "1.1.0")
        self.validate_schema(item, f"https://schemas.stacspec.org/v{version}/item-spec/json-schema/item.json")
        for uri in item.get("stac_extensions", []):
            self.validate_schema(item, uri)
        return self.metadata_structure.validate(item)

    @staticmethod
    def extensions(item):
        if "data_state:directory_root" in item.get("properties", {}):
            from .asset_paths import set_directory_root

            set_directory_root(item)
        prefixes = set()

        def visit(value):
            if isinstance(value, dict):
                for key, child in list(value.items()):
                    if child is None and (key.startswith(("cf:", "raster:", "eo:")) or key == "unit"):
                        value.pop(key)
                        continue
                    prefixes.update(prefix for prefix in EXTENSIONS if key.startswith(prefix))
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(item.get("properties", {}))
        visit(item.get("assets", {}))
        declared = item.get("stac_extensions", [])

        def declared_family(uri):
            location = urlsplit(uri)
            return (location.netloc, location.path.rsplit("/v", 1)[0])

        retained = [uri for uri in declared if uri not in EXTENSIONS.values()]
        families = {declared_family(uri) for uri in retained}
        item["stac_extensions"] = list(
            dict.fromkeys(
                [
                    *retained,
                    *[
                        uri
                        for prefix, uri in EXTENSIONS.items()
                        if prefix in prefixes and declared_family(uri) not in families
                    ],
                ]
            )
        )
        return item

    @classmethod
    def derived(cls, metadata, identifier, assets):
        item = deepcopy(metadata)
        item.update(type="Feature", stac_version="1.1.0", id=identifier, assets=deepcopy(assets))
        item.pop("collection", None)
        item["links"] = []
        item.setdefault("geometry", None)
        if item["geometry"] is None:
            item.pop("bbox", None)
        item.setdefault("properties", {})
        return cls.extensions(item)
