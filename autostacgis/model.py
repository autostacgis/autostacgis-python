"""STAC metadata, native payload handles, and immutable workflow identities."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass
class Record:
    item: dict
    kind: str = "raster"
    key: str = ""
    source: str | None = None
    runtime: dict = field(default_factory=dict)
    payload: object = None

    def __post_init__(self):
        if not self.key:
            self.key = digest([self.item.get("collection"), self.item.get("id"), self.item.get("assets")])[
                :24
            ]

    def clone(self):
        return Record(
            deepcopy(self.item), self.kind, self.key, self.source, deepcopy(self.runtime), self.payload
        )


@dataclass
class Step:
    key: str
    module: str
    inputs: dict
    outputs: dict
    settings: dict
    cost: float
    dependencies: tuple = ()
    name: str | None = None
    paths: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


@dataclass
class Plan:
    steps: list
    initial: dict
    predicted: dict
    outputs: dict
    cost: float
    status: str = "synthesized"
    report: dict = field(default_factory=dict)

    def to_dict(self):
        from dataclasses import asdict

        return {
            "status": self.status,
            "cost": self.cost,
            "steps": [asdict(s) for s in self.steps],
            "outputs": {name: [self.predicted[k].item for k in keys] for name, keys in self.outputs.items()},
            **self.report,
        }
