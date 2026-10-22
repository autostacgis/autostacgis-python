from ..checks import combine
from ..metadata import MISSING, get


def has_role(inputs, settings, context):
    results = []
    for records in inputs.values():
        for record in records:
            roles = get(record.item, f"assets.{record.runtime.get('selected_asset', 'data')}.roles")
            results.append(
                "unknown" if roles is MISSING else ("pass" if settings["role"] in roles else "fail")
            )
    return combine(results)


ID = "has-role"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": has_role}
