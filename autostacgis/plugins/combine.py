"""Combine routed groups into a single collection without merging their pixels."""

from ..sequence import Group


def combine(groups, settings, context):
    records = {r.key: r for group in groups for r in group}
    return [Group(records.values())] if records else []


combine.accepts_groups = True
ID = "combine"
REQUIRES = ()
IMPLEMENTATIONS = {"sequence": combine}
