"""Directory roots derived from asset locations, without filesystem access."""

import ntpath
import posixpath
import re
from urllib.parse import urlsplit, urlunsplit


def directory_root(item):
    """Return the deepest shared asset directory, or None across URI authorities.

    Relative hrefs yield a relative root. Query strings and fragments are not
    part of a directory. Every asset participates, including metadata/quicklooks.
    """
    hrefs = [a["href"] for a in item.get("assets", {}).values() if a.get("href")]
    if hrefs and all(re.match(r"^[A-Za-z]:[\\/]", h) or h.startswith("\\\\") for h in hrefs):
        try:
            return ntpath.commonpath([ntpath.dirname(h) for h in hrefs])
        except ValueError:
            return None
    locations = [urlsplit(h) for h in hrefs]
    if not locations:
        return None
    authority = {(p.scheme.lower(), p.netloc) for p in locations}
    if len(authority) != 1:
        return None
    paths = [posixpath.dirname(p.path) or "." for p in locations]
    try:
        root = posixpath.commonpath(paths)
    except ValueError:
        return None
    if not root:
        root = "."
    scheme, host = next(iter(authority))
    if scheme and not host and all(h.startswith(f"{scheme}://") for h in hrefs):
        return f"{scheme}://{root}"
    return urlunsplit((scheme, host, root, "", "")) if scheme else root


def set_directory_root(item):
    item.setdefault("properties", {})["data_state:directory_root"] = directory_root(item)
    return item
