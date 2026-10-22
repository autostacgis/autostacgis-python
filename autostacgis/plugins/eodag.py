from itertools import chain
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import AutoSTACGISError
from ..io import local
from ..model import Record


class EodagSource:
    def __init__(self):
        self.gateway = None
        self.products = {}

    def discover(self, settings, query, app):
        from eodag import EODataAccessGateway
        from eodag.autostacgis import describe_product
        from eodag.config import EODAGSettings

        self.gateway = EODataAccessGateway(settings=EODAGSettings(**settings.get("config", {})))
        search = dict(settings.get("search", {}))
        if "area" in query:
            search["geom"] = query["area"]
        if "time_range" in query:
            search.update(start=query["time_range"][0], end=query["time_range"][1])
        if "lookback_hours" in query:
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            search.update(
                start=(now - timedelta(hours=query["lookback_hours"])).isoformat(), end=now.isoformat()
            )
        if "page" in search or "raise_errors" in search:
            raise AutoSTACGISError("EODAG pagination is managed by AutoSTACGIS")
        first = self.gateway.search(**search, raise_errors=True)
        first.raise_errors = True
        for page in chain([first], first.next_page(update=False)):
            for product in page:
                item = describe_product(product, settings.get("state_rules", []))
                record = Record(item, settings.get("type", "raster"))
                self.products[record.key] = product
                record.runtime["original_id"] = item["id"]
                yield record

    def acquire(self, record, settings, app):
        directory = app.path(settings.get("download_directory", str(app.work / "downloads")))
        directory.mkdir(parents=True, exist_ok=True)
        options = dict(settings.get("download_options", {}))
        if "output_dir" in options:
            raise AutoSTACGISError("Use download_directory")
        product = self.products[record.key]
        path = self.gateway.download(product, output_dir=str(directory), **options)
        if not path or not local(path).exists():
            raise AutoSTACGISError("EODAG did not acquire a local product")
        location = local(path).resolve()
        record.runtime["product_path"] = str(location)
        # EODAG retains provider URLs; map downloaded files without overwriting them.
        for name, asset in record.item.get("assets", {}).items():
            basename = Path(urlsplit(asset["href"]).path).name
            candidates = list(location.rglob(basename)) if location.is_dir() and basename else []
            if location.is_file() and location.name == basename:
                candidates = [location]
            if len(candidates) > 1:
                raise AutoSTACGISError(f"Ambiguous acquired asset {name}: {basename}")
            if candidates:
                record.runtime.setdefault("local_assets", {})[name] = str(candidates[0])
        return record


ID = "eodag"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_in": EodagSource, "data_in": EodagSource}
