import os

import httpx

from ..ancillary import descriptor
from ..data import FileRaster
from ..errors import AutoSTACGISError


class DemSource:
    """OpenTopography global DEM, including ellipsoidal SRTM for RPC warps."""

    def discover(self, settings, query, app):
        yield descriptor(settings, query, "digital_elevation_model", "raster")

    def acquire(self, record, settings, app):
        key = os.environ.get(settings.get("key_environment", "OPENTOPOGRAPHY_API_KEY"))
        if not key:
            raise AutoSTACGISError("OpenTopography requires its API key in the configured environment variable")
        west, south, east, north = record.runtime["area"]
        target = app.work / "downloads" / record.key / "dem.tif"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".partial.tif")
        params = dict(
            west=west,
            south=south,
            east=east,
            north=north,
            demtype=settings.get("demtype", "SRTMGL1_Ellip"),
            outputFormat="GTiff",
            API_Key=key,
        )
        try:
            with httpx.stream(
                "GET",
                settings.get("endpoint", "https://portal.opentopography.org/API/globaldem"),
                params=params,
                timeout=180,
            ) as response:
                if response.status_code != 200:
                    raise AutoSTACGISError(f"DEM provider failed with HTTP {response.status_code}")
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
            FileRaster(temporary)  # Refuse HTML/JSON error bodies, even for HTTP 200.
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        record.item["assets"]["data"]["href"] = str(target)
        record.payload = FileRaster(target)
        from ..data.metadata import refresh_metadata

        refresh_metadata(record)
        return record


ID = "dem"
REQUIRES = ()
IMPLEMENTATIONS = {"metadata_in": DemSource, "data_in": DemSource}
