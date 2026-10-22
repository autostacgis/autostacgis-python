from copy import deepcopy
from urllib.parse import quote

import httpx

from ..errors import AutoSTACGISError


def stac_api(record, settings, app):
    if any(a.get("href", "").startswith("autostacgis:") for a in record.item.get("assets", {}).values()):
        raise AutoSTACGISError("Publish data before runtime-only metadata")
    item = deepcopy(record.item)
    collection = settings["collection"]
    root = settings["api_url"].rstrip("/")
    item["collection"] = collection
    item["links"] = [link for link in item.get("links", []) if link["rel"] != "collection"] + [
        {"rel": "collection", "href": root + "/collections/" + quote(collection, safe="")}
    ]
    app.stac.validate(item)
    url = root + "/collections/" + quote(collection, safe="") + "/items"
    with httpx.Client(headers=settings.get("headers"), timeout=60) as client:
        response = client.post(url, json=item)
        if response.status_code == 409:
            response = client.put(url + "/" + quote(item["id"], safe=""), json=item)
        response.raise_for_status()
        if response.status_code == 202:
            raise AutoSTACGISError("STAC API write was not confirmed")
    return item


ID = "stac-api"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_out": stac_api}
