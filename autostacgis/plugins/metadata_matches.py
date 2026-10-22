from ..checks import combine
from ..metadata import matches


def metadata_matches(inputs, settings, context):
    results = [matches(r.item, settings.get("equals", {})) for r in inputs["subject"]]
    return combine(results)


ID = "metadata-matches"
REQUIRES = ()
IMPLEMENTATIONS = {"checks": metadata_matches}
