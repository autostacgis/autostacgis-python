from ..metadata import matches
from ..sequence import Group


def filtered(records, settings, context):
    selected = [r for r in records if matches(r.item, settings.get("equals", {})) == "pass"]
    return [Group(selected)] if selected else []


ID = "filter"
REQUIRES = ()
IMPLEMENTATIONS = {"sequence": filtered}


def goal(settings, records, context):
    return {"for_each": settings}


IMPLEMENTATIONS["goal"] = goal
