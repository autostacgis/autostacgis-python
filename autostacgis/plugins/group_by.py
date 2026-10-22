from collections import defaultdict

from ..errors import AutoSTACGISError
from ..metadata import MISSING, get
from ..model import digest
from ..sequence import Group


def group_by(records, settings, context):
    groups = defaultdict(list)
    for record in records:
        value = get(record.item, settings["field"])
        if value is MISSING or value is None:
            raise AutoSTACGISError(f"Missing grouping field {settings['field']} on {record.key}")
        groups[digest(value)].append(record)
    return [Group(groups[k]) for k in sorted(groups)]


ID = "group-by"
REQUIRES = ()
IMPLEMENTATIONS = {"sequence": group_by}
