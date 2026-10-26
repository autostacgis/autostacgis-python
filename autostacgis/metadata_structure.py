"""Declared STAC metadata, shared by discovery, execution, and plugin contexts."""

import re
from copy import deepcopy
from urllib.parse import urldefrag, urljoin

from jsonschema import FormatChecker, validators
from referencing import Registry, Resource

from .errors import AutoSTACGISError


class MetadataStructure:
    """Combine STAC JSON Schemas and reject fields they do not declare.

    Boolean additionalProperties=True is deliberately not a field declaration.
    Dynamic dictionaries must declare patternProperties or an object-valued
    additionalProperties schema, as the STAC assets dictionary does.
    """

    def __init__(self, configuration, stac):
        self.configuration = deepcopy(configuration or {})
        self.stac = stac
        validators.validator_for(self.configuration.get("inline", {})).check_schema(
            self.configuration.get("inline", {})
        )
        self.inline_uri = "https://autostacgis.local/metadata-structure.json"
        self.stac.schemas[self.inline_uri] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": self.inline_uri,
            **self.configuration.get("inline", {}),
        }

    def definition(self):
        return deepcopy(self.configuration)

    def _expand(self, schema, base="", seen=frozenset()):
        if not isinstance(schema, dict):
            return
        base = urljoin(base, schema.get("$id", ""))
        yield schema, base
        if "$ref" in schema:
            uri = urljoin(base, schema["$ref"])
            if uri not in seen:
                document, fragment = urldefrag(uri)
                self.stac.retrieve(document)
                target = self.stac.schemas[document]
                for part in fragment.lstrip("/").split("/") if fragment else []:
                    target = target[part.replace("~1", "/").replace("~0", "~")]
                yield from self._expand(target, document, seen | {uri})
        for keyword in ("allOf", "anyOf", "oneOf"):
            for branch in schema.get(keyword, []):
                yield from self._expand(branch, base, seen)
        for keyword in ("if", "then", "else"):
            if keyword in schema:
                yield from self._expand(schema[keyword], base, seen)

    def schemas(self, version="1.1.0"):
        core = f"https://schemas.stacspec.org/v{version}/item-spec/json-schema/item.json"
        return [{"$ref": uri} for uri in [core, *self.configuration.get("schemas", [])]] + [
            {"$ref": self.inline_uri}
        ]

    def fields(self, path=(), version="1.1.0"):
        """Return declared field schemas at a tuple path (use an asset name for maps)."""
        nodes = [(schema, "") for schema in self.schemas(version)]
        for name in path:
            nodes = self._children(nodes, str(name))
        result = {}
        for schema, base in nodes:
            for branch, uri in self._expand(schema, base):
                for name, value in branch.get("properties", {}).items():
                    result.setdefault(name, []).append(deepcopy(value))
        return result

    def _children(self, nodes, name):
        result = []
        for schema, base in nodes:
            for branch, uri in self._expand(schema, base):
                if name in branch.get("properties", {}):
                    result.append((branch["properties"][name], uri))
                for pattern, value in branch.get("patternProperties", {}).items():
                    # STAC extension schemas allow other namespaces using an empty
                    # pattern schema. That pass-through is not a field declaration.
                    if isinstance(value, dict) and value and re.search(pattern, name):
                        result.append((value, uri))
                if name.isdecimal():
                    index = int(name)
                    prefix = branch.get("prefixItems", [])
                    items = branch.get("items")
                    if isinstance(items, list):
                        prefix, items = items, None
                    if index < len(prefix):
                        result.append((prefix[index], uri))
                    elif isinstance(items, dict):
                        result.append((items, uri))
                dynamic = branch.get("additionalProperties")
                if isinstance(dynamic, dict) and any(
                    any(key in node for key in ("type", "properties", "enum", "const"))
                    for node, _ in self._expand(dynamic, uri)
                ):
                    result.append((dynamic, uri))
        return result

    def validate(self, item):
        schemas = self.schemas(item.get("stac_version", "1.1.0"))
        configured = set(self.configuration.get("schemas", []))
        schemas.extend({"$ref": uri} for uri in item.get("stac_extensions", []) if uri not in configured)
        registry = Registry(retrieve=self.stac.retrieve).with_resources(
            (uri, Resource.from_contents(schema)) for uri, schema in self.stac.schemas.items()
        )
        # Core and stac_extensions are already validated by Stac. Validate every
        # configured schema too, even when an Item forgot its extension URI.
        for schema in schemas[1:]:
            validators.validator_for(schema)(
                schema, registry=registry, format_checker=FormatChecker()
            ).validate(item)
        if not self.configuration.get("strict", True):
            return item

        def visit(value, nodes, path):
            if isinstance(value, dict):
                for name, child in value.items():
                    children = self._children(nodes, name)
                    if not children:
                        raise AutoSTACGISError(f"Undeclared metadata field {path}.{name}")
                    visit(child, children, f"{path}.{name}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, self._children(nodes, str(index)), f"{path}[{index}]")

        visit(item, [(schema, "") for schema in schemas], "$")
        return item
