from copy import deepcopy

from helpers import scene

from autostacgis.metadata import output_metadata

REQUEST = {
    "version": "1.0.0",
    "goal": {
        "for_each": {
            "kind": "raster",
            "select": {"assets.$asset.data_state:spectral_sampling": "multispectral"},
            "require": {"all": [{"path": "assets.$asset.data_state:pansharpened", "equals": True}]},
        }
    },
}


def test_selected_asset_copy_remove_and_empty_start():
    image = scene()
    image.item["assets"]["RED"] = image.item["assets"].pop("data")
    image.item["assets"]["RED"]["private"] = {"x": 1}
    image.runtime["selected_asset"] = "RED"
    original = deepcopy(image.item)
    output = output_metadata(
        {"image": [image]},
        {
            "inherit_metadata": ["image"],
            "set": [{"assets.data.note": "new"}],
            "remove": ["assets.data.private"],
        },
    )
    assert output["assets"]["data"]["unit"] == "1"
    assert output["assets"]["data"]["note"] == "new"
    assert "private" not in output["assets"]["data"]
    assert image.item == original
    assert output_metadata({"image": [image]}, {"inherit_metadata": []}) == {}
    assert output_metadata(
        {"image": [image]},
        {
            "inherit_metadata": ["image.properties.platform"],
        },
    ) == {"properties": {"platform": "test"}}


def test_each_from_preserves_corresponding_member_metadata():
    images = [scene("one"), scene("two")]
    images[0].item["properties"]["view:off_nadir"] = 12
    images[1].item["properties"]["view:off_nadir"] = 24
    output = {"inherit_metadata": ["images"], "each_from": "images"}
    assert [
        output_metadata({"images": images}, output, i)["properties"]["view:off_nadir"] for i in range(2)
    ] == [12, 24]
