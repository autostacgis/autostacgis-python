"""Verify the bundled specification artifacts and optional source checkout."""

import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "spec.lock.json").read_text())
source = Path(sys.argv[1]) if len(sys.argv) > 1 else None
for name, expected in lock["files"].items():
    bundled = root / "autostacgis/spec" / name
    assert hashlib.sha256(bundled.read_bytes()).hexdigest() == expected, name
    if source:
        assert bundled.read_bytes() == (source / name).read_bytes(), f"Specification drift: {name}"
print("Specification snapshot verified:", lock["version"], lock["commit"])
