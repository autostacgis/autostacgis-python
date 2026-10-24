"""Shared HTTPS catalogs and checksum-verified sources for plugins and workflows."""

import re
from hashlib import sha256
from urllib.parse import urlsplit

import httpx

from .errors import AutoSTACGISError


class RemoteRegistry:
    def __init__(self, url, kind):
        self.url, self.kind = url, kind

    @staticmethod
    def fetch(url):
        if urlsplit(url).scheme != "https":
            raise AutoSTACGISError("Published registries and sources must use HTTPS")
        response = httpx.get(url, timeout=60, follow_redirects=True)
        response.raise_for_status()
        if urlsplit(str(response.url)).scheme != "https":
            raise AutoSTACGISError("Registry redirects must remain on HTTPS")
        return response

    def catalog(self):
        result = self.fetch(self.url).json()
        if (
            not isinstance(result, dict)
            or result.get("format_version") != 1
            or not isinstance(result.get(self.kind), list)
        ):
            raise AutoSTACGISError(f"Unsupported {self.kind} manifest")
        names = []
        for row in result[self.kind]:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(key), str) and row[key] for key in ("id", "version", "description")
            ):
                raise AutoSTACGISError(f"Invalid {self.kind} manifest entry")
            source = row.get("source", {})
            if (
                not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.-]+)?", row["version"])
                or not isinstance(source, dict)
                or not isinstance(source.get("url"), str)
                or urlsplit(source["url"]).scheme != "https"
                or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("sha256", "")))
            ):
                raise AutoSTACGISError(f"Invalid {self.kind} version or source")
            names.append(row["id"])
        if len(names) != len(set(names)):
            raise AutoSTACGISError(f"{self.kind} manifest contains duplicate names")
        return result

    def entry(self, name, version=None):
        row = next((row for row in self.catalog()[self.kind] if row["id"] == name), None)
        if row is None or (version and row["version"] != version):
            raise AutoSTACGISError(f"Version is not published: {name} {version or ''}")
        return row

    def source(self, row):
        response = self.fetch(row["source"]["url"])
        if len(response.content) > 2_000_000:
            raise AutoSTACGISError("Published source exceeds 2 MB")
        if sha256(response.content).hexdigest() != row["source"]["sha256"]:
            raise AutoSTACGISError("Source checksum does not match the published manifest")
        return response
