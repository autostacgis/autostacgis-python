class AutoSTACGISError(ValueError):
    """Invalid configuration, plan, or output."""


class NoPlan(AutoSTACGISError):
    pass


class SearchLimit(AutoSTACGISError):
    pass


class AmbiguousProducer(AutoSTACGISError):
    """A requirement has competing loaded algorithms."""
