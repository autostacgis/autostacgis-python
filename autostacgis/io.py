from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import url2pathname

import httpx

from .config import read
from .errors import AutoSTACGISError


def local(href):
    href = str(href)
    if not href.startswith("file:"):
        return Path(href)
    uri = urlsplit(href)
    path = uri.path
    if uri.netloc and uri.netloc.lower() != "localhost":
        path = "//" + uri.netloc + path
    return Path(url2pathname(path))


def is_local(href):
    href = str(href)
    return bool(Path(href).drive) or urlsplit(href).scheme in ("", "file")


def file_reference(path):
    """Return a valid STAC URI for a local file, including spaces and drive letters."""
    return local(path).resolve().as_uri()


def load_item(path):
    if str(path).startswith(("http://", "https://")):
        response = httpx.get(str(path), timeout=30, follow_redirects=True)
        response.raise_for_status()
        item = response.json()
        base = str(response.url)
        for asset in item.get("assets", {}).values():
            asset["href"] = urljoin(base, asset["href"])
    else:
        path = local(path).resolve()
        item = read(path)
        for asset in item.get("assets", {}).values():
            if not urlsplit(asset["href"]).scheme:
                asset["href"] = file_reference(path.parent / asset["href"])
    if item.get("type") != "Feature" or "stac_version" not in item:
        raise AutoSTACGISError(f"Not a STAC Item: {path}")
    return item


def merge_verified(original, additional):
    for key, value in additional.items():
        if key not in original or original[key] is None:
            original[key] = deepcopy(value)
        elif isinstance(original[key], dict) and isinstance(value, dict):
            merge_verified(original[key], value)
        elif value is not None and original[key] != value:
            raise AutoSTACGISError(f"Metadata conflict at {key}: {original[key]!r} versus {value!r}")
    return original


def asset_path(record, key=None):
    key = key or record.runtime.get("selected_asset", "data")
    href = record.runtime.get("local_assets", {}).get(key, record.item["assets"][key]["href"])
    return local(href) if is_local(href) else href


def atomic_json(path, data):
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4

    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)
