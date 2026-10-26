"""Discover and download published workflow JSON; no GUI or database dependency."""

import json
import re
from pathlib import Path

from .config import unique, validate
from .errors import AutoSTACGISError
from .remote_registry import RemoteRegistry

REGISTRY_URL = "https://autostacgis.github.io/autostacgis-workflows/workflows.json"


class WorkflowManager:
    def __init__(self, registry_url=REGISTRY_URL):
        self.remote = RemoteRegistry(registry_url, "workflows")

    def catalog(self):
        return self.remote.catalog()

    def get(self, name, version=None):
        row = self.remote.entry(name, version)
        document = json.loads(self.remote.source(row).text, object_pairs_hook=unique)
        validate(document, "config")
        # Installed-plugin validation happens on activation/validation.
        # Downloading a workflow must not import or install its plugins implicitly.
        return {
            "id": row["id"],
            "version": row["version"],
            "description": row["description"],
            "document": document,
        }

    def download(self, name, directory, version=None, *, overwrite=False):
        value = self.get(name, version)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", value["id"]):
            raise AutoSTACGISError("Unsafe workflow filename")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{value['id']}.json"
        with path.open("w" if overwrite else "x", encoding="utf-8") as stream:
            json.dump(value["document"], stream, indent=2, allow_nan=False)
            stream.write("\n")
        return path
