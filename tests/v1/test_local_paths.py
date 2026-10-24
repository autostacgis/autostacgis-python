import nturl2path
from pathlib import PureWindowsPath

import pytest

from autostacgis import io


def test_file_uri_round_trip_and_asset_resolution(tmp_path):
    path = tmp_path / "scene with spaces é.tif"
    href = path.as_uri()
    assert io.local(href) == path
    assert io.file_reference(path) == href
    assert io.is_local(href)
    assert not io.is_local("https://example.org/scene.tif")
    from autostacgis import Record

    record = Record({"id": "local", "assets": {"data": {"href": href}}})
    assert io.asset_path(record) == path


@pytest.mark.parametrize(
    "href,path",
    [
        ("file:///C:/data/scene%20%C3%A9.tif", "C:/data/scene é.tif"),
        ("file://localhost/C:/data/scene.tif", "C:/data/scene.tif"),
        ("file://server/share/scene%20one.tif", "//server/share/scene one.tif"),
    ],
)
def test_windows_drive_and_unc_uris(href, path, monkeypatch):
    monkeypatch.setattr(io, "Path", PureWindowsPath)
    monkeypatch.setattr(io, "url2pathname", nturl2path.url2pathname)
    assert io.local(href) == PureWindowsPath(path)
    assert io.is_local(path)
    assert io.is_local(href)
    assert not io.is_local("s3://bucket/scene.tif")
