from datetime import datetime

import httpx
import numpy as np

from ..ancillary import descriptor
from ..data import Batches
from ..errors import AutoSTACGISError


class AtmosphereSource:
    """NASA POWER daily atmosphere samples; missing physical quantities stay unknown."""

    def discover(self, settings, query, app):
        yield descriptor(settings, query, "atmosphere", "table")

    def discover_from(self, settings, query, app, records):
        scenes = [
            r
            for r in records
            if r.kind == "raster"
            and r.item.get("properties", {}).get("datetime")
            and any(a.get("data_state:modality") == "optical" for a in r.item.get("assets", {}).values())
        ]
        if not scenes:
            yield from self.discover(settings, query, app)
            return
        seen = set()
        for scene in scenes:
            acquired = scene.item["properties"]["datetime"]
            day = datetime.fromisoformat(acquired.replace("Z", "+00:00")).date().isoformat()
            bounds = scene.item.get("bbox")
            if not bounds:
                continue
            key = (day, tuple(bounds))
            if key in seen:
                continue
            seen.add(key)
            yield descriptor(
                settings,
                {"area": bounds, "time_range": [day + "T00:00:00Z", day + "T23:59:59Z"]},
                "atmosphere",
                "table",
            )

    def acquire(self, record, settings, app):
        import pyarrow as pa

        day = datetime.fromisoformat(record.runtime["time_range"][0].replace("Z", "+00:00")).strftime(
            "%Y%m%d"
        )
        west, south, east, north = record.runtime["area"]
        size = settings.get("grid_size", 1)
        if size < 1:
            raise AutoSTACGISError("Atmosphere grid_size must be positive")
        xs = np.linspace(west, east, size) if size > 1 else [(west + east) / 2]
        ys = np.linspace(south, north, size) if size > 1 else [(south + north) / 2]
        values = {key: [] for key in ("aot550", "water_vapor", "ozone")}
        for x in xs:
            for y in ys:
                response = httpx.get(
                    settings.get("endpoint", "https://power.larc.nasa.gov/api/temporal/daily/point"),
                    params={
                        "parameters": "AOD_55,PW,TO3",
                        "community": "RE",
                        "longitude": float(x),
                        "latitude": float(y),
                        "start": day,
                        "end": day,
                        "format": "JSON",
                    },
                    timeout=60,
                )
                response.raise_for_status()
                payload = response.json()
                for provider, name, factor in [
                    ("AOD_55", "aot550", 1),
                    ("PW", "water_vapor", 1),
                    ("TO3", "ozone", 0.001),
                ]:
                    value = payload.get("properties", {}).get("parameter", {}).get(provider, {}).get(day)
                    if (
                        value is not None
                        and value != payload.get("header", {}).get("fill_value", -999)
                        and value >= 0
                    ):
                        values[name].append(float(value) * factor)
        parameters = {key: float(np.mean(value)) if value else None for key, value in values.items()}
        parameters.update(settings.get("parameters", {}))
        parameters.update(month=int(day[4:6]), day=int(day[6:8]))
        record.item["properties"]["autostacgis:atmosphere"] = parameters
        record.item["properties"]["autostacgis:atmosphere_source"] = "NASA POWER"
        record.payload = Batches(pa.Table.from_pylist([parameters]).to_batches(), kind="table")
        return record


ID = "atmosphere"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_in": AtmosphereSource, "data_in": AtmosphereSource}
