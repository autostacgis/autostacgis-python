"""Refresh the bundled artifacts from a clean specification checkout."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]).resolve()
if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
    raise SystemExit("Commit the specification before creating its snapshot")
revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
artifacts = sorted(
    path
    for pattern in (
        "schemas/*.json",
        "extensions/*/schema.json",
        "extensions/lock.json",
        "vocabularies/*.json",
        "conformance/*.json",
    )
    for path in source.glob(pattern)
)
files = {}
for path in artifacts:
    relative = path.relative_to(source)
    destination = root / "autostacgis/spec" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
    files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
for path in (root / "autostacgis/spec").rglob("*.json"):
    if str(path.relative_to(root / "autostacgis/spec")) not in files:
        path.unlink()
version = json.loads((source / "schemas/config.json").read_text())["$id"].split("/v")[-1].split("/")[0]
(root / "spec.lock.json").write_text(
    json.dumps(
        {
            "repository": "https://github.com/autostacgis/autostacgis",
            "version": version,
            "commit": revision,
            "files": files,
        },
        indent=2,
    )
    + "\n"
)
print("Pinned specification", revision)
