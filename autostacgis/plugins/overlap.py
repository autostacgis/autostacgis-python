from ..errors import AutoSTACGISError
from ..sequence import Group


def by_overlap(records, settings, context):
    from shapely.geometry import shape

    remaining = list(records)
    groups = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for record in list(remaining):
                if record.item.get("geometry") is None:
                    raise AutoSTACGISError(f"Unknown geometry for {record.key}")
                if any(shape(record.item["geometry"]).intersects(shape(r.item["geometry"])) for r in group):
                    group.append(record)
                    remaining.remove(record)
                    changed = True
        groups.append(Group(group))
    return groups


ID = "overlap"
REQUIRES = ()
IMPLEMENTATIONS = {"sequence": by_overlap}
