from pathlib import Path

from wcmatch import glob

from .errors import AutoSTACGISError
from .io import is_local, load_item, local
from .metadata import MISSING, assign, get
from .model import Record, digest


class LocalFiles:
    def discover(self, settings, query, app):
        root = app.path(settings.get("root_directory", "."))
        flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB | glob.NEGATE
        paths = sorted(
            set(glob.iglob(settings.get("search_glob", "**/*.tif"), root_dir=str(root), flags=flags))
        )
        if "files" in query:
            if not isinstance(query["files"], list) or any(
                not isinstance(path, str) for path in query["files"]
            ):
                raise AutoSTACGISError("query.files must be an array of file paths")
            chosen = {app.path(path) for path in query["files"]}
            paths = [match for match in paths if (root / match).resolve() in chosen]
        seen = set()
        for match in paths:
            path = (root / match).resolve()
            if path.suffix.lower() == ".json":
                item = load_item(path)
            else:
                # Exact sidecar association: scene.tif -> scene.tif.stac.json.
                sidecar = Path(str(path) + settings.get("sidecar_suffix", ".stac.json"))
                if sidecar.exists():
                    item = load_item(sidecar)
                    if not any(
                        local(a["href"]).resolve() == path
                        for a in item["assets"].values()
                        if is_local(a["href"])
                    ):
                        raise AutoSTACGISError(f"Sidecar does not reference selected asset: {path}")
                else:
                    item = {
                        "type": "Feature",
                        "stac_version": "1.1.0",
                        "id": digest(str(path))[:24],
                        "geometry": None,
                        "properties": {},
                        "links": [],
                        "stac_extensions": [],
                        "assets": {"data": {"href": path.as_uri(), "roles": ["data"]}},
                    }
            for rule in settings.get("metadata_rules", []):
                relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
                if glob.globmatch(relative, rule["match"], flags=flags):
                    for assignment in rule.get("set", []):
                        key, value = next(iter(assignment.items()))
                        current = get(item, key)
                        if current is not MISSING and current is not None and current != value:
                            raise AutoSTACGISError(
                                f"Path metadata rule conflicts with existing metadata at {key}"
                            )
                        assign(item, key, value)
            app.stac.extensions(item)
            from .data.kinds import describe_kinds

            record = describe_kinds(Record(item, settings.get("type", "raster")), settings.get("type"))
            record.runtime.update(product_path=str(path), original_id=item["id"])
            if record.key not in seen:
                seen.add(record.key)
                yield record
