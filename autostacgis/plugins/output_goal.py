"""Constrain a named output, composable with other goal plugins."""

ID = "output"
REQUIRES = ()


def goal(settings, records, context):
    return {"output": settings}


IMPLEMENTATIONS = {"goal": goal}
