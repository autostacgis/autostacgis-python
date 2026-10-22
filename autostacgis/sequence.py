"""Metadata-only routing, evaluated by core before binding each processing collection."""

from .errors import AutoSTACGISError


class Group(list):
    """An explicit processing group, including a group with one member."""


def route(app, calls, records, owner=None):
    calls = calls if isinstance(calls, list) else [calls]
    groups = [Group(records)]
    original = {r.key for r in records}
    for call in calls:
        definition, settings = app.resolve_call("sequence", call)
        if owner:
            from .dependencies import require

            require(app.plugins, app.registry, owner, definition["id"])
        function = app.registry.resolve("sequence", definition["implementation"])
        if settings.get("select"):
            from .contracts import test

            groups = [Group(r for r in group if test(r, settings["select"]) is True) for group in groups]
            groups = [group for group in groups if group]
        if getattr(function, "accepts_groups", False):
            groups = function(groups, settings, app.scope(definition["id"], settings))
        else:
            groups = [
                out
                for group in groups
                for out in function(group, settings, app.scope(definition["id"], settings))
            ]
        for group in groups:
            if (
                not group
                or len({r.key for r in group}) != len(group)
                or any(r.key not in original for r in group)
            ):
                raise AutoSTACGISError("sequence must return nonempty groups of unique input records")
    return groups
