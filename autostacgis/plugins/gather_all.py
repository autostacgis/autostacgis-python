from ..sequence import Group


def gather_all(records, settings, context):
    return [Group(records)] if records else []


ID = "gather-all"
REQUIRES = ()
IMPLEMENTATIONS = {"sequence": gather_all}
