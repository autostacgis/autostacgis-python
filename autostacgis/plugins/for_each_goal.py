"""Require one derived output for every selected initial dataset."""

ID = "for-each"
REQUIRES = ()


def goal(settings, records, context):
    return {"for_each": settings}


IMPLEMENTATIONS = {"goal": goal}
