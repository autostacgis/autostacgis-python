from ..errors import AutoSTACGISError
from ..sequence import Group


def explicit(records, settings, context):
    groups = []
    for ids in settings["groups"]:
        group = []
        for identifier in ids:
            candidates = [
                r
                for r in records
                if identifier in (r.key, r.item.get("id"), r.runtime.get("original_id"))
                or identifier in r.runtime.get("source_ids", [])
            ]
            if len(candidates) != 1:
                raise AutoSTACGISError(f"Group member {identifier!r} is missing or ambiguous")
            group.append(candidates[0])
        if len({r.key for r in group}) != len(group):
            raise AutoSTACGISError("Duplicate member within a group")
        if not group:
            raise AutoSTACGISError("Empty group")
        groups.append(Group(group))
    return groups


ID = "explicit-groups"
REQUIRES = ()
IMPLEMENTATIONS = {"sequence": explicit}
