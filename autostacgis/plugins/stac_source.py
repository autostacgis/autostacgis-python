from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ..data.kinds import describe_kinds
from ..errors import AutoSTACGISError
from ..io import load_item
from ..model import Record, digest


class StacSource:
    def discover(self, settings, query, app):
        for uri in settings.get("items", []):
            path = uri if urlsplit(uri).scheme else app.path(uri)
            item = load_item(path)
            app.stac.validate(item)
            yield describe_kinds(Record(item, settings.get("type", "raster")), settings.get("type"))
        if settings.get("api_url"):
            url = settings["api_url"].rstrip("/") + "/search"
            body = dict(settings.get("search", {}))
            if "area" in query:
                body["bbox"] = query["area"]
            if "time_range" in query:
                body["datetime"] = "/".join(query["time_range"])
            method = "POST"
            seen = set()
            while url:
                token = digest([url, method, body])
                if token in seen:
                    raise AutoSTACGISError("Recursive STAC search pagination")
                seen.add(token)
                response = httpx.request(method, url, json=body if method == "POST" else None, timeout=60)
                response.raise_for_status()
                page = response.json()
                for item in page["features"]:
                    app.stac.validate(item)
                    yield describe_kinds(Record(item, settings.get("type", "raster")), settings.get("type"))
                link = next((link for link in page.get("links", []) if link["rel"] == "next"), None)
                url = urljoin(url, link["href"]) if link else None
                method = link.get("method", "GET") if link else "GET"
                body = link.get("body") if link else None

    def acquire(self, record, settings, app):
        if not settings.get("download", False):
            return record
        directory = app.work / "downloads" / record.key
        directory.mkdir(parents=True, exist_ok=True)
        for name, asset in record.item["assets"].items():
            href = asset["href"]
            if href.startswith(("https://", "http://")):
                target = directory / (digest(name)[:12] + Path(urlsplit(href).path).suffix)
                temporary = target.with_suffix(target.suffix + ".part")
                with httpx.stream("GET", href, timeout=120, follow_redirects=True) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as stream:
                        for block in response.iter_bytes():
                            stream.write(block)
                temporary.replace(target)
                record.runtime.setdefault("local_assets", {})[name] = str(target)
        return record


ID = "stac-source"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_in": StacSource, "data_in": StacSource}
