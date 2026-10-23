"""Compatibility entry point for the separately installed standard plugin catalog."""

DEFAULTS = {"data_transfer": {"plugin": "buffered-transfer"}}


def catalog():
    from autostacgis_plugins._catalog import catalog as standard_catalog

    return standard_catalog()
